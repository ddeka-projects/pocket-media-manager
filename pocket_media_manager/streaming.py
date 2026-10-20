"""Bounded file streaming with Safari's single byte ranges and HEAD support."""

from email.utils import formatdate
import re
import os
from pathlib import Path
import stat

from .errors import AppError

from fastapi.responses import Response, StreamingResponse


class OpenFileResponse(StreamingResponse):
    def __init__(self, stream, start, length, **kwargs):
        self.stream = stream

        def chunks():
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(256 * 1024, remaining))
                if not chunk:
                    raise OSError("Media was truncated during streaming.")
                remaining -= len(chunk)
                yield chunk

        super().__init__(chunks(), **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.stream.close()


def open_media(root, metadata_root, path):
    """Validate the token path and own its file handle before sending headers."""
    root, metadata_root, path = Path(root), Path(metadata_root), Path(path)
    stream = None
    try:
        relative = path.relative_to(root)
        if path.is_relative_to(metadata_root):
            raise ValueError("metadata is not media")
        before = path.lstat()
        for component in (root, *(root / parent for parent in reversed(relative.parents)), path):
            info = component.lstat()
            reparse = getattr(info, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
            )
            if stat.S_ISLNK(info.st_mode) or reparse:
                raise ValueError("linked media is not allowed")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        stream = os.fdopen(descriptor, "rb")
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not os.path.samestat(before, info):
            raise ValueError("media changed before opening")
        if not path.resolve().is_relative_to(root) or path.resolve().is_relative_to(metadata_root):
            raise ValueError("media moved outside the library")
        return stream, info
    except (OSError, ValueError):
        if stream is not None:
            stream.close()
        raise AppError("Playback link expired or file unavailable.", 404, "unavailable") from None


def media_response(root, metadata_root, path, request):
    stream, info = open_media(root, metadata_root, path)
    size = info.st_size
    etag = f'"{info.st_dev:x}-{info.st_ino:x}-{info.st_size:x}-{info.st_mtime_ns:x}"'
    modified = formatdate(info.st_mtime, usegmt=True)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
        "ETag": etag,
        "Last-Modified": modified,
        "Cache-Control": "private, no-cache",
        "Content-Disposition": "inline",
    }
    start, end = 0, size - 1
    status = 200
    raw = request.headers.get("range") if request.method == "GET" else None
    if_range = request.headers.get("if-range")
    if if_range and if_range not in (etag, modified):
        raw = None
    # HTTP permits ignoring unsupported units or multiple ranges; never build a
    # large multipart response for a local episode. Safari uses a single range.
    if raw and raw.startswith("bytes=") and "," not in raw:
        match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", raw)
        valid = bool(match and any(match.groups())) and size > 0
        if valid:
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
                valid = start < size and end >= start
            else:
                length = int(last)
                valid = length > 0
                start, end = max(0, size - length), size - 1
        if not valid:
            stream.close()
            return Response(
                status_code=416,
                headers={**headers, "Content-Length": "0", "Content-Range": f"bytes */{size}"},
                media_type="application/octet-stream",
            )
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
    if request.method == "HEAD" or size == 0:
        stream.close()
        return Response(status_code=status, headers=headers, media_type="application/octet-stream")
    return OpenFileResponse(
        stream,
        start,
        end - start + 1,
        status_code=status,
        headers=headers,
        media_type="application/octet-stream",
    )
