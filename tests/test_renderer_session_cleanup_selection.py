"""Exercise cross-page deletion selection against the real renderer assets."""

from __future__ import annotations

import subprocess

from codex_usage_hud.renderer_assets.router import TEXT as ROUTER
from codex_usage_hud.renderer_assets.session_cleanup import TEXT as SESSION_CLEANUP


_HOST = r"""
const assert = require("node:assert/strict");
const listeners = new Map();
const commands = [];
const layers = [];
let renders = 0;
let sessionCleanupSearchTimer = 0;
let sessionCleanupElapsedTimer = 0;
let sessionCleanupScanWatchdogTimer = 0;
let storageRefreshRaf = 0;
let storageRefreshTimer = 0;
let storageRefreshLastAt = 0;
let storageBodyScrollTop = 0;
let cleanupContentScrollTop = 0;
let sessionTableScrollTop = 0;
let settingsActiveTab = "storage";
const settingsModalId = "settings-test";
const rootId = "root-test";
const shared = {};
const root = { dataset: {}, contains: () => true };
const modal = { hidden: true, querySelector: () => null, appendChild: (node) => layers.push(node) };
global.window = {};
global.HTMLSelectElement = class {};
global.HTMLInputElement = class {};
global.HTMLElement = class {};
global.document = {
  querySelector: () => null,
  getElementById: () => modal,
  createElement: () => ({ dataset: {}, innerHTML: "", querySelector: () => null }),
};
const sessionTransferState = { open: false };
const sessionCleanupState = {
  data: null, sessionIndex: null, pendingRequestId: "", selectedIds: new Set(),
  selectionSnapshot: null, previewScope: null, previewTokenShown: "",
  search: "", searchDraft: "", searchRequestId: "", searchResultQuery: "",
  searchResultRevision: "", searchResultState: "idle", searchResultGeneration: 0,
  searchResultMatches: new Set(), searchResultDetails: new Map(),
  workdirId: "", workdirOptions: [], sessionJumpInflight: new Set(),
  sessionJumpUnavailableIds: new Set(), dateStart: "", dateEnd: "",
  dateDraftStart: "", dateDraftEnd: "", archive: "all", availability: "all",
  clientKind: "all", modelProvider: "all", sort: "recent", page: 0,
};
const ctx = {
  domains: { register: (_name, domain) => domain },
  lifecycle: {
    clearTimeout() {}, clearInterval() {}, timeout: () => 1,
    scope: () => ({ listen: (node, type, callback) => {
      if (node === root) listeners.set(type, [...(listeners.get(type) || []), callback]);
    } }),
  },
};
function currentPayload() { return {}; }
function providerRegistryDisplayName(_settings, provider) { return provider; }
function backgroundUsageTime(value) { return value; }
function renderSettingsModal() { renders += 1; }
function persistSessionCleanupFilters() {}
function settingsDialogRoot() { return modal; }
function closeSettingsConfirm() {}
function setSettingsStatus() {}
function rerenderUsageInsightsIfVisible() {}
function typedSettingsRequestId(prefix) { return `${prefix}-${commands.length + 1}`; }
function submitSettingsCommand(command) { commands.push(command); return true; }

function inventory(count = 95) {
  return {
    revision: "r1", capability: { available: true }, operation: { state: "completed" },
    sessions: Array.from({ length: count }, (_, index) => ({
      id: `session-${index}`, title: `Session ${index}`,
      updatedAt: new Date(1700000000000 + index * 1000).toISOString(),
      selectable: index >= 2, status: index === 0 ? "current" : index === 1 ? "running" : "idle",
      archived: index < 75, bytes: 1024, descendantCount: 1,
      clientKind: "app", modelProvider: "openai", workdirName: "project", workdirId: "workdir-1",
    })),
    workdirs: [{ id: "workdir-1", label: "project" }],
  };
}
function emit(type, selector, properties = {}) {
  const node = { ...properties, closest: (value) => value === selector ? node : null };
  const event = { target: node, key: "Enter", preventDefault() {}, stopPropagation() {} };
  for (const callback of listeners.get(type) || []) callback(event);
}
function pageSelect(checked = true) {
  emit("change", '[data-session-cleanup-select-all="true"]', { checked });
}
function action(name) { emit("click", "[data-action]", { dataset: { action: name } }); }
function panel() { return sessionCleanupPanelHtml(); }
function expandButton() { return panel().match(/<button[^>]*data-action="session-cleanup-select-filtered"[^>]*>/)?.[0]; }
"""


_BIND_ROOT = ROUTER[
    ROUTER.index("  function bindRoot(root) {") : ROUTER.index("  function markHudStale() {")
]
_ESCAPE = ROUTER[: ROUTER.index("  // ")]


def _run(tmp_path, assertions: str) -> None:
    script = "\n".join(
        [_HOST, _ESCAPE, SESSION_CLEANUP, _BIND_ROOT,
         "bindRoot(root); sessionCleanupState.data = inventory();", assertions]
    )
    script_path = tmp_path / "session_cleanup_selection.js"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(script_path)], capture_output=True, text=True,
        encoding="utf-8", check=False,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"


def test_cross_page_select_all_excludes_protected_and_supports_exceptions(tmp_path) -> None:
    _run(tmp_path, r"""
assert.equal(expandButton(), undefined);
pageSelect();
assert.equal(sessionCleanupState.selectedIds.size, 30);
assert.ok(expandButton());
action("session-cleanup-select-filtered");
assert.equal(sessionCleanupState.selectedIds.size, 93);
assert.equal(sessionCleanupState.selectedIds.has("session-0"), false);
assert.equal(sessionCleanupState.selectedIds.has("session-1"), false);
assert.ok(panel().includes("\u5df2\u9009\u5168\u90e8 93"));
assert.ok(panel().includes("\u5df2\u8df3\u8fc7 2"));
assert.equal(expandButton(), undefined);
action("session-cleanup-page");
assert.equal(sessionCleanupState.page, 1);
assert.equal(sessionCleanupState.selectedIds.size, 93);
const id = sessionCleanupPageRows()[0].id;
emit("change", "[data-session-cleanup-id]", { checked: false, dataset: { sessionCleanupId: id } });
assert.equal(sessionCleanupState.selectedIds.size, 92);
assert.ok(panel().includes("\u5df2\u6392\u9664 1"));
assert.ok(panel().includes('aria-checked="mixed"'));
pageSelect(false);
assert.equal(sessionCleanupState.selectedIds.size, 63);
pageSelect();
assert.equal(sessionCleanupState.selectedIds.size, 93);
action("session-cleanup-selection-clear");
assert.equal(sessionCleanupState.selectedIds.size, 0);
assert.equal(sessionCleanupState.selectionSnapshot, null);
// Page-by-page selection remains usable for one final deletion.
sessionCleanupState.page = 0;
pageSelect();
moveSessionCleanupPage(1);
pageSelect();
assert.equal(sessionCleanupState.selectedIds.size, 60);
// Large selections still keep only thirty rendered rows.
clearSessionCleanupSelection();
sessionCleanupState.data = inventory(5000);
sessionCleanupState.page = 0;
pageSelect();
action("session-cleanup-select-filtered");
assert.equal(sessionCleanupState.selectedIds.size, 4998);
assert.equal((panel().match(/class="codex-usage-hud-session-row"/g) || []).length, 30);
""")


def test_scope_changes_clear_selection_but_sort_keeps_it(tmp_path) -> None:
    _run(tmp_path, r"""
for (const [key, value] of [["sort", "oldest"], ["archive", "archived"],
  ["availability", "selectable"], ["clientKind", "app"], ["modelProvider", "openai"],
  ["workdirId", "workdir-1"]]) {
  sessionCleanupState.page = 0;
  pageSelect();
  action("session-cleanup-select-filtered");
  const count = sessionCleanupState.selectedIds.size;
  emit("change", "[data-session-cleanup-filter]", { value, dataset: { sessionCleanupFilter: key } });
  assert.equal(sessionCleanupState.selectedIds.size, key === "sort" ? count : 0, key);
  if (key !== "sort") assert.equal(sessionCleanupState.selectionSnapshot, null);
}
for (const name of ["session-cleanup-date-reset", "session-cleanup-date-confirm", "session-cleanup-filters-clear"]) {
  pageSelect();
  action("session-cleanup-select-filtered");
  action(name);
  assert.equal(sessionCleanupState.selectedIds.size, 0, name);
  assert.equal(sessionCleanupState.selectionSnapshot, null);
}
pageSelect();
action("session-cleanup-select-filtered");
emit("input", '[data-session-cleanup-search="true"]', { value: "Session" });
assert.equal(sessionCleanupState.selectedIds.size, 0);
assert.equal(sessionCleanupState.selectionSnapshot, null);
assert.equal(sessionCleanupSelectionReady(), false);
""")


def test_bulk_selection_waits_for_matching_completed_search(tmp_path) -> None:
    _run(tmp_path, r"""
sessionCleanupState.search = "Session";
sessionCleanupState.searchResultQuery = "Session";
sessionCleanupState.searchResultRevision = "r1";
sessionCleanupState.searchResultMatches = new Set(sessionCleanupState.data.sessions.map((item) => item.id));
for (const state of ["idle", "pending", "indexing", "failed"]) {
  sessionCleanupState.searchResultState = state;
  pageSelect();
  assert.ok(expandButton().includes("disabled"), state);
  assert.equal(selectAllSessionCleanupRows(), false, state);
  assert.equal(sessionCleanupState.selectionSnapshot, null);
}
sessionCleanupState.searchResultState = "completed";
for (const [key, value] of [["searchRequestId", "waiting"], ["searchResultQuery", "other"], ["searchResultRevision", "old"]]) {
  const previous = sessionCleanupState[key];
  sessionCleanupState[key] = value;
  assert.equal(selectAllSessionCleanupRows(), false, key);
  sessionCleanupState[key] = previous;
}
sessionCleanupSearchTimer = 1;
assert.equal(selectAllSessionCleanupRows(), false);
sessionCleanupSearchTimer = 0;
assert.equal(selectAllSessionCleanupRows(), true);
assert.equal(sessionCleanupState.selectedIds.size, 93);
assert.equal(expandButton(), undefined);
clearSessionCleanupSelection();
sessionCleanupState.searchResultMatches = new Set(sessionCleanupState.data.sessions.slice(0, 50).map((item) => item.id));
pageSelect();
assert.equal(selectAllSessionCleanupRows(), true);
assert.equal(sessionCleanupState.selectedIds.size, 48);
assert.equal(sessionCleanupState.selectedIds.has("session-80"), false);
""")


def test_selected_targets_stay_fixed_across_inventory_and_search_updates(tmp_path) -> None:
    _run(tmp_path, r"""
pageSelect();
action("session-cleanup-select-filtered");
const next = inventory();
next.sessions.push({ ...next.sessions[94], id: "new-session" });
applySessionCleanupPayload(null, { sessionCleanup: next });
assert.equal(sessionCleanupState.selectedIds.size, 93);
assert.equal(sessionCleanupState.selectedIds.has("new-session"), false);
assert.ok(panel().includes("\u65b0\u589e 1 \u4e2a\u672a\u9009\u4e2d"));
assert.ok(!panel().includes("\u5df2\u9009\u5168\u90e8 93"));
const partial = { revision: "r1", operation: { action: "search", state: "completed" } };
applySessionCleanupPayload(null, { sessionCleanup: partial });
assert.equal(sessionCleanupState.selectedIds.size, 93);
next.sessions.find((item) => item.id === "session-50").selectable = false;
applySessionCleanupPayload(null, { sessionCleanup: next });
assert.equal(sessionCleanupState.selectedIds.has("session-50"), false);
const refreshed = inventory();
refreshed.revision = "r2";
applySessionCleanupPayload(null, { sessionCleanup: refreshed });
assert.equal(sessionCleanupState.selectedIds.size, 0);
assert.equal(sessionCleanupState.selectionSnapshot, null);
""")


def test_delete_preview_freezes_scope_and_exact_selected_ids(tmp_path) -> None:
    _run(tmp_path, r"""
sessionCleanupState.archive = "archived";
sessionCleanupState.workdirId = "workdir-1";
pageSelect();
action("session-cleanup-select-filtered");
setSessionCleanupItemSelected("session-50", false);
const expected = Array.from(sessionCleanupState.selectedIds);
assert.equal(expected.length, 72);
assert.equal(requestSessionCleanupPreview(), true);
const command = commands.at(-1);
assert.equal(command.action, "sessionCleanupPreview");
assert.deepEqual(command.itemIds, expected);
assert.equal(command.inventoryRevision, "r1");
const frozenLabel = sessionCleanupState.previewScope.label;
assert.ok(frozenLabel.includes("project"));
assert.ok(frozenLabel.includes("\u5df2\u5728 Codex \u4e2d\u5f52\u6863"));
sessionCleanupState.archive = "all";
sessionCleanupState.workdirId = "";
sessionCleanupState.data.operation = {
  state: "preview", requestId: command.requestId, confirmationToken: "confirm-1",
  selectedIds: command.itemIds, descendantCount: 72, estimatedBytes: 72 * 1024,
};
openSessionCleanupExecuteConfirm();
const html = layers.at(-1).innerHTML;
assert.ok(html.includes(frozenLabel));
assert.ok(html.includes("\u5df2\u8df3\u8fc7 2"));
assert.ok(html.includes("\u6c38\u4e45\u5220\u9664 72"));
assert.ok(html.includes("72 KB"));
assert.ok(html.includes("\u9884\u8ba1\u91ca\u653e"));
assert.ok(html.includes("\u4e0d\u4f1a\u8fdb\u5165\u56de\u6536\u7ad9"));
// Another preview response cannot borrow this preview's scope.
sessionCleanupState.data.operation.requestId = "unrelated";
openSessionCleanupExecuteConfirm();
assert.ok(!layers.at(-1).innerHTML.includes(frozenLabel));
""")


def test_resident_search_update_only_removes_selection_and_never_adds_matches(tmp_path) -> None:
    _run(tmp_path, r"""
sessionCleanupState.search = "Session";
sessionCleanupState.searchResultQuery = "Session";
sessionCleanupState.searchResultRevision = "r1";
sessionCleanupState.searchResultState = "completed";
sessionCleanupState.searchResultMatches = new Set(sessionCleanupState.data.sessions.slice(0, 50).map((item) => item.id));
pageSelect();
assert.equal(selectAllSessionCleanupRows(), true);
assert.equal(sessionCleanupState.selectedIds.size, 48);
const matches = [...sessionCleanupState.searchResultMatches].filter((id) => id !== "session-30");
matches.push("session-50");
applySessionCleanupPayload(null, { sessionCleanup: {
  revision: "r1", operation: { state: "completed", action: "search" },
  search: { query: "Session", workdirId: "", revision: "r1", state: "completed", generation: 2, matches },
} });
assert.equal(sessionCleanupState.selectedIds.size, 47);
assert.equal(sessionCleanupState.selectedIds.has("session-30"), false);
assert.equal(sessionCleanupState.selectedIds.has("session-50"), false);
assert.ok(panel().includes("\u5df2\u6392\u9664 1"));
assert.ok(panel().includes("\u65b0\u589e 1 \u4e2a\u672a\u9009\u4e2d"));
""")
