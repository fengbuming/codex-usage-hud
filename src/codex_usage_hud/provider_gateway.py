"""One Codex identity, with durable upstream selection per conversation.

Provider definitions remain editable in Codex's TOML. Their URLs all point to
the gateway while HUD metadata retains the upstream, including for old native
provider identities. Selection never depends on the foreground window.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import hashlib
from pathlib import Path
import re
import secrets
import sqlite3
import os
import time
from contextlib import contextmanager
from threading import RLock
from urllib.parse import urlsplit
import uuid

PROVIDER_ID = "codex_hud"
MARKER = "# codex-hud-gateway: "
_MARKER_RE = re.compile(r"(?m)^# codex-hud-gateway: (.+)\r?$")
_LOCK = RLock()


def gateway_metadata(text: str, *, include_inactive: bool = False) -> dict:
    match = _MARKER_RE.search(text)
    if not match:
        return {}
    value = json.loads(match[1])
    if not isinstance(value, dict) or not value.get("token"):
        raise ValueError("HUD 统一代理配置无效，请先恢复 config.toml。")
    return value if include_inactive or value.get("active", True) else {}


def gateway_info(config_path: Path | None = None) -> dict:
    from . import codex_provider_config as config

    path = config_path or config.default_codex_config_path()
    try:
        text = config._read_text_exact(path)
    except FileNotFoundError:
        return {}
    metadata = gateway_metadata(text)
    if not metadata:
        return {}
    parsed = config._parse_toml_mapping(text, strict=True)
    native = (parsed.get("model_providers") or {}).get(PROVIDER_ID) or {}
    if parsed.get("model_provider") != PROVIDER_ID or native.get("base_url") != metadata.get("endpoint"):
        # A different config manager owns the current route. Do not advertise
        # an old HUD marker as an active takeover or silently fight that tool.
        return {}
    return {**metadata, "default_provider": _default_provider(path, metadata["default_provider"]),
            "provider_id": PROVIDER_ID}


@contextmanager
def _state_db(path: Path):
    target = path.with_name("hud-provider-sessions.sqlite")
    with _LOCK:
        # sqlite serializes HUD/relay writes across processes. No recurring
        # watcher, full-history scan, or whole-file session-map rewrite.
        db = sqlite3.connect(target, timeout=5)
        try:
            os.chmod(target, 0o600)
            db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, provider TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS copies (id TEXT PRIMARY KEY, source_provider TEXT NOT NULL, prepared INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS foreign_seals (id TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(id, digest))")
            db.execute("CREATE TABLE IF NOT EXISTS image_sessions (id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS image_drafts (token TEXT PRIMARY KEY, provider TEXT NOT NULL, thread_id TEXT NOT NULL, expires INTEGER NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _default_provider(path: Path, fallback: str) -> str:
    if not path.with_name("hud-provider-sessions.sqlite").exists():
        return fallback
    with _read_state_db(path) as db:
        row = db.execute("SELECT value FROM settings WHERE key = 'default_provider'").fetchone()
    return str(row[0]) if row else fallback


@contextmanager
def _read_state_db(path: Path):
    """Read projections without taking a cross-process write lock."""
    target = path.with_name("hud-provider-sessions.sqlite")
    db = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        yield db
    finally:
        db.close()


def canonical_thread_id(value: object) -> str:
    try:
        return str(uuid.UUID(str(value or "")))
    except (TypeError, ValueError, AttributeError):
        return ""


def request_thread_id(headers: Mapping) -> str:
    normalized = {str(key).lower(): value for key, value in headers.items()}
    metadata = normalized.get("x-codex-turn-metadata", "")
    try:
        metadata = json.loads(str(metadata))
    except ValueError:
        metadata = {}
    candidates = [metadata.get("thread_id") if isinstance(metadata, dict) else None,
                  normalized.get("session_id"), normalized.get("x-codex-thread-id")]
    ids = {thread_id for value in candidates if (thread_id := canonical_thread_id(value))}
    if len(ids) > 1:
        raise ValueError("请求中的会话 ID 不一致，已停止路由。")
    return next(iter(ids), "")


def session_provider(thread_id: str, native_provider: str = "", *, config_path: Path | None = None) -> str:
    from .codex_provider_config import default_codex_config_path

    path = config_path or default_codex_config_path()
    bound = ""
    if path.with_name("hud-provider-sessions.sqlite").exists():
        with _read_state_db(path) as db:
            row = db.execute("SELECT provider FROM sessions WHERE id = ?", (thread_id,)).fetchone()
            bound = str(row[0]) if row else ""
    return bound or (native_provider if native_provider != PROVIDER_ID else "")


def _historical_provider(path: Path, thread_id: str, *, before: int = 0) -> str:
    """Look up an exact thread, on demand, without scanning rollout trees."""
    for database in sorted(path.parent.glob("state_*.sqlite"), reverse=True):
        try:
            db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                columns = {row[1] for row in db.execute("PRAGMA table_info(threads)")}
                if not columns:
                    continue
                fields = "model_provider, created_at" if "created_at" in columns else "model_provider, NULL"
                row = db.execute(f"SELECT {fields} FROM threads WHERE id = ?", (thread_id,)).fetchone()
            finally:
                db.close()
            if row and row[0]:
                if before and row[1] is not None and int(row[1]) >= before:
                    return ""
                return str(row[0]).lower()
        except sqlite3.Error as exc:
            raise RuntimeError("无法读取历史会话供应商，已停止路由。") from exc
    return ""


def _parent_thread_id(path: Path, thread_id: str) -> str:
    for database in sorted(path.parent.glob("state_*.sqlite"), reverse=True):
        db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(threads)")}
            if "source" not in columns:
                continue
            row = db.execute("SELECT source FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if not row:
                continue
            try:
                source = json.loads(row[0])
            except (ValueError, TypeError):
                return ""
            def parent(value, depth=0):
                if not isinstance(value, dict) or depth > 5:
                    return ""
                return canonical_thread_id(value.get("parent_thread_id")) or next(
                    (found for item in value.values() if (found := parent(item, depth + 1))), "")
            return parent(source)
        finally:
            db.close()
    return ""


def select_provider(provider: str, *, config_path: Path | None = None, thread_id: str = "", copied_from: str = "") -> dict:
    from . import codex_provider_config as config

    path = config_path or config.default_codex_config_path()
    provider = provider.strip().lower()
    info = gateway_info(path)
    if not info:
        if thread_id or copied_from:
            raise ValueError("HUD 统一代理当前未接管，未修改会话供应商。")
        return config.set_default_codex_provider(provider, config_path=path)
    definitions = config.read_provider_definitions(path)
    if provider == PROVIDER_ID or (provider not in definitions and provider != "openai"):
        raise ValueError("目标供应商已删除或尚未配置，未切换代理。")
    if provider in definitions and definitions[provider].wire_api != "responses":
        raise ValueError("Codex 统一代理需要供应商支持 Responses API。")
    # Validate credentials and the upstream before changing any selection.
    _provider_route(path, provider, definitions)
    with _state_db(path) as db:
        if thread_id:
            thread_id = canonical_thread_id(thread_id)
            if not thread_id:
                raise ValueError("会话 ID 无效。")
            row = db.execute("SELECT provider FROM sessions WHERE id = ?", (thread_id,)).fetchone()
            if row and row[0] != provider:
                raise ValueError("已有会话不能原地更换供应商，请先复制会话。")
            if copied_from and (not canonical_thread_id(copied_from) or copied_from == thread_id):
                raise ValueError("复制会话标识无效。")
            # Only an explicitly requested fresh copy may change its saved
            # native identity. Direct chats created while takeover was disabled
            # remain owned by their native provider regardless of creation time.
            historical = _historical_provider(
                path, thread_id,
                before=int(info.get("activated_at", 0)) if copied_from else 0,
            )
            if historical and historical not in {PROVIDER_ID, provider}:
                raise ValueError("历史会话必须使用原供应商，请先复制会话。")
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?)", (thread_id, provider))
            if copied_from:
                source_provider = session_provider(copied_from, config_path=path) or _historical_provider(path, copied_from)
                if not source_provider or source_provider == PROVIDER_ID:
                    raise ValueError("无法确认原会话的真实供应商，未绑定副本。")
                db.execute("INSERT OR IGNORE INTO copies(id, source_provider) VALUES (?, ?)", (thread_id, source_provider))
        else:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('default_provider', ?)", (provider,))
    return {"changed": True, "providerId": provider, "gateway": True,
            "nativeProviderId": PROVIDER_ID, "sessionId": thread_id}


def resolve_route(path: Path, headers: Mapping, *, generation: bool) -> dict:
    from . import codex_provider_config as config

    info = gateway_info(path)
    if not info:
        if gateway_metadata(config._read_text_exact(path)):
            raise ValueError("Codex 配置已由其它工具修改，已停止 HUD 路由。请先解除其它工具的接管，再启动 HUD。")
        info = {"default_provider": _default_provider(path, "openai")}
    thread_id = request_thread_id(headers)
    if generation and not thread_id:
        raise ValueError("Codex 请求未携带会话 ID，无法安全选择供应商。")
    with _state_db(path) as db:
        row = db.execute("SELECT provider FROM sessions WHERE id = ?", (thread_id,)).fetchone() if thread_id else None
        provider = str(row[0]) if row else ""
        if not provider:
            historical = _historical_provider(path, thread_id) if thread_id else ""
            parent = _parent_thread_id(path, thread_id) if thread_id else ""
            parent_row = db.execute("SELECT provider FROM sessions WHERE id = ?", (parent,)).fetchone() if parent else None
            inherited = str(parent_row[0]) if parent_row else (_historical_provider(path, parent) if parent else "")
            if inherited == PROVIDER_ID:
                inherited = ""
            provider = inherited or (historical if historical != PROVIDER_ID else "") or info["default_provider"]
        definitions = config.read_provider_definitions(path)
        route = _provider_route(path, provider, definitions)
        if generation and thread_id and not row:
            db.execute("INSERT INTO sessions VALUES (?, ?)", (thread_id, provider))
        copy = db.execute("SELECT source_provider FROM copies WHERE id = ?", (thread_id,)).fetchone()
    mode = image_sessions(path).get(thread_id, {})
    return {**route, "provider": provider, "thread_id": thread_id,
            "image_generation": mode if mode.get("provider") == provider else {},
            "foreign_history": bool(copy and copy[0] != provider)}


def image_sessions(path: Path | None = None) -> dict:
    """Read explicit per-chat image modes, including stores created by older relays."""
    from .codex_provider_config import default_codex_config_path

    path = path or default_codex_config_path()
    if not path.with_name("hud-provider-sessions.sqlite").exists():
        return {}
    with _read_state_db(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_sessions'").fetchone():
            return {}
        return {row[0]: {"provider": row[1], "model": row[2]} for row in db.execute("SELECT id, provider, model FROM image_sessions")}


def image_generation_context(thread_id: str, *, config_path: Path | None = None) -> dict:
    """Resolve the live composer's route without borrowing the HUD's selection."""
    from .codex_provider_config import default_codex_config_path

    path = config_path or default_codex_config_path()
    thread_id = canonical_thread_id(thread_id)
    if not thread_id or not gateway_info(path):
        raise ValueError("请在已开启 HUD 请求代理的会话中使用生图。")
    route = resolve_route(path, {"session_id": thread_id}, generation=False)
    return {"sessionId": thread_id, "provider": route["provider"]}


def image_draft_model(value: object) -> tuple[str, str] | None:
    match = re.fullmatch(r"hud-image/([0-9a-f]{32})/(.+)", str(value or ""))
    return (match[1], match[2]) if match else None


def set_image_draft(enabled: bool, model: str = "", token: str = "", *, config_path: Path | None = None) -> dict:
    """Arm a particular native draft, never the next arbitrary new conversation."""
    from .codex_provider_config import default_codex_config_path, read_provider_definitions
    from .image_generation import is_image_model

    path = config_path or default_codex_config_path()
    info = gateway_info(path)
    if not info or not isinstance(enabled, bool):
        raise ValueError("请先开启 HUD 供应商请求代理。")
    if not enabled:
        with _state_db(path) as db:
            row = db.execute("SELECT thread_id FROM image_drafts WHERE token = ?", (token,)).fetchone()
            # A copied native preference in another empty composer must not
            # disable the conversation that already consumed this token.
            if row and not row[0]:
                db.execute("DELETE FROM image_drafts WHERE token = ?", (token,))
        return {"enabled": False, "draft": True}
    provider = info["default_provider"]
    route = _provider_route(path, provider, read_provider_definitions(path))
    if not is_image_model(model) or model not in route.get("models", []):
        raise ValueError("当前新会话供应商未配置所选图片模型。")
    token = uuid.uuid4().hex
    with _state_db(path) as db:
        db.execute("DELETE FROM image_drafts WHERE expires < ? AND thread_id = ''", (int(time.time()),))
        db.execute("INSERT INTO image_drafts VALUES (?, ?, '', ?)", (token, provider, int(time.time()) + 1800))
    return {"enabled": True, "draft": True, "provider": provider, "model": model,
            "nativeModel": f"hud-image/{token}/{model}"}


def bind_image_draft(path: Path, route: dict, body: bytes | None) -> bytes | None:
    if not body:
        return body
    payload = json.loads(body)
    alias = image_draft_model(payload.get("model")) if isinstance(payload, dict) else None
    if not alias:
        return body
    from .image_generation import is_image_model

    token, model = alias
    thread_id = canonical_thread_id(route.get("thread_id"))
    with _state_db(path) as db:
        row = db.execute("SELECT provider, thread_id, expires FROM image_drafts WHERE token = ?", (token,)).fetchone()
        if not row or (not row[1] and row[2] < time.time()) or not thread_id:
            raise ValueError("生图草稿已失效，请关闭后重新开启生图。")
        if row[0] != route["provider"] or (row[1] and row[1] != thread_id):
            raise ValueError("生图草稿不属于当前会话或供应商，请重新开启生图。")
        if not is_image_model(model) or model not in route.get("models", []):
            raise ValueError("当前供应商不支持所选图片模型。")
        db.execute("UPDATE image_drafts SET thread_id = ? WHERE token = ?", (thread_id, token))
        db.execute("INSERT OR REPLACE INTO image_sessions VALUES (?, ?, ?)", (thread_id, row[0], model))
    route["image_generation"] = {"provider": row[0], "model": model}
    payload["model"] = model
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def set_image_generation(thread_id: str, enabled: bool, model: str = "", *, config_path: Path | None = None) -> dict:
    from .codex_provider_config import default_codex_config_path
    from .image_generation import is_image_model

    path = config_path or default_codex_config_path()
    thread_id = canonical_thread_id(thread_id)
    if not thread_id or not isinstance(enabled, bool):
        raise ValueError("请在当前会话中切换生图模式。")
    if not gateway_info(path):
        raise ValueError("请先开启 HUD 供应商请求代理，再使用生图。")
    route = resolve_route(path, {"session_id": thread_id}, generation=False)
    if enabled and (not is_image_model(model) or model not in route.get("models", [])):
        raise ValueError("请先在当前供应商中配置并选择图片模型。")
    with _state_db(path) as db:
        if enabled:
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?)", (thread_id, route["provider"]))
            db.execute("INSERT OR REPLACE INTO image_sessions VALUES (?, ?, ?)", (thread_id, route["provider"], model))
        else:
            db.execute("DELETE FROM image_sessions WHERE id = ?", (thread_id,))
    return {"sessionId": thread_id, "enabled": enabled, "provider": route["provider"], "model": model if enabled else ""}


def prepare_request_body(path: Path, route: Mapping, body: bytes | None) -> bytes | None:
    """Remove only source-account reasoning seals from a cross-provider copy.

    Never retry a generation against another provider, or silently discard an
    opaque compaction that may be the only surviving conversation context.
    """
    if not route.get("foreign_history") or not body:
        return body
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("input"), list):
        return body
    thread_id = str(route["thread_id"])
    with _state_db(path) as db:
        prepared = db.execute("SELECT prepared FROM copies WHERE id = ?", (thread_id,)).fetchone()
        first = prepared and not prepared[0]
        if first and payload.get("previous_response_id"):
            raise ValueError("副本仍引用原供应商的响应，请重新打开副本后再发送完整上下文。")
        known = {row[0] for row in db.execute("SELECT digest FROM foreign_seals WHERE id = ?", (thread_id,))}
        result = []
        changed = False
        for item in payload["input"]:
            if not isinstance(item, dict):
                result.append(item)
                continue
            seal = item.get("encrypted_content")
            digest = hashlib.sha256(seal.encode()).hexdigest() if isinstance(seal, str) and seal else ""
            foreign = bool(digest and (first or digest in known))
            if foreign and item.get("type") in {"compaction", "compaction_summary"}:
                raise ValueError("该副本包含原供应商的加密压缩上下文，目标供应商无法解密。请先在原会话生成文字交接摘要，再用目标供应商新建会话。")
            if first and item.get("type") == "item_reference":
                raise ValueError("该副本仍引用原供应商的历史对象，请重新打开副本后再发送完整上下文。")
            if foreign:
                db.execute("INSERT OR IGNORE INTO foreign_seals VALUES (?, ?)", (thread_id, digest))
                item = {k: v for k, v in item.items() if k != "encrypted_content"}
                changed = True
                if item.get("type") == "reasoning" and not item.get("summary"):
                    continue
            result.append(item)
        db.execute("UPDATE copies SET prepared = 1 WHERE id = ?", (thread_id,))
    if not changed:
        return body
    payload["input"] = result
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _provider_route(path: Path, provider: str, definitions: Mapping) -> dict:
    from . import codex_provider_config as config
    from .provider_proxy import _validated_proxy_upstream

    text = config._read_text_exact(path)
    info = gateway_metadata(text, include_inactive=True)
    builtin_urls = info.get("builtin_urls") or config._parse_toml_mapping(text, strict=True)
    definition = definitions.get(provider)
    if definition is None and provider != "openai":
        raise ValueError(f"会话供应商「{provider}」已删除；请恢复配置或复制到其他供应商。")
    if definition is not None:
        if definition.wire_api != "responses":
            raise ValueError("所选供应商不支持 Responses API。")
        route = {"base_url": definition.base_url, "enabled": definition.use_proxy,
                 "port": definition.proxy_port, "model": definition.desktop_model,
                 "models": list(definition.desktop_models), "headers": {}}
        raw = config._parse_toml_mapping(text, strict=True)
        entry = next((v for k, v in raw.get("model_providers", {}).items()
                      if str(k).casefold() == definition.provider_id.casefold()), {})
        if entry.get("auth"):
            raise ValueError("此供应商使用命令获取凭据，请先在 HUD 保存 API key 后启用统一代理。")
        query_params = entry.get("query_params", {})
        if not isinstance(query_params, Mapping) or any(not isinstance(v, str) for v in query_params.values()):
            raise ValueError("供应商 query_params 必须是字符串映射。")
        route["query_params"] = dict(query_params)
        route["headers"].update(entry.get("http_headers") or {})
        for name, env in (entry.get("env_http_headers") or {}).items():
            value = config._user_environment_value(str(env))
            if value:
                route["headers"][name] = value
        key = definition.bearer_token or config._user_environment_value(definition.env_key)
        if definition.requires_openai_auth:
            key = config.read_codex_auth_api_key(path)
    else:
        route = {"base_url": builtin_urls.get("openai_base_url") or "https://api.openai.com/v1", "headers": {}}
        key = config.read_codex_auth_api_key(path) or config._user_environment_value("OPENAI_API_KEY")
    if (definition is None or definition.requires_openai_auth) and config.codex_auth_uses_official_account(path):
        auth, _, _ = config._read_json_object(config.default_codex_auth_path(path))
        tokens = auth.get("tokens") or {}
        key = str(tokens.get("access_token") or "")
        root = str(builtin_urls.get("chatgpt_base_url") or "https://chatgpt.com/backend-api").rstrip("/")
        route["base_url"] = root if root.endswith("/codex") else root + "/codex"
        if tokens.get("account_id"):
            route["headers"]["ChatGPT-Account-Id"] = str(tokens["account_id"])
    if not key:
        raise ValueError(f"供应商「{provider}」缺少可用凭据，请先保存 API key 或登录官方账号。")
    # Header names are case-insensitive. Never forward the caller's credential,
    # nor allow a configured header to override the selected upstream key.
    route["headers"] = {str(k): str(v) for k, v in route["headers"].items()
                        if str(k).lower() not in {"authorization", "host", "content-length", "connection", "transfer-encoding", "x-codex-hud-gateway-token"}}
    route["headers"]["Authorization"] = "Bearer " + key
    upstream = _validated_proxy_upstream(route)
    endpoint = urlsplit(str(info.get("endpoint") or ""))
    loopback = {"127.0.0.1", "localhost", "::1"}
    if endpoint.hostname and upstream.port == endpoint.port and (
        upstream.hostname == endpoint.hostname or {upstream.hostname, endpoint.hostname} <= loopback
    ):
        raise ValueError("供应商不能指向 HUD 统一代理自身。")
    return route


def activate_gateway(path: Path, relay, *, on_config_written: Callable[[str], None] | None = None) -> None:
    from . import codex_provider_config as config
    from .provider_proxy import with_proxy_metadata
    from .provider_gateway_auth import GATEWAY_PATH_ID, with_gateway_auth
    from .provider_gateway_config import clear_provider_subtables

    if not path.exists():
        return
    original = config._read_text_exact(path)
    parsed = config._parse_toml_mapping(original, strict=True)
    metadata = gateway_metadata(original, include_inactive=True)
    was_gateway = bool(metadata)
    if not metadata and PROVIDER_ID in (parsed.get("model_providers") or {}):
        raise ValueError("config.toml 已使用保留供应商 codex_hud，无法接管。")
    default = str(parsed.get("model_provider") or "openai").lower()
    if metadata and default != PROVIDER_ID and metadata.get("active", True) and default == "magpie":
        raise ValueError("magpie 正在接管 Codex 配置。请先在 magpie 中解除 Codex 接管，再启动 HUD 统一代理。")
    metadata = metadata or {"token": secrets.token_urlsafe(24), "default_provider": default,
                            "activated_at": int(time.time()),
                            "builtin_urls": {k: parsed.get(k) for k in ("openai_base_url", "chatgpt_base_url")}}
    default = _default_provider(path, metadata["default_provider"]) if was_gateway and metadata.get("active", True) else default
    metadata.setdefault("activated_at", int(time.time()))
    definitions = config.read_provider_definitions(path)
    _provider_route(path, default, definitions)
    # The service stores only the config path; upstream credentials are read
    # for the selected provider at request time, never copied to control state.
    route = getattr(relay, "gateway_route", relay.route)
    endpoint = route({"base_url": "http://localhost/v1", "token": metadata["token"], "path_id": GATEWAY_PATH_ID,
                      "gateway_config": str(path.resolve())}, path)
    metadata["endpoint"] = endpoint
    named_endpoint = urlsplit(endpoint).path.split("/")[1:2] == [GATEWAY_PATH_ID]
    metadata["active"] = True
    if not gateway_metadata(original):
        with _state_db(path) as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('default_provider', ?)", (default,))
    candidate = original
    newline = config._preferred_newline(original)
    for provider, definition in definitions.items():
        if provider == PROVIDER_ID:
            continue
        body = config._section_range(candidate, definition.provider_id)[2]
        old_metadata = config.proxy_metadata(definition.section_text)
        old_metadata.update(base_url=definition.base_url, enabled=definition.use_proxy,
                            port=definition.proxy_port, gateway=True)
        body = config._set_quoted_value(body, "base_url", endpoint, newline)
        candidate = config._replace_section_body(candidate, definition.provider_id,
                                                 with_proxy_metadata(body, old_metadata, newline))
        candidate = with_gateway_auth(candidate, definition.provider_id, metadata["token"] if named_endpoint else None)
    body = newline.join(['name = "Codex HUD"', f'base_url = "{endpoint}"', 'wire_api = "responses"',
                         'requires_openai_auth = false', 'supports_websockets = false',
                         f'experimental_bearer_token = "{metadata["token"]}"'])
    if config.codex_auth_uses_official_account(path):
        # Keep Codex's own OAuth refresh active when an official login exists.
        body = body.replace('requires_openai_auth = false', 'requires_openai_auth = true')
        body = re.sub(r'\nexperimental_bearer_token = .+', '', body)
    candidate = clear_provider_subtables(candidate, PROVIDER_ID)
    if config._section_range(candidate, PROVIDER_ID):
        candidate = config._replace_section_body(candidate, PROVIDER_ID, body)
    else:
        candidate = config._add_provider_section_text(candidate, PROVIDER_ID, body)
    candidate = with_gateway_auth(candidate, PROVIDER_ID, metadata["token"] if named_endpoint else None)
    first_table = config.TABLE_HEADER_PATTERN.search(candidate)
    end = first_table.start() if first_table else len(candidate)
    head = config._set_quoted_value(candidate[:end], "model_provider", PROVIDER_ID, newline)
    head = config._set_quoted_value(head, "openai_base_url", endpoint, newline)
    head = config._set_quoted_value(head, "chatgpt_base_url", endpoint, newline)
    head = _MARKER_RE.sub("", head).rstrip("\r\n") + newline
    candidate = head + MARKER + json.dumps(metadata, separators=(",", ":")) + newline + candidate[end:]
    config._validate_toml(candidate)
    if candidate != original:
        # One private recovery copy, before the first takeover. Never replace a
        # user's earlier backup or print credentials in diagnostics.
        backup = path.with_name(path.name + ".hud-gateway-backup")
        if not backup.exists():
            config._write_new_text_atomically(backup, original)
        config._write_text_atomically(path, candidate, original, private=True)
        if on_config_written is not None:
            on_config_written(candidate)


def prepare_exit(provider: str, *, config_path: Path | None = None,
                 on_config_written: Callable[[str], None] | None = None) -> dict:
    """Restore a usable direct config before authorizing normal HUD shutdown."""
    from . import codex_provider_config as config
    from .provider_gateway_auth import with_gateway_auth
    from .provider_gateway_config import clear_provider_subtables, copy_provider_settings
    from .provider_proxy import updated_proxy_metadata, with_proxy_metadata

    path = config_path or config.default_codex_config_path()
    original = config._read_text_exact(path)
    config._parse_toml_mapping(original, strict=True)
    if not gateway_metadata(original, include_inactive=True):
        return config.set_default_codex_provider(provider, config_path=path)
    provider = provider.strip().lower()
    definitions = config.read_provider_definitions(path)
    if provider == PROVIDER_ID or (provider not in definitions and provider != "openai"):
        raise ValueError("请选择退出后使用的有效供应商。")
    direct_route = _provider_route(path, provider, definitions)
    candidate = original
    newline = config._preferred_newline(original)
    for key, definition in definitions.items():
        if key == PROVIDER_ID:
            continue
        body = config._section_range(candidate, definition.provider_id)[2]
        route_metadata = config.proxy_metadata(definition.section_text)
        route_metadata.pop("gateway", None)
        route_metadata = updated_proxy_metadata(
            route_metadata, base_url=definition.base_url, enabled=definition.use_proxy,
            port=definition.proxy_port, model=definition.desktop_model, models=list(definition.desktop_models),
        )
        restored_url = config.provider_proxy_relay.config_base_url(route_metadata, path)
        body = config._set_quoted_value(body, "base_url", restored_url, newline)
        body = with_proxy_metadata(body, route_metadata, newline)
        candidate = config._replace_section_body(candidate, definition.provider_id, body)
        candidate = with_gateway_auth(candidate, definition.provider_id, None)
    metadata = gateway_metadata(original, include_inactive=True)
    # Retain the native identity so chats created through HUD remain openable
    # after exit. Codex rejects a saved identity with no provider table.
    selected = definitions.get(provider)
    if selected:
        candidate = copy_provider_settings(candidate, selected.provider_id, PROVIDER_ID)
    else:
        body = newline.join(['name = "OpenAI"', f'base_url = "{direct_route["base_url"]}"',
                             'wire_api = "responses"', 'requires_openai_auth = true'])
        candidate = clear_provider_subtables(candidate, PROVIDER_ID)
        candidate = config._replace_section_body(candidate, PROVIDER_ID, body)
    candidate = with_gateway_auth(candidate, PROVIDER_ID, None)
    metadata["active"] = False
    metadata["default_provider"] = provider
    candidate = _MARKER_RE.sub(lambda _: MARKER + json.dumps(metadata, separators=(",", ":")), candidate)
    first_table = config.TABLE_HEADER_PATTERN.search(candidate)
    end = first_table.start() if first_table else len(candidate)
    head = config._set_quoted_value(candidate[:end], "model_provider", provider, newline)
    for key, value in metadata.get("builtin_urls", {}).items():
        head = config._set_quoted_value(head, key, value, newline) if value is not None else re.sub(rf"(?m)^[\t ]*{key}[\t ]*=.*(?:\r?\n|$)", "", head)
    candidate = head.rstrip("\r\n") + newline + candidate[end:]
    candidate, _ = config._apply_default_desktop_model(candidate, provider, selected.desktop_model if selected else "")
    config._validate_toml(candidate)
    config._write_text_atomically(path, candidate, original, private=True)
    if on_config_written is not None:
        on_config_written(candidate)
    with _state_db(path) as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES ('default_provider', ?)", (provider,))
    return {"providerId": provider, "gateway": False, "configPath": str(path),
            "exitToken": hashlib.sha256(path.read_bytes()).hexdigest()}


def validate_exit(token: str, *, config_path: Path | None = None) -> None:
    from . import codex_provider_config as config

    path = config_path or config.default_codex_config_path()
    if not path.exists():
        return
    metadata = gateway_metadata(config._read_text_exact(path), include_inactive=True)
    if not metadata:
        return
    if metadata.get("active", True) and not gateway_info(path):
        return
    if metadata.get("active", True):
        raise ValueError("退出会断开 HUD 代理。请先选择退出后使用的供应商并确认退出。")
    if not token:
        # A disabled gateway no longer needs an exit-time restoration handshake.
        return
    if not token or not secrets.compare_digest(token, hashlib.sha256(path.read_bytes()).hexdigest()):
        raise ValueError("退出配置已变化，请重新选择退出后使用的供应商。")
