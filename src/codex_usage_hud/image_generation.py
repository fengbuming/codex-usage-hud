"""Adapt an explicitly enabled chat's Responses turn to the Images API.

Only the newest user message is sent. Agent instructions, tools and unrelated
conversation history never become image prompts. This adapter makes one paid
request and never retries a generation automatically.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import uuid


MAX_IMAGE_BYTES = 40 * 1024 * 1024


def is_image_model(model: object) -> bool:
    return bool(re.fullmatch(r"(?:gpt-image-[\w.-]+|gemini-[\w.-]*image[\w.-]*|gemini-nano-banana[\w.-]*)", str(model or ""), re.I))


def image_request(payload: dict, mode: dict) -> dict:
    model = str(payload.get("model") or "")
    if not is_image_model(model):
        model = mode["model"]
    messages = payload.get("input")
    if isinstance(messages, str):
        content = messages
    elif isinstance(messages, list):
        message = next((item for item in reversed(messages) if isinstance(item, dict) and item.get("role") == "user"), None)
        if message is None:
            raise ValueError("生图需要一条新的用户提示词。")
        content = message.get("content", "")
    else:
        raise ValueError("生图请求缺少用户提示词。")
    if isinstance(content, list):
        if any(isinstance(part, dict) and part.get("type") not in {"input_text", "text"} for part in content):
            raise ValueError("当前生图模式支持文字生成图片；参考图编辑暂不支持，请先移除附件。")
        content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise ValueError("请填写要生成的图片内容。")
    return {"model": model, "prompt": content.strip(), "n": 1, "size": "1024x1024", "response_format": "b64_json"}


def _generate(route: dict, request: dict) -> dict:
    base = urlsplit(route["base_url"])
    params = route.get("query_params") or {}
    query = urlencode([*(pair for pair in parse_qsl(base.query, keep_blank_values=True) if pair[0] not in params), *params.items()])
    url = base._replace(path=base.path.rstrip("/") + "/images/generations", query=query, fragment="").geturl()
    proxy = f"http://127.0.0.1:{int(route.get('port', 7897))}"
    # Respect this provider's explicit route; ignore machine-wide proxy variables.
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy} if route.get("enabled") else {}))
    headers = {**route.get("headers", {}), "Content-Type": "application/json", "Accept": "application/json"}
    try:
        with opener.open(Request(url, data=json.dumps(request).encode("utf-8"), headers=headers), timeout=180) as response:
            data = response.read(MAX_IMAGE_BYTES * 2 + 1)
            if len(data) > MAX_IMAGE_BYTES * 2:
                raise ValueError("生图返回超过大小限制。")
            return json.loads(data)
    except HTTPError as exc:
        # Do not expose vendor response bodies, signed URLs or credentials.
        raise ValueError(f"生图接口返回 HTTP {exc.code}，请检查供应商额度和模型支持。未自动重试。") from None


def save_image(result: dict, config_path: Path, thread_id: str) -> Path:
    from .provider_gateway import canonical_thread_id

    if not canonical_thread_id(thread_id):
        raise ValueError("生图会话 ID 无效。")
    thread_id = canonical_thread_id(thread_id)
    items = result.get("data")
    encoded = items[0].get("b64_json") if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if not isinstance(encoded, str) or len(encoded) > MAX_IMAGE_BYTES * 2:
        raise ValueError("供应商未返回有效 b64_json 图片；请确认 Images API 支持 response_format=b64_json。")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("供应商返回的图片 Base64 无效。") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("生成的图片超过 40 MiB。")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = "png"
    elif raw.startswith(b"\xff\xd8\xff"):
        extension = "jpg"
    elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        extension = "webp"
    else:
        raise ValueError("供应商返回的内容不是支持的图片格式。")
    # The thread ID originates in canonical_thread_id, never in model output.
    directory = config_path.parent / "generated_images" / "hud" / thread_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (uuid.uuid4().hex + "." + extension)
    with path.open("xb") as output:
        output.write(raw)
    os.chmod(path, 0o600)
    return path.resolve()


def serve_image_generation(handler, config_path: Path, route: dict, body: bytes | None) -> None:
    """Return native Responses events with a durable local-image Markdown result."""
    payload = json.loads(body or b"{}")
    if not isinstance(payload, dict):
        raise ValueError("生图请求格式无效。")
    request = image_request(payload, route["image_generation"])
    if request["model"] not in route.get("models", []):
        raise ValueError("所选图片模型不在当前供应商的模型列表中。")
    response_id = "resp_hud_image_" + uuid.uuid4().hex
    response = {"id": response_id, "object": "response", "created_at": int(time.time()),
                "status": "in_progress", "model": request["model"], "output": [], "error": None}
    sequence = 0
    streaming = payload.get("stream") is True

    def event(kind: str, **fields) -> None:
        nonlocal sequence
        value = {"type": kind, "sequence_number": sequence, **fields}
        sequence += 1
        handler.wfile.write(("event: " + kind + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode("utf-8"))
        handler.wfile.flush()

    if streaming:
        handler.send_response_only(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.hud_image_response_started = True
        event("response.created", response=response)
        event("response.in_progress", response=response)
    try:
        result = _generate(route, request)
        if not isinstance(result, dict):
            raise ValueError("生图接口返回格式无效。")
        path = save_image(result, config_path, route["thread_id"])
        text = f"![生成的图片]({path.as_posix()})"
        part = {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
        item = {"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
                "phase": "final_answer", "status": "completed", "content": [part]}
        response.update(status="completed", output=[item])
        if streaming:
            pending = {**item, "status": "in_progress", "content": []}
            event("response.output_item.added", output_index=0, item=pending)
            event("response.content_part.added", item_id=item["id"], output_index=0, content_index=0,
                  part={**part, "text": ""})
            event("response.output_text.delta", item_id=item["id"], output_index=0, content_index=0, delta=text)
            event("response.output_text.done", item_id=item["id"], output_index=0, content_index=0, text=text)
            event("response.content_part.done", item_id=item["id"], output_index=0, content_index=0, part=part)
            event("response.output_item.done", output_index=0, item=item)
            event("response.completed", response=response)
            return
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # A terminal response.failed avoids a misleading successful empty turn.
        response.update(status="failed", error={"code": "image_generation_failed", "message": str(exc)})
        if streaming:
            event("response.failed", response=response)
            return
    data = json.dumps(response, ensure_ascii=False).encode("utf-8")
    handler.send_response_only(200 if response["status"] == "completed" else 502)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(data)
    handler.wfile.flush()
