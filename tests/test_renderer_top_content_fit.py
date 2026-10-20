"""Top HUD borrows title space only for clipped, non-scrolling content."""

from __future__ import annotations

import subprocess

import pytest

from codex_usage_hud.renderer_assets.budget import TEXT as BUDGET
from codex_usage_hud.renderer_assets.layout_anchors import TEXT as ANCHORS
from codex_usage_hud.renderer_assets.layout_observers import TEXT as OBSERVERS


HARNESS = r'''
const assert = require('node:assert/strict');
function style() {
  return {
    removeProperty(name) { delete this[name.replace(/-([a-z])/g, (_, c) => c.toUpperCase())]; },
    setProperty(name, value) { this[name] = value; },
  };
}
let available = 400, railWidths = [160, 170, 170];
const rails = () => railWidths.map(width => ({
  style: style(),
  querySelector: () => ({scrollWidth: width - 2, getBoundingClientRect: () => ({width: width - 2})}),
}));
let items = rails();
const viewport = {classList: {contains: () => true}, get clientWidth() {return available}};
const strip = {
  classList: {contains: () => true}, isConnected: true, parentElement: viewport,
  dataset: {}, style: style(), querySelectorAll: () => items,
};
global.getComputedStyle = () => ({columnGap: '7px'});
global.innerWidth = 1000;
global.innerHeight = 600;
const PANEL = {top: {collapsedHeight: 36}};
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const minWidthFor = (_name, expanded) => expanded ? 220 : 120;
let header = {left: 20, top: 8, width: 960, height: 50};
let slot = {left: 500, right: 951, width: 451};
const conversationHeaderElement = () => ({getBoundingClientRect: () => header});
const topHeaderSlot = () => slot;
const visible = () => true;
let manual = false, scheduled = 0;
const getPanelState = () => ({manual, width: 130});
const scheduleForPanels = names => {assert.deepEqual(names, ['top']); scheduled++};
const panel = {dataset: {expanded: 'false'}, querySelector: () => strip};
strip.closest = () => panel;
const rootId = 'hud';
global.document = {querySelector: () => panel};
'''


def run_behavior(checks: str, *, sync: bool = False) -> None:
    budget = BUDGET.split("  const budgetDomain = ctx.domains.register(", 1)[0]
    anchors = ANCHORS[ANCHORS.index("      function topAnchor"):ANCHORS.index("      function footerControlRects")]
    refresh = OBSERVERS[OBSERVERS.index("      function refreshTopContentFit"):OBSERVERS.index("      function applyPanelToggleGeometry")]
    controller = OBSERVERS[:OBSERVERS.index("      function applyPanelToggleGeometry")] if sync else refresh
    script = HARNESS + budget + "const budgetDomain = createBudgetDomain({}, {});\n" + anchors + controller + checks
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=script, text=True, encoding="utf-8", capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("available", "scrolling", "borrow"),
    [(600, False, False), (514, False, False), (513, False, False),
     (400, False, True), (255, False, True), (254, True, False), (100, True, False)],
    ids=["short-title", "exact-fit", "one-pixel-tolerance", "clipped-middle",
         "above-marquee-boundary", "at-marquee-boundary", "narrow-marquee"],
)
def test_top_content_fit_preserves_full_fit_and_marquee(available: int, scrolling: bool, borrow: bool) -> None:
    run_behavior(f'''
available = {available};
slot = {{left: 951 - available - 51, right: 951, width: available + 51}};
const anchor = topAnchor(36);
const layout = budgetDomain.measureCollapsedProgressStrip(strip);
assert.equal(layout.scrolling, {str(scrolling).lower()});
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow === 'true', {str(scrolling).lower()});
const fitted = fitTopContentRect(panel, anchor);
assert.equal(fitted !== anchor, {str(borrow).lower()});
if (fitted !== anchor) {{
  assert.equal(fitted.left + fitted.width, anchor.area.right);
  assert.equal(fitted.width, 565);
  assert.equal(fitted.top, anchor.top);
}}
''')


def test_top_content_fit_restores_on_updates_and_respects_manual_and_expanded() -> None:
    run_behavior(r'''
refreshTopContentFit(); assert.equal(scheduled, 1);
manual = true; refreshTopContentFit(); assert.equal(scheduled, 1);
assert.equal(strip.dataset.overflow, 'true');
manual = false; panel.dataset.expanded = 'true';
const anchor = topAnchor(36);
assert.equal(fitTopContentRect(panel, anchor), anchor);
refreshTopContentFit(); assert.equal(scheduled, 1);
panel.dataset.expanded = 'false'; available = 600;
refreshTopContentFit(); assert.equal(scheduled, 1);
panel.dataset.topContentFit = 'true';
refreshTopContentFit(); assert.equal(scheduled, 2);
available = 200; delete panel.dataset.topContentFit;
refreshTopContentFit(); assert.equal(scheduled, 2);
strip.isConnected = false;
assert.equal(fitTopContentRect(panel, anchor), anchor);
''')


def test_top_content_fit_clamps_to_existing_cover_area_and_handles_single_metric() -> None:
    run_behavior(r'''
railWidths = [160, 500, 500]; items = rails(); available = 400;
const anchor = topAnchor(36);
const fitted = fitTopContentRect(panel, anchor);
assert.equal(fitted.left, 180);
assert.equal(fitted.width, 771);
assert.equal(fitted.left + fitted.width, anchor.area.right);
railWidths = [500]; items = rails(); available = 400;
assert.equal(budgetDomain.measureCollapsedProgressStrip(strip).scrolling, false);
assert.notEqual(fitTopContentRect(panel, anchor), anchor);
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow, undefined);
assert.equal(items[0].style.width, undefined);
strip.querySelectorAll = () => [];
assert.equal(fitTopContentRect(panel, anchor), anchor);
assert.equal(fitTopContentRect(panel, {...anchor, source: 'fallback'}).source, 'fallback');
''')


def test_sync_position_rechecks_native_slot_and_restores_without_persisting_geometry() -> None:
    run_behavior(r'''
const ctx = {lifecycle: {active: () => true}};
const runtimeIsCurrent = () => true;
const noop = () => {};
const positionStartupBubble = noop, positionRestReminderBubble = noop;
const applyPanelStates = noop, applyRuntimeErrorsPanelState = noop;
const refreshLayoutObservers = noop, startBootstrapObserver = noop;
const desktopChromeMissing = () => false;
const desiredHeight = () => 36;
const refreshAllMarquees = () => budgetDomain.refreshCollapsedProgressStrip(strip);
document.getElementById = () => ({querySelector: () => panel});
function applyRect(_panel, left, top, width, height) {
  panel.rect = {left, top, width, height}; available = width - 51;
}
const state = getPanelState('top');
syncPosition(['top']);
assert.equal(panel.rect.width, 565);
assert.equal(panel.rect.left, 386);
assert.equal(panel.dataset.topContentFit, 'true');
// Repeated settle passes must not grow or oscillate the fitted rectangle.
syncPosition(['top']);
assert.equal(panel.rect.width, 565);
assert.equal(panel.rect.left, 386);
// A wider native slot returns to normal automatic placement.
slot = {left: 300, right: 951, width: 651};
syncPosition(['top']);
assert.equal(panel.rect.width, 651);
assert.equal(panel.rect.left, 300);
assert.equal(panel.dataset.topContentFit, undefined);
// Narrow native slots still enter the original marquee path.
slot = {left: 720, right: 951, width: 231};
syncPosition(['top']);
assert.equal(panel.rect.width, 231);
assert.equal(panel.rect.left, 720);
assert.equal(strip.dataset.overflow, 'true');
assert.deepEqual(state, {manual: false, width: 130});
// No room after a title: reuse the existing title-cover area.
slot = {left: 950, right: 951, width: 1};
syncPosition(['top']);
assert.equal(panel.rect.width, 771);
assert.equal(strip.dataset.overflow, undefined);
// Borrowing is bounded: very long totals must scroll after reaching the bound.
railWidths = [160, 500, 500]; items = rails();
slot = {left: 500, right: 951, width: 451};
syncPosition(['top']);
assert.equal(panel.rect.width, 771);
assert.equal(panel.dataset.topContentScroll, 'true');
assert.equal(strip.dataset.overflow, 'true');
assert.equal(topAnchor(390).width, 451);
slot = {left: 950, right: 951, width: 1};
assert.equal(topAnchor(390).width, 771);
''', sync=True)


def test_top_content_width_sweep_has_no_static_clipping() -> None:
    run_behavior(r'''
const ctx = {lifecycle: {active: () => true}};
const runtimeIsCurrent = () => true;
const noop = () => {};
const positionStartupBubble = noop, positionRestReminderBubble = noop;
const applyPanelStates = noop, applyRuntimeErrorsPanelState = noop;
const refreshLayoutObservers = noop, startBootstrapObserver = noop;
const desktopChromeMissing = () => false;
const desiredHeight = () => 36;
const refreshAllMarquees = () => budgetDomain.refreshCollapsedProgressStrip(strip);
document.getElementById = () => ({querySelector: () => panel});
function applyRect(_panel, left, top, width, height) {
  panel.rect = {left, top, width, height}; available = Math.max(1, width - 51);
}
for (const scale of [.8, 1, 1.25, 1.5, 2]) {
  for (const baseWidths of [[160], [160, 170], [160, 170, 170], [160, 300, 90], [160, 500, 500]]) {
    railWidths = baseWidths.map(width => Math.ceil(width * scale)); items = rails();
    for (let width = 1; width <= 800; width += 7) {
      slot = {left: 951 - width, right: 951, width};
      syncPosition(['top']);
      const layout = budgetDomain.measureCollapsedProgressStrip(strip);
      assert.ok(!layout.clipped || strip.dataset.overflow === 'true', JSON.stringify({width, railWidths, layout}));
      assert.ok(panel.rect.width >= 120);
      assert.ok(panel.rect.left >= 8 && panel.rect.left + panel.rect.width <= 951);
      const previous = {...panel.rect};
      syncPosition(['top']);
      assert.deepEqual(panel.rect, previous);
    }
  }
}
''', sync=True)


def test_intrinsic_measurement_does_not_inherit_stretched_rail_width() -> None:
    run_behavior(r'''
const copy = width => ({scrollWidth: width, getBoundingClientRect: () => ({width})});
items[0].querySelector = () => ({children: [copy(100), copy(200)], scrollWidth: 800});
global.getComputedStyle = () => ({paddingLeft: '10px', paddingRight: '10px', columnGap: '8px'});
assert.equal(budgetDomain.measureCollapsedProgressStrip(strip).widths[0], 330);
available = 1000;
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.style.gridTemplateColumns, '330px minmax(170px, 1fr) minmax(170px, 1fr)');
''')


def test_rendered_complete_labels_do_not_start_marquee_from_conservative_probe() -> None:
    run_behavior(r'''
const copy = width => ({
  clientWidth: width,
  scrollWidth: width,
  getBoundingClientRect: () => ({width}),
});
railWidths = [260, 280, 280];
items = railWidths.map(width => ({
  style: style(),
  querySelector: selector => selector.includes('size-probe')
    ? {scrollWidth: width - 2, getBoundingClientRect: () => ({width: width - 2})}
    : {children: [copy(width - 40), copy(30)]},
}));
available = 350;
const layout = budgetDomain.measureCollapsedProgressStrip(strip);
assert.equal(layout.required > layout.available, true);
assert.equal(layout.clipped, false);
assert.equal(layout.scrolling, false);
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow, undefined);
items[1].querySelector = selector => selector.includes('size-probe')
  ? {scrollWidth: 278, getBoundingClientRect: () => ({width: 278})}
  : {children: [{clientWidth: 80, scrollWidth: 120, getBoundingClientRect: () => ({width: 80})}, copy(30)]};
const clipped = budgetDomain.measureCollapsedProgressStrip(strip);
assert.equal(clipped.clipped, true);
assert.equal(clipped.scrolling, true);
''')


def test_title_exhaustion_keeps_the_native_right_controls_clear() -> None:
    source = ANCHORS[ANCHORS.index("      function topHeaderSlot"):ANCHORS.index("      function topAnchor")]
    script = r'''
const assert = require('node:assert/strict');
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const topTitlebarSlot = () => null;
const headerControlButtons = () => [];
const headerTitleTextEdge = () => 950;
const headerLeftControlEdge = () => 0;
const headerRightControlStart = () => 850;
''' + source + r'''
const slot = topHeaderSlot({}, {left: 20, right: 980, width: 960});
assert.equal(slot.right, 840);
assert.equal(slot.left, 840);
assert.equal(slot.width, 1);
'''
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"], input=script,
        text=True, encoding="utf-8", capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_collapse_refreshes_content_after_expanded_data_changes() -> None:
    source = OBSERVERS[OBSERVERS.index("      function applyPanelToggleGeometry"):OBSERVERS.index("      function syncPositionSettled")]
    script = r'''
const assert = require('node:assert/strict');
const runtimeIsCurrent = () => true;
const ctx = {lifecycle: {active: () => true}};
const rootId = 'hud', PANEL = {top: {}};
const panel = {dataset: {expanded: 'true'}, getBoundingClientRect: () => ({left:300,top:20,width:400})};
global.document = {getElementById: () => ({querySelector: () => panel})};
global.innerHeight = 800;
const getPanelState = () => ({});
const desiredHeight = (_name, _state, expanded) => expanded ? 390 : 36;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const applyRect = (_panel,left,top,width,height) => {panel.rect={left,top,width,height}};
const refreshAllMarquees = () => {};
let refreshes = 0;
const refreshTopContentFit = () => refreshes++;
''' + source + r'''
applyPanelToggleGeometry('top');
assert.equal(panel.rect.width, 400);
assert.equal(panel.rect.height, 390);
assert.equal(refreshes, 0);
panel.dataset.expanded = 'false';
applyPanelToggleGeometry('top');
assert.deepEqual(panel.rect,{left:300,top:20,width:400,height:36});
assert.equal(refreshes, 1);
'''
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"], input=script,
        text=True, encoding="utf-8", capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
