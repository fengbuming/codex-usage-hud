from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.config import UserConfig
from codex_usage_hud.provider_registry import discover_provider_registry
from codex_usage_hud.usage_insights import _provider_registry_payload


class ProviderRegistryTests(unittest.TestCase):
    def test_default_app_provider_reads_api_key_from_paired_auth_store(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://api.example/v1"\n'
            'wire_api = "responses"\n'
            "requires_openai_auth = true\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            (root / "auth.json").write_text(
                '{"OPENAI_API_KEY": "app-secret"}\n', encoding="utf-8"
            )
            registry = discover_provider_registry(
                user_config=UserConfig.defaults(),
                config_path=config_path,
                include_history=False,
            )
            payload = _provider_registry_payload(
                SimpleNamespace(provider_registry=registry)
            )

        self.assertEqual(registry.config_path, str(config_path))
        self.assertEqual(registry.entries["custom"].env_key, "")
        self.assertTrue(registry.entries["custom"].has_api_key)
        self.assertFalse(registry.entries["custom"].official_account)
        self.assertTrue(registry.entries["custom"].requires_openai_auth)
        self.assertEqual(payload["custom"]["apiKey"], "app-secret")
        self.assertTrue(payload["custom"]["requiresOpenaiAuth"])

    def test_registry_marks_official_account_provider_as_locked(self) -> None:
        config_text = (
            'model_provider = "custom"\n'
            "[model_providers.custom]\n"
            'base_url = "https://api.example/v1"\n'
            "requires_openai_auth = true\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            (root / "auth.json").write_text(
                '{"tokens": {"refresh_token": "redacted"}}\n',
                encoding="utf-8",
            )
            registry = discover_provider_registry(
                user_config=UserConfig.defaults(),
                config_path=config_path,
                include_history=False,
            )
            payload = _provider_registry_payload(
                SimpleNamespace(provider_registry=registry)
            )

        self.assertTrue(registry.entries["custom"].official_account)
        self.assertTrue(payload["custom"]["officialAccount"])
        self.assertTrue(payload["custom"]["requiresOpenaiAuth"])

    def test_registry_exposes_requires_openai_auth_per_definition(self) -> None:
        config_text = (
            'model_provider = "custom"\n'
            "[model_providers.custom]\n"
            'base_url = "https://api.example/v1"\n'
            "requires_openai_auth = true\n"
            "\n"
            "[model_providers.muyuan]\n"
            'base_url = "https://old.example/v1"\n'
            'env_key = "MUYUAN_API_KEY"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            registry = discover_provider_registry(
                user_config=UserConfig.defaults(),
                config_path=config_path,
                include_history=False,
            )
            payload = _provider_registry_payload(
                SimpleNamespace(provider_registry=registry)
            )

        self.assertTrue(registry.entries["custom"].requires_openai_auth)
        self.assertFalse(registry.entries["muyuan"].requires_openai_auth)
        self.assertTrue(payload["custom"]["requiresOpenaiAuth"])
        self.assertFalse(payload["muyuan"]["requiresOpenaiAuth"])

    def test_default_environment_provider_exposes_environment_key_and_config(self) -> None:
        config_text = (
            'model_provider = "muyuan"\n'
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://api.example/v1"\n'
            'env_key = "MUYUAN_API_KEY"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            registry = discover_provider_registry(
                user_config=UserConfig.defaults(),
                config_path=config_path,
                include_history=False,
            )
            with patch(
                "codex_usage_hud.usage_insights._user_environment_value",
                return_value="environment-secret",
            ):
                payload = _provider_registry_payload(
                    SimpleNamespace(provider_registry=registry)
                )

        self.assertEqual(payload["muyuan"]["apiKey"], "environment-secret")
        self.assertIn('env_key = "MUYUAN_API_KEY"', payload["muyuan"]["configText"])

    def test_default_bearer_clone_exposes_key_and_environment_metadata(self) -> None:
        config_text = (
            'model_provider = "token-x-copy"\n'
            "[model_providers.token-x-copy]\n"
            'name = "Token X copy"\n'
            'base_url = "https://api.example/v1"\n'
            'experimental_bearer_token = "inline-secret"\n'
            'env_key = "TOKEN_X_COPY_API_KEY"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            registry = discover_provider_registry(
                user_config=UserConfig.defaults(),
                config_path=config_path,
                include_history=False,
            )
            payload = _provider_registry_payload(
                SimpleNamespace(provider_registry=registry)
            )

        self.assertEqual(payload["token-x-copy"]["apiKey"], "inline-secret")
        self.assertEqual(payload["token-x-copy"]["envKey"], "TOKEN_X_COPY_API_KEY")
        self.assertTrue(payload["token-x-copy"]["hasApiKey"])
        self.assertTrue(payload["token-x-copy"]["usesExperimentalBearer"])

    def test_registry_uses_base_provider_profiles_saved_settings_and_recent_history(self) -> None:
        now = datetime(2026, 7, 16, tzinfo=timezone.utc)
        config_text = """
model_provider = "custom"

[model_providers]
[model_providers.custom]
name = "OpenAI"
[model_providers.muyuan]
name = "Muyuan"

[profiles.muyuan]
model_provider = "muyuan"
[profiles.shared]
model_provider = "custom"
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            session_path = root / "sessions" / "2026" / "recent.jsonl"
            session_path.parent.mkdir(parents=True)
            session_path.write_text(
                json.dumps(
                    {
                        "timestamp": (now - timedelta(days=2)).isoformat(),
                        "type": "session_meta",
                        "payload": {"model_provider": "history-only"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            archived = root / "archived_sessions" / "old.jsonl"
            archived.parent.mkdir(parents=True)
            archived.write_text(
                json.dumps(
                    {
                        "timestamp": (now - timedelta(days=31)).isoformat(),
                        "type": "session_meta",
                        "payload": {"model_provider": "too-old"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config = UserConfig.from_dict(
                {"provider_settings": {"saved-only": {"model_prices": {}}}}
            )
            registry = discover_provider_registry(
                user_config=config,
                config_path=config_path,
                sessions_root=root / "sessions",
                now=now,
            )

        self.assertEqual(registry.app_provider, "custom")
        self.assertEqual(
            registry.providers(),
            ("custom", "muyuan", "saved-only", "history-only"),
        )
        self.assertEqual(registry.entries["muyuan"].profile_names, ("muyuan",))
        self.assertIn("[model_providers.muyuan]", registry.entries["muyuan"].config_text)
        self.assertEqual(registry.entries["custom"].profile_names, ("shared",))
        self.assertIn("[model_providers.custom]", registry.entries["custom"].config_text)
        self.assertTrue(registry.entries["history-only"].historical_only)
        self.assertIn("saved-only", registry.entries)
        self.assertNotIn("too-old", registry.entries)

    def test_registry_keeps_unknown_only_when_a_recent_session_lacks_provider(self) -> None:
        now = datetime(2026, 7, 16, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "sessions" / "unknown.jsonl"
            session_path.parent.mkdir(parents=True)
            session_path.write_text(
                json.dumps(
                    {
                        "timestamp": now.isoformat(),
                        "type": "session_meta",
                        "payload": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            registry = discover_provider_registry(
                user_config=UserConfig.defaults(),
                config_path=root / "missing.toml",
                sessions_root=root / "sessions",
                now=now,
            )

        self.assertIn("unknown", registry.entries)
        self.assertTrue(registry.entries["unknown"].historical_only)

    def test_registry_can_skip_history_discovery_for_foreground_provider_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "sessions" / "2026" / "recent.jsonl"
            session_path.parent.mkdir(parents=True)
            session_path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-07-16T00:00:00+00:00",
                        "type": "session_meta",
                        "payload": {"model_provider": "history-only"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            registry = discover_provider_registry(
                user_config=UserConfig.defaults(),
                config_path=root / "missing.toml",
                sessions_root=root / "sessions",
                include_history=False,
            )

        self.assertNotIn("history-only", registry.entries)


if __name__ == "__main__":
    unittest.main()
