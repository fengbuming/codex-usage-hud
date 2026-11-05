"""Private header authentication for the readable local gateway URL."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
import secrets

GATEWAY_PATH_ID = "codex_hud"
GATEWAY_AUTH_HEADER = "X-Codex-Hud-Gateway-Token"


def gateway_authorized(headers, token: str) -> bool:
    supplied = headers.get(GATEWAY_AUTH_HEADER, "")
    if not supplied:
        kind, _, value = str(headers.get("Authorization", "")).partition(" ")
        supplied = value if kind.lower() == "bearer" else ""
    return bool(token and supplied) and secrets.compare_digest(str(supplied).encode(), token.encode())


def with_gateway_auth(text: str, provider: str, token: str | None) -> str:
    """Merge/remove just our header, preserving inline and table-based headers."""
    from . import codex_provider_config as config

    parsed = config._parse_toml_mapping(text)
    entry = next((v for k, v in (parsed.get("model_providers") or {}).items()
                  if str(k).casefold() == provider.casefold()), {})
    headers = entry.get("http_headers") or {}
    if not isinstance(headers, Mapping):
        raise ValueError("供应商 http_headers 必须是 TOML 表。")
    headers = {str(k): str(v) for k, v in headers.items() if str(k).lower() != GATEWAY_AUTH_HEADER.lower()}
    if token:
        headers[GATEWAY_AUTH_HEADER] = token
    newline = config._preferred_newline(text)
    table_name = f"model_providers.{provider}.http_headers"
    for start, end, header, body in config._toml_table_ranges(text):
        name = config._toml_table_name(header).replace('"', "").replace("'", "")
        if name.casefold() == table_name.casefold():
            prefix = text[start:end - len(body)]
            ending = re.search(r"(?:\r?\n)+\Z", body)
            replacement = prefix + newline.join(f"{json.dumps(k)} = {json.dumps(v, ensure_ascii=False)}" for k, v in headers.items()) + (ending[0] if ending else newline)
            return text[:start] + (replacement if headers else "") + text[end:]
    section = config._section_range(text, provider)
    if not section:
        raise ValueError("供应商配置不存在，无法设置代理鉴权。")
    start, end, body = section
    spans = []
    for match in re.finditer(r"(?m)^[\t ]*http_headers(?:\.[^=\r\n]+)?[\t ]*=", body):
        if any(a <= match.start() < b for a, b in spans):
            continue
        # Parse complete assignments, including multiline inline tables and
        # strings, rather than trying to edit their contents with a regex.
        for line_end in re.finditer(r"\r?\n|$", body[match.start():]):
            stop = match.start() + line_end.end()
            value = config._parse_toml_mapping(body[match.start():stop])
            if isinstance(value.get("http_headers"), Mapping):
                spans.append((match.start(), stop))
                break
        else:
            raise ValueError("无法安全更新供应商 http_headers。")
    for a, b in reversed(spans):
        body = body[:a] + body[b:]
    if headers:
        inline = ", ".join(f"{json.dumps(k)} = {json.dumps(v, ensure_ascii=False)}" for k, v in headers.items())
        # Proxy metadata is kept at the end of a provider section. Put the
        # header first so repeated activation cannot move the two lines back
        # and forth or accumulate blank lines and filesystem change events.
        body = "http_headers = { " + inline + " }" + newline + body.lstrip("\r\n")
    return text[:start] + body + text[end:]
