"""Focused behavior checks for the native-title hover interaction."""

from __future__ import annotations

import subprocess

import pytest

from codex_usage_hud.renderer_assets.kernel import TEXT as KERNEL
from codex_usage_hud.renderer_assets.layout_gestures import TEXT as GESTURES


HARNESS = r"""
const assert = require('node:assert/strict');
class EventProbe {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), callback]);
  }
  removeEventListener(type, callback) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(x => x !== callback));
  }
  dispatch(type, event = {}) { for (const fn of this.listeners.get(type) || []) fn(event); }
}
global.window = new EventProbe();
global.document = new EventProbe();
let now = 0, nextId = 1;
const timers = new Map(), frames = new Map();
global.setTimeout = (fn, delay) => {
  const id = nextId++; timers.set(id, {fn, at: now + delay}); return id;
};
global.clearTimeout = id => timers.delete(id);
global.requestAnimationFrame = fn => { const id = nextId++; frames.set(id, fn); return id; };
global.cancelAnimationFrame = id => frames.delete(id);
function advance(ms) {
  now += ms;
  for (const [id, task] of [...timers]) {
    if (task.at <= now) { timers.delete(id); task.fn(); }
  }
}
function flushFrames() {
  const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn());
}
global.ResizeObserver = class {
  constructor(fn) { this.callback = fn; this.nodes = []; }
  observe(node) { this.nodes.push(node); }
  disconnect() { this.nodes = []; }
};
const rect = (left, top, right, bottom) => ({left, top, right, bottom, width: right-left, height: bottom-top});
const rootId = 'hud', stateName = 'state';
global.innerWidth = 800; global.innerHeight = 600;
const normalize = text => String(text || '').trim();
const runtimeIsCurrent = () => true;
const visible = node => !!node && node.isConnected && node.shown;
const parent = {parentElement: null, getBoundingClientRect: () => rect(20, 10, 450, 35)};
let textRects = [rect(25, 14, 700, 31)];
const title = {
  isConnected: true, shown: true, textContent: 'A native title', parentElement: parent,
  getBoundingClientRect: () => rect(20, 10, 450, 35),
  closest: () => null, contains: () => false,
};
const textNode = {nodeValue: title.textContent, parentElement: title};
const panel = {
  isConnected: true, shown: true, dataset: {panel: 'top'},
  getBoundingClientRect: () => rect(120, 8, 430, 42),
  removeAttribute: name => { if (name === 'data-native-title-reveal') delete panel.dataset.nativeTitleReveal; },
};
const header = {isConnected: true, shown: true, querySelectorAll: () => [title]};
const conversationHeaderElement = () => header;
global.getComputedStyle = () => ({display: 'block', visibility: 'visible', opacity: '1', overflowX: 'hidden', overflowY: 'hidden'});
global.NodeFilter = {SHOW_TEXT: 4};
document.querySelector = () => panel;
document.createTreeWalker = () => {
  let read = false; return {nextNode() { if (read) return null; read = true; return textNode; }};
};
document.createRange = () => ({selectNodeContents() {}, getClientRects: () => textRects});
let pointerX = 0, modal = false;
document.elementFromPoint = () => ({closest: () => (
  modal || (panel.dataset.nativeTitleReveal !== 'true' && pointerX >= 120 && pointerX < 430)
) ? {} : null});
function move(x, y = 20, extra = {}) {
  pointerX = x;
  document.dispatch('pointermove', {clientX: x, clientY: y, pointerType: 'mouse', buttons: 0, ...extra});
}
const revealed = () => panel.dataset.nativeTitleReveal === 'true';
"""


@pytest.mark.parametrize(
    "scenario",
    [
        # A wide flex container is not evidence that a short title is covered.
        """
        textRects = [rect(25, 14, 90, 31)];
        move(40); advance(200);
        assert.equal(revealed(), false); assert.equal(timers.size, 0);
        """,
        # Native clipping, rather than the intrinsic text width, is authoritative.
        """
        title.getBoundingClientRect = () => rect(20, 10, 110, 35);
        move(40); advance(200);
        assert.equal(revealed(), false);
        """,
        """
        move(40); advance(149); assert.equal(revealed(), false);
        move(45); advance(1); assert.equal(revealed(), true);
        move(250); advance(500); assert.equal(revealed(), true);
        move(600); advance(100); move(250); advance(100);
        assert.equal(revealed(), true);
        move(600); advance(150); assert.equal(revealed(), false);
        move(250); advance(200); assert.equal(revealed(), false);
        """,
        """
        move(40); advance(100); move(600); advance(100);
        assert.equal(revealed(), false); assert.equal(timers.size, 0);
        move(40, 20, {buttons: 1}); advance(200); assert.equal(revealed(), false);
        move(40, 20, {pointerType: 'touch'}); advance(200); assert.equal(revealed(), false);
        move(40); title.isConnected = false; advance(150); assert.equal(revealed(), false);
        """,
        """
        move(40); advance(150); assert.equal(revealed(), true);
        modal = true; move(40); advance(150); assert.equal(revealed(), false);
        modal = false; move(40); advance(150);
        window.dispatch('blur'); assert.equal(revealed(), false);
        move(40); advance(150); document.dispatch('pointerout', {relatedTarget: null});
        assert.equal(revealed(), false);
        move(40); advance(150); document.dispatch('pointerdown');
        assert.equal(revealed(), false);
        move(40); advance(150); document.hidden = true; document.dispatch('visibilitychange');
        assert.equal(revealed(), false);
        """,
        """
        move(40); advance(150); assert.equal(revealed(), true);
        textRects = [rect(25, 14, 90, 31)]; invalidateNativeTitleReveal(); flushFrames();
        advance(200); assert.equal(revealed(), false);
        textRects = [rect(25, 14, 700, 31)]; invalidateNativeTitleReveal(); flushFrames();
        advance(150); assert.equal(revealed(), true);
        resetNativeTitleReveal(); advance(200); assert.equal(revealed(), false);
        move(40); assert.equal(timers.size, 1);
        ctx.teardown.run(); advance(200); assert.equal(revealed(), false);
        assert.equal(timers.size, 0); assert.equal(frames.size, 0);
        assert.equal(document.listeners.get('pointermove').length, 0);
        assert.equal(nativeTitleRevealObserver.nodes.length, 0);
        """,
    ],
    ids=["short-title", "native-clipping", "hover-and-restore", "cancel-and-drag", "overlays-and-focus", "invalidation-and-teardown"],
)
def test_native_title_reveal_behavior(scenario: str) -> None:
    controller = GESTURES[GESTURES.index("      let nativeTitleRevealGeometry;"):]
    script = HARNESS + KERNEL + controller + """
    installNativeTitleReveal();
    ctx.teardown.add('native-title-test', resetNativeTitleReveal);
    """ + scenario
    subprocess.run(
        ["node", "--input-type=commonjs", "-"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=10,
    )
