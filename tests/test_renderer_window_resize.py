"""Window restore must reflow HUD geometry without switching sessions."""

import subprocess

import pytest

from codex_usage_hud.renderer_assets.kernel import TEXT as KERNEL
from codex_usage_hud.renderer_assets.layout_anchors import TEXT as ANCHORS
from codex_usage_hud.renderer_assets.layout_observers import TEXT as OBSERVERS
from codex_usage_hud.renderer_assets.router import TEXT as ROUTER


@pytest.mark.parametrize("manual", [False, True], ids=["automatic", "manual"])
def test_window_restore_reflows_both_panels_and_stops_when_idle(manual: bool) -> None:
    harness = r"""
const assert = require('node:assert/strict');
class EventProbe {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) || []), fn]); }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) || []).filter(f => f !== fn)); }
  dispatch(type) { for (const fn of this.listeners.get(type) || []) fn(); }
}
global.window = new EventProbe();
let nextId = 0;
const frames = new Map(), timers = new Map();
global.requestAnimationFrame = fn => { const id = ++nextId; frames.set(id, fn); return id; };
global.cancelAnimationFrame = id => frames.delete(id);
global.setTimeout = fn => { const id = ++nextId; timers.set(id, fn); return id; };
global.clearTimeout = id => timers.delete(id);
function flushFrames() { const fns = [...frames.values()]; frames.clear(); fns.forEach(fn => fn()); }
function settle() { const fns = [...timers.values()]; timers.clear(); fns.forEach(fn => fn()); flushFrames(); }
global.innerWidth = 1200; global.innerHeight = 800;
const rootId = 'hud', scheduleName = 'schedule', resizeHandlerName = 'resize';
const scrollHandlerName = 'scroll', rafName = 'raf', settleTimerName = 'settle';
const PANEL = {
  top: {collapsedHeight: 36, minCollapsedWidth: 100},
  request: {collapsedHeight: 32, minCollapsedWidth: 100},
};
let topSlotCache, pendingSyncPanels = null, pendingForceAutoFit = false;
let cachedHeaderNode = {}, cachedComposerNode = {}, current = true;
const runtimeIsCurrent = () => current;
const layoutGeometryFrozen = () => false;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const px = v => `${v}px`;
const noop = () => {};
const resetNativeTitleReveal = noop, invalidateNativeTitleReveal = noop;
const positionStartupBubble = noop, positionRestReminderBubble = noop;
const applyPanelStates = noop, refreshAllMarquees = noop;
const budgetDomain = {measureCollapsedProgressStrip: () => ({clipped: false})};
const refreshLayoutObservers = noop, startBootstrapObserver = noop;
const desktopChromeMissing = () => false;
const panels = Object.fromEntries(Object.keys(PANEL).map(name => [name, {dataset: {panel: name}, style: {}, querySelector: () => null}]));
const root = {querySelector: s => panels[s.match(/data-panel="(.*?)"/)?.[1]]};
global.document = {getElementById: () => root, querySelector: () => null};
const states = {top: {}, request: {}};
const getPanelState = name => states[name];
const visible = () => true;
let chromeWidth = innerWidth, chromeHeight = innerHeight;
const header = {getBoundingClientRect: () => ({left: 200, top: 40, right: chromeWidth, width: chromeWidth - 200, height: 50})};
function conversationHeaderElement() { cachedHeaderNode = header; return header; }
function topHeaderSlot() { return {left: 400, right: chromeWidth - 100, width: chromeWidth - 500}; }
const composer = {getBoundingClientRect: () => ({left: 200, right: chromeWidth, top: chromeHeight - 150, bottom: chromeHeight - 20})};
function composerElement() { cachedComposerNode = composer; return composer; }
function footerGapSlot() { return {left: 350, right: chromeWidth - 150, rowTop: chromeHeight - 60, rowBottom: chromeHeight - 28, rowHeight: 32}; }
"""
    # Exercise the real geometry, frame coalescing, settle timers and listener.
    geometry = ANCHORS[ANCHORS.index("      function minWidthFor"):ANCHORS.index("      function candidateHeaders")]
    top_anchor = ANCHORS[
        ANCHORS.index("      function collapsedTopContentWidth"):
        ANCHORS.index("      function footerControlRects")
    ]
    request_anchor = ANCHORS[ANCHORS.index("      function requestAnchor"):]
    sync = OBSERVERS[:OBSERVERS.index("      function applyPanelToggleGeometry")]
    manual_alignment = OBSERVERS[
        OBSERVERS.index("      function alignFittingManualTopRight"):
        OBSERVERS.index("      let columnResizePointerId")
    ]
    scheduling = OBSERVERS[OBSERVERS.index("      function syncPositionSettled"):OBSERVERS.index("      function scheduleRequestAfterComposerSettles")]
    listeners = ROUTER[ROUTER.index("  window[scheduleName] ="):ROUTER.index("  modelPickerDomain.install();")]
    checks = r"""
syncPosition();
if (MANUAL) {
  states.top = {manual: true, width: 240, x: 420, y: 52};
  states.request = {manual: true, anchorSource: 'footer-gap', widthRatio: .5, xRatio: .5, bottomOffset: 0};
}
const originalStates = JSON.stringify(states);
const rect = name => Object.fromEntries(['left','top','width','height'].map(k => [k, parseFloat(panels[name].style[k])]));
const withinViewport = name => {
  const r = rect(name);
  assert.ok(r.left >= 0 && r.left + r.width <= innerWidth);
  assert.ok(r.top >= 0 && r.top + r.height <= innerHeight);
};
// Maximize, including several resize events before the next frame.
innerWidth = chromeWidth = 1800; innerHeight = chromeHeight = 1100;
for (let i = 0; i < 5; i++) window.dispatch('resize');
assert.equal(frames.size, 1); assert.equal(timers.size, 3);
flushFrames(); settle();
const maximized = {top: rect('top'), request: rect('request')};
assert.equal(maximized.top.left + maximized.top.width, chromeWidth - 100);
// Restore the viewport first; Desktop chrome reflows after the immediate frame.
innerWidth = 1000; innerHeight = 650;
window.dispatch('resize'); flushFrames();
chromeWidth = innerWidth; chromeHeight = innerHeight; settle();
withinViewport('top'); withinViewport('request');
assert.ok(rect('request').top < maximized.request.top);
if (!MANUAL) {
  assert.equal(rect('top').width, 500);
  assert.equal(rect('request').width, 500);
} else {
  assert.equal(rect('top').width, 240);
  assert.equal(rect('request').width, 250);
}
assert.equal(JSON.stringify(states), originalStates);
assert.equal(rect('top').left + rect('top').width, chromeWidth - 100);
// No perpetual polling, and scrolling/typing cannot move these panels.
window.dispatch('scroll'); window.dispatch('input');
assert.equal(frames.size, 0); assert.equal(timers.size, 0);
// Disposing a runtime removes listeners and cancels pending settle work.
window.dispatch('resize'); ctx.teardown.run();
assert.equal(frames.size, 0); assert.equal(timers.size, 0);
window.dispatch('resize');
assert.equal(frames.size, 0); assert.equal(timers.size, 0);
""".replace("MANUAL", str(manual).lower())
    script = harness + KERNEL + geometry + top_anchor + request_anchor + manual_alignment + sync + scheduling + listeners + checks
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=script, text=True, encoding="utf-8", capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_chat_column_width_changes_reflow_without_typing_or_polling() -> None:
    harness = r"""
const assert = require('node:assert/strict');
let resizeObserver;
const mutationCallbacks = [];
let now = 1000;
Date.now = () => now;
let previewDialogs = [];
class EventProbe {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) || []), fn]); }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== fn)); }
  dispatch(type, event) { for (const fn of this.listeners.get(type) || []) fn(event); }
}
global.window = new EventProbe();
global.document = new EventProbe();
global.document.documentElement = null;
global.document.querySelectorAll = selector => selector === 'img' ? [] : previewDialogs;
global.Node = {ELEMENT_NODE: 1};
global.Element = class ElementProbe {
  constructor(role = '', cursor = 'default', tagName = 'DIV', rect = {left: 0, top: 0, right: 10, bottom: 10, width: 10, height: 10}) {
    this.role = role; this.cursor = cursor; this.tagName = tagName; this.rect = rect; this.nodeType = 1; this.isConnected = true;
  }
  closest() { return null; }
  matches(selector) { return selector.includes('[role="dialog"]') && this.role === 'dialog'; }
  querySelectorAll() { return []; }
  getBoundingClientRect() { return this.rect; }
  getAttribute(name) {
    if (name === 'role') return this.role;
    if (name === 'aria-orientation') return this.role === 'separator' ? 'vertical' : null;
    return null;
  }
};
global.document.documentElement = new Element();
global.getComputedStyle = node => ({cursor: node.cursor, display: node.role === 'dialog' ? 'block' : '', visibility: 'visible', opacity: '1', position: 'fixed', zIndex: '100'});
global.ResizeObserver = class {
  constructor(callback) { this.callback = callback; resizeObserver = this; }
  observe() {}
  disconnect() {}
};
global.MutationObserver = class {
  constructor(callback) { mutationCallbacks.push(callback); }
  observe() {}
  disconnect() {}
};
const rootId = 'hud', mutationObserverName = 'mutation', resizeObserverName = 'resize';
const settleTimerName = 'settle', composerSettleTimerName = 'composer-settle';
global.innerWidth = 1280; global.innerHeight = 800;
const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
const frameCallbacks = new Map();
let nextFrameId = 0;
const PANEL = {top: {}, request: {}};
let pendingSyncPanels = null, pendingForceAutoFit = false;
const runtimeIsCurrent = () => true, ctx = { lifecycle: {
  active: () => true,
  listen: (_scope, target, type, handler, options) => target.addEventListener(type, handler, options),
  timeout: (_scope, callback) => { ctxTimers.set(++nextFrameId, callback); return nextFrameId; },
  clearTimeout: id => ctxTimers.delete(id),
  disposeScope() {},
}, observers: {
  set: (_name, observer) => observer, clear() {}
}, frames: {
  schedule: (name, callback) => { frameCallbacks.set(name, callback); return 1; },
  cancel: name => frameCallbacks.delete(name),
}};
const ctxTimers = new Map();
let observedHeaderNode = null, observedComposerNode = null;
const headerRect = {left: 200, right: 1200, top: 40, bottom: 90};
const composerRect = {left: 200, right: 1200, top: 700, bottom: 780};
const header = {getBoundingClientRect: () => headerRect};
const composer = {getBoundingClientRect: () => composerRect};
const visible = node => Boolean(node);
const conversationHeaderElement = () => header, composerElement = () => composer;
const invalidateHeaderAnchor = () => {}, invalidateComposerAnchor = () => {};
const handleLayoutMutations = () => {};
const scheduled = [];
const scheduleForPanels = (...args) => { if (!layoutGeometryFrozen()) scheduled.push(['schedule', ...args]); };
const syncPositionSettled = (...args) => { if (!layoutGeometryFrozen()) scheduled.push(['settle', ...args]); };
const flushPreviewFrame = () => { const callback = frameCallbacks.get('layout_preview'); frameCallbacks.delete('layout_preview'); callback?.(); };
const ensureComposerInputWatchers = () => {};
"""
    observers = OBSERVERS[
        OBSERVERS.index("      function horizontalLayoutSignature"):
        OBSERVERS.index("      function stopBootstrapObserver")
    ]
    checks = r"""
refreshLayoutObservers();
assert.equal(resizeObserver !== undefined, true);
composerRect.right = 800;
resizeObserver.callback();
assert.equal(scheduled.length, 2);
const afterWidthChange = scheduled.length;
composerRect.bottom = 760;
resizeObserver.callback();
assert.equal(scheduled.length, afterWidthChange);

// The divider can translate a fixed-width composer without changing its size,
// so ResizeObserver has no reason to fire. Pointer tracking still reflows it.
scheduled.length = 0;
const divider = new Element('separator');
document.dispatch('pointerdown', {
  button: 0, pointerId: 17, target: divider, composedPath: () => [divider],
});
assert.equal(scheduled.length, 1);
composerRect.left = 250; composerRect.right = 850;
headerRect.left = 250; headerRect.right = 850;
document.dispatch('pointermove', {pointerId: 17, target: divider, composedPath: () => [divider]});
assert.equal(scheduled.length, 2);
document.dispatch('pointerup', {pointerId: 17});
assert.equal(scheduled.length, 4);
document.dispatch('pointermove', {pointerId: 17, target: divider, composedPath: () => [divider]});
assert.equal(scheduled.length, 4);

// Ignore ordinary pointer input, while also accepting Desktop splitters whose
// only exposed resize signal is their computed cursor style.
const content = new Element('', 'default');
document.dispatch('pointerdown', {
  button: 0, pointerId: 18, target: content, composedPath: () => [content],
});
assert.equal(scheduled.length, 4);
const cursorDivider = new Element('', 'col-resize');
document.dispatch('pointerdown', {
  button: 0, pointerId: 19, target: cursorDivider, composedPath: () => [cursorDivider],
});
assert.equal(scheduled.length, 5);
document.dispatch('pointercancel', {pointerId: 19});
assert.equal(scheduled.length, 7);

// Clicking a large Codex image freezes geometry immediately, before the
// preview layer has finished mounting. The modal keeps it frozen, and closing
// the modal does not trigger a compensating layout pass.
scheduled.length = 0;
const image = new Element('', 'default', 'IMG', {left: 400, top: 200, right: 800, bottom: 500, width: 400, height: 300});
document.dispatch('pointerdown', {button: 0, pointerId: 20, target: image, composedPath: () => [image]});
assert.equal(layoutGeometryFrozen(), true);
scheduleForPanels(['request']); syncPositionSettled();
assert.equal(scheduled.length, 0);
const largePreviewImage = new Element('', 'default', 'IMG', {left: 200, top: 120, right: 1000, bottom: 720, width: 800, height: 600});
const dialog = new Element('dialog', 'default', 'DIV', {left: 0, top: 0, right: 1280, bottom: 800, width: 1280, height: 800});
dialog.querySelectorAll = () => [largePreviewImage];
previewDialogs = [dialog];
mutationCallbacks[0]([{target: document.documentElement, addedNodes: [dialog], removedNodes: []}]);
flushPreviewFrame();
scheduleForPanels(['top']);
assert.equal(scheduled.length, 0);
dialog.isConnected = false; previewDialogs = [];
mutationCallbacks[0]([{target: document.documentElement, addedNodes: [], removedNodes: [dialog]}]);
flushPreviewFrame();
assert.equal(layoutGeometryFrozen(), true);
scheduleForPanels(['request']);
assert.equal(scheduled.length, 0);
now += 701;
scheduleForPanels(['request']);
assert.equal(scheduled.length, 1);
"""
    script = harness + observers + checks
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=script, text=True, encoding="utf-8", capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_conversation_header_prefers_main_slot_and_keeps_legacy_fallback() -> None:
    harness = r"""
const assert = require('node:assert/strict');
let cachedHeaderNode = null;
const main = {isConnected: true}, shell = {isConnected: true};
let mainSlot = main, surface = {
  querySelector: () => mainSlot,
  closest: () => shell,
};
const fallback = {isConnected: true};
const anchorUsable = node => Boolean(node?.isConnected);
const candidateHeaders = () => [fallback];
const scoreHeader = () => 1;
global.document = {querySelector: () => surface};
"""
    selection = ANCHORS[
        ANCHORS.index("      function conversationHeaderElement"):
        ANCHORS.index("      function conversationHeaderRect")
    ]
    checks = r"""
assert.equal(conversationHeaderElement(), main);
// A pane transition can replace the main slot without replacing the shell.
main.isConnected = false;
mainSlot = {isConnected: true};
assert.equal(conversationHeaderElement(), mainSlot);
// Older Desktop builds still use the surrounding native header.
cachedHeaderNode = null; mainSlot = null;
assert.equal(conversationHeaderElement(), shell);
cachedHeaderNode = null; surface = null;
assert.equal(conversationHeaderElement(), fallback);
"""
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=harness + selection + checks, text=True, encoding="utf-8",
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_three_column_top_anchor_keeps_the_native_menu_slot() -> None:
    harness = r"""
const assert = require('node:assert/strict');
global.innerWidth = 1280; global.innerHeight = 820;
const PANEL = {top: {collapsedHeight: 36, minCollapsedWidth: 120, fallbackWidth: 520}};
const minWidthFor = () => 120;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const visible = node => Boolean(node);
const headerRect = {left: 291, right: 679, top: 44, bottom: 96, width: 388, height: 52};
const composerRect = {left: 307, right: 662, top: 700, bottom: 800, width: 355, height: 100};
const header = {getBoundingClientRect: () => headerRect};
const composer = {getBoundingClientRect: () => composerRect};
const conversationHeaderElement = () => header;
const composerElement = () => composer;
let naturalSlot = {left: 620, right: 654, width: 34};
const topHeaderSlot = () => naturalSlot;
"""
    top_anchor = ANCHORS[
        ANCHORS.index("      function topAnchor"):
        ANCHORS.index("      function footerControlRects")
    ]
    checks = r"""
const anchor = topAnchor(36, null);
assert.equal(anchor.source, 'header-slot');
assert.equal(anchor.left, 620);
assert.equal(anchor.width, 34);
assert.equal(anchor.area.right, 654);
assert.equal(anchor.overlayTitle, false);

// Wider three-column layouts still use the titlebar menu, independently of
// the composer width and its centered margins.
composerRect.right = 940; composerRect.width = 633;
headerRect.right = 957; headerRect.width = 666;
naturalSlot = {left: 450, right: 930, width: 480};
const wideThreeColumn = topAnchor(36, null);
assert.equal(wideThreeColumn.left, 450);
assert.equal(wideThreeColumn.width, 480);
assert.equal(wideThreeColumn.area.right, 930);

// Divider drag updates both edges instead of reusing the previous width.
composerRect.right = 902; composerRect.width = 595;
headerRect.right = 919; headerRect.width = 628;
naturalSlot = {left: 450, right: 900, width: 450};
const resizedThreeColumn = topAnchor(36, null);
assert.equal(resizedThreeColumn.left, 450);
assert.equal(resizedThreeColumn.width, 450);
assert.equal(resizedThreeColumn.area.right, 900);

// Returning to a two-column viewport restores the normal title slot.
composerRect.right = 1260; composerRect.width = 953;
headerRect.right = 1272; headerRect.width = 981;
naturalSlot = {left: 450, right: 1100, width: 650};
const twoColumn = topAnchor(36, null);
assert.equal(twoColumn.left, 450);
assert.equal(twoColumn.width, 650);

// A centered composer in a wide two-column window is not a right side pane.
composerRect.left = 420; composerRect.right = 1140; composerRect.width = 720;
headerRect.left = 291; headerRect.right = 1272; headerRect.width = 981;
naturalSlot = {left: 450, right: 1200, width: 750};
const centeredComposer = topAnchor(36, null);
assert.equal(centeredComposer.area.right, 1200);
assert.equal(centeredComposer.width, 750);
assert.equal(centeredComposer.overlayTitle, false);
"""
    script = harness + top_anchor + checks
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=script, text=True, encoding="utf-8", capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("viewport_width", [900, 1366, 1920])
@pytest.mark.parametrize("sidebar_width", [0, 251])
@pytest.mark.parametrize("right_pane_width", [0, 300], ids=["two-column", "three-column"])
def test_summary_and_side_pane_keep_top_hud_docked_to_main_header_menu(
    viewport_width: int,
    sidebar_width: int,
    right_pane_width: int,
) -> None:
    harness = r"""
const assert = require('node:assert/strict');
global.innerWidth = VIEWPORT_WIDTH; global.innerHeight = 820;
const rootId = 'hud';
const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const visible = node => Boolean(node);
const PANEL = {top: {collapsedHeight: 36, fallbackWidth: 520}};
const minWidthFor = (_name, expanded) => expanded ? 220 : 120;
let topSlotCache = null;
let cachedHeaderNode = null;
const headerRect = {
  left: SIDEBAR_WIDTH, right: innerWidth - 8 - RIGHT_PANE_WIDTH, top: 36, bottom: 88,
  width: innerWidth - 8 - SIDEBAR_WIDTH - RIGHT_PANE_WIDTH, height: 52,
};
const composerRect = {left: headerRect.left + 36, right: headerRect.right - 36};
const composerElement = () => ({getBoundingClientRect: () => composerRect});
const title = {
  getBoundingClientRect: () => ({
    left: headerRect.left + 52, right: headerRect.left + 312,
    top: 50, bottom: 72, width: 260, height: 22,
  }),
  closest: () => null, textContent: 'Conversation title',
};
const button = (label, rightOffset) => ({
  getBoundingClientRect: () => ({
    left: headerRect.right - rightOffset, right: headerRect.right - rightOffset + 28,
    top: 46, bottom: 74, width: 28, height: 28,
  }),
  getAttribute: name => name === 'aria-label' ? label : null,
  closest: () => null, querySelectorAll: () => [], textContent: '',
});
const moreButton = button('Chat actions', 96);
const summaryButton = button('Toggle pinned summary', 62);
const sidePanelButton = button('Open side panel', 28);
const header = {
  isConnected: true,
  getBoundingClientRect: () => headerRect,
  querySelectorAll: selector => selector.startsWith('button')
    ? [moreButton, summaryButton, sidePanelButton] : [title],
};
// Desktop's shell spans every pane. Only its main slot owns this menu.
const shell = {
  getBoundingClientRect: () => ({...headerRect, right: innerWidth - 8}),
};
const surface = {
  querySelector: selector => selector === '[data-app-shell-titlebar-slot="main"]' ? header : null,
  closest: () => shell,
};
global.document = {querySelector: () => surface};
const anchorUsable = node => Boolean(node?.isConnected);
const collapsed = {}, strip = {};
let requiredWidth = 514;
const budgetDomain = {measureCollapsedProgressStrip: () => ({required: requiredWidth})};
global.getComputedStyle = () => ({paddingLeft: '8px', paddingRight: '7px', columnGap: '6px'});
const panel = {
  dataset: {expanded: 'false'},
  querySelector: selector => {
    if (selector === '.codex-usage-hud-collapsed') return collapsed;
    if (selector === '[data-field="topCollapsedProgress"]') return strip;
    if (selector === '.codex-usage-hud-settings-button') {
      return {getBoundingClientRect: () => ({width: 22})};
    }
    return null;
  },
};
""".replace("VIEWPORT_WIDTH", str(viewport_width)).replace("SIDEBAR_WIDTH", str(sidebar_width)).replace("RIGHT_PANE_WIDTH", str(right_pane_width))
    geometry = ANCHORS[
        ANCHORS.index("      function conversationHeaderElement"):
        ANCHORS.index("      function conversationHeaderRect")
    ] + ANCHORS[
        ANCHORS.index("      function headerLeftControlEdge"):
        ANCHORS.index("      function footerControlRects")
    ]
    checks = r"""
const menuRight = moreButton.getBoundingClientRect().left - 10;
assert.equal(conversationHeaderElement(), header);
const initialSlot = topTitlebarSlot(header, headerRect);
// Opening, resizing, and closing the summary changes only the composer.
for (const summaryWidth of [0, 300, 340, 220, 0, 300]) {
  composerRect.right = headerRect.right - 36 - summaryWidth;
  const slot = topTitlebarSlot(header, headerRect);
  assert.equal(slot.left, initialSlot.left);
  assert.equal(slot.right, menuRight);
  for (const required of [120, 514, 1400]) {
    requiredWidth = required;
    const anchor = topAnchor(36);
    assert.equal(anchor.area.right, menuRight);
    assert.equal(anchor.overlayTitle, false);
    const fitted = fitTopContentRect(panel, anchor);
    assert.equal(fitted.left + fitted.width, menuRight);
    assert.equal(fitted.width, Math.min(required + 51, slot.width));
    assert.ok(fitted.left >= slot.left);
  }
  // Expansion and saved manual widths use the same native slot boundary.
  assert.equal(topAnchor(390).area.right, menuRight);
  const manual = topAnchor(36, 260);
  assert.equal(manual.area.right, menuRight);
  assert.equal(manual.width, Math.min(260, slot.width));
}
"""
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=harness + geometry + checks, text=True, encoding="utf-8",
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_two_column_title_slot_uses_native_controls_not_composer_right_edge() -> None:
    harness = r"""
const assert = require('node:assert/strict');
global.innerWidth = 1008;
const rootId = 'hud';
const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const visible = node => Boolean(node);
let cachedComposerNode = null;
let topSlotCache = null;
const composerRect = {left: 300, right: 824, width: 524, top: 700, bottom: 780, height: 80};
const composerElement = () => ({getBoundingClientRect: () => composerRect});
const titleRect = {left: 360, right: 620, top: 50, bottom: 72, width: 260, height: 22};
const title = {
  getBoundingClientRect: () => titleRect,
  closest: () => null,
  textContent: 'A long native conversation title with more text',
};
const settingsButton = {
  getBoundingClientRect: () => ({left: 798, right: 820, top: 50, bottom: 72, width: 22, height: 22}),
  getAttribute: name => name === 'aria-label' ? 'Settings' : null,
  closest: () => null,
  querySelectorAll: () => [],
  textContent: '',
};
const moreButton = {
  getBoundingClientRect: () => ({left: 948, right: 970, top: 50, bottom: 72, width: 22, height: 22}),
  getAttribute: name => name === 'aria-label' ? 'More actions' : null,
  closest: () => null,
  querySelectorAll: () => [],
  textContent: '',
};
const sidePanelButton = {
  getBoundingClientRect: () => ({left: 990, right: 1012, top: 50, bottom: 72, width: 22, height: 22}),
  getAttribute: name => name === 'aria-label' ? 'Open side panel' : null,
  closest: () => null,
  querySelectorAll: () => [],
  textContent: '',
};
const header = {
  querySelectorAll: selector => selector.startsWith('button') ? [settingsButton, moreButton, sidePanelButton] : [title],
};
"""
    slot_logic = ANCHORS[
        ANCHORS.index("      function headerLeftControlEdge"):
        ANCHORS.index("      function topHeaderSlot")
    ]
    checks = r"""
const headerRect = {left: 292, right: 1020, top: 44, bottom: 96, width: 728, height: 52};
const slot = topTitlebarSlot(header, headerRect);
assert.ok(slot);
assert.ok(slot.left > titleRect.left, JSON.stringify({slot, titleRect}));
assert.ok(slot.left < titleRect.right, JSON.stringify({slot, titleRect}));
assert.ok(slot.left - titleRect.left >= 48, JSON.stringify({slot, titleRect}));
assert.ok(slot.left < slot.right && slot.right > titleRect.left, JSON.stringify({slot, titleRect}));
assert.ok(slot.right > composerRect.right + 100, JSON.stringify({slot, composerRect}));
assert.equal(slot.right, moreButton.getBoundingClientRect().left - 10);
assert.ok(slot.right > settingsButton.getBoundingClientRect().right);
assert.ok(slot.right <= headerRect.right - 8);

// Desktop also calls this right-side menu 'Chat actions'. It must never be
// mistaken for a left-side control that would consume the entire HUD slot.
moreButton.getAttribute = name => name === 'aria-label' ? 'Chat actions' : null;
topSlotCache = null;
const chatActionsSlot = topTitlebarSlot(header, headerRect);
assert.ok(chatActionsSlot && chatActionsSlot.width > 400);
assert.equal(chatActionsSlot.right, 938);

// An unlabeled three-dot SVG is accepted, while a settings control with an
// unrelated 'options' label must not become the docking target.
moreButton.getAttribute = () => null;
moreButton.querySelectorAll = () => [{}, {}, {}];
settingsButton.getAttribute = name => name === 'aria-label' ? 'Settings options' : null;
topSlotCache = null;
assert.equal(topTitlebarSlot(header, headerRect).right, 938);

// Docking is independent of the header's percentage-based margin.
headerRect.right = 980; headerRect.width = 688;
topSlotCache = null;
assert.equal(topTitlebarSlot(header, headerRect).right, 938);
"""
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=harness + slot_logic + checks,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_manual_top_hud_right_aligns_only_when_collapsed_content_fits() -> None:
    harness = r"""
const assert = require('node:assert/strict');
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
let measurement = {clipped: false};
const budgetDomain = {measureCollapsedProgressStrip: () => measurement};
const panel = {querySelector: () => ({})};
const anchor = {area: {left: 200, right: 850, width: 650}};
"""
    helper = OBSERVERS[
        OBSERVERS.index("      function alignFittingManualTopRight"):
        OBSERVERS.index("      let columnResizePointerId")
    ]
    checks = r"""
assert.equal(alignFittingManualTopRight(panel, anchor, 200, 400, false), 450);
measurement = {clipped: true};
assert.equal(alignFittingManualTopRight(panel, anchor, 200, 400, false), 200);
measurement = {clipped: false};
assert.equal(alignFittingManualTopRight(panel, anchor, 230, 400, false), 450);
assert.equal(alignFittingManualTopRight(panel, anchor, 200, 620, false), 230);
assert.equal(alignFittingManualTopRight(panel, anchor, 200, 400, true), 200);
anchor.overlayTitle = true;
assert.equal(alignFittingManualTopRight(panel, anchor, 200, 400, false), 200);
"""
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=harness + helper + checks, text=True, encoding="utf-8", capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
