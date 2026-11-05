from unittest.mock import MagicMock
import ctypes

import pytest

from codex_usage_hud import provider_exit
from codex_usage_hud.windows_tray import WindowsTrayIcon
from codex_usage_hud.runtime_commands import _handle_renderer_settings_command


def test_windows_taskdialog_button_array_uses_native_packed_stride(monkeypatch):
    from ctypes import wintypes

    class NativeButton(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("id", ctypes.c_int), ("text", wintypes.LPCWSTR)]

    def show(config_pointer, button_pointer, _radio_pointer, _verified):
        config = config_pointer._obj
        assert ctypes.sizeof(config.buttons._type_) == ctypes.sizeof(ctypes.c_int) + ctypes.sizeof(ctypes.c_void_p)
        address = ctypes.addressof(config.radios.contents)
        second = NativeButton.from_address(address + ctypes.sizeof(NativeButton))
        assert second.id == 101 and second.text == "Beta"
        button_pointer._obj.value = 2
        return 0

    monkeypatch.setattr(ctypes, "WinDLL", lambda _name: type("Native", (), {"TaskDialogIndirect": staticmethod(show)})(), raising=False)
    assert provider_exit._windows_choose([("alpha", "Alpha"), ("beta", "Beta")], "alpha") == ""


@pytest.mark.parametrize("selected", ["", "beta"])
def test_native_guard_cancel_or_restore_before_allowing_exit(monkeypatch, selected):
    if provider_exit.os.name != "nt":
        monkeypatch.setattr(provider_exit.sys, "platform", "darwin")
    monkeypatch.setattr(provider_exit, "gateway_info", lambda: {"default_provider": "alpha"})
    monkeypatch.setattr(provider_exit, "read_provider_definitions", lambda: {})
    monkeypatch.setattr(provider_exit, "_windows_choose", lambda *_args: selected)
    monkeypatch.setattr(provider_exit, "_mac_choose", lambda *_args: selected)
    prepare = MagicMock()
    monkeypatch.setattr(provider_exit, "prepare_exit", prepare)
    assert provider_exit.confirm_gateway_exit() is bool(selected)
    assert prepare.call_count == bool(selected)


def test_cancelled_tray_exit_can_be_requested_again():
    callback = MagicMock(side_effect=[False, True])
    tray = WindowsTrayIcon(on_exit=callback)
    tray._request_exit()
    tray._request_exit()
    tray._request_exit()
    assert callback.call_count == 2


def test_renderer_cannot_bypass_active_gateway_exit_guard(monkeypatch):
    from codex_usage_hud import provider_gateway

    guard = MagicMock(side_effect=ValueError("select a provider first"))
    monkeypatch.setattr(provider_gateway, "validate_exit", guard)
    exit_requested = MagicMock()
    result = _handle_renderer_settings_command({"action": "exit"}, MagicMock(), MagicMock(), exit_requested)
    assert result["kind"] == "error"
    exit_requested.set.assert_not_called()
