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
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const px = v => `${v}px`;
const noop = () => {};
const resetNativeTitleReveal = noop, invalidateNativeTitleReveal = noop;
const positionStartupBubble = noop, positionRestReminderBubble = noop;
const applyPanelStates = noop, refreshAllMarquees = noop;
const refreshLayoutObservers = noop, startBootstrapObserver = noop;
const desktopChromeMissing = () => false;
const panels = Object.fromEntries(Object.keys(PANEL).map(name => [name, {dataset: {panel: name}, style: {}}]));
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
    top_anchor = ANCHORS[ANCHORS.index("      function topAnchor"):ANCHORS.index("      function footerControlRects")]
    request_anchor = ANCHORS[ANCHORS.index("      function requestAnchor"):]
    sync = OBSERVERS[:OBSERVERS.index("      function applyPanelToggleGeometry")]
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
// No perpetual polling, and scrolling/typing cannot move these panels.
window.dispatch('scroll'); window.dispatch('input');
assert.equal(frames.size, 0); assert.equal(timers.size, 0);
// Disposing a runtime removes listeners and cancels pending settle work.
window.dispatch('resize'); ctx.teardown.run();
assert.equal(frames.size, 0); assert.equal(timers.size, 0);
window.dispatch('resize');
assert.equal(frames.size, 0); assert.equal(timers.size, 0);
""".replace("MANUAL", str(manual).lower())
    script = harness + KERNEL + geometry + top_anchor + request_anchor + sync + scheduling + listeners + checks
    result = subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=script, text=True, encoding="utf-8", capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
