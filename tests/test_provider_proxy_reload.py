from threading import Event, RLock, Thread
from types import SimpleNamespace
import sys

import pytest

from codex_usage_hud.provider_proxy_reload import ProxyReloadManager


@pytest.fixture
def source_manager(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for name in ("__init__.py", "provider_gateway.py", "codex_provider_config.py"):
        (root / name).write_text("", encoding="utf-8")
    (root / "late.py").write_text('VALUE = "one"\n', encoding="utf-8")
    (root / "provider_proxy.py").write_text('''
class ProviderProxyRelay:
    def __init__(self, port):
        self.port = port
    def _handler_type(self):
        class Handler:
            def _forward(self):
                self.started.set()
                self.release.wait(5)
                from .late import VALUE
                self.output.append(VALUE)
        return Handler
''', encoding="utf-8")
    owner = SimpleNamespace(port=1234, server=object(), routes={}, config_paths=set(), lock=RLock(), source_evidence_listeners={})
    manager = ProxyReloadManager(owner, source_root=root)
    try:
        yield root, manager
    finally:
        manager.close()


def request(*, released=False):
    value = SimpleNamespace(started=Event(), release=Event(), output=[])
    if released:
        value.release.set()
    return value


def test_reload_pins_inflight_requests_and_lazy_imports_then_retires_old_code(source_manager):
    root, manager = source_manager
    first = manager.reload()
    old = request()
    worker = Thread(target=manager.dispatch, args=(old,))
    worker.start()
    try:
        assert old.started.wait(1)
        (root / "late.py").write_text('VALUE = "two"\n', encoding="utf-8")
        second = manager.reload()
        assert second["generation"] != first["generation"]
        assert second["activeRequests"] == 1 and second["retiredGenerations"] == 1
        current = request(released=True)
        manager.dispatch(current)
        assert current.output == ["two"]
        assert old.output == []
        with pytest.raises(RuntimeError, match="Active"):
            manager.close()
    finally:
        old.release.set()
        worker.join(2)
    assert old.output == ["one"], "an old request must not import changed dependencies"
    assert manager.health()["retiredGenerations"] == 0
    assert first["generation"] not in sys.modules
    assert manager.reload()["changed"] is False


def test_failed_source_load_keeps_working_generation_and_removes_failed_imports(source_manager):
    root, manager = source_manager
    before = manager.reload()
    modules = {name for name in sys.modules if name.startswith("_codex_hud_proxy_")}
    (root / "provider_gateway.py").write_text("not valid python @@@", encoding="utf-8")
    with pytest.raises(SyntaxError):
        manager.reload()
    assert manager.health()["generation"] == before["generation"]
    assert {name for name in sys.modules if name.startswith("_codex_hud_proxy_")} == modules
    current = request(released=True)
    manager.dispatch(current)
    assert current.output == ["one"]
