"""Assemble the web app and its independently maintained operating conventions."""

import argparse
import asyncio
import logging
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import state
from .api import router
from .config import REPOSITORY, Settings, load_settings
from .controller import ControllerOwnership
from .errors import AppError
from .logging_setup import configure_logging
from .presentation import _error, _page
from .process_lock import ProcessLock
from .security import validate_request
from .service import _feedback_request, _pending_response
from .storage import Database, FeedbackRequired

logger = logging.getLogger(__name__)
COOKIE_NAME = "pocket_browser"
CONTROLLER_PATHS = {"/api/controller/claim", "/api/controller/takeover", "/api/controller/release"}
PUBLIC_PATHS = {"/health", "/favicon.ico", "/pocket-manager-icon.png", "/manifest.webmanifest"}


async def _daily_backups(database: Database) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await run_in_threadpool(database.backup, daily=True)
        except (OSError, sqlite3.Error):
            logger.exception("Automatic backup failed; will retry on the next hourly check")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configure_logging(root=REPOSITORY)
        configuration = settings or load_settings()
        process_lock = ProcessLock(configuration.media_root)
        process_lock.acquire()
        backup_task = None
        try:
            database = Database(configuration)
            database.initialize()
            application.state.settings = configuration
            application.state.database = database
            application.state.controller = ControllerOwnership()
            application.state.operation_lock = asyncio.Lock()
            count = await run_in_threadpool(database.refresh_catalog, allow_pending=True)
            await run_in_threadpool(database.backup, daily=True)
            logger.info("Catalog ready: %s media files", count)
            backup_task = asyncio.create_task(_daily_backups(database))
            yield
        except Exception:
            logger.exception("Server startup or shutdown failed")
            raise
        finally:
            if backup_task:
                backup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await backup_task
            state.clear_state()
            process_lock.close()

    application = FastAPI(title="Pocket Media Manager", version="0.3.0", lifespan=lifespan)
    application.include_router(router)
    application.mount(
        "/static",
        StaticFiles(directory=REPOSITORY / "pocket_media_manager" / "static"),
        name="static",
    )

    @application.middleware("http")
    async def browser_context(request: Request, call_next):
        try:
            validate_request(request, request.app.state.settings)
            path = request.url.path
            public = (
                path in PUBLIC_PATHS
                or path.startswith("/static/")
                or (path.startswith("/stream/") and path != "/stream/play")
            )
            if public:
                response = await call_next(request)
            else:
                # Ownership endpoints run under the same gate as whole request operations.
                # A takeover cannot overtake a save/player launch already admitted here.
                async with request.app.state.operation_lock:
                    if path in CONTROLLER_PATHS:
                        response = await call_next(request)
                    else:
                        page = request.headers.get("x-page-id", "")
                        token = request.headers.get("x-controller-token", "")
                        if (
                            not page
                            and not token
                            and request.method in {"GET", "HEAD"}
                            and "text/html" in request.headers.get("accept", "")
                        ):
                            response = _page(
                                "Pocket Media Manager",
                                "<h1>Pocket Media Manager</h1><p>Connecting…</p><noscript>Enable JavaScript to use this application.</noscript>",
                            )
                        else:
                            with request.app.state.controller.authorized(page, token):
                                pass
                            browser_id = request.cookies.get(COOKIE_NAME, "")
                            new_cookie = re.fullmatch(r"[0-9a-f]{32}", browser_id) is None
                            if new_cookie:
                                browser_id = uuid.uuid4().hex
                            request.state.browser_id = browser_id
                            request.state.browser = await run_in_threadpool(
                                request.app.state.database.browser, browser_id
                            )
                            pending = await run_in_threadpool(
                                request.app.state.database.pending_encounter
                            )
                            request.state.pending_encounter = pending
                            if pending and not _feedback_request(request, pending["id"]):
                                response = _pending_response(request, pending["id"])
                            else:
                                response = await call_next(request)
                            if new_cookie:
                                response.set_cookie(
                                    COOKIE_NAME,
                                    browser_id,
                                    max_age=365 * 24 * 3600,
                                    httponly=True,
                                    samesite="strict",
                                )
        except AppError as error:
            response = JSONResponse(
                {
                    "ok": False,
                    "error": {"code": error.code, "message": str(error), **error.details},
                },
                status_code=error.status,
            )
        except (OSError, sqlite3.Error):
            logger.exception("Media storage is unavailable")
            response = _error(
                request,
                503,
                "Media storage is unavailable. Check the media drive and logs/server.log.",
                "storage_unavailable",
            )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    @application.post("/api/controller/claim")
    async def claim(request: Request):
        payload = await _controller_payload(request)
        return request.app.state.controller.claim(payload.get("page_id", ""))

    @application.post("/api/controller/takeover")
    async def takeover(request: Request):
        payload = await _controller_payload(request)
        return request.app.state.controller.claim(payload.get("page_id", ""), takeover=True)

    @application.post("/api/controller/release")
    async def release(request: Request):
        payload = await _controller_payload(request)
        request.app.state.controller.release(payload.get("page_id", ""), payload.get("token", ""))
        return {"ok": True}

    @application.exception_handler(AppError)
    async def app_error(request: Request, error: AppError):
        return JSONResponse(
            {"ok": False, "error": {"code": error.code, "message": str(error), **error.details}},
            status_code=error.status,
        )

    @application.exception_handler(FeedbackRequired)
    async def pending_feedback(request: Request, error: FeedbackRequired):
        return _pending_response(request, error.encounter_id)

    @application.exception_handler(KeyError)
    async def missing_record(request: Request, error: KeyError):
        return _error(request, 404, str(error.args[0]), "not_found")

    @application.exception_handler(ValueError)
    async def invalid_value(request: Request, error: ValueError):
        from .storage import Conflict

        return _error(
            request,
            409 if isinstance(error, Conflict) else 400,
            str(error),
            "conflict" if isinstance(error, Conflict) else "invalid_request",
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return _error(
            request,
            error.status_code,
            str(error.detail),
            "not_found" if error.status_code == 404 else "invalid_request",
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        return _error(request, 422, "A required value is missing or invalid.", "invalid_request")

    @application.exception_handler(OSError)
    @application.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, error: Exception):
        logger.exception("Storage operation failed", exc_info=error)
        return _error(
            request,
            503,
            "Media storage is unavailable. Check the media drive and logs/server.log.",
            "storage_unavailable",
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        logger.error(
            "Unexpected request failure", exc_info=(type(error), error, error.__traceback__)
        )
        return _error(
            request,
            500,
            "The request could not be completed. Check logs/server.log before retrying a save.",
            "internal_error",
        )

    return application


async def _controller_payload(request: Request) -> dict:
    try:
        payload = await request.json()
    except ValueError:
        raise AppError("Send a JSON object with page_id.") from None
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise AppError("Send an application/json request.", 415, "unsupported_media_type")
    allowed = {"page_id", "token"} if request.url.path.endswith("/release") else {"page_id"}
    if (
        not isinstance(payload, dict)
        or set(payload) - allowed
        or not isinstance(payload.get("page_id"), str)
    ):
        raise AppError("Send a JSON object with page_id.")
    if "token" in payload and not isinstance(payload["token"], str):
        raise AppError("Invalid controller token.")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Pocket Media Manager")
    parser.add_argument(
        "--reload", action="store_true", help="Reload Python code during development"
    )
    arguments = parser.parse_args(argv)
    configure_logging(root=REPOSITORY)
    try:
        configuration = load_settings()
        import uvicorn

        if arguments.reload:
            uvicorn.run(
                "pocket_media_manager.app:create_app",
                factory=True,
                host=configuration.host,
                port=configuration.port,
                reload=True,
                reload_dirs=[str(REPOSITORY / "pocket_media_manager")],
                log_config=None,
                proxy_headers=False,
            )
        else:
            uvicorn.run(
                create_app(configuration),
                host=configuration.host,
                port=configuration.port,
                log_config=None,
                proxy_headers=False,
            )
        return 0
    except Exception:
        logger.exception("Server could not start; check .env and media-directory access")
        return 1
