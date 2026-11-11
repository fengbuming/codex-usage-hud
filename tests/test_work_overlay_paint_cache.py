"""Pixel and lifecycle checks for the existing helper's paint-work cache."""

from __future__ import annotations

import os
import math
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.qt_ui

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from codex_usage_hud.ui.work_overlay.qt_visuals import CompletedBadgeWidget


class _DirectBadge(CompletedBadgeWidget):
    def _paint_content_layer(self, painter, palette):
        self._paint_static_content(painter, palette)


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "seguisym.ttf", "segoeui.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    return application


def _light_theme():
    return {"surface": "#ffffff", "panelSurface": "#ffffff", "requestPanelSurface": "#ffffff",
            "text": "#111111", "muted": "#555555", "panelBorder": "#cccccc"}


def _item():
    return {
        "id": "work", "status": "recent", "title": "Unicode 扫描任务 finished",
        "clientKind": "cli", "profileName": "development", "workdir": "E:/Project/example",
        "elapsedText": "已处理 3分42秒", "tokensText": "129K", "costText": "$1.23", "cacheHitText": "82%",
        "sessionOutputTokensPerSecond": 45, "sessionOutputMinTokensPerSecond": 20,
        "sessionOutputMaxTokensPerSecond": 90,
    }


def _capture(parent, ratio, dpi=None):
    image = QImage(math.ceil(parent.width() * ratio), math.ceil(parent.height() * ratio),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    if dpi is not None:
        image.setDotsPerMeterX(round(dpi / 0.0254))
        image.setDotsPerMeterY(round(dpi / 0.0254))
    image.fill(Qt.GlobalColor.transparent)
    parent.render(image, QPoint())
    return bytes(image.constBits())


@pytest.mark.parametrize("ratio", [1, 1.25, 1.5, 2])
@pytest.mark.parametrize("progress", [0.15, 0.4, 1])
@pytest.mark.parametrize("light", [False, True])
def test_cached_badge_keeps_native_pixels_through_morph_dpi_and_fractional_positions(app, ratio, progress, light):
    theme = _light_theme() if light else None
    captures = []
    for cls in (_DirectBadge, CompletedBadgeWidget):
        parent = QWidget()
        parent.resize(210, 210)
        parent.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        badge = cls(_item(), parent, animate_intro=False, theme_tokens=theme)
        assert (QColor(badge._paint_palette["fillMid"]).lightnessF() > 0.5) == light
        badge.move(3, 7)  # fractional physical-pixel offsets at 125% / 150%
        badge._progress = progress
        badge._liquid_elapsed = 8
        badge._sweep_elapsed = 8
        badge._liquid_phase = 2
        badge._bubble_phase = 0.3
        captures.append(_capture(parent, ratio))
        parent.close()
        parent.deleteLater()
    differences = [abs(a - b) for a, b in zip(*captures)]
    # Source-over composition of a premultiplied layer can round a channel by
    # 1–2/255, while positions, font rendering and all animations stay native.
    assert max(differences) <= 2
    assert sum(differences) / len(differences) < 0.04


def test_animation_frames_reuse_static_content_but_data_theme_and_dpi_invalidate(app):
    badge = CompletedBadgeWidget(_item(), animate_intro=False)
    try:
        with patch.object(badge, "_paint_static_content", wraps=badge._paint_static_content) as paint:
            _capture(badge, 1)
            first = badge._static_raster
            for frame in range(5):
                badge._liquid_phase += 0.1
                badge._bubble_phase += 0.02
                _capture(badge, 1)
            assert paint.call_count == 1
            assert badge._static_raster is first
            badge.set_item({**_item(), "title": "Renamed", "costText": "$2.34"})
            _capture(badge, 1)
            assert paint.call_count == 2
            assert badge._static_raster is not first
            badge.set_theme_tokens(_light_theme())
            _capture(badge, 1)
            assert paint.call_count == 3
            previous = badge._static_raster
            _capture(badge, 2)
            assert badge._static_raster is not previous
            assert badge._static_raster.devicePixelRatio() == 2
    finally:
        badge.close()


def test_hidden_badge_stops_existing_animation_timer_and_cache_is_bounded(app):
    badge = CompletedBadgeWidget(_item(), animate_intro=False)
    try:
        for index in range(15):
            badge.set_item({**_item(), "title": f"title {index}"})
            _capture(badge, 1)
        assert badge._static_raster.sizeInBytes() <= 168 * 182 * 4
        badge.show()
        assert badge._timer.isActive()
        badge.hide()
        assert not badge._timer.isActive()
    finally:
        badge.close()


@pytest.mark.parametrize("speed", [None, 2.98, 400])
def test_cached_badge_keeps_missing_speed_and_click_launch_rendering(app, speed):
    captures = []
    for cls in (_DirectBadge, CompletedBadgeWidget):
        badge = cls({**_item(), "sessionOutputTokensPerSecond": speed}, animate_intro=False)
        badge._liquid_elapsed, badge._sweep_elapsed = 8, 8
        badge._liquid_phase, badge._bubble_phase = 2, 0.3
        with patch("codex_usage_hud.ui.work_overlay.qt_visuals.time.monotonic", return_value=100):
            badge.set_switch_pending(True, 99.8)
            captures.append(_capture(badge, 1.5))
        badge.close()
    differences = [abs(a - b) for a, b in zip(*captures)]
    assert max(differences) <= 2
    assert sum(differences) / len(differences) < 0.04


@pytest.mark.parametrize("dpi", [120, 144])
def test_different_logical_dpi_keeps_original_geometry_and_font_metrics(app, dpi):
    images = []
    for cls in (_DirectBadge, CompletedBadgeWidget):
        badge = cls(_item(), animate_intro=False)
        badge._liquid_elapsed, badge._sweep_elapsed = 8, 8
        badge._liquid_phase, badge._bubble_phase = 2, 0.3
        image = QImage(badge.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDotsPerMeterX(round(dpi / 0.0254))
        image.setDotsPerMeterY(round(dpi / 0.0254))
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        badge._paint_content_layer(painter, badge._paint_palette)
        painter.end()
        images.append(bytes(image.constBits()))
        badge.close()
    assert images[0] == images[1]
