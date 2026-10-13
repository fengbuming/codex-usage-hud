import ctypes
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud.platforms import windows_cli


@pytest.mark.parametrize("title,expected", [
    ("⠙ Session | repo", "Session | repo"),
    ("⠹ Session | repo", "Session | repo"),
    ("Session | repo", "Session | repo"),
    ("Session | other", "Session | other"),
    ("⠙literal title", "⠙literal title"),
])
def test_terminal_title_ignores_only_animated_prefix(title, expected):
    assert windows_cli._terminal_title_key(title) == expected


class TerminalProbe:
    def __init__(self, titles):
        self.titles = {index + 10: title for index, title in enumerate(titles)}
        self.selected = False
        self.focused = False
        self._ole32 = SimpleNamespace(CoInitializeEx=MagicMock(return_value=0),
                                      CoUninitialize=MagicMock())
        self._local = SimpleNamespace()

    def _automation_for_thread(self):
        return 1

    def _raw_view_walker(self, automation):
        return 2

    def _element_from_handle(self, automation, hwnd):
        return 3

    def _children(self, walker, element):
        return [*self.titles, 4] if element == 3 else []

    def _element_int(self, element, index):
        return 50019 if element in self.titles else 50032

    def _element_bstr(self, element, index):
        if index == 30:
            return "TermControl" if element == 4 else ""
        return self.titles.get(element, "")

    def _retain(self, element):
        return element

    def _release(self, element):
        pass

    def _element_bool(self, element, index):
        return self.selected if element == 100 else self.focused

    def _method(self, element, index, *signature):
        if index == 16:
            def pattern(_, pattern_id, result):
                assert pattern_id == 10010
                ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p))[0] = 100
                return 0
            return pattern
        def action(_):
            if element == 100:
                self.selected = True
            else:
                self.focused = True
            return 0
        return action


@pytest.mark.parametrize("titles,status", [
    (["⠹ Session | repo"], "switched"),
    (["⠹ Session | repo", "⠼ Session | repo"], "terminal-ambiguous"),
    (["⠹ Other | repo"], "terminal-tab-not-found"),
])
def test_focus_terminal_matches_spinner_changes_and_rejects_duplicates(monkeypatch, titles, status):
    probe = TerminalProbe(titles)
    monkeypatch.setattr(windows_cli, "_UiaProbe", lambda: probe)
    api = object.__new__(windows_cli.WindowsCliApi)
    api.u = SimpleNamespace(GetForegroundWindow=lambda: 200)
    api.restore = MagicMock(return_value=True)
    assert api.focus_terminal([200], "⠙ Session | repo") == (status == "switched", status)
    if status == "switched":
        assert probe.selected and probe.focused
        api.restore.assert_called_once_with(200)
    else:
        api.restore.assert_not_called()
