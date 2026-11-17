import json
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from codex_usage_hud.config import UserConfigStore
from codex_usage_hud.settings_bridge import SettingsBridgeServer


def test_history_endpoint_requires_private_token_and_keeps_full_unicode(tmp_path):
    full = '完整复制😀\n' * 10000
    calls = []
    def read(ref):
        calls.append(ref)
        return {'ref': ref, 'kind': 'text', 'value': full} if ref == 'valid' else None
    bridge = SettingsBridgeServer(UserConfigStore(tmp_path / 'settings.json'), port=0,
                                  renderer_history_callback=read)
    try:
        bridge.start()
        for url in (bridge.url + '/renderer-history?ref=valid', bridge.url + '/renderer-history?access_token=wrong&ref=valid'):
            with pytest.raises(HTTPError) as error:
                urlopen(url, timeout=3)
            assert error.value.code == 403
        assert not calls
        with urlopen(bridge.renderer_history_url + '&ref=valid', timeout=3) as response:
            assert response.getheader('Cache-Control') == 'no-store'
            assert json.load(response) == {'ref': 'valid', 'kind': 'text', 'value': full}
        with pytest.raises(HTTPError) as error:
            urlopen(bridge.renderer_history_url + '&ref=expired', timeout=3)
        assert error.value.code == 404
    finally:
        bridge.close()


def test_history_read_failure_does_not_expose_request_or_local_path(tmp_path):
    def fail(_ref):
        raise OSError('secret path and prompt body')
    bridge = SettingsBridgeServer(UserConfigStore(tmp_path / 'settings.json'), port=0, renderer_history_callback=fail)
    try:
        bridge.start()
        with pytest.raises(HTTPError) as error:
            urlopen(bridge.renderer_history_url + '&ref=valid', timeout=3)
        assert error.value.code == 503
        assert json.load(error.value) == {'error': 'history unavailable'}
    finally:
        bridge.close()
