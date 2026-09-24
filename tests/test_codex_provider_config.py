from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import codex_usage_hud.codex_provider_config as provider_config_module
from codex_usage_hud.codex_provider_config import (
    CODEX_AUTH_API_KEY,
    _delete_user_environment_value,
    _chat_completions_url,
    clone_provider_with_bearer_key,
    delete_provider_config,
    fetch_provider_models,
    fetch_provider_models_for_cli,
    read_provider_definitions,
    read_codex_auth_api_key,
    save_provider_configs,
    send_cli_chat_probe,
    send_provider_chat_probe,
    set_default_codex_provider,
    verify_provider_connectivity,
)


class CodexProviderConfigTests(unittest.TestCase):
    def test_atomic_provider_write_preserves_existing_mode(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenAI"\n\n'
            "[model_providers.hiyo]\n"
            'name = "Hiyo"\n'
            'base_url = "https://hiyo.example/v1"\n'
            'env_key = "HIYO_API_KEY"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            if os.name != "nt":
                path.chmod(0o600)
            original_mode = stat.S_IMODE(path.stat().st_mode)
            with patch.object(
                provider_config_module.os,
                "chmod",
                wraps=provider_config_module.os.chmod,
            ) as chmod:
                set_default_codex_provider("hiyo", config_path=path)

            self.assertTrue(
                any(
                    int(call.args[1]) == original_mode
                    for call in chmod.call_args_list
                )
            )
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), original_mode)

    def test_delete_user_environment_value_uses_winreg_signature(self) -> None:
        fake_winreg = MagicMock()
        handle = object()
        fake_winreg.CreateKeyEx.return_value.__enter__.return_value = handle

        with patch.object(
            sys.modules["os"], "name", "nt"
        ), patch.dict(sys.modules, {"winreg": fake_winreg}), patch(
            "codex_usage_hud.codex_provider_config._broadcast_environment_change"
        ):
            _delete_user_environment_value("MUYUAN_API_KEY")

        fake_winreg.DeleteValue.assert_called_once_with(handle, "MUYUAN_API_KEY")

    def test_default_provider_cannot_be_deleted(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "默认 Codex App Provider"):
                delete_provider_config("custom", config_path=path)
            self.assertEqual(path.read_text(encoding="utf-8"), config_text)

    def test_set_default_provider_switches_top_level_key_and_preserves_toml(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenRouter 代理"\n'
            'base_url = "https://openrouter.zjxqai.com/v1"\n'
            'env_key = "OPENROUTER_API_KEY"\n\n'
            "[model_providers.hiyo]\n"
            'name = "Hiyo Free"\n'
            'base_url = "https://free.hiyo.top/v1"\n'
            'env_key = "HIYO_API_KEY"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            result = set_default_codex_provider("hiyo", config_path=path)
            self.assertTrue(result["changed"])
            self.assertEqual(result["providerId"], "hiyo")
            updated = path.read_text(encoding="utf-8")
            self.assertEqual(
                updated.splitlines()[0],
                'model_provider = "hiyo"',
            )
            self.assertIn('name = "OpenRouter 代理"', updated)
            self.assertIn('base_url = "https://free.hiyo.top/v1"', updated)
            self.assertNotIn('model_provider = "custom"', updated)

    def test_set_default_provider_is_idempotent(self) -> None:
        config_text = (
            'model_provider = "hiyo"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenRouter 代理"\n'
            'base_url = "https://openrouter.zjxqai.com/v1"\n'
            'env_key = "OPENROUTER_API_KEY"\n\n'
            "[model_providers.hiyo]\n"
            'name = "Hiyo Free"\n'
            'base_url = "https://free.hiyo.top/v1"\n'
            'env_key = "HIYO_API_KEY"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            result = set_default_codex_provider("hiyo", config_path=path)
            self.assertFalse(result["changed"])
            self.assertEqual(path.read_text(encoding="utf-8"), config_text)

    def test_set_default_provider_rejects_unknown_and_invalid_ids(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenRouter 代理"\n'
            'base_url = "https://openrouter.zjxqai.com/v1"\n'
            'env_key = "OPENROUTER_API_KEY"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未在 config.toml"):
                set_default_codex_provider("nope", config_path=path)
            with self.assertRaisesRegex(ValueError, "不合法"):
                set_default_codex_provider("bad id!", config_path=path)
            self.assertEqual(path.read_text(encoding="utf-8"), config_text)

    def test_set_default_provider_appends_key_when_missing(self) -> None:
        config_text = (
            "[model_providers.custom]\n"
            'name = "OpenRouter 代理"\n'
            'base_url = "https://openrouter.zjxqai.com/v1"\n'
            'env_key = "OPENROUTER_API_KEY"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            result = set_default_codex_provider("custom", config_path=path)
            self.assertTrue(result["changed"])
            updated = path.read_text(encoding="utf-8")
            self.assertTrue(updated.startswith('model_provider = "custom"'))
            self.assertIn("[model_providers.custom]", updated)

    def test_delete_provider_removes_related_tables_and_profile(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n\n'
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://muyuan.example/v1"\n\n'
            "[model_providers.muyuan.headers]\n"
            'x-tenant = "one"\n\n'
            "[profiles.muyuan]\n"
            'model_provider = "muyuan"\n'
            'model = "gpt-5"\n\n'
            "[features]\n"
            "memories = true\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            profile = root / "muyuan.config.toml"
            profile.write_text('model_provider = "muyuan"\n', encoding="utf-8")

            result = delete_provider_config("muyuan", config_path=path)
            updated = path.read_text(encoding="utf-8")

        self.assertTrue(result["changed"])
        self.assertNotIn("[model_providers.muyuan]", updated)
        self.assertNotIn("[profiles.muyuan]", updated)
        self.assertIn('model_provider = "custom"', updated)
        self.assertIn("[features]\nmemories = true", updated)
        self.assertFalse(profile.exists())

    @patch("codex_usage_hud.codex_provider_config._delete_user_environment_value")
    def test_reserved_builtin_provider_ids_rejected_on_add_and_edit(
        self, mock_delete_env: MagicMock
    ) -> None:
        """Codex 内置保留 ID（openai/ollama/lmstudio）不能作为自定义供应商新增或编辑。"""
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://example/v1"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            # 模拟用户 config.toml 中已存在非法的 ollama 段（例如旧版 HUD 写入）。
            path.write_text(
                config_text
                + "\n[model_providers.ollama]\n"
                + 'name = "ollama"\n'
                + 'base_url = "https://ollama.com/v1"\n'
                + 'env_key = "OLLAMA_API_KEY"\n'
                + 'wire_api = "responses"\n',
                encoding="utf-8",
            )

            for reserved_id in ("openai", "ollama", "lmstudio", "Ollama"):
                with self.subTest(reserved=reserved_id):
                    with self.assertRaisesRegex(ValueError, "内置 Provider 重名"):
                        save_provider_configs(
                            [
                                {
                                    "provider_id": reserved_id,
                                    "base_url": "https://new.example/v1",
                                    "env_key": "NEW_API_KEY",
                                    "api_key": "secret",
                                    "is_new": True,
                                }
                            ],
                            config_path=path,
                        )
                    # 编辑已有 ollama 段同样拦截，避免把保留 ID 配置写回。
                    if reserved_id.casefold() == "ollama":
                        with self.assertRaisesRegex(ValueError, "内置 Provider 重名"):
                            save_provider_configs(
                                [
                                    {
                                        "provider_id": "ollama",
                                        "base_url": "https://new.example/v1",
                                        "env_key": "OLLAMA_API_KEY",
                                        "api_key": "secret",
                                        "is_new": False,
                                    }
                                ],
                                config_path=path,
                            )
            # 删除保留 ID 段是允许的（恢复 config 合法性的唯一途径）。
            result = delete_provider_config("ollama", config_path=path)
            self.assertTrue(result["changed"])
            updated = path.read_text(encoding="utf-8")
            self.assertNotIn("[model_providers.ollama]", updated)
        mock_delete_env.assert_called_once_with("OLLAMA_API_KEY")

    def test_similar_but_not_reserved_provider_id_is_allowed(self) -> None:
        """形似保留 ID 的自定义 ID（如 ollama-local）仍可正常新增。"""
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://example/v1"\n'
        )
        env_values: dict[str, str] = {}

        def fake_get_env(key: str) -> str:
            return env_values.get(str(key), "")

        def fake_set_env(key: str, value: str) -> None:
            env_values[str(key)] = str(value)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=fake_get_env,
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=fake_set_env,
            ):
                result = save_provider_configs(
                    [
                        {
                            "provider_id": "ollama-local",
                            "base_url": "http://localhost:11434/v1",
                            "env_key": "OLLAMA_API_KEY",
                            "api_key": "secret",
                            "is_new": True,
                        }
                    ],
                    config_path=path,
                )
                updated = path.read_text(encoding="utf-8")
        self.assertTrue(result["changed"])
        self.assertIn("[model_providers.ollama-local]", updated)
        self.assertEqual(env_values.get("OLLAMA_API_KEY"), "secret")

    def test_default_custom_provider_updates_config_and_auth_without_profile(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            '[model_providers]\n'
            '[model_providers.custom]\n'
            'name = "OpenAI"\n'
            'base_url = "https://old.example/v1"\n'
            'wire_api = "responses"\n'
            'requires_openai_auth = true\n'
            'experimental_bearer_token = "keep-out-of-hud"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            (root / "auth.json").write_text(
                '{"OPENAI_API_KEY": "old-secret", "other": "keep"}\n',
                encoding="utf-8",
            )
            result = save_provider_configs(
                [
                    {
                        "provider_id": "custom",
                        "base_url": "https://new.example/v1",
                        "api_key": "new-secret",
                        "section_text": (
                            "[model_providers.custom]\n"
                            'name = "Custom App"\n'
                            'base_url = "https://new.example/v1"\n'
                            'wire_api = "responses"'
                        ),
                    }
                ],
                config_path=path,
            )
            updated = path.read_text(encoding="utf-8")
            auth = json.loads((root / "auth.json").read_text(encoding="utf-8"))
            definition = read_provider_definitions(path)["custom"]
            auth_value = read_codex_auth_api_key(path)

        self.assertTrue(result["changed"])
        self.assertTrue(result["defaultProviderEdited"])
        self.assertEqual(result["profilePaths"], [])
        self.assertEqual(result["environmentKeys"], [])
        self.assertEqual(result["authKeys"], [CODEX_AUTH_API_KEY])
        self.assertIn('base_url = "https://new.example/v1"', updated)
        self.assertIn("requires_openai_auth = true", updated)
        self.assertIn('experimental_bearer_token = "keep-out-of-hud"', updated)
        self.assertNotIn('env_key = "', updated)
        self.assertEqual(auth, {"OPENAI_API_KEY": "new-secret", "other": "keep"})
        self.assertEqual(auth_value, "new-secret")
        self.assertEqual(definition.auth_key, CODEX_AUTH_API_KEY)
        self.assertTrue(definition.has_api_key)

    def test_default_custom_provider_noop_does_not_request_restart(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://same.example/v1"\n'
            'wire_api = "responses"\n'
            "requires_openai_auth = true\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            (root / "auth.json").write_text(
                '{"OPENAI_API_KEY": "same-secret"}\n', encoding="utf-8"
            )
            result = save_provider_configs(
                {
                    "provider_id": "custom",
                    "base_url": "https://same.example/v1",
                    "api_key": "same-secret",
                    "section_text": (
                        "[model_providers.custom]\n"
                        'name = "ignored preview"\n'
                        'base_url = "https://same.example/v1"\n'
                        'wire_api = "responses"'
                    ),
                },
                config_path=path,
            )

        self.assertFalse(result["changed"])
        self.assertFalse(result["defaultProviderEdited"])
        self.assertEqual(result["authKeys"], [])

    def test_default_auth_write_rolls_back_when_profile_write_fails(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://old.example/v1"\n'
            'wire_api = "responses"\n'
            "requires_openai_auth = true\n\n"
            "[model_providers.muyuan]\n"
            'base_url = "https://muyuan.example/v1"\n'
            'env_key = "MUYUAN_API_KEY"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            auth_path = root / "auth.json"
            auth_path.write_text(
                '{"OPENAI_API_KEY": "old-secret"}\n', encoding="utf-8"
            )
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                return_value="",
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=RuntimeError("registry unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
                    save_provider_configs(
                        [
                            {
                                "provider_id": "custom",
                                "base_url": "https://new.example/v1",
                                "api_key": "new-secret",
                            },
                            {
                                "provider_id": "muyuan",
                                "base_url": "https://new-muyuan.example/v1",
                                "env_key": "MUYUAN_API_KEY",
                                "api_key": "muyuan-secret",
                            },
                        ],
                        config_path=config_path,
                    )

            self.assertEqual(config_path.read_text(encoding="utf-8"), config_text)
            self.assertEqual(
                auth_path.read_text(encoding="utf-8"),
                '{"OPENAI_API_KEY": "old-secret"}\n',
            )

    def test_default_auth_write_validation_failure_rolls_back(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://old.example/v1"\n'
            'wire_api = "responses"\n'
            "requires_openai_auth = true\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            auth_path = root / "auth.json"
            auth_path.write_text(
                '{"OPENAI_API_KEY": "old-secret"}\n', encoding="utf-8"
            )

            real_read_text = provider_config_module._read_text_exact
            new_secret_reads = [0]

            def flaky_read_text(path):
                # 正常读取磁盘；只有第一次读到新 key 的「写后校验」读取返回
                # 不一致内容，模拟校验失败。恢复阶段的 CAS 读取（第二次读到
                # 新 key）仍走真实读取，确保 _write_codex_auth_api_key 内部
                # 回滚能把 auth.json 恢复成旧值。
                text = real_read_text(path)
                if path == auth_path and "new-secret" in text:
                    new_secret_reads[0] += 1
                    if new_secret_reads[0] == 1:
                        return text + "\n"
                return text

            with patch(
                "codex_usage_hud.codex_provider_config._read_text_exact",
                side_effect=flaky_read_text,
            ):
                with self.assertRaisesRegex(RuntimeError, "写入后校验失败"):
                    save_provider_configs(
                        {
                            "provider_id": "custom",
                            "base_url": "https://new.example/v1",
                            "api_key": "new-secret",
                        },
                        config_path=config_path,
                    )

            # auth.json 已被 _write_codex_auth_api_key 内部回滚恢复原值，
            # 不会因为外层 auth_changed 未置位而残留新 key。
            self.assertEqual(
                auth_path.read_text(encoding="utf-8"),
                '{"OPENAI_API_KEY": "old-secret"}\n',
            )
            # config.toml 也由外层事务回滚。
            self.assertEqual(config_path.read_text(encoding="utf-8"), config_text)

    def test_official_account_rejects_default_provider_edit(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://old.example/v1"\n'
            'wire_api = "responses"\n'
            "requires_openai_auth = true\n"
        )
        auth_text = (
            '{"tokens": {"access_token": "redacted", "account_id": "account"}}\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            auth_path = root / "auth.json"
            config_path.write_text(config_text, encoding="utf-8")
            auth_path.write_text(auth_text, encoding="utf-8")

            definition = read_provider_definitions(config_path)["custom"]
            with self.assertRaisesRegex(ValueError, "官方账号登录"):
                save_provider_configs(
                    {
                        "provider_id": "custom",
                        "base_url": "https://new.example/v1",
                        "api_key": "attempted-key",
                    },
                    config_path=config_path,
                )

            self.assertTrue(definition.official_account)
            self.assertEqual(config_path.read_text(encoding="utf-8"), config_text)
            self.assertEqual(auth_path.read_text(encoding="utf-8"), auth_text)

    def test_official_account_rejects_non_custom_default_provider_edit(self) -> None:
        # 官方账号 guard 必须覆盖任意默认供应商，而不只是 custom /
        # requires_openai_auth：非 custom 默认供应商（如内置 openai）同样禁止编辑。
        config_text = (
            'model_provider = "openai"\n\n'
            "[model_providers]\n"
            "[model_providers.openai]\n"
            'name = "OpenAI"\n'
            'base_url = "https://old.example/v1"\n'
            'env_key = "OPENAI_API_KEY"\n'
            'wire_api = "responses"\n'
        )
        auth_text = (
            '{"tokens": {"access_token": "redacted", "account_id": "account"}}\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            auth_path = root / "auth.json"
            config_path.write_text(config_text, encoding="utf-8")
            auth_path.write_text(auth_text, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "官方账号登录"):
                save_provider_configs(
                    {
                        "provider_id": "openai",
                        "base_url": "https://new.example/v1",
                        "api_key": "attempted-key",
                    },
                    config_path=config_path,
                )

            self.assertEqual(config_path.read_text(encoding="utf-8"), config_text)
            self.assertEqual(auth_path.read_text(encoding="utf-8"), auth_text)

    def test_read_provider_definitions_exposes_requires_openai_auth(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://api.example/v1"\n'
            "requires_openai_auth = true\n"
            "\n"
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://old.example/v1"\n'
            'env_key = "MUYUAN_API_KEY"\n'
            'wire_api = "responses"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")

            definitions = read_provider_definitions(config_path)

        self.assertTrue(definitions["custom"].requires_openai_auth)
        self.assertFalse(definitions["muyuan"].requires_openai_auth)

    def test_windows_newlines_are_preserved(self) -> None:
        config_text = (
            'model_provider = "custom"\r\n'
            '\r\n'
            '[model_providers]\r\n'
            '[model_providers.custom]\r\n'
            'name = "OpenAI"\r\n'
            '\r\n'
            '[model_providers.muyuan]\r\n'
            'name = "Muyuan"\r\n'
            'base_url = "https://old.example/v1"\r\n'
            'env_key = "MUYUAN_API_KEY"\r\n'
            'wire_api = "responses"\r\n'
            '\r\n'
            '[features]\r\n'
            'memories = true\r\n'
        )
        environment = {"MUYUAN_API_KEY": "old-secret"}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_bytes(config_text.encode("utf-8"))
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                save_provider_configs(
                    {
                        "muyuan": {
                            "base_url": "https://new.example/v1",
                            "env_key": "MUYUAN_API_KEY",
                            "api_key": "new-secret",
                        }
                    },
                    config_path=path,
                )
            updated_bytes = path.read_bytes()

        self.assertIn(b"\r\n", updated_bytes)
        self.assertNotIn(b"\n", updated_bytes.replace(b"\r\n", b""))

    def test_existing_provider_update_preserves_unrelated_toml(self) -> None:
        config_text = (
            'model_provider = "custom"\n'
            "\n"
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            "\n"
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://old.example/v1"\n'
            'env_key = "OLD_API_KEY"\n'
            'wire_api = "responses"\n'
            "\n"
            "[features]\n"
            "memories = true\n"
        )
        environment = {"OLD_API_KEY": "old-secret", "NEW_API_KEY": ""}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                result = save_provider_configs(
                    [
                        {
                            "provider_id": "muyuan",
                            "base_url": "https://new.example/v1",
                            "env_key": "NEW_API_KEY",
                            "api_key": "new-secret",
                        }
                    ],
                    config_path=path,
                )
            updated = path.read_text(encoding="utf-8")
            definitions = read_provider_definitions(path)

        self.assertTrue(result["changed"])
        self.assertEqual(environment["NEW_API_KEY"], "new-secret")
        self.assertIn('model_provider = "custom"', updated)
        self.assertIn("[features]\nmemories = true", updated)
        self.assertIn('name = "Muyuan"', updated)
        self.assertIn('base_url = "https://new.example/v1"', updated)
        self.assertIn('env_key = "NEW_API_KEY"', updated)
        self.assertEqual(definitions["muyuan"].base_url, "https://new.example/v1")
        self.assertEqual(definitions["muyuan"].env_key, "NEW_API_KEY")

    def test_numeric_leading_provider_id_requires_underscore_env_key(self) -> None:
        # 数字开头的 Provider ID（如 123abc）本身合法，但自动生成的环境变量名
        # 若保持数字开头（123ABC_API_KEY）会被 ENVIRONMENT_KEY_PATTERN 拒绝；
        # 修复后前端生成 _ 前缀（_123ABC_API_KEY），后端应接受该形式。
        config_text = 'model_provider = "custom"\n'
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            environment: dict[str, str] = {}
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                # 修复前的数字开头 env_key 必须被拒绝。
                with self.assertRaisesRegex(ValueError, "环境变量"):
                    save_provider_configs(
                        [
                            {
                                "provider_id": "123abc",
                                "base_url": "https://123.example/v1",
                                "env_key": "123ABC_API_KEY",
                                "api_key": "secret",
                            }
                        ],
                        config_path=path,
                    )
                # 修复后的 _ 前缀 env_key（前端自动生成）必须被接受。
                result = save_provider_configs(
                    [
                        {
                            "provider_id": "123abc",
                            "base_url": "https://123.example/v1",
                            "env_key": "_123ABC_API_KEY",
                            "api_key": "secret",
                        }
                    ],
                    config_path=path,
                )
            updated = path.read_text(encoding="utf-8")
            definitions = read_provider_definitions(path)

        self.assertTrue(result["changed"])
        self.assertEqual(environment["_123ABC_API_KEY"], "secret")
        self.assertIn("[model_providers.123abc]", updated)
        self.assertIn('env_key = "_123ABC_API_KEY"', updated)
        self.assertEqual(definitions["123abc"].env_key, "_123ABC_API_KEY")

    def test_editor_section_text_replaces_full_provider_body(self) -> None:
        config_text = (
            'model_provider = "custom"\n'
            "\n"
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            "\n"
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://old.example/v1"\n'
            'env_key = "MUYUAN_API_KEY"\n'
            'wire_api = "responses"\n'
            "\n"
            "[features]\n"
            "memories = true\n"
        )
        section_text = (
            "[model_providers.muyuan]\n"
            'name = "Edited display name"\n'
            'base_url = "https://edited.example/v1"\n'
            'env_key = "EDITED_API_KEY"\n'
            'wire_api = "responses"\n'
            "supports_websockets = true"
        )
        environment = {"MUYUAN_API_KEY": "old-secret", "EDITED_API_KEY": ""}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                save_provider_configs(
                    {
                        "provider_id": "muyuan",
                        "section_text": section_text,
                        "api_key": "edited-secret",
                    },
                    config_path=path,
                )
            updated = path.read_text(encoding="utf-8")
            definition = read_provider_definitions(path)["muyuan"]

        self.assertIn('name = "Edited display name"', updated)
        self.assertIn("supports_websockets = true", updated)
        self.assertNotIn('name = "Muyuan"', updated)
        self.assertEqual(environment["EDITED_API_KEY"], "edited-secret")
        self.assertEqual(
            definition.section_text.replace("\r\n", "\n").rstrip(),
            section_text,
        )

    def test_new_provider_creates_profile_and_defaults_scope_metadata(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        environment = {"CUNAI_API_KEY": ""}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                result = save_provider_configs(
                    [
                        {
                            "provider_id": "cunai",
                            "base_url": "https://cuna.example/v1",
                            "env_key": "CUNAI_API_KEY",
                            "api_key": "secret",
                            "is_new": True,
                        }
                    ],
                    config_path=path,
                )
            profile = root / "cunai.config.toml"
            updated = path.read_text(encoding="utf-8")
            profile_text = profile.read_text(encoding="utf-8")

        self.assertTrue(result["changed"])
        self.assertEqual(environment["CUNAI_API_KEY"], "secret")
        self.assertIn("[model_providers.cunai]", updated)
        self.assertIn('base_url = "https://cuna.example/v1"', updated)
        self.assertEqual(profile_text, 'model_provider = "cunai"\n')

    def test_new_provider_accepts_editor_section_text(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        section_text = (
            "[model_providers.cunai]\n"
            'name = "CUN.AI"\n'
            'base_url = "https://www.cun.ai/v1"\n'
            'env_key = "CUNAI_API_KEY"\n'
            'wire_api = "responses"\n'
            "supports_websockets = true"
        )
        environment = {"CUNAI_API_KEY": ""}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                save_provider_configs(
                    {
                        "provider_id": "cunai",
                        "section_text": section_text,
                        "api_key": "secret",
                        "is_new": True,
                    },
                    config_path=path,
                )
            self.assertEqual((root / "cunai.config.toml").read_text(encoding="utf-8"), 'model_provider = "cunai"\n')
            self.assertEqual(environment["CUNAI_API_KEY"], "secret")
            self.assertIn("supports_websockets = true", path.read_text(encoding="utf-8"))

    def test_editor_section_text_rejects_nested_table(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "其它 TOML 表头"):
                save_provider_configs(
                    {
                        "provider_id": "cunai",
                        "section_text": (
                            "[model_providers.cunai]\n"
                            'base_url = "https://www.cun.ai/v1"\n'
                            'env_key = "CUNAI_API_KEY"\n'
                            "[model_providers.cunai.headers]\n"
                            'x-api-key = "bad"\n'
                        ),
                        "api_key": "secret",
                        "is_new": True,
                    },
                    config_path=path,
                )

    def test_failed_environment_write_rolls_back_config_and_profile(self) -> None:
        config_text = (
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                return_value="",
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=RuntimeError("registry unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
                    save_provider_configs(
                        [
                            {
                                "provider_id": "broken",
                                "base_url": "https://broken.example/v1",
                                "env_key": "BROKEN_API_KEY",
                                "api_key": "secret",
                                "is_new": True,
                            }
                        ],
                        config_path=path,
                    )
            self.assertEqual(path.read_text(encoding="utf-8"), config_text)
            self.assertFalse((root / "broken.config.toml").exists())

    def test_delete_provider_removes_user_environment_variable(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenAI"\n\n'
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://muyuan.example/v1"\n'
            'env_key = "MUYUAN_API_KEY"\n'
        )
        deleted = []
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._delete_user_environment_value",
                side_effect=lambda name: deleted.append(name),
            ):
                result = delete_provider_config("muyuan", config_path=path)

        self.assertEqual(deleted, ["MUYUAN_API_KEY"])
        self.assertIn("environmentKeys", result)
        self.assertEqual(result["environmentKeys"], ["MUYUAN_API_KEY"])

    def test_delete_provider_without_env_key_does_not_touch_environment(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenAI"\n\n'
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://muyuan.example/v1"\n'
        )
        deleted = []
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._delete_user_environment_value",
                side_effect=lambda name: deleted.append(name),
            ):
                result = delete_provider_config("muyuan", config_path=path)

        self.assertEqual(deleted, [])
        self.assertEqual(result["environmentKeys"], [])


class VerifyProviderConnectivityTests(unittest.TestCase):
    def test_verify_provider_connectivity_calls_models_endpoint_with_bearer(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, length: int = -1) -> bytes:
                del length
                return b'{"data": [{"id": "gpt-4o"}]}'

        def fake_urlopen(request, timeout: float = 8.0):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse()

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=fake_urlopen,
        ):
            connected = verify_provider_connectivity(
                "https://api.example.com/v1/", "sk-test-secret"
            )

        self.assertEqual(captured["url"], "https://api.example.com/v1/models")
        self.assertEqual(captured["authorization"], "Bearer sk-test-secret")
        self.assertTrue(connected)

    def test_verify_provider_connectivity_accepts_response_without_data_list(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, length: int = -1) -> bytes:
                del length
                return b'{"object": "list", "data": []}'

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=lambda request, timeout: FakeResponse(),
        ):
            self.assertTrue(
                verify_provider_connectivity(
                    "https://api.example.com/v1", "key"
                )
            )

    def test_verify_provider_connectivity_rejects_missing_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "base_url"):
            verify_provider_connectivity("", "key")
        with self.assertRaisesRegex(ValueError, "API key"):
            verify_provider_connectivity("https://api.example.com/v1", "")

    def test_verify_provider_connectivity_surfaces_http_error(self) -> None:
        from urllib.error import HTTPError

        def fake_urlopen(request, timeout: float = 8.0):
            del request, timeout
            raise HTTPError("https://api.example.com/v1/models", 401, "Unauthorized", None, None)

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=fake_urlopen,
        ):
            with self.assertRaisesRegex(ValueError, "HTTP 401"):
                verify_provider_connectivity("https://api.example.com/v1", "key")


class FetchProviderModelsTests(unittest.TestCase):
    def _fake_urlopen(self, body: bytes):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, length: int = -1) -> bytes:
                del length
                return body

        return FakeResponse()

    def test_fetch_provider_models_parses_data_ids(self) -> None:
        body = b'{"object": "list", "data": [{"id": "gpt-5"}, {"id": "gpt-5.6"}, {"id": "gpt-5.6"}]}'
        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=lambda request, timeout: self._fake_urlopen(body),
        ):
            models = fetch_provider_models("https://api.example.com/v1", "key")

        self.assertEqual(models, ["gpt-5", "gpt-5.6"])

    def test_fetch_provider_models_accepts_bare_array_and_name_field(self) -> None:
        body = b'[{"name": "claude-3", "model": "claude-3"}]'
        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=lambda request, timeout: self._fake_urlopen(body),
        ):
            models = fetch_provider_models("https://api.example.com/v1", "key")

        self.assertEqual(models, ["claude-3"])

    def test_fetch_provider_models_returns_empty_for_non_json_body(self) -> None:
        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=lambda request, timeout: self._fake_urlopen(b"not json"),
        ):
            self.assertEqual(
                fetch_provider_models("https://api.example.com/v1", "key"), []
            )

    def test_fetch_provider_models_uses_models_endpoint(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout: float = 8.0):
            captured["url"] = request.full_url
            return self._fake_urlopen(b'{"data": [{"id": "gpt-5"}]}')

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=fake_urlopen,
        ):
            fetch_provider_models("https://api.example.com/v1/", "sk-test")

        self.assertEqual(captured["url"], "https://api.example.com/v1/models")

    def test_fetch_provider_models_rejects_missing_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "base_url"):
            fetch_provider_models("", "key")
        with self.assertRaisesRegex(ValueError, "API key"):
            fetch_provider_models("https://api.example.com/v1", "")


class FetchProviderModelsForCliTests(unittest.TestCase):
    @patch(
        "codex_usage_hud.codex_provider_config.fetch_provider_models",
        return_value=["gpt-5"],
    )
    def test_default_provider_uses_auth_json_instead_of_environment(
        self, fetch_models
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.toml"
            config_path.write_text(
                'model_provider = "custom"\n'
                "[model_providers.custom]\n"
                'name = "OpenAI"\n'
                'base_url = "https://api.example.com/v1"\n'
                'wire_api = "responses"\n'
                "requires_openai_auth = true\n",
                encoding="utf-8",
            )
            (root / "auth.json").write_text(
                '{"OPENAI_API_KEY": "sk-from-auth"}\n', encoding="utf-8"
            )
            result = fetch_provider_models_for_cli(
                "custom", config_path=config_path
            )

        fetch_models.assert_called_once_with(
            "https://api.example.com/v1", "sk-from-auth", 8.0
        )
        self.assertEqual(result["authKey"], CODEX_AUTH_API_KEY)
        self.assertEqual(result["envKey"], "")
        self.assertEqual(result["models"], ["gpt-5"])

    def test_missing_provider_definition_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text(
                '[model_providers]\nother = { base_url = "https://x" }\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "未在 config.toml 中找到"):
                fetch_provider_models_for_cli("missing", config_path=config_path)

    @patch("codex_usage_hud.codex_provider_config._user_environment_value")
    @patch("codex_usage_hud.codex_provider_config.urlopen")
    def test_resolves_base_url_env_key_and_queries_models(
        self, fake_urlopen, fake_env
    ) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, length: int = -1) -> bytes:
                del length
                return b'{"data": [{"id": "gpt-5"}]}'

        fake_urlopen.return_value = FakeResponse()
        fake_env.return_value = "sk-from-env"
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text(
                '[model_providers.muyuan]\n'
                'name = "muyuan"\n'
                'base_url = "https://api.example.com/v1"\n'
                'env_key = "MUYUAN_API_KEY"\n',
                encoding="utf-8",
            )
            result = fetch_provider_models_for_cli(
                "muyuan", config_path=config_path
            )

        self.assertEqual(fake_env.call_count, 2)
        for call_args in fake_env.call_args_list:
            self.assertEqual(call_args.args, ("MUYUAN_API_KEY",))
        self.assertEqual(result["provider"], "muyuan")
        self.assertEqual(result["models"], ["gpt-5"])
        self.assertEqual(result["envKey"], "MUYUAN_API_KEY")
        self.assertEqual(result["baseUrl"], "https://api.example.com/v1")

    def test_missing_api_key_raises(self) -> None:
        with patch(
            "codex_usage_hud.codex_provider_config._user_environment_value",
            return_value="",
        ):
            with tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "config.toml"
                config_path.write_text(
                    '[model_providers.muyuan]\n'
                    'name = "muyuan"\n'
                    'base_url = "https://api.example.com/v1"\n'
                    'env_key = "MUYUAN_API_KEY"\n',
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "没有可用的 API key"):
                    fetch_provider_models_for_cli(
                        "muyuan", config_path=config_path
                    )


class SendProviderChatProbeTests(unittest.TestCase):
    def _fake_urlopen(self, body: bytes = b""):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, length: int = -1) -> bytes:
                del length
                return body

        return FakeResponse()

    def test_chat_completions_url_handles_full_path_base_url(self) -> None:
        self.assertEqual(
            _chat_completions_url(
                "https://chatapi.weixin.qq.com/openai/v1/chat/completions"
            ),
            "https://chatapi.weixin.qq.com/openai/v1/chat/completions",
        )
        self.assertEqual(
            _chat_completions_url("https://api.example.com/v1"),
            "https://api.example.com/v1/chat/completions",
        )
        self.assertEqual(
            _chat_completions_url("https://api.example.com/v1/"),
            "https://api.example.com/v1/chat/completions",
        )

    def test_send_provider_chat_probe_posts_chat_completions_and_parses_reply(self) -> None:
        captured = {}
        body = b'{"choices": [{"message": {"role": "assistant", "content": "Hello!"}}]}'

        def fake_urlopen(request, timeout: float = 30.0):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["content_type"] = request.get_header("Content-type")
            captured["data"] = request.data.decode("utf-8")
            captured["timeout"] = timeout
            return self._fake_urlopen(body)

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=fake_urlopen,
        ):
            result = send_provider_chat_probe(
                "https://api.example.com/v1", "sk-test", "gpt-5", message="hi"
            )

        self.assertEqual(
            captured["url"], "https://api.example.com/v1/chat/completions"
        )
        self.assertEqual(captured["authorization"], "Bearer sk-test")
        self.assertEqual(captured["content_type"], "application/json")
        self.assertIn('"model": "gpt-5"', captured["data"])
        self.assertIn('"content": "hi"', captured["data"])
        self.assertEqual(result, {"ok": True, "reply": "Hello!", "model": "gpt-5"})

    def test_send_provider_chat_probe_uses_full_path_base_url_as_is(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout: float = 30.0):
            del timeout
            captured["url"] = request.full_url
            return self._fake_urlopen(
                b'{"choices": [{"message": {"content": "ok"}}]}'
            )

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=fake_urlopen,
        ):
            send_provider_chat_probe(
                "https://chatapi.weixin.qq.com/openai/v1/chat/completions",
                "key",
                "Deepseek-v4-flash",
            )

        self.assertEqual(
            captured["url"],
            "https://chatapi.weixin.qq.com/openai/v1/chat/completions",
        )

    def test_send_provider_chat_probe_surfaces_http_error_detail(self) -> None:
        from io import BytesIO
        from urllib.error import HTTPError

        def fake_urlopen(request, timeout: float = 30.0):
            del request, timeout
            raise HTTPError(
                "https://x.example/chat/completions",
                400,
                "Bad Request",
                None,
                BytesIO(b'{"error": {"message": "invalid model: model name not found", "code": 400}}'),
            )

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=fake_urlopen,
        ):
            result = send_provider_chat_probe(
                "https://x.example/v1", "key", "bad-model"
            )

        self.assertFalse(result["ok"])
        self.assertIn("HTTP 400", str(result["error"]))
        self.assertIn("invalid model", str(result["error"]))

    def test_send_provider_chat_probe_rejects_missing_inputs(self) -> None:
        self.assertFalse(send_provider_chat_probe("", "key", "gpt-5")["ok"])
        self.assertFalse(
            send_provider_chat_probe("https://x.example/v1", "", "gpt-5")["ok"]
        )
        self.assertFalse(
            send_provider_chat_probe("https://x.example/v1", "key", "")["ok"]
        )

    def test_send_provider_chat_probe_rejects_unrecognizable_reply(self) -> None:
        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=lambda request, timeout: self._fake_urlopen(b"not json"),
        ):
            result = send_provider_chat_probe(
                "https://api.example.com/v1", "key", "gpt-5"
            )

        self.assertFalse(result["ok"])
        self.assertIn("未返回可识别", str(result["error"]))


class SendCliChatProbeTests(unittest.TestCase):
    @patch("codex_usage_hud.codex_provider_config._user_environment_value")
    @patch("codex_usage_hud.codex_provider_config.urlopen")
    def test_send_cli_chat_probe_resolves_config_and_posts(self, fake_urlopen, fake_env) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, length: int = -1) -> bytes:
                del length
                return b'{"choices": [{"message": {"content": "ok"}}]}'

        captured = {}
        fake_urlopen.side_effect = lambda request, timeout: captured.update(
            {"url": request.full_url}
        ) or FakeResponse()
        fake_env.return_value = "sk-from-env"
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text(
                '[model_providers.qq]\n'
                'name = "qq"\n'
                'base_url = "https://chatapi.weixin.qq.com/openai/v1/chat/completions"\n'
                'env_key = "QQ_API_KEY"\n',
                encoding="utf-8",
            )
            result = send_cli_chat_probe(
                "qq", "Deepseek-v4-flash", config_path=config_path
            )

        self.assertEqual(captured["url"], "https://chatapi.weixin.qq.com/openai/v1/chat/completions")
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "qq")
        self.assertEqual(result["baseUrl"], "https://chatapi.weixin.qq.com/openai/v1/chat/completions")
        self.assertEqual(result["envKey"], "QQ_API_KEY")

    def test_send_cli_chat_probe_missing_provider_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text(
                '[model_providers]\nother = { base_url = "https://x" }\n',
                encoding="utf-8",
            )
            result = send_cli_chat_probe("missing", "gpt-5", config_path=config_path)

        self.assertFalse(result["ok"])
        self.assertIn("未在 config.toml 中找到", str(result["error"]))

    def test_send_cli_chat_probe_missing_api_key_returns_error(self) -> None:
        with patch(
            "codex_usage_hud.codex_provider_config._user_environment_value",
            return_value="",
        ):
            with tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "config.toml"
                config_path.write_text(
                    '[model_providers.muyuan]\n'
                    'name = "muyuan"\n'
                    'base_url = "https://api.example.com/v1"\n'
                    'env_key = "MUYUAN_API_KEY"\n',
                    encoding="utf-8",
                )
                result = send_cli_chat_probe(
                    "muyuan", "gpt-5", config_path=config_path
                )

        self.assertFalse(result["ok"])
        self.assertIn("没有可用的 API key", str(result["error"]))


class CloneProviderWithBearerKeyTests(unittest.TestCase):
    def test_clone_matches_mixed_case_source_id_and_restricts_config_mode(self) -> None:
        config_text = (
            'model_provider = "MuYuan"\n\n'
            "[model_providers.MuYuan]\n"
            'name = "Tenant API"\n'
            'base_url = "https://gateway.example/v1"\n'
            'wire_api = "responses"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            if os.name != "nt":
                path.chmod(0o644)
            replace_modes: list[int] = []
            real_replace = provider_config_module.os.replace

            def inspect_replace(source: str | Path, destination: str | Path) -> None:
                replace_modes.append(stat.S_IMODE(Path(source).stat().st_mode))
                real_replace(source, destination)

            with patch.object(
                provider_config_module.os,
                "replace",
                side_effect=inspect_replace,
            ):
                result = clone_provider_with_bearer_key(
                    "mUyUaN", "sk-mixed-case", config_path=path
                )

            self.assertEqual(result["newProviderId"], "muyuan-copy")
            updated = path.read_text(encoding="utf-8")
            self.assertIn("[model_providers.muyuan-copy]", updated)
            self.assertIn(
                'experimental_bearer_token = "sk-mixed-case"',
                updated,
            )
            self.assertEqual(
                updated.splitlines()[0], 'model_provider = "muyuan-copy"'
            )
            if os.name != "nt":
                self.assertEqual(replace_modes, [0o600])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_clone_preserves_non_auth_connection_options(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "Tenant API"\n'
            'base_url = "https://gateway.example/v1"\n'
            'wire_api = "responses"\n'
            'http_headers = { "X-Tenant" = "acme" }\n'
            'query_params = { "api-version" = "2025-04-01" }\n'
            'stream_idle_timeout_ms = 45000\n'
            'env_key = "TENANT_API_KEY"\n'
            'requires_openai_auth = true\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            clone_provider_with_bearer_key(
                "custom", "sk-clone-key-789", config_path=path
            )
            updated = path.read_text(encoding="utf-8")

        copy_section = updated.split("[model_providers.custom-copy]", 1)[1]
        self.assertIn('http_headers = { "X-Tenant" = "acme" }', copy_section)
        self.assertIn(
            'query_params = { "api-version" = "2025-04-01" }', copy_section
        )
        self.assertIn("stream_idle_timeout_ms = 45000", copy_section)
        self.assertIn('experimental_bearer_token = "sk-clone-key-789"', copy_section)
        self.assertNotIn('env_key = "TENANT_API_KEY"', copy_section)
        self.assertNotIn("requires_openai_auth", copy_section)

    def test_clone_creates_new_section_and_switches_default(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://openrouter.zjxqai.com/v1"\n'
            'wire_api = "responses"\n'
            "requires_openai_auth = true\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            result = clone_provider_with_bearer_key(
                "custom", "sk-clone-key-123", config_path=path
            )
            self.assertTrue(result["changed"])
            self.assertEqual(result["newProviderId"], "custom-copy")
            self.assertEqual(result["name"], "OpenAI（副本）")
            updated = path.read_text(encoding="utf-8")
            self.assertEqual(
                updated.splitlines()[0],
                'model_provider = "custom-copy"',
            )
            self.assertIn("[model_providers.custom-copy]", updated)
            self.assertIn('name = "OpenAI（副本）"', updated)
            self.assertIn(
                'experimental_bearer_token = "sk-clone-key-123"', updated
            )
            self.assertIn("[model_providers.custom]", updated)
            copy_section = updated.split("[model_providers.custom-copy]", 1)[1]
            self.assertNotIn("requires_openai_auth", copy_section)

    def test_clone_increments_suffix_on_collision(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://openrouter.zjxqai.com/v1"\n\n'
            "[model_providers.custom-copy]\n"
            'name = "OpenAI 旧副本"\n'
            'base_url = "https://openrouter.zjxqai.com/v1"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            result = clone_provider_with_bearer_key(
                "custom", "sk-clone-key-456", config_path=path
            )
            self.assertEqual(result["newProviderId"], "custom-copy2")
            updated = path.read_text(encoding="utf-8")
            self.assertIn("[model_providers.custom-copy2]", updated)
            self.assertIn(
                'experimental_bearer_token = "sk-clone-key-456"', updated
            )
            self.assertEqual(
                updated.splitlines()[0],
                'model_provider = "custom-copy2"',
            )

    def test_clone_rejects_missing_source_and_empty_key(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            'base_url = "https://openrouter.zjxqai.com/v1"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未在 config.toml 的 .*中定义"):
                clone_provider_with_bearer_key("missing", "sk-1", config_path=path)
            with self.assertRaisesRegex(ValueError, "API key 不能为空"):
                clone_provider_with_bearer_key("custom", "", config_path=path)
            self.assertEqual(path.read_text(encoding="utf-8"), config_text)


if __name__ == "__main__":
    unittest.main()
