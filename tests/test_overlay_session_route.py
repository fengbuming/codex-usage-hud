import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

from codex_usage_hud import overlay_commands
from codex_usage_hud.platforms.desktop_thread_navigator import _navigation_script
from codex_usage_hud.platforms.session_switch import (
    CdpSessionSwitchBackend, SessionSwitchController, SessionSwitchRequest,
    SessionSwitchResult,
)

SESSION_ID = "10000000-0000-4000-8000-000000000abc"


def test_canonical_bubble_routes_without_sidebar_or_title_search() -> None:
    backend = CdpSessionSwitchBackend(port=50697, timeout_seconds=1)
    backend._navigator = SimpleNamespace(navigate=MagicMock(return_value=SimpleNamespace(
        verified=True, thread_id=SESSION_ID, error="", detail="",
    )))
    backend._controller = SimpleNamespace(activate_thread=MagicMock())
    result = backend.activate(SessionSwitchRequest(session_id=SESSION_ID, title="Duplicate title"))
    assert result.ok
    assert result.active_session_id == SESSION_ID
    assert result.matched_by == "desktop-route"
    backend._controller.activate_thread.assert_not_called()


def test_canonical_id_never_falls_back_to_a_different_thread_with_same_title() -> None:
    native = SimpleNamespace(name="windows-search", activate=MagicMock(return_value=
        SessionSwitchResult(ok=True, status="switched", active_session_id="wrong-thread")))
    cdp = SimpleNamespace(name="cdp", activate=MagicMock(return_value=
        SessionSwitchResult(ok=False, status="thread-not-owned")))
    result = SessionSwitchController([native, cdp]).activate_session(
        session_id=SESSION_ID, title="Duplicate title")
    assert not result.ok
    assert result.status == "thread-not-owned"
    native.activate.assert_not_called()


def test_standalone_cold_start_preserves_debugger_flag_and_port(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_USAGE_HUD_CDP_PORT", "50697")
    launch = MagicMock(return_value=True)
    monkeypatch.setattr(overlay_commands, "launch_codex_app", launch)

    def prepare(**ports):
        ports["launch"](debugger=True, cdp_port=ports["cdp_port"])
        return True, "visible", "", 1

    monkeypatch.setattr(overlay_commands, "prepare_codex_window_for_renderer", prepare)
    assert overlay_commands._prepare_codex_window_for_standalone(
        timeout_seconds=15, launch_if_missing=True)[0]
    launch.assert_called_once_with(debugger=True, cdp_port=50697)


def test_navigation_verifies_async_active_id_and_releases_observer(tmp_path) -> None:
    script = _navigation_script(SESSION_ID, ["/local/"],
                                preflight_timeout_ms=500, verify_timeout_ms=500)
    harness = r'''
const assert = require('node:assert/strict');
const listeners = new Set(), timers = new Set();
let active = false, observer, disconnected = 0, opened = 0;
const row = {getAttribute: key => key === 'data-app-action-sidebar-thread-active' ? String(active) : ''};
const document = {documentElement: {}, querySelectorAll: () => [row]};
class MutationObserver {
  constructor(callback) {this.callback = callback; observer = this;}
  observe() {}
  disconnect() {disconnected++;}
}
const window = {
  addEventListener: (_, fn) => listeners.add(fn),
  removeEventListener: (_, fn) => listeners.delete(fn),
  setTimeout(fn, ms) {let timer = setTimeout(() => {timers.delete(timer); fn();}, ms); timers.add(timer); return timer;},
  clearTimeout(timer) {timers.delete(timer); clearTimeout(timer);},
  electronBridge: {async sendMessageFromView(message) {
    if (message.type === 'mcp-request') {
      queueMicrotask(() => listeners.forEach(fn => fn({data: {
        type: 'mcp-response', hostId: 'local',
        message: {id: message.request.id, result: {thread: {id: message.request.params.threadId}}},
      }})));
    } else {
      opened++;
      setTimeout(() => {observer.callback(); assert.equal(disconnected, 0);}, 1);
      setTimeout(() => {active = true; observer.callback();}, 5);
    }
  }},
};
'''
    path = tmp_path / "route-observer.js"
    path.write_text(harness + "\nconst navigation = " + script + r''';
navigation.then(report => {
  assert.equal(report.verified, true);
  assert.equal(opened, 1);
  assert.equal(disconnected, 1);
  assert.equal(listeners.size, 0);
  assert.equal(timers.size, 0);
}).catch(error => {console.error(error); process.exitCode = 1;});
''', encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
