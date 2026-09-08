"""Unit tests for the renderer-injected HUD payload and CDP client."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import queue
import sys
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, MagicMock, call, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.parser import (
    Activity,
    ConfirmedTokens,
    GapTiming,
    ParsedSession,
    RequestRound,
    RequestTokens,
    SlowSummary,
    TaskHistory,
    ToolCallTiming,
)
from codex_usage_hud.core.runtime_errors import RuntimeErrorEvent
from codex_usage_hud.platforms.codex_theme import CodexThemeSnapshot
from codex_usage_hud.support_assets import support_qr_asset_paths, support_qr_payload
from codex_usage_hud import renderer_client as renderer_hud
from codex_usage_hud.renderer_cdp import bindings as renderer_bindings
from codex_usage_hud.renderer_client import RendererHudClient
from codex_usage_hud.renderer_payload_builder import (
    _renderer_theme_payload,
    payload_from_snapshot,
)


class RendererHudPayloadTests(unittest.TestCase):
    def test_partial_budget_cost_is_not_presented_as_zero_or_complete(self) -> None:
        snapshot = ParsedSession(status="parsed")
        snapshot.today_tokens = 1200
        snapshot.today_cost_usd = None
        snapshot.week_tokens = 4200
        snapshot.week_cost_usd = None

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertIn("今日 1,200/不可用", payload["topLine"])
        self.assertIn("本周 4,200/不可用", payload["topLine"])
        self.assertEqual(payload["topProgress"]["budget"][0]["label"], "今日 1,200/不可用")
        self.assertEqual(payload["topProgress"]["budget"][1]["label"], "本周 4,200/不可用")
        self.assertEqual(payload["topProgress"]["budget"][0].get("rightText", ""), "")
        self.assertIn("价格不可用", payload["topLine"])
        self.assertNotIn("今日 1,200/$0", payload["topLine"])
        self.assertNotIn("本周 4,200/$0", payload["topLine"])

    def test_payload_uses_the_followed_session_provider(self) -> None:
        snapshot = ParsedSession(
            session_id="custom-session",
            model_provider="custom",
            selection_source="renderer:Custom session",
        )

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["activeSessionProvider"], "custom")
        self.assertEqual(
            payload["payloadDomains"]["sessionSwitch"]["activeSessionProvider"],
            "custom",
        )

    def test_renderer_theme_payload_accepts_persisted_source(self) -> None:
        snapshot = CodexThemeSnapshot.from_probe_result(
            {
                "mode": "dark",
                "effectiveVariant": "dark",
                "darkCodeThemeId": "linear",
                "darkTheme": {
                    "accent": "#5e6ad2",
                    "contrast": 60,
                    "fonts": {"code": None, "ui": "Inter"},
                    "ink": "#e3e4e6",
                    "opaqueWindows": True,
                    "semanticColors": {
                        "diffAdded": "#69c967",
                        "diffRemoved": "#ff7e78",
                        "skill": "#c2a1ff",
                    },
                    "surface": "#0f0f11",
                },
            },
            source="persisted",
        )

        assert snapshot is not None
        payload = _renderer_theme_payload(snapshot)

        self.assertEqual(payload["source"], "persisted")
        self.assertEqual(payload["variant"], "dark")
        self.assertEqual(payload["tokens"]["accent"], "#5e6ad2")
        self.assertEqual(payload["effectiveTheme"]["codeThemeId"], "linear")

    def test_payload_from_snapshot_formats_compact_hud_lines(self) -> None:
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            selection_source="cdp:Live Renderer Thread",
            refreshed_at=datetime(2026, 6, 5, 13, 10, 11).astimezone(),
            last_event_time=datetime(2026, 6, 5, 13, 10, 1).astimezone(),
            task_index=3,
            task_count=3,
            confirmed=ConfirmedTokens(
                cumulative_total=12345,
                cumulative_input=10000,
                cumulative_cached=6000,
                cumulative_output=2345,
                cumulative_cost_usd=0.1234,
            ),
            request=RequestTokens(
                status="running",
                model="gpt-5.5",
                input_tokens=1200,
                cached_tokens=800,
                output_tokens=90,
                reasoning_tokens=10,
                total_tokens=1300,
                estimated=True,
                cost_usd=0.0123,
            ),
        )
        snapshot.today_tokens = 50000
        snapshot.today_cost_usd = 0.5
        snapshot.week_tokens = 200000
        snapshot.week_cost_usd = 1.5

        payload = payload_from_snapshot(
            snapshot,
            theme={
                "variant": "dark",
                "source": "cdp",
                "tokens": {
                    "surface": "#181818",
                    "panelSurface": "#202020",
                    "panelBorder": "#3a485a",
                    "headerSurface": "#202833",
                    "divider": "#273241",
                    "text": "#e8eef7",
                    "muted": "#8492a6",
                    "accent": "#339cff",
                    "info": "#ad7bf9",
                    "warning": "#ffb86b",
                    "error": "#fa423e",
                    "success": "#40c977",
                    "requestSurface": "#151515",
                    "requestHeaderSurface": "#202020",
                    "requestPanelSurface": "#171717",
                    "requestText": "#f0f0f0",
                    "requestMuted": "#808080",
                    "progressTrack": "#111822",
                    "progressTrackBorder": "#314052",
                    "progressTrackText": "#657589",
                    "progressCache": "#5d8bff",
                    "progressCacheEnd": "#7d6dff",
                    "progressCacheText": "#07131f",
                    "progressDay": "#339cff",
                    "progressDayEnd": "#5d8bff",
                    "progressDayText": "#07131f",
                    "progressWeek": "#6ea8ff",
                    "progressWeekEnd": "#8db7ff",
                    "progressWeekText": "#07131f",
                    "progressOverflow": "#fa423e",
                    "progressOverflowHighlight": "#ffb5b2",
                    "progressOverflowAnchor": "#ff7a77",
                    "progressOverflowAnchorEdge": "#ffc3a4",
                    "progressOverflowBadge": "#5e2424",
                    "progressOverflowBadgeEdge": "#fa423e",
                    "progressOverflowBadgeText": "#ffe0de",
                },
            },
            update_state={
                "visible": True,
                "phase": "downloading",
                "icon": "download",
                "title": "正在下载更新：50%",
            },
        ).to_json()

        top_line = str(payload["topLine"])
        self.assertNotIn("Live Renderer Thread", str(payload["topLine"]))
        self.assertTrue(top_line.startswith("本会话 14k/"))
        self.assertIn("本会话 14k/$0.123/", top_line)
        self.assertNotIn("$0.136", top_line)
        self.assertIn("/~61% | 今日", top_line)
        self.assertIn("今日 50k/$0.500", top_line)
        self.assertIn("本周 200k/$1.50", top_line)
        top_progress = payload["topProgress"]
        self.assertIsInstance(top_progress, dict)
        collapsed_progress = top_progress["collapsed"]
        self.assertEqual([item["tone"] for item in collapsed_progress], ["session", "day", "week"])
        self.assertTrue(collapsed_progress[0]["label"].startswith("本会话 14k/"))
        self.assertIn("/~61%", collapsed_progress[0]["label"])
        self.assertNotIn("rightText", collapsed_progress[0])
        self.assertEqual(collapsed_progress[1]["rightText"], "总 $100.00")
        self.assertEqual(collapsed_progress[2]["rightText"], "总 $400.00")
        self.assertEqual(top_progress["cache"]["label"], "缓存命中 ~61%")
        self.assertEqual(top_progress["budget"][0]["label"], "今日 50k/$0.500")
        self.assertEqual(top_progress["budget"][0]["rightText"], "总 $100.00")
        self.assertNotIn("overflowRatio", top_progress["budget"][0])
        self.assertNotIn("overflowBadge", top_progress["budget"][0])
        self.assertEqual(top_progress["budget"][1]["label"], "本周 200k/$1.50")
        self.assertEqual(top_progress["budget"][1]["rightText"], "总 $400.00")
        request_line = str(payload["requestLine"])
        self.assertIn("↑~1,200", request_line)
        self.assertIn("◎~61%", request_line)
        self.assertIn("∑1,290", request_line)
        self.assertLess(request_line.index("↑~1,200"), request_line.index("◎~61%"))
        self.assertLess(request_line.index("◎~61%"), request_line.index("↓~90"))
        self.assertLess(request_line.index("↻~800"), request_line.index("∑1,290"))
        self.assertTrue(request_line.endswith("↻~800 ∑1,290"))
        self.assertEqual(payload["model"], "gpt-5.5")
        self.assertEqual(payload["observedModels"], ["gpt-5.5"])
        self.assertFalse(payload["warning"])
        self.assertIn("topDetails", payload)
        self.assertIn("topCopies", payload)
        self.assertIn("requestRows", payload)
        self.assertIn("requestRowDetails", payload)
        self.assertEqual(payload["requestRowsTotal"], 1)
        self.assertIn("settings", payload)
        self.assertIn("activeDisplayMode", payload)
        self.assertIn("settingsPath", payload)
        self.assertIn("settingsBridgeUrl", payload)
        self.assertIn("settingsCommandStatus", payload)
        self.assertIn("workOverlaySelectableMax", payload)
        self.assertIn("desktopOverlayDependency", payload)
        self.assertIn("theme", payload)
        self.assertEqual(payload["theme"]["variant"], "dark")
        self.assertEqual(payload["theme"]["tokens"]["accent"], "#339cff")
        self.assertIn("updateState", payload)
        self.assertEqual(payload["appVersion"], "1.2.0")
        self.assertIn("本会话用量", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("实时请求", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("符号说明", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("只看剩余", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("applySettingsPayload(root", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("--codex-usage-hud-surface", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-update-button", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="update-action"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-settings-button", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="settings-open"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-resize-edge-left", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-resize-corner-bottom-right", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-overflow", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-badge", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertRegex(
            renderer_hud.RENDERER_HUD_SCRIPT,
            r"\.codex-usage-hud-progress-track-text \{[^}]*z-index: 6;",
        )
        self.assertIn("function normalizePayloadDomains", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("applyPayloadDomains(root, nextPayload, renderedDomains)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('if ("currentSession" in domains)', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('if ("diagnostics" in domains)', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("Object.assign(nextPayload, domainPayload);", renderer_hud.RENDERER_HUD_SCRIPT)

        self.assertIn("codex-usage-hud-resize-corner-top-left", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-edge="left"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-edge="bottom-right"', renderer_hud.RENDERER_HUD_SCRIPT)

        self.assertIn('data-edge="top-left"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('class="codex-usage-hud-resize"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("codex-usage-hud-settings-menu", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="settings-menu"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="coffee-open"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertGreaterEqual(
            renderer_hud.RENDERER_HUD_SCRIPT.count(
                'class="codex-usage-hud-panel-header" data-action="toggle"'
            ),
            2,
        )
        self.assertIn("请作者喝咖啡", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-fetch-prices", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-price-advanced", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-pricing-apply-all="true"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("应用于所有 providers", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("applyPricingToAllProviders", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("updatePricingApplyAllPreview", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="settings-sync-provider-prices"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("syncCurrentProviderPricesToOthers", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("同步当前 Provider 的模型单价到其它 Provider", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("保存后生效", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("repeat(3, 30px)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-pricing-change-summary="true"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-pricing-apply-all", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("margin-inline: 18px;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("当前生效价格版本", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-price-field="provider"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-price-field="base_url"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-price-field="cache_write"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("缓存写入", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-price-field="reasoning"', renderer_hud.RENDERER_HUD_SCRIPT)
        for action in (
            "pricing-export",
            "pricing-open",
            "pricing-open-cancel",
            "pricing-import-open",
            "pricing-effective-confirm",
            "pricing-import-preview",
            "pricing-import-commit",
        ):
            self.assertIn(f'data-action="{action}"', renderer_hud.RENDERER_HUD_SCRIPT)
        script = renderer_hud.RENDERER_HUD_SCRIPT
        self.assertLess(
            script.index('data-action="settings-fetch-prices"'),
            script.index('data-action="pricing-export"'),
        )
        self.assertLess(
            script.index('data-action="pricing-export"'),
            script.index('data-action="pricing-import-open"'),
        )
        self.assertIn("codex-usage-hud-pricing-icon-action", script)
        self.assertIn("⇩", script)
        self.assertIn("⇧", script)
        self.assertNotIn('data-action="pricing-template"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="pricing-copy-example"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="pricing-recalculate-open"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="pricing-recalc-preview"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="pricing-recalc-execute"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("pricingPath", renderer_hud.RENDERER_HUD_SCRIPT)

        self.assertIn("pricingUsedTemplate", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("pricingDownload", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("applyPricingCommandStatus", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-pricing-import-file="true"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("unknownPriceModels", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-add-detected-model", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="settings-exit"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="settings-exit-confirm"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-setting-key="stop_hud_on_lock_screen"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("锁屏后完全退出HUD进程", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertLess(
            renderer_hud.RENDERER_HUD_SCRIPT.index('data-action="settings-exit"'),
            renderer_hud.RENDERER_HUD_SCRIPT.index('data-setting-key="stop_hud_on_lock_screen"'),
        )
        self.assertIn("退出 HUD", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("版本更新", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-check-update", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("__codexUsageHudStaleTimer", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("payloadNeedsStaleGuard", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("数据可能不是最新", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("HUD 更新暂停", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("后端未继续刷新", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("正在显示旧数据", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-install-update", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-restart", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("settings-apply-display-mode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("HUD 显示方案", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-setting-key="display_mode"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('option value="renderer"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("Renderer -> Qt -> Tk", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('option value="qt"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('option value="tk"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("Qt 独立窗口", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("Tk 独立窗口", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-settings-loading-track", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("openSettingsLoading", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("settingsCommandKey", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("codexUsageHudSettingsCommand:v1", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("localStorage.setItem(settingsCommandKey", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("请作者喝咖啡链接", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-setting-key="support_url"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-setting-key="work_overlay_max_items"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("会话进度气泡数量", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("0 为关闭", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("气泡运行环境", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-settings-compact-inputs", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("<span>位置</span>", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('aria-label="气泡位置"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("方形进度气泡", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("圆形总结", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("未安装", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("立即安装", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("启用气泡", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("需要安装环境", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("已安装，启用气泡", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-install-desktop-overlay", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-enable-desktop-overlay", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("desktopOverlayDependency", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("workOverlaySelectableMax", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("拉取价格", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("window.confirm", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("fetch(`${bridge}/command`", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("fetch(`${bridge}/active-session`", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function readActiveSessionRef()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activeSessionHistoryPatchName", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activeSessionCanonicalIdName", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activeSessionSettledTimerName", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('postActiveSession("settled")', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("transientWithoutCanonicalId", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("rendererSessionId", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activeSessionIdIsProvisional(rawRendererSessionId)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("[data-app-action-sidebar-thread-id]", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activeSessionRowSelector", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("a[href*='thread']", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("[role='button']", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('postActiveSession("click"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            "function activeSessionNodeOwnedByHud(node)",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn(
            "function activeSessionFirstOutsideHud(selector)",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn(
            "if (activeSessionNodeOwnedByHud(event.target)) return;",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn(
            ".filter((row) => !activeSessionNodeOwnedByHud(row))",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("activeSessionSelectionSeqName", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activeSessionAppliedSeqName", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("previousActiveSessionSelectionSeq", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("previousActiveSessionAppliedSeq", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function showActiveSessionFollowFeedback", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("会话切换中：正在读取会话数据", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("renderer 事件通道不可用", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("rendererSessionId: rawRendererSessionId", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("selectionSeq", renderer_hud.RENDERER_HUD_SCRIPT)
        binding_send = renderer_hud.RENDERER_HUD_SCRIPT.index(
            "ctx.bindings.send(activeSessionBindingName, payload);"
        )
        delivered_signature = renderer_hud.RENDERER_HUD_SCRIPT.index(
            "window[activeSessionLastSignatureName] = signature;",
            binding_send,
        )
        self.assertLess(binding_send, delivered_signature)
        self.assertIn("newSession", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("reason === \"new-session\"", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("if (!ref.sessionId && !ref.title) return;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            "ref.rendererSessionId || ref.rawSessionId || ref.sessionId",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("codex-usage-hud-support-qr-grid", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-support-qr-title", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("previousPayload.supportImages?.length", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settingsBridgeUrl", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("updateState", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("renderUpdateButtons", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const submitted = submitSettingsCommand", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codexUsageHudSettingsCommand", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function patchCodexModelPicker()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("data-codex-usage-hud-model-option", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("reasoningEffortLabel", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("ultra", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("ctx.bindings.available(settingsCommandBindingName)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("ctx.bindings.send(settingsCommandBindingName, payload)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('setSettingsStatus(state.message || state.title || "", state.error ? "error" : "")', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("window.__codexUsageHudRemove()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("expiresAt: Date.now() + 10000", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("navigator.clipboard", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("requestRowDetails", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("header.app-header-tint", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("app-shell-header-context-menu-surface", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("topTitlebarSlot", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("headerLayoutSignature", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("characterData: true", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("topSlotCache = null", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('window[scrollHandlerName] = () => scheduleForPanels(["request"])', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("observe(document.documentElement", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function refreshLayoutObservers()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("window[mutationObserverName].observe(headerNode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("window[mutationObserverName].observe(composerNode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('window[resizeObserverName] = { disconnect() {} };', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function startBootstrapObserver()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("window[bootstrapObserverName].observe(document.body", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('"layout_bootstrap"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-startup-bubble", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('root.dataset.hudReady = "false";', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function renderStartupBubble(root, startup)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-startup-progress-track", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-field="startupProgressFill"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const retainedDomains = { ...previousDomains, ...domains };", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('previousState.hydrated === true', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('"currentSession" in domains && "budget" in domains', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const renderedDomains = root === previousRoot ? domains : retainedDomains;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("applyPayloadDomains(root, nextPayload, renderedDomains);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('if ("sessionSwitch" in domains && wasReady)', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("invalidateHeaderAnchor();", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("invalidateComposerAnchor();", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            'scheduleForPanels(Object.keys(PANEL), { invalidateTop: true, forceAutoFit: true });',
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn(
            'syncPositionSettled(Object.keys(PANEL), { forceAutoFit: true });',
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("if (state.manual && !forceAutoFit)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function positionStartupBubble", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("top: 72px;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("bottom: auto;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const root = document.getElementById(rootId);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            'else if (!wasReady && "startup" in domains) root.dataset.hudReady = "false";',
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("const ref = readActiveSessionRef();", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("Return the same renderer-observed reference", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn(
            "else {\n      ensureRoot();\n      syncPosition();\n      refreshLayoutObservers();\n    }",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("cachedHeaderNode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("cachedComposerNode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function scheduleRequestAfterComposerSettles()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("handleLayoutMutations", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("layoutMutationTouchesTextInput", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("pointer-events: none;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function headerLeftControlEdge(headerNode, header", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function headerRightControlStart(headerNode, header", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function footerProtectedControlRects(composerNode, composer)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function footerControlIdentity(node)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function isBackgroundInfoControl(node)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('node.querySelector?.("svg > title, title")?.textContent', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("if (!(node instanceof Element) || !node.isConnected) return false;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const footerBandTop = Math.max(composer.top, composer.bottom - 96);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("The real Desktop composer commonly spans almost the whole content", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('const widthOverride = forceAutoFit || name === "request"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('"svg",', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("semantic: isBackgroundInfoControl(node),", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("rect.right > composer.left - 2", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("rect.left < composer.right + 2", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("[data-codex-intelligence-trigger]", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("[role='img'][aria-label*='上下文']", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("ring is a role=img span", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("rect.protectedFooterControl === true", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const safetyPadding = protectedControl ? 14 : padding;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("previous.left = Math.min(previous.left, left);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const best = rankedGaps.find((gap) => gap.width >= minWidth) || rankedGaps[0];", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const autoFitWidth = maxWidth;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("hasBackgroundInfoControl: controls.some((rect) => rect.backgroundInfoControl === true)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("the usable gap already excludes the background-information icon", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("const iconShift = widthOverride == null && slot.hasBackgroundInfoControl ? 25 : 0;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const top = clamp(composer.bottom - height - 4", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("Only protect external nodes when they are the known native", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("const rowCenter = slot.rowTop + (slot.rowHeight / 2);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const widthLimit = widthOverride == null ? autoFitWidth : maxWidth;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("syncPosition([gesture.name]);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("desiredWidth(name, {}, gesture.expanded, gesture.width", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function headerTitleScopeSelector()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function mutationTouchesHeaderTitleScope(mutation)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('const titleButton = element.closest?.("button");', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("titleButton?.matches?.(selector)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function mutationTouchesComposerModeControl(mutation)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            "if (!layoutMutationTouchesTextInput(mutation) && mutationTouchesComposerModeControl(mutation))",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn(
            'scheduleForPanels(["top"], { invalidateTop: true });',
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn(
            'scheduleForPanels(["request"]);',
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("chat actions", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("open in", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-line-inner", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-marquee", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-strip", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-strip-viewport", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-strip-marquee", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-size-probe", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            "textNode.textContent = fullText;",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn(
            'rail.appendChild(progressTextLayer("codex-usage-hud-progress-track-text"));',
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertNotIn(
            "codex-usage-hud-progress-fill-text",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("codex-usage-hud-progress-text", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("codex-usage-hud-progress-total", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("codex-usage-hud-progress-label", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("mix-blend-mode: difference;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function refreshCollapsedProgressStrip(node)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const collapsedTailPeekWidth = 40;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("tailShare > collapsedTailPeekWidth", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            "grid-template-columns: max-content minmax(0, 1fr) minmax(0, 1fr);",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("codex-usage-hud-budget-rails", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("container-type: inline-size;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("@container (max-width: 560px)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("@container (max-width: 440px)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-activity-timeline", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="activity-task-prev"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="activity-task-next"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-field="topActivityTaskOrdinal"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function selectActivityTask", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activityTaskNavigable", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activityTaskPinned", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            "function locateActivityTrailRound(root, taskIndex, roundIndex)",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("[data-content-search-turn-key]", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("[data-content-search-unit-key]", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("[data-app-action-timeline-scroll]", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activityTaskTurnId", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("node.scrollTop = offset > 0 ? -offset : 0;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-locate-highlight="true"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            'async function scrollToActivityRound(\n'
            "    copyText,\n"
            "    taskPrompt,\n"
            '    roundIndex = 0,\n'
            '    taskTurnId = "",\n'
            "    locateTexts = [],\n"
            "  )",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn('data-field="topActivityLoadMore"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("list.appendChild(button)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("height: calc(34px * 4 + 7px * 3);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("grid-auto-rows: minmax(34px, auto);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("bottom: -15px;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("list.dataset.context !== context", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const previousScrollTop = list.scrollTop || 0;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("if (!contextChanged)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("list.dataset.signature !== signature", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("grid-template-rows: repeat(3, minmax(32px, 1fr));", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("暂无会话高消耗轮次", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertLess(
            renderer_hud.RENDERER_HUD_SCRIPT.index('data-field="topTaskOrdinalSession"'),
            renderer_hud.RENDERER_HUD_SCRIPT.index("当前活动"),
        )
        self.assertGreater(
            renderer_hud.RENDERER_HUD_SCRIPT.index('data-field="topTaskOrdinalActivity"'),
            renderer_hud.RENDERER_HUD_SCRIPT.index("当前活动"),
        )
        self.assertIn("codex-usage-hud-copy-chip", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("正在执行", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("当前需求", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("interpolateNumericText", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("canAnimateNumericText", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("FileEditViewWindowHelp", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("-webkit-app-region: no-drag", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("headerTitleTextEdge", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("headerRightControlStart", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("manualTopRect", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("footerGapSlot", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("Automatic top HUD geometry owns the whole title-bar slot", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("widthOverride == null ? slot.width : widthOverride", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codexUsageHudPanelState:v5", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertLess(
            renderer_hud.RENDERER_HUD_SCRIPT.index(
                '<div class="codex-usage-hud-request-list" data-field="requestRows"></div>'
            ),
            renderer_hud.RENDERER_HUD_SCRIPT.index(
                '<div class="codex-usage-hud-panel-header" data-action="toggle">',
                renderer_hud.RENDERER_HUD_SCRIPT.index("function requestExpandedMarkup()"),
            ),
        )
        self.assertNotIn("Σ", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="reset"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("↯", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('"main header"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertTrue(payload["updateState"]["visible"])
        self.assertEqual(payload["updateState"]["phase"], "downloading")
        top_details = payload["topDetails"]
        self.assertIsInstance(top_details, dict)
        self.assertEqual(top_details["title"], "Live Renderer Thread")
        self.assertEqual(top_details["sessionCost"], "$0.123")
        self.assertEqual(top_details["sessionTokens"], "12k")
        self.assertEqual(top_details["taskOrdinal"], "第3次需求")
        self.assertEqual(top_details["taskOrdinalSession"], "")
        self.assertEqual(top_details["taskOrdinalActivity"], "第3次需求")
        self.assertEqual(top_details["sessionMix"], "缓存命中 ~61%")
        self.assertEqual(top_details["sessionAverage"], "均值 n/a")
        self.assertIn("↑↻ $", top_details["sessionComposition"])
        self.assertNotIn("=", top_details["sessionComposition"])
        self.assertNotIn("\n", top_details["sessionComposition"])
        self.assertFalse(str(top_details["sessionComposition"]).startswith("$"))
        self.assertIn("heavyRounds", top_details)
        self.assertEqual(top_details["activityLastLabel"], "需求轮次")
        self.assertEqual(top_details["activityLast"], "1轮")
        self.assertEqual(top_details["sessionInputTokens"], "10k")
        self.assertEqual(top_details["sessionCachedTokens"], "6,000")
        self.assertEqual(top_details["sessionOutputTokens"], "2,345")
        self.assertEqual(top_details["sessionReasoningTokens"], "0")
        self.assertEqual(top_details["warnings"], "")
        self.assertEqual(top_details["currentTask"], "Live Renderer Thread")
        self.assertIsInstance(top_details["activityTrail"], list)
        self.assertTrue(top_details["activityTrail"])
        self.assertNotIn("budget", top_details)
        self.assertNotIn("legend", top_details)
        self.assertNotIn("requestTokens", top_details)
        self.assertNotIn("requestCost", top_details)
        self.assertTrue(payload["requestRows"])
        request_row = str(payload["requestRows"][0])
        self.assertLess(request_row.index("↑400"), request_row.index("◎~67%"))
        self.assertLess(request_row.index("↻800"), request_row.index("∑1,290"))
        request_row_details = payload["requestRowDetails"]
        self.assertIsInstance(request_row_details, list)
        self.assertEqual(request_row, request_row_details[0]["text"])

    def test_settings_auto_save_keeps_model_price_drafts_pending_for_apply(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('rootScope.listen(root, "focusout", (event) => {', script)
        self.assertIn('const settingControl = event.target?.closest?.(\'[data-setting-key]\');', script)
        self.assertIn('settingControl instanceof HTMLSelectElement', script)
        self.assertIn("codex-usage-hud-rest-reminder-card", script)
        self.assertNotIn('autoSaveNonPricingSettingsFromModal({ section: "restReminder" })', script)
        self.assertIn("function settingsWithPersistedPriceTables(candidate, base = hudSettingsFromPayload())", script)
        self.assertIn("const settings = settingsWithPersistedPriceTables(collectSettingsForm());", script)
        self.assertIn('section === "restReminder"\n          ? settingsWithPersistedPriceTables(collectSettingsForm())', script)
        self.assertIn("if (submitted && !section)", script)
        self.assertIn("const addedCustomModelPrice = addCustomModelPriceToDraft(", script)
        self.assertIn("if (addedCustomModelPrice) settingsDirtyProviders.add(provider);", script)
        self.assertIn("model_prices: base?.model_prices", script)
        self.assertIn('data-action="settings-pricing-apply"', script)
        self.assertIn("function applyModelPricesFromModal()", script)
        self.assertIn("function syncPricingApplyDirtyState()", script)
        self.assertIn('data-pricing-apply-dirty-dot="true"', script)
        self.assertIn("syncPricingApplyDirtyState();", script)
        self.assertLess(
            script.index('data-action="settings-delete-provider"'),
            script.index('data-action="settings-pricing-apply"'),
        )
        self.assertNotIn('data-action="settings-save"', script)
        self.assertNotIn('data-action="settings-export"', script)
        self.assertIn('data-action="rest-reminder-save"', script)

    def test_request_rows_can_expand_beyond_the_default_page(self) -> None:
        snapshot = ParsedSession(status="parsed")
        snapshot.request_history = [
            RequestRound(
                index=index,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=100,
                cached_tokens=20,
                output_tokens=10,
                reasoning_tokens=0,
                total_tokens=110,
                estimated=False,
            )
            for index in range(1, 32)
        ]

        payload = payload_from_snapshot(snapshot, request_rows_limit=60).to_json()

        self.assertEqual(payload["requestRowsTotal"], 31)
        self.assertEqual(len(payload["requestRows"]), 31)
        self.assertEqual(len(payload["requestRowDetails"]), 31)

    def test_provider_settings_use_tabs_with_modal_drafts(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('data-action="settings-provider-tab"', script)
        self.assertIn('data-provider-tab="true"', script)
        self.assertIn('data-provider-navigation="true"', script)
        self.assertIn('data-provider-tab-fixed="true"', script)
        self.assertIn('data-provider-tab-viewport="true"', script)
        self.assertIn('data-action="settings-provider-nav"', script)
        self.assertIn('data-direction="prev"', script)
        self.assertIn('data-direction="next"', script)
        self.assertIn("function syncSettingsProviderTabNavigation", script)
        self.assertIn("function scrollSettingsProviderRail", script)
        self.assertIn("tabs.scrollLeft + (target.getBoundingClientRect().left - railRect.left)", script)
        self.assertIn("function activateSettingsProviderTab", script)
        self.assertIn("settingsProviderRailDirectionForTab", script)
        self.assertIn("scrollbar-width: none", script)
        self.assertIn('data-action="settings-add-provider"', script)
        self.assertIn('data-action="settings-edit-provider"', script)
        self.assertIn('data-action="settings-delete-provider"', script)
        self.assertIn("function openProviderConfigDialog", script)
        self.assertIn("function applyProviderConfigDialog", script)
        self.assertIn("function openProviderDeleteDialog", script)
        self.assertIn("function confirmProviderDeleteDialog", script)
        self.assertIn('data-provider-config-field="section_text"', script)
        self.assertIn('<details class="codex-usage-hud-provider-config-preview">', script)
        self.assertIn('<label>复制模型列表 / 单价配置', script)
        self.assertIn('<div class="codex-usage-hud-provider-config-grid-placeholder" aria-hidden="true"></div>', script)
        self.assertIn('data-provider-delete-model-prices="true"', script)
        self.assertIn('data-provider-delete-session-history="true"', script)
        self.assertIn("删除供应商：", script)
        self.assertIn("padding: 10px 18px;", script)
        self.assertIn("默认 Codex App Provider 不支持删除。", script)
        self.assertIn("defaultProviderSectionText", script)
        self.assertIn('String(settingsProviderDraft.appProvider || "").trim().toLowerCase()', script)
        self.assertIn("section_text: String(draft.configText || \"\")", script)
        self.assertIn("const isAppProvider = !isNew", script)
        self.assertIn("const isDefaultProvider = isAppProvider &&", script)
        self.assertIn("registryEntry.requiresOpenaiAuth === true", script)
        self.assertIn("providerRegistryEntry.requiresOpenaiAuth === true", script)
        self.assertIn("defaultProviderEditable", script)
        self.assertIn("auth.json", script)
        self.assertIn("OPENAI_API_KEY", script)
        self.assertIn("Codex App 使用 auth.json 中的 OPENAI_API_KEY", script)
        self.assertIn("officialAccount", script)
        self.assertIn("官方账号登录，默认 Provider 不可编辑", script)
        self.assertIn("官方账号登录时，默认 Codex App Provider 由 Codex Desktop 管理，不支持编辑。", script)
        self.assertIn("providerRegistryEntry.officialAccount === true", script)
        self.assertIn("function showSettingsRestartPrompt", script)
        self.assertIn("关闭窗口可稍后重启。", script)
        self.assertIn("showSettingsRestartPrompt(status.message", script)
        self.assertIn("suggestedProviderEnvironmentKey", script)
        self.assertIn("function suggestedProviderIdFromBaseUrl", script)
        self.assertIn("new URL(candidate).hostname", script)
        self.assertIn("suggestedProviderIdFromBaseUrl(baseUrlNode.value)", script)
        self.assertIn("const initialFocusNode = isNew ? baseUrlNode : apiKeyNode;", script)
        self.assertIn("编辑时自动选中 API key 输入框", script)
        self.assertIn("仅气泡通知不统计", script)
        self.assertIn("codexProviders: collectCodexProviderUpdates()", script)
        # 编辑供应商只保存 provider 配置，不提交 model 单价。
        self.assertIn('{ action: "save", codexProviders: collectCodexProviderUpdates() }', script)
        self.assertIn("编辑供应商只保存 provider 配置", script)
        self.assertIn("从 provider registry 刷新 API key 回显值", script)
        self.assertIn('data-provider-enabled="true"', script)
        self.assertIn('data-provider-notification-only="true"', script)
        self.assertIn('data-provider-quick-launch="true"', script)
        self.assertIn("quick_launch_providers: Array.from(draft.quickLaunchProviders || [])", script)
        self.assertIn("function syncCodexCliQuickLaunchMenu", script)
        self.assertIn('data-codex-usage-hud-cli-menu-toggle="true"', script)
        self.assertIn('data-codex-usage-hud-cli-provider="true"', script)
        self.assertIn("仅气泡通知不统计", script)
        self.assertIn("function captureSettingsProviderForm", script)
        self.assertIn('data-price-model="${escapeHtml(model)}"', script)
        self.assertIn('const originalKey = String(row.dataset.priceKey || "").trim();', script)
        self.assertIn("const sameScope = originalKey", script)
        self.assertIn("? originalKey", script)
        self.assertIn("function switchSettingsProvider", script)
        self.assertIn('data-action="settings-provider-toggle-api-key"', script)
        self.assertIn('data-action="settings-provider-test-connectivity"', script)
        self.assertIn("function toggleProviderApiKeyVisibility", script)
        self.assertIn("function testProviderConnectivityFromDialog", script)
        self.assertIn("function applyProviderConnectivityStatus", script)
        self.assertIn("function commitSettingsFromDraft", script)
        self.assertIn("providerModelsFetching", script)
        self.assertIn("测试连通性", script)
        self.assertIn("连通性测试成功。", script)
        self.assertIn('action: "fetchProviderModels"', script)
        self.assertIn("provider_order is the persisted append-only order", script)
        self.assertIn("function canonicalSettingsPriceTable", script)
        self.assertIn("default_model_prices", script)
        self.assertIn("settings.default_model_prices", script)
        self.assertIn('<option value="">不复制，使用当前默认价格</option>', script)
        self.assertIn("const isNewProvider", script)
        self.assertIn('settings.provider_scope_mode === "custom"', script)
        self.assertIn("settingsDirtyProviders.add(activeProvider)", script)
        self.assertIn("${count} 个 Provider 有未保存修改", script)
        self.assertIn('data-action="settings-discard-confirm"', script)
        self.assertIn('["ArrowLeft", "ArrowRight"]', script)
        self.assertIn(
            'provider_scope_mode: allProvidersSelected ? "all" : "custom"',
            script,
        )
        self.assertIn("notification_only_providers: notificationOnlyProviders", script)
        self.assertIn("provider_order: draft.order", script)
        self.assertIn("if (counterpart) counterpart.checked = false", script)
        self.assertIn('pricing_url: String(settings.pricing_url || "").trim()', script)
        self.assertIn("weekly_adjustment_usd: settings.weekly_adjustment_usd", script)
        self.assertIn("model_prices: settings.model_prices", script)
        self.assertIn(
            'renderSettingsModal("settings", "", { resetProviderDraft: true })',
            script,
        )
        self.assertNotIn('data-provider-select="true"', script)
        self.assertNotIn('data-provider-scope="true"', script)
        self.assertNotIn("data-advanced=", script)
        self.assertNotIn("price-table[data-advanced", script)
        self.assertNotIn("未保存的输入不会自动保留", script)
        # 数字开头的 Provider ID 自动生成的环境变量名必须以 _ 前缀开头，
        # 否则会被 front-end 与 codex_provider_config 的 ENVIRONMENT_KEY_PATTERN
        # （^[A-Za-z_][A-Za-z0-9_]*$）同时拒绝（issue: 新增供应商「添加」无反应）。
        self.assertIn('const prefix = /^[0-9]/.test(normalized) ? "_" : "";', script)
        self.assertIn('return `${prefix}${normalized || "PROVIDER"}_API_KEY`;', script)
        # 校验错误必须显示在 provider 配置对话框内部，避免被 confirm-layer 遮罩遮挡。
        self.assertIn("function setProviderConfigDialogError", script)
        self.assertIn('data-provider-config-status="true"', script)
        self.assertIn('layer?.querySelector(\'[data-provider-config-status="true"]\')', script)

    def test_renderer_payload_exposes_default_prices_for_new_providers(self) -> None:
        payload = payload_from_snapshot(ParsedSession(status="waiting")).to_json()
        defaults = payload["settings"]["default_model_prices"]

        self.assertEqual(payload["settings"]["quick_launch_providers"], [])

        self.assertEqual(
            set(defaults),
            {
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.5",
                "gpt-5.6-luna",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
            },
        )
        self.assertEqual(defaults["gpt-5.6-luna"]["input"], 0.2)
        self.assertEqual(defaults["gpt-5.6-terra"]["output"], 12.0)

    def test_model_price_columns_and_row_paste_follow_standard_order(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        header = script[script.index('class="codex-usage-hud-price-header"'):]
        self.assertLess(header.index("模型"), header.index("输入"))
        self.assertLess(header.index("输入"), header.index("缓存读取"))
        self.assertLess(header.index("缓存读取"), header.index("缓存写入"))
        self.assertLess(header.index("缓存写入"), header.index("输出"))
        self.assertIn("function priceClipboardValues(text)", script)
        self.assertIn(".split(/\\s+/).filter(Boolean)", script)
        self.assertIn("function fillPriceRowFromClipboard(row, text)", script)
        self.assertIn('const row = event.target?.closest?.(\'[data-price-row="true"]\')', script)
        self.assertIn('rootScope.listen(document, "paste"', script)
        settings_exports_start = script.index("const settingsShellDomain = ctx.domains.register")
        settings_exports_end = script.index("function escapeHtml", settings_exports_start)
        settings_exports = script[settings_exports_start:settings_exports_end]
        self.assertIn(
            "captureSettingsProviderForm,\n      priceClipboardValues,\n      fillPriceRowFromClipboard,\n      updateSettingsProviderDraftStatus",
            settings_exports,
        )
        self.assertIn("}, true);", script)
        self.assertNotIn("推理输出", script)

    def test_payload_from_renderer_new_session_clears_session_and_round_stats(self) -> None:
        snapshot = ParsedSession(
            status="waiting",
            selection_source="renderer-new-session",
        )
        snapshot.today_tokens = 1234567
        snapshot.today_cost_usd = 1.23
        snapshot.week_tokens = 2345678
        snapshot.week_cost_usd = 2.34

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["session"], "新会话")
        self.assertTrue(
            str(payload["topLine"]).startswith("新会话 等待首个会话事件 | 今日")
        )
        top_progress = payload["topProgress"]
        self.assertEqual(
            top_progress["collapsed"][0]["label"],
            "新会话 等待首个会话事件",
        )
        top_details = payload["topDetails"]
        self.assertEqual(top_details["title"], "新会话")
        self.assertEqual(top_details["session"], "新会话 | 行 0 | 确认 0")
        self.assertEqual(top_details["sessionCost"], "$0.0000")
        self.assertEqual(top_details["sessionTokens"], "0")
        self.assertEqual(top_details["sessionRounds"], "0 轮确认")
        self.assertEqual(top_details["sessionAverage"], "均值 n/a")
        self.assertEqual(top_details["currentTask"], "新会话")
        self.assertEqual(top_details["activityLast"], "0轮")
        self.assertEqual(top_details["heavyRounds"], [])
        self.assertEqual(payload["requestRows"], [])
        self.assertEqual(payload["requestRowDetails"], [])

    def test_payload_from_pending_renderer_session_never_displays_zero_usage(self) -> None:
        snapshot = ParsedSession(
            status="waiting",
            selection_source="renderer-pending-map",
            renderer_session_id="local:client-new-thread:pending-1",
            selection_seq=9,
            selection_observed_at_ms=int(time.time() * 1000) - 125,
            follow_state="pending",
            follow_reason="awaiting-exact-mapping",
            follow_timing={"receivedAt": 123, "resolvedAt": 124},
        )

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertTrue(payload["pendingSession"])
        self.assertFalse(payload["newSession"])
        self.assertEqual(payload["session"], "会话加载中")
        self.assertIn("加载精确会话映射", str(payload["topLine"]))
        self.assertNotIn("本会话 0", str(payload["topLine"]))
        self.assertEqual(
            payload["requestLine"],
            "会话切换中：正式 ID 已收到，等待本地映射",
        )
        self.assertEqual(payload["selectionSeq"], 9)
        self.assertEqual(
            payload["rendererSessionId"], "local:client-new-thread:pending-1"
        )
        self.assertFalse(payload["cachedPreview"])
        self.assertEqual(payload["followState"], "pending")
        self.assertEqual(payload["followReason"], "awaiting-exact-mapping")
        self.assertEqual(
            payload["followTiming"],
            {"receivedAt": 123, "resolvedAt": 124},
        )
        self.assertGreaterEqual(payload["followElapsedMs"], 100)
        self.assertEqual(payload["requestRows"], [])
        self.assertEqual(payload["requestRowDetails"], [])

    def test_payload_exposes_debug_runtime_errors(self) -> None:
        snapshot = ParsedSession(status="waiting")
        event = RuntimeErrorEvent(
            source="active_session",
            severity="error",
            code="active_session.unmatched_thread",
            message="Renderer thread could not be mapped",
            context={"threadId": "abc"},
            first_seen_at=1.0,
            last_seen_at=2.0,
            count=3,
        )

        payload = payload_from_snapshot(
            snapshot,
            debug=True,
            runtime_errors=[event],
        ).to_json()

        self.assertTrue(payload["debug"])
        self.assertEqual(len(payload["runtimeErrors"]), 1)
        error = payload["runtimeErrors"][0]
        self.assertEqual(error["code"], "active_session.unmatched_thread")
        self.assertEqual(error["context"], {"threadId": "abc"})
        self.assertEqual(error["count"], 3)

    def test_renderer_script_renders_debug_error_hud(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("function renderRuntimeErrors", script)
        self.assertIn("codex-usage-hud-runtime-errors", script)
        self.assertIn("runtimeErrors", script)
        self.assertIn("DEBUG HUD active", script)
        self.assertIn("runtimeErrorsPanel.hidden = !debug", script)
        self.assertIn("debugStatusItem", script)
        self.assertIn("left: 16px;", script)
        self.assertNotIn("right: 16px;", script)
        self.assertIn("user-select: text;", script)
        self.assertIn('title.dataset.action = "runtime-errors-move";', script)
        self.assertIn("[data-action='runtime-errors-move']", script)
        self.assertIn("beginRuntimeErrorsGesture", script)
        self.assertIn("setRuntimeErrorsPanelState", script)

    def test_renderer_debug_error_hud_defaults_collapsed_and_toggles_width(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('runtimeErrorsPanel.dataset.expanded = String(expanded);', script)
        self.assertIn('expanded: false', script)
        self.assertIn("runtime-errors-toggle", script)
        self.assertIn('body.hidden = !expanded;', script)
        self.assertIn('heading.textContent = "errors";', script)
        self.assertIn('setRuntimeErrorsPanelState({ expanded });', script)
        self.assertIn('.codex-usage-hud-runtime-errors[data-expanded="false"]', script)
        self.assertIn('.codex-usage-hud-runtime-errors[data-expanded="true"]', script)

    def test_payload_from_snapshot_exposes_update_domains(self) -> None:
        snapshot = ParsedSession(
            session_id="session-domain",
            session_title="Domain Split",
            status="parsed",
            selection_source="renderer",
            refreshed_at=datetime(2026, 7, 6, 10, 0, 0).astimezone(),
            last_event_time=datetime(2026, 7, 6, 9, 59, 58).astimezone(),
            confirmed=ConfirmedTokens(cumulative_total=42, cumulative_cost_usd=0.01),
            request=RequestTokens(status="idle", model="gpt-5", total_tokens=0),
        )
        snapshot.today_tokens = 100
        snapshot.week_tokens = 200
        snapshot.today_cost_usd = 0.02
        snapshot.week_cost_usd = 0.04

        payload = payload_from_snapshot(
            snapshot,
            settings_bridge_url="http://127.0.0.1:8765",
            background_usage_bridge_url=(
                "http://127.0.0.1:8765/background-usage?access_token=test"
            ),
            background_usage_revision=7,
            background_usage_notification={
                "count": 3,
                "eventId": "event-3",
                "range": "7d",
            },
            settings_command_status={"lastCommand": "saved"},
            theme={"variant": "dark"},
            update_state={"visible": True, "phase": "ready"},
            debug=True,
            runtime_errors=[
                RuntimeErrorEvent(
                    source="renderer",
                    severity="error",
                    code="renderer.anchor_missing",
                    message="anchor missing",
                    context={"session": "session-domain"},
                    first_seen_at=1.0,
                    last_seen_at=2.0,
                )
            ],
            work_overlay_selectable_max=3,
            desktop_overlay_dependency={"available": True},
        ).to_json()

        domains = payload["payloadDomains"]
        self.assertEqual(
            set(domains),
            {
                "currentSession",
                "sessionSwitch",
                "budget",
                "settings",
                "overlay",
                "diagnostics",
                "backgroundUsage",
            },
        )
        self.assertEqual(domains["currentSession"]["topLine"], payload["topLine"])
        self.assertEqual(domains["sessionSwitch"]["topLine"], payload["topLine"])
        self.assertNotIn("topDetails", domains["sessionSwitch"])
        self.assertEqual(domains["currentSession"]["requestRows"], payload["requestRows"])
        self.assertEqual(domains["budget"]["topProgress"], payload["topProgress"])
        self.assertEqual(domains["settings"]["settingsBridgeUrl"], "http://127.0.0.1:8765")
        self.assertEqual(domains["settings"]["updateState"]["phase"], "ready")
        self.assertEqual(domains["overlay"]["workOverlaySelectableMax"], 3)
        self.assertEqual(domains["overlay"]["desktopOverlayDependency"], {"available": True})
        self.assertEqual(domains["backgroundUsage"]["backgroundUsageRevision"], 7)
        expected_notification = {
            "count": 3,
            "eventId": "event-3",
            "range": "7d",
        }
        self.assertEqual(
            domains["currentSession"]["backgroundUsageNotification"],
            expected_notification,
        )
        self.assertEqual(
            domains["sessionSwitch"]["backgroundUsageNotification"],
            expected_notification,
        )
        self.assertEqual(
            domains["backgroundUsage"]["backgroundUsageNotification"],
            expected_notification,
        )
        self.assertIn(
            "access_token=test",
            domains["backgroundUsage"]["backgroundUsageBridgeUrl"],
        )
        self.assertEqual(
            domains["backgroundUsage"]["settingsCommandStatus"],
            {"lastCommand": "saved"},
        )
        self.assertNotIn(
            "prompt",
            repr(domains["backgroundUsage"]).casefold(),
        )
        self.assertTrue(domains["diagnostics"]["debug"])
        self.assertEqual(
            domains["diagnostics"]["runtimeErrors"][0]["code"],
            "renderer.anchor_missing",
        )

    def test_renderer_background_usage_tab_contract(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('data-tab="backgroundUsage"', script)
        self.assertIn('>用量总览</button>', script)
        self.assertIn('"Top10会话用量"', script)
        self.assertIn('"Top10会话金额"', script)
        self.assertIn('全部后台功能', script)
        self.assertIn('label="后台任务"', script)
        self.assertIn('label="用户会话排行"', script)
        self.assertIn("function backgroundUsageSessionRankingMode", script)
        self.assertIn("function backgroundUsageSessionRankingPanelHtml", script)
        self.assertIn("function backgroundUsageSessionRankingDetailHtml", script)
        self.assertIn(
            'const workdirPath = String(session?.workdir || "").trim();',
            script,
        )
        self.assertIn(
            'const workdirHtml = workdirPath && sessionId',
            script,
        )
        self.assertIn("codex-usage-hud-background-workdir-link", script)
        self.assertIn("codex-usage-hud-background-event-row", script)
        self.assertIn("function backgroundUsageWorkdirAssociationText", script)
        self.assertIn("记录时运行目录", script)
        self.assertIn("当前目录不可用", script)
        event_source = script.split(
            "function backgroundUsageEventHtml", 1
        )[1].split("function backgroundUsageDetailHtml", 1)[0]
        self.assertNotIn("workdir", event_source)
        self.assertIn('data-action="background-usage-open-workdir"', script)
        self.assertIn('action: "openBackgroundUsageWorkdir", eventId', script)
        self.assertIn(
            'const workdirButton = event.target?.closest?.(\'[data-action="usage-insights-open-workdir"]\');',
            script,
        )
        self.assertIn('sessionAction: "background-usage-session-select"', script)
        self.assertIn('data-session-ranking-detail="true"', script)
        self.assertIn('"background-usage-session-select"', script)
        self.assertIn('data-usage-session-id="${escapeHtml(sessionId)}"', script)
        self.assertIn("action.dataset.usageSessionId", script)
        self.assertNotIn(
            '.codex-usage-hud-background-master-detail[data-session-ranking="true"] {\n'
            '        grid-template-columns: minmax(0, 1fr);',
            script,
        )
        self.assertIn('backgroundUsageState.range === "30d") return "month"', script)
        self.assertIn('[["today", "今天"], ["7d", "近 7 天"], ["30d", "近 30 天"]]', script)
        self.assertIn("usageInsightsRankingRowsHtml", script)
        self.assertIn('class="codex-usage-hud-session-ranking-row"', script)
        self.assertIn('codex-usage-hud-session-ranking-select', script)
        self.assertIn(
            'codex-usage-hud-background-event-list '
            'codex-usage-hud-session-ranking-list',
            script,
        )
        self.assertIn(
            'const workdir = String(item?.workdirName || "").trim();',
            script,
        )
        self.assertIn('data-action="usage-insights-open-workdir"', script)
        self.assertIn('codex-usage-hud-session-ranking-workdir', script)
        self.assertIn('codex-usage-hud-session-ranking-model', script)
        self.assertIn('#${index + 1} ${escapeHtml(cost)}', script)
        self.assertIn('role="button" tabindex="0"', script)
        self.assertIn("function usageInsightsSessionModelNames", script)
        self.assertIn("使用模型${modelNames.length > 1", script)
        self.assertIn('data-target-title="', script)
        self.assertIn('data-workdir="', script)
        self.assertIn("if (submitted) closeSettingsModal();", script)
        self.assertIn(
            ".codex-usage-hud-background-detail-head > .codex-usage-hud-settings-action",
            script,
        )
        self.assertIn("codex-usage-hud-background-detail-full", script)
        self.assertIn(
            ".codex-usage-hud-session-ranking-row "
            ".codex-usage-hud-background-event-totals > *",
            script,
        )
        self.assertIn('data-background-usage-filter="feature"', script)
        self.assertIn('data-background-usage-filter="model"', script)
        self.assertIn('data-action="background-usage-range"', script)
        self.assertIn(
            'if (backgroundUsageState.range === "all") {\n'
            '          backgroundUsageState.range = "7d";',
            script,
        )
        self.assertIn("HUD 估算", script)
        self.assertIn("本机日志值", script)
        self.assertIn(
            "backgroundUsageFormatCost(summary.estimatedCostUsd)",
            script,
        )
        self.assertNotIn(
            "backgroundUsageFormatCost(summary.estimatedCostUsd || 0)",
            script,
        )
        self.assertIn(
            '.codex-usage-hud-settings-dialog[data-active-tab="backgroundUsage"] '
            ".codex-usage-hud-settings-body {",
            script,
        )
        self.assertRegex(
            script,
            r'\.codex-usage-hud-settings-dialog\[data-active-tab="backgroundUsage"\] '
            r'\.codex-usage-hud-settings-body \{\s+overflow: auto;',
        )

        detail_start = script.index("async function loadBackgroundUsageDetail(eventId,")
        list_start = script.index("async function loadBackgroundUsage(", detail_start)
        self.assertIn(
            'backgroundUsageEndpoint("/detail")',
            script[detail_start:list_start],
        )
        self.assertNotIn(
            'backgroundUsageEndpoint("/detail")',
            script[list_start:],
        )
        self.assertIn('"backgroundUsageQuery"', script)
        self.assertIn('"backgroundUsageDetail"', script)
        self.assertIn("backgroundUsageResponse", script)
        self.assertIn("queryRequestId", script)
        self.assertIn("detailRequestId", script)
        self.assertIn("scheduleBackgroundUsageRequestTimeout", script)
        self.assertIn("clearBackgroundUsageRequestTimeout", script)
        self.assertIn("captureBackgroundUsageScrollPositions", script)
        self.assertIn("restoreBackgroundUsageScrollPositions", script)
        self.assertIn("backgroundUsageHistoryScrollTops", script)
        self.assertIn("backgroundUsageDetailScrollTops", script)
        self.assertIn("const policyButton = policy && !disabled", script)
        self.assertIn('禁用此类任务', script)
        self.assertIn('启用此类任务', script)
        self.assertNotIn('禁止此后台任务', script)
        self.assertIn("backgroundUsagePolicyConfirm(backgroundUsageState.detail)", script)
        self.assertIn("applyBackgroundUsagePolicy,", script)
        self.assertIn('role="switch"', script)
        self.assertIn('aria-checked="${displayDisabled ? "false" : "true"}"', script)
        self.assertIn('data-desired-state="${desiredState}"', script)
        self.assertIn('backgroundUsageState.policyLoading = true', script)
        self.assertIn("后台任务控制能力当前不可用。", script)
        self.assertIn("policy && !disabled", script)
        self.assertIn("backgroundUsageDetailLoaded", script)
        self.assertIn('data-action="background-usage-open-notification"', script)
        self.assertIn("backgroundUsageNotificationCount", script)
        self.assertIn("codex-usage-hud-background-unread-dot", script)
        self.assertIn('{ markViewed: true }', script)
        self.assertIn("markViewed: markViewed === true", script)
        self.assertIn('["all", "全部"]', script)
        self.assertRegex(
            script,
            r"backgroundUsageState\.range = normalizeBackgroundUsageRange\(\s+"
            r"response\?\.payload\?\.range,",
        )
        self.assertRegex(
            script,
            r"\.\$\{requestClass\} \.codex-usage-hud-collapsed\[data-has-badge=\"true\"\] "
            r"\{\s+grid-template-columns: auto minmax\(0, 1fr\) auto 22px;",
        )
        submit_start = script.index("function submitBackgroundUsageCommand")
        submit_end = script.index("function readThemeStorage", submit_start)
        submit_contract = script[submit_start:submit_end]
        self.assertIn("id: requestId", submit_contract)
        self.assertIn("requestId,", submit_contract)
        ranking_start = script.index("async function loadBackgroundUsage(")
        ranking_end = script.index("function renderSettingsModal", ranking_start)
        ranking_contract = script[ranking_start:ranking_end]
        self.assertIn('if (force || !insights || state === "idle")', ranking_contract)
        self.assertIn("requestUsageInsightsRefresh({ force });", ranking_contract)
        self.assertIn("if (usageInsightsState.refreshRequestId) return false;", script)
        self.assertIn("if (backgroundUsageSessionRankingMode()) {\n          const insights = usageInsightsFromPayload();", script)
        self.assertIn(
            "const incompleteCost = coverage?.hasCompleteCost === false;",
            script,
        )
        self.assertIn("费用不可估", script)
        self.assertIn("由 Codex App 官方 agent 工具在后台发起的请求", script)
        self.assertIn("featureEnLabel", script)
        self.assertIn("featurePurpose", script)
        self.assertNotIn('label: "待处理"', script)
        self.assertNotIn('label: "已处理"', script)
        self.assertNotIn("function backgroundUsageStatusMeta", script)
        self.assertIn(
            "const eventTime = backgroundUsageTime(event?.lastSeenAt, { compact: true });",
            script,
        )
        self.assertIn(
            '<span class="codex-usage-hud-background-event-meta">${escapeHtml(modelText)}</span>',
            script,
        )
        self.assertIn(
            "const detailTime = backgroundUsageTime(detail.lastSeenAt, { compact: true });",
            script,
        )
        self.assertNotIn("本机已归因", script)
        self.assertIn('data-action="background-usage-copy-prompt"', script)
        self.assertIn("user-select: text;", script)
        self.assertIn("-webkit-user-select: text;", script)

        jump_start = script.index("function applyBackgroundUsagePayload")
        jump_end = script.index("function install()", jump_start)
        jump_contract = script[jump_start:jump_end]
        self.assertIn("backgroundUsageOpenEventId", jump_contract)
        self.assertIn('renderSettingsModal("backgroundUsage")', jump_contract)
        self.assertIn(
            'kind === "open"',
            jump_contract,
        )
        self.assertIn(
            "if (requestId !== backgroundUsageState.queryRequestId) return;",
            jump_contract,
        )
        self.assertIn(
            "if (!responseError && backgroundUsageState.selectedEventId)",
            jump_contract,
        )
        # Auto-located open from the bottom-right badge must mark the event viewed.
        self.assertRegex(
            jump_contract,
            r"void loadBackgroundUsageDetail\(\s+"
            r"backgroundUsageState\.selectedEventId,\s+\{ markViewed: true \},",
        )
        self.assertIn(
            "markBackgroundUsageEventViewed(openEventId)",
            jump_contract,
        )
        self.assertNotIn(
            "loadBackgroundUsage({ eventId: openEventId, force: true })",
            jump_contract,
        )
        open_click_start = script.index(
            'if (action.dataset.action === "background-usage-open-notification")'
        )
        open_click_end = script.index(
            'if (action.dataset.action === "settings-open")',
            open_click_start,
        )
        open_click_contract = script[open_click_start:open_click_end]
        self.assertIn(
            "void loadBackgroundUsageDetail(eventId, { markViewed: true })",
            open_click_contract,
        )
    def test_session_management_is_the_only_storage_settings_surface(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('data-tab="storage" data-active="${activeTab === "storage"}">会话管理</button>', script)
        self.assertIn("function storagePanelHtml()", script)
        self.assertIn("${sessionCleanupPanelHtml()}", script)
        self.assertIn('data-action="session-cleanup-open-workdir"', script)
        self.assertIn(
            'action: "openSessionCleanupWorkdir", itemId, inventoryRevision',
            script,
        )
        self.assertIn('const secondaryWorkdir = workdirButton("secondary");', script)
        self.assertIn(
            ".codex-usage-hud-session-row > .codex-usage-hud-session-workdir",
            script,
        )
        self.assertIn(
            '<span aria-hidden="true"></span><span>最后活动</span>',
            script,
        )
        self.assertIn(
            'codex-usage-hud-session-workdir-filter"><select data-session-cleanup-filter="workdirId"',
            script,
        )
        self.assertNotIn(
            'codex-usage-hud-session-workdir-filter"><label><span>工作目录</span>',
            script,
        )
        self.assertIn(
            'const sessionCleanupRow = event.target?.closest?.(".codex-usage-hud-session-row");',
            script,
        )
        self.assertNotIn("safeCleanup", script)
        self.assertNotIn("safe-cleanup", script)
        self.assertNotIn("垃圾清理", script)
        self.assertNotIn("空间清理", script)
        self.assertIn("grid-template-rows: minmax(0, 1fr) auto;", script)
        self.assertIn("function readSettingsUiState()", script)
        self.assertIn("function writeSettingsUiState(open, tab)", script)
        self.assertIn("ctx.storage.write(sessionStorage, settingsUiStorageKey", script)
        self.assertIn("writeSettingsUiState(true, activeTab);", script)
        self.assertIn("writeSettingsUiState(false, settingsActiveTab);", script)
        self.assertIn("function restoreOpenSettingsModal()", script)
        self.assertIn("if (root !== previousRoot) restoreOpenSettingsModal();", script)
        self.assertIn("settingsUiState?.open !== true", script)
        self.assertIn(
            'const settingsModal = event.target?.closest?.(`#${settingsModalId}`);',
            script,
        )
        self.assertIn("if (settingsModal && root.contains(settingsModal))", script)
        self.assertNotIn("SessionCleanupModalGuard", script)
        scan_start = script.index("function requestSessionCleanupScan")
        scan_end = script.index("function requestSessionCleanupPreview", scan_start)
        scan_script = script[scan_start:scan_end]
        self.assertLess(
            scan_script.index("submitSettingsCommand("),
            scan_script.index('renderSettingsModal("storage"'),
        )
        self.assertIn("function formatSessionCleanupElapsed(startedAt)", script)
        self.assertIn(
            "const elapsed = formatSessionCleanupElapsed(sessionCleanupState.scanStartedAt);",
            script,
        )
        self.assertIn("function sessionCleanupScanActive()", script)
        self.assertIn("function scheduleSessionCleanupScanWatchdog(requestId)", script)
        self.assertIn('"session_cleanup_scan_watchdog"', script)
        self.assertIn("扫描命令未收到响应，请重新扫描。", script)
        self.assertIn("function ensureSessionCleanupElapsedTicker()", script)
        self.assertIn(
            'ctx.lifecycle.interval(\n            "session_cleanup_elapsed"',
            script,
        )
        self.assertIn('data-session-cleanup-elapsed="true"', script)
        self.assertIn("stopSessionCleanupElapsedTicker();", script)
        self.assertNotIn("formatCleanupElapsed(", script)
        session_payload_start = script.index("function applySessionCleanupPayload")
        session_payload_end = script.index("function applyPayloadDomains", session_payload_start)
        session_payload_script = script[session_payload_start:session_payload_end]
        self.assertNotIn("restoreOpenSettingsModal();", session_payload_script)
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr));", script)
        self.assertIn(
            ".codex-usage-hud-session-cleanup:has(> .codex-usage-hud-cleanup-empty-state):not(:has(> .codex-usage-hud-cleanup-scan-strip))",
            script,
        )

    def test_session_search_commits_only_on_button_or_enter_without_interrupting_ime(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("const commitSessionCleanupSearch = (sessionSearch) => {", script)
        self.assertIn('rootScope.listen(root, "keydown", (event) => {', script)
        self.assertIn('event.key !== "Enter" || event.isComposing || event.keyCode === 229', script)
        self.assertIn('data-action="session-cleanup-search-submit"', script)
        self.assertIn("commitSessionCleanupSearch(input);", script)
        input_start = script.index('rootScope.listen(root, "input", (event) => {')
        input_end = script.index('rootScope.listen(document, "paste", (event) => {', input_start)
        input_script = script[input_start:input_end]
        self.assertNotIn('renderSettingsModal("storage");', input_script)
        self.assertIn("IME composition emits input events before the final text is committed.", input_script)
        self.assertIn("requestSessionCleanupSearch(search);", input_script)
        self.assertIn('"session_cleanup_search"', input_script)
        self.assertIn('data-session-cleanup-filter="workdirId"', script)
        self.assertIn("function sessionCleanupWorkdirOptionHtml", script)
        self.assertIn("const SESSION_CLEANUP_PAGE_SIZE = 30;", script)
        self.assertIn("function sessionCleanupPageRows", script)
        self.assertIn('data-action="session-cleanup-page"', script)
        self.assertIn("第 ${pageIndex + 1} / ${pageCount} 页", script)
        self.assertNotIn("rows.slice(0, 180)", script)
        self.assertNotIn("仅显示前 180 项", script)
        self.assertIn('action: "sessionCleanupSearch"', script)
        self.assertIn("Inventory mutations invalidate opaque match ids.", script)
        self.assertIn("requestDetailedWorkdirs", script)
        self.assertIn("workdirOptionsRetryBlocked", script)
        self.assertIn("const responseReady = !responseError", script)
        self.assertIn("function resetSessionCleanupPendingRequests()", script)
        self.assertIn("resetSessionCleanupPendingRequests();", script)
        self.assertIn("an empty/mismatched request id is a", script)

    def test_session_jump_float_reuses_native_find_and_locks_drag_origin(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        # Reopening an already-mounted native find bar must execute a fresh
        # search instead of posting the toggle message a second time.
        self.assertIn(
            'function fillThreadFindInput(input, query, { force = false } = {})',
            script,
        )
        self.assertIn('const existingInput = document.getElementById("content-search-input");', script)
        self.assertIn("function threadFindInputVisible(input)", script)
        self.assertIn("if (threadFindInputVisible(existingInput))", script)
        self.assertIn('fillThreadFindInput(existingInput, value, { force: true });', script)
        self.assertIn('setter.call(input, "");', script)
        self.assertIn('input.dispatchEvent(new KeyboardEvent("keydown"', script)
        self.assertIn('key: "Enter", code: "Enter", keyCode: 13, which: 13', script)
        # The current UI labels this broader, user-facing source category
        # "正文匹配"; it deliberately does not promise native Find accuracy.
        self.assertIn("正文匹配", script)
        self.assertIn("索引命中", script)
        self.assertIn("命中索引内容，打开后可能无法被 Codex 原生查找定位", script)

        # The pointer origin is captured while the side-anchored panel still
        # has its visual transform; only then is the transform removed.
        self.assertIn("const rect = panel.getBoundingClientRect();\n        dragging = true;", script)
        self.assertIn('panel.style.bottom = "auto";', script)
        self.assertIn('panel.classList.remove("side-left", "side-right");', script)
        self.assertIn('searchFloat.dragging = true;', script)
        self.assertIn('searchFloat.dragging = false;', script)

        # The result list has a stable header, metadata row, and three-button
        # footer so long titles and match chips do not shift controls around.
        self.assertIn("codex-usage-hud-search-heading", script)
        self.assertIn("codex-usage-hud-search-row-main", script)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", script)
        self.assertIn("max-height: min(42vh, 360px);", script)

    def test_session_cleanup_filters_survive_renderer_reinject(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn(
            'sessionCleanupFiltersStorageKey = "codexUsageHudSessionCleanupFilters:v1"',
            script,
        )
        self.assertIn("function readSessionCleanupFilters()", script)
        self.assertIn("function persistSessionCleanupFilters()", script)
        self.assertIn("...readSessionCleanupFilters(),", script)
        self.assertIn(
            "ctx.storage.write(sessionStorage, sessionCleanupFiltersStorageKey, JSON.stringify(state));",
            script,
        )
        self.assertGreaterEqual(script.count("persistSessionCleanupFilters();"), 5)

    def test_session_cleanup_confirm_survives_same_token_repaint(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('layer.dataset.sessionCleanupConfirm = "true";', script)
        self.assertIn('layer.dataset.sessionCleanupConfirmToken = token;', script)
        self.assertIn("function restoreSessionCleanupConfirm(expectedToken)", script)
        self.assertIn("restoreSessionCleanupConfirm(sessionCleanupConfirmToken);", script)
        self.assertIn("currentToken !== token", script)
        self.assertIn("restoreSessionCleanupConfirm(token);", script)
        self.assertNotIn(
            'closeSettingsConfirm();\n        sessionCleanupState.previewTokenShown = "";',
            script,
        )

    def test_session_cleanup_delete_keeps_loading_layer_until_matching_terminal_payload(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("function openSessionCleanupDeleteLoading(requestId)", script)
        self.assertIn('mode: "session-cleanup-delete"', script)
        self.assertIn('layer.dataset.sessionCleanupDeleteLoading = "true";', script)
        self.assertIn('layer.dataset.sessionCleanupDeleteRequestId = String(requestId || "");', script)
        self.assertIn("openSessionCleanupDeleteLoading(requestId);", script)
        self.assertIn("completedExecute && loadingRequestId && responseRequestId === loadingRequestId", script)
        self.assertIn('data-session-cleanup-delete-loading="true"', script)
        self.assertIn(
            '.codex-usage-hud-settings-confirm-actions .codex-usage-hud-settings-action[data-danger="true"]',
            script,
        )

    def test_provider_delete_uses_correlated_session_cleanup_loading(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("function openProviderDeleteLoading(requestId, provider, deleteSessionHistory = false)", script)
        self.assertIn('mode: "provider-delete"', script)
        self.assertIn('layer.dataset.providerDeleteLoading = "true";', script)
        self.assertIn('layer.dataset.providerDeleteRequestId = String(requestId || "");', script)
        self.assertIn('layer.dataset.providerDeleteProvider = String(provider || "").trim().toLowerCase();', script)
        self.assertIn("openProviderDeleteLoading(requestId, provider, deleteSessionHistory);", script)
        self.assertIn('data-provider-delete-loading="true"', script)
        self.assertIn("function clearProviderDeleteWorkflow()", script)
        self.assertIn("providerDeleteHasSessionHistory", script)
        self.assertIn("clearProviderDeleteWorkflow();\n          closeSettingsConfirm();", script)
        self.assertIn(
            ".codex-usage-hud-provider-delete-card > .codex-usage-hud-settings-confirm-title",
            script,
        )
        self.assertIn("padding: 14px 18px 12px;", script)
        self.assertLess(
            script.index("pricingWorkflowState.providerDeleteRequestId = requestId;"),
            script.index("openProviderDeleteLoading(requestId, provider, deleteSessionHistory);"),
        )

    def test_provider_delete_removes_draft_even_when_latest_registry_has_no_provider(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT
        start = script.index("function removeProviderFromSettingsUi(provider)")
        end = script.index("function handleSettingsCommandSubmissionError", start)
        removal = script[start:end]

        self.assertIn("delete settingsProviderDraft.providers?.[provider];", removal)
        self.assertNotIn("settingsProviderNames(settings)", removal)

    def test_renderer_payload_can_emit_domain_only_update(self) -> None:
        payload = payload_from_snapshot(
            ParsedSession(status="parsed"),
            debug=True,
            runtime_errors=[
                RuntimeErrorEvent(
                    source="renderer",
                    severity="warning",
                    code="renderer.anchor_missing",
                    message="anchor missing",
                    first_seen_at=1.0,
                    last_seen_at=2.0,
                )
            ],
        )

        partial = payload.to_domain_json("diagnostics")

        self.assertEqual(
            set(partial),
            {"debug", "runtimeErrors", "connectionHealth", "payloadDomains"},
        )
        self.assertEqual(set(partial["payloadDomains"]), {"diagnostics"})
        self.assertTrue(partial["debug"])
        self.assertEqual(partial["runtimeErrors"][0]["code"], "renderer.anchor_missing")
        self.assertNotIn("topLine", partial)
        self.assertNotIn("requestRows", partial)

    def test_renderer_payload_can_emit_session_switch_without_expanded_details(self) -> None:
        snapshot = ParsedSession(
            session_id="session-switch",
            session_title="Switch Target",
            status="parsed",
            selection_source="renderer",
            confirmed=ConfirmedTokens(cumulative_total=42, cumulative_cost_usd=0.01),
            request=RequestTokens(status="idle", model="gpt-5", total_tokens=10),
        )

        partial = payload_from_snapshot(snapshot).to_domain_json("sessionSwitch")

        self.assertEqual(set(partial["payloadDomains"]), {"sessionSwitch"})
        self.assertEqual(partial["topLine"], partial["payloadDomains"]["sessionSwitch"]["topLine"])
        self.assertIn("requestLine", partial)
        self.assertNotIn("topDetails", partial)
        self.assertNotIn("requestRows", partial)
        self.assertNotIn("requestRowDetails", partial)

    def test_renderer_payload_can_emit_private_session_cleanup_domain(self) -> None:
        session_cleanup = {
            "revision": "2-opaque",
            "capability": {"available": True},
            "sessions": [
                {
                    "id": "opaque-session-item",
                    "title": "Old task",
                    "workdirName": "project-a",
                    "selectable": True,
                    "descendantCount": 2,
                }
            ],
            "operation": {"state": "idle"},
        }
        payload = payload_from_snapshot(
            ParsedSession(status="parsed"),
            session_cleanup=session_cleanup,
        )

        partial = payload.to_domain_json("sessionCleanup")

        self.assertEqual(set(partial["payloadDomains"]), {"sessionCleanup"})
        self.assertEqual(partial["sessionCleanup"]["revision"], "2-opaque")
        self.assertEqual(
            partial["sessionCleanup"]["sessions"][0]["id"],
            "opaque-session-item",
        )
        self.assertNotIn("topLine", partial)
        self.assertNotIn("10000000-0000-4000-8000-000000000001", repr(partial))
        self.assertNotIn("C:\\Users", repr(partial))

    def test_update_payload_backoff_skips_retry_after_invalidating_renderer(self) -> None:
        client = RendererHudClient(port=9229, enabled=True)
        target = {"id": "target-1", "webSocketDebuggerUrl": "ws://target-1"}
        with (
            patch.object(client, "_page_target", return_value=target),
            patch.object(client, "_install") as install,
            patch.object(client, "_send_update", return_value=False) as send,
            patch.object(client, "_clear_target_cache") as clear_target,
        ):
            self.assertFalse(client.update_payload({"payloadDomains": {}}))
            self.assertEqual(client.last_status, "failed")
            self.assertEqual(install.call_count, 2)
            clear_target.assert_called_once_with(clear_script=True)

            self.assertFalse(client.update_payload({"payloadDomains": {}}))
            self.assertEqual(client.last_status, "backoff")
            self.assertEqual(send.call_count, 2)
            self.assertEqual(install.call_count, 2)

    def test_send_update_does_not_fallback_after_persistent_binding_failure(self) -> None:
        client = RendererHudClient(port=9229, enabled=True)
        binding = MagicMock()
        binding.send_command.side_effect = TimeoutError("binding timed out")
        client._active_session_binding = binding
        with patch("codex_usage_hud.renderer_client.send_cdp_command") as send:
            self.assertFalse(
                client._send_update(
                    "ws://target-1",
                    {"payloadDomains": {}},
                )
            )
        send.assert_not_called()
        self.assertEqual(
            client.last_update_metrics["persistentFallbackReason"],
            "TimeoutError: binding timed out",
        )
        self.assertIsNone(client.last_update_metrics["fallbackMs"])

    def test_send_update_verifies_unacknowledged_persistent_response(self) -> None:
        client = RendererHudClient(port=9229, enabled=True)
        persistent = MagicMock(
            return_value={
                "result": {"result": {"value": {"ok": False, "applyMs": 0.0}}}
            }
        )
        client._active_session_binding = SimpleNamespace(send_command=persistent)
        verified = {
            "result": {"result": {"value": {"ok": True, "applyMs": 1.5}}}
        }

        with patch(
            "codex_usage_hud.renderer_client.send_cdp_command",
            return_value=verified,
        ) as verify:
            self.assertTrue(
                client._send_update(
                    "ws://target-1",
                    {"payloadDomains": {"settings": {}}},
                )
            )

        persistent.assert_called_once()
        verify.assert_called_once()
        self.assertEqual(
            client.last_update_metrics["transport"],
            "active-session-binding-verified",
        )
        self.assertEqual(
            client.last_update_metrics["persistentVerification"],
            "ephemeral-acknowledged",
        )
        self.assertGreater(client.last_update_metrics["fallbackMs"], 0.0)

    def test_update_payload_reports_failed_update_after_in_place_recovery_fails(self) -> None:
        client = RendererHudClient(port=9229, enabled=True)
        install_force_flags = []
        send_payloads = []

        client._page_target = lambda: {  # type: ignore[method-assign]
            "id": "target-1",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9229/devtools/page/1",
        }

        def install(websocket_url: str, target_id: str, *, force: bool = False) -> None:
            install_force_flags.append(force)
            client._script_identifier = "script-1"
            client._target_id = target_id
            client._websocket_url = websocket_url

        def send_update(websocket_url: str, payload: dict[str, object]) -> bool:
            send_payloads.append((websocket_url, dict(payload)))
            return False

        client._install = install  # type: ignore[method-assign]
        client._send_update = send_update  # type: ignore[method-assign]

        result = client.update_payload({"debug": True})

        self.assertFalse(result)
        self.assertEqual(install_force_flags, [False, True])
        self.assertEqual(len(send_payloads), 2)
        self.assertEqual(client.last_status, "failed")
        self.assertIn("renderer update function did not acknowledge payload", client.last_error)

    def test_renderer_active_session_detects_blank_titlebar_new_session(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("function activeSessionHeaderTitleText()", script)
        self.assertIn("function activeSessionComposerVisible()", script)
        self.assertIn("function activeSessionHeaderLooksNewSession(rows)", script)
        self.assertIn("activeSessionHeaderLooksNewSession(rows)", script)
        self.assertIn('matchedBy: "header-empty"', script)

    def test_renderer_active_session_marks_client_new_thread_as_pending(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("function activeSessionIdIsProvisional(value)", script)
        self.assertIn('normalized.startsWith("client-new-thread:")', script)
        self.assertIn("pendingSession: true", script)
        self.assertIn('reason: newSession ? "new-session" : (pendingSession ? "pending-session" : reason)', script)

    def test_renderer_active_session_reconciles_after_composer_send(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("activeSessionComposerHandlerName", script)
        self.assertIn("activeSessionComposerTarget", script)
        self.assertIn("if (!composerBadgeEnabled)", script)
        self.assertIn('scheduleActiveSessionSendFollowup("composer-send")', script)
        self.assertIn('scheduleActiveSessionSendFollowup("composer-submit")', script)
        self.assertIn('scheduleActiveSessionSendFollowup("composer-enter")', script)
        self.assertIn('activeSessionScope.listen(document, "submit", submit, true)', script)
        self.assertIn('activeSessionScope.listen(document, "keydown", keydown, true)', script)
        self.assertIn("keepFollowup = report();", script)
        self.assertIn("const delays = keepFollowup", script)
        self.assertIn(
            "? [32, 120, 320, 800, 1600, 3200, 5600, 9000]",
            script,
        )
        self.assertIn(': [32, 120, 320];', script)

    def test_renderer_active_session_filters_out_folder_rows(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("function activeSessionRowLooksThread(row)", script)
        self.assertIn(
            "sourceRow?.querySelector?.(activeSessionTitleSelector)", script
        )
        self.assertIn(
            ".filter((row) => activeSessionRowLooksThread(row))",
            script,
        )

    def test_renderer_active_session_container_prefers_identity_rows(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn(
            "Array.from(document.querySelectorAll(activeSessionIdentitySelector))",
            script,
        )
        self.assertIn(
            "titleNode?.closest?.(activeSessionRowSelector)",
            script,
        )

    def test_renderer_active_session_follows_composer_submit_from_new_session(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn(
            "function scheduleActiveSessionSendFollowup(reason = \"composer-send\", expectedSessionId = \"\")",
            script,
        )
        self.assertIn("keepFollowup = report();", script)
        self.assertIn("const delays = keepFollowup", script)
        self.assertIn("delays.map((ms) => ctx.lifecycle.timeout(", script)
        self.assertIn(
            "function activeSessionComposerSubmitButton(button)",
            script,
        )
        self.assertIn(
            'button.getAttribute("type")?.toLowerCase() === "submit"',
            script,
        )
        self.assertIn('button.getAttribute("aria-disabled") === "true"', script)
        self.assertIn(
            'scheduleActiveSessionSendFollowup("composer-enter")',
            script,
        )
        self.assertIn(
            'scheduleActiveSessionSendFollowup("composer-send-click")',
            script,
        )
        self.assertIn(
            'composerScope.listen(input, "keydown", handlers.keydown, true)',
            script,
        )
        self.assertIn(
            'scheduleActiveSessionSendFollowup("history")',
            script,
        )
        self.assertIn(
            'scheduleActiveSessionSendFollowup("popstate")',
            script,
        )

    def test_renderer_composer_watchers_do_not_redeclare_handlers_binding(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn(
            "const existingHandlers = window[composerInputHandlersName];",
            script,
        )

    def test_payload_hides_pre_send_estimate_and_activity_light_by_default(self) -> None:
        from codex_usage_hud.core.pre_send_estimator import BaseEstimate
        from codex_usage_hud.core.activity_monitor import ReadingActivity

        snapshot = ParsedSession(status="parsed")
        snapshot.estimate_base = BaseEstimate(total_tokens=152000)
        snapshot.reading_activity = ReadingActivity(
            active=True, file_name="ScanClient.cs", tool_name="read_file"
        )

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["preSendEstimate"], "")
        self.assertEqual(payload["preSendBaseTokens"], 0)
        self.assertEqual(payload["preSendBreakdown"], [])
        self.assertFalse(payload["preSendHasPrices"])
        self.assertIsNone(payload["preSendTotalCost"])
        self.assertFalse(payload["activityWarning"])
        self.assertEqual(payload["activityReadingFile"], "")

    def test_payload_activity_light_off_by_default(self) -> None:
        payload = payload_from_snapshot(ParsedSession(status="waiting")).to_json()
        self.assertFalse(payload["activityWarning"])
        self.assertEqual(payload["activityReadingFile"], "")

    def test_renderer_script_disables_pre_send_badge_by_default(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT
        self.assertIn("const composerBadgeEnabled = false;", script)
        self.assertIn('data-has-badge="${name === "request" && composerBadgeEnabled ? "true" : "false"}"', script)
        self.assertIn("if (!composerBadgeEnabled) return;", script)

    def test_payload_hides_pre_send_breakdown_rows_by_default(self) -> None:
        from codex_usage_hud.core.pre_send_estimator import BaseEstimate

        snapshot = ParsedSession(status="parsed")
        snapshot.estimate_base = BaseEstimate(
            total_tokens=48981,
            input_text_tokens=6,
            session_history_tokens=48000,
            context_files_tokens=920,
            mcp_schema_tokens=5,
            padding_tokens=50,
        )
        payload = payload_from_snapshot(snapshot).to_json()
        rows = payload["preSendBreakdown"]
        self.assertEqual(rows, [])
        self.assertFalse(payload["preSendHasPrices"])
        self.assertIsNone(payload["preSendTotalCost"])
        self.assertEqual(payload["preSendInputPrice"], 0.0)

    def test_payload_ignores_pre_send_cost_when_disabled(self) -> None:
        from codex_usage_hud.core.pre_send_estimator import BaseEstimate

        snapshot = ParsedSession(status="parsed")
        snapshot.estimate_base = BaseEstimate(
            session_history_tokens=10000,
            padding_tokens=50,
        ).with_pricing(
            input_price_per_token=5e-6,
            cached_price_per_token=0.5e-6,
            cache_hit_rate=0.8,
            model_name="gpt-5.5",
        )
        payload = payload_from_snapshot(snapshot).to_json()
        self.assertFalse(payload["preSendHasPrices"])
        self.assertIsNone(payload["preSendTotalCost"])
        self.assertEqual(payload["preSendInputPrice"], 0.0)
        self.assertEqual(payload["preSendBreakdown"], [])

    def test_payload_hides_attachment_rows_when_pre_send_disabled(self) -> None:
        from codex_usage_hud.core.pre_send_estimator import (
            AttachmentEstimate,
            BaseEstimate,
        )

        snapshot = ParsedSession(status="parsed")
        snapshot.estimate_base = BaseEstimate(
            input_text_tokens=6,
            session_history_tokens=1000,
            padding_tokens=50,
            attachments=AttachmentEstimate(
                image_tokens=300, image_count=2,
                file_tokens=5000, file_count=1,
                mention_tokens=10, mention_count=1,
                approximate=True,
            ),
        )
        payload = payload_from_snapshot(snapshot).to_json()
        self.assertEqual(payload["preSendBreakdown"], [])

    def test_renderer_script_collects_composer_attachments(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT
        # 采集与上报函数存在。
        self.assertIn("function collectComposerAttachments", script)
        self.assertIn("function reportComposerAttachments", script)
        self.assertIn("/composer-attachments", script)
        # 使用探针实测确认的稳定选择器。
        self.assertIn("composer-attachment-surface", script)
        self.assertIn("inline-mention-brand-aware", script)
        self.assertIn("file-attachment", script)
        self.assertIn("skills: []", script)
        self.assertIn("collectMentionOrSkillText", script)
        self.assertIn("attachments.skills", script)
        self.assertIn("looksLikeFileReferenceText", script)
        self.assertIn("defaultKind === \"mention\"", script)
        # mention chip 也要能带上 fiber 上的绝对路径 / 解析 [name](path) markdown。
        self.assertIn("readFiberFilePath(node)", script)
        self.assertIn("parseMarkdownFileRef", script)
        self.assertIn("pushMentionEntry", script)
        # ProseMirror atMention 节点的路径挂在 pmViewDesc.node.attrs 上。
        self.assertIn("pmViewDesc", script)
        self.assertIn("img[src^='blob:']", script)
        self.assertIn("img[src^='data:image']", script)
        self.assertIn("collectImageAttachment", script)
        self.assertIn("scheduleComposerBadgeUpdate", script)
        self.assertIn('ctx.frames.schedule("composer_badge"', script)
        self.assertIn("composerAttachmentsDebounceMs", script)
        self.assertIn("}, composerAttachmentsDebounceMs)", script)

    def test_payload_from_snapshot_exposes_overflow_progress_style(self) -> None:
        snapshot = ParsedSession(status="parsed")
        snapshot.today_tokens = 6600000
        snapshot.today_cost_usd = 112.0
        snapshot.daily_limit_usd = 100.0
        snapshot.week_tokens = 124700000
        snapshot.week_cost_usd = 128.0
        snapshot.weekly_limit_usd = 100.0

        payload = payload_from_snapshot(snapshot).to_json()

        top_progress = payload["topProgress"]
        collapsed = top_progress["collapsed"]
        day = collapsed[1]
        week = collapsed[2]
        self.assertEqual(day["label"], "今日 6.6M/$112.00")
        self.assertNotIn("compactLabel", day)
        self.assertNotIn("rightText", day)
        self.assertEqual(day["ratio"], 1.0)
        self.assertAlmostEqual(day["overflowRatio"], 0.12, places=3)
        self.assertEqual(day["overflowBadge"], "+12% / +$12.00")
        self.assertEqual(day["overflowBadgeCompact"], "+$12.00")
        self.assertEqual(day["overflowBadgeIcon"], "🚨")
        self.assertEqual(week["label"], "本周 124.7M/$128.00")
        self.assertNotIn("compactLabel", week)
        self.assertNotIn("rightText", week)
        self.assertEqual(week["ratio"], 1.0)
        self.assertAlmostEqual(week["overflowRatio"], 0.28, places=3)
        self.assertEqual(week["overflowBadge"], "+28% / +$28.00")
        self.assertEqual(week["overflowBadgeCompact"], "+$28.00")
        self.assertNotIn("overflowBadgeIcon", week)

        budget_day = top_progress["budget"][0]
        budget_week = top_progress["budget"][1]
        self.assertEqual(budget_day["label"], "今日 6.6M/$112.00")
        self.assertNotIn("compactLabel", budget_day)
        self.assertNotIn("rightText", budget_day)
        self.assertAlmostEqual(budget_day["overflowRatio"], 0.12, places=3)
        self.assertEqual(budget_day["overflowBadge"], "+12% / +$12.00")
        self.assertEqual(budget_day["overflowBadgeCompact"], "+$12.00")
        self.assertEqual(budget_day["overflowBadgeIcon"], "🚨")
        self.assertNotIn("rightText", budget_week)
        self.assertAlmostEqual(budget_week["overflowRatio"], 0.28, places=3)
        self.assertEqual(budget_week["overflowBadge"], "+28% / +$28.00")
        self.assertEqual(budget_week["overflowBadgeCompact"], "+$28.00")
        self.assertNotIn("overflowBadgeIcon", budget_week)

    def test_renderer_top_redesign_styles_are_theme_tokenized(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('const version = "69";', script)
        self.assertIn("function refreshProgressRailBadge", script)
        self.assertIn("function progressBadgeCandidates", script)
        self.assertIn("function progressRailLeftLabelFits", script)
        self.assertIn("if (progressRailLeftLabelFits(rail))", script)
        self.assertNotIn("max-width: min(52%, 156px)", script)
        self.assertIn("codex-usage-hud-progress-badge-icon", script)
        self.assertIn("codex-usage-hud-progress-badge-copy", script)
        self.assertIn(
            'rail.style.setProperty("--codex-usage-hud-progress-overflow-width", overflowWidth)',
            script,
        )
        self.assertIn(
            'rail.style.setProperty("--codex-usage-hud-progress-badge-pad"',
            script,
        )
        self.assertRegex(
            script,
            r"\.codex-usage-hud-progress-overflow \{[^}]*right: 0;[^}]*bottom: 0;[^}]*height: 4px;",
        )
        self.assertRegex(
            script,
            r"\.codex-usage-hud-progress-overflow-anchor \{[^}]*bottom: 0;[^}]*width: 7px;[^}]*height: 7px;",
        )
        self.assertRegex(
            script,
            r"\.codex-usage-hud-progress-badge \{[^}]*top: 2px;[^}]*min-height: 16px;",
        )
        self.assertNotIn(".codex-usage-hud-progress-badge::before", script)
        self.assertIn("function applyCachedActiveSessionPayload", script)
        self.assertIn("function cacheActiveSessionPayload", script)
        self.assertIn("if (payload?.cachedPreview) return;", script)
        self.assertIn("activeSessionPayloadKeys(ref).map((key) => cache.get(key))", script)
        self.assertIn("window.__codexUsageHudCachedPreview", script)
        self.assertIn("restoredActiveSessionSelectionSeq", script)
        self.assertIn(
            "previousActiveSessionSelectionSeq,\n    previousActiveSessionAppliedSeq",
            script,
        )
        self.assertIn("function applyActiveSessionSequence", script)
        current_apply = script.index("function applyCurrentSessionPayload")
        session_apply = script.index("function applySessionSwitchPayload")
        self.assertIn(
            "applyActiveSessionSequence(payload);",
            script[current_apply:session_apply],
        )
        self.assertIn(
            "applyActiveSessionSequence(payload);",
            script[session_apply:],
        )
        self.assertIn(
            "appliedSeq > Number(window[activeSessionSelectionSeqName] || 0)",
            script,
        )
        self.assertIn(
            "scrollbar-color: var(--codex-usage-hud-divider) var(--codex-usage-hud-surface);",
            script,
        )
        self.assertRegex(
            script,
            r"#\$\{rootId\} \.codex-usage-hud-activity-main,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-activity-step,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-activity-metric,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-session-insight,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-heavy-round,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-token-chip \{\s+"
            r"background: var\(--codex-usage-hud-request-panel-surface\);",
        )
        self.assertRegex(
            script,
            r"#\$\{rootId\} \.codex-usage-hud-chip \{\s+"
            r"background: var\(--codex-usage-hud-header-surface\);\s+"
            r"color: var\(--codex-usage-hud-text\);",
        )
        self.assertRegex(
            script,
            r"#\$\{rootId\} \.codex-usage-hud-stat-label,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-section-title,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-token-chip span:first-child,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-heavy-round-detail,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-activity-node-time,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-activity-node-detail,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-session-composition,\s+"
            r"#\$\{rootId\} \.codex-usage-hud-value\.muted \{\s+"
            r"color: var\(--codex-usage-hud-muted\);",
        )
        self.assertIn(
            "background: color-mix(in srgb, var(--codex-usage-hud-warning) 13%, var(--codex-usage-hud-panel-surface));",
            script,
        )
        self.assertIn(
            "box-shadow: 0 0 0 3px color-mix(in srgb, var(--codex-usage-hud-accent) 16%, transparent);",
            script,
        )
        self.assertGreater(
            script.rfind("background: var(--codex-usage-hud-request-panel-surface);"),
            script.index("background: #101821;"),
        )

    def test_payload_warning_summary_uses_ratio_and_threshold_only(self) -> None:
        now = datetime(2026, 6, 5, 13, 10, 11).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            refreshed_at=now,
            last_event_time=now,
        )
        snapshot.today_cost_usd = 59.12
        snapshot.week_cost_usd = 210.0
        snapshot.daily_limit_usd = 85.0
        snapshot.weekly_limit_usd = 400.0
        snapshot.budget_warnings = [
            "日额度已用 59.12/85 USD (70%)，超过 50% 阈值",
            "周额度已用 210.00/400 USD (52%)，超过 50% 阈值",
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["topDetails"]["warnings"], "预警  日已用 70%，超过 50% 阈值；周已用 52%，超过 50% 阈值")

    def test_error_snapshot_uses_single_collapsed_top_progress_rail(self) -> None:
        snapshot = ParsedSession(status="missing", error="session file unavailable")

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertIn("未找到", payload["topLine"])
        top_progress = payload["topProgress"]
        self.assertEqual(top_progress["budget"], [])
        self.assertEqual(len(top_progress["collapsed"]), 1)
        self.assertEqual(top_progress["collapsed"][0]["tone"], "error")
        self.assertEqual(top_progress["collapsed"][0]["ratio"], 1.0)

    def test_support_qr_assets_are_available_for_renderer_payload(self) -> None:
        images = support_qr_payload()
        paths = support_qr_asset_paths()

        self.assertEqual([item["key"] for item in images], ["alipay", "wechat"])
        self.assertEqual([item["key"] for item in paths], ["alipay", "wechat"])
        self.assertTrue(images[0]["src"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(images[1]["src"].startswith("data:image/jpeg;base64,"))
        self.assertGreater(len(images[0]["src"]), 1000)
        self.assertGreater(len(images[1]["src"]), 1000)
        self.assertTrue(Path(paths[0]["path"]).exists())
        self.assertTrue(Path(paths[1]["path"]).exists())

    def test_rest_reminder_settings_use_product_copy_and_live_countdown(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("休息提醒", script)
        self.assertIn("本轮", script)
        self.assertIn("data-rest-reminder-start-time", script)
        self.assertIn("data-rest-reminder-status-title", script)
        self.assertIn("data-rest-reminder-remaining", script)
        self.assertIn("data-rest-reminder-prompt-elapsed", script)
        self.assertIn("promptStartedAtMs", script)
        self.assertIn("restReminderPromptElapsedText", script)
        self.assertIn('bubbleVisible && phase === "prompt"', script)
        self.assertIn("restReminderStatusTitle", script)
        self.assertIn('"rest_reminder_settings"', script)
        self.assertIn('startInput.dataset.userEdited !== "true"', script)
        self.assertIn(
            "formatRestReminderInputTime(Number(timing?.timerStartedAtMs))",
            script,
        )
        self.assertIn(
            'data-setting-key="rest_reminder_interval_minutes" type="number" min="1" max="180"',
            script,
        )
        self.assertIn(
            "Math.max(1, Math.round(Number(settings.rest_reminder_interval_minutes)",
            script,
        )
        self.assertRegex(
            script,
            r'"rest_reminder_interval_minutes",\s*Number\(settings\.rest_reminder_interval_minutes\) \|\| 45,\s*1,\s*180,',
        )
        self.assertIn('data-setting-key="rest_reminder_break_minutes"', script)
        self.assertIn('case "break": return "休息中"', script)
        self.assertNotIn("离开多久算作休息", script)
        self.assertNotIn('data-setting-key="rest_reminder_idle_reset_minutes"', script)
        self.assertIn(
            "grid-template-columns: repeat(4, minmax(0, 1fr));",
            script,
        )
        self.assertIn(
            "今日已休息 ${formatRestReminderRemaining(todayRestedSeconds)} 共${todayRestedCount}次",
            script,
        )
        self.assertIn("restReminderSummaryText", script)
        self.assertIn("todayRestedCount", script)
        self.assertIn('data-rest-reminder-summary="true"', script)
        self.assertNotIn('notification.status === "sent" ? "通知 OK" : "待测试"', script)
        self.assertIn("rest_reminder_work_start_time", script)
        self.assertIn("rest_reminder_lunch_enabled", script)
        self.assertIn("rest-reminder-test-notification", script)
        self.assertIn("打开实际提醒预览", script)
        self.assertLess(
            script.index("codex-usage-hud-rest-reminder-card"),
            script.index("codex-usage-hud-support-qr-grid"),
        )
        self.assertNotIn("离开后重新计时", script)
        self.assertNotIn("到点会优先弹出 PySide6", script)

    def test_rest_reminder_start_time_only_applies_after_user_edit(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('restStartInput.dataset.userEdited = "true"', script)
        self.assertIn(
            'startNode?.value && startNode.dataset.userEdited === "true"',
            script,
        )
        self.assertIn("rest_reminder_timer_started_at_ms = started.getTime()", script)
        self.assertIn("status.restReminderSaved === true", script)
        self.assertIn("restReminderSaveRequestId", script)
        self.assertIn("delete startNode.dataset.userEdited", script)

    def test_settings_close_button_has_large_hit_target(self) -> None:
        """Settings modal X should stay easy to click, not glyph-sized."""
        script = renderer_hud.RENDERER_HUD_SCRIPT

        css_start = script.index(".codex-usage-hud-settings-close {")
        css_end = script.index("}", css_start)
        css = script[css_start:css_end]
        self.assertIn("width: 32px", css)
        self.assertIn("height: 32px", css)
        self.assertIn("min-width: 32px", css)
        self.assertIn("min-height: 32px", css)
        self.assertIn("pointer-events: auto", css)
        self.assertIn("-webkit-app-region: no-drag", css)
        self.assertIn("inline-flex", css)
        self.assertIn('data-action="settings-close"', script)
        self.assertIn('aria-label="关闭"', script)

    def test_rest_reminder_toast_receives_pointer_events(self) -> None:
        """HUD root is pointer-events:none; toast + buttons must re-enable hits."""
        script = renderer_hud.RENDERER_HUD_SCRIPT

        toast_css_start = script.index(".codex-usage-hud-rest-toast {")
        toast_css_end = script.index("}", toast_css_start)
        toast_css = script[toast_css_start:toast_css_end]
        self.assertIn("pointer-events: auto", toast_css)

        mask_css_start = script.index(".codex-usage-hud-rest-mask {")
        mask_css_end = script.index("}", mask_css_start)
        mask_css = script[mask_css_start:mask_css_end]
        self.assertIn("pointer-events: auto", mask_css)
        self.assertIn("position: fixed", mask_css)
        self.assertIn("inset: 0", mask_css)
        self.assertIn("backdrop-filter", mask_css)

        # Interactive whitelist also includes the toast class so children inherit.
        whitelist_start = script.index(".codex-usage-hud-settings-modal,")
        whitelist_end = script.index("pointer-events: auto;", whitelist_start)
        whitelist = script[whitelist_start:whitelist_end]
        self.assertIn("codex-usage-hud-rest-toast", whitelist)
        self.assertIn("codex-usage-hud-rest-mask", whitelist)
        self.assertIn("codex-usage-hud-rest-bubble", whitelist)

        self.assertIn('data-rest-reminder-mask="true"', script)
        self.assertIn("codex-usage-hud-rest-toast-icon", script)
        self.assertIn("codex-usage-hud-rest-toast-hint", script)
        self.assertIn('aria-live="assertive"', script)
        self.assertIn('data-rest-reminder-break-countdown="true"', script)
        self.assertIn('"rest_reminder_overlay"', script)
        self.assertIn("stopRestReminderOverlayTicker()", script)
        self.assertIn('toast.dataset.visible = "false"', script)
        self.assertIn("等待你的选择 · 不会自动跳过本次休息", script)
        self.assertIn('data-action="rest-reminder-credit"', script)
        self.assertIn('data-minutes="3"', script)
        self.assertIn('data-minutes="5"', script)
        self.assertIn('data-minutes="10"', script)
        self.assertIn('data-action="rest-reminder-credit-more"', script)
        self.assertIn('action: "restReminderCredit"', script)
        self.assertIn("rest-reminder-credit-custom-input", script)
        self.assertIn("rest-reminder-credit-custom-confirm", script)
        self.assertIn("rest-reminder-credit-custom-cancel", script)
        self.assertNotIn("window.prompt", script)
        self.assertIn("promptWaitInfinite === true", script)
        self.assertIn("专注休息", script)
        self.assertIn('data-action="rest-reminder-ack"', script)
        self.assertIn('data-action="rest-reminder-start"', script)
        self.assertIn('action: "rest-reminder-finish"', script)
        self.assertIn('data-action="rest-reminder-postpone"', script)
        self.assertIn('action: "restReminderAck"', script)
        self.assertIn('action: "restReminderStart"', script)
        self.assertIn('action: "restReminderFinish"', script)
        self.assertIn('action: "restReminderPostpone"', script)

    def test_rest_reminder_renderer_bubble_is_pyside_missing_composer_fallback(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('data-rest-reminder-bubble="true"', script)
        self.assertIn("desktopOverlayDependency().installed === false", script)
        self.assertIn("reminder?.bubbleVisible === true", script)
        self.assertIn("function positionRestReminderBubble", script)
        self.assertIn("const composer = composerRect()", script)
        self.assertIn("positionRestReminderBubble(root)", script)
        self.assertIn('ctx.lifecycle.frame("rest_reminder", () => positionRestReminderBubble(root))', script)
        self.assertIn("休息已延迟", script)
        self.assertIn("本次已休息", script)
        self.assertIn("今日累计", script)
        self.assertIn("提前结束", script)
        self.assertNotIn("setInterval(positionRestReminderBubble", script)

    def test_payload_can_include_support_qr_images(self) -> None:
        snapshot = ParsedSession(status="waiting")
        images = support_qr_payload()

        payload = payload_from_snapshot(snapshot, support_images=images).to_json()

        self.assertEqual(payload["supportImages"][0]["label"], "支付宝")
        self.assertEqual(payload["supportImages"][1]["label"], "微信赞赏")
        self.assertTrue(payload["supportImages"][0]["src"].startswith("data:image/jpeg;base64,"))

    def test_settings_domain_update_does_not_emit_empty_support_images(self) -> None:
        snapshot = ParsedSession(status="waiting")

        payload = payload_from_snapshot(snapshot, support_images=[]).to_domain_json("settings")

        self.assertNotIn("supportImages", payload)
        self.assertNotIn("supportImages", payload["payloadDomains"]["settings"])

    def test_session_index_progress_is_carried_by_the_settings_domain(self) -> None:
        snapshot = ParsedSession(status="waiting")

        payload = payload_from_snapshot(
            snapshot,
            session_index={
                "coverage": "partial(1m)",
                "jobState": "attached",
                "builtCount": 4,
                "totalCount": 10,
                "selectedRange": "1m",
            },
        ).to_domain_json("settings", "sessionCleanup")

        self.assertEqual(payload["sessionIndex"]["builtCount"], 4)
        self.assertEqual(
            payload["payloadDomains"]["settings"]["sessionIndex"]["jobState"],
            "attached",
        )

    def test_settings_domain_update_does_not_emit_empty_theme(self) -> None:
        snapshot = ParsedSession(status="waiting")

        payload = payload_from_snapshot(snapshot).to_domain_json("settings")

        self.assertNotIn("theme", payload)
        self.assertNotIn("theme", payload["payloadDomains"]["settings"])

    def test_settings_domain_update_keeps_non_empty_theme(self) -> None:
        snapshot = ParsedSession(status="waiting")

        payload = payload_from_snapshot(
            snapshot,
            theme={"variant": "light", "tokens": {"surface": "#ffffff"}},
        ).to_domain_json("settings")

        self.assertEqual(payload["theme"]["variant"], "light")
        self.assertEqual(
            payload["payloadDomains"]["settings"]["theme"]["tokens"]["surface"],
            "#ffffff",
        )

    def test_renderer_theme_guard_preserves_theme_for_partial_settings_updates(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("const themePayload = payload?.theme;", script)
        self.assertIn("Object.keys(themePayload).length === 0", script)
        self.assertIn(".codex-usage-hud-settings-dialog", script)
        self.assertIn("var(--codex-usage-hud-surface", script)

    def test_renderer_script_listens_for_live_theme_changes(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT
        theme_section = script.split("function rendererThemeSnapshot()", 1)[1].split(
            "function normalizeThreadId(value)",
            1,
        )[0]

        self.assertIn('const themeBindingName = "codexUsageHudTheme";', script)
        self.assertIn("new MutationObserver", script)
        self.assertIn('attributeFilter: ["class", "style", "data-theme", "data-color-scheme"]', script)
        self.assertIn("matchMedia?.(\"(prefers-color-scheme: dark)\")", script)
        self.assertIn("window.__codexUsageHudReportTheme", script)
        self.assertNotIn("setInterval(", theme_section)

    def test_renderer_theme_observer_is_owned_by_theme_lifecycle(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT
        layout_section = script.split("function refreshLayoutObservers()", 1)[1].split(
            "function stopBootstrapObserver()",
            1,
        )[0]
        stale_section = script.split("function scheduleStaleGuard(payload)", 1)[1].split(
            "function normalizePayloadDomains(payload)",
            1,
        )[0]
        remove_section = script.split("window.__codexUsageHudRemove = ", 1)[1].split(
            "window[scheduleName] =",
            1,
        )[0]

        self.assertNotIn("themeObserverName", layout_section)
        self.assertNotIn("themeMediaQueryName", layout_section)
        self.assertNotIn("themeStorageHandlerName", layout_section)
        self.assertNotIn("themeTimerName", stale_section)
        self.assertIn("ctx.teardown.run();", remove_section)
        self.assertIn('ctx.teardown.add("theme", stopRendererThemeObserver);', script)
        self.assertIn("ctx.lifecycle.clearTimeout(window[themeTimerName] || 0);", script)

    def test_renderer_theme_snapshot_prefers_current_codex_chrome_tokens(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn(
            'accent: colorValue("--codex-base-accent", "--color-text-accent", "--vscode-focusBorder"',
            script,
        )
        self.assertIn(
            'surface: colorValue("--codex-base-surface", "--color-background-surface", "--vscode-editor-background"',
            script,
        )
        self.assertIn(
            'ink: colorValue("--codex-base-ink", "--color-text-foreground", "--vscode-editor-foreground"',
            script,
        )
        self.assertIn(
            'diffAdded: colorValue("--color-decoration-added", "--vscode-gitDecoration-addedResourceForeground"',
            script,
        )
        self.assertIn(
            'diffRemoved: colorValue("--color-decoration-deleted", "--vscode-gitDecoration-deletedResourceForeground"',
            script,
        )
        self.assertIn(
            'skill: colorValue("--color-accent-purple", "--vscode-terminal-ansiMagenta"',
            script,
        )

    def test_renderer_theme_binding_converts_probe_payload(self) -> None:
        captured: list[dict[str, object]] = []
        client = RendererHudClient(enabled=False)
        try:
            client.set_theme_callback(captured.append)
            client._handle_theme_binding_payload(
                {
                    "mode": "light",
                    "effectiveVariant": "light",
                    "cssTheme": {
                        "accent": "#0969da",
                        "surface": "#ffffff",
                        "ink": "#24292f",
                        "diffAdded": "#1a7f37",
                        "diffRemoved": "#cf222e",
                        "skill": "#8250df",
                    },
                }
            )
        finally:
            client.close()

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["source"], "cdp")
        self.assertEqual(captured[0]["variant"], "light")
        self.assertEqual(captured[0]["tokens"]["surface"], "#ffffff")

    def test_renderer_script_persists_support_images_across_page_reinject(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('supportImagesStorageKey = "codexUsageHudSupportImages:v1"', script)
        self.assertIn("function loadPersistedSupportImages()", script)
        self.assertIn("function persistSupportImages(images)", script)
        self.assertIn("const persistedSupportImages = loadPersistedSupportImages();", script)
        self.assertIn("nextPayload.supportImages = persistedSupportImages;", script)

    def test_payload_marks_request_errors_for_red_renderer_bubble(self) -> None:
        snapshot = ParsedSession(
            request=RequestTokens(
                status="error",
                error="exceeded retry limit, last status: 429 Too Many Requests",
            )
        )
        self.assertIn('action: "loadMoreRequestRows"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('list.addEventListener("scroll"', renderer_hud.RENDERER_HUD_SCRIPT)

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["requestStatus"], "error")
        self.assertIn("429 Too Many Requests", str(payload["requestLine"]))
        self.assertTrue(payload["warning"])
        self.assertIn("codex-usage-hud-error", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("node.classList.toggle(errorClass", renderer_hud.RENDERER_HUD_SCRIPT)

    def test_payload_exposes_top_copy_targets_and_live_request_row_details(self) -> None:
        started_at = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        completed_at = datetime(2026, 6, 5, 13, 0, 5).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            request=RequestTokens(status="running", model="gpt-5.5"),
            slow=SlowSummary(
                slowest_tool_call=ToolCallTiming(
                    call_id="call_1",
                    name="shell_command",
                    args='{"command":"git status","timeout_ms":1000}',
                    start=started_at,
                    start_line=3,
                    end=completed_at,
                    end_line=4,
                ),
                longest_gap_detail=GapTiming(
                    start=started_at,
                    end=completed_at,
                    duration_seconds=5.0,
                    category="model_or_idle",
                    from_event="user:开始",
                    to_event="assistant:结束",
                    start_line=10,
                    end_line=11,
                ),
            ),
        )
        snapshot.request_history = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=100,
                cached_tokens=10,
                output_tokens=20,
                reasoning_tokens=0,
                total_tokens=120,
                estimated=False,
                cost_usd=0.01,
                started_at=started_at,
                completed_at=completed_at,
            ),
            RequestRound(
                index=2,
                status="running",
                model="gpt-5.5",
                input_tokens=200,
                cached_tokens=50,
                output_tokens=10,
                reasoning_tokens=0,
                total_tokens=210,
                estimated=True,
                cost_usd=0.02,
                started_at=started_at,
            ),
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["topCopies"]["slow"], "git status")
        self.assertIn("类型: 模型思考", payload["topCopies"]["gap"])
        self.assertIn("行号: 10 -> 11", payload["topCopies"]["gap"])
        top_details = payload["topDetails"]
        self.assertEqual(top_details["slow"], "最慢工具:5.0s")
        self.assertEqual(top_details["gap"], "最长等待:5.0s")
        trail_titles = [str(item["title"]) for item in top_details["activityTrail"]]
        self.assertTrue(any("工具调用" in title for title in trail_titles))
        self.assertTrue(any("等待结束" in title for title in trail_titles))
        self.assertIn("shell_command", str(top_details["activityTrail"]))
        details = payload["requestRowDetails"]
        self.assertEqual(details[0]["prefix"].strip(), "#2 ~$0.020")
        self.assertTrue(details[0]["running"])
        self.assertTrue(str(details[0]["time"]).strip().endswith("s"))
        self.assertTrue(str(details[0]["startedAt"]))
        self.assertFalse(details[1]["running"])
        self.assertEqual(details[1]["time"], "13:00:05")

    def test_activity_trail_uses_current_rounds_and_filters_old_tool_events(self) -> None:
        task_started_at = datetime(2026, 6, 5, 9, 9, 0).astimezone()
        old_started_at = datetime(2026, 6, 4, 22, 38, 4).astimezone()
        old_completed_at = datetime(2026, 6, 4, 22, 38, 24).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            task_started_at=task_started_at,
            request=RequestTokens(status="confirmed", model="gpt-5.5"),
            slow=SlowSummary(
                slowest_tool_call=ToolCallTiming(
                    call_id="old",
                    name="shell_command",
                    args='{"command":"python -m unittest old"}',
                    start=old_started_at,
                    start_line=3,
                    end=old_completed_at,
                    end_line=4,
                )
            ),
        )
        snapshot.request_history = [
            RequestRound(
                index=index,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=100 + index,
                cached_tokens=10,
                output_tokens=20,
                reasoning_tokens=0,
                total_tokens=120 + index,
                estimated=False,
                cost_usd=0.001,
                started_at=task_started_at,
                completed_at=datetime(2026, 6, 5, 9, 9, index).astimezone(),
                activity_summary=f"输入：当前需求第 {index} 轮",
                copy_text=f"输入：当前需求第 {index} 轮",
            )
            for index in range(1, 30)
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        trail = payload["topDetails"]["activityTrail"]
        round_nodes = [item for item in trail if str(item["title"]).startswith("轮次 #")]
        self.assertEqual(len(round_nodes), 29)
        self.assertNotIn("22:38", str(trail))
        self.assertIn("当前需求第 29 轮", str(trail[0]))

    def test_payload_formats_heavy_rounds_as_copyable_single_line_reason(self) -> None:
        started_at = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        completed_at = datetime(2026, 6, 5, 13, 0, 31).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            task_prompt="分析一个很大的日志文件",
            request=RequestTokens(status="confirmed", model="gpt-5.5"),
        )
        snapshot.session_request_history = [
            RequestRound(
                index=28,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=208000,
                cached_tokens=3840,
                output_tokens=1232,
                reasoning_tokens=0,
                total_tokens=210000,
                estimated=False,
                cost_usd=1.06,
                started_at=started_at,
                completed_at=completed_at,
                activity_summary="输入：分析一个很大的日志文件",
                copy_text="输入：\n分析一个很大的日志文件",
                activity_texts=("分析一个很大的日志文件", "工具调用 shell"),
            ),
            RequestRound(
                index=7,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=20,
                cached_tokens=0,
                output_tokens=10,
                reasoning_tokens=0,
                total_tokens=30,
                estimated=False,
                cost_usd=0.01,
            ),
        ]
        snapshot.request_history = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=10,
                cached_tokens=0,
                output_tokens=5,
                reasoning_tokens=0,
                total_tokens=15,
                estimated=False,
                cost_usd=0.005,
            )
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        heavy = payload["topDetails"]["heavyRounds"][0]
        self.assertEqual(heavy["title"], "Req1-#28 $1.06 · ∑210k")
        self.assertEqual(heavy["taskPrompt"], "分析一个很大的日志文件")
        self.assertEqual(heavy["detail"], "输入：分析一个很大的日志文件")
        self.assertIn("分析一个很大的日志文件", heavy["copyText"])
        self.assertEqual(
            heavy["locateTexts"],
            ["分析一个很大的日志文件", "工具调用 shell"],
        )
        self.assertNotIn("gpt-5.5", heavy["detail"])
        self.assertNotIn("已确认", heavy["detail"])

    def test_payload_marks_heavy_rounds_of_rolled_back_tasks(self) -> None:
        started_at = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        completed_at = datetime(2026, 6, 5, 13, 0, 31).astimezone()
        snapshot = ParsedSession(
            session_id="session-rollback-123456",
            status="parsed",
            task_prompt="继续",
            request=RequestTokens(status="confirmed", model="gpt-5.5"),
        )
        snapshot.activity_tasks = [
            TaskHistory(
                index=1,
                count=2,
                turn_id="turn-retry",
                prompt="继续",
                rolled_back=True,
                started_at=started_at,
                completed_at=completed_at,
                request=RequestTokens(status="confirmed", model="gpt-5.5"),
                request_history=[
                    RequestRound(
                        index=1,
                        status="confirmed",
                        model="gpt-5.5",
                        input_tokens=208000,
                        cached_tokens=3840,
                        output_tokens=1232,
                        reasoning_tokens=0,
                        total_tokens=210000,
                        estimated=False,
                        cost_usd=1.06,
                        started_at=started_at,
                        completed_at=completed_at,
                    )
                ],
            ),
            TaskHistory(
                index=2,
                count=2,
                turn_id="turn-live",
                prompt="继续",
                started_at=completed_at,
                request=RequestTokens(status="confirmed", model="gpt-5.5"),
                request_history=[
                    RequestRound(
                        index=1,
                        status="confirmed",
                        model="gpt-5.5",
                        input_tokens=20,
                        cached_tokens=0,
                        output_tokens=10,
                        reasoning_tokens=0,
                        total_tokens=30,
                        estimated=False,
                        cost_usd=0.01,
                        started_at=completed_at,
                    )
                ],
            ),
        ]

        heavy = payload_from_snapshot(snapshot).to_json()["topDetails"]["heavyRounds"]

        self.assertTrue(heavy[0]["rolledBack"])
        self.assertIn("已回滚", heavy[0]["title"])
        self.assertIn("已被回滚", heavy[0]["tooltip"])
        self.assertFalse(heavy[1]["rolledBack"])
        self.assertNotIn("已回滚", heavy[1]["title"])

    def test_payload_switches_activity_card_to_completed_task_stats(self) -> None:
        started_at = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        completed_at = datetime(2026, 6, 5, 13, 0, 42).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            task_prompt="把右侧活动面板改成完成态统计",
            task_index=4,
            task_count=4,
            task_started_at=started_at,
            task_completed_at=completed_at,
            last_output=Activity(
                kind="agent",
                detail="已完成 HUD 布局和统计字段调整。",
                timestamp=completed_at,
            ),
            request=RequestTokens(status="confirmed", model="gpt-5.5"),
        )
        snapshot.request_history = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=100,
                cached_tokens=80,
                output_tokens=20,
                reasoning_tokens=0,
                total_tokens=120,
                estimated=False,
                cost_usd=0.001,
                started_at=started_at,
                completed_at=completed_at,
            ),
            RequestRound(
                index=2,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=50,
                cached_tokens=40,
                output_tokens=10,
                reasoning_tokens=0,
                total_tokens=60,
                estimated=False,
                cost_usd=0.002,
                started_at=started_at,
                completed_at=completed_at,
            ),
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        top_details = payload["topDetails"]
        self.assertEqual(top_details["executingLabel"], "完成任务")
        self.assertEqual(top_details["taskOrdinal"], "共4次需求")
        self.assertEqual(top_details["taskOrdinalSession"], "共4次需求")
        self.assertEqual(top_details["taskOrdinalActivity"], "")
        self.assertEqual(top_details["executing"], "已完成 HUD 布局和统计字段调整。")
        self.assertEqual(top_details["currentTaskLabel"], "当前需求")
        self.assertEqual(top_details["currentTask"], "把右侧活动面板改成完成态统计")
        self.assertEqual(top_details["activityElapsedLabel"], "已处理")
        self.assertEqual(top_details["activityElapsed"], "42.0s")
        self.assertEqual(top_details["activityGapLabel"], "处理轮次")
        self.assertEqual(top_details["activityGap"], "2轮")
        self.assertEqual(top_details["activityLastLabel"], "处理花费")
        self.assertEqual(top_details["activityLast"], "$0.003")
        self.assertEqual(top_details["activityLastTooltip"], "180Tokens/$0.003/80%")

    def test_payload_exposes_completed_task_activity_history_for_navigation(self) -> None:
        started = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        first_done = datetime(2026, 6, 5, 13, 0, 10).astimezone()
        second_start = datetime(2026, 6, 5, 13, 0, 11).astimezone()
        second_done = datetime(2026, 6, 5, 13, 0, 25).astimezone()
        snapshot = ParsedSession(
            session_id="session-history-123",
            session_title="History",
            status="parsed",
            task_index=2,
            task_count=2,
            task_prompt="第二需求",
            task_started_at=second_start,
            task_completed_at=second_done,
            request=RequestTokens(status="confirmed", model="gpt-5.5"),
        )
        snapshot.request_history = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=30,
                cached_tokens=10,
                output_tokens=5,
                reasoning_tokens=0,
                total_tokens=35,
                estimated=False,
                cost_usd=0.02,
                started_at=second_start,
                completed_at=second_done,
            )
        ]
        snapshot.activity_tasks = [
            TaskHistory(
                index=1,
                count=2,
                turn_id="turn-1",
                prompt="第一需求",
                started_at=started,
                completed_at=first_done,
                request=RequestTokens(status="confirmed", model="gpt-5.5"),
                request_history=[
                    RequestRound(
                        index=1,
                        status="confirmed",
                        model="gpt-5.5",
                        input_tokens=100,
                        cached_tokens=50,
                        output_tokens=10,
                        reasoning_tokens=0,
                        total_tokens=110,
                        estimated=False,
                        cost_usd=0.01,
                        started_at=started,
                        completed_at=first_done,
                    )
                ],
            ),
            TaskHistory(
                index=2,
                count=2,
                turn_id="turn-2",
                prompt="第二需求",
                started_at=second_start,
                completed_at=second_done,
                request=snapshot.request,
                request_history=snapshot.request_history,
            ),
        ]

        details = payload_from_snapshot(snapshot).to_json()["topDetails"]
        self.assertEqual(details["activityTaskIndex"], 2)
        self.assertEqual(details["activityTaskCount"], 2)
        self.assertTrue(details["activityTaskNavigable"])
        self.assertEqual(
            [(item["taskOrdinal"], item["currentTask"]) for item in details["activityTasks"]],
            [("Req 1/2", "第一需求"), ("Req 2/2", "第二需求")],
        )
        self.assertEqual(details["activityTasks"][0]["activityGap"], "1轮")
        self.assertEqual(details["activityTasks"][1]["activityGap"], "1轮")
        self.assertEqual(
            [item["turnId"] for item in details["activityTasks"]],
            ["turn-1", "turn-2"],
        )
        self.assertEqual(
            [
                (item["taskIndex"], item["taskTurnId"])
                for item in details["heavyRounds"]
            ],
            [(2, "turn-2"), (1, "turn-1")],
        )

    def test_payload_keeps_activity_task_navigation_enabled_while_processing(self) -> None:
        first_start = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        first_done = datetime(2026, 6, 5, 13, 0, 10).astimezone()
        current_start = datetime(2026, 6, 5, 13, 0, 11).astimezone()
        snapshot = ParsedSession(
            session_id="session-running-history-123",
            status="parsed",
            task_index=2,
            task_count=2,
            task_prompt="处理中需求",
            task_started_at=current_start,
            request=RequestTokens(status="running", model="gpt-5.5"),
        )
        snapshot.activity_tasks = [
            TaskHistory(
                index=1,
                count=2,
                turn_id="turn-1",
                prompt="已完成需求",
                started_at=first_start,
                completed_at=first_done,
            ),
            TaskHistory(
                index=2,
                count=2,
                turn_id="turn-2",
                prompt="处理中需求",
                started_at=current_start,
                request=snapshot.request,
            ),
        ]

        details = payload_from_snapshot(snapshot).to_json()["topDetails"]

        self.assertEqual(details["activityTasks"][1]["executingLabel"], "正在执行")
        self.assertTrue(details["activityTaskNavigable"])
        self.assertEqual(
            [item["turnId"] for item in details["activityTasks"]],
            ["turn-1", "turn-2"],
        )

    def test_payload_merges_same_second_activity_nodes_and_suppresses_token_details(self) -> None:
        started_at = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        completed_at = datetime(2026, 6, 5, 13, 0, 42).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            task_prompt="完成右侧活动轨迹合并",
            task_started_at=started_at,
            task_completed_at=completed_at,
            last_event_time=completed_at,
            activity=Activity(
                kind="confirmed",
                detail="received token_count",
                timestamp=completed_at,
            ),
            request=RequestTokens(
                status="confirmed",
                model="gpt-5.5",
                completed_at=completed_at,
            ),
        )
        snapshot.request_history = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=100,
                cached_tokens=80,
                output_tokens=20,
                reasoning_tokens=0,
                total_tokens=120,
                estimated=False,
                cost_usd=0.001,
                started_at=started_at,
                completed_at=completed_at,
            )
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        trail = payload["topDetails"]["activityTrail"]
        self.assertGreaterEqual(len(trail), 1)
        newest = trail[0]
        self.assertEqual(newest["title"], "任务完成，Token确认")
        self.assertEqual(newest["detail"], "完成右侧活动轨迹合并")
        self.assertNotIn("请求完成", str(newest))
        self.assertNotIn("received token_count", str(newest))

    def test_renderer_script_resizes_request_panel_from_fixed_bottom(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('beginGesture(event, name, "resize", action.dataset.edge || "", false)', script)
        self.assertIn('const resizeFromLeft = gesture.edge === "left" || gesture.edge.endsWith("-left")', script)
        self.assertIn('const left = resizeFromLeft ? (gesture.left + gesture.width - width) : gesture.left', script)
        self.assertIn("height = clamp(gesture.height - dy", script)
        self.assertIn("top = bottom - height", script)
        self.assertIn("patch.bottomOffset", script)
        self.assertIn("anchor.top + anchor.height + bottomOffset - height", script)
        self.assertIn(": clamp(anchor.top + Number(state.yOffset || 0)", script)


class RendererHudClientTests(unittest.TestCase):
    def test_wait_for_renderer_reports_startup_progress_stages(self) -> None:
        client = RendererHudClient(enabled=False)
        observed_stages: list[str] = []
        client.update = lambda _snapshot: True  # type: ignore[method-assign]

        self.assertTrue(
            renderer_hud.wait_for_renderer(
                client,
                lambda: ParsedSession(status="waiting"),
                timeout_seconds=0.1,
                progress_callback=observed_stages.append,
            )
        )

        self.assertEqual(observed_stages, ["reading_session", "showing_hud"])
        self.assertIn("snapshotBuildMs", client.last_attach_metrics)

    def test_wait_for_renderer_retries_cold_start_until_payload_is_acknowledged(
        self,
    ) -> None:
        class Clock:
            now = 0.0

            def monotonic(self) -> float:
                return self.now

            def sleep(self, seconds: float) -> None:
                self.now += seconds

        class ColdStartClient:
            last_status = "starting"
            last_update_metrics: dict[str, object] = {}

            def __init__(self) -> None:
                self.startup_updates = 0
                self.last_attach_metrics: dict[str, object] = {}

            def update_startup(self, _snapshot: ParsedSession) -> bool:
                self.startup_updates += 1
                return self.startup_updates == 3

        clock = Clock()
        client = ColdStartClient()
        snapshot_factory = MagicMock(return_value=ParsedSession(status="waiting"))
        observed_stages: list[str] = []

        self.assertTrue(
            renderer_hud.wait_for_renderer(
                client,  # type: ignore[arg-type]
                snapshot_factory,
                timeout_seconds=1.0,
                retry_until_ready=True,
                retry_interval_seconds=0.25,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
                progress_callback=observed_stages.append,
            )
        )

        snapshot_factory.assert_called_once_with()
        self.assertEqual(client.startup_updates, 3)
        self.assertEqual(clock.now, 0.5)
        self.assertEqual(observed_stages, [])
        self.assertEqual(client.last_attach_metrics["attempts"], 3)
        self.assertTrue(client.last_attach_metrics["retryUntilReady"])

    def test_startup_payload_failure_does_not_open_normal_retry_gate(self) -> None:
        client = RendererHudClient(enabled=True)
        client._page_target = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("Codex splash is not ready")
        )

        with patch.object(client, "_clear_target_cache") as clear_target:
            self.assertFalse(client.update_payload({}, startup_retry=True))

        clear_target.assert_called_once_with(clear_script=True)
        allowed, reason, remaining = client.update_gate_state()
        self.assertTrue(allowed)
        self.assertEqual(reason, "")
        self.assertEqual(remaining, 0.0)
        self.assertEqual(client._update_failure_count, 0)

    def test_startup_payload_primes_persistent_binding_before_first_update(self) -> None:
        client = RendererHudClient(enabled=True)
        target = {
            "id": "target-1",
            "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
        }
        binding = MagicMock()
        binding.wait_ready.return_value = True
        binding.send_command.return_value = {
            "result": {"result": {"value": {"ok": True, "applyMs": 0.5}}}
        }
        client._active_session_binding = binding

        def install(websocket_url: str, target_id: str, *, force: bool = False) -> None:
            del force
            client._script_identifier = "script-1"
            client._target_id = target_id
            client._websocket_url = websocket_url

        client._page_target = MagicMock(return_value=target)  # type: ignore[method-assign]
        client._install = install  # type: ignore[method-assign]

        self.assertTrue(
            client.update_payload(
                {"payloadDomains": {}, "debug": True},
                startup_retry=True,
            )
        )

        self.assertEqual(
            binding.method_calls[:3],
            [
                call.ensure(
                    "ws://127.0.0.1/devtools/page/1",
                    "target-1",
                ),
                call.wait_ready(client.timeout_seconds),
                call.send_command(
                    "ws://127.0.0.1/devtools/page/1",
                    "Runtime.evaluate",
                    ANY,
                    client.timeout_seconds,
                ),
            ],
        )
        binding.wait_ready.assert_called_once_with(client.timeout_seconds)
        binding.send_command.assert_called_once()

    def test_first_existing_cdp_attach_waits_for_binding_before_payload(self) -> None:
        client = RendererHudClient(enabled=True)
        target = {
            "id": "target-1",
            "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
        }
        binding = MagicMock()
        binding.wait_ready.return_value = True
        binding.send_command.return_value = {
            "result": {"result": {"value": {"ok": True, "applyMs": 0.5}}}
        }
        client._active_session_binding = binding
        client._page_target = MagicMock(return_value=target)  # type: ignore[method-assign]
        client._install = MagicMock()  # type: ignore[method-assign]

        self.assertTrue(client.update_payload({"payloadDomains": {}, "debug": True}))

        self.assertEqual(
            binding.method_calls[:3],
            [
                call.ensure(
                    "ws://127.0.0.1/devtools/page/1",
                    "target-1",
                ),
                call.wait_ready(client.timeout_seconds),
                call.send_command(
                    "ws://127.0.0.1/devtools/page/1",
                    "Runtime.evaluate",
                    ANY,
                    client.timeout_seconds,
                ),
            ],
        )

    def test_startup_payload_rechecks_target_after_unacknowledged_update(self) -> None:
        client = RendererHudClient(enabled=True)
        target = {
            "id": "target-1",
            "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
        }
        client._page_target = MagicMock(return_value=target)  # type: ignore[method-assign]
        client._install = MagicMock()  # type: ignore[method-assign]
        client._send_update = MagicMock(return_value=False)  # type: ignore[method-assign]

        with patch.object(client, "_clear_target_cache") as clear_target:
            self.assertFalse(
                client.update_payload(
                    {"payloadDomains": {}, "debug": True},
                    startup_retry=True,
                )
            )

        clear_target.assert_called_once_with(clear_script=True)

    def test_client_installs_renderer_script_with_model_catalog(self) -> None:
        install_calls: list[str] = []
        originals = (
            renderer_hud._renderer_hud_script_with_model_catalog,
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, timeout_seconds
            install_calls.append(script)
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        renderer_hud._renderer_hud_script_with_model_catalog = lambda: (
            renderer_hud.RENDERER_HUD_SCRIPT
            + "\n// GPT-5.6-Sol gpt-5.6-sol ultra"
        )
        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_install, fake_send)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
        finally:
            (
                renderer_hud._renderer_hud_script_with_model_catalog,
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(len(install_calls), 1)
        self.assertIn("GPT-5.6-Sol", install_calls[0])
        self.assertIn("gpt-5.6-sol", install_calls[0])
        self.assertIn("ultra", install_calls[0])

    def test_client_installs_renderer_script_once_and_pushes_payloads(self) -> None:
        install_calls: list[tuple[str, str]] = []
        list_calls = 0
        update_expressions: list[str] = []
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            nonlocal list_calls
            del port, timeout_seconds
            list_calls += 1
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del timeout_seconds
            install_calls.append((websocket_url, script))
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, timeout_seconds
            self.assertEqual(method, "Runtime.evaluate")
            update_expressions.append(str(params["expression"]))
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_install, fake_send)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            self.assertTrue(client.update_payload({"topLine": "C", "requestLine": "D"}))
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(len(install_calls), 1)
        self.assertEqual(list_calls, 1)
        self.assertIn("__codexUsageHudUpdate", update_expressions[0])
        self.assertIn('"topLine": "C"', update_expressions[1])

    def test_client_reuses_theme_snapshot_between_refreshes(self) -> None:
        theme_snapshot = CodexThemeSnapshot.from_probe_result(
            {
                "mode": "dark",
                "effectiveVariant": "dark",
                "cssTheme": {
                    "accent": "#5e6ad2",
                    "surface": "#0f0f11",
                    "ink": "#e3e4e6",
                },
            },
            source="cdp",
        )
        assert theme_snapshot is not None
        client = RendererHudClient(enabled=True)
        probe_calls = 0

        def probe() -> CodexThemeSnapshot:
            nonlocal probe_calls
            probe_calls += 1
            return theme_snapshot

        client._theme_probe.snapshot = probe  # type: ignore[method-assign]
        client.update_payload = lambda _payload: True  # type: ignore[method-assign]
        try:
            self.assertTrue(client.update(ParsedSession(status="waiting")))
            self.assertTrue(client.update(ParsedSession(status="waiting")))
        finally:
            client.close()

        self.assertEqual(probe_calls, 1)

    def test_send_update_records_cdp_and_renderer_apply_timings(self) -> None:
        client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
        originals = (renderer_hud.send_cdp_command,)
        expressions: list[str] = []

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, timeout_seconds
            self.assertEqual(method, "Runtime.evaluate")
            expressions.append(str(params["expression"]))
            return {
                "result": {
                    "result": {
                        "value": {
                            "ok": True,
                            "applyMs": 812.5,
                        }
                    }
                }
            }

        (renderer_hud.send_cdp_command,) = (fake_send,)
        try:
            self.assertTrue(
                client._send_update(  # pylint: disable=protected-access
                    "ws://127.0.0.1/devtools/page/1",
                    {
                        "topLine": "A",
                        "payloadDomains": {"sessionSwitch": {"topLine": "A"}},
                    },
                )
            )
        finally:
            (renderer_hud.send_cdp_command,) = originals

        self.assertIn("performance.now", expressions[0])
        self.assertGreaterEqual(client.last_update_metrics["cdpMs"], 0.0)
        self.assertEqual(client.last_update_metrics["rendererApplyMs"], 812.5)
        self.assertGreater(client.last_update_metrics["payloadBytes"], 0)
        self.assertEqual(client.last_update_metrics["payloadDomains"], ["sessionSwitch"])

    def test_client_uses_subscribed_target_state_after_cache_ttl(self) -> None:
        install_calls: list[tuple[str, str]] = []
        list_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            nonlocal list_calls
            del port, timeout_seconds
            list_calls += 1
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del timeout_seconds
            install_calls.append((websocket_url, script))
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_install, fake_send)
        try:
            client = RendererHudClient(
                port=9229,
                timeout_seconds=0.05,
                target_cache_seconds=0.0,
                enabled=True,
            )
            self.assertTrue(client.update_payload({"topLine": "A"}))
            time.sleep(0.002)
            self.assertTrue(client.update_payload({"topLine": "B"}))
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(len(install_calls), 1)
        self.assertEqual(list_calls, 1)

    def test_client_reports_target_discovery_disconnect_without_http_rescan(self) -> None:
        list_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            nonlocal list_calls
            del port, timeout_seconds
            list_calls += 1
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_install, fake_send)
        try:
            client = RendererHudClient(
                port=9229,
                timeout_seconds=0.05,
                target_cache_seconds=0.0,
                enabled=True,
            )
            self.assertTrue(client.update_payload({"topLine": "A"}))
            client._target_discovery.mark_disconnected("CDP websocket closed")
            self.assertFalse(client.update_payload({"topLine": "B"}))
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(list_calls, 1)
        self.assertIn("CDP target discovery disconnected", client.last_error)

    def test_binding_disconnect_isolated_and_not_restarted_for_same_target(self) -> None:
        attempts: list[str] = []
        binding = renderer_hud._RendererBinding(
            "testBinding",
            lambda _payload: None,
            timeout_seconds=0.05,
        )

        def fail_connect(websocket_url: str):
            attempts.append(websocket_url)
            raise RuntimeError("closed")

        binding._connect = fail_connect  # type: ignore[method-assign]
        try:
            binding.ensure("ws://127.0.0.1:9229/devtools/page/1", "target-1")
            time.sleep(0.02)
            binding.ensure("ws://127.0.0.1:9229/devtools/page/1", "target-1")
        finally:
            binding.close()

        self.assertEqual(attempts, ["ws://127.0.0.1:9229/devtools/page/1"])

    def test_critical_binding_can_retry_same_target_after_disconnect(self) -> None:
        attempts: list[str] = []
        binding = renderer_hud._RendererBinding(
            "testBinding",
            lambda _payload: None,
            timeout_seconds=0.05,
            retry_same_target=True,
        )

        def fail_connect(websocket_url: str):
            attempts.append(websocket_url)
            raise RuntimeError("closed")

        binding._connect = fail_connect  # type: ignore[method-assign]
        try:
            binding.ensure("ws://127.0.0.1:9229/devtools/page/1", "target-1")
            time.sleep(0.02)
            binding.ensure("ws://127.0.0.1:9229/devtools/page/1", "target-1")
            time.sleep(0.02)
        finally:
            binding.close()

        self.assertEqual(
            attempts,
            [
                "ws://127.0.0.1:9229/devtools/page/1",
                "ws://127.0.0.1:9229/devtools/page/1",
            ],
        )

    def test_binding_command_channel_routes_response_by_id(self) -> None:
        binding = renderer_hud._RendererBinding(
            "testBinding",
            lambda _payload: None,
            timeout_seconds=0.05,
        )
        binding._sock = object()  # type: ignore[assignment]
        binding._websocket_url = "ws://127.0.0.1:9229/devtools/page/1"
        binding._ready_event.set()

        def respond(_sock, command_id, method, params):
            self.assertEqual(method, "Runtime.evaluate")
            self.assertEqual(params, {"expression": "1"})
            ready, response = binding._pending_responses[command_id]
            response["payload"] = {"id": command_id, "result": {"ok": True}}
            ready.set()

        binding._send_command = respond  # type: ignore[method-assign]

        result = binding.send_command(
            binding._websocket_url,
            "Runtime.evaluate",
            {"expression": "1"},
            0.05,
        )

        self.assertEqual(result["result"], {"ok": True})

    def _fake_binding_transport(
        self,
    ) -> tuple[
        object,
        queue.Queue[str],
        renderer_hud._RendererBinding,
        object,
    ]:
        incoming: queue.Queue[str] = queue.Queue()

        class FakeSocket:
            def shutdown(self, _how: int) -> None:
                pass

            def close(self) -> None:
                pass

        fake_socket = FakeSocket()

        def fake_receive(_sock: object) -> str:
            try:
                return incoming.get(timeout=0.05)
            except queue.Empty:
                raise socket.timeout()

        def fake_send(_sock, command_id, method, params):
            del method, params
            incoming.put(
                json.dumps(
                    {
                        "id": command_id,
                        "result": {"ok": True} if command_id >= 100 else {},
                    }
                )
            )

        binding = renderer_hud._RendererBinding(
            "testBinding",
            lambda _payload: None,
            timeout_seconds=0.05,
        )
        binding._connect = lambda _url: fake_socket  # type: ignore[method-assign]
        binding._send_command = fake_send  # type: ignore[method-assign]
        return fake_socket, incoming, binding, fake_receive

    def test_binding_reader_routes_command_response_while_callback_is_blocked(self) -> None:
        callback_started = threading.Event()
        release_callback = threading.Event()
        observed_threads: tuple[threading.Thread | None, threading.Thread | None] = (
            None,
            None,
        )

        def callback(_payload):
            callback_started.set()
            release_callback.wait(1.0)

        _fake_socket, incoming, binding, fake_receive = self._fake_binding_transport()
        binding.callback = callback
        with patch.object(renderer_bindings, "receive_message", fake_receive):
            binding.ensure("ws://127.0.0.1:9229/devtools/page/1", "target-1")
            try:
                self.assertTrue(binding.wait_ready(0.5))
                incoming.put(
                    json.dumps(
                        {
                            "method": "Runtime.bindingCalled",
                            "params": {
                                "name": "testBinding",
                                "payload": json.dumps({"event": "blocked"}),
                            },
                        }
                    )
                )
                self.assertTrue(callback_started.wait(0.2))
                observed_threads = (binding._thread, binding._callback_thread)
                started = time.perf_counter()
                result = binding.send_command(
                    "ws://127.0.0.1:9229/devtools/page/1",
                    "Runtime.evaluate",
                    {"expression": "1"},
                    0.2,
                )
                self.assertEqual(result["result"], {"ok": True})
                self.assertLess(time.perf_counter() - started, 0.15)
            finally:
                release_callback.set()
                binding.close(join_timeout=0.5)
        self.assertFalse(observed_threads[0].is_alive())
        self.assertFalse(observed_threads[1].is_alive())

    def test_binding_callback_worker_preserves_event_order(self) -> None:
        observed: list[dict[str, object]] = []
        _fake_socket, incoming, binding, fake_receive = self._fake_binding_transport()
        binding.callback = observed.append
        with patch.object(renderer_bindings, "receive_message", fake_receive):
            binding.ensure("ws://127.0.0.1:9229/devtools/page/1", "target-1")
            try:
                self.assertTrue(binding.wait_ready(0.5))
                for value in (1, 2, 3):
                    incoming.put(
                        json.dumps(
                            {
                                "method": "Runtime.bindingCalled",
                                "params": {
                                    "name": "testBinding",
                                    "payload": json.dumps({"value": value}),
                                },
                            }
                        )
                    )
                deadline = time.monotonic() + 0.5
                while len(observed) < 3 and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(observed, [{"value": 1}, {"value": 2}, {"value": 3}])
            finally:
                binding.close(join_timeout=0.5)

    def test_binding_disconnect_follows_queued_events(self) -> None:
        observed: list[object] = []
        callback_queue: queue.Queue[object] = queue.Queue()
        stop_event = threading.Event()
        worker = threading.Thread(
            target=renderer_bindings._RendererBinding._run_callback_worker,
            args=(
                callback_queue,
                stop_event,
                observed.append,
                lambda reason: observed.append(("disconnect", reason)),
            ),
            daemon=True,
        )
        worker.start()
        callback_queue.put({"value": 1})
        callback_queue.put(renderer_bindings._BindingDisconnect("closed"))
        worker.join(timeout=0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(observed, [{"value": 1}, ("disconnect", "closed")])

    def test_binding_close_stops_reader_and_callback_worker(self) -> None:
        _fake_socket, _incoming, binding, fake_receive = self._fake_binding_transport()
        with patch.object(renderer_bindings, "receive_message", fake_receive):
            binding.ensure("ws://127.0.0.1:9229/devtools/page/1", "target-1")
            self.assertTrue(binding.wait_ready(0.5))
            reader = binding._thread
            callback_worker = binding._callback_thread
            binding.close(join_timeout=0.5)
        self.assertFalse(reader.is_alive())
        self.assertFalse(callback_worker.is_alive())

    def test_renderer_update_prefers_persistent_active_session_channel(self) -> None:
        client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
        persistent = MagicMock(
            return_value={
                "result": {
                    "result": {"value": {"ok": True, "applyMs": 1.5}}
                }
            }
        )
        client._active_session_binding = SimpleNamespace(send_command=persistent)

        with patch("codex_usage_hud.renderer_client.send_cdp_command") as fallback:
            self.assertTrue(
                client._send_update(
                    "ws://127.0.0.1:9229/devtools/page/1",
                    {"payloadDomains": {"sessionSwitch": {}}},
                )
            )

        fallback.assert_not_called()
        persistent.assert_called_once()
        self.assertEqual(
            client.last_update_metrics["transport"],
            "active-session-binding",
        )

    def test_active_session_binding_disconnect_is_reported_to_callback(self) -> None:
        observed: list[dict[str, object]] = []
        client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
        client.set_active_session_callback(observed.append)

        client._handle_active_session_binding_disconnect("binding closed")
        client.close()

        self.assertTrue(observed[0]["channelUnavailable"])
        self.assertEqual(observed[0]["reason"], "binding closed")
        self.assertGreater(int(observed[0]["observedAt"]), 0)

    def test_binding_subscription_does_not_block_renderer_payload_path(self) -> None:
        binding = renderer_hud._RendererBinding(
            "testBinding",
            lambda _payload: None,
            timeout_seconds=0.5,
        )
        connect_started = threading.Event()
        release_connect = threading.Event()

        def slow_connect(_websocket_url: str):
            connect_started.set()
            release_connect.wait(1.0)
            raise RuntimeError("test complete")

        binding._connect = slow_connect  # type: ignore[method-assign]
        started = time.perf_counter()
        try:
            binding.ensure("ws://127.0.0.1:9229/devtools/page/1", "target-1")
            elapsed = time.perf_counter() - started
            self.assertTrue(connect_started.wait(0.2))
            self.assertLess(elapsed, 0.05)
        finally:
            release_connect.set()
            binding.close()

    def test_client_starts_active_session_binding_after_update(self) -> None:
        ensure_calls: list[tuple[str, str]] = []
        close_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                nonlocal close_calls
                close_calls += 1

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_active_session_callback(lambda payload: payload)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertEqual(close_calls, 1)

    def test_client_bootstraps_active_session_binding_before_payload_update(self) -> None:
        ensure_calls: list[tuple[str, str]] = []
        update_expressions: list[str] = []
        observed_sessions: list[dict[str, object]] = []
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                pass

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, timeout_seconds
            self.assertEqual(method, "Runtime.evaluate")
            update_expressions.append(str(params["expression"]))
            return {
                "result": {
                    "result": {
                        "value": {
                            "startup": True,
                            "active": {
                                "sessionId": "thread-current",
                                "title": "Current Renderer Thread",
                                "reason": "bootstrap",
                            },
                        }
                    }
                }
            }

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_active_session_callback(observed_sessions.append)
            self.assertTrue(
                client.bootstrap_active_session(
                    startup_payload={"payloadDomains": {"startup": {"title": "Starting"}}}
                )
            )
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertIn("__codexUsageHudReportActiveSession", update_expressions[0])
        self.assertIn("__codexUsageHudUpdate", update_expressions[0])
        self.assertTrue(client.last_bootstrap_metrics["startupBubble"])
        self.assertEqual(observed_sessions[0]["sessionId"], "thread-current")

    def test_client_starts_attachments_binding_after_update(self) -> None:
        # 附件走 CDP binding（页面 CSP 拦截 fetch），验证 update 后 binding 被 ensure。
        ensure_calls: list[tuple[str, str]] = []
        close_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        captured: list[str] = []

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds
                captured.append(binding_name)

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                nonlocal close_calls
                close_calls += 1

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_attachments_callback(lambda payload: payload)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertIn(renderer_hud.COMPOSER_ATTACHMENTS_BINDING_NAME, captured)
        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertEqual(close_calls, 1)

    def test_client_starts_settings_command_binding_after_update(self) -> None:
        ensure_calls: list[tuple[str, str]] = []
        close_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        captured: list[str] = []
        bindings: list[object] = []

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds
                self.retry_same_target = False
                bindings.append(self)
                captured.append(binding_name)

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                nonlocal close_calls
                close_calls += 1

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_settings_command_callback(lambda payload: payload)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertIn(renderer_hud.SETTINGS_COMMAND_BINDING_NAME, captured)
        self.assertTrue(bindings[0].retry_same_target)
        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertEqual(close_calls, 1)

    def test_client_starts_layout_binding_after_update(self) -> None:
        # 布局变更（拖拽/缩放/展开）通过 CDP binding 上报，验证 update 后 binding 被 ensure。
        ensure_calls: list[tuple[str, str]] = []
        close_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        captured: list[str] = []

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds
                captured.append(binding_name)

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                nonlocal close_calls
                close_calls += 1

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_layout_callback(lambda payload: payload)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertIn(renderer_hud.LAYOUT_BINDING_NAME, captured)
        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertEqual(close_calls, 1)

    def test_client_starts_audit_binding_after_update(self) -> None:
        # audit binding was removed; setter is now a no-op that must accept
        # any callable without wiring a new binding.
        client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=False)
        client.set_audit_callback(lambda payload: payload)
        self.assertFalse(hasattr(client, "_audit_binding"))

    def test_client_does_not_expose_renderer_settings_polling_fallback(self) -> None:
        client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)

        self.assertFalse(hasattr(client, "take_settings_command"))

    def test_client_close_uses_cached_target_when_force_lookup_fails(self) -> None:
        calls: list[tuple[str, str]] = []
        removed_scripts: list[tuple[str, str]] = []
        originals = (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
            renderer_hud.remove_new_document_script,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            raise RuntimeError("target list unavailable")

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del method, timeout_seconds
            calls.append((websocket_url, str(params["expression"])))
            return {"result": {"result": {"value": True}}}

        def fake_remove(websocket_url: str, identifier: str, timeout_seconds: float) -> None:
            del timeout_seconds
            removed_scripts.append((websocket_url, identifier))

        (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
            renderer_hud.remove_new_document_script,
        ) = (fake_list_targets, fake_send, fake_remove)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client._websocket_url = "ws://cached"
            client._script_identifier = "script-1"
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.send_cdp_command,
                renderer_hud.remove_new_document_script,
            ) = originals

        self.assertEqual(calls[0][0], "ws://cached")
        self.assertIn("codex-usage-hud-root", calls[0][1])
        self.assertEqual(removed_scripts, [("ws://cached", "script-1")])

    def test_remove_renderer_hud_from_pages_sweeps_page_targets(self) -> None:
        calls: list[str] = []
        originals = (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            self.assertEqual(port, 9229)
            del timeout_seconds
            return [
                {
                    "type": "page",
                    "title": "Codex",
                    "webSocketDebuggerUrl": "ws://page-1",
                },
                {
                    "type": "worker",
                    "title": "ignored",
                    "webSocketDebuggerUrl": "ws://worker",
                },
            ]

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del method, timeout_seconds
            calls.append(f"{websocket_url}:{params['expression']}")
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_send)
        try:
            removed = renderer_hud.remove_renderer_hud_from_pages(
                port=9229,
                timeout_seconds=0.05,
            )
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(removed, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("ws://page-1", calls[0])
        self.assertIn("__codexUsageHudRemove", calls[0])

    def test_renderer_restart_prompt_survives_subsequent_status_payloads(self) -> None:
        """重启提示必须粘性保留，不能被后续轮次的普通/空命令状态清掉。

        对应 Review 结论：settings_command_status 每轮渲染后被清空（单次消费），
        若 JS 端无条件按当前 status.restartVisible 清除弹窗，「稍后重启」提示会
        被后续 payload 隐藏。断言脚本里存在粘性保护：
        1. showSettingsRestartPrompt 会置位 settingsRestartPending；
        2. 无 restartVisible 的状态只有在没有 pending 弹窗时才调用
           clearSettingsRestartPrompt；
        3. 空状态兜底分支会依据 settingsRestartPending 恢复重启按钮。
        """
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("let settingsRestartPending = false;", script)
        self.assertIn(
            "settingsRestartPending = true;",
            script,
        )
        self.assertIn(
            "if (!status.restartVisible && !settingsRestartPending) {",
            script,
        )
        self.assertIn(
            "clearSettingsRestartPrompt();",
            script,
        )
        self.assertIn(
            "if (settingsRestartPending) {",
            script,
        )
        self.assertIn(
            "setSettingsRestartVisible(true, { codex: settingsRestartCodex });",
            script,
        )

    def test_renderer_command_status_is_consumed_once_in_payload(self) -> None:
        """后端命令状态是单次消费：第一轮 payload 携带 restartVisible，
        清空后第二轮 payload 不再携带（JS 端依赖粘性状态保留弹窗）。"""
        snapshot = ParsedSession(
            session_id="session-restart-lifecycle",
            session_title="Restart Lifecycle",
            status="parsed",
            selection_source="renderer",
            refreshed_at=datetime(2026, 7, 6, 10, 0, 0).astimezone(),
            last_event_time=datetime(2026, 7, 6, 9, 59, 58).astimezone(),
            confirmed=ConfirmedTokens(cumulative_total=42, cumulative_cost_usd=0.01),
            request=RequestTokens(status="idle", model="gpt-5", total_tokens=0),
        )
        snapshot.today_tokens = 100
        snapshot.week_tokens = 200
        snapshot.today_cost_usd = 0.02
        snapshot.week_cost_usd = 0.04

        def build_payload(command_status):
            payload = payload_from_snapshot(
                snapshot,
                settings_bridge_url="http://127.0.0.1:8765",
                settings_command_status=command_status,
            ).to_json()
            return payload["settingsCommandStatus"]

        first = build_payload(
            {
                "restartVisible": True,
                "restartCodex": True,
                "message": "默认 Codex App Provider 配置已保存，需要重启 Codex Desktop 才能生效。",
            }
        )
        self.assertEqual(first["restartVisible"], True)
        self.assertEqual(first["restartCodex"], True)

        # _record_success 清空 settings_command_status 后的下一轮 payload。
        second = build_payload({})
        self.assertEqual(second, {})


if __name__ == "__main__":
    unittest.main()
