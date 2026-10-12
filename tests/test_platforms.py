"""Unit tests for cross-platform Codex path helpers."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms import get_current_platform
from codex_usage_hud.platforms import session_switch as session_switch_module
from codex_usage_hud.platforms.base import BasePlatform
from codex_usage_hud.platforms.linux import LinuxPlatform
from codex_usage_hud.platforms.macos import MacOSPlatform
from codex_usage_hud.platforms.session_switch import (
    SessionSwitchController,
    SessionSwitchRequest,
    SessionSwitchResult,
    WindowsSearchSessionSwitchBackend,
)
from codex_usage_hud.platforms.windows import (
    MOUSE_HOOK_ENV,
    _UIA_LIST_ITEM_CONTROL_TYPE_ID,
    _UIA_TEXT_CONTROL_TYPE_ID,
    _MsaaTitleProbe,
    _env_flag,
    _UiaTitleNode,
    _UiaTitleProbe,
    WindowsPlatform,
)


class ActiveSessionDetectionTests(unittest.TestCase):
    def test_detect_active_session_returns_newest_jsonl_file(self) -> None:
        platform = get_current_platform()

        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_root = Path(temp_dir)
            nested_dir = sessions_root / "nested"
            nested_dir.mkdir()

            older_session = sessions_root / "older-session.jsonl"
            newer_session = nested_dir / "newer-session.jsonl"

            older_session.write_text('{"id": "older"}\n', encoding="utf-8")
            newer_session.write_text('{"id": "newer"}\n', encoding="utf-8")

            os.utime(older_session, (1_000, 1_000))
            os.utime(newer_session, (2_000, 2_000))

            active_session = platform.detect_active_session(sessions_root)

            self.assertEqual(active_session, newer_session)

    def test_detect_active_session_considers_archived_sessions_sibling(self) -> None:
        platform = get_current_platform()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            archived_root = root / "archived_sessions" / "2026"
            sessions_root.mkdir()
            archived_root.mkdir(parents=True)

            current_session = sessions_root / "current-session.jsonl"
            archived_session = archived_root / "archived-session.jsonl"

            current_session.write_text('{"id": "current"}\n', encoding="utf-8")
            archived_session.write_text('{"id": "archived"}\n', encoding="utf-8")

            os.utime(current_session, (1_000, 1_000))
            os.utime(archived_session, (2_000, 2_000))

            active_session = platform.detect_active_session(sessions_root)

            self.assertEqual(active_session, archived_session)


class PlatformFactoryTests(unittest.TestCase):
    def test_get_current_platform_returns_platform_instance(self) -> None:
        platform = get_current_platform()

        self.assertIsNotNone(platform)
        self.assertIsInstance(platform, BasePlatform)

        if sys.platform.startswith("win"):
            self.assertIsInstance(platform, WindowsPlatform)
        elif sys.platform == "darwin":
            self.assertIsInstance(platform, MacOSPlatform)
        elif sys.platform.startswith("linux"):
            self.assertIsInstance(platform, LinuxPlatform)
        else:
            self.fail(f"Unexpected platform under test: {sys.platform}")

    def test_cross_platform_imports_do_not_require_winfunctype(self) -> None:
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(SRC_ROOT)
            if not existing_pythonpath
            else str(SRC_ROOT) + os.pathsep + existing_pythonpath
        )
        script = (
            "import ctypes\n"
            "if hasattr(ctypes, 'WINFUNCTYPE'):\n"
            "    delattr(ctypes, 'WINFUNCTYPE')\n"
            "import codex_usage_hud.cli\n"
            "import codex_usage_hud.platforms.windows\n"
            "import codex_usage_hud.platforms.windows_tracker\n"
            "print('cross-platform-import-ok')\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cross-platform-import-ok", result.stdout)


class SessionSwitchControllerTests(unittest.TestCase):
    class _Backend:
        def __init__(self, name: str, result: SessionSwitchResult) -> None:
            self.name = name
            self.result = result
            self.requests: list[SessionSwitchRequest] = []

        def activate(self, request: SessionSwitchRequest) -> SessionSwitchResult:
            self.requests.append(request)
            return self.result

    def test_controller_returns_first_successful_backend(self) -> None:
        first = self._Backend(
            "native",
            SessionSwitchResult(ok=False, status="unsupported", backend="native"),
        )
        second = self._Backend(
            "cdp",
            SessionSwitchResult(ok=True, status="switched", backend="cdp"),
        )

        controller = SessionSwitchController([first, second])
        result = controller.activate_session(
            session_id="thread-1",
            title="Target Thread",
            workdir="E:\\Work",
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.backend, "cdp")
        self.assertEqual(len(first.requests), 1)
        self.assertEqual(len(second.requests), 1)
        self.assertEqual(second.requests[0].title, "Target Thread")

    def test_controller_stops_on_missing_target(self) -> None:
        first = self._Backend(
            "native",
            SessionSwitchResult(ok=False, status="missing-target", backend="native"),
        )
        second = self._Backend(
            "cdp",
            SessionSwitchResult(ok=True, status="switched", backend="cdp"),
        )

        controller = SessionSwitchController([first, second])
        result = controller.activate_session()

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "missing-target")
        self.assertEqual(len(first.requests), 1)
        self.assertEqual(len(second.requests), 0)

    def test_controller_can_restrict_activation_to_cdp_backend(self) -> None:
        keyboard = self._Backend(
            "windows-search",
            SessionSwitchResult(
                ok=True,
                status="switched",
                backend="windows-search",
            ),
        )
        cdp = self._Backend(
            "cdp",
            SessionSwitchResult(ok=False, status="thread-not-found", backend="cdp"),
        )

        controller = SessionSwitchController([cdp, keyboard])
        result = controller.activate_session(
            session_id="thread-1",
            title="Target Thread",
            backend_names=("cdp",),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.backend, "cdp")
        self.assertEqual(len(cdp.requests), 1)
        self.assertEqual(len(keyboard.requests), 0)

    def test_windows_search_backend_opens_current_codex_search_with_ctrl_k(self) -> None:
        platform = SimpleNamespace(
            get_active_conversation_title=mock.Mock(
                side_effect=["Previous Thread", "Target Thread"]
            )
        )
        tracker = SimpleNamespace(enabled=True, activate_main_window=mock.Mock(return_value=123))
        backend = WindowsSearchSessionSwitchBackend(
            platform,
            timeout_seconds=0.5,
            settle_seconds=0.1,
            poll_seconds=0.05,
        )

        with (
            mock.patch.object(session_switch_module.sys, "platform", "win32"),
            mock.patch.object(
                session_switch_module,
                "CodexWindowTracker",
                return_value=tracker,
            ),
            mock.patch.object(
                session_switch_module,
                "_temporary_clipboard_text",
                return_value=nullcontext(True),
            ),
            mock.patch.object(
                session_switch_module,
                "_send_virtual_keys",
                return_value=True,
            ) as send_keys,
            mock.patch.object(session_switch_module.time, "sleep"),
        ):
            result = backend.activate(
                SessionSwitchRequest(session_id="thread-1", title="Target Thread")
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.backend, "windows-search")
        self.assertEqual(
            send_keys.call_args_list,
            [
                mock.call(
                    session_switch_module._VK_CONTROL,
                    session_switch_module._VK_K,
                ),
                mock.call(
                    session_switch_module._VK_CONTROL,
                    session_switch_module._VK_A,
                ),
                mock.call(
                    session_switch_module._VK_CONTROL,
                    session_switch_module._VK_V,
                ),
                mock.call(
                    session_switch_module._VK_CONTROL,
                    session_switch_module._VK_1,
                ),
            ],
        )


    def test_windows_search_failure_dismisses_modal_only_while_codex_is_active(self) -> None:
        for active in (True, False):
            with self.subTest(active=active):
                tracker = SimpleNamespace(
                    enabled=True,
                    activate_main_window=mock.Mock(return_value=123),
                    is_active=mock.Mock(return_value=active),
                )
                platform = SimpleNamespace(
                    get_active_conversation_title=mock.Mock(return_value="Previous Thread")
                )
                backend = WindowsSearchSessionSwitchBackend(platform)
                with (
                    mock.patch.object(session_switch_module.sys, "platform", "win32"),
                    mock.patch.object(session_switch_module, "CodexWindowTracker", return_value=tracker),
                    mock.patch.object(session_switch_module, "_temporary_clipboard_text", return_value=nullcontext(True)),
                    mock.patch.object(session_switch_module, "_send_virtual_keys", return_value=True) as send_keys,
                    mock.patch.object(session_switch_module.time, "sleep"),
                    mock.patch.object(session_switch_module.time, "monotonic", side_effect=[0.0, 10.0]),
                ):
                    result = backend.activate(SessionSwitchRequest(session_id="thread-1", title="Target Thread"))
                self.assertEqual(result.status, "search-shortcut-timeout")
                tracker.is_active.assert_called_once_with(123)
                self.assertEqual(
                    mock.call(session_switch_module._VK_ESCAPE) in send_keys.call_args_list,
                    active,
                )

    def test_windows_search_failed_paste_dismisses_modal(self) -> None:
        tracker = SimpleNamespace(
            enabled=True,
            activate_main_window=mock.Mock(return_value=123),
            is_active=mock.Mock(return_value=True),
        )
        platform = SimpleNamespace(get_active_conversation_title=lambda: "Previous Thread")
        with (
            mock.patch.object(session_switch_module.sys, "platform", "win32"),
            mock.patch.object(session_switch_module, "CodexWindowTracker", return_value=tracker),
            mock.patch.object(session_switch_module, "_temporary_clipboard_text", return_value=nullcontext(True)),
            mock.patch.object(session_switch_module, "_send_virtual_keys", side_effect=[True, True, False, True]) as send_keys,
            mock.patch.object(session_switch_module.time, "sleep"),
        ):
            result = WindowsSearchSessionSwitchBackend(platform).activate(
                SessionSwitchRequest(session_id="thread-1", title="Target Thread")
            )
        self.assertEqual(result.status, "sendinput-error")
        self.assertEqual(send_keys.call_args_list[-1], mock.call(session_switch_module._VK_ESCAPE))


class WindowsActiveTitleTests(unittest.TestCase):
    def test_windows_platform_initializes_native_active_title_probes_by_default(self) -> None:
        with mock.patch(
            "codex_usage_hud.platforms.windows.CodexCdpProbe",
            return_value=object(),
        ), mock.patch(
            "codex_usage_hud.platforms.windows._UiaTitleProbe",
            return_value=object(),
        ) as uia_probe, mock.patch(
            "codex_usage_hud.platforms.windows._MsaaTitleProbe",
            return_value=object(),
        ) as msaa_probe:
            platform = WindowsPlatform()

        self.assertIsNotNone(platform._cdp_probe)
        self.assertIsNotNone(platform._uia_title_probe)
        self.assertIsNotNone(platform._title_probe)
        uia_probe.assert_called_once()
        msaa_probe.assert_called_once()

    def test_windows_platform_suspend_uses_cdp_only_for_active_titles(self) -> None:
        class _FakeCdpProbe:
            def snapshot(self) -> object | None:
                return None

        class _FakeTitleProbe:
            def __init__(self) -> None:
                self.calls = 0

            def conversation_title(self, hwnd: int) -> str | None:
                del hwnd
                self.calls += 1
                return "Native Title"

        native_probe = _FakeTitleProbe()
        platform = object.__new__(WindowsPlatform)
        platform._last_observed_title = ""
        platform._last_observed_session_id = ""
        platform._cdp_probe = _FakeCdpProbe()
        platform._uia_title_probe = native_probe
        platform._title_probe = native_probe
        platform._find_codex_window = lambda: 123  # type: ignore[method-assign]

        platform.suspend_native_active_title()

        self.assertTrue(platform.supports_active_title_polling())
        self.assertFalse(platform.supports_active_title_events())
        self.assertIsNone(platform.get_active_conversation_title())
        self.assertEqual(native_probe.calls, 0)

        platform.resume_native_active_title()

        self.assertEqual(platform.get_active_conversation_title(), "Native Title")
        self.assertEqual(native_probe.calls, 1)

    def test_windows_platform_suspend_skips_native_event_watcher(self) -> None:
        with mock.patch(
            "codex_usage_hud.platforms.windows.CodexCdpProbe",
            return_value=object(),
        ), mock.patch(
            "codex_usage_hud.platforms.windows._UiaTitleProbe",
            return_value=object(),
        ) as uia_probe, mock.patch(
            "codex_usage_hud.platforms.windows._MsaaTitleProbe",
            return_value=object(),
        ):
            platform = WindowsPlatform()

        platform.suspend_native_active_title()
        with mock.patch(
            "codex_usage_hud.platforms.windows._UiaTitleWatcher",
        ) as uia_watcher, mock.patch(
            "codex_usage_hud.platforms.windows._WinEventTitleWatcher",
        ) as msaa_watcher:
            started = platform.watch_active_conversation_title(
                threading.Event(),
                lambda title: None,
            )

        self.assertFalse(started)
        uia_probe.assert_called_once()
        uia_watcher.assert_not_called()
        msaa_watcher.assert_not_called()

    class _FailingOleacc:
        def AccessibleObjectFromWindow(self, *args: object) -> int:
            del args
            raise OSError(-2147467259, "Unspecified error")

        def AccessibleObjectFromEvent(self, *args: object) -> int:
            del args
            raise OSError(-2147467259, "Unspecified error")

    def _msaa_probe_with_failing_oleacc(self) -> _MsaaTitleProbe:
        probe = object.__new__(_MsaaTitleProbe)
        probe._oleacc = self._FailingOleacc()
        probe._init_com_for_thread = lambda: True  # type: ignore[method-assign]
        return probe

    def test_low_level_mouse_hook_is_opt_in(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_env_flag(MOUSE_HOOK_ENV, default=False))
        with mock.patch.dict(os.environ, {MOUSE_HOOK_ENV: "1"}, clear=True):
            self.assertTrue(_env_flag(MOUSE_HOOK_ENV, default=False))

    def test_msaa_conversation_title_ignores_oleacc_hresult_errors(self) -> None:
        probe = self._msaa_probe_with_failing_oleacc()

        self.assertIsNone(probe.conversation_title(123))

    def test_msaa_event_title_ignores_oleacc_hresult_errors(self) -> None:
        probe = self._msaa_probe_with_failing_oleacc()

        self.assertIsNone(probe.title_from_event(123, -4, 0))

    def test_uia_title_scoring_ignores_sidebar_rows_for_full_window_poll(self) -> None:
        window_rect = (570, 167, 1806, 905)
        main_title = _UiaTitleNode(
            name="修复 HUD 会话标题错乱",
            control_type=_UIA_TEXT_CONTROL_TYPE_ID,
            selected=False,
            offscreen=False,
            rect=(826, 216, 976, 236),
        )
        pinned_sidebar_row = _UiaTitleNode(
            name="所有业务分支维护1w",
            control_type=_UIA_LIST_ITEM_CONTROL_TYPE_ID,
            selected=False,
            offscreen=False,
            rect=(578, 275, 787, 307),
        )

        main_score = _UiaTitleProbe._score_title_node(
            main_title,
            16,
            window_rect,
            main_title_only=True,
        )
        sidebar_poll_score = _UiaTitleProbe._score_title_node(
            pinned_sidebar_row,
            17,
            window_rect,
            main_title_only=True,
        )
        sidebar_click_score = _UiaTitleProbe._score_title_node(
            pinned_sidebar_row,
            17,
            window_rect,
        )

        self.assertGreater(main_score, 10_000)
        self.assertEqual(sidebar_poll_score, 0)
        self.assertGreater(sidebar_click_score, 0)

    def test_windows_platform_poll_refreshes_before_using_cached_event_title(self) -> None:
        class _FakeProbe:
            def __init__(self, title: str | None) -> None:
                self.title = title

            def conversation_title(self, hwnd: int) -> str | None:
                del hwnd
                return self.title

        platform = object.__new__(WindowsPlatform)
        platform._last_observed_title = "置顶旧标题"
        platform._last_observed_session_id = ""
        platform._cdp_probe = None
        platform._uia_title_probe = _FakeProbe("当前窗口标题")
        platform._title_probe = None
        platform._find_codex_window = lambda: 123  # type: ignore[method-assign]

        self.assertEqual(platform.get_active_conversation_title(), "当前窗口标题")
        self.assertEqual(platform._last_observed_title, "当前窗口标题")

    def test_windows_platform_falls_back_to_uia_when_cdp_snapshot_missing(self) -> None:
        class _FakeCdpProbe:
            def snapshot(self) -> object | None:
                return None

        class _FakeTitleProbe:
            def conversation_title(self, hwnd: int) -> str | None:
                del hwnd
                return "UIA 降级标题"

        platform = object.__new__(WindowsPlatform)
        platform._last_observed_title = ""
        platform._last_observed_session_id = ""
        platform._cdp_probe = _FakeCdpProbe()
        platform._uia_title_probe = _FakeTitleProbe()
        platform._title_probe = None
        platform._find_codex_window = lambda: 123  # type: ignore[method-assign]

        self.assertEqual(platform.get_active_conversation_title(), "UIA 降级标题")

    def test_windows_platform_does_not_return_cached_title_when_poll_is_empty(self) -> None:
        class _FakeTitleProbe:
            def conversation_title(self, hwnd: int) -> str | None:
                del hwnd
                return None

        platform = object.__new__(WindowsPlatform)
        platform._last_observed_title = "旧会话标题"
        platform._last_observed_session_id = ""
        platform._cdp_probe = None
        platform._uia_title_probe = _FakeTitleProbe()
        platform._title_probe = _FakeTitleProbe()
        platform._find_codex_window = lambda: 123  # type: ignore[method-assign]

        self.assertIsNone(platform.get_active_conversation_title())
        self.assertEqual(platform._last_observed_title, "旧会话标题")

    def test_windows_platform_exposes_visible_cdp_app_error(self) -> None:
        class _FakeCdpProbe:
            def snapshot(self) -> object | None:
                return SimpleNamespace(
                    app_error="exceeded retry limit, last status: 429 Too Many Requests"
                )

        platform = object.__new__(WindowsPlatform)
        platform._cdp_probe = _FakeCdpProbe()

        self.assertEqual(
            platform.get_active_app_error(),
            "exceeded retry limit, last status: 429 Too Many Requests",
        )


class MacOSActiveTitleTests(unittest.TestCase):
    def test_macos_platform_initializes_cdp_probe_by_default(self) -> None:
        with mock.patch(
            "codex_usage_hud.platforms.macos.CodexCdpProbe",
            return_value=object(),
        ) as cdp_probe:
            platform = MacOSPlatform()

        self.assertIsNotNone(platform._cdp_probe)
        self.assertTrue(platform.supports_active_title_polling())
        cdp_probe.assert_called_once()

    def test_macos_platform_reads_active_ref_from_cdp_snapshot(self) -> None:
        class _FakeCdpProbe:
            def snapshot(self) -> object | None:
                return SimpleNamespace(
                    session_id=" session-123 ",
                    title=" 当前会话 ",
                    app_error="",
                )

        platform = object.__new__(MacOSPlatform)
        platform._last_observed_title = ""
        platform._last_observed_session_id = ""
        platform._cdp_probe = _FakeCdpProbe()

        self.assertEqual(platform.get_active_conversation_ref(), ("session-123", "当前会话"))
        self.assertEqual(platform.get_active_conversation_title(), "当前会话")
        self.assertEqual(platform._last_observed_session_id, "session-123")
        self.assertEqual(platform._last_observed_title, "当前会话")

    def test_macos_platform_exposes_visible_cdp_app_error(self) -> None:
        class _FakeCdpProbe:
            def snapshot(self) -> object | None:
                return SimpleNamespace(
                    session_id="",
                    title="",
                    app_error="rate limit exceeded",
                )

        platform = object.__new__(MacOSPlatform)
        platform._cdp_probe = _FakeCdpProbe()

        self.assertEqual(platform.get_active_app_error(), "rate limit exceeded")


if __name__ == "__main__":
    unittest.main()
