"""Unit tests for the renderer settings bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.config import (
    ModelPrice,
    ProviderSettings,
    UserConfig,
    UserConfigStore,
)
from codex_usage_hud.settings_bridge import SettingsBridgeServer


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class SettingsBridgeServerTests(unittest.TestCase):
    def test_health_identifies_current_hud_process_for_source_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = SettingsBridgeServer(UserConfigStore(Path(temp_dir) / "hud_settings.json"), port=0)
            try:
                bridge.start()
                with urlopen(bridge.url.rsplit("/settings", 1)[0] + "/health", timeout=2) as response:
                    self.assertEqual(json.load(response), {"status": "ok", "pid": os.getpid()})
            finally:
                bridge.close()

    def test_background_usage_endpoints_require_token_and_lazy_load_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            event_id = "10000000-0000-4000-8000-000000000003"
            query_callback = MagicMock(
                return_value={
                    "summary": {"eventCount": 1},
                    "events": [{"eventId": event_id, "featureLabel": "Memory consolidation"}],
                }
            )
            detail_callback = MagicMock(
                return_value={
                    "eventId": event_id,
                    "prompt": "sensitive local prompt",
                    "requests": [],
                }
            )
            confirm_callback = MagicMock(return_value=True)
            bridge = SettingsBridgeServer(
                store,
                port=0,
                background_usage_query_callback=query_callback,
                background_usage_detail_callback=detail_callback,
                background_usage_confirm_callback=confirm_callback,
            )
            try:
                bridge.start()
                with self.assertRaises(HTTPError) as denied:
                    urlopen(f"{bridge.url}/background-usage", timeout=2)
                self.assertEqual(denied.exception.code, 403)

                with urlopen(
                    f"{bridge.background_usage_url}&range=7d&feature=memory_consolidation",
                    timeout=2,
                ) as response:
                    list_payload = json.loads(response.read().decode("utf-8"))
                detail_url = bridge.background_usage_url.replace(
                    "/background-usage?",
                    "/background-usage/detail?",
                )
                with urlopen(f"{detail_url}&eventId={event_id}", timeout=2) as response:
                    detail_payload = json.loads(response.read().decode("utf-8"))
                confirm_url = bridge.background_usage_url.replace(
                    "/background-usage?",
                    "/background-usage/confirm?",
                )
                request = Request(
                    confirm_url,
                    data=json.dumps({"eventId": event_id}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    confirm_payload = json.loads(response.read().decode("utf-8"))
            finally:
                bridge.close()

        self.assertEqual(list_payload["status"], "ok")
        self.assertNotIn("prompt", json.dumps(list_payload).casefold())
        query_callback.assert_called_once_with(
            {
                "range_key": "7d",
                "feature": "memory_consolidation",
                "model": "",
                "event_id": "",
            }
        )
        self.assertEqual(
            detail_payload["backgroundUsageDetail"]["prompt"],
            "sensitive local prompt",
        )
        detail_callback.assert_called_once_with(event_id)
        self.assertTrue(confirm_payload["changed"])
        confirm_callback.assert_called_once_with(event_id)

    def test_legacy_fetch_prices_endpoint_cannot_bypass_version_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            existing = UserConfig.defaults()
            existing.provider_settings = {
                "custom": ProviderSettings(
                    model_prices={"gpt-5": ModelPrice(1.0, 1.0, 1.0, 1.0)},
                    pricing_url="https://pricing.example/custom.json",
                ),
                "muyuan": ProviderSettings(
                    model_prices={"gpt-5": ModelPrice(2.0, 2.0, 2.0, 2.0)},
                    pricing_url="https://pricing.example/muyuan-old.json",
                ),
            }
            store.save(existing)
            bridge = SettingsBridgeServer(store, port=0)
            try:
                url = bridge.start()
                request = Request(
                    f"{url}/prices/fetch",
                    data=json.dumps(
                        {
                            "provider": "muyuan",
                            "url": "https://pricing.example/muyuan-new.json",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                fetch = patch("codex_usage_hud.config.urlopen")
                with fetch as fetch_mock, self.assertRaises(HTTPError) as rejected:
                    urlopen(request, timeout=2)
                payload = json.loads(rejected.exception.read().decode("utf-8"))
                persisted = store.load()
            finally:
                bridge.close()

        self.assertEqual(rejected.exception.code, 409)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("生效时间", payload["message"])
        fetch_mock.assert_not_called()
        self.assertEqual(
            persisted.provider_settings["muyuan"].pricing_url,
            "https://pricing.example/muyuan-old.json",
        )
        self.assertEqual(
            persisted.provider_settings["muyuan"].model_prices["gpt-5"].input,
            2.0,
        )
        self.assertEqual(
            persisted.provider_settings["custom"].pricing_url,
            "https://pricing.example/custom.json",
        )
        self.assertEqual(
            persisted.provider_settings["custom"].model_prices["gpt-5"].input,
            1.0,
        )

    def test_settings_endpoint_cannot_bypass_versioned_price_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            existing = UserConfig.defaults()
            store.save(existing)
            bridge = SettingsBridgeServer(store, port=0)
            try:
                url = bridge.start()
                request = Request(
                    f"{url}/settings",
                    data=json.dumps(
                        {
                            "settings": {
                                "model_prices": {
                                    "gpt-5.6-sol": {
                                        "input": 99,
                                        "cached_input": 9.9,
                                        "cache_write": 99,
                                        "output": 199,
                                        "reasoning": 199,
                                    }
                                },
                                "pricing_versions": [
                                    {
                                        "version_id": "bypass-version",
                                        "model": "gpt-5.6-sol",
                                        "input": 99,
                                        "cached_input": 9.9,
                                        "cache_write": 99,
                                        "output": 199,
                                        "reasoning": 199,
                                        "effective_at": "2026-08-01T00:00:00Z",
                                        "created_at": "2026-08-01T00:00:00Z",
                                        "created_by": "user_edit",
                                        "source": "manual",
                                    }
                                ],
                            }
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(request, timeout=2)
                payload = json.loads(rejected.exception.read().decode("utf-8"))
                persisted = store.load()
            finally:
                bridge.close()

        self.assertEqual(rejected.exception.code, 409)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("生效时间", payload["message"])
        self.assertEqual(persisted.pricing_versions, ())
        self.assertEqual(persisted.model_prices["gpt-5.6-sol"].input, 5.0)

    def test_settings_endpoint_loads_and_saves_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            existing = UserConfig.defaults()
            existing.daily_budget_usd = 12.34
            store.save(existing)
            bridge = SettingsBridgeServer(store, port=0)
            try:
                url = bridge.start()
                with urlopen(f"{url}/settings", timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    private_network_header = response.headers.get(
                        "Access-Control-Allow-Private-Network"
                    )

                self.assertEqual(payload["status"], "ok")
                self.assertIn("settingsPath", payload)
                self.assertEqual(private_network_header, "true")

                request = Request(
                    f"{url}/settings",
                    data=json.dumps(
                        {"settings": {"weekly_adjustment_usd": 9.25}}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                persisted = store.load()
                persisted_adjustment = persisted.weekly_adjustment_usd
                persisted_daily_budget = persisted.daily_budget_usd
            finally:
                bridge.close()

        self.assertEqual(saved["status"], "ok")
        self.assertEqual(saved["settings"]["weekly_adjustment_usd"], 9.25)
        self.assertEqual(persisted_adjustment, 9.25)
        self.assertEqual(persisted_daily_budget, 12.34)

    def test_configured_bridge_port_is_stable_when_available(self) -> None:
        port = _available_port()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            bridge = SettingsBridgeServer(store, port=port)
            try:
                url = bridge.start()
                server_thread = bridge._thread
            finally:
                bridge.close()

        self.assertTrue(
            url.endswith(f":{port}"),
            msg=url,
        )
        self.assertIsNotNone(server_thread)
        self.assertFalse(server_thread.is_alive())

    def test_restart_endpoint_invokes_restart_callback(self) -> None:
        restart_requested = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            bridge = SettingsBridgeServer(
                store,
                port=0,
                restart_callback=restart_requested.set,
            )
            try:
                url = bridge.start()
                request = Request(
                    f"{url}/restart",
                    data=json.dumps({"reason": "settings"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(
                        response.headers.get("Content-Type"),
                        "application/json; charset=utf-8",
                    )
            finally:
                bridge.close()

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(restart_requested.is_set())

    def test_command_endpoint_invokes_command_callback(self) -> None:
        received: list[dict[str, object]] = []
        command_received = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")

            def on_command(command: dict[str, object]) -> None:
                received.append(command)
                command_received.set()

            bridge = SettingsBridgeServer(
                store,
                port=0,
                command_callback=on_command,
            )
            try:
                url = bridge.start()
                request = Request(
                    f"{url}/command",
                    data=json.dumps({"action": "exit", "id": "cmd-1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                bridge.close()

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(command_received.is_set())
        self.assertEqual(received, [{"action": "exit", "id": "cmd-1"}])

    def test_command_endpoint_accepts_storage_command_without_scanning_http_thread(self) -> None:
        received: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            bridge = SettingsBridgeServer(
                store,
                port=0,
                command_callback=lambda command: received.append(command),
            )
            try:
                url = bridge.start()
                request = Request(
                    f"{url}/command",
                    data=json.dumps({"action": "scan", "requestId": "storage-1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                bridge.close()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(received, [{"action": "scan", "requestId": "storage-1"}])

    def test_active_session_endpoint_invokes_callback(self) -> None:
        received: list[dict[str, object]] = []
        session_received = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")

            def on_active_session(payload: dict[str, object]) -> None:
                received.append(payload)
                session_received.set()

            bridge = SettingsBridgeServer(
                store,
                port=0,
                active_session_callback=on_active_session,
            )
            try:
                url = bridge.start()
                request = Request(
                    f"{url}/active-session",
                    data=json.dumps(
                        {"sessionId": "thread-1", "title": "Selected Thread"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                bridge.close()

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(session_received.is_set())
        self.assertEqual(
            received,
            [{"sessionId": "thread-1", "title": "Selected Thread"}],
        )


if __name__ == "__main__":
    unittest.main()
