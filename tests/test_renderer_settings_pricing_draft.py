"""Behavioural test: settings payload must repaint the provider price table.

Regression: 在「价格检查 / 官方模型价格」里点「确认更新」后，后台确实写入了新单价，
但设置界面的模型单价列表仍显示旧值，必须重开设置界面才刷新。根因是缓存的供应商
草稿（settingsProviderDraft）不会随 settings 域 payload 失效。

这里用 node 直接跑真实的 settings shell 资产代码（带最小 DOM 桩），断言：
1. 打开设置界面时按 payload 渲染旧单价；
2. settings 域 payload 带来新单价时（即使没有任何 settingsCommandStatus），单价表
   必须立刻重绘成新值 —— 这正是原 bug 缺失的行为；
3. payload 未变化时不产生额外重绘（事件驱动，无事件不做工作）；
4. 只有其它供应商的单价变化时，不打断当前供应商的表单；
5. 其它供应商的草稿同样被对账更新，切换 tab 即可看到新值。
"""

from __future__ import annotations

import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS_SHELL


_HOST_STUBS = r"""
const assert = require("node:assert/strict");

const settingsModalId = "codex-usage-hud-settings-modal-test";
const settingsProviderName = "__codexUsageHudSettingsProvider";
const rootId = "codex-usage-hud-root";

const renders = { editor: 0, modal: 0 };
const editorHtml = { value: "" };
const modalHtml = { value: "" };

function makeNode(extra = {}) {
  return Object.assign({
    dataset: {},
    hidden: false,
    innerHTML: "",
    type: "",
    title: "",
    textContent: "",
    className: "",
    scrollLeft: 0,
    scrollWidth: 0,
    clientWidth: 0,
    querySelector: () => null,
    querySelectorAll: () => [],
    setAttribute() {},
    removeAttribute() {},
    getAttribute: () => null,
    appendChild() {},
    remove() {},
    closest: () => null,
    matches: () => false,
    addEventListener() {},
    removeEventListener() {},
    getBoundingClientRect: () => ({ left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 }),
    focus() {},
  }, extra);
}

const root = makeNode();
const editor = makeNode();
Object.defineProperty(editor, "innerHTML", {
  get: () => editorHtml.value,
  set: (value) => { editorHtml.value = value; renders.editor += 1; },
});
const modal = makeNode();
Object.defineProperty(modal, "innerHTML", {
  get: () => modalHtml.value,
  set: (value) => { modalHtml.value = value; renders.modal += 1; },
});

global.window = {};
global.document = {
  body: makeNode(),
  documentElement: makeNode(),
  getElementById: (id) => (id === settingsModalId ? modal : root),
  querySelector: (selector) => (
    String(selector).includes('[data-provider-editor="true"]') ? editor : null
  ),
  querySelectorAll: () => [],
  createElement: () => makeNode(),
  addEventListener() {},
  removeEventListener() {},
};
global.HTMLButtonElement = class {};
global.HTMLSelectElement = class {};
global.HTMLElement = class {};
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.requestAnimationFrame = () => 1;
global.cancelAnimationFrame = () => {};
global.setTimeout = () => 1;
global.clearTimeout = () => {};
global.MutationObserver = class { observe() {} disconnect() {} };
global.getComputedStyle = () => ({ getPropertyValue: () => "" });

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function providerRegistryDisplayName(_settings, provider) {
  return String(provider || "");
}

const themeDomain = { apply() {} };
const restReminderDomain = { apply() {} };
const sessionViewDomain = { applySearchJump() {} };
function refreshComposerBadgeState() {}
function ensureSessionCleanupElapsedTicker() {}

// settings shell 只把 shared 当透传参数（内部未使用），这里给空对象即可。
const shared = {};

// 这些跨域状态在真实 bundle 里由 fragment_01 顶层声明，settings shell 直接读写。
let settingsActiveTab = "settings";
let settingsProviderDraft = null;
const settingsDirtyProviders = new Set();
let restReminderSavedRequestId = "";
let pendingProviderRestartAfterSave = null;
let settingsRestartPending = false;
let settingsRestartCodex = false;
let settingsStatusErrorSticky = false;

function readSettingsUiState() { return null; }
function writeSettingsUiState() {}

// fragment_01 顶层声明的其余状态/定时器（settings shell 的打开与状态同步路径会读到）。
const sessionTransferState = { open: false, data: null, requestId: "", scanRequestId: "" };
const backgroundUsageState = { data: null, detail: null, selectedEventId: "", loadedRevision: -1 };
const backgroundUsageBodyScrollTops = new Map();
const backgroundUsageHistoryScrollTops = new Map();
const backgroundUsageDetailScrollTops = new Map();
let restReminderCountdownTimer = 0;
let sessionCleanupElapsedTimer = 0;
let sessionCleanupScanWatchdogTimer = 0;
let sessionCleanupSearchTimer = 0;
let storageRefreshRaf = 0;
let storageRefreshTimer = 0;
let storageRefreshLastAt = 0;
let backgroundUsageFetchSeq = 0;
let backgroundUsageDetailSeq = 0;
let backgroundUsageQueryTimeoutId = 0;
let backgroundUsageDetailTimeoutId = 0;

let payload = null;
const ctx = {
  domains: { register: (_name, domain) => domain },
  state: { payload: () => payload },
  storage: { read: () => null, write: () => {} },
  lifecycle: { listen: () => () => {}, active: () => true },
  observers: {},
  bindings: { available: () => false, send: () => true },
};

function currentPayload() {
  return ctx.state.payload();
}
"""


_TEST_BODY = r"""
function providerTable(provider, input) {
  return {
    "gpt-5.6-sol": {
      model: "gpt-5.6-sol",
      provider,
      input,
      cached_input: input / 10,
      cache_write: input,
      output: input * 5,
      reasoning: input * 5,
    },
  };
}

function makePayload({ customInput = 5, otherInput = 5 } = {}) {
  return {
    settings: {
      app_provider: "custom",
      provider_order: ["custom", "other"],
      provider_registry: { custom: {}, other: {} },
      model_prices: {},
      provider_settings: {
        custom: {
          model_prices: providerTable("custom", customInput),
          pricing_url: "",
          weekly_adjustment_usd: 0,
        },
        other: {
          model_prices: providerTable("other", otherInput),
          pricing_url: "",
          weekly_adjustment_usd: 0,
        },
      },
      pricing_sync: { unread_change_count: 0 },
    },
    settingsCommandStatus: {},
  };
}

// 取出某个模型行的「输入」单价输入框值，用于断言单价表是否真的被重绘。
function priceRowInputValue(html, model) {
  const start = html.indexOf(`data-price-key="${model}"`);
  if (start < 0) return null;
  const next = html.indexOf('data-price-row="true"', start + 1);
  const row = html.slice(start, next < 0 ? undefined : next);
  const match = /data-price-field="input"[^>]*value="([^"]*)"/.exec(row);
  return match ? match[1] : null;
}

payload = makePayload();
settingsShellDomain.renderSettingsModal("settings");
assert.ok(renders.modal > 0, "settings modal should render");
assert.equal(priceRowInputValue(modalHtml.value, "gpt-5.6-sol"), "5",
  "opening the settings panel must render the payload price table");

// 1) 后台写入新单价：settings 域 payload 到达，且没有任何 settingsCommandStatus。
renders.editor = 0;
payload = makePayload({ customInput: 4 });
settingsShellDomain.applySettingsPayload(root, payload);
assert.equal(renders.editor, 1,
  "a changed price table in the settings payload must repaint the price table");
assert.equal(priceRowInputValue(editorHtml.value, "gpt-5.6-sol"), "4",
  "the repainted price table must show the committed price");

// 2) 同一 payload 再推一次：指纹没变，不应重绘。
renders.editor = 0;
settingsShellDomain.applySettingsPayload(root, payload);
assert.equal(renders.editor, 0, "an unchanged payload must not repaint the price table");

// 3) 只有其它供应商的单价变化：不能打断当前供应商的表单。
renders.editor = 0;
payload = makePayload({ customInput: 4, otherInput: 9 });
settingsShellDomain.applySettingsPayload(root, payload);
assert.equal(renders.editor, 0,
  "another provider's price change must not rewrite the active provider editor");

// 4) 其它供应商的草稿同样已对账：切到它的 tab 就能看到新单价。
settingsShellDomain.switchSettingsProvider("other");
assert.equal(priceRowInputValue(editorHtml.value, "gpt-5.6-sol"), "9",
  "the reconciled draft of the other provider must carry the new price");

console.log("settings pricing draft reconciliation ok");
"""


def test_settings_payload_price_change_repaints_provider_price_table(tmp_path) -> None:
    # settings shell 资产接近 400KB，命令行参数会超长，落盘后再交给 node 执行。
    script = "\n".join([_HOST_STUBS, SETTINGS_SHELL, _TEST_BODY])
    script_path = tmp_path / "settings_pricing_draft.js"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert "settings pricing draft reconciliation ok" in completed.stdout
