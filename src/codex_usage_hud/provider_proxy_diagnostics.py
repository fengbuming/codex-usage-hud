"""Bounded, request-driven diagnostics without changing successful responses."""

from __future__ import annotations

import gzip
import json
import logging
import re
import zlib
from urllib.parse import urlsplit, urlunsplit

HUD_REQUEST_HEADER = "X-Codex-Hud-Request-Id"
_ID_HEADERS = ("x-request-id", "request-id", "x-req-id", "req-id", "reqid", "x-correlation-id", "x-amzn-requestid")
_ID_KEYS = ("request_id", "requestId", "reqId", "req_id")
_CHUNK = 65536
_LOGGER = logging.getLogger(__name__)


def clean_label(value: object) -> str:
    return " ".join(str(value or "").split())[:256]


def safe_base_url(value: object) -> str:
    """Never put URL credentials, query secrets or fragments in diagnostics."""
    try:
        url = urlsplit(str(value or ""))
        host = url.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if url.port:
            host += f":{url.port}"
        path = re.sub(r"(?i)(?:sk-|bearer[-_])[a-z0-9_-]{12,}", "[redacted]", url.path)
        return urlunsplit((url.scheme, host, path, "", ""))
    except ValueError:
        return "[invalid upstream URL]"


def upstream_request_id(headers, body: bytes = b"") -> str:
    normalized = {str(k).lower(): v for k, v in headers.items()}
    for name in _ID_HEADERS:
        if normalized.get(name):
            return clean_label(normalized[name])
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        # A large error may exceed the diagnostic sample; its body is still
        # forwarded in full. Extract only explicitly named request-id fields.
        match = re.search(rb'"(?:request_id|requestId|reqId|req_id)"\s*:\s*"([^"\r\n]{1,256})"', body)
        return clean_label(match[1].decode("utf-8", "replace")) if match else ""

    def find(value, depth=0):
        if not isinstance(value, dict) or depth > 3:
            return ""
        for name in _ID_KEYS:
            if isinstance(value.get(name), (str, int)) and value[name]:
                return clean_label(value[name])
        return next((found for name in ("error", "response", "metadata")
                     if (found := find(value.get(name), depth + 1))), "")

    return find(payload)


def diagnostic_text(route: dict, hud_id: str, upstream_id: str = "", status=None) -> str:
    provider = clean_label(route.get("provider") or route.get("provider_id") or "unknown")
    fields = [f"provider={provider}", f"baseUrl={safe_base_url(route.get('base_url'))}"]
    if status is not None:
        fields.append(f"upstreamStatus={status}")
    fields.extend((f"upstreamReqId={upstream_id or 'not provided'}", f"hudReqId={hud_id}"))
    if route.get("enabled"):
        fields.append(f"connectionProxy=http://127.0.0.1:{route.get('port')}")
    return " | ".join(fields)


def error_body_chunks(response):
    encoding = str(response.getheader("Content-Encoding", "")).strip().lower()
    if encoding in {"", "identity"}:
        return iter(lambda: response.read1(_CHUNK), b"")
    if encoding == "gzip":
        reader = gzip.GzipFile(fileobj=response)
    elif encoding == "zstd":
        try:
            import zstandard
        except ImportError:
            return None

        reader = zstandard.ZstdDecompressor().stream_reader(response, closefd=False)
    elif encoding == "deflate":
        def decoded():
            decoder = zlib.decompressobj()
            try:
                while data := response.read1(_CHUNK):
                    while data:
                        value = decoder.decompress(data, _CHUNK)
                        data = decoder.unconsumed_tail
                        if value:
                            yield value
                if value := decoder.flush():
                    yield value
            except zlib.error as exc:
                raise ValueError("Unable to decode upstream deflate error response") from exc
        return decoded()
    else:
        # Preserve unknown encodings verbatim, including their headers.
        return None
    def read_decoded():
        try:
            yield from iter(lambda: reader.read(_CHUNK), b"")
        except Exception as exc:
            raise ValueError("Unable to decode upstream compressed error response") from exc
    return read_decoded()


def sample_error_body(chunks):
    sample = bytearray()
    while len(sample) < _CHUNK:
        chunk = next(chunks, b"")
        if not chunk:
            break
        sample.extend(chunk)
    return bytes(sample)


def annotate_sse_frame(frame: bytes, route: dict, hud_id: str, response_headers) -> bytes:
    lines = frame.splitlines()
    data = b"\n".join(line[5:].lstrip(b" ") for line in lines if line.startswith(b"data:"))
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError):
        return frame
    if not isinstance(payload, dict):
        return frame
    event = next((line[6:].strip() for line in lines if line.startswith(b"event:")), b"")
    if payload.get("type") not in {"error", "response.failed"} and event not in {b"error", b"response.failed"}:
        return frame
    message = payload.get("error")
    if not isinstance(message, dict):
        message = (payload.get("response") or {}).get("error") if isinstance(payload.get("response"), dict) else None
    if not isinstance(message, dict):
        message = payload
    original = message.get("message")
    if not isinstance(original, str):
        return frame
    request_id = upstream_request_id(response_headers, data)
    context = diagnostic_text(route, hud_id, request_id)
    message["message"] = original + "\n[Codex HUD] " + context
    _LOGGER.warning("provider_upstream_sse_error %s", context)
    newline = b"\r\n" if b"\r\n" in frame else b"\n"
    kept = [line for line in lines if line and not line.startswith(b"data:")]
    kept.append(b"data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return newline.join(kept) + newline * 2


class SseDiagnostics:
    """Buffer at most one small SSE event; leave large events untouched."""

    def __init__(self, route, hud_id, headers):
        self.route, self.hud_id, self.headers = route, hud_id, headers
        self.pending = bytearray()
        self.passthrough = False

    def feed(self, data: bytes) -> bytes:
        self.pending.extend(data)
        result = bytearray()
        while match := re.search(rb"\r?\n\r?\n", self.pending):
            end = match.end()
            frame = bytes(self.pending[:end])
            del self.pending[:end]
            result.extend(frame if self.passthrough else annotate_sse_frame(frame, self.route, self.hud_id, self.headers))
            self.passthrough = False
        if len(self.pending) > _CHUNK:
            result.extend(self.pending[:-3])
            del self.pending[:-3]
            self.passthrough = True
        return bytes(result)

    def finish(self) -> bytes:
        return bytes(self.pending)
