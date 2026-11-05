"""New-chat HUD placement must leave native header controls clickable."""

from __future__ import annotations

import subprocess

import pytest

from codex_usage_hud.renderer_assets.layout_anchors import TEXT as ANCHORS
from codex_usage_hud.renderer_assets.layout_observers import TEXT as OBSERVERS


HARNESS = r"""
const assert = require('node:assert/strict');
global.innerWidth = 1225; global.innerHeight = 749;
const rootId = 'hud';
const normalize = value => String(value || '').trim();
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const visible = node => !!node && !node.hidden;
const PANEL = {top: {collapsedHeight: 36}};
const minWidthFor = (_name, expanded) => expanded ? 220 : 120;
let topSlotCache = null;
let titleText = '';
const activeSessionHeaderTitleText = () => titleText;
const activeSessionTitleIsNewSession = text => /^(new chat|新聊天)$/i.test(text || '');
const headerRect = {left: 290, right: 1168, top: 44, bottom: 96, width: 878, height: 52};
const button = (left, right, {top = 56, bottom = 84, hud = false, hidden = false} = {}) => ({
  hidden, textContent: '',
  getBoundingClientRect: () => ({left, right, top, bottom, width: right-left, height: bottom-top}),
  closest: () => hud ? {} : null,
  getAttribute: () => null, querySelectorAll: () => [],
});
let buttons = [button(1140, 1168), button(1181, 1209)];
const header = {
  getBoundingClientRect: () => headerRect,
  querySelectorAll: selector => selector.startsWith('button') ? buttons.slice(0, 1) : [],
};
let headerNode = header;
const conversationHeaderElement = () => headerNode;
const composerElement = () => ({getBoundingClientRect: () => ({left: 310, right: 1160})});
global.document = {querySelectorAll: () => buttons};
global.getComputedStyle = () => ({paddingLeft: '8', paddingRight: '7', columnGap: '6'});
const budgetDomain = {measureCollapsedProgressStrip: () => ({required: 400})};
const panel = {
  dataset: {expanded: 'false'},
  querySelector: selector => selector === '.codex-usage-hud-settings-button'
    ? {getBoundingClientRect: () => ({width: 22})} : null,
};
function assertClear(rect) {
  for (const node of buttons.filter(node => !node.hidden && !node.closest())) {
    const b = node.getBoundingClientRect();
    assert.ok(rect.left + rect.width <= b.left || rect.left >= b.right
      || rect.top + rect.height <= b.top || rect.top >= b.bottom, JSON.stringify({rect, b}));
  }
}
"""


def run_geometry(checks: str) -> None:
    source = ANCHORS[
        ANCHORS.index("      function headerLeftControlEdge"):
        ANCHORS.index("      function footerControlRects")
    ] + ANCHORS[ANCHORS.index("      function manualTopRect"):]
    run_node(HARNESS + source + checks)


def run_node(script: str) -> None:
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"], input=script, text=True,
        encoding="utf-8", capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("width", [640, 900, 1225])
def test_new_chat_docks_before_all_native_controls(width: int) -> None:
    run_geometry(f"innerWidth = {width};\n" + r"""
headerRect.right = innerWidth - 57;
headerRect.width = headerRect.right - headerRect.left;
// The first icon has no accessible name; the second is outside the main slot.
buttons = [button(innerWidth - 125, innerWidth - 97), button(innerWidth - 44, innerWidth - 16)];
for (titleText of ['', 'New chat', '新聊天']) {
  const anchor = fitTopContentRect(panel, topAnchor(36));
  assert.equal(anchor.area.right, innerWidth - 135);
  assert.equal(anchor.left + anchor.width, anchor.area.right);
  assert.equal(anchor.blocked, false);
  assertClear(anchor);
  const saved = manualTopRect({x: innerWidth - 80, y: 4, width: 900}, anchor, false, 36);
  assert.equal(saved.top, anchor.top);
  assert.ok(saved.left >= anchor.area.left);
  assertClear({...saved, height: 36});
}
""")


def test_new_chat_uses_largest_gap_and_remeasures_moved_sibling_controls() -> None:
    run_geometry(r"""
buttons = [button(298, 600), button(1140, 1168), button(1181, 1209)];
let anchor = fitTopContentRect(panel, topAnchor(36));
assert.equal(anchor.area.left, 610);
assert.equal(anchor.area.right, 1130);
assertClear(anchor);
// A sibling can cross the selected main slot's boundary; protect its full hit box.
buttons[2] = button(1100, 1190);
anchor = fitTopContentRect(panel, topAnchor(36));
assert.equal(anchor.area.right, 1090);
assertClear(anchor);
// HUD descendants, hidden controls and controls on a different row do not consume space.
buttons.push(button(650, 900, {hud: true}), button(650, 900, {hidden: true}),
  button(650, 900, {top: 4, bottom: 32}));
assert.equal(topAnchor(36).area.right, 1090);
""")


def test_missing_header_still_avoids_top_right_controls() -> None:
    run_geometry(r"""
headerNode = null;
buttons = [button(1140, 1168, {top: 4, bottom: 32}), button(1181, 1209, {top: 4, bottom: 32})];
const anchor = fitTopContentRect(panel, topAnchor(36));
assert.equal(anchor.area.right, 1130);
assertClear(anchor);
""")


def test_no_safe_space_blocks_hud_until_controls_leave() -> None:
    run_geometry(r"""
buttons = [button(298, 1168)];
assert.equal(topAnchor(36).blocked, true);
buttons = [];
assert.equal(topAnchor(36).blocked, false);
assertClear(topAnchor(36));
""")


def test_header_control_changes_reflow_manual_hud_without_polling() -> None:
    source = OBSERVERS[
        OBSERVERS.index("      function layoutMutationTarget"):
        OBSERVERS.index("      let observedTitleToolbarNode")
    ]
    run_node(r"""
const assert = require('node:assert/strict');
const rootId = 'hud';
const runtimeIsCurrent = () => true, ctx = {lifecycle: {active: () => true}};
const elementFromMutationNode = node => node;
const cachedHeaderNode = null, cachedComposerNode = null;
const mutationTouchesHeaderTitleScope = () => false;
const headerScopeSelector = () => 'header';
const getPanelState = () => ({manual: true});
let reflows = 0;
const invalidateHeaderAnchor = () => reflows++;
const scheduleForPanels = names => assert.deepEqual(names, ['top']);
const syncPositionSettled = () => {};
const button = {matches: () => true};
const header = {closest: selector => selector === '#hud' ? null : header};
const unrelated = {closest: () => null};
""" + source + r"""
handleLayoutMutations([{target: header, addedNodes: [button]}]);
assert.equal(reflows, 1);
handleLayoutMutations([{target: header, removedNodes: [button]}]);
assert.equal(reflows, 2);
handleLayoutMutations([{target: unrelated, addedNodes: [button]}]);
assert.equal(reflows, 2);
""")
