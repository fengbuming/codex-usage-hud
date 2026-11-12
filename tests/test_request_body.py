import gzip
import io
import json
import tracemalloc

import pytest

from codex_usage_hud.request_body import BodyFiles, RequestBody, decode_body, receive_body
from codex_usage_hud.provider_proxy import _rewrite_model
from codex_usage_hud.provider_gateway import apply_request_service_tier


@pytest.fixture
def bodies():
    files = BodyFiles()
    yield files
    files.close()
    assert not files.files


def make_body(files, value):
    body = files.new()
    body.write(value)
    return body.rewind()


@pytest.mark.parametrize('value', [
    b'{"input":[{"text":"a\\\"b\\\\c"}],"model":"old"}',
    '{"input":[{"text":"中文😀"}],"model":"old"}'.encode(),
    b'{"model":"first","model":"old","input":[1,true,null,{"a":[]}]}',
    b'{ "\\u006dodel" : "old", "service_tier":"default" } \r\n',
])
def test_model_and_tier_edits_preserve_full_input(bodies, value):
    body = make_body(bodies, value)
    edited = _rewrite_model(body, 'new')
    edited = apply_request_service_tier(edited, 'fast')
    assert isinstance(edited, RequestBody)
    expected = json.loads(value)
    expected.update(model='new', service_tier='fast')
    assert edited.load() == expected
    assert body.load() == json.loads(value)


@pytest.mark.parametrize('value', [b'{"model":"old",}', b'{"input":[1,]}', b'{"input":"bad\n"}', b'{}more', b'{"input":01}', b'{"input":"bad\\q"}'])
def test_invalid_json_is_never_patched(bodies, value):
    body = make_body(bodies, value)
    with pytest.raises(ValueError):
        body.field('model')
    assert _rewrite_model(body, 'new') is body


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'utf-16-be', 'utf-32'])
def test_json_encoding_normalization_preserves_unicode(bodies, encoding):
    payload = {'model': 'old', 'input': '中文😀'}
    body = make_body(bodies, json.dumps(payload, ensure_ascii=False).encode(encoding))
    assert _rewrite_model(body, 'new').load() == {**payload, 'model': 'new'}


def test_large_model_edit_uses_bounded_python_allocations(bodies, monkeypatch):
    body = make_body(bodies, b'{"input":[{"text":"' + b'x' * (8 * 1024 * 1024) + b'"}],"model":"old"}')
    loads = json.loads
    def bounded_load(value, *args, **kwargs):
        assert len(value) < 4096, 'Control-field edit must not decode the conversation'
        return loads(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', bounded_load)
    tracemalloc.start()
    try:
        edited = _rewrite_model(body, 'new')
        assert edited.field('model') == 'new'
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024
    assert edited.size == body.size


def test_streamed_input_transform_keeps_large_unsealed_items_opaque(bodies):
    payload = {"input": [{"type": "message", "content": "完整内容" * 10000}, {"type": "reasoning", "encrypted_content": "foreign"}, 42]}
    body = make_body(bodies, json.dumps(payload).encode())
    def transform(item):
        if item and item.get('encrypted_content'):
            return False
        return None
    assert body.map_input(transform).load() == {'input': [payload['input'][0], 42]}


def test_chunked_receive_decompression_and_truncation_cleanup(bodies):
    raw = b'{"model":"old","input":[1,2,3]}'
    compressed = gzip.compress(raw)
    stream = io.BytesIO(f'{len(compressed):x}\r\n'.encode() + compressed + b'\r\n0\r\n\r\n')
    body = receive_body(stream, {'Transfer-Encoding': 'chunked'}, bodies)
    assert decode_body(body, 'gzip').load() == json.loads(raw)
    with pytest.raises(ValueError, match='Truncated'):
        receive_body(io.BytesIO(b'a'), {'Content-Length': '20'}, bodies)
    with pytest.raises(ValueError):
        receive_body(io.BytesIO(b'-1\r\n'), {'Transfer-Encoding': 'chunked'}, bodies)


def test_bodyless_websocket_handshake_allocates_no_temporary_file(bodies):
    assert receive_body(io.BytesIO(), {'Upgrade': 'websocket'}, bodies) is None
    assert bodies.files == []


def test_background_projection_uses_same_last_three_inputs(bodies):
    payload = {'instructions': 'system', 'input': [1, 2, 3, 4, 5]}
    body = make_body(bodies, json.dumps(payload).encode())
    assert body.background_payload() == {'instructions': 'system', 'input': [3, 4, 5]}


def test_image_prompt_does_not_decode_old_history_or_unsupported_attachment(bodies, monkeypatch):
    loads = json.loads
    payload = {'model': 'gpt-image-2', 'stream': True, 'input': [
        {'role': 'assistant', 'content': 'old' * 10000}, {'role': 'user', 'content': 'a whale'}]}
    body = make_body(bodies, json.dumps(payload).encode())
    def bounded(value, *args, **kwargs):
        assert len(value) < 4096
        return loads(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', bounded)
    assert body.image_payload() == {**{k: v for k, v in payload.items() if k != 'input'}, 'input': [payload['input'][-1]]}
    attached = make_body(bodies, b'{"model":"gpt-image-2","input":[{"role":"user","content":[{"type":"input_image","image_url":"' + b'x' * 10000 + b'"}]}]}')
    from codex_usage_hud.image_generation import image_request
    with pytest.raises(ValueError, match='参考图'):
        image_request(attached.image_payload())


def test_spooled_image_decodes_in_bounded_chunks_and_keeps_exact_bytes(bodies, tmp_path):
    import base64
    from codex_usage_hud.image_generation import save_image

    raw = b'\x89PNG\r\n\x1a\n' + b'x' * (4 * 1024 * 1024)
    body = make_body(bodies, json.dumps({'data': [{'b64_json': base64.b64encode(raw).decode()}]}).encode())
    tracemalloc.start()
    try:
        path = save_image(body, tmp_path / 'config.toml', '10000000-0000-4000-8000-000000000001')
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert path.read_bytes() == raw
    assert peak < 2 * 1024 * 1024


def test_image_base64_json_escapes_and_invalid_data(bodies, tmp_path):
    import base64
    from codex_usage_hud.image_generation import save_image

    raw = b'\xff\xd8\xff' + b'123'
    encoded = json.dumps({'data': [{'b64_json': base64.b64encode(raw).decode()}]}).encode().replace(b'/', b'\\/')
    assert save_image(make_body(bodies, encoded), tmp_path / 'config.toml', '10000000-0000-4000-8000-000000000001').read_bytes() == raw
    invalid = make_body(bodies, b'{"data":[{"b64_json":"AA==AAAA"}]}')
    with pytest.raises(ValueError, match='Base64'):
        save_image(invalid, tmp_path / 'config.toml', '10000000-0000-4000-8000-000000000001')
