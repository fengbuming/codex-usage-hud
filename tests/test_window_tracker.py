"""Unit tests for Windows tracker geometry fallback helpers."""

from __future__ import annotations

import threading
import time
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms import windows_tracker as wt
from codex_usage_hud.platforms.windows_tracker import CodexWindowTracker, PhysicalRect


class CodexWindowTrackerGeometryTests(unittest.TestCase):
    def test_geometry_fallback_builds_title_and_input_landmarks(self) -> None:
        rect = PhysicalRect(left=240, top=0, right=1230, bottom=740)

        landmarks = CodexWindowTracker.geometry_fallback(rect)

        self.assertEqual(landmarks.source, "geometry")
        self.assertEqual(landmarks.title_bar.as_xywh(), (240, 0, 990, 45))
        self.assertEqual(landmarks.input_box.as_xywh(), (538, 648, 347, 56))

    def test_input_dock_coordinates_touch_input_top_edge(self) -> None:
        rect = PhysicalRect(left=240, top=0, right=1230, bottom=740)
        landmarks = CodexWindowTracker.geometry_fallback(rect)

        dock = CodexWindowTracker.dock_coordinates_from_landmarks(
            landmarks.title_bar,
            landmarks.input_box,
            target="input",
            hud_height=32,
        )

        self.assertEqual(dock, (538, 616, 347))

    def test_title_dock_coordinates_stay_inside_title_bar(self) -> None:
        rect = PhysicalRect(left=240, top=0, right=1230, bottom=740)
        landmarks = CodexWindowTracker.geometry_fallback(rect)

        x, y, width = CodexWindowTracker.dock_coordinates_from_landmarks(
            landmarks.title_bar,
            landmarks.input_box,
            target="title",
            hud_height=36,
        )

        self.assertGreaterEqual(x, landmarks.title_bar.left)
        self.assertGreaterEqual(y, landmarks.title_bar.top)
        self.assertLessEqual(y + 36, landmarks.title_bar.bottom)
        self.assertGreaterEqual(width, 320)
        self.assertLessEqual(x + width, landmarks.title_bar.right)

    def test_uia_input_scoring_rejects_mid_window_scroll_content(self) -> None:
        window = PhysicalRect(left=230, top=126, right=1482, bottom=872)
        node = wt._UiNode(
            rect=PhysicalRect(left=652, top=551, right=1389, bottom=573),
            control_type=50004,
            name="message",
            automation_id="",
            class_name="",
            offscreen=False,
        )

        self.assertEqual(wt._UiaProbe._score_input_box(node, window), 0)

    def test_uia_header_button_candidate_collects_top_buttons_only(self) -> None:
        window = PhysicalRect(left=100, top=50, right=1100, bottom=850)
        button = wt._UiNode(
            rect=PhysicalRect(left=900, top=80, right=930, bottom=110),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="Open in File Explorer",
            automation_id="",
            class_name="",
            offscreen=False,
        )
        image = wt._UiNode(
            rect=PhysicalRect(left=940, top=80, right=970, bottom=110),
            control_type=wt._UIA_IMAGE_CONTROL_TYPE_ID,
            name="",
            automation_id="avatar",
            class_name="",
            offscreen=False,
        )
        group = wt._UiNode(
            rect=PhysicalRect(left=980, top=80, right=1030, bottom=110),
            control_type=wt._UIA_GROUP_CONTROL_TYPE_ID,
            name="",
            automation_id="",
            class_name="",
            offscreen=False,
        )
        mid_window_button = wt._UiNode(
            rect=PhysicalRect(left=900, top=320, right=930, bottom=350),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="Later button",
            automation_id="",
            class_name="",
            offscreen=False,
        )

        candidate = wt._UiaProbe._header_button_candidate(button, window, 4)

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.rect.as_xywh(), (900, 80, 30, 30))
        self.assertIsNotNone(wt._UiaProbe._header_button_candidate(image, window, 4))
        self.assertIsNotNone(wt._UiaProbe._header_button_candidate(group, window, 4))
        self.assertIsNone(
            wt._UiaProbe._header_button_candidate(mid_window_button, window, 4)
        )

    def test_uia_header_button_collection_sorts_and_clusters_right_edge(self) -> None:
        header = PhysicalRect(left=400, top=70, right=1000, bottom=120)
        candidates = [
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=940, top=82, right=970, bottom=112),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Right B",
                automation_id="",
                class_name="",
                depth=4,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=420, top=80, right=448, bottom=108),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Left action",
                automation_id="",
                class_name="",
                depth=5,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=760, top=80, right=790, bottom=110),
                control_type=wt._UIA_IMAGE_CONTROL_TYPE_ID,
                name="Separated right",
                automation_id="",
                class_name="",
                depth=4,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=900, top=80, right=930, bottom=110),
                control_type=wt._UIA_GROUP_CONTROL_TYPE_ID,
                name="Right A",
                automation_id="",
                class_name="",
                depth=3,
            ),
        ]

        collection = wt._UiaProbe._collect_header_button_candidates(candidates, header)

        self.assertEqual(
            [item.name for item in collection.ordered],
            ["Left action", "Separated right", "Right A", "Right B"],
        )
        self.assertEqual(
            [item.name for item in collection.right_cluster],
            ["Right A", "Right B"],
        )
        self.assertEqual(
            [item.name for item in collection.left_title_actions],
            ["Left action"],
        )

    def test_uia_header_collection_filters_deeper_popup_items(self) -> None:
        header = PhysicalRect(left=370, top=159, right=1399, bottom=205)
        candidates = [
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=714, top=168, right=742, bottom=196),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Stable left",
                automation_id="",
                class_name="",
                depth=13,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=759, top=168, right=789, bottom=196),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Popup left",
                automation_id="",
                class_name="",
                depth=16,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=1203, top=168, right=1230, bottom=196),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Popup right",
                automation_id="",
                class_name="",
                depth=16,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=1235, top=168, right=1266, bottom=196),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Right A",
                automation_id="",
                class_name="",
                depth=13,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=1295, top=168, right=1323, bottom=196),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Right B",
                automation_id="",
                class_name="",
                depth=13,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=1329, top=168, right=1357, bottom=196),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Right C",
                automation_id="",
                class_name="",
                depth=12,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=1363, top=168, right=1391, bottom=196),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Right D",
                automation_id="",
                class_name="",
                depth=12,
            ),
        ]

        collection = wt._UiaProbe._collect_header_button_candidates(candidates, header)

        self.assertEqual([item.name for item in collection.left_title_actions], ["Stable left"])
        self.assertEqual(collection.right_cluster[0].name, "Right A")
        self.assertNotIn("Popup left", [item.name for item in collection.left_title_actions])
        self.assertNotIn("Popup right", [item.name for item in collection.right_cluster])

    def test_uia_header_event_candidates_fall_back_to_geometry_when_text_varies(self) -> None:
        header = PhysicalRect(left=593, top=207, right=1622, bottom=253)
        candidates = [
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=708, top=216, right=734, bottom=244),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="",
                automation_id="left-a",
                class_name="",
                depth=12,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=759, top=216, right=788, bottom=244),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="",
                automation_id="left-b",
                class_name="",
                depth=11,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=1458, top=216, right=1487, bottom=244),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="",
                automation_id="right-a",
                class_name="",
                depth=9,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=1492, top=216, right=1521, bottom=244),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="",
                automation_id="right-b",
                class_name="",
                depth=9,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=1526, top=216, right=1555, bottom=244),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="",
                automation_id="right-c",
                class_name="",
                depth=8,
            ),
        ]

        collection = wt._UiaProbe._collect_header_button_candidates(candidates, header)
        selected = wt._UiaProbe._header_event_candidates(collection)

        self.assertEqual(selected[0].automation_id, "left-b")
        self.assertEqual(
            [item.automation_id for item in selected[1:]],
            ["right-a", "right-b", "right-c"],
        )

    def test_uia_header_roi_title_rejects_tall_scroll_content(self) -> None:
        window = PhysicalRect(left=119, top=123, right=1399, bottom=943)
        scroll_content = wt._UiNode(
            rect=PhysicalRect(left=516, top=42, right=1253, bottom=213),
            control_type=wt._UIA_GROUP_CONTROL_TYPE_ID,
            name="Scrolled transcript group",
            automation_id="",
            class_name="",
            offscreen=False,
        )
        header_row = wt._UiNode(
            rect=PhysicalRect(left=370, top=159, right=1399, bottom=205),
            control_type=wt._UIA_TITLE_BAR_CONTROL_TYPE_ID,
            name="Codex title",
            automation_id="",
            class_name="",
            offscreen=False,
        )

        self.assertIsNone(
            wt._UiaProbe._header_roi_title_candidate(scroll_content, window)
        )
        accepted = wt._UiaProbe._header_roi_title_candidate(header_row, window)

        self.assertIsNotNone(accepted)
        assert accepted is not None
        self.assertEqual(accepted[1], PhysicalRect(left=370, top=159, right=1399, bottom=205))

    def test_uia_candidate_header_rect_groups_compact_header_row(self) -> None:
        window = PhysicalRect(left=119, top=123, right=1399, bottom=943)
        header_left = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=714, top=168, right=742, bottom=196),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="Header left",
            automation_id="",
            class_name="",
            depth=4,
        )
        header_right = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=1235, top=168, right=1266, bottom=196),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="Header right",
            automation_id="",
            class_name="",
            depth=4,
        )
        scroll_candidate = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=714, top=168, right=955, bottom=241),
            control_type=wt._UIA_GROUP_CONTROL_TYPE_ID,
            name="Tall transcript action group",
            automation_id="",
            class_name="",
            depth=8,
        )

        inferred = wt._UiaProbe._candidate_header_rect(
            [scroll_candidate, header_left, header_right],
            window,
        )

        self.assertIsNotNone(inferred)
        assert inferred is not None
        self.assertGreaterEqual(inferred.top, 150)
        self.assertLessEqual(inferred.bottom, 210)

    def test_uia_header_roi_uses_gap_between_left_actions_and_right_cluster(self) -> None:
        header = PhysicalRect(left=400, top=70, right=1000, bottom=120)
        collection = wt._HeaderButtonCollection(
            ordered=(),
            right_cluster=(
                wt._HeaderButtonCandidate(
                    rect=PhysicalRect(left=900, top=80, right=930, bottom=110),
                    control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                    name="Right A",
                    automation_id="",
                    class_name="",
                    depth=3,
                ),
            ),
            left_title_actions=(
                wt._HeaderButtonCandidate(
                    rect=PhysicalRect(left=420, top=80, right=448, bottom=108),
                    control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                    name="More actions",
                    automation_id="",
                    class_name="",
                    depth=4,
                ),
            ),
        )

        roi, reason = wt._UiaProbe._header_roi_rect(collection, header)

        self.assertEqual(reason, "ok")
        self.assertIsNotNone(roi)
        assert roi is not None
        self.assertGreater(roi.left, 448)
        self.assertLess(roi.right, 900)
        self.assertGreaterEqual(roi.top, header.top)
        self.assertLessEqual(roi.bottom, header.bottom)

        too_narrow = wt._HeaderButtonCollection(
            ordered=(),
            right_cluster=collection.right_cluster,
            left_title_actions=(
                wt._HeaderButtonCandidate(
                    rect=PhysicalRect(left=760, top=80, right=875, bottom=108),
                    control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                    name="Wide left action",
                    automation_id="",
                    class_name="",
                    depth=4,
                ),
            ),
        )

        roi, reason = wt._UiaProbe._header_roi_rect(too_narrow, header)

        self.assertIsNone(roi)
        self.assertEqual(reason, "roi-too-narrow")

    def test_uia_header_right_sidebar_marker_requires_right_panel_position(self) -> None:
        window = PhysicalRect(left=0, top=0, right=1280, bottom=820)
        right_panel_marker = wt._UiNode(
            rect=PhysicalRect(left=720, top=180, right=880, bottom=212),
            control_type=wt._UIA_TEXT_CONTROL_TYPE_ID,
            name="OMX Notepad",
            automation_id="",
            class_name="",
            offscreen=False,
        )
        left_chat_text = wt._UiNode(
            rect=PhysicalRect(left=280, top=180, right=440, bottom=212),
            control_type=wt._UIA_TEXT_CONTROL_TYPE_ID,
            name="OMX Notepad",
            automation_id="",
            class_name="",
            offscreen=False,
        )
        top_menu_text = wt._UiNode(
            rect=PhysicalRect(left=180, top=12, right=220, bottom=36),
            control_type=wt._UIA_TEXT_CONTROL_TYPE_ID,
            name="帮助",
            automation_id="",
            class_name="",
            offscreen=False,
        )

        self.assertTrue(wt._UiaProbe._right_sidebar_marker(right_panel_marker, window))
        self.assertFalse(wt._UiaProbe._right_sidebar_marker(left_chat_text, window))
        self.assertFalse(wt._UiaProbe._right_sidebar_marker(top_menu_text, window))

    def test_uia_header_main_titlebar_roi_uses_help_to_minimize_gap(self) -> None:
        window = PhysicalRect(left=0, top=0, right=1280, bottom=820)
        titlebar = wt._UiaProbe._main_titlebar_rect(window)
        help_menu = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=264, top=8, right=292, bottom=34),
            control_type=wt._UIA_MENU_ITEM_CONTROL_TYPE_ID,
            name="帮助",
            automation_id="",
            class_name="",
            depth=3,
        )
        minimize = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=1146, top=8, right=1192, bottom=38),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="最小化",
            automation_id="",
            class_name="",
            depth=3,
        )

        roi = wt._UiaProbe._main_titlebar_roi_rect(
            [help_menu, minimize],
            window,
            titlebar,
        )

        self.assertIsNotNone(roi)
        assert roi is not None
        self.assertGreater(roi.left, help_menu.rect.right)
        self.assertLess(roi.right, minimize.rect.left)
        self.assertEqual(roi.top, 0)
        self.assertEqual(roi.bottom, 38)

    def test_uia_header_main_titlebar_roi_accounts_for_maximized_frame_inset(self) -> None:
        window = PhysicalRect(left=-8, top=-8, right=1928, bottom=1040)
        titlebar = wt._UiaProbe._main_titlebar_rect(window)
        roi = wt._UiaProbe._main_titlebar_roi_rect([], window, titlebar)

        self.assertEqual(titlebar.top, 0)
        self.assertIsNotNone(roi)
        assert roi is not None
        self.assertEqual(roi.top, 0)
        self.assertEqual(roi.bottom, 38)
        self.assertEqual(roi.left, 305)
        self.assertEqual(roi.right, 1772)

    def test_uia_bottom_roi_uses_gap_between_permission_and_model(self) -> None:
        window = PhysicalRect(left=100, top=50, right=1300, bottom=850)
        left = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=220, top=800, right=330, bottom=828),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="完全访问",
            automation_id="",
            class_name="",
            depth=3,
        )
        right = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=1080, top=800, right=1170, bottom=828),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="5.5 超高",
            automation_id="",
            class_name="",
            depth=3,
        )

        roi, reason = wt._UiaProbe._bottom_roi_rect(left, (), right, window)

        self.assertEqual(reason, "ok")
        self.assertIsNotNone(roi)
        assert roi is not None
        self.assertGreater(roi.left, left.rect.right)
        self.assertLess(roi.right, right.rect.left)
        self.assertGreaterEqual(roi.top, left.rect.top)
        self.assertLessEqual(roi.bottom, left.rect.bottom)

    def test_uia_bottom_row_filter_rejects_matching_text_above_composer_row(self) -> None:
        window = PhysicalRect(left=100, top=50, right=1300, bottom=850)
        input_rect = PhysicalRect(left=300, top=730, right=900, bottom=786)
        body_text = wt._UiNode(
            rect=PhysicalRect(left=300, top=620, right=900, bottom=652),
            control_type=wt._UIA_TEXT_CONTROL_TYPE_ID,
            name="完全访问",
            automation_id="",
            class_name="",
            offscreen=False,
        )
        composer_button = wt._UiNode(
            rect=PhysicalRect(left=300, top=800, right=410, bottom=828),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="完全访问",
            automation_id="",
            class_name="",
            offscreen=False,
        )

        body_candidate = wt._UiaProbe._bottom_control_candidate(body_text, window, 4)
        composer_candidate = wt._UiaProbe._bottom_control_candidate(
            composer_button,
            window,
            4,
        )

        self.assertIsNotNone(body_candidate)
        self.assertIsNotNone(composer_candidate)
        assert body_candidate is not None
        assert composer_candidate is not None

        row_candidates = wt._UiaProbe._bottom_roi_row_candidates(
            [body_candidate, composer_candidate],
            input_rect,
            window,
        )

        self.assertEqual([item.name for item in row_candidates], ["完全访问"])
        self.assertEqual(row_candidates[0].rect, composer_candidate.rect)

    def test_uia_bottom_roi_uses_geometry_when_labels_are_omitted(self) -> None:
        window = PhysicalRect(left=250, top=0, right=680, bottom=820)
        input_rect = PhysicalRect(left=282, top=706, right=648, bottom=764)
        plus = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=302, top=780, right=320, bottom=802),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="",
            automation_id="",
            class_name="",
            depth=5,
        )
        shield = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=338, top=780, right=352, bottom=802),
            control_type=wt._UIA_IMAGE_CONTROL_TYPE_ID,
            name="",
            automation_id="",
            class_name="",
            depth=6,
        )
        left_arrow = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=356, top=780, right=370, bottom=802),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="",
            automation_id="",
            class_name="",
            depth=6,
        )
        model_text = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=560, top=780, right=590, bottom=802),
            control_type=wt._UIA_TEXT_CONTROL_TYPE_ID,
            name="5.5",
            automation_id="",
            class_name="",
            depth=6,
        )
        model_arrow = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=594, top=780, right=606, bottom=802),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="",
            automation_id="",
            class_name="",
            depth=6,
        )
        send = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=612, top=772, right=642, bottom=806),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="发送",
            automation_id="",
            class_name="",
            depth=5,
        )

        candidates = [plus, shield, left_arrow, model_text, model_arrow, send]
        row_candidates = wt._UiaProbe._bottom_roi_row_candidates(
            candidates,
            input_rect,
            window,
        )
        left, right = wt._UiaProbe._bottom_roi_controls(row_candidates)
        roi, reason = wt._UiaProbe._bottom_roi_rect(left, (), right, window)

        self.assertEqual(reason, "ok")
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        self.assertIsNotNone(roi)
        assert left is not None
        assert right is not None
        assert roi is not None
        self.assertEqual(left.rect.right, left_arrow.rect.right)
        self.assertEqual(right.rect.left, model_text.rect.left)
        self.assertGreater(roi.left, left_arrow.rect.right)
        self.assertLess(roi.right, model_text.rect.left)

    def test_uia_bottom_text_node_cannot_be_left_permission_control(self) -> None:
        text = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=589, top=933, right=825, bottom=943),
            control_type=wt._UIA_TEXT_CONTROL_TYPE_ID,
            name="正文里的“完全访问”不会污染底栏行。",
            automation_id="",
            class_name="",
            depth=28,
        )
        right = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=1172, top=890, right=1258, bottom=918),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="5.5 超高",
            automation_id="",
            class_name="",
            depth=13,
        )

        left_control, right_control = wt._UiaProbe._bottom_roi_controls([text, right])

        self.assertIsNone(left_control)
        self.assertIs(right_control, right)

    def test_uia_bottom_candidate_accepts_composer_row_pushed_above_bottom_panel(self) -> None:
        window = PhysicalRect(left=250, top=0, right=1280, bottom=820)
        input_rect = PhysicalRect(left=398, top=344, right=1134, bottom=524)
        input_node = wt._UiNode(
            rect=input_rect,
            control_type=wt._UIA_EDIT_CONTROL_TYPE_ID,
            name="composer message input",
            automation_id="",
            class_name="",
            offscreen=False,
        )
        left = wt._UiNode(
            rect=PhysicalRect(left=448, top=490, right=536, bottom=514),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="完全访问",
            automation_id="",
            class_name="",
            offscreen=False,
        )
        right = wt._UiNode(
            rect=PhysicalRect(left=1012, top=490, right=1082, bottom=514),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="5.5 超高",
            automation_id="",
            class_name="",
            offscreen=False,
        )
        terminal_tab = wt._UiNode(
            rect=PhysicalRect(left=266, top=552, right=394, bottom=578),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="D:\\Program Files\\PowerShell",
            automation_id="",
            class_name="",
            offscreen=False,
        )

        self.assertEqual(wt._UiaProbe._score_input_box(input_node, window), 0)
        self.assertGreater(wt._UiaProbe._score_bottom_roi_input_box(input_node, window), 0)

        candidates = [
            wt._UiaProbe._bottom_control_candidate(left, window, 5),
            wt._UiaProbe._bottom_control_candidate(right, window, 5),
            wt._UiaProbe._bottom_control_candidate(terminal_tab, window, 5),
        ]
        compact_candidates = [item for item in candidates if item is not None]
        row_candidates = wt._UiaProbe._bottom_roi_row_candidates(
            compact_candidates,
            input_rect,
            window,
        )
        left_control, right_control = wt._UiaProbe._bottom_roi_controls(row_candidates)
        roi, reason = wt._UiaProbe._bottom_roi_rect(
            left_control,
            (),
            right_control,
            window,
        )

        self.assertEqual([item.name for item in row_candidates], ["完全访问", "5.5 超高"])
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(roi)

    def test_uia_bottom_roi_combined_sidebar_and_bottom_panel_ignores_left_noise(self) -> None:
        window = PhysicalRect(left=169, top=123, right=1449, bottom=943)
        input_rect = PhysicalRect(left=464, top=562, right=805, bottom=606)
        outside_noise = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=360, top=610, right=408, bottom=631),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="",
            automation_id="",
            class_name="",
            depth=17,
        )
        permission_icon = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=460, top=610, right=482, bottom=638),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="",
            automation_id="",
            class_name="",
            depth=12,
        )
        permission_arrow = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=510, top=610, right=541, bottom=638),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="",
            automation_id="",
            class_name="",
            depth=12,
        )
        model = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=720, top=610, right=773, bottom=638),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="5.5",
            automation_id="",
            class_name="",
            depth=13,
        )
        send = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=789, top=606, right=819, bottom=640),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="发送",
            automation_id="",
            class_name="",
            depth=13,
        )

        row_candidates = wt._UiaProbe._bottom_roi_row_candidates(
            [outside_noise, permission_icon, permission_arrow, model, send],
            input_rect,
            window,
        )
        left, right = wt._UiaProbe._bottom_roi_controls(row_candidates)
        roi, reason = wt._UiaProbe._bottom_roi_rect(left, (), right, window)

        self.assertNotIn(outside_noise, row_candidates)
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        self.assertIsNotNone(roi)
        assert left is not None
        assert right is not None
        assert roi is not None
        self.assertGreaterEqual(left.rect.left, permission_icon.rect.left)
        self.assertGreater(roi.left, permission_arrow.rect.right)
        self.assertLess(roi.right, model.rect.left)

    def test_uia_bottom_roi_leaves_room_for_goal_and_plan_tags(self) -> None:
        window = PhysicalRect(left=100, top=50, right=1300, bottom=850)
        left = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=220, top=800, right=330, bottom=828),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="完全访问",
            automation_id="",
            class_name="",
            depth=3,
        )
        plan = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=360, top=800, right=430, bottom=828),
            control_type=wt._UIA_TEXT_CONTROL_TYPE_ID,
            name="计划",
            automation_id="",
            class_name="",
            depth=4,
        )
        goal = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=450, top=800, right=520, bottom=828),
            control_type=wt._UIA_TEXT_CONTROL_TYPE_ID,
            name="目标",
            automation_id="",
            class_name="",
            depth=4,
        )
        usage_text = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=560, top=800, right=900, bottom=828),
            control_type=wt._UIA_TEXT_CONTROL_TYPE_ID,
            name="9.25 18.9M 094%",
            automation_id="",
            class_name="",
            depth=4,
        )
        right = wt._HeaderButtonCandidate(
            rect=PhysicalRect(left=1080, top=800, right=1170, bottom=828),
            control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
            name="选择模型 Ctrl+Shift+M 5.5 超高",
            automation_id="",
            class_name="",
            depth=3,
        )

        blockers = wt._UiaProbe._bottom_left_blockers(
            [left, plan, goal, usage_text, right],
            left,
            right,
        )
        roi, reason = wt._UiaProbe._bottom_roi_rect(left, blockers, right, window)

        self.assertEqual([item.name for item in blockers], ["计划", "目标"])
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(roi)
        assert roi is not None
        self.assertGreater(roi.left, goal.rect.right)
        self.assertLess(roi.left, usage_text.rect.left)

    def test_uia_header_button_collector_logs_candidates_without_dock_changes(self) -> None:
        window = PhysicalRect(left=100, top=50, right=1100, bottom=850)
        candidates = [
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=900, top=80, right=930, bottom=110),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="Open in File Explorer",
                automation_id="",
                class_name="",
                depth=4,
            ),
            wt._HeaderButtonCandidate(
                rect=PhysicalRect(left=420, top=80, right=448, bottom=108),
                control_type=wt._UIA_BUTTON_CONTROL_TYPE_ID,
                name="More actions",
                automation_id="",
                class_name="",
                depth=5,
            ),
        ]

        header = PhysicalRect(left=400, top=70, right=1000, bottom=120)
        with self.assertLogs("codex_usage_hud.windows_tracker", level="INFO") as logs:
            wt._UiaProbe._log_header_button_candidates(candidates, window, header)

        combined = "\n".join(logs.output)
        self.assertIn("uia_header_buttons", combined)
        self.assertIn("right_count=1", combined)
        self.assertIn("right_cluster=", combined)
        self.assertIn("left_title=", combined)
        self.assertIn("Open in File Explorer", combined)
        self.assertIn("More actions", combined)

    def test_disabled_uia_landmarks_stay_geometry_only(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        rect = PhysicalRect(left=230, top=126, right=1482, bottom=872)
        tracker._landmark_cache = wt._Landmarks(
            title_bar=PhysicalRect(left=632, top=162, right=1302, bottom=208),
            input_box=PhysicalRect(left=650, top=520, right=1320, bottom=576),
            source="uia",
        )
        tracker._landmark_cache_hwnd = 123
        tracker._landmark_cache_window_rect = rect
        tracker._landmark_cache_at = 0.0

        def fail_schedule(_: int, __: PhysicalRect) -> None:
            raise AssertionError("UIA refresh should stay disabled")

        tracker._schedule_uia_refresh = fail_schedule  # type: ignore[method-assign]

        landmarks = tracker._landmarks(123, rect)

        self.assertEqual(landmarks.source, "geometry")
        self.assertEqual(landmarks.input_box.bottom, 836)


class UiaHeaderRoiEventWatcherTests(unittest.TestCase):
    class _FakeProbe:
        def __init__(
            self,
            *,
            targets: tuple[int, ...],
            root: int = 22,
        ) -> None:
            self.targets = targets
            self.root = root
            self.add_automation_calls: list[tuple[int, int]] = []
            self.add_property_calls: list[tuple[int, tuple[int, ...]]] = []
            self.remove_automation_calls: list[tuple[int, int]] = []
            self.remove_property_calls: list[int] = []
            self.released: list[int] = []
            self.fast_scan: wt._HeaderRoiScan | None = None

        def _automation_for_thread(self) -> int:
            return 11

        def _element_from_handle(self, automation: int, hwnd: int) -> int:
            return self.root

        def find_header_event_targets(
            self,
            hwnd: int,
            window_rect: PhysicalRect,
        ) -> tuple[int, ...]:
            return self.targets

        def add_automation_event_handler(
            self,
            automation: int,
            event_id: int,
            element: int,
            handler: int,
        ) -> bool:
            self.add_automation_calls.append((event_id, element))
            return True

        def add_property_changed_event_handler(
            self,
            automation: int,
            element: int,
            handler: int,
            property_ids: tuple[int, ...],
        ) -> bool:
            self.add_property_calls.append((element, tuple(property_ids)))
            return True

        def remove_automation_event_handler(
            self,
            automation: int,
            event_id: int,
            element: int,
            handler: int,
        ) -> None:
            self.remove_automation_calls.append((event_id, element))

        def remove_property_changed_event_handler(
            self,
            automation: int,
            element: int,
            handler: int,
        ) -> None:
            self.remove_property_calls.append(element)

        def find_header_roi_from_event_targets(
            self,
            elements: tuple[int, ...],
            window_rect: PhysicalRect,
        ) -> wt._HeaderRoiScan | None:
            del elements, window_rect
            return self.fast_scan

        def _release(self, ptr: int) -> None:
            self.released.append(ptr)

    def _build_watcher(self, probe: "_FakeProbe") -> wt._UiaHeaderRoiEventWatcher:
        tracker = SimpleNamespace(
            _uia_probe=probe,
            invalidate_header_roi_cache=lambda reason: None,
            publish_header_roi_snapshot=lambda snapshot, reason: None,
        )
        watcher = wt._UiaHeaderRoiEventWatcher(tracker)  # type: ignore[arg-type]
        watcher._hwnd = 196764
        watcher._window_rect = PhysicalRect(left=342, top=171, right=1622, bottom=991)
        watcher._message_loop = lambda stop_event: None  # type: ignore[method-assign]
        return watcher

    def test_watcher_uses_anchor_targets_with_root_layout_sentinel(self) -> None:
        probe = self._FakeProbe(targets=(501, 502))
        watcher = self._build_watcher(probe)

        watcher._run(threading.Event())

        self.assertEqual(
            sorted({element for _event_id, element in probe.add_automation_calls}),
            [probe.root, 501, 502],
        )
        self.assertEqual(probe.add_property_calls, [])
        self.assertEqual(
            sorted(
                event_id
                for event_id, element in probe.add_automation_calls
                if element == probe.root
            ),
            [
                wt._UIA_STRUCTURE_CHANGED_EVENT_ID,
                wt._UIA_LAYOUT_INVALIDATED_EVENT_ID,
            ],
        )
        self.assertIn(probe.root, probe.released)
        self.assertIn(501, probe.released)
        self.assertIn(502, probe.released)

    def test_watcher_falls_back_to_root_when_no_anchor_targets_exist(self) -> None:
        probe = self._FakeProbe(targets=())
        watcher = self._build_watcher(probe)

        watcher._run(threading.Event())

        self.assertEqual(
            sorted({element for _event_id, element in probe.add_automation_calls}),
            [probe.root],
        )
        self.assertEqual(
            sorted({element for element, _props in probe.add_property_calls}),
            [probe.root],
        )
        self.assertEqual(probe.released, [probe.root])

    def test_header_roi_invalidation_notifies_callback(self) -> None:
        tracker = object.__new__(CodexWindowTracker)
        tracker._uia_lock = threading.Lock()
        tracker._header_roi_cache_at = 10.0
        tracker._header_roi_cache = object()
        tracker._header_roi_cache_hwnd = 123
        tracker._header_roi_cache_window_rect = PhysicalRect(1, 2, 3, 4)
        reasons: list[str] = []
        tracker._header_roi_change_callback = reasons.append

        tracker.invalidate_header_roi_cache("uia-event")

        self.assertEqual(reasons, ["uia-event"])
        self.assertIsNone(tracker._header_roi_cache)
        self.assertEqual(tracker._header_roi_cache_hwnd, 0)

    def test_event_driven_header_roi_cache_does_not_expire_by_ttl(self) -> None:
        class _Probe:
            def __init__(self) -> None:
                self.calls = 0

            def find_header_roi(self, hwnd: int, window_rect: PhysicalRect) -> None:
                del hwnd, window_rect
                self.calls += 1
                return None

        rect = PhysicalRect(100, 50, 900, 650)
        roi = PhysicalRect(300, 70, 700, 110)
        probe = _Probe()
        tracker = object.__new__(CodexWindowTracker)
        tracker.enabled = True
        tracker.enable_uia = True
        tracker._uia_probe = probe
        tracker._uia_lock = threading.Lock()
        tracker._header_roi_cache_at = 0.0
        tracker._header_roi_cache = wt.HeaderRoiSnapshot(
            status="visible",
            hwnd=77,
            window_rect=rect,
            roi=roi,
        )
        tracker._header_roi_cache_hwnd = 77
        tracker._header_roi_cache_window_rect = rect
        tracker._header_roi_event_watcher = SimpleNamespace(
            is_event_driven_for=lambda hwnd: hwnd == 77,
        )
        tracker._ensure_header_roi_event_watcher = lambda hwnd, window_rect: None
        tracker.get_window_snapshot = lambda: wt.DockSnapshot(
            status="visible",
            hwnd=77,
            window_rect=rect,
        )

        snapshot = tracker.get_header_roi_snapshot()

        self.assertEqual(probe.calls, 0)
        self.assertEqual(snapshot.roi, roi)

    def test_watcher_publishes_fast_snapshot_on_debounced_event(self) -> None:
        probe = self._FakeProbe(targets=(501, 502))
        probe.fast_scan = wt._HeaderRoiScan(
            header_rect=PhysicalRect(342, 171, 1622, 216),
            collection=wt._HeaderButtonCollection(
                ordered=(),
                right_cluster=(),
                left_title_actions=(),
            ),
            roi=PhysicalRect(521, 171, 1348, 209),
            nodes=2,
            reason="event-target-main-titlebar",
        )
        published: list[tuple[wt.HeaderRoiSnapshot, str]] = []
        tracker = SimpleNamespace(
            _uia_probe=probe,
            invalidate_header_roi_cache=lambda reason: published.append((None, reason)),  # type: ignore[arg-type]
            publish_header_roi_snapshot=lambda snapshot, reason: published.append((snapshot, reason)),
        )
        watcher = wt._UiaHeaderRoiEventWatcher(tracker)  # type: ignore[arg-type]
        watcher._hwnd = 196764
        watcher._window_rect = PhysicalRect(left=342, top=171, right=1622, bottom=991)
        watcher._owned_target_elements = (501, 502)
        watcher._last_event_at = time.monotonic() - 0.2

        watcher._drain_debounced_event()

        self.assertEqual(len(published), 1)
        snapshot, reason = published[0]
        assert snapshot is not None
        self.assertEqual(reason, "uia-event")
        self.assertEqual(snapshot.roi, PhysicalRect(521, 171, 1348, 209))


class CodexWindowTrackerSelectionTests(unittest.TestCase):
    def test_browser_title_with_codex_is_not_codex_candidate(self) -> None:
        self.assertFalse(
            CodexWindowTracker._looks_like_codex(
                "Codex documentation - Chrome",
                "Chrome_WidgetWin_1",
                "chrome.exe",
            )
        )
        self.assertFalse(
            CodexWindowTracker._is_stable_candidate(
                wt._WindowCandidate(
                    hwnd=101,
                    title="Codex documentation - Chrome",
                    class_name="Chrome_WidgetWin_1",
                    process="chrome.exe",
                    rect=PhysicalRect(left=40, top=20, right=1320, bottom=840),
                    visible=True,
                    minimized=False,
                    cloaked=False,
                )
            )
        )

    def test_renamed_chatgpt_exe_still_looks_like_codex(self) -> None:
        # Codex Desktop 26.707+ renamed the Electron GUI to ChatGPT.exe.  The
        # tracker must still classify that process as the Codex window,
        # otherwise the HUD will sit forever in "waiting for Codex" state on
        # freshly-updated installs.
        self.assertTrue(
            CodexWindowTracker._looks_like_codex(
                "Codex",
                "Chrome_WidgetWin_1",
                "ChatGPT.exe",
            )
        )
        # A ChatGPT.exe process with no title on the top-level chrome frame
        # is also acceptable (the main window occasionally reports empty
        # titles between transitions).
        self.assertTrue(
            CodexWindowTracker._looks_like_codex(
                "",
                "Chrome_WidgetWin_1",
                "chatgpt.exe",
            )
        )

    def test_cached_hidden_window_does_not_block_visible_codex_window(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        hidden = wt._WindowCandidate(
            hwnd=101,
            title="",
            class_name="Chrome_WidgetWin_0",
            process="Codex.exe",
            rect=PhysicalRect(left=40, top=20, right=980, bottom=720),
            visible=False,
            minimized=False,
            cloaked=False,
        )
        visible = wt._WindowCandidate(
            hwnd=202,
            title="Codex",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=PhysicalRect(left=120, top=60, right=1360, bottom=860),
            visible=True,
            minimized=False,
            cloaked=False,
        )

        tracker.user32 = SimpleNamespace(IsWindow=lambda hwnd: True)
        tracker._last_hwnd = hidden.hwnd
        tracker._last_hwnd_verified_at = time.monotonic()
        tracker._candidate_from_hwnd = (  # type: ignore[method-assign]
            lambda hwnd, verify_codex=False: hidden
            if hwnd == hidden.hwnd
            else visible
            if hwnd == visible.hwnd
            else None
        )
        tracker._findwindow_candidates = lambda: [hidden.hwnd, visible.hwnd]  # type: ignore[method-assign]
        tracker._enum_window_candidates = lambda: []  # type: ignore[method-assign]

        hwnd = tracker.find_main_window()

        self.assertEqual(hwnd, visible.hwnd)
        self.assertEqual(tracker._last_hwnd, visible.hwnd)
        self.assertGreater(tracker._last_hwnd_verified_at, 0.0)

    def test_empty_title_popup_does_not_replace_cached_minimized_main_window(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        minimized_main = wt._WindowCandidate(
            hwnd=101,
            title="Codex",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=None,
            visible=True,
            minimized=True,
            cloaked=False,
        )
        popup = wt._WindowCandidate(
            hwnd=202,
            title="",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=PhysicalRect(left=120, top=60, right=420, bottom=360),
            visible=True,
            minimized=False,
            cloaked=False,
        )

        tracker.user32 = SimpleNamespace(IsWindow=lambda hwnd: True)
        tracker._last_hwnd = minimized_main.hwnd
        tracker._last_hwnd_verified_at = time.monotonic()
        tracker._candidate_from_hwnd = (  # type: ignore[method-assign]
            lambda hwnd, verify_codex=False: minimized_main
            if hwnd == minimized_main.hwnd
            else popup
            if hwnd == popup.hwnd
            else None
        )
        tracker._findwindow_candidates = lambda: [popup.hwnd]  # type: ignore[method-assign]
        tracker._enum_window_candidates = lambda: []  # type: ignore[method-assign]

        hwnd = tracker.find_main_window()

        self.assertEqual(hwnd, minimized_main.hwnd)
        self.assertEqual(tracker._last_hwnd, minimized_main.hwnd)
        self.assertGreater(tracker._last_hwnd_verified_at, 0.0)

    def test_allow_inactive_finds_minimized_main_window_without_cache(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        minimized_main = wt._WindowCandidate(
            hwnd=101,
            title="Codex",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=None,
            visible=True,
            minimized=True,
            cloaked=False,
        )
        popup = wt._WindowCandidate(
            hwnd=202,
            title="",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=PhysicalRect(left=120, top=60, right=420, bottom=360),
            visible=True,
            minimized=False,
            cloaked=False,
        )

        tracker.user32 = SimpleNamespace(IsWindow=lambda hwnd: True)
        tracker._candidate_from_hwnd = (  # type: ignore[method-assign]
            lambda hwnd, verify_codex=False: minimized_main
            if hwnd == minimized_main.hwnd
            else popup
            if hwnd == popup.hwnd
            else None
        )
        tracker._findwindow_candidates = lambda: [minimized_main.hwnd, popup.hwnd]  # type: ignore[method-assign]
        tracker._enum_window_candidates = lambda: []  # type: ignore[method-assign]

        hwnd = tracker.find_main_window(allow_inactive=True)

        self.assertEqual(hwnd, minimized_main.hwnd)

    def test_empty_title_chrome_widgetwin_zero_does_not_win_over_titled_main(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        wrong_surface = wt._WindowCandidate(
            hwnd=101,
            title="",
            class_name="Chrome_WidgetWin_0",
            process="Codex.exe",
            rect=PhysicalRect(left=40, top=20, right=1320, bottom=840),
            visible=True,
            minimized=False,
            cloaked=False,
        )
        main = wt._WindowCandidate(
            hwnd=202,
            title="Codex",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=PhysicalRect(left=120, top=60, right=1360, bottom=860),
            visible=True,
            minimized=False,
            cloaked=False,
        )

        tracker.user32 = SimpleNamespace(IsWindow=lambda hwnd: True)
        tracker._candidate_from_hwnd = (  # type: ignore[method-assign]
            lambda hwnd, verify_codex=False: wrong_surface
            if hwnd == wrong_surface.hwnd
            else main
            if hwnd == main.hwnd
            else None
        )
        tracker._findwindow_candidates = lambda: [wrong_surface.hwnd, main.hwnd]  # type: ignore[method-assign]
        tracker._enum_window_candidates = lambda: []  # type: ignore[method-assign]

        hwnd = tracker.find_main_window(allow_inactive=True)

        self.assertEqual(hwnd, main.hwnd)

    def test_is_active_keeps_codex_owned_popup_active(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        tracker.enabled = True
        pid_map = {123: 41001, 999: 41001}

        def get_pid(hwnd: int, pid_ptr: object) -> int:
            hwnd_value = int(getattr(hwnd, "value", hwnd) or 0)
            pid_ptr._obj.value = pid_map.get(hwnd_value, 0)  # type: ignore[attr-defined]
            return 1

        tracker.user32 = SimpleNamespace(
            IsIconic=lambda hwnd: False,
            IsWindowVisible=lambda hwnd: True,
            GetForegroundWindow=lambda: 999,
            GetWindowThreadProcessId=get_pid,
        )
        tracker._is_cloaked = lambda hwnd: False  # type: ignore[method-assign]
        tracker._process_name = lambda pid: "Codex.exe"  # type: ignore[method-assign]

        self.assertTrue(tracker.is_active(123, {456}))

    def test_is_active_rejects_minimized_same_process_foreground(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        tracker.enabled = True
        pid_map = {123: 41001, 999: 41001}

        def get_pid(hwnd: int, pid_ptr: object) -> int:
            hwnd_value = int(getattr(hwnd, "value", hwnd) or 0)
            pid_ptr._obj.value = pid_map.get(hwnd_value, 0)  # type: ignore[attr-defined]
            return 1

        tracker.user32 = SimpleNamespace(
            IsIconic=lambda hwnd: int(getattr(hwnd, "value", hwnd) or 0) == 999,
            IsWindowVisible=lambda hwnd: True,
            GetForegroundWindow=lambda: 999,
            GetWindowThreadProcessId=get_pid,
        )
        tracker._is_cloaked = lambda hwnd: False  # type: ignore[method-assign]
        tracker._process_name = lambda pid: "Codex.exe"  # type: ignore[method-assign]

        self.assertFalse(tracker.is_active(123, {456}))

    def test_is_active_goes_inactive_for_other_process_foreground(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        tracker.enabled = True
        pid_map = {123: 41001, 999: 52002}

        def get_pid(hwnd: int, pid_ptr: object) -> int:
            hwnd_value = int(getattr(hwnd, "value", hwnd) or 0)
            pid_ptr._obj.value = pid_map.get(hwnd_value, 0)  # type: ignore[attr-defined]
            return 1

        tracker.user32 = SimpleNamespace(
            IsIconic=lambda hwnd: False,
            IsWindowVisible=lambda hwnd: True,
            GetForegroundWindow=lambda: 999,
            GetWindowThreadProcessId=get_pid,
        )
        tracker._is_cloaked = lambda hwnd: False  # type: ignore[method-assign]
        tracker._process_name = lambda pid: "Explorer.exe"  # type: ignore[method-assign]

        self.assertFalse(tracker.is_active(123, {456}))


class CodexWindowTrackerActivationTests(unittest.TestCase):
    def test_minimized_chatgpt_main_window_survives_icon_rect_size_filter(self) -> None:
        main = SimpleNamespace(
            title="ChatGPT", class_name="Chrome_WidgetWin_1", process="ChatGPT.exe",
            visible=True, minimized=True, cloaked=False, rect=PhysicalRect(0, 0, 160, 28),
        )
        self.assertTrue(CodexWindowTracker._is_restore_candidate(main))
        self.assertFalse(CodexWindowTracker._is_stable_candidate(main))

    def test_computer_use_overlay_is_never_a_main_window_candidate(self) -> None:
        overlay = SimpleNamespace(
            title="ChatGPT is using your computer. Esc to cancel",
            class_name="CodexComputerUseSwiftOverlay", process="ChatGPT.exe",
            visible=True, minimized=False, cloaked=False,
            rect=PhysicalRect(0, 0, 1920, 1080),
        )
        self.assertFalse(CodexWindowTracker._looks_like_codex(
            overlay.title, overlay.class_name, overlay.process))
        self.assertFalse(CodexWindowTracker._is_stable_candidate(overlay))
        self.assertFalse(CodexWindowTracker._is_restore_candidate(overlay))

    def test_foreground_verification_excludes_helper_and_other_chat_windows(self) -> None:
        tracker = object.__new__(CodexWindowTracker)
        tracker.enabled = True
        tracker._is_cloaked = lambda hwnd: False
        tracker.user32 = SimpleNamespace(
            GetForegroundWindow=lambda: 999,
            IsWindowVisible=lambda hwnd: True,
            IsIconic=lambda hwnd: False,
        )
        self.assertFalse(tracker.is_main_window_foreground(123))
        tracker.user32.GetForegroundWindow = lambda: 123
        self.assertTrue(tracker.is_main_window_foreground(123))
        tracker.user32.IsIconic = lambda hwnd: True
        self.assertFalse(tracker.is_main_window_foreground(123))

    @staticmethod
    def _value(raw: object) -> int:
        return int(getattr(raw, "value", raw) or 0)

    def test_activate_window_restores_and_attaches_foreground_threads(self) -> None:
        tracker = object.__new__(CodexWindowTracker)
        calls: list[tuple[str, int, int, bool] | tuple[str, int, int] | tuple[str, int]] = []

        def get_window_thread_process_id(hwnd: object, _: object) -> int:
            hwnd_value = self._value(hwnd)
            if hwnd_value == 700:
                return 701
            if hwnd_value == 123:
                return 1230
            return 0

        def attach_thread_input(src: object, dst: object, attach: bool) -> int:
            calls.append(("attach", self._value(src), self._value(dst), bool(attach)))
            return 1

        tracker.kernel32 = SimpleNamespace(GetCurrentThreadId=lambda: 500)
        tracker.user32 = SimpleNamespace(
            GetForegroundWindow=lambda: 700,
            GetWindowThreadProcessId=get_window_thread_process_id,
            AttachThreadInput=attach_thread_input,
            ShowWindow=lambda hwnd, cmd: calls.append(("show", self._value(hwnd), int(cmd))) or 1,
            BringWindowToTop=lambda hwnd: calls.append(("top", self._value(hwnd))) or 1,
            SetActiveWindow=lambda hwnd: calls.append(("active", self._value(hwnd))) or hwnd,
            SetForegroundWindow=lambda hwnd: calls.append(("foreground", self._value(hwnd))) or 1,
            SetFocus=lambda hwnd: calls.append(("focus", self._value(hwnd))) or hwnd,
        )

        tracker._activate_window(123)

        self.assertEqual(
            calls,
            [
                ("attach", 500, 701, True),
                ("attach", 500, 1230, True),
                ("show", 123, 9),
                ("top", 123),
                ("active", 123),
                ("foreground", 123),
                ("focus", 123),
                ("attach", 500, 1230, False),
                ("attach", 500, 701, False),
            ],
        )

    def test_activate_window_skips_redundant_thread_attach(self) -> None:
        tracker = object.__new__(CodexWindowTracker)
        calls: list[tuple[str, int, int, bool] | tuple[str, int, int] | tuple[str, int]] = []

        tracker.kernel32 = SimpleNamespace(GetCurrentThreadId=lambda: 500)
        tracker.user32 = SimpleNamespace(
            GetForegroundWindow=lambda: 123,
            GetWindowThreadProcessId=lambda hwnd, _: 500,
            AttachThreadInput=lambda src, dst, attach: calls.append(
                ("attach", self._value(src), self._value(dst), bool(attach))
            )
            or 1,
            ShowWindow=lambda hwnd, cmd: calls.append(("show", self._value(hwnd), int(cmd))) or 1,
            BringWindowToTop=lambda hwnd: calls.append(("top", self._value(hwnd))) or 1,
            SetActiveWindow=lambda hwnd: calls.append(("active", self._value(hwnd))) or hwnd,
            SetForegroundWindow=lambda hwnd: calls.append(("foreground", self._value(hwnd))) or 1,
            SetFocus=lambda hwnd: calls.append(("focus", self._value(hwnd))) or hwnd,
        )

        tracker._activate_window(123)

        self.assertEqual(
            calls,
            [
                ("show", 123, 9),
                ("top", 123),
                ("active", 123),
                ("foreground", 123),
                ("focus", 123),
            ],
        )


if __name__ == "__main__":
    unittest.main()
