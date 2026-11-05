"""Explicit gateway opt-in; changing the preference never terminates a relay."""

from dataclasses import replace

from . import codex_provider_config as config
from .provider_gateway import _state_db, activate_gateway, gateway_info, prepare_exit
from .provider_proxy import provider_proxy_relay


def set_gateway_enabled(store, enabled: bool, *, provider: str = "", config_path=None, relay=None) -> dict:
    if not isinstance(enabled, bool):
        raise ValueError("请指定供应商请求代理的开关状态。")
    path = config_path or config.default_codex_config_path()
    if not path.is_file():
        raise ValueError("未找到 Codex 配置，请先完成 Codex 初始化。")
    previous = store.load()
    original = config._read_text_exact(path)
    previous_gateway = gateway_info(path)
    if enabled:
        activate_gateway(path, relay if relay is not None else provider_proxy_relay)
        info = gateway_info(path)
        if not info:
            raise ValueError("供应商请求代理尚未启用。")
        result = {"providerId": info["default_provider"], "gateway": True}
    else:
        info = gateway_info(path)
        if info:
            if not provider:
                raise ValueError("请选择关闭代理后使用的供应商。")
            result = prepare_exit(provider, config_path=path)
        else:
            result = {"gateway": False}
    updated = config._read_text_exact(path)
    try:
        store.save(replace(previous, provider_gateway_enabled=enabled))
    except OSError:
        # Roll back only our own write; concurrent external edits must survive.
        if updated != original:
            config._write_text_atomically(path, original, updated, private=True)
            if previous_gateway:
                with _state_db(path) as db:
                    db.execute("INSERT OR REPLACE INTO settings VALUES ('default_provider', ?)",
                               (previous_gateway["default_provider"],))
        raise
    return {**result, "enabled": enabled, "requiresConfigReload": True}
