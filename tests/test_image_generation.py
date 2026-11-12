"""Focused image routing/protocol tests; no network or paid API calls."""

import base64
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from codex_usage_hud import image_generation as images
from codex_usage_hud import provider_gateway as gateway
from codex_usage_hud import codex_provider_config as config
from codex_usage_hud.provider_proxy import ProviderProxyRelay
from codex_usage_hud.provider_gateway_auth import GATEWAY_AUTH_HEADER, GATEWAY_PATH_ID


A = "10000000-0000-4000-8000-000000000001"
B = "10000000-0000-4000-8000-000000000002"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZQAAAABJRU5ErkJggg==")


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('''model_provider = "alpha"
[model_providers.alpha]
base_url = "https://alpha.example/v1"
experimental_bearer_token = "alpha-key"
# codex-hud-desktop-model: ["gpt-image-2","gpt-image-2.5","gpt-6.1-sol"]
# codex-hud-proxy: {"base_url":"https://alpha.example/v1","models":["gpt-image-2","gpt-image-2.5","gpt-6.1-sol"]}
[model_providers.beta]
base_url = "https://beta.example/v1"
experimental_bearer_token = "beta-key"
''', encoding="utf-8")
    monkeypatch.setattr(config, "default_codex_config_path", lambda: path)
    monkeypatch.setattr(config, "_user_environment_value", lambda _name: "")
    relay = SimpleNamespace(route=lambda *_: "http://127.0.0.1:57324/codex_hud/v1")
    gateway.activate_gateway(path, relay)
    return path


def test_legacy_image_mode_does_not_override_per_request_routing(home):
    before = home.read_bytes()
    gateway.set_image_generation(A, True, "gpt-image-2", config_path=home)
    assert gateway.resolve_route(home, {"session_id": A}, generation=True)["image_generation"] == {}
    assert gateway.resolve_route(home, {"session_id": B}, generation=True)["image_generation"] == {}
    gateway.select_provider("beta", config_path=home)
    assert gateway.resolve_route(home, {"session_id": A}, generation=True)["provider"] == "alpha"
    assert gateway.image_sessions(home)[A]["provider"] == "alpha"
    gateway.set_image_generation(A, False, config_path=home)
    assert gateway.resolve_route(home, {"session_id": A}, generation=True)["image_generation"] == {}
    assert home.read_bytes() == before, "mode changes must not rewrite global Codex config"


@pytest.mark.parametrize("thread_id,enabled,model", [("", True, "gpt-image-2"), (A, "true", "gpt-image-2"),
    (A, True, "gpt-6.1-sol"), (A, True, "gpt-image-not-configured")])
def test_mode_rejects_missing_identity_invalid_switch_and_unconfigured_models(home, thread_id, enabled, model):
    with pytest.raises(ValueError):
        gateway.set_image_generation(thread_id, enabled, model, config_path=home)
    assert gateway.image_sessions(home) == {}


def test_old_gateway_store_can_be_read_without_migrating(tmp_path):
    import sqlite3

    path = tmp_path / "config.toml"
    with sqlite3.connect(tmp_path / "hud-provider-sessions.sqlite") as db:
        db.execute("CREATE TABLE settings(key TEXT, value TEXT)")
    assert gateway.image_sessions(path) == {}


def test_only_latest_user_text_becomes_prompt_and_native_image_selection_wins():
    payload = {"model": "gpt-image-2.5", "instructions": "private developer context", "tools": [{"type": "function"}],
               "input": [{"role": "user", "content": "old request"}, {"role": "assistant", "content": "old reply"},
                         {"role": "user", "content": [{"type": "input_text", "text": "a moonlit whale"}]}]}
    request = images.image_request(payload)
    assert request == {"model": "gpt-image-2.5", "prompt": "a moonlit whale", "n": 1,
                       "size": "1024x1024"}
    with pytest.raises(ValueError, match="未选择"):
        images.image_request({"model": "gpt-6.1-sol", "input": "clouds"})


@pytest.mark.parametrize("payload", [{"input": []}, {"input": " "}, {"input": [{"role": "user", "content": [
    {"type": "input_image", "image_url": "data:image/png;base64,private"}]}]}])
def test_missing_text_and_unsupported_attachments_never_generate(payload):
    with pytest.raises(ValueError):
        images.image_request({"model": "gpt-image-2", **payload})


@pytest.mark.parametrize("model", ["gpt-image-2", "gpt-image-2.5", "gemini-3-pro-image-preview", "gemini-nano-banana"])
def test_request_model_classifies_known_image_families(model):
    route = {"provider": "alpha", "models": [model]}
    assert images.image_request_mode(route, json.dumps({"model": model}).encode()) == {"provider": "alpha", "model": model}


@pytest.mark.parametrize("payload", [None, b"invalid", b"[]", b"{}", b'{"model":null}',
    b'{"model":"gpt-6.1-sol","tools":[{"type":"image_generation"}]}',
    b'{"model":"gemini-3-pro","input":[{"type":"input_image"}]}',
    b'{"model":"custom-image-vision-chat"}'])
def test_text_vision_and_unknown_models_never_inherit_image_mode(payload):
    route = {"provider": "alpha", "models": ["gpt-image-2"], "image_generation": {"model": "gpt-image-2"}}
    assert images.image_request_mode(route, payload) == {}


def test_recognized_but_unconfigured_image_model_is_rejected_before_fallback():
    with pytest.raises(ValueError, match="模型列表"):
        images.image_request_mode({"provider": "beta", "models": ["gpt-6.1-sol"]}, b'{"model":"gpt-image-2"}')


class Handler:
    def __init__(self):
        self.wfile = BytesIO()
        self.headers = {}
        self.status = None

    def send_response_only(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass


def route():
    return {"thread_id": A, "models": ["gpt-image-2"], "image_generation": {"model": "gpt-image-2"}}


def test_stream_emits_native_terminal_message_and_durable_local_image(tmp_path):
    handler = Handler()

    def generate(_route, request):
        assert b"response.created" in handler.wfile.getvalue(), "send progress before waiting on the API"
        assert "response_format" not in request
        return {"data": [{"b64_json": base64.b64encode(PNG).decode()}]}

    with patch.object(images, "_generate", side_effect=generate) as call:
        images.serve_image_generation(handler, tmp_path / "config.toml", route(), b'{"model":"gpt-image-2","input":"whale","stream":true}')
    assert call.call_count == 1
    frames = [json.loads(line[6:]) for line in handler.wfile.getvalue().decode().splitlines() if line.startswith("data: ")]
    assert frames[-1]["type"] == "response.completed"
    assert [frame["sequence_number"] for frame in frames] == list(range(len(frames)))
    message = frames[-1]["response"]["output"][0]
    assert message["role"] == "assistant" and message["phase"] == "final_answer"
    saved = list((tmp_path / "generated_images" / "hud" / A).glob("*.png"))
    assert len(saved) == 1 and saved[0].read_bytes() == PNG
    assert saved[0].as_posix() in message["content"][0]["text"]


@pytest.mark.parametrize("streaming", [True, False])
def test_failures_are_terminal_and_never_retried(tmp_path, streaming):
    handler = Handler()
    with patch.object(images, "_generate", side_effect=ValueError("upstream unavailable")) as call:
        images.serve_image_generation(handler, tmp_path / "config.toml", route(), json.dumps({"model": "gpt-image-2", "input": "whale", "stream": streaming}).encode())
    assert call.call_count == 1
    text = handler.wfile.getvalue().decode()
    if streaming:
        assert "response.failed" in text and "response.completed" not in text
    else:
        assert handler.status == 502 and json.loads(text)["status"] == "failed"
    assert not (tmp_path / "generated_images").exists()


def test_generator_respects_provider_auth_base_path_query_and_explicit_proxy():
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = BytesIO(b'{"data":[]}')
    value = {"base_url": "https://alpha.example/api/v2?api-version=old", "enabled": True, "port": 7897,
             "headers": {"Authorization": "Bearer selected-key"}, "query_params": {"api-version": "new"}}
    with patch.object(images, "build_opener", return_value=opener), patch.object(images, "ProxyHandler") as proxy:
        result = images._generate(value, {"prompt": "whale"})
        try:
            assert result.load() == {"data": []}
        finally:
            result.owner.close()
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://alpha.example/api/v2/images/generations?api-version=new"
    assert request.get_header("Authorization") == "Bearer selected-key"
    proxy.assert_called_once_with({"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"})
    opener.open.side_effect = HTTPError(request.full_url, 429, "private credentials in vendor body", {}, None)
    with patch.object(images, "build_opener", return_value=opener), pytest.raises(ValueError, match="HTTP 429") as error:
        images._generate(value, {})
    assert "private credentials" not in str(error.value)


@pytest.mark.parametrize("model", ["gpt-image-1", "gpt-image-2", "GPT-IMAGE-2"])
def test_gpt_images_use_native_base64_contract_without_legacy_parameter(model):
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = BytesIO(b'{"data":[]}')

    def strict_api(request, **_kwargs):
        payload = json.loads(request.data)
        assert payload["model"] == model
        assert "response_format" not in payload, "GPT Images rejects this legacy parameter"
        return opener.open.return_value

    opener.open.side_effect = strict_api
    with patch.object(images, "build_opener", return_value=opener):
        result = images._generate({"base_url": "https://api.openai.com/v1"},
                                  images.image_request({"model": model, "input": "whale"}))
        try:
            assert result.load() == {"data": []}
        finally:
            result.owner.close()
    assert opener.open.call_count == 1


@pytest.mark.parametrize("model", ["gemini-3-pro-image-preview", "gemini-nano-banana"])
def test_compatible_image_models_keep_explicit_base64_response_format(model):
    assert images.image_request({"model": model, "input": "whale"})["response_format"] == "b64_json"


@pytest.mark.parametrize("encoded", [None, "bad base64!", base64.b64encode(b"<script>not an image</script>").decode()])
def test_invalid_vendor_images_are_not_written(tmp_path, encoded):
    with pytest.raises(ValueError):
        images.save_image({"data": [{"b64_json": encoded}]}, tmp_path / "config.toml", A)
    assert not (tmp_path / "generated_images").exists()


@pytest.mark.parametrize("thread_id", [A, B])
def test_relay_automatically_routes_image_models_in_existing_and_new_chats(home, thread_id):
    if thread_id == A:
        gateway.resolve_route(home, {"session_id": thread_id}, generation=True)
    relay = ProviderProxyRelay()
    relay.routes[GATEWAY_PATH_ID] = {"gateway_config": str(home), "token": gateway.gateway_info(home)["token"]}
    handler = object.__new__(relay._handler_type())
    handler.command = "POST"
    handler.path = "/codex_hud/v1/responses"
    handler.rfile = BytesIO(b'{"input":"whale","model":"gpt-image-2"}')
    handler.headers = {"session_id": thread_id, GATEWAY_AUTH_HEADER: gateway.gateway_info(home)["token"], "Content-Length": str(len(handler.rfile.getvalue()))}
    with patch.object(images, "serve_image_generation") as serve, patch("http.client.HTTPSConnection") as connection:
        handler._forward()
    assert serve.call_count == 1
    assert serve.call_args.args[2]["thread_id"] == thread_id
    connection.return_value.request.assert_not_called()

    assert gateway.image_sessions(home) == {}, "automatic routing must not persist an image toggle"

def test_mode_command_delivers_ack_and_refreshes_settings_without_full_reparse():
    from codex_usage_hud.runtime_commands import handle_general_command
    from codex_usage_hud.runtime_settings import partial_domains_for_command
    from codex_usage_hud.config import UserConfig

    command = {"action": "imageGenerationSet", "sessionId": A, "enabled": True, "model": "gpt-image-2"}
    ports = SimpleNamespace(gateway_command=MagicMock(return_value={"sessionId": A, "enabled": True}))
    status = handle_general_command(command, ports)
    ports.gateway_command.assert_called_once_with(command)
    assert status["providerGateway"] == {"sessionId": A, "enabled": True}
    assert partial_domains_for_command(command, previous_config=UserConfig.defaults(), current_config=UserConfig.defaults()) == {"settings"}
    ports.gateway_command.side_effect = ValueError("provider unavailable")
    assert handle_general_command(command, ports)["kind"] == "error"


def test_payload_projects_saved_modes_without_credentials(home):
    from codex_usage_hud.renderer_payload_builder import payload_from_snapshot
    from codex_usage_hud.core.parser import ParsedSession

    gateway.set_image_generation(A, True, "gpt-image-2", config_path=home)
    payload = payload_from_snapshot(ParsedSession(status="waiting")).to_json()
    assert payload["settings"]["provider_gateway"]["image_sessions"] == {A: {"provider": "alpha", "model": "gpt-image-2"}}
    assert "alpha-key" not in json.dumps(payload)


def test_new_draft_binds_only_its_own_first_native_request(home):
    before = home.read_bytes()
    armed = gateway.set_image_draft(True, "gpt-image-2", config_path=home)
    assert armed["draft"] is True and armed["enabled"] is True
    assert gateway.image_sessions(home) == {}
    assert gateway.resolve_route(home, {"session_id": B}, generation=True)["image_generation"] == {}
    route = gateway.resolve_route(home, {"session_id": A}, generation=True)
    body = json.dumps({"model": armed["nativeModel"], "input": "a whale"}).encode()
    decoded = json.loads(gateway.bind_image_draft(home, route, body))
    assert decoded["model"] == "gpt-image-2"
    assert route["image_generation"]["model"] == "gpt-image-2"
    assert gateway.image_sessions(home)[A]["provider"] == "alpha"
    assert gateway.bind_image_draft(home, route, body), "same-chat native retries may resolve the binding"
    other = gateway.resolve_route(home, {"session_id": B}, generation=True)
    with pytest.raises(ValueError, match="不属于当前会话"):
        gateway.bind_image_draft(home, other, body)
    token = gateway.image_draft_model(armed["nativeModel"])[0]
    gateway.set_image_draft(False, token=token, config_path=home)
    assert A in gateway.image_sessions(home), "an empty composer must not disable an already-bound chat"
    assert home.read_bytes() == before


def test_cancelled_or_wrong_provider_draft_never_routes_an_image(home):
    armed = gateway.set_image_draft(True, "gpt-image-2", config_path=home)
    token = gateway.image_draft_model(armed["nativeModel"])[0]
    route = gateway.resolve_route(home, {"session_id": A}, generation=True)
    body = json.dumps({"model": armed["nativeModel"], "input": "whale"}).encode()
    with pytest.raises(ValueError, match="不属于当前会话"):
        gateway.bind_image_draft(home, {**route, "provider": "beta"}, body)
    gateway.set_image_draft(False, token=token, config_path=home)
    with pytest.raises(ValueError, match="草稿已失效"):
        gateway.bind_image_draft(home, route, body)
    assert gateway.image_sessions(home) == {}


def test_live_context_resolves_actual_provider_without_global_default_or_credentials(home):
    gateway.resolve_route(home, {"session_id": A}, generation=True)
    gateway.select_provider("beta", config_path=home)
    result = gateway.image_generation_context(A, config_path=home)
    assert result == {"sessionId": A, "provider": "alpha"}
    assert "key" not in json.dumps(result)


def test_relay_binds_new_draft_before_model_rewrite_and_calls_images_adapter(home):
    armed = gateway.set_image_draft(True, "gpt-image-2", config_path=home)
    relay = ProviderProxyRelay()
    relay.routes[GATEWAY_PATH_ID] = {"gateway_config": str(home), "token": gateway.gateway_info(home)["token"]}
    handler = object.__new__(relay._handler_type())
    handler.command = "POST"
    handler.path = "/codex_hud/v1/responses"
    handler.rfile = BytesIO(json.dumps({"input": "whale", "model": armed["nativeModel"]}).encode())
    handler.headers = {"session_id": A, GATEWAY_AUTH_HEADER: gateway.gateway_info(home)["token"], "Content-Length": str(len(handler.rfile.getvalue()))}
    models = []
    with patch.object(images, "serve_image_generation", side_effect=lambda *args: models.append(args[3].field('model'))) as serve, patch("http.client.HTTPSConnection") as connection:
        handler._forward()
    assert serve.call_args.args[2]["thread_id"] == A
    assert models == ["gpt-image-2"]
    assert serve.call_args.args[3].file.closed
    connection.return_value.request.assert_not_called()


def _relay_handler(home, payload, thread_id=A, path="/responses"):
    relay = ProviderProxyRelay()
    token = gateway.gateway_info(home)["token"]
    relay.routes[GATEWAY_PATH_ID] = {"gateway_config": str(home), "token": token}
    handler = object.__new__(relay._handler_type())
    handler.command = "POST"
    handler.path = "/codex_hud/v1" + path
    handler.rfile = BytesIO(json.dumps(payload).encode())
    handler.wfile = BytesIO()
    handler.headers = {"session_id": thread_id, GATEWAY_AUTH_HEADER: token,
                       "Content-Length": str(len(handler.rfile.getvalue()))}
    handler.send_response_only = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler._send_relay_failure = MagicMock()
    return handler


def test_switching_models_routes_each_request_and_ignores_old_chat_flag(home):
    gateway.set_image_generation(A, True, "gpt-image-2", config_path=home)
    served, forwarded = [], []
    with patch.object(images, "serve_image_generation", side_effect=lambda *args: served.append(args[3].field('model'))) as serve, patch("http.client.HTTPSConnection") as connection:
        connection.return_value.request.side_effect = lambda *args, **kwargs: forwarded.append(kwargs['body'].field('model'))
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.headers = {}
        response.getheaders.return_value = []
        response.read1.return_value = b""
        for model in ["gpt-image-2", "gpt-6.1-sol", "gpt-image-2.5"]:
            handler = _relay_handler(home, {"model": model, "input": "whale"})
            handler._forward()
            handler._send_relay_failure.assert_not_called()
        assert served == ["gpt-image-2", "gpt-image-2.5"]
        assert serve.call_count == 2
        assert connection.return_value.request.call_count == 1
        assert forwarded == ["gpt-6.1-sol"]


@pytest.mark.parametrize("model,path", [("gpt-image-not-configured", "/responses"), ("gpt-image-2", "/responses/compact")])
def test_invalid_image_request_does_not_fall_back_to_another_model_or_generate(home, model, path):
    handler = _relay_handler(home, {"model": model, "input": "whale"}, path=path)
    with patch.object(images, "serve_image_generation") as serve, patch("http.client.HTTPSConnection") as connection:
        handler._forward()
    handler._send_relay_failure.assert_called_once()
    serve.assert_not_called()
    connection.return_value.request.assert_not_called()
