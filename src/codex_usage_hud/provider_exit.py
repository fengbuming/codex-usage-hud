"""Native exit guard for the tray and CLI, including when no renderer is open."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys

from .codex_provider_config import read_provider_definitions
from .provider_gateway import gateway_info, prepare_exit

_LOGGER = logging.getLogger(__name__)
_WARNING = ("退出 HUD 后，统一代理可能断开，正在生成的回复和缓存代理地址的会话可能受影响。"
            "请先等待回复完成。请选择退出后使用的供应商；HUD 会先保存直连配置。"
            "若旧窗口无法发送，请重启 Codex 或重新启动 HUD。")


def _windows_choose(choices: list[tuple[str, str]], default: str) -> str:
    from ctypes import wintypes

    class Button(ctypes.Structure):
        _pack_ = 1  # TASKDIALOG_BUTTON uses the same commctrl.h packing as the config.
        _fields_ = [("id", ctypes.c_int), ("text", wintypes.LPCWSTR)]

    class Config(ctypes.Structure):
        # commctrl.h packs TASKDIALOGCONFIG at one-byte alignment.
        _pack_ = 1
        _fields_ = [
            ("size", wintypes.UINT), ("parent", wintypes.HWND), ("instance", wintypes.HINSTANCE),
            ("flags", wintypes.UINT), ("common", wintypes.UINT), ("title", wintypes.LPCWSTR),
            ("icon", ctypes.c_void_p), ("instruction", wintypes.LPCWSTR), ("content", wintypes.LPCWSTR),
            ("button_count", wintypes.UINT), ("buttons", ctypes.POINTER(Button)), ("default_button", ctypes.c_int),
            ("radio_count", wintypes.UINT), ("radios", ctypes.POINTER(Button)), ("default_radio", ctypes.c_int),
            ("verification", wintypes.LPCWSTR), ("expanded", wintypes.LPCWSTR),
            ("expanded_control", wintypes.LPCWSTR), ("collapsed_control", wintypes.LPCWSTR),
            ("footer_icon", ctypes.c_void_p), ("footer", wintypes.LPCWSTR),
            ("callback", ctypes.c_void_p), ("callback_data", ctypes.c_ssize_t), ("width", wintypes.UINT),
        ]

    radios = (Button * len(choices))(*(Button(100 + i, name) for i, (_, name) in enumerate(choices)))
    buttons = (Button * 1)(Button(1, "保存直连配置并退出 HUD"))
    selected = next((100 + i for i, (provider, _) in enumerate(choices) if provider == default), 100)
    config = Config()
    config.size = ctypes.sizeof(Config)
    config.flags = 0x0008 | 0x1000  # allow cancel; position relative to parent
    config.common = 0x0008  # Cancel
    config.title = "退出 Codex HUD"
    config.instruction = "退出会影响代理连接"
    config.content = _WARNING
    config.button_count, config.buttons, config.default_button = 1, buttons, 2
    config.radio_count, config.radios, config.default_radio = len(choices), radios, selected
    button, radio = ctypes.c_int(), ctypes.c_int()
    task_dialog = ctypes.WinDLL("comctl32").TaskDialogIndirect
    task_dialog.argtypes = [ctypes.POINTER(Config), ctypes.POINTER(ctypes.c_int),
                           ctypes.POINTER(ctypes.c_int), ctypes.c_void_p]
    task_dialog.restype = ctypes.c_long
    result = task_dialog(ctypes.byref(config), ctypes.byref(button), ctypes.byref(radio), None)
    if result < 0:
        raise OSError("无法显示代理退出确认，请从 HUD 设置页退出。")
    index = radio.value - 100
    return choices[index][0] if button.value == 1 and 0 <= index < len(choices) else ""


def _mac_choose(choices: list[tuple[str, str]], default: str) -> str:
    names = [name for _, name in choices]
    selected = next((name for provider, name in choices if provider == default), names[0])
    script = '''on run argv
set selectedName to item 1 of argv
set providerNames to items 2 thru -1 of argv
set chosen to choose from list providerNames with title "退出 Codex HUD" with prompt "退出 HUD 会影响代理连接。请先等待回复完成，选择退出后使用的供应商。保存失败将取消退出；旧窗口可能需要重启 Codex。" default items {selectedName} OK button name "保存并退出" cancel button name "取消"
if chosen is false then return ""
return item 1 of chosen
end run'''
    result = subprocess.run(["osascript", "-e", script, selected, *names], capture_output=True,
                            text=True, encoding="utf-8", timeout=300)
    if result.returncode:
        raise OSError("无法显示代理退出确认，请从 HUD 设置页退出。")
    chosen = result.stdout.strip()
    return next((provider for provider, name in choices if name == chosen), "")


def confirm_gateway_exit() -> bool:
    """Cancel on dialog/config errors; the caller must leave HUD running."""
    info = gateway_info()
    if not info:
        return True
    definitions = read_provider_definitions()
    choices = [(provider, f"{definition.name or provider} ({provider})")
               for provider, definition in definitions.items() if provider != "codex_hud"]
    if "openai" not in definitions:
        choices.append(("openai", "OpenAI (openai)"))
    try:
        if os.name == "nt":
            provider = _windows_choose(choices, info["default_provider"])
        elif sys.platform == "darwin":
            provider = _mac_choose(choices, info["default_provider"])
        else:
            _LOGGER.warning("请从 HUD 设置页选择退出后的供应商，再退出 HUD。")
            return False
        if not provider:
            return False
        prepare_exit(provider)
        return True
    except (OSError, ValueError, RuntimeError, AttributeError, subprocess.SubprocessError):
        _LOGGER.exception("provider_exit_cancelled")
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, "直连配置未保存，HUD 将继续运行。请从 HUD 设置页重试。", "未退出 HUD", 0x10)
        return False
