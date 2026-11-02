"""Unit tests for GitHub Release updater helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.updater import (
    AutoUpdateManager,
    UpdateInfo,
    UpdateAsset,
    UpdateVerificationError,
    check_for_update,
    download_update_asset,
    format_update_info,
    installer_asset_name,
    is_newer_version,
    release_api_urls,
    select_windows_installer_asset,
    verify_update_asset,
    version_key,
)


class VersionComparisonTests(unittest.TestCase):
    def test_version_key_accepts_plain_and_tagged_versions(self) -> None:
        self.assertEqual(version_key("v1.0.0"), (1, 0, 0))
        self.assertEqual(version_key("1.2"), (1, 2, 0))

    def test_is_newer_version_uses_semver_order(self) -> None:
        self.assertTrue(is_newer_version("v1.0.1", "1.0.0"))
        self.assertFalse(is_newer_version("v1.0.0", "1.0.0"))
        self.assertFalse(is_newer_version("v0.3.0", "1.0.0"))


class UpdateAssetSelectionTests(unittest.TestCase):
    def test_installer_asset_name_matches_release_convention(self) -> None:
        self.assertEqual(
            installer_asset_name("1.0.0"),
            "codex-usage-hud-v1.0.0-windows-x64-setup.exe",
        )

    def test_select_windows_installer_prefers_exact_versioned_asset(self) -> None:
        asset = select_windows_installer_asset(
            [
                {
                    "name": "codex-usage-hud-v1.0.0-windows-x64.zip",
                    "browser_download_url": "https://example.test/archive.zip",
                },
                {
                    "name": "codex-usage-hud-v1.0.0-windows-x64-setup.exe",
                    "browser_download_url": "https://example.test/setup.exe",
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                },
            ],
            latest_version="v1.0.0",
        )

        self.assertIsNotNone(asset)
        self.assertEqual(asset.name, "codex-usage-hud-v1.0.0-windows-x64-setup.exe")
        self.assertEqual(asset.size, 123)
        self.assertEqual(asset.sha256, "a" * 64)

    def test_verify_update_asset_rejects_an_unexpected_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "installer.exe"
            installer.write_bytes(b"trusted payload")
            asset = UpdateAsset(
                name=installer.name,
                url="https://example.test/installer.exe",
                size=installer.stat().st_size,
                sha256="0" * 64,
            )

            with self.assertRaises(UpdateVerificationError):
                verify_update_asset(installer, asset)

    def test_format_update_info_mentions_installer_when_available(self) -> None:
        asset = select_windows_installer_asset(
            [
                {
                    "name": "codex-usage-hud-v1.0.1-windows-x64-setup.exe",
                    "browser_download_url": "https://example.test/setup.exe",
                }
            ],
            latest_version="v1.0.1",
        )

        text = format_update_info(
            UpdateInfo(
                current_version="1.0.0",
                latest_version="v1.0.1",
                available=True,
                asset=asset,
            )
        )

        self.assertIn("1.0.0 -> v1.0.1", text)
        self.assertIn("codex-usage-hud-v1.0.1-windows-x64-setup.exe", text)


class _FakeResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self._chunks = list(chunks)
        self.status = status
        self.headers = headers or {}
        self.delay_seconds = max(0.0, float(delay_seconds))

    def read(self, _size: int = -1) -> bytes:
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


class AutoUpdateManagerTests(unittest.TestCase):
    def _wait_for_phase(
        self,
        manager: AutoUpdateManager,
        phase: str,
        *,
        timeout_seconds: float = 2.0,
    ) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if manager.tick().phase == phase:
                return
            time.sleep(0.02)
        self.fail(f"timed out waiting for phase {phase}, last={manager.status().phase}")

    def _wait_for_progress(
        self,
        manager: AutoUpdateManager,
        *,
        timeout_seconds: float = 2.0,
    ) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            state = manager.tick()
            if state.phase == "downloading" and state.downloaded_bytes > 0:
                return
            time.sleep(0.02)
        self.fail("timed out waiting for download progress")

    def test_auto_update_manager_can_pause_resume_and_finish_download(self) -> None:
        requests: list[dict[str, str]] = []
        launched: list[Path] = []
        payload = bytes(range(256)) * 4
        payload_sha256 = hashlib.sha256(payload).hexdigest()

        def fake_check(**_kwargs: object) -> UpdateInfo:
            return UpdateInfo(
                current_version="1.0.0",
                latest_version="v1.0.1",
                available=True,
                asset=UpdateAsset(
                    name="codex-usage-hud-v1.0.1-windows-x64-setup.exe",
                    url="https://example.test/setup.exe",
                    size=len(payload),
                    sha256=payload_sha256,
                ),
                release_url="https://example.test/release",
            )

        def fake_open(request, timeout: float):
            del timeout
            requests.append({str(key).lower(): value for key, value in request.header_items()})
            if len(requests) == 1:
                return _FakeResponse(
                    [bytes([value]) for value in payload],
                    headers={"Content-Length": str(len(payload))},
                    delay_seconds=0.02,
                )
            range_header = str(requests[-1].get("range") or "")
            offset = int(range_header.split("=", 1)[1].split("-", 1)[0])
            return _FakeResponse(
                [payload[offset:]],
                status=206,
                headers={
                    "Content-Length": str(len(payload) - offset),
                    "Content-Range": f"bytes {offset}-{len(payload) - 1}/{len(payload)}",
                },
            )

        def fake_launch(path: Path) -> None:
            launched.append(path)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = AutoUpdateManager(
                current_version="1.0.0",
                check_interval_seconds=3600,
                retry_interval_seconds=60,
                download_dir=Path(tmp_dir),
                check_func=fake_check,
                download_opener=fake_open,
                launch_func=fake_launch,
            )
            try:
                self._wait_for_phase(manager, "downloading")
                self._wait_for_progress(manager)
                paused = manager.handle_click()
                self.assertEqual(paused.phase, "paused")
                self._wait_for_phase(manager, "paused")
                paused_bytes = manager.status().downloaded_bytes
                self.assertGreater(paused_bytes, 0)
                self.assertLess(paused_bytes, len(payload))
                self.assertEqual(requests[0].get("user-agent"), "codex-usage-hud-updater")

                deadline = time.time() + 1.0
                resumed = manager.status()
                while time.time() < deadline:
                    resumed = manager.handle_click()
                    if resumed.phase in {"downloading", "ready"}:
                        break
                    time.sleep(0.02)
                self.assertIn(resumed.phase, {"downloading", "ready"})
                self._wait_for_phase(manager, "ready")
                ready = manager.status()
                self.assertEqual(ready.asset_name, "codex-usage-hud-v1.0.1-windows-x64-setup.exe")
                self.assertEqual(ready.downloaded_bytes, len(payload))
                self.assertEqual(Path(ready.installer_path).read_bytes(), payload)
                resumed_offset = int(str(requests[1].get("range") or "bytes=0-").split("=", 1)[1].split("-", 1)[0])
                self.assertGreaterEqual(resumed_offset, paused_bytes)
                self.assertLess(resumed_offset, len(payload))

                launched_state = manager.handle_click()
                self.assertEqual(launched_state.phase, "ready")
                self.assertTrue(launched, launched_state)
                self.assertEqual(launched[0], Path(ready.installer_path))
            finally:
                manager.close()

    def test_manual_check_reports_available_without_starting_download(self) -> None:
        requests: list[dict[str, str]] = []

        def fake_check(**_kwargs: object) -> UpdateInfo:
            return UpdateInfo(
                current_version="1.0.0",
                latest_version="v1.0.1",
                available=True,
                asset=UpdateAsset(
                    name="codex-usage-hud-v1.0.1-windows-x64-setup.exe",
                    url="https://example.test/setup.exe",
                    size=10,
                    sha256=hashlib.sha256(b"1234567890").hexdigest(),
                ),
                release_url="https://example.test/release",
            )

        def fake_open(request, timeout: float):
            del timeout
            requests.append({str(key).lower(): value for key, value in request.header_items()})
            return _FakeResponse([b"123"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = AutoUpdateManager(
                current_version="1.0.0",
                check_interval_seconds=3600,
                retry_interval_seconds=60,
                download_dir=Path(tmp_dir),
                check_func=fake_check,
                download_opener=fake_open,
            )
            try:
                state = manager.request_check(auto_download=False)
                self.assertEqual(state.phase, "checking")
                self._wait_for_phase(manager, "available")
                available = manager.status()
                self.assertEqual(available.asset_name, "codex-usage-hud-v1.0.1-windows-x64-setup.exe")
                self.assertTrue(available.visible)
                self.assertEqual(requests, [])
            finally:
                manager.close()

    def test_request_install_downloads_and_launches_installer_async(self) -> None:
        launched: list[Path] = []
        payload = b"installer-payload"

        def fake_check(**_kwargs: object) -> UpdateInfo:
            return UpdateInfo(
                current_version="1.0.0",
                latest_version="v1.0.1",
                available=True,
                asset=UpdateAsset(
                    name="codex-usage-hud-v1.0.1-windows-x64-setup.exe",
                    url="https://example.test/setup.exe",
                    size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                ),
                release_url="https://example.test/release",
            )

        def fake_open(request, timeout: float):
            del request, timeout
            return _FakeResponse(
                [payload],
                headers={"Content-Length": str(len(payload))},
            )

        def fake_launch(path: Path) -> None:
            launched.append(path)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = AutoUpdateManager(
                current_version="1.0.0",
                check_interval_seconds=3600,
                retry_interval_seconds=60,
                download_dir=Path(tmp_dir),
                check_func=fake_check,
                download_opener=fake_open,
                launch_func=fake_launch,
            )
            try:
                state = manager.request_install()
                self.assertEqual(state.phase, "checking")
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    manager.tick()
                    if launched:
                        break
                    time.sleep(0.02)
                self.assertTrue(launched)
                ready = manager.status()
                self.assertEqual(ready.phase, "ready")
                self.assertIn("已启动", ready.message)
                self.assertEqual(Path(ready.installer_path).read_bytes(), payload)
            finally:
                manager.close()

    def test_auto_update_manager_falls_back_when_official_download_fails(self) -> None:
        payload = b"mirror verified installer"
        requests: list[str] = []

        def fake_check(**_kwargs: object) -> UpdateInfo:
            return UpdateInfo(
                current_version="1.0.0",
                latest_version="v1.0.1",
                available=True,
                asset=UpdateAsset(
                    name="codex-usage-hud-v1.0.1-windows-x64-setup.exe",
                    url="https://github.example/releases/setup.exe",
                    size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                ),
            )

        def fake_open(request, timeout: float):
            del timeout
            requests.append(request.full_url)
            if len(requests) == 1:
                raise OSError("official route unavailable")
            return _FakeResponse([payload], headers={"Content-Length": str(len(payload))})

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = AutoUpdateManager(
                current_version="1.0.0",
                check_interval_seconds=3600,
                retry_interval_seconds=60,
                download_dir=Path(tmp_dir),
                check_func=fake_check,
                download_opener=fake_open,
            )
            try:
                manager.request_check(auto_download=True)
                self._wait_for_phase(manager, "ready")
                state = manager.status()
                self.assertTrue(state.verified)
                self.assertEqual(Path(state.installer_path).read_bytes(), payload)
                self.assertEqual(requests[0], "https://github.example/releases/setup.exe")
                self.assertIn("ghproxy.net", requests[1])
            finally:
                manager.close()

    def test_download_update_asset_falls_back_to_a_mirror_and_publishes_only_verified_file(self) -> None:
        payload = b"verified installer"
        requests: list[str] = []
        asset = UpdateAsset(
            name="codex-usage-hud-v1.0.1-windows-x64-setup.exe",
            url="https://github.example/releases/setup.exe",
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        info = UpdateInfo(
            current_version="1.0.0",
            latest_version="v1.0.1",
            available=True,
            asset=asset,
        )

        def fake_open(request, timeout: float):
            del timeout
            requests.append(request.full_url)
            if len(requests) == 1:
                raise OSError("official route unavailable")
            return _FakeResponse([payload], headers={"Content-Length": str(len(payload))})

        with tempfile.TemporaryDirectory() as temp_dir:
            installer = download_update_asset(
                info,
                destination_dir=Path(temp_dir),
                opener=fake_open,
            )
            self.assertEqual(installer.read_bytes(), payload)
            self.assertFalse(installer.with_name(f"{installer.name}.part").exists())
            self.assertEqual(requests[0], asset.url)
            self.assertIn("ghproxy.net", requests[1])

    def test_download_update_asset_switches_source_when_first_source_is_too_slow(self) -> None:
        from unittest.mock import patch

        payload = b"slow source then fast source"
        requests: list[str] = []
        asset = UpdateAsset(
            name="codex-usage-hud-v1.0.1-windows-x64-setup.exe",
            url="https://github.example/releases/setup.exe",
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        info = UpdateInfo(current_version="1.0.0", latest_version="v1.0.1", available=True, asset=asset)

        def fake_open(request, timeout: float):
            del timeout
            requests.append(request.full_url)
            if len(requests) == 1:
                return _FakeResponse([payload[:1]], headers={"Content-Length": "1"}, delay_seconds=0.02)
            return _FakeResponse([payload], headers={"Content-Length": str(len(payload))})

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "codex_usage_hud.updater.DEFAULT_UPDATE_LOW_SPEED_GRACE_SECONDS", 0.01
        ), patch("codex_usage_hud.updater.DEFAULT_UPDATE_LOW_SPEED_BYTES_PER_SECOND", 10_000):
            installer = download_update_asset(info, destination_dir=Path(temp_dir), opener=fake_open)
            self.assertEqual(installer.read_bytes(), payload)
        self.assertGreaterEqual(len(requests), 2)
        self.assertIn("ghproxy.net", requests[1])

    def test_check_worker_handles_unexpected_exception_without_leaving_checking_phase(self) -> None:
        """check_func 若抛出未捕获异常，_check_worker 必须兜底更新为 error
        状态并释放 check_thread，否则 phase 永远卡在 checking、loading 弹窗
        永不消失。"""

        def exploding_check(**_kwargs: object) -> UpdateInfo:
            raise RuntimeError("simulated unexpected failure in check_func")

        manager = AutoUpdateManager(
            current_version="1.0.0",
            check_interval_seconds=3600,
            retry_interval_seconds=60,
            check_func=exploding_check,
        )
        try:
            manager.request_check(auto_download=False)
            self._wait_for_phase(manager, "error")
            state = manager.status()
            self.assertEqual(state.phase, "error")
            self.assertIn("simulated unexpected failure", state.error)
            self.assertIn("检查更新失败", state.message)
            # check_thread 已释放，不会永远卡在 checking
            self.assertIsNone(manager._check_thread)
        finally:
            manager.close()

    def test_check_worker_invokes_on_state_change_callback_when_finished(self) -> None:
        """后台检查完成后必须调用 on_state_change 回调，否则事件循环
        不会被唤醒、payload 不刷新、loading 弹窗永远不消失。"""
        notifications: list[AutoUpdateState] = []

        def fake_check(**_kwargs: object) -> UpdateInfo:
            return UpdateInfo(current_version="1.0.0", latest_version="1.0.0")

        manager = AutoUpdateManager(
            current_version="1.0.0",
            check_interval_seconds=3600,
            retry_interval_seconds=60,
            check_func=fake_check,
            on_state_change=lambda state: notifications.append(state),
        )
        try:
            manager.request_check(auto_download=False)
            self._wait_for_phase(manager, "up_to_date")
            self.assertTrue(notifications, "on_state_change 回调未被调用")
            self.assertEqual(notifications[-1].phase, "up_to_date")
        finally:
            manager.close()


class UpdateCheckMirrorTests(unittest.TestCase):
    """GitHub API 多源（官方+镜像）检查更新的回归测试。"""

    def test_release_api_urls_includes_official_then_mirror(self) -> None:
        urls = release_api_urls("owner/repo")
        self.assertEqual(
            urls[0],
            "https://api.github.com/repos/owner/repo/releases/latest",
        )
        self.assertEqual(len(urls), 2)
        self.assertIn("gh-proxy.com", urls[1])
        self.assertIn("api.github.com", urls[1])

    def test_release_api_urls_deduplicates_identical_urls(self) -> None:
        urls = release_api_urls(
            "owner/repo",
            mirror_url_templates=("https://gh-proxy.com/{url}",),
        )
        self.assertEqual(len(urls), 2)
        self.assertEqual(len(set(urls)), 2)

    def test_check_for_update_falls_back_to_mirror_when_official_fails(self) -> None:
        from unittest.mock import patch

        mirror_payload = {
            "tag_name": "v1.0.1",
            "html_url": "https://github.com/owner/repo/releases/tag/v1.0.1",
            "assets": [],
        }
        call_urls: list[str] = []

        def fake_json_request(url: str, *, timeout_seconds: float):
            del timeout_seconds
            call_urls.append(url)
            if len(call_urls) == 1:
                raise OSError("official route unavailable")
            return mirror_payload

        with patch(
            "codex_usage_hud.updater._json_request",
            side_effect=fake_json_request,
        ):
            info = check_for_update(current_version="1.0.0")

        self.assertEqual(info.latest_version, "v1.0.1")
        self.assertFalse(info.error)
        self.assertEqual(len(call_urls), 2)
        self.assertIn("api.github.com", call_urls[0])
        self.assertIn("gh-proxy.com", call_urls[1])

    def test_check_for_update_returns_friendly_error_when_all_sources_fail(self) -> None:
        from unittest.mock import patch

        def fake_json_request(url: str, *, timeout_seconds: float):
            del url, timeout_seconds
            raise OSError("connection timed out")

        with patch(
            "codex_usage_hud.updater._json_request",
            side_effect=fake_json_request,
        ):
            info = check_for_update(current_version="1.0.0")

        self.assertTrue(info.error)
        self.assertIn("无法连接 GitHub", info.error)
        self.assertIn("connection timed out", info.error)

    def test_check_for_update_uses_official_when_it_succeeds(self) -> None:
        from unittest.mock import patch

        official_payload = {
            "tag_name": "v1.0.0",
            "html_url": "https://github.com/owner/repo/releases/tag/v1.0.0",
            "assets": [],
        }
        call_count = [0]

        def fake_json_request(url: str, *, timeout_seconds: float):
            del url, timeout_seconds
            call_count[0] += 1
            return official_payload

        with patch(
            "codex_usage_hud.updater._json_request",
            side_effect=fake_json_request,
        ):
            info = check_for_update(current_version="1.0.0")

        self.assertEqual(info.latest_version, "v1.0.0")
        self.assertFalse(info.error)
        self.assertEqual(call_count[0], 1, "官方成功时不应尝试镜像")


if __name__ == "__main__":
    unittest.main()
