"""Behavioural test: the provider session transfer dialog's row eligibility.

Regression: 供应商会话的「复制或迁移」对话框里，一个已经停止的会话仍然是灰的、勾不
上。它被判成了 ``status = current``（HUD 当前跟随的会话），而复选框只认
``selectable`` —— 那是「永久删除保护」的标记，和迁移能否执行不是一回事：复制根本
不会删除源会话，所以「已停止的当前会话」本该可以复制，只有迁移才该拦住它。

同时这个对话框有三处会放大困惑：行内只给已归档的行显示原因（其它被拦住的行只
有一个灰复选框）、打开对话框时复用上一次扫描的旧清单（切换会话后状态不会重算）、
以及切换复制/迁移模式时列表不重绘。

这里用 node 直接跑真实的 settings shell 资产代码（带最小 DOM 桩），断言：
1. 复制模式：已停止的当前会话可勾选；迁移模式：同一行不可勾选；
2. 仍在运行中的会话在两种模式下都不可勾选（放宽复制模式不能把运行中的会话放进来）；
3. 每个被拦住的行都要显示中文原因，内部英文 ``blockedReason`` 不上屏；
4. 打开对话框时，陈旧清单必须触发重新扫描；新鲜清单不得产生额外扫描（无事件不做工作）。
"""

from __future__ import annotations

import subprocess

from codex_usage_hud.renderer_assets.session_cleanup import TEXT as SESSION_CLEANUP
from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS_SHELL


_HOST_STUBS = r"""
const assert = require("node:assert/strict");

const settingsModalId = "codex-usage-hud-settings-modal-test";
const settingsProviderName = "__codexUsageHudSettingsProvider";
const rootId = "codex-usage-hud-root";
// fragment_01 顶层声明的状态名（settings shell 会直接读写 window 上的这份状态）。
const sessionTransferStateName = "__codexUsageHudSessionTransferState";
const codexCliLaunchStorageKey = "codexUsageHudCodexCliLaunches:v1";
const settingsCommandBindingName = "codexUsageHudSettingsCommand";
const activeSessionBindingName = "codexUsageHudActiveSession";
const layoutBindingName = "codexUsageHudLayout";
const themeBindingName = "codexUsageHudTheme";
const composerAttachmentsBindingName = "codexUsageHudComposerAttachments";

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
const modal = makeNode();
const editor = makeNode();
let transferLayerHtmlWrites = 0;
const transferLayer = makeNode();
Object.defineProperty(transferLayer, "innerHTML", {
  configurable: true,
  get: () => "",
  set: () => { transferLayerHtmlWrites += 1; },
});

global.window = {};
global.document = {
  body: makeNode(),
  documentElement: makeNode(),
  getElementById: () => root,
  querySelector: (selector) => {
    const value = String(selector || "");
    if (value.includes(".codex-usage-hud-settings-dialog")) return modal;
    if (value.includes('[data-session-transfer-dialog="true"]')) {
      return sessionTransferState.open ? transferLayer : null;
    }
    if (value.includes('[data-provider-editor="true"]')) return editor;
    return null;
  },
  querySelectorAll: () => [],
  createElement: () => transferLayer,
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
const shared = {};

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

const sessionTransferState = {
  open: false,
  sourceProvider: "",
  targetProvider: "",
  mode: "copy",
  search: "",
  selectedIds: new Set(),
  scanRequestId: "",
  requestId: "",
  data: null,
  operation: null,
  startedAt: 0,
  cancelledRequestId: "",
  page: 0,
};
const sessionCleanupState = {
  data: null,
  pendingRequestId: "",
  scanStartedAt: 0,
  selectedIds: new Set(),
  previewTokenShown: "",
  searchDraft: "",
  operation: null,
};
const codexCliState = { open: false, launchRequestId: "", provider: "", options: null };
const pricingWorkflowState = { providerDeleteRequestId: "", providerDeleteProvider: "" };
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
const submittedCommands = [];
const ctx = {
  domains: { register: (_name, domain) => domain },
  state: { payload: () => payload },
  storage: { read: () => null, write: () => {} },
  lifecycle: {
    listen: () => () => {},
    active: () => true,
    timeout: () => 1,
    interval: () => 1,
    clearTimeout() {},
    clearInterval() {},
  },
  observers: {},
  bindings: {
    available: () => true,
    send: (_name, command) => { submittedCommands.push(command); return true; },
  },
};

function currentPayload() {
  return ctx.state.payload();
}
"""


_TEST_BODY = r"""
const SOURCE_PROVIDER = "oceanhong";
const TARGET_PROVIDER = "wintoken";

function makeRow(overrides) {
  return {
    id: "session-1",
    title: "会话",
    workdirName: "proj",
    workdirId: "wd-1",
    updatedAt: "2026-09-18T16:38:10+08:00",
    status: "idle",
    archived: false,
    bytes: 1024,
    descendantCount: 0,
    selectable: true,
    // 内部英文句子：绝不允许出现在界面上。
    blockedReason: "The current session cannot be permanently deleted.",
    active: false,
    transferable: true,
    transferBlockedReason: "",
    modelProvider: SOURCE_PROVIDER,
    clientKind: "cli",
    pendingSourceCleanup: false,
    ...overrides,
  };
}

const STOPPED_CURRENT = makeRow({
  id: "session-stopped-current",
  title: "已停止的当前会话",
  status: "current",
  selectable: false,
  active: false,
});
const BUSY_CURRENT = makeRow({
  id: "session-busy-current",
  title: "仍在运行的当前会话",
  status: "current",
  selectable: false,
  active: true,
});
const RUNNING = makeRow({
  id: "session-running",
  title: "运行中的会话",
  status: "running",
  selectable: false,
  active: true,
});
const IDLE = makeRow({ id: "session-idle", title: "空闲会话" });
const OTHER_PROVIDER = makeRow({
  id: "session-other-provider",
  title: "别的供应商",
  modelProvider: "token-x",
});

function makePayload({ generatedAt = new Date().toISOString() } = {}) {
  const sessions = [STOPPED_CURRENT, BUSY_CURRENT, RUNNING, IDLE, OTHER_PROVIDER];
  return {
    settings: {
      app_provider: TARGET_PROVIDER,
      provider_order: [SOURCE_PROVIDER, TARGET_PROVIDER],
      provider_registry: { [SOURCE_PROVIDER]: {}, [TARGET_PROVIDER]: {} },
      provider_settings: { [SOURCE_PROVIDER]: {}, [TARGET_PROVIDER]: {} },
      model_prices: {},
    },
    sessionCleanup: {
      revision: "rev-1",
      generatedAt,
      capability: { available: true, reason: "" },
      operation: { action: "idle", state: "idle" },
      totals: {},
      sessions,
    },
  };
}

function rowFor(html, id) {
  const marker = `data-session-transfer-id="${id}"`;
  const at = html.indexOf(marker);
  if (at < 0) return "";
  // 行从包住复选框的 <label> 开始，否则会漏掉 <input> 标签本身。
  const start = html.lastIndexOf("<label", at);
  const next = html.indexOf("data-session-transfer-id=", at + 1);
  const end = next < 0 ? html.length : html.lastIndexOf("<label", next);
  return html.slice(start, end);
}

function isDisabled(html, id) {
  return /<input type="checkbox"[^>]*\sdisabled/.test(rowFor(html, id));
}

function selectableIds() {
  return settingsShellDomain.sessionTransferRows()
    .filter((row) => row.selectable === true)
    .map((row) => row.id);
}

function loadPayload(options = {}) {
  const full = makePayload(options);
  payload = full;
  sessionCleanupState.data = full.sessionCleanup;
  sessionTransferState.data = full.sessionCleanup;
  return full;
}

// ---- 复制模式：已停止的当前会话必须可勾选 ------------------------------
sessionTransferState.open = true;
sessionTransferState.sourceProvider = SOURCE_PROVIDER;
sessionTransferState.targetProvider = TARGET_PROVIDER;
sessionTransferState.mode = "copy";
loadPayload();

assert.deepEqual(
  selectableIds(),
  ["session-stopped-current", "session-idle"],
  "复制模式下：已停止的当前会话可勾选；运行中的会话仍不可勾选；其它供应商的行不出现",
);

let html = settingsShellDomain.sessionTransferDialogHtml();
assert.equal(isDisabled(html, "session-stopped-current"), false,
  "复制模式下已停止的当前会话的复选框必须可用");
assert.equal(isDisabled(html, "session-busy-current"), true,
  "复制模式下仍在运行的当前会话必须被拦住（分叉一个写了一半的会话没有意义）");
assert.equal(isDisabled(html, "session-running"), true,
  "复制模式下运行中的会话必须被拦住");
assert.ok(!html.includes("The current session cannot be permanently deleted."),
  "内部英文 blockedReason 不允许上屏");
assert.ok(rowFor(html, "session-busy-current").includes("仍在运行中"),
  "被拦住的行必须显示中文原因");
assert.ok(rowFor(html, "session-running").includes("仍在运行中"),
  "被拦住的行必须显示中文原因");

// ---- 迁移模式：当前会话不能被迁移（迁移会删除源会话） ------------------
sessionTransferState.mode = "migrate";
assert.deepEqual(selectableIds(), ["session-idle"],
  "迁移模式下：已停止的当前会话同样不可勾选，只有空闲会话可选");

html = settingsShellDomain.sessionTransferDialogHtml();
assert.equal(isDisabled(html, "session-stopped-current"), true,
  "迁移模式下已停止的当前会话必须被拦住");
assert.ok(rowFor(html, "session-stopped-current").includes("不能迁移"),
  "迁移模式下要说明是「不能迁移」而不是「仍在运行」");
assert.ok(!html.includes("The current session cannot be permanently deleted."),
  "内部英文 blockedReason 不允许上屏");

// ---- 陈旧清单：打开对话框必须重新扫描 ----------------------------------
sessionTransferState.open = false;
sessionTransferState.data = null;
sessionCleanupState.data = null;
sessionCleanupState.pendingRequestId = "";
submittedCommands.length = 0;

payload = makePayload();
settingsShellDomain.openSessionTransferDialog(SOURCE_PROVIDER);
assert.equal(submittedCommands.length, 0,
  "新鲜清单不该触发额外扫描（无事件不做周期性工作）");

sessionTransferState.open = false;
sessionTransferState.data = null;
sessionCleanupState.data = null;
sessionCleanupState.pendingRequestId = "";
submittedCommands.length = 0;

payload = makePayload({ generatedAt: new Date(Date.now() - 10 * 60 * 1000).toISOString() });
settingsShellDomain.openSessionTransferDialog(SOURCE_PROVIDER);
assert.equal(submittedCommands.length, 1,
  "陈旧清单必须重新扫描：切换会话后 status/selectable 会变，旧快照会让行一直灰着");
assert.equal(submittedCommands[0].action, "sessionCleanupScan",
  "重新扫描要发出会话清单扫描命令");

// ---- 重复 payload：不得替换目标 Provider 下拉框所在的 DOM --------------
const terminal = makePayload();
terminal.sessionCleanup.operation = {
  action: "sessionTransfer",
  state: "completed",
  requestId: "transfer-1",
  sourceProvider: SOURCE_PROVIDER,
  targetProvider: TARGET_PROVIDER,
};
payload = terminal;
sessionCleanupState.data = terminal.sessionCleanup;
sessionTransferState.data = terminal.sessionCleanup;
sessionTransferState.operation = terminal.sessionCleanup.operation;
sessionTransferState.open = true;
sessionTransferState.sourceProvider = SOURCE_PROVIDER;
sessionTransferState.targetProvider = TARGET_PROVIDER;
transferLayerHtmlWrites = 0;

settingsShellDomain.applySessionTransferPayload(terminal);
settingsShellDomain.applySessionTransferPayload(terminal);
assert.equal(transferLayerHtmlWrites, 0,
  "相同 revision 的终态 payload 不得重建对话框，否则打开的 Provider 下拉框会被收起");

const nextTerminal = makePayload();
nextTerminal.sessionCleanup.operation = {
  ...terminal.sessionCleanup.operation,
  requestId: "transfer-2",
  state: "failed",
};
settingsShellDomain.applySessionTransferPayload(nextTerminal);
settingsShellDomain.applySessionTransferPayload(nextTerminal);
assert.equal(transferLayerHtmlWrites, 1,
  "新的终态 operation 必须重建一次以更新结果和行状态，但重复下发不得再次重建");

transferLayerHtmlWrites = 0;
const refreshed = makePayload();
refreshed.sessionCleanup.revision = "rev-2";
settingsShellDomain.applySessionTransferPayload(refreshed);
assert.equal(transferLayerHtmlWrites, 1,
  "会话清单 revision 变化时仍须重建列表");
settingsShellDomain.applySessionTransferPayload(refreshed);
assert.equal(transferLayerHtmlWrites, 1,
  "同一份新清单重复下发时不得再次重建对话框");

console.log("session transfer row eligibility ok");
"""


def test_session_transfer_rows_follow_the_transfer_mode(tmp_path) -> None:
    # settings shell 资产接近 400KB，命令行参数会超长，落盘后再交给 node 执行。
    # 装配顺序与 manifest 一致：session_cleanup 资产提供 sessionCleanupFromPayload。
    script = "\n".join([_HOST_STUBS, SESSION_CLEANUP, SETTINGS_SHELL, _TEST_BODY])
    script_path = tmp_path / "session_transfer_rows.js"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert "session transfer row eligibility ok" in completed.stdout
