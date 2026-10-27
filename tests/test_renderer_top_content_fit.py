"""Top HUD geometry fits collapsed content and keeps the right edge anchored."""

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
    removals: 0,
    removeProperty(name) { this.removals++; delete this[name.replace(/-([a-z])/g, (_, c) => c.toUpperCase())]; },
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
const collapsed = {};
const settings = {getBoundingClientRect: () => ({width: 22})};
const leftControls = {getBoundingClientRect: () => ({width: 0})};
const main = {querySelector: () => null};
global.getComputedStyle = node => node === collapsed
  ? {paddingLeft: '8px', paddingRight: '7px', columnGap: '6px'}
  : {columnGap: '7px'};
global.innerWidth = 1000;
global.innerHeight = 600;
const PANEL = {top: {collapsedHeight: 36}};
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const minWidthFor = (_name, expanded) => expanded ? 220 : 120;
let header = {left: 20, top: 8, width: 960, height: 50};
let slot = {left: 500, right: 951, width: 451};
const conversationHeaderElement = () => ({getBoundingClientRect: () => header});
const topHeaderSlot = () => slot;
const compactChatTitlebarSlot = (_header, slot) => slot;
const layoutGeometryFrozen = () => false;
const alignFittingManualTopRight = (_panel, _anchor, left) => left;
const visible = () => true;
let manual = false, scheduled = 0;
const getPanelState = () => ({manual, width: 130});
const scheduleForPanels = names => {assert.deepEqual(names, ['top']); scheduled++};
const panel = {
  dataset: {expanded: 'false'},
  getBoundingClientRect: () => panel.rect || ({left: slot.left, top: 15, right: slot.right, width: slot.width, height: 36}),
  querySelector: selector => {
    if (selector === '[data-field="topCollapsedProgress"]') return strip;
    if (selector === '.codex-usage-hud-collapsed') return collapsed;
    if (selector === '.codex-usage-hud-settings-button') return settings;
    if (selector === '.codex-usage-hud-left-controls') return leftControls;
    if (selector === '.codex-usage-hud-main') return main;
    return null;
  },
};
strip.closest = () => panel;
const rootId = 'hud';
global.document = {querySelector: () => panel};
'''


def run_behavior(checks: str, *, sync: bool = False) -> None:
    budget = BUDGET.split("  const budgetDomain = ctx.domains.register(", 1)[0]
    helpers = ANCHORS[ANCHORS.index("      function collapsedTopContentWidth"):ANCHORS.index("      function topAnchor")]
    anchors = helpers + ANCHORS[ANCHORS.index("      function topAnchor"):ANCHORS.index("      function footerControlRects")]
    refresh = OBSERVERS[OBSERVERS.index("      function refreshTopContentFit"):OBSERVERS.index("      function applyPanelToggleGeometry")]
    controller = OBSERVERS[:OBSERVERS.index("      function applyPanelToggleGeometry")] if sync else refresh
    script = HARNESS + budget + "const budgetDomain = createBudgetDomain({}, {});\n" + anchors + controller + checks
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=script, text=True, encoding="utf-8", capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("available", "scrolling"),
    [(600, False), (514, False), (513, False),
     (400, False), (255, False), (254, True), (100, True)],
    ids=["short-title", "exact-fit", "one-pixel-tolerance", "clipped-middle",
         "above-marquee-boundary", "at-marquee-boundary", "narrow-marquee"],
)
def test_top_content_overflow_fits_to_the_right_edge(available: int, scrolling: bool) -> None:
    run_behavior(f'''
available = {available};
slot = {{left: 951 - available - 51, right: 951, width: available + 51}};
const anchor = topAnchor(36);
const layout = budgetDomain.measureCollapsedProgressStrip(strip);
assert.equal(layout.scrolling, {str(scrolling).lower()});
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow === 'true', {str(scrolling).lower()});
assert.equal(anchor.width, slot.width);
''')


def test_top_content_refresh_updates_scroll_without_scheduling_geometry() -> None:
    run_behavior(r'''
refreshTopContentFit(); assert.equal(scheduled, 0);
assert.equal(panel.dataset.topContentScroll, 'true');
manual = true; refreshTopContentFit(); assert.equal(scheduled, 0);
assert.equal(strip.dataset.overflow, 'true');
manual = false; panel.dataset.expanded = 'true';
refreshTopContentFit(); assert.equal(scheduled, 0);
panel.dataset.expanded = 'false'; available = 600;
refreshTopContentFit(); assert.equal(scheduled, 0);
assert.equal(panel.dataset.topContentScroll, undefined);
available = 200;
refreshTopContentFit(); assert.equal(scheduled, 0);
assert.equal(panel.dataset.topContentScroll, 'true');
strip.isConnected = false;
assert.equal(topAnchor(36).width, slot.width);
''')


def test_top_content_overflow_handles_long_and_single_metric_strips() -> None:
    run_behavior(r'''
railWidths = [160, 500, 500]; items = rails(); available = 400;
const anchor = topAnchor(36);
assert.equal(anchor.width, slot.width);
railWidths = [500]; items = rails(); available = 400;
assert.equal(budgetDomain.measureCollapsedProgressStrip(strip).scrolling, false);
assert.equal(topAnchor(36).width, slot.width);
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow, undefined);
assert.equal(items[0].style.width, undefined);
strip.querySelectorAll = () => [];
assert.equal(topAnchor(36).width, slot.width);
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
assert.equal(panel.rect.width, 451);
assert.equal(panel.rect.left, 500);
assert.equal(panel.dataset.topContentScroll, 'true');
// Repeated content measurements keep the fitted right edge stable.
syncPosition(['top']);
assert.equal(panel.rect.width, 451);
assert.equal(panel.rect.left, 500);
railWidths = [120]; items = rails(); syncPosition(['top']);
assert.deepEqual(panel.rect, {left: 780, top: 15, width: 171, height: 36});
railWidths = [280, 420, 380]; items = rails(); syncPosition(['top']);
assert.deepEqual(panel.rect, {left: 500, top: 15, width: 451, height: 36});
railWidths = [160, 170, 170]; items = rails(); syncPosition(['top']);
assert.deepEqual(panel.rect, {left: 500, top: 15, width: 451, height: 36});
// A wider native slot returns to normal automatic placement.
slot = {left: 300, right: 951, width: 651};
syncPosition(['top']);
assert.equal(panel.rect.width, 565);
assert.equal(panel.rect.left, 386);
// Narrow native slots still enter the original marquee path.
slot = {left: 720, right: 951, width: 231};
syncPosition(['top']);
assert.equal(panel.rect.width, 231);
assert.equal(panel.rect.left, 720);
assert.equal(strip.dataset.overflow, 'true');
assert.deepEqual(state, {manual: false, width: 130});
// No native title slot width still leaves panel geometry stable and bounded.
slot = {left: 950, right: 951, width: 1};
syncPosition(['top']);
assert.equal(panel.rect.width, 1);
// Very long totals scroll within the title slot.
railWidths = [160, 500, 500]; items = rails();
slot = {left: 500, right: 951, width: 451};
syncPosition(['top']);
assert.equal(panel.rect.width, 451);
assert.equal(panel.dataset.topContentScroll, 'true');
assert.equal(strip.dataset.overflow, 'true');
assert.equal(topAnchor(390).width, 451);
slot = {left: 950, right: 951, width: 1};
assert.equal(topAnchor(390).width, 1);
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
      const requiredPanelWidth = Math.ceil(layout.required + 51);
      const expectedWidth = Math.min(width, requiredPanelWidth);
      assert.ok(Math.abs(panel.rect.width - expectedWidth) <= 1, JSON.stringify({width, railWidths, layout, fit: collapsedTopContentWidth(panel, layout), expectedWidth, rect: panel.rect}));
      assert.equal(panel.rect.left + panel.rect.width, 951, JSON.stringify({width, railWidths, layout, expectedWidth, rect: panel.rect}));
      assert.ok(panel.rect.left >= 8 && panel.rect.left + panel.rect.width <= 951);
      assert.equal(panel.dataset.topContentScroll, layout.clipped ? 'true' : undefined);
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
assert.equal(strip.style.gridTemplateColumns, '330px minmax(0, 1fr) minmax(0, 1fr)');
''')


def test_session_rail_stays_intrinsic_and_tail_rails_split_space_evenly() -> None:
    run_behavior(r'''
railWidths = [180, 240, 320]; items = rails(); available = 760;
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(items[0].style.width, '180px');
assert.equal(items[1].style.width, undefined);
assert.equal(items[2].style.width, undefined);
assert.equal(strip.style.gridTemplateColumns, '180px minmax(0, 1fr) minmax(0, 1fr)');
assert.equal(budgetDomain.measureCollapsedProgressStrip(strip).scrolling, false);
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


def test_overflow_refresh_preserves_marquee_when_live_label_widths_change() -> None:
    run_behavior(r'''
available = 220; railWidths = [160, 220, 210]; items = rails();
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow, 'true');
const removals = strip.style.removals;
const oldDistance = strip.style['--codex-usage-hud-progress-strip-distance'];
railWidths = [160, 235, 215]; items = rails();
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow, 'true');
assert.ok(strip.style.removals === removals);
assert.notEqual(strip.style['--codex-usage-hud-progress-strip-distance'], oldDistance);
assert.equal(strip.dataset.layoutSignature, '160,235,215|404');
''')


def test_overflow_mode_does_not_reflow_tail_rails_near_fit_boundary() -> None:
    run_behavior(r'''
panel.dataset.topContentScroll = 'true';
available = 500; railWidths = [160, 200, 200]; items = rails();
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow, 'true');
const removals = strip.style.removals;
// A new Today/Week label fits by only six pixels. Keep intrinsic rail widths
// and marquee mode instead of switching both tail columns to 1fr.
railWidths = [160, 160, 160]; items = rails();
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow, 'true');
assert.equal(strip.style.removals, removals);
assert.equal(items[1].style.width, '160px');
assert.equal(items[2].style.width, '160px');
assert.equal(strip.style['--codex-usage-hud-progress-strip-distance'], '0px');
// Once there is stable room, return to the normal split layout.
available = 540;
railWidths = [160, 145, 145]; items = rails();
budgetDomain.refreshCollapsedProgressStrip(strip);
assert.equal(strip.dataset.overflow, undefined);
assert.ok(strip.style.removals > removals);
assert.equal(items[1].style.width, undefined);
assert.equal(items[2].style.width, undefined);
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
const headerOverflowMenuControlStart = () => 850;
const headerTitleTextBounds = () => null;
const topTitlebarLeft = () => 970;
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
const layoutGeometryFrozen = () => false;
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
