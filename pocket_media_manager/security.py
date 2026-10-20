"""Restrict the trusted-LAN listener and same-origin browser operations."""

import ipaddress
import socket
from urllib.parse import urlsplit

from fastapi import Request

from .config import Settings
from .errors import AppError


def _private_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return address.is_loopback or (address.is_private and not address.is_unspecified)
    except ValueError:
        return False


def validate_request(request: Request, settings: Settings) -> None:
    # Never trust proxy headers: the Windows server is reached directly on the LAN.
    if request.client is None or not _private_address(request.client.host):
        raise AppError(
            "This application accepts connections only from the local network.",
            403,
            "peer_rejected",
        )
    raw_host = request.headers.get("host", "")
    try:
        parsed_host = urlsplit("http://" + raw_host)
        hostname = parsed_host.hostname or ""
        port = parsed_host.port or 80
    except ValueError:
        raise AppError("Invalid Host header.", 403, "host_rejected") from None
    allowed_names = {
        "localhost",
        socket.gethostname().lower(),
        socket.getfqdn().lower(),
        urlsplit(settings.public_base_url).hostname,
        settings.host.lower(),
    }
    if (
        not raw_host
        or parsed_host.username
        or parsed_host.password
        or parsed_host.path
        or parsed_host.query
        or parsed_host.fragment
        or parsed_host.port == 0
        or hostname.lower() not in allowed_names
        and not _private_address(hostname)
        or port != settings.port
    ):
        raise AppError(
            "The requested host is not an allowed local server address.", 403, "host_rejected"
        )
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{raw_host}".lower()
    if origin is not None and origin.lower().rstrip("/") != expected:
        raise AppError(
            "Open this application directly before sending requests.", 403, "origin_rejected"
        )
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise AppError("Requests from another website are not accepted.", 403, "origin_rejected")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin is None:
        raise AppError(
            "State-changing requests require this application's Origin header.",
            403,
            "origin_required",
        )
