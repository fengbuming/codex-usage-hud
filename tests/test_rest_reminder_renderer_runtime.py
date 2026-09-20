"""Exercise reminder dismissal against repeated renderer payloads."""

import shutil
import subprocess

import pytest

from codex_usage_hud.renderer_assets.rest_reminder import TEXT


def test_dismissed_prompt_stays_hidden_until_next_round_or_submission_failure():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for renderer behavior checks")
    harness = r'''
const assert = require("node:assert/strict");
const rootId = "hud";
const shared = {};
const element = () => ({dataset: {}, setAttribute() {}, querySelector() {return null;}});
const toast = element(), mask = element(), bubble = element();
const root = {querySelector(selector) {
  if (selector.includes("toast")) return toast;
  if (selector.includes("mask")) return mask;
  if (selector.includes("bubble")) return bubble;
  return null;
}};
const document = {getElementById() {return root;}, querySelector(s) {return root.querySelector(s);}};
const ctx = {domains: {register(name, domain) {return domain;}}, lifecycle: {
  interval() {return 1;}, clearInterval() {}, frame() {},
}};
const desktopOverlayDependency = () => ({installed: true});
const formatRestReminderRemaining = () => "00:30";
let payload;
const currentPayload = () => payload;
'''
    checks = r'''
const prompt = {visible: true, phase: "prompt", promptWaitInfinite: true,
  promptStartedAtMs: 1000, promptEndsAtMs: 0};
payload = {restReminder: prompt};
restReminderDomain.apply(root, payload);
assert.equal(toast.dataset.visible, "true");
restReminderDomain.dismiss();
for (let i = 0; i < 5; i++) restReminderDomain.apply(root, payload);
assert.equal(toast.dataset.visible, "false");
assert.equal(mask.dataset.visible, "false");
restReminderDomain.restore();
assert.equal(toast.dataset.visible, "true");
restReminderDomain.dismiss();
payload = {restReminder: {...prompt, phase: "resting", visible: false}};
restReminderDomain.apply(root, payload);
payload = {restReminder: prompt};
restReminderDomain.apply(root, payload);
assert.equal(toast.dataset.visible, "false");
payload = {restReminder: {...prompt, promptStartedAtMs: 2000}};
restReminderDomain.apply(root, payload);
assert.equal(toast.dataset.visible, "true");
payload = {restReminder: {visible: true, phase: "preview", preview: true,
  promptEndsAtMs: Date.now() + 60000}};
restReminderDomain.apply(root, payload);
assert.equal(toast.dataset.visible, "true");
restReminderDomain.dismiss();
restReminderDomain.apply(root, payload);
assert.equal(toast.dataset.visible, "false");
payload = {restReminder: {...payload.restReminder, promptEndsAtMs: Date.now() + 120000}};
restReminderDomain.apply(root, payload);
assert.equal(toast.dataset.visible, "true");
'''
    result = subprocess.run(
        [node, "-e", harness + TEXT + checks], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
