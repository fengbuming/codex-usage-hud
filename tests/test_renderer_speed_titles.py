from __future__ import annotations

import json
import subprocess

from codex_usage_hud.renderer_assets.session_view import TEXT as SESSION_VIEW
from codex_usage_hud.renderer_assets.speed_titles import VEHICLE_ART


def test_speed_titles_use_upstream_thresholds_hysteresis_hold_and_idle_delay() -> None:
    title_helpers = SESSION_VIEW.split("  const speedTitleArt = ", 1)[1].split(
        "  function lineInner(", 1
    )[0]
    title_helpers = "const speedTitleArt = " + title_helpers
    title_helpers = title_helpers.replace(
        "__CODEX_USAGE_HUD_SPEED_TITLE_ART__",
        json.dumps(VEHICLE_ART, ensure_ascii=False, separators=(",", ":")),
    )
    render_output_speed = (
        "  function renderOutputSpeed("
        + SESSION_VIEW.split("  function renderOutputSpeed(", 1)[1].split(
            "  function setFieldTitle(", 1
        )[0]
    )
    script = r'''
const assert = require("node:assert/strict");
let now = 0;
let nextTimer = 0;
const timers = new Map();
const ctx = {lifecycle: {
  timeout(owner, callback, delay) {
    const id = ++nextTimer;
    timers.set(id, {owner, callback, due: now + delay});
    return id;
  },
  clearTimeout(id) { timers.delete(id); },
}};
function advance(milliseconds) {
  const end = now + milliseconds;
  for (;;) {
    const due = [...timers.entries()].sort((a, b) => a[1].due - b[1].due)[0];
    if (!due || due[1].due > end) break;
    now = due[1].due;
    timers.delete(due[0]);
    due[1].callback();
  }
  now = end;
}
function speedNode() {
  const icon = {dataset: {}, style: {}, innerHTML: "", setAttribute(key, value) { this[key] = value; }};
  const value = {textContent: ""};
  return {
    hidden: true, title: "", dataset: {}, value, icon,
    querySelector(selector) {
      if (selector === '[data-field="outputSpeedIcon"]') return icon;
      if (selector === '[data-field="outputSpeedValue"]') return value;
      return null;
    },
  };
}
const nodes = [speedNode(), speedNode()];
const root = {querySelectorAll: selector => selector === '[data-field="outputSpeed"]' ? nodes : []};
''' + title_helpers + render_output_speed + r'''
const expectedThresholds = [0.5, 40, 60, 80, 120, 160, 240, 340, 480, 680, 1000, 1400, 2000];
assert.equal(speedTitleSteps.length, 13);
for (let index = 1; index <= expectedThresholds.length; index += 1) {
  assert.equal(speedTitleThreshold(index), expectedThresholds[index - 1]);
  assert.match(speedTitleMarkup(index), /<svg .*<\/svg>/);
  assert.match(speedTitleMarkup(index), /data-speed-motion="(walk|road|rail|flight|rocket)"/);
}
assert.match(speedTitleMarkup(1), /data-speed-part="leg-0"/);
assert.match(speedTitleMarkup(1), /data-speed-part="leg-1"/);
assert.match(speedTitleMarkup(2), /translate\(26.63 60.23\).*data-speed-part="wheel"/);
assert.match(speedTitleMarkup(13), /data-speed-part="flame"/);
renderOutputSpeed(root, {requestStatus: "confirmed", outputSpeedMode: "measured", outputTokensPerSecond: 39.99});
assert.equal(nodes[0].hidden, false);
assert.equal(nodes[0].value.textContent, "≈40.0 tok/s");
assert.equal(nodes[0].dataset.speedTitle, "待机");
assert.equal(nodes[0].icon.dataset.speedActive, "false");
assert.match(nodes[0].icon.title, /低于 0\.5 tok\/s 持续 3 秒/);
renderOutputSpeed(root, {requestStatus: "confirmed", outputSpeedMode: "measured", outputTokensPerSecond: 40});
advance(799);
assert.equal(nodes[0].dataset.speedTitle, "待机");
advance(1);
assert.equal(nodes[0].dataset.speedTier, "2");
assert.equal(nodes[0].dataset.speedTitle, "自行车");
assert.equal(nodes[1].dataset.speedTitle, "自行车");
assert.equal(nodes[0].icon.dataset.speedActive, "true");
assert.match(nodes[0].icon.innerHTML, /<circle/);
assert.match(nodes[0].icon.style.color, /^#[0-9a-f]{6}$/i);
const bicycleMarkup = nodes[0].icon.innerHTML;
renderOutputSpeed(root, {requestStatus: "confirmed", outputSpeedMode: "measured", outputTokensPerSecond: 36});
assert.equal(speedTitleCandidate(36), 2);
assert.equal(nodes[0].icon.innerHTML, bicycleMarkup);
renderOutputSpeed(root, {requestStatus: "confirmed", outputSpeedMode: "measured", outputTokensPerSecond: 35.99});
advance(800);
assert.equal(nodes[0].dataset.speedTier, "1");
renderOutputSpeed(root, {requestStatus: "confirmed", outputSpeedMode: "measured", outputTokensPerSecond: 0.4});
advance(2999);
assert.equal(nodes[0].dataset.speedTitle, "步行");
advance(1);
assert.equal(nodes[0].dataset.speedTitle, "待机");
assert.equal(nodes[0].icon.dataset.speedActive, "false");
renderOutputSpeed(root, {requestStatus: "running", outputSpeedMode: "estimated", outputTokensPerSecond: 2100});
assert.equal(nodes[0].value.textContent, "≈2100 tok/s");
assert.equal(nodes[0].dataset.speedMode, "estimated");
advance(800);
assert.equal(nodes[0].dataset.speedTier, "13");
assert.equal(nodes[0].icon.dataset.speedTitle, "rocket");
renderOutputSpeed(root, {requestStatus: "confirmed", outputSpeedMode: "estimated", outputTokensPerSecond: 45});
assert.equal(nodes[0].value.textContent, "≈45.0 tok/s");
advance(800);
assert.equal(nodes[0].icon.dataset.speedTitle, "bicycle");
renderOutputSpeed(root, {requestStatus: "waiting", outputSpeedMode: "idle"});
assert.equal(nodes[0].icon.dataset.speedActive, "false");
assert.equal(timers.size, 0);
'''
    result = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_vehicle_art_contains_every_upstream_speed_title() -> None:
    assert tuple(VEHICLE_ART) == (
        "walk",
        "bicycle",
        "ebike",
        "motorcycle",
        "car",
        "sportsCar",
        "bulletTrain",
        "raceCar",
        "maglev",
        "jetliner",
        "fighterJet",
        "hypersonic",
        "rocket",
    )
    assert all(art["parts"] and len(art["viewBox"]) == 4 for art in VEHICLE_ART.values())
