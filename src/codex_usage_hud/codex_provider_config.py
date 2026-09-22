"""Maintain Codex ``model_providers`` without rewriting unrelated TOML."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import ctypes
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ENVIRONMENT_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CODEX_AUTH_API_KEY = "OPENAI_API_KEY"
# Codex 内置保留 Provider ID：不允许在 [model_providers.<id>] 中自定义覆盖。
# 参考 Codex 报错："model_providers contains reserved built-in provider IDs"。
RESERVED_BUILTIN_PROVIDER_IDS = frozenset({"openai", "ollama", "lmstudio"})
OFFICIAL_ACCOUNT_TOKEN_KEYS = frozenset(
    {"access_token", "refresh_token", "id_token", "account_id"}
)
PROVIDER_SECTION_PATTERN = re.compile(
    r"(?m)^[\t ]*\[model_providers\.(?P<id>[A-Za-z0-9_-]+)\][\t ]*(?:#.*)?(?:\r?\n|$)"
)
TABLE_HEADER_PATTERN = re.compile(
    r"(?m)^[\t ]*\[\[?[^\r\n]+\]\]?\s*(?:#.*)?$"
)
TOML_TABLE_HEADER_PATTERN = re.compile(
    r"(?m)^[\t ]*(?P<header>\[\[?[^\r\n]+\]\]?)[\t ]*"
    r"(?:#.*)?(?:\r?\n|$)"
)
QUOTED_STRING_PATTERN = re.compile(
    r"(?m)^[\t ]*(?P<key>[A-Za-z0-9_-]+)[\t ]*=[\t ]*"
    r"\"(?P<value>(?:\\.|[^\"\\\r\n])*)\"[\t ]*(?:#.*)?\r?$"
)


@dataclass(frozen=True, slots=True)
class CodexProviderDefinition:
    """Non-secret provider metadata exposed to the settings renderer."""

    provider_id: str
    name: str = ""
    base_url: str = ""
    env_key: str = ""
    auth_key: str = ""
    official_account: bool = False
    requires_openai_auth: bool = False
    wire_api: str = "responses"
    has_api_key: bool = False
    section_text: str = ""


def default_codex_config_path() -> Path:
    codex_home = str(os.environ.get("CODEX_HOME") or "").strip()
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def default_codex_auth_path(config_path: str | Path | None = None) -> Path:
    """Return the Codex auth store paired with a config.toml path."""
    path = (
        Path(config_path).expanduser()
        if config_path is not None
        else default_codex_config_path()
    )
    return path.with_name("auth.json")


def _read_json_object(path: Path) -> tuple[dict[str, Any], str, bool]:
    try:
        text = _read_text_exact(path)
    except OSError:
        return {}, "", False
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Codex auth.json 不是有效 JSON：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Codex auth.json 必须是 JSON 对象。")
    return dict(payload), text, True


def read_codex_auth_api_key(
    config_path: str | Path | None = None,
    *,
    auth_key: str = CODEX_AUTH_API_KEY,
) -> str:
    """Read the default Codex App API key without exposing the auth file."""
    payload, _text, _exists = _read_json_object(default_codex_auth_path(config_path))
    return str(payload.get(auth_key) or "")


def codex_auth_uses_official_account(
    config_path: str | Path | None = None,
) -> bool:
    """Return whether Codex auth.json contains an official account session."""
    payload, _text, _exists = _read_json_object(default_codex_auth_path(config_path))
    tokens = payload.get("tokens")
    if not isinstance(tokens, Mapping):
        return False
    return bool(OFFICIAL_ACCOUNT_TOKEN_KEYS.intersection(tokens.keys()))


def _json_with_preserved_newline(payload: Mapping[str, Any], original: str) -> str:
    newline = _preferred_newline(original)
    suffix = newline if original.endswith(("\r", "\n")) else ""
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    return rendered.replace("\n", newline) + suffix


def _write_codex_auth_api_key(
    config_path: str | Path | None,
    value: str,
) -> tuple[bool, Path, str, bool, str]:
    """Atomically update OPENAI_API_KEY and return rollback metadata."""
    path = default_codex_auth_path(config_path)
    payload, original_text, existed = _read_json_object(path)
    if str(payload.get(CODEX_AUTH_API_KEY) or "") == value:
        return False, path, original_text, existed, original_text
    payload[CODEX_AUTH_API_KEY] = value
    candidate = _json_with_preserved_newline(payload, original_text)
    try:
        if existed:
            _write_text_atomically(path, candidate, original_text)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not _write_new_text_atomically(path, candidate):
                raise RuntimeError("Codex auth.json 在保存前发生了变化，请重新打开设置后再试。")
        if _read_text_exact(path) != candidate:
            raise RuntimeError("Codex auth.json 写入后校验失败。")
    except Exception:
        try:
            _restore_codex_auth_text(path, original_text, existed, candidate)
        except Exception as rollback_error:
            raise RuntimeError(
                "Codex auth.json 写入校验失败，且之前的值无法恢复。"
            ) from rollback_error
        raise
    return True, path, original_text, existed, candidate


def _restore_codex_auth_text(
    path: Path,
    original_text: str,
    existed: bool,
    expected_text: str,
) -> None:
    if not existed:
        try:
            if _read_text_exact(path) != expected_text:
                return
            path.unlink()
        except FileNotFoundError:
            return
        return
    _write_text_atomically(path, original_text, expected_text)


def _user_environment_value(name: str) -> str:
    key = str(name or "").strip()
    if not key:
        return ""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value, _kind = winreg.QueryValueEx(handle, key)
            return str(value or "")
        except (FileNotFoundError, OSError):
            pass
    return str(os.environ.get(key) or "")


def _set_user_environment_value(name: str, value: str) -> None:
    key = str(name or "").strip()
    if not key:
        raise ValueError("用户环境变量名称不能为空")
    if os.name != "nt":
        # The feature is intentionally Windows-compatible with the existing
        # PowerShell helper.  Updating os.environ alone would not persist the
        # key for a subsequently launched Codex process on Unix.
        raise RuntimeError("用户环境变量持久化目前仅支持 Windows。")
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment") as handle:
        winreg.SetValueEx(handle, key, 0, winreg.REG_SZ, str(value))
    _broadcast_environment_change()


def _delete_user_environment_value(name: str) -> None:
    """Remove one persisted user environment variable, if present."""
    key = str(name or "").strip()
    if not key:
        return
    if os.name != "nt":
        # No persistent user environment variable to remove on Unix.
        return
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            winreg.DeleteValue(handle, key)
    except FileNotFoundError:
        # Not present; idempotent success.
        return
    except OSError:
        return
    _broadcast_environment_change()


def _broadcast_environment_change() -> None:
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32
        result = ctypes.c_size_t(0)
        user32.SendMessageTimeoutW(
            ctypes.c_void_p(0xFFFF),
            ctypes.c_uint(0x001A),
            ctypes.c_void_p(0),
            ctypes.c_wchar_p("Environment"),
            ctypes.c_uint(0x0002),
            ctypes.c_uint(5000),
            ctypes.byref(result),
        )
    except (AttributeError, OSError):
        # Registry persistence is the important operation.  A process that
        # already has its environment block cannot always receive the hint.
        return


def _toml_string(value: object) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _preferred_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _normalize_body(body: str, newline: str) -> str:
    return re.sub(r"\r\n|\r|\n", newline, body).rstrip("\r\n")


def _read_text_exact(path: Path) -> str:
    """Read text without Python's universal-newline conversion."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text_exact(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _section_range(text: str, provider_id: str) -> tuple[int, int, str] | None:
    target = str(provider_id or "").strip()
    match = next(
        (
            item
            for item in PROVIDER_SECTION_PATTERN.finditer(text)
            if item.group("id").casefold() == target.casefold()
        ),
        None,
    )
    if match is None:
        return None
    body_start = match.end()
    next_header = re.search(
        r"(?m)^[\t ]*\[\[?[^\r\n]+\]\]?\s*(?:#.*)?(?:\r?\n|$)",
        text[body_start:],
    )
    body_end = body_start + next_header.start() if next_header else len(text)
    return body_start, body_end, text[body_start:body_end]


def _provider_section_text(text: str, provider_id: str) -> str:
    target = str(provider_id or "").strip()
    match = next(
        (
            item
            for item in PROVIDER_SECTION_PATTERN.finditer(text)
            if item.group("id").casefold() == target.casefold()
        ),
        None,
    )
    section = _section_range(text, target)
    if match is None or section is None:
        return ""
    _body_start, body_end, _body = section
    return text[match.start() : body_end]


def _get_quoted_value(body: str, key: str) -> str:
    for match in QUOTED_STRING_PATTERN.finditer(body):
        if match.group("key") == key:
            return match.group("value")
    single_quoted = re.search(
        r"(?m)^[\t ]*"
        + re.escape(key)
        + r"[\t ]*=[\t ]*'(?P<value>[^'\r\n]*)'",
        body,
    )
    if single_quoted:
        return str(single_quoted.group("value") or "")
    return ""


def _toml_table_ranges(
    text: str,
) -> list[tuple[int, int, str, str]]:
    matches = list(TOML_TABLE_HEADER_PATTERN.finditer(text))
    return [
        (
            match.start(),
            matches[index + 1].start() if index + 1 < len(matches) else len(text),
            str(match.group("header") or "").strip(),
            text[match.end() : (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(text)
            )],
        )
        for index, match in enumerate(matches)
    ]


def _toml_table_name(header: str) -> str:
    value = str(header or "").strip()
    if value.startswith("[[") and value.endswith("]]"):
        return value[2:-2].strip()
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1].strip()
    return ""


def _provider_related_table(
    header: str,
    provider_id: str,
) -> bool:
    name = _toml_table_name(header).casefold()
    prefix = f"model_providers.{str(provider_id or '').strip()}".casefold()
    return name == prefix or name.startswith(f"{prefix}.")


def _provider_profile_tables(
    ranges: Sequence[tuple[int, int, str, str]],
    provider_id: str,
) -> set[str]:
    target = str(provider_id or "").strip().casefold()
    roots: set[str] = set()
    for _start, _end, header, body in ranges:
        name = _toml_table_name(header)
        parts = name.split(".")
        if len(parts) != 2 or parts[0].casefold() != "profiles":
            continue
        if _get_quoted_value(body, "model_provider").strip().casefold() == target:
            roots.add(name.casefold())
    return roots


def _remove_provider_tables(text: str, provider_id: str) -> tuple[str, list[str]]:
    ranges = _toml_table_ranges(text)
    profile_roots = _provider_profile_tables(ranges, provider_id)
    remove: list[tuple[int, int]] = []
    removed_headers: list[str] = []
    for start, end, header, _body in ranges:
        name = _toml_table_name(header)
        folded_name = name.casefold()
        if _provider_related_table(header, provider_id) or any(
            folded_name.startswith(f"{root}.") or folded_name == root
            for root in profile_roots
        ):
            remove.append((start, end))
            removed_headers.append(header)
    if not remove:
        return text, removed_headers
    result = text
    for start, end in reversed(remove):
        result = result[:start] + result[end:]
    return result, removed_headers


def _set_quoted_value(body: str, key: str, value: str, newline: str) -> str:
    escaped = _toml_string(value)
    pattern = re.compile(
        r"(?m)^(?P<prefix>[\t ]*"
        + re.escape(key)
        + r"[\t ]*=[\t ]*)(?P<value>[^\r\n]*)(?P<ending>\r?)$"
    )
    match = pattern.search(body)
    if match:
        replacement = (
            match.group("prefix")
            + '"'
            + escaped
            + '"'
            + match.group("ending")
        )
        return body[: match.start()] + replacement + body[match.end() :]
    separator = newline if body and not body.endswith(("\r", "\n")) else ""
    return body + separator + f'{key} = "{escaped}"'


def _replace_section_body(text: str, provider_id: str, body: str) -> str:
    section = _section_range(text, provider_id)
    if section is None:
        raise ValueError(f"[model_providers.{provider_id}] 不存在。")
    if TABLE_HEADER_PATTERN.search(_normalize_body(body, _preferred_newline(text))):
        raise ValueError("Provider 内容不能包含其他 TOML 表头。")
    start, end, _old_body = section
    newline = _preferred_newline(text)
    clean_body = _normalize_body(body, newline)
    replacement = f"{clean_body}{newline}" if clean_body else ""
    if end < len(text):
        replacement += newline
    return text[:start] + replacement + text[end:]


def _provider_section_body_from_editor(section_text: str, provider_id: str) -> str:
    raw = str(section_text or "").strip("\r\n")
    match = PROVIDER_SECTION_PATTERN.match(raw)
    expected = str(provider_id or "").strip()
    if match is None or match.group("id").casefold() != expected.casefold():
        raise ValueError(
            f"配置文本必须以 [model_providers.{expected}] 开头，且 Provider ID 必须一致。"
        )
    body = raw[match.end() :]
    if TABLE_HEADER_PATTERN.search(_normalize_body(body, "\n")):
        raise ValueError("Provider 配置文本不能包含其它 TOML 表头。")
    return body


def _add_provider_section(text: str, provider_id: str, base_url: str, env_key: str) -> str:
    newline = _preferred_newline(text)
    body = newline.join(
        (
            f"[model_providers.{provider_id}]",
            f'name = "{_toml_string(provider_id)}"',
            f'base_url = "{_toml_string(base_url)}"',
            f'env_key = "{_toml_string(env_key)}"',
            'wire_api = "responses"',
        )
    )
    suffix = "" if text.endswith(("\r", "\n")) else newline
    return f"{text}{suffix}{newline}{body}{newline}"


def _add_provider_section_text(text: str, provider_id: str, body: str) -> str:
    newline = _preferred_newline(text)
    clean_body = _normalize_body(body, newline)
    block = f"[model_providers.{provider_id}]"
    if clean_body:
        block = f"{block}{newline}{clean_body}"
    suffix = "" if text.endswith(("\r", "\n")) else newline
    return f"{text}{suffix}{newline}{block}{newline}"


def _validate_toml(text: str) -> None:
    try:
        import tomllib  # type: ignore[attr-defined]
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return
    try:
        tomllib.loads(text)
    except Exception as exc:
        raise ValueError(f"编辑后的 Codex config.toml 不是有效 TOML：{exc}") from exc


def _parse_toml_mapping(text: str) -> Mapping[str, Any]:
    try:
        import tomllib  # type: ignore[attr-defined]
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return {}
    try:
        candidate = tomllib.loads(text)
    except Exception:
        return {}
    return candidate if isinstance(candidate, Mapping) else {}


def _write_text_atomically(path: Path, text: str, expected: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{id(text)}")
    try:
        _write_text_exact(temporary, text)
        current = _read_text_exact(path)
        if current != expected:
            raise RuntimeError("config.toml 在保存前发生了变化，请重新打开设置后再试。")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_new_text_atomically(path: Path, text: str) -> bool:
    if path.exists():
        return False
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{id(text)}")
    try:
        _write_text_exact(temporary, text)
        try:
            # ``os.replace`` would overwrite a profile created by another
            # process.  ``os.rename`` keeps the create-only contract on both
            # Windows and POSIX.
            os.rename(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_provider_definitions(
    config_path: str | Path | None = None,
) -> dict[str, CodexProviderDefinition]:
    """Read provider metadata and editable TOML section text."""
    path = Path(config_path).expanduser() if config_path is not None else default_codex_config_path()
    try:
        text = _read_text_exact(path)
    except OSError:
        return {}
    parsed = _parse_toml_mapping(text)
    if not parsed:
        return {}
    raw_providers = parsed.get("model_providers")
    if not isinstance(raw_providers, Mapping):
        return {}
    default_provider = str(parsed.get("model_provider") or "").strip().casefold()
    result: dict[str, CodexProviderDefinition] = {}
    for raw_id, raw_value in raw_providers.items():
        provider_id = str(raw_id or "").strip()
        if not provider_id or not isinstance(raw_value, Mapping):
            continue
        env_key = str(raw_value.get("env_key") or "").strip()
        normalized_provider = provider_id.casefold()
        requires_openai_auth = normalized_provider == "custom" or bool(
            raw_value.get("requires_openai_auth")
        )
        auth_key = (
            CODEX_AUTH_API_KEY
            if normalized_provider == default_provider and requires_openai_auth
            else ""
        )
        official_account = bool(
            auth_key and codex_auth_uses_official_account(path)
        )
        result[provider_id.casefold()] = CodexProviderDefinition(
            provider_id=provider_id,
            name=str(raw_value.get("name") or "").strip(),
            base_url=str(raw_value.get("base_url") or "").strip(),
            env_key=env_key,
            auth_key=auth_key,
            official_account=official_account,
            requires_openai_auth=requires_openai_auth,
            wire_api=str(raw_value.get("wire_api") or "responses").strip(),
            has_api_key=(
                bool(read_codex_auth_api_key(path, auth_key=auth_key))
                if auth_key
                else bool(_user_environment_value(env_key)) if env_key else False
            ),
            section_text=_provider_section_text(text, provider_id),
        )
    return result


def _validate_request(
    update: Mapping[str, Any],
    *,
    allow_api_key_without_env: bool = False,
) -> tuple[str, str, str, str, bool]:
    provider_id = str(update.get("provider_id") or update.get("providerId") or "").strip()
    base_url = str(update.get("base_url") or update.get("baseUrl") or "").strip()
    env_key = str(update.get("env_key") or update.get("envKey") or "").strip()
    api_key = str(update.get("api_key") or update.get("apiKey") or "")
    is_new = bool(update.get("is_new", update.get("isNew", False)))
    if not PROVIDER_ID_PATTERN.fullmatch(provider_id) or (
        is_new and provider_id.casefold() == "custom"
    ):
        raise ValueError("Provider ID 只能使用字母、数字、连字符或下划线，且不能是 custom。")
    if provider_id.casefold() in RESERVED_BUILTIN_PROVIDER_IDS:
        raise ValueError(
            "Provider ID 与 Codex 内置 Provider 重名（openai / ollama / lmstudio），"
            "内置 Provider 无法覆盖，请改用其它 ID（例如 ollama-local）。"
        )
    if not base_url or "\r" in base_url or "\n" in base_url:
        raise ValueError("base_url 不能为空且必须是单行文本。")
    if env_key and not ENVIRONMENT_KEY_PATTERN.fullmatch(env_key):
        raise ValueError("请输入有效的用户环境变量名称。")
    if is_new and not env_key:
        raise ValueError("新增供应商时请输入用户环境变量名称。")
    if api_key and not env_key and not allow_api_key_without_env:
        raise ValueError("填写 API key 时必须同时填写用户环境变量名称。")
    if "\r" in api_key or "\n" in api_key:
        raise ValueError("API key 不能包含换行。")
    return provider_id, base_url, env_key, api_key, is_new


def save_provider_configs(
    updates: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    *,
    config_path: str | Path | None = None,
) -> dict[str, object]:
    """Apply provider sections, user env keys, and new provider profiles.

    The TOML editor follows the existing PowerShell helper: it changes only
    the selected ``[model_providers.<id>]`` body, validates the candidate,
    writes atomically, creates ``<id>.config.toml`` only for a new profile,
    and rolls back changes made by this operation when verification fails.
    """
    if isinstance(updates, Mapping):
        if "provider_id" in updates or "providerId" in updates:
            normalized_updates = [updates]
        else:
            normalized_updates = []
            for provider_id, value in updates.items():
                if not isinstance(value, Mapping):
                    continue
                item = dict(value)
                item.setdefault("provider_id", provider_id)
                normalized_updates.append(item)
    elif isinstance(updates, Sequence) and not isinstance(updates, (str, bytes, bytearray)):
        normalized_updates = [item for item in updates if isinstance(item, Mapping)]
    else:
        normalized_updates = []
    if not normalized_updates:
        return {"changed": False, "providerIds": []}
    path = Path(config_path).expanduser() if config_path is not None else default_codex_config_path()
    if not path.exists():
        raise FileNotFoundError(f"Codex config was not found: {path}")
    original_text = _read_text_exact(path)
    candidate_text = original_text
    definitions = read_provider_definitions(path)
    parsed_config = _parse_toml_mapping(original_text)
    default_provider = str(parsed_config.get("model_provider") or "").strip().casefold()
    raw_model_providers = parsed_config.get("model_providers")
    seen_ids: set[str] = set()
    env_before: dict[str, str] = {}
    env_after: dict[str, str] = {}
    profiles_to_create: list[tuple[Path, str]] = []
    provider_ids: list[str] = []
    default_provider_before_section = ""
    default_provider_candidate_section = ""
    default_auth_before = ""
    default_auth_after: str | None = None
    auth_path: Path | None = None
    auth_original_text = ""
    auth_original_exists = False
    auth_candidate_text = ""
    auth_changed = False

    for update in normalized_updates:
        raw_section_text = str(
            update.get("section_text") or update.get("sectionText") or ""
        )
        section_body = None
        validation_update: Mapping[str, Any] = update
        requested_provider_id = str(
            update.get("provider_id") or update.get("providerId") or ""
        ).strip()
        normalized_requested_id = requested_provider_id.casefold()
        raw_default_entry: Mapping[str, Any] = {}
        if isinstance(raw_model_providers, Mapping):
            for raw_id, raw_entry in raw_model_providers.items():
                if str(raw_id or "").strip().casefold() == normalized_requested_id:
                    if isinstance(raw_entry, Mapping):
                        raw_default_entry = raw_entry
                    break
        is_default_provider = (
            bool(default_provider) and normalized_requested_id == default_provider
        )
        is_default_app_provider = (
            is_default_provider
            and (
                normalized_requested_id == "custom"
                or bool(raw_default_entry.get("requires_openai_auth"))
            )
        )
        if is_default_provider and codex_auth_uses_official_account(path):
            raise ValueError(
                "官方账号登录时，默认 Codex App Provider 由 Codex Desktop 管理，不支持编辑。"
            )
        if raw_section_text:
            section_body = _provider_section_body_from_editor(
                raw_section_text,
                requested_provider_id,
            )
            validation_update = dict(update)
            validation_update["base_url"] = (
                _get_quoted_value(section_body, "base_url")
                or str(update.get("base_url") or update.get("baseUrl") or "")
            )
            validation_update["env_key"] = (
                ""
                if is_default_app_provider
                else _get_quoted_value(section_body, "env_key")
            )
        provider_id, base_url, env_key, api_key, is_new = _validate_request(
            validation_update,
            allow_api_key_without_env=is_default_app_provider,
        )
        normalized_id = provider_id.casefold()
        if normalized_id in seen_ids:
            raise ValueError(f"Provider {provider_id} 在同一次保存中重复。")
        seen_ids.add(normalized_id)
        provider_ids.append(provider_id)
        existing = definitions.get(normalized_id)
        if is_default_app_provider:
            auth_path = default_codex_auth_path(path)
            default_auth_before = read_codex_auth_api_key(path)
            if api_key:
                default_auth_after = api_key
            elif default_auth_before:
                default_auth_after = default_auth_before
            else:
                raise ValueError("默认 Codex App Provider 尚未配置 API key，请填写后再保存。")
        section = _section_range(candidate_text, provider_id)
        if section is None:
            if section_body is not None and not is_default_app_provider:
                candidate_text = _add_provider_section_text(
                    candidate_text, provider_id, section_body
                )
            elif is_default_app_provider:
                newline = _preferred_newline(candidate_text)
                default_body = newline.join(
                    (
                        f'name = "{_toml_string(provider_id)}"',
                        f'base_url = "{_toml_string(base_url)}"',
                        'wire_api = "responses"',
                        "requires_openai_auth = true",
                    )
                )
                candidate_text = _add_provider_section_text(
                    candidate_text, provider_id, default_body
                )
            else:
                candidate_text = _add_provider_section(
                    candidate_text, provider_id, base_url, env_key
                )
            if not is_default_app_provider:
                profile_path = path.parent / f"{provider_id}.config.toml"
                newline = _preferred_newline(candidate_text)
                profile_text = f'model_provider = "{_toml_string(provider_id)}"{newline}'
                profiles_to_create.append((profile_path, profile_text))
        else:
            _start, _end, body = section
            if is_default_app_provider:
                newline = _preferred_newline(candidate_text)
                # Codex App owns the auth-related fields in this section. Only
                # change base_url so its special flags survive a HUD round trip.
                next_body = _set_quoted_value(body, "base_url", base_url, newline)
            elif section_body is not None:
                next_body = section_body
            else:
                newline = _preferred_newline(candidate_text)
                next_body = _set_quoted_value(body, "base_url", base_url, newline)
                if env_key or re.search(r"(?m)^[\t ]*env_key[\t ]*=", body):
                    next_body = _set_quoted_value(next_body, "env_key", env_key, newline)
            candidate_text = _replace_section_body(candidate_text, provider_id, next_body)

        if is_default_app_provider:
            default_provider_before_section = _provider_section_text(
                original_text, provider_id
            )
            default_provider_candidate_section = _provider_section_text(
                candidate_text, provider_id
            )
        else:
            previous_env_key = existing.env_key if existing else ""
            previous_env_value = _user_environment_value(previous_env_key)
            target_env_value = _user_environment_value(env_key)
            if env_key not in env_before:
                env_before[env_key] = target_env_value
            if api_key:
                env_after[env_key] = api_key
            elif not target_env_value:
                if previous_env_key and previous_env_value and previous_env_key == env_key:
                    env_after[env_key] = previous_env_value
                elif is_new or not existing or previous_env_key != env_key:
                    raise ValueError(f"请为 Provider {provider_id} 填写 API key。")

    _validate_toml(candidate_text)
    config_changed = candidate_text != original_text
    config_written = False
    created_profiles: list[Path] = []
    env_written: list[str] = []
    try:
        if config_changed:
            _write_text_atomically(path, candidate_text, original_text)
            config_written = True
        for profile_path, profile_text in profiles_to_create:
            if _write_new_text_atomically(profile_path, profile_text):
                created_profiles.append(profile_path)
        if default_auth_after is not None:
            (
                auth_changed,
                auth_path,
                auth_original_text,
                auth_original_exists,
                auth_candidate_text,
            ) = _write_codex_auth_api_key(path, default_auth_after)
        for env_key, value in env_after.items():
            if _user_environment_value(env_key) == value:
                continue
            _set_user_environment_value(env_key, value)
            env_written.append(env_key)
        if config_changed and _read_text_exact(path) != candidate_text:
            raise RuntimeError("Codex config 写入后校验失败。")
        for profile_path in created_profiles:
            expected_profile = next(
                text for candidate_path, text in profiles_to_create
                if candidate_path == profile_path
            )
            if _read_text_exact(profile_path) != expected_profile:
                raise RuntimeError("Codex provider profile 写入后校验失败。")
        for env_key, value in env_after.items():
            if _user_environment_value(env_key) != value:
                raise RuntimeError("用户环境变量写入后校验失败。")
    except Exception:
        rollback_failed = False
        if auth_changed and auth_path is not None:
            try:
                _restore_codex_auth_text(
                    auth_path,
                    auth_original_text,
                    auth_original_exists,
                    auth_candidate_text,
                )
            except Exception:
                rollback_failed = True
        for env_key in reversed(env_written):
            try:
                _set_user_environment_value(env_key, env_before.get(env_key, ""))
            except Exception:
                rollback_failed = True
        for profile_path in reversed(created_profiles):
            try:
                profile_path.unlink()
            except OSError:
                rollback_failed = True
        if config_written:
            try:
                _write_text_atomically(path, original_text, candidate_text)
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise RuntimeError(
                "Provider 保存失败，且之前的部分值无法完全恢复，请检查 config.toml 和用户环境变量。"
            ) from None
        raise

    return {
        "changed": bool(config_changed or created_profiles or env_written or auth_changed),
        "providerIds": provider_ids,
        "configPath": str(path),
        "profilePaths": [str(item) for item in created_profiles],
        "environmentKeys": list(env_written),
        "authPath": str(auth_path) if auth_path is not None else "",
        "authKeys": [CODEX_AUTH_API_KEY] if auth_changed else [],
        "defaultProviderEdited": bool(
            default_provider_before_section != default_provider_candidate_section
            or auth_changed
        ),
    }


def set_default_codex_provider(
    provider_id: str,
    *,
    config_path: str | Path | None = None,
) -> dict[str, object]:
    """Set the top-level ``model_provider`` key without touching other TOML.

    The running Codex Desktop re-reads config.toml when a new session starts
    (verified by a live probe: flipping ``model_provider`` took effect for the
    next thread with no app restart), so this is the hot-switch path for new
    App sessions.  Only the selected provider section/ID is validated; all
    unrelated TOML is preserved byte-for-byte and written atomically.
    """
    requested = str(provider_id or "").strip()
    if not requested:
        raise ValueError("供应商 ID 不能为空。")
    normalized = requested.casefold()
    if not PROVIDER_ID_PATTERN.fullmatch(requested):
        raise ValueError(
            f"供应商 ID「{requested}」不合法：仅允许字母、数字、下划线和连字符。"
        )
    path = (
        Path(config_path).expanduser()
        if config_path is not None
        else default_codex_config_path()
    )
    if not path.exists():
        raise FileNotFoundError(f"Codex config was not found: {path}")
    original_text = _read_text_exact(path)
    parsed = _parse_toml_mapping(original_text)
    current = str(parsed.get("model_provider") or "").strip().casefold()
    if current == normalized:
        return {"changed": False, "providerId": requested}
    raw_model_providers = parsed.get("model_providers")
    defined_ids = (
        {
            str(provider).strip().casefold()
            for provider in raw_model_providers
            if str(provider or "").strip()
        }
        if isinstance(raw_model_providers, Mapping)
        else set()
    )
    if normalized not in defined_ids and normalized not in RESERVED_BUILTIN_PROVIDER_IDS:
        raise ValueError(
            f"供应商「{requested}」未在 config.toml 的 [model_providers] 中定义，无法设为默认。"
        )
    newline = _preferred_newline(original_text)
    first_table = re.search(
        r"(?m)^[\t ]*\[\[?[^\r\n]+\]\]?\s*(?:#.*)?(?:\r?\n|$)",
        original_text,
    )
    head_end = first_table.start() if first_table else len(original_text)
    head = original_text[:head_end]
    tail = original_text[head_end:]
    candidate_head = _set_quoted_value(head, "model_provider", normalized, newline)
    # 顶层键必须与后续表头用换行分隔（head 为空或原文件末行无换行时都会被粘在一起）。
    candidate_head = candidate_head.rstrip("\r\n") + newline
    candidate_text = candidate_head + tail
    candidate_parsed = _parse_toml_mapping(candidate_text)
    if str(candidate_parsed.get("model_provider") or "").strip().casefold() != normalized:
        raise ValueError("config.toml 顶层 model_provider 写入后校验失败。")
    _write_text_atomically(path, candidate_text, original_text)
    return {"changed": True, "providerId": requested, "configPath": str(path)}


def clone_provider_with_bearer_key(
    source_provider: str,
    api_key: str,
    *,
    config_path: str | Path | None = None,
) -> dict[str, object]:
    """Clone a provider section with a fresh bearer token and switch default.

    Used for the "edit default provider's API key" flow: rewriting auth.json
    only takes effect after a Codex restart (the App Server loads credentials
    at startup and never re-reads them while running).  Instead this clones
    the provider's ``[model_providers.<id>]`` section into a new id that
    carries the new key in ``experimental_bearer_token`` — a config.toml field
    the running App re-reads hot (verified by a live probe: a newly added
    provider section with a bearer token was picked up by the App Server and
    used for the next request without restart) — then flips the top-level
    ``model_provider`` to the new id.  The original provider section is
    preserved untouched.
    """
    requested = str(source_provider or "").strip()
    if not requested:
        raise ValueError("要克隆的供应商 ID 不能为空。")
    normalized_source = requested.casefold()
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("API key 不能为空。")
    path = (
        Path(config_path).expanduser()
        if config_path is not None
        else default_codex_config_path()
    )
    if not path.exists():
        raise FileNotFoundError(f"Codex config was not found: {path}")
    original_text = _read_text_exact(path)
    parsed = _parse_toml_mapping(original_text)
    raw_model_providers = parsed.get("model_providers")
    definitions = (
        raw_model_providers
        if isinstance(raw_model_providers, Mapping)
        else {}
    )
    source_entry = definitions.get(normalized_source)
    if not isinstance(source_entry, Mapping):
        raise ValueError(f"供应商「{requested}」未在 config.toml 的 [model_providers] 中定义，无法克隆。")
    existing_ids = {str(pid or "").strip().casefold() for pid in definitions}
    base = f"{normalized_source}-copy"
    new_id = base
    suffix = 2
    while new_id in existing_ids:
        new_id = f"{base}{suffix}"
        suffix += 1
    if not PROVIDER_ID_PATTERN.fullmatch(new_id):
        raise ValueError(f"克隆生成的供应商 ID「{new_id}」不合法。")
    section = _section_range(original_text, normalized_source)
    if section is None:
        raise ValueError(f"供应商「{requested}」的配置段不存在。")
    _start, _end, source_body = section
    newline = _preferred_newline(original_text)
    source_name = _get_quoted_value(source_body, "name") or requested
    base_url = _get_quoted_value(source_body, "base_url")
    if not base_url:
        raise ValueError(f"供应商「{requested}」缺少 base_url，无法克隆。")
    # Keep every provider option that is not authentication material.  In
    # particular, tenant headers/query parameters and stream timeouts are
    # connection behaviour and must survive the clone.
    auth_keys = {
        "env_key",
        "auth_key",
        "api_key",
        "experimental_bearer_token",
        "requires_openai_auth",
    }
    retained_lines: list[str] = []
    name_replaced = False
    assignment_pattern = re.compile(
        r"^(?P<indent>[\t ]*)(?P<key>[A-Za-z0-9_-]+)(?P<rest>[\t ]*=.*)$"
    )
    for line in _normalize_body(source_body, newline).split(newline):
        match = assignment_pattern.match(line)
        if match is not None:
            field = match.group("key").casefold()
            if field in auth_keys:
                continue
            if field == "name":
                retained_lines.append(
                    f'{match.group("indent")}name = "{_toml_string(f"{source_name}（副本）")}"'
                )
                name_replaced = True
                continue
        retained_lines.append(line)
    if not name_replaced:
        retained_lines.insert(0, f'name = "{_toml_string(f"{source_name}（副本）")}"')
    retained_lines.append(f'experimental_bearer_token = "{_toml_string(key)}"')
    new_body = newline.join(retained_lines).strip(newline)
    candidate_text = _add_provider_section_text(original_text, new_id, new_body)
    # 顶层 model_provider 切到新供应商（复用 set_default_codex_provider 的 head/tail 改写）。
    first_table = re.search(
        r"(?m)^[\t ]*\[\[?[^\r\n]+\]\]?\s*(?:#.*)?(?:\r?\n|$)",
        candidate_text,
    )
    head_end = first_table.start() if first_table else len(candidate_text)
    head = candidate_text[:head_end]
    tail = candidate_text[head_end:]
    candidate_head = _set_quoted_value(head, "model_provider", new_id, newline)
    candidate_head = candidate_head.rstrip("\r\n") + newline
    candidate_text = candidate_head + tail
    _validate_toml(candidate_text)
    candidate_parsed = _parse_toml_mapping(candidate_text)
    candidate_providers = candidate_parsed.get("model_providers")
    new_entry = (
        candidate_providers.get(new_id)
        if isinstance(candidate_providers, Mapping)
        else None
    )
    if not isinstance(new_entry, Mapping):
        raise ValueError("克隆后的供应商段写入校验失败。")
    if str(candidate_parsed.get("model_provider") or "").strip().casefold() != new_id:
        raise ValueError("config.toml 顶层 model_provider 写入后校验失败。")
    _write_text_atomically(path, candidate_text, original_text)
    return {
        "changed": True,
        "providerId": requested,
        "newProviderId": new_id,
        "name": f"{source_name}（副本）",
        "configPath": str(path),
    }


MAX_PROVIDER_MODELS_RESPONSE_BYTES = 8 * 1024 * 1024


def _request_provider_models(
    base_url: str,
    api_key: str,
    timeout_seconds: float = 8.0,
) -> str:
    """Request ``GET {base_url}/models`` and return the raw response body.

    ``base_url`` typically ends in ``/v1``; the models endpoint is appended
    directly.  Returns the (possibly empty) response text on a successful HTTP
    status and raises ``ValueError`` for network, timeout, HTTP, or malformed
    responses so the caller can surface a user-facing message.
    """
    target = str(base_url or "").strip().rstrip("/")
    if not target:
        raise ValueError("base_url 不能为空。")
    parsed_target = urlsplit(target)
    if parsed_target.scheme.lower() not in {"http", "https"} or not parsed_target.netloc:
        raise ValueError("base_url 必须使用 HTTP(S)。")
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("API key 不能为空。")
    models_url = f"{target}/models"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": "codex-usage-hud",
    }
    try:
        request = Request(models_url, headers=headers)
        with urlopen(request, timeout=max(1.0, timeout_seconds)) as response:
            raw = response.read(MAX_PROVIDER_MODELS_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ValueError(f"连通性测试失败（HTTP {exc.code}）。") from exc
    except (
        OSError,
        URLError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise ValueError(f"连通性测试失败：{exc}") from exc
    if len(raw) > MAX_PROVIDER_MODELS_RESPONSE_BYTES:
        raise ValueError("模型列表响应体过大，已中止解析。")
    return raw.decode("utf-8", errors="replace")


def _parse_model_ids(body: str) -> list[str]:
    """Extract model identifiers from a Codex-compatible ``/models`` response.

    Accepts an OpenAI-style ``{"data": [{"id": "..."}]}`` payload as well as a
    bare JSON array.  Unknown, truncated, or caretaker objects are ignored.
    """
    text = str(body or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        return []

    def candidate_id(item: object) -> str:
        if isinstance(item, Mapping):
            value = item.get("id") or item.get("name") or item.get("model")
            return str(value or "").strip() if value else ""
        if isinstance(item, str):
            return item.strip()
        return ""

    raw_items: list[object] = []
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, Mapping):
        data = payload.get("data")
        raw_items = data if isinstance(data, list) else []
    model_ids: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        model_id = candidate_id(item)
        if not model_id or model_id.casefold() in seen:
            continue
        seen.add(model_id.casefold())
        model_ids.append(model_id)
    return model_ids


def fetch_provider_models(
    base_url: str,
    api_key: str,
    timeout_seconds: float = 8.0,
) -> list[str]:
    """Query ``GET {base_url}/models`` and return the available model IDs.

    This is the primary endpoint used for both connectivity testing and model
    listing.  Returns ``[]`` when the endpoint answers successfully but the
    body contains no recognizable model entries; raises ``ValueError`` with a
    user-facing message on network, timeout, HTTP, or malformed responses.
    """
    body = _request_provider_models(base_url, api_key, timeout_seconds)
    return _parse_model_ids(body)


def verify_provider_connectivity(
    base_url: str,
    api_key: str,
    timeout_seconds: float = 8.0,
) -> bool:
    """Probe ``GET {base_url}/models`` as a pure connectivity test.

    Backward-compatible wrapper around :func:`fetch_provider_models`: returns
    ``True`` whenever the models endpoint answers with a successful HTTP status
    for the given API key, regardless of whether the body contains models.
    """
    _request_provider_models(base_url, api_key, timeout_seconds)
    return True


def delete_provider_config(
    provider_id: str,
    *,
    config_path: str | Path | None = None,
) -> dict[str, object]:
    """Remove one non-default provider from Codex TOML configuration.

    The provider table, nested tables, and profiles that explicitly select the
    provider are removed from 'config.toml'. HUD-created profile files are
    removed only when their 'model_provider' value matches the target. When the
    provider declares an ``env_key``, the matching user environment variable is
    also removed so credentials do not accumulate after deletion.
    """
    provider = str(provider_id or "").strip()
    if not PROVIDER_ID_PATTERN.fullmatch(provider):
        raise ValueError("Provider ID 只能使用字母、数字、连字符或下划线。")
    normalized_provider = provider.casefold()
    path = (
        Path(config_path).expanduser()
        if config_path is not None
        else default_codex_config_path()
    )
    if not path.exists():
        return {
            "changed": False,
            "providerId": normalized_provider,
            "configPath": str(path),
            "removedTables": [],
            "profilePaths": [],
            "environmentKeys": [],
        }
    original_text = _read_text_exact(path)
    parsed_config = _parse_toml_mapping(original_text)
    default_provider = str(parsed_config.get("model_provider") or "").strip().casefold()
    if default_provider == normalized_provider:
        raise ValueError("默认 Codex App Provider 不支持删除供应商配置。")

    raw_providers = parsed_config.get("model_providers")
    provider_env_key = ""
    if isinstance(raw_providers, Mapping):
        raw_entry = raw_providers.get(provider)
        if isinstance(raw_entry, Mapping):
            provider_env_key = str(raw_entry.get("env_key") or "").strip()

    candidate_text, removed_tables = _remove_provider_tables(
        original_text,
        provider,
    )
    profile_backups: list[tuple[Path, str]] = []
    if path.parent.is_dir():
        for profile_path in path.parent.glob("*.config.toml"):
            try:
                profile_text = _read_text_exact(profile_path)
                profile_config = _parse_toml_mapping(profile_text)
            except OSError:
                continue
            if (
                str(profile_config.get("model_provider") or "").strip().casefold()
                == normalized_provider
            ):
                profile_backups.append((profile_path, profile_text))

    config_changed = candidate_text != original_text
    if config_changed:
        _validate_toml(candidate_text)
    deleted_profiles: list[Path] = []
    config_written = False
    env_deleted: list[str] = []
    try:
        if config_changed:
            _write_text_atomically(path, candidate_text, original_text)
            config_written = True
        for profile_path, _profile_text in profile_backups:
            profile_path.unlink()
            deleted_profiles.append(profile_path)
        if provider_env_key:
            _delete_user_environment_value(provider_env_key)
            env_deleted.append(provider_env_key)
        if config_written and _read_text_exact(path) != candidate_text:
            raise RuntimeError("Codex config 删除后校验失败。")
        if any(profile_path.exists() for profile_path in deleted_profiles):
            raise RuntimeError("Codex provider profile 删除后校验失败。")
    except Exception:
        rollback_failed = False
        if config_written:
            try:
                _write_text_atomically(path, original_text, candidate_text)
            except Exception:
                rollback_failed = True
        deleted_set = set(deleted_profiles)
        for profile_path, profile_text in profile_backups:
            if profile_path not in deleted_set:
                continue
            try:
                _write_text_exact(profile_path, profile_text)
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise RuntimeError(
                "Provider 删除失败，且之前的部分值无法完全恢复，请检查 config.toml 和 Provider profile。"
            ) from None
        raise

    changed = bool(config_changed or deleted_profiles or env_deleted)
    return {
        "changed": changed,
        "providerId": normalized_provider,
        "configPath": str(path),
        "removedTables": removed_tables,
        "profilePaths": [str(item) for item in deleted_profiles],
        "environmentKeys": env_deleted,
    }


def fetch_provider_models_for_cli(
    provider_id: str,
    *,
    config_path: str | Path | None = None,
    timeout_seconds: float = 8.0,
) -> dict[str, object]:
    """Resolve a provider's model list from its stored Codex configuration.

    Reads the ``[model_providers.<id>]`` section from ``config.toml`` to obtain
    ``base_url``. Ordinary profiles use their user environment variable; the
    default Codex App provider uses ``auth.json``. Returns
    ``{"provider", "baseUrl", "envKey", "models": [...]}`` on success and
    raises ``ValueError`` with a user-facing message otherwise.
    """
    provider = str(provider_id or "").strip()
    if not provider or not PROVIDER_ID_PATTERN.fullmatch(provider):
        raise ValueError("Provider ID 无效。")
    definitions = read_provider_definitions(config_path)
    definition = definitions.get(provider.casefold())
    if definition is None:
        raise ValueError(f"未在 config.toml 中找到 Provider {provider} 的配置。")
    base_url = definition.base_url
    env_key = definition.env_key
    if not base_url:
        raise ValueError(f"Provider {provider} 未配置 base_url。")
    if definition.auth_key:
        api_key = read_codex_auth_api_key(config_path, auth_key=definition.auth_key)
        missing_message = "Codex auth.json 中没有可用的 API key，请先在编辑供应商中保存。"
    else:
        if not env_key:
            raise ValueError(f"Provider {provider} 未配置用户环境变量名称。")
        api_key = _user_environment_value(env_key)
        missing_message = (
            f"用户环境变量 {env_key} 中没有可用的 API key，请先在新增/编辑供应商中保存。"
        )
    if not api_key:
        raise ValueError(missing_message)
    models = fetch_provider_models(base_url, api_key, timeout_seconds)
    return {
        "provider": provider,
        "baseUrl": base_url,
        "envKey": env_key,
        "authKey": definition.auth_key,
        "models": models,
    }


MAX_PROVIDER_CHAT_RESPONSE_BYTES = 512 * 1024
CHAT_COMPLETIONS_SUFFIX = "/chat/completions"


def _chat_completions_url(base_url: str) -> str:
    """Resolve the chat completions endpoint for a provider base URL.

    Some providers (for example ``chatapi.weixin.qq.com``) store the full
    ``.../chat/completions`` path in ``base_url`` and expose no ``/models``
    endpoint, so appending a suffix would produce a broken URL.
    """
    target = str(base_url or "").strip().rstrip("/")
    if target.casefold().endswith(CHAT_COMPLETIONS_SUFFIX):
        return target
    return f"{target}{CHAT_COMPLETIONS_SUFFIX}"


def _parse_chat_reply(body: str) -> str:
    """Extract the assistant text from an OpenAI-style chat response."""
    text = str(body or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except Exception:
        return ""
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    message = first.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        if content is not None:
            return str(content).strip()
    return str(first.get("text") or "").strip()


def _http_error_message(exc: HTTPError) -> str:
    """Read a provider error detail (``{"error": {"message": ...}}``)."""
    raw: bytes | None = None
    fp = getattr(exc, "fp", None)
    if isinstance(fp, bytes):
        raw = fp
    elif fp is not None:
        try:
            raw = fp.read(MAX_PROVIDER_CHAT_RESPONSE_BYTES)
        except Exception:
            raw = None
    if not raw:
        return ""
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return raw.decode("utf-8", errors="replace")[:200].strip()
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            message = str(error.get("message") or "").strip()
            if message:
                return message
            code = str(error.get("code") or "").strip()
            if code:
                return f"错误码 {code}"
        if str(payload.get("message") or "").strip():
            return str(payload.get("message") or "").strip()
        if str(payload.get("detail") or "").strip():
            return str(payload.get("detail") or "").strip()
    return ""


def send_provider_chat_probe(
    base_url: str,
    api_key: str,
    model: str,
    message: str = "hi",
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Send a short chat message to verify model availability.

    Unlike the models-list endpoint, some providers with no ``/models``
    route still answer chat completions.  This probe posts ``hi`` (or a
    caller-supplied message) and returns ``{"ok": True, "reply": ...}`` on
    success or ``{"ok": False, "error": ...}`` otherwise instead of raising,
    so the settings UI can show the provider's own error detail (for example
    an invalid model name).
    """
    target = str(base_url or "").strip()
    if not target:
        return {"ok": False, "error": "base_url 不能为空。"}
    parsed_target = urlsplit(target)
    if parsed_target.scheme.lower() not in {"http", "https"} or not parsed_target.netloc:
        return {"ok": False, "error": "base_url 必须使用 HTTP(S)。"}
    key = str(api_key or "").strip()
    if not key:
        return {"ok": False, "error": "API key 不能为空。"}
    normalized_model = str(model or "").strip()
    if not normalized_model:
        return {"ok": False, "error": "请输入自定义模型名称。"}
    probe_message = str(message or "").strip() or "hi"
    chat_url = _chat_completions_url(target)
    payload = json.dumps(
        {
            "model": normalized_model,
            "messages": [{"role": "user", "content": probe_message}],
            "max_tokens": 16,
        }
    ).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "codex-usage-hud",
    }
    try:
        request = Request(chat_url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=max(1.0, timeout_seconds)) as response:
            raw = response.read(MAX_PROVIDER_CHAT_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        detail = _http_error_message(exc)
        suffix = f"：{detail}" if detail else ""
        return {"ok": False, "error": f"聊天测试失败（HTTP {exc.code}）{suffix}"}
    except (OSError, URLError) as exc:
        return {"ok": False, "error": f"聊天测试失败：{exc}"}
    if len(raw) > MAX_PROVIDER_CHAT_RESPONSE_BYTES:
        return {"ok": False, "error": "聊天响应体过大，已中止解析。"}
    reply = _parse_chat_reply(raw.decode("utf-8", errors="replace"))
    if not reply:
        return {
            "ok": False,
            "error": "聊天测试未返回可识别的回复内容（端点可能不是 OpenAI 兼容的 chat/completions）。",
        }
    return {"ok": True, "reply": reply, "model": normalized_model}


def send_cli_chat_probe(
    provider_id: str,
    model: str,
    message: str = "hi",
    *,
    config_path: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Resolve a provider's stored configuration and run a chat probe.

    Mirrors :func:`fetch_provider_models_for_cli`: reads ``base_url`` and
    ``env_key`` from ``config.toml``, reads the API key from the matching user
    environment variable, and posts a short chat message.
    """
    provider = str(provider_id or "").strip()
    if not provider or not PROVIDER_ID_PATTERN.fullmatch(provider):
        return {"ok": False, "error": "Provider ID 无效。"}
    definitions = read_provider_definitions(config_path)
    definition = definitions.get(provider.casefold())
    if definition is None:
        return {"ok": False, "error": f"未在 config.toml 中找到 Provider {provider} 的配置。"}
    base_url = definition.base_url
    env_key = definition.env_key
    if not base_url:
        return {"ok": False, "error": f"Provider {provider} 未配置 base_url。"}
    if not env_key:
        return {"ok": False, "error": f"Provider {provider} 未配置用户环境变量名称。"}
    api_key = _user_environment_value(env_key)
    if not api_key:
        return {
            "ok": False,
            "error": f"用户环境变量 {env_key} 中没有可用的 API key，请先在新增/编辑供应商中保存。",
        }
    result = send_provider_chat_probe(
        base_url,
        api_key,
        model,
        message=message,
        timeout_seconds=timeout_seconds,
    )
    result.setdefault("provider", provider)
    result.setdefault("baseUrl", base_url)
    result.setdefault("envKey", env_key)
    return result


__all__ = [
    "CODEX_AUTH_API_KEY",
    "OFFICIAL_ACCOUNT_TOKEN_KEYS",
    "RESERVED_BUILTIN_PROVIDER_IDS",
    "CodexProviderDefinition",
    "default_codex_auth_path",
    "default_codex_config_path",
    "codex_auth_uses_official_account",
    "delete_provider_config",
    "fetch_provider_models",
    "fetch_provider_models_for_cli",
    "read_provider_definitions",
    "read_codex_auth_api_key",
    "save_provider_configs",
    "send_cli_chat_probe",
    "send_provider_chat_probe",
    "verify_provider_connectivity",
]
