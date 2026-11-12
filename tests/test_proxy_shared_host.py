from types import SimpleNamespace

import pytest

from codex_usage_hud import provider_proxy_service as service


def test_group_hosts_two_authenticated_listeners_and_reuses_each(tmp_path, monkeypatch):
    started = []
    def start(path, port, **kwargs):
        relay = SimpleNamespace(path=path, port=port)
        started.append((relay, kwargs['group']))
        return relay
    monkeypatch.setattr(service, '_start_helper', start)
    group = service._HelperGroup(tmp_path)
    first = group.listen(tmp_path / 'one.json', 57323)
    second = group.listen(tmp_path / 'two.json', 57324)
    assert first is not second
    assert group.listen(tmp_path / 'one.json', 57323) is first
    assert [owner for _relay, owner in started] == [group, group]
    with pytest.raises(ValueError):
        group.listen(tmp_path / 'other.json', 57323)
    with pytest.raises(ValueError):
        group.listen(tmp_path.parent / 'outside.json', 57325)


def test_new_endpoint_reuses_peer_process_without_launching_or_stopping_it(tmp_path, monkeypatch):
    state = {'version': 4, 'control_token': 'test-only'}
    monkeypatch.setattr(service, '_read_state', lambda *args: dict(state))
    monkeypatch.setattr(service, 'launch_helper', lambda *args, **kwargs: pytest.fail('must reuse peer'))
    class Probe:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, *args): pass
        def connect_ex(self, *args): return 1
    monkeypatch.setattr(service.socket, 'socket', Probe)
    attached = []
    def call(client, action, payload=None):
        if client.port == 57324 and not attached:
            raise OSError('listener not started')
        if action == 'listen':
            attached.append(payload)
        return {'version': 4, 'pid': 1234}
    monkeypatch.setattr(service.ProviderProxyServiceClient, '_call', call)
    client = service.ProviderProxyServiceClient(57324, state_path=tmp_path / 'two.json', peer=(57323, tmp_path / 'one.json'))
    client.ensure()
    assert attached == [{'port': 57324, 'state_path': str((tmp_path / 'two.json').resolve())}]
    assert client.process is None


def test_live_version_three_listener_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(service, '_read_state', lambda *args: {'version': 3, 'control_token': 'test-only'})
    calls = []
    def call(client, action, payload=None):
        calls.append(action)
        return {'version': 3}
    monkeypatch.setattr(service.ProviderProxyServiceClient, '_call', call)
    monkeypatch.setattr(service, 'launch_helper', lambda *args, **kwargs: pytest.fail('do not replace live listener'))
    client = service.ProviderProxyServiceClient(57323, state_path=tmp_path / 'one.json')
    client.ensure()
    assert calls == ['health']


def test_frozen_proxy_dispatch_does_not_import_renderer_or_desktop_helper():
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    code = '''
import json,sys
from unittest.mock import patch
from codex_usage_hud import provider_proxy_service
from codex_usage_hud.helper_entrypoint import main
with patch.object(provider_proxy_service,'main',return_value=12):
    assert main(['--provider-proxy-helper'])==12
assert 'codex_usage_hud.runtime_orchestration' not in sys.modules
assert 'codex_usage_hud.desktop_overlay_setup' not in sys.modules
print(json.dumps({'ok':True}))
'''
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=root,
                            env=dict(os.environ, PYTHONPATH=str(root / 'src')),
                            capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(result.stdout) == {'ok': True}


def test_listen_control_requires_token_and_dispatches_to_same_host(tmp_path, monkeypatch):
    import io
    import json
    from unittest.mock import MagicMock
    from codex_usage_hud.provider_proxy import ProviderProxyRelay

    monkeypatch.setattr(ProviderProxyRelay, '_start', lambda _self: None)
    group = SimpleNamespace(listen=MagicMock())
    state_path = tmp_path / 'primary.json'
    relay = service._start_helper(state_path, 57323, group=group)
    state = service._read_state(state_path, 57323)
    payload = json.dumps({'state_path': str(tmp_path / 'secondary.json'), 'port': 57324}).encode()
    def request(token):
        handler = object.__new__(relay._handler_type())
        handler.path = service.CONTROL_PATH + 'listen'
        handler.headers = {service.CONTROL_HEADER: token, 'Content-Length': str(len(payload))}
        handler.rfile = io.BytesIO(payload)
        handler.wfile = io.BytesIO()
        handler.send_error = MagicMock()
        handler.send_response_only = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._forward()
        return handler
    try:
        rejected = request('wrong')
        rejected.send_error.assert_called_once_with(403)
        group.listen.assert_not_called()
        accepted = request(state['control_token'])
        group.listen.assert_called_once_with(tmp_path / 'secondary.json', 57324)
        accepted.send_response_only.assert_called_once_with(200)
        assert json.loads(accepted.wfile.getvalue())['version'] == service.PROTOCOL_VERSION
    finally:
        relay.reload_manager.close()
