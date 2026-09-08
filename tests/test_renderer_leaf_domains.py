from __future__ import annotations

import subprocess
from pathlib import Path

from codex_usage_hud.renderer_assets import manifest
from codex_usage_hud.renderer_assets.budget import TEXT as BUDGET
from codex_usage_hud.renderer_assets.active_session import TEXT as ACTIVE_SESSION
from codex_usage_hud.renderer_assets.background_usage import TEXT as BACKGROUND_USAGE
from codex_usage_hud.renderer_assets.diagnostics import TEXT as DIAGNOSTICS
from codex_usage_hud.renderer_assets.kernel import TEXT as KERNEL
from codex_usage_hud.renderer_assets.layout import TEXT as LAYOUT
from codex_usage_hud.renderer_assets.model_picker import TEXT as MODEL_PICKER
from codex_usage_hud.renderer_assets.composer import TEXT as COMPOSER
from codex_usage_hud.renderer_assets.rest_reminder import TEXT as REST_REMINDER
from codex_usage_hud.renderer_assets.session_view import TEXT as SESSION_VIEW
from codex_usage_hud.renderer_assets.session_cleanup import TEXT as SESSION_CLEANUP
from codex_usage_hud.renderer_assets.shared import TEXT as SHARED
from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS_SHELL
from codex_usage_hud.renderer_assets.theme import TEXT as THEME


DOMAIN_SOURCES = (
    ("model_picker", "ModelPicker", MODEL_PICKER),
    ("theme", "Theme", THEME),
    ("diagnostics", "Diagnostics", DIAGNOSTICS),
    ("budget", "Budget", BUDGET),
    ("rest_reminder", "RestReminder", REST_REMINDER),
    ("session_view", "SessionView", SESSION_VIEW),
)

P6_5_DOMAIN_SOURCES = (
    ("layout", "Layout", LAYOUT),
    ("composer", "Composer", COMPOSER),
)


def test_leaf_domain_owners_are_registered_in_the_fixed_order() -> None:
    assert manifest.ASSET_ORDER[4:10] == (
        "02_model_picker",
        "03_theme",
        "04_diagnostics",
        "05_budget",
        "06_rest_reminder",
        "07_session_view",
    )

    for name, factory, source in DOMAIN_SOURCES:
        assert f"function create{factory}Domain(ctx, shared)" in source
        assert f'ctx.domains.register(\n    "{name}",' in source
        assert "function install()" in source
        assert "function apply(" in source
        assert "function dispose()" in source


def test_layout_and_composer_owners_are_registered_after_settings_shell() -> None:
    assert manifest.ASSET_ORDER[14:16] == ("12_layout", "13_composer")
    for name, factory, source in P6_5_DOMAIN_SOURCES:
        assert f"function create{factory}Domain(ctx, shared)" in source
        assert f'ctx.domains.register(\n    "{name}",' in source
        assert "function install()" in source
        assert "function apply(" in source
        assert "function dispose()" in source

    assert "function ensureStyle()" not in manifest.ROUTER
    assert "function beginGesture(" not in manifest.ROUTER
    assert "function composerElement()" not in manifest.ROUTER
    assert "function ensureComposerInputWatchers()" not in manifest.ROUTER


def test_active_session_owner_keeps_sequence_capture_before_reinjection_remove() -> None:
    assert manifest.ASSET_ORDER[16:17] == ("14_active_session",)
    assert "function createActiveSessionDomain(ctx, shared)" in ACTIVE_SESSION
    assert 'ctx.domains.register(\n    "active_session",' in ACTIVE_SESSION
    assert "function ensureActiveSessionWatchers()" in ACTIVE_SESSION
    assert "function cacheActiveSessionPayload(payload)" in ACTIVE_SESSION
    assert "window.__codexUsageHudReportActiveSession" in ACTIVE_SESSION
    assert "function ensureActiveSessionWatchers()" not in manifest.ROUTER
    assert "function cacheActiveSessionPayload(payload)" not in manifest.ROUTER


def test_session_cleanup_pagination_uses_thirty_rows_and_reaches_last_page() -> None:
    factory = SESSION_CLEANUP.split(
        "  const sessionCleanupDomain = ctx.domains.register(", 1
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{}};
global.location = {{ href: "app://-/index.html" }};
const sessionCleanupState = {{
  data: null,
  search: "",
  workdirId: "",
  searchResultQuery: "",
  searchResultRevision: "",
  searchResultState: "idle",
  searchResultMatches: new Set(),
  searchResultDetails: new Map(),
  archive: "all",
  availability: "all",
  clientKind: "all",
  modelProvider: "all",
  sort: "recent",
  page: 0,
}};
const currentPayload = () => ({{}});
const renderSettingsModal = () => {{}};
const ctx = {{ lifecycle: {{ clearTimeout: () => {{}}, clearInterval: () => {{}} }} }};
const shared = {{}};
{factory}
const domain = createSessionCleanupDomain(ctx, shared);
sessionCleanupState.data = {{
  revision: "r1",
  sessions: Array.from({{ length: 65 }}, (_, index) => ({{
    id: `session-${{index + 1}}`,
    title: `Session ${{index + 1}}`,
    updatedAt: new Date(1700000000000 + index * 1000).toISOString(),
    status: "idle",
    archived: false,
    selectable: true,
    bytes: index,
    descendantCount: 0,
    clientKind: "app",
    modelProvider: "openai",
    workdirName: "project",
    workdirId: "project-id",
  }})),
}};
assert.equal(domain.sessionCleanupPageCount(65), 3);
assert.equal(domain.sessionCleanupPageRows().length, 30);
assert.equal(domain.sessionCleanupPageRows()[0].id, "session-65");
sessionCleanupState.page = 1;
assert.equal(domain.sessionCleanupPageRows().length, 30);
assert.equal(domain.sessionCleanupPageRows()[0].id, "session-35");
sessionCleanupState.page = 2;
assert.equal(domain.sessionCleanupPageRows().length, 5);
assert.equal(domain.sessionCleanupPageRows()[0].id, "session-5");
assert.equal(domain.moveSessionCleanupPage(-1), true);
assert.equal(sessionCleanupState.page, 1);
sessionCleanupState.page = 99;
assert.equal(domain.sessionCleanupPageRows().length, 5);
assert.equal(sessionCleanupState.page, 2);
console.log("session-cleanup-pagination-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "session-cleanup-pagination-ok" in completed.stdout


def test_session_index_reuses_the_empty_result_coverage_hint() -> None:
    """Index expansion has one inline entry point, not a duplicate dialog."""
    assert 'data-action="session-index-dialog"' not in SESSION_CLEANUP
    assert "openSessionIndexDialog" not in SESSION_CLEANUP
    assert 'data-action="session-index-dialog"' not in manifest.ROUTER
    assert "sessionIndexControlRequestId" in manifest.RENDERER_HUD_SCRIPT_TEMPLATE
    assert 'data-action="session-index-extend"' in SESSION_CLEANUP


def test_active_session_sequence_uses_canonical_identity_for_live_updates() -> None:
    active_session_factory = ACTIVE_SESSION.split(
        "  const activeSessionDomain = ctx.domains.register(",
        1,
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{}};
global.location = {{
  href: "app://-/index.html",
  pathname: "/",
  search: "",
  hash: "",
}};
const rootId = "codex-usage-hud-root";
const activeSessionCanonicalIdName = "__canonical";
const activeSessionCanonicalAtName = "__canonicalAt";
const activeSessionSettledTimerName = "__settled";
const activeSessionSelectionKeyName = "__selectionKey";
const activeSessionSelectionSeqName = "__selectionSeq";
const activeSessionAppliedSeqName = "__appliedSeq";
const activeSessionLastSignatureName = "__signature";
const activeSessionBindingName = "codexUsageHudActiveSession";
const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
const settingsBridgeUrl = () => "";
const sent = [];
const ctx = {{
  bindings: {{
    available: () => true,
    send: (_name, payload) => {{ sent.push(payload); return true; }},
  }},
  lifecycle: {{
    clearTimeout: () => {{}},
    timeout: () => 1,
    active: () => true,
  }},
}};
const shared = {{}};
{active_session_factory}
const domain = createActiveSessionDomain(ctx, shared);

function postAndAck(ref, reason = "event") {{
  const before = sent.length;
  domain.postActiveSession(reason, ref);
  assert.equal(sent.length, before + 1);
  const payload = sent[sent.length - 1];
  window[activeSessionAppliedSeqName] = payload.selectionSeq;
  return payload;
}}

const first = postAndAck({{
  sessionId: "thread-a",
  rendererSessionId: "local:thread-a",
  title: "First title",
  url: location.href,
  matchedBy: "sidebar-row",
}}, "click");
assert.equal(first.selectionSeq, 1);

const titleUpdate = postAndAck({{
  sessionId: "thread-a",
  rendererSessionId: "local:thread-a",
  title: "Updated title",
  url: location.href,
  matchedBy: "sidebar-row",
}});
assert.equal(titleUpdate.selectionSeq, 1);
assert.equal(titleUpdate.title, "Updated title");

const rawIdUpdate = postAndAck({{
  sessionId: "thread-a",
  rendererSessionId: "thread-a",
  title: "Updated title",
  url: location.href,
  matchedBy: "sidebar-row",
}});
assert.equal(rawIdUpdate.selectionSeq, 1);

const differentThread = postAndAck({{
  sessionId: "thread-b",
  rendererSessionId: "local:thread-b",
  title: "Other thread",
  url: location.href,
  matchedBy: "sidebar-row",
}});
assert.equal(differentThread.selectionSeq, 2);

const provisional = postAndAck({{
  sessionId: "",
  rendererSessionId: "local:client-new-thread:pending-a",
  title: "Pending thread",
  url: location.href,
  pendingSession: true,
  matchedBy: "sidebar-row",
}}, "click");
assert.equal(provisional.selectionSeq, 3);

const provisionalTitleUpdate = postAndAck({{
  sessionId: "",
  rendererSessionId: "local:client-new-thread:pending-a",
  title: "Pending thread renamed",
  url: location.href,
  pendingSession: true,
  matchedBy: "sidebar-row",
}});
assert.equal(provisionalTitleUpdate.selectionSeq, 4);

const titleOnly = postAndAck({{
  sessionId: "",
  rendererSessionId: "",
  title: "Header-only thread",
  url: location.href,
  matchedBy: "header-title",
}}, "click");
assert.equal(titleOnly.selectionSeq, 5);

const titleOnlyUpdate = postAndAck({{
  sessionId: "",
  rendererSessionId: "",
  title: "Header-only thread updated",
  url: location.href,
  matchedBy: "header-title",
}});
assert.equal(titleOnlyUpdate.selectionSeq, 6);

const newSession = postAndAck({{
  sessionId: "",
  rendererSessionId: "",
  title: "",
  url: location.href,
  newSession: true,
}}, "click");
assert.equal(newSession.selectionSeq, 7);

const nextCanonicalThread = postAndAck({{
  sessionId: "thread-c",
  rendererSessionId: "local:thread-c",
  title: "Canonical thread",
  url: location.href,
  matchedBy: "sidebar-row",
}});
assert.equal(nextCanonicalThread.selectionSeq, 8);
console.log("active-session-canonical-identity-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "active-session-canonical-identity-ok" in completed.stdout


def test_background_usage_range_change_bypasses_same_revision_cache() -> None:
    background_usage_factory = BACKGROUND_USAGE.split(
        "  const backgroundUsageDomain = ctx.domains.register(", 1
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{}};
global.document = {{ getElementById: () => null }};
global.location = {{ href: "app://-/index.html" }};
const settingsCommandBindingName = "codexUsageHudSettings";
const settingsModalId = "codex-usage-hud-settings";
let settingsActiveTab = "backgroundUsage";
const currentPayload = () => ({{
  backgroundUsageRevision: 7,
  backgroundUsageBridgeUrl: "",
}});
let backgroundUsageFetchSeq = 0;
let backgroundUsageDetailSeq = 0;
let backgroundUsageQueryTimeoutId = 0;
let backgroundUsageDetailTimeoutId = 0;
const backgroundUsageRequestTimeoutMs = 5000;
const backgroundUsageBodyScrollTops = new Map();
const backgroundUsageHistoryScrollTops = new Map();
const backgroundUsageDetailScrollTops = new Map();
const backgroundUsageState = {{
  range: "7d",
  feature: "",
  model: "",
  selectedEventId: "",
  selectedSessionId: "",
  data: {{ summary: {{}} }},
  detail: null,
  loading: false,
  detailLoading: false,
  error: "",
  loadedRevision: 7,
  loadedFilterKey: JSON.stringify(["today", "", ""]),
  promptExpanded: false,
  queryRequestId: "",
  queryFilterKey: "",
  detailRequestId: "",
}};
const sent = [];
const ctx = {{
  bindings: {{
    available: () => true,
    send: (_name, payload) => {{ sent.push(payload); return true; }},
  }},
  lifecycle: {{
    active: () => true,
    clearTimeout: () => {{}},
    timeout: () => 1,
    frame: () => 1,
  }},
  teardown: {{ add: () => () => {{}} }},
}};
const shared = {{}};
{background_usage_factory}
const domain = createBackgroundUsageDomain(ctx, shared);

(async () => {{
  await domain.loadBackgroundUsage();
  assert.equal(sent.length, 1);
  assert.equal(backgroundUsageState.loading, true);
  assert.equal(
    backgroundUsageState.queryFilterKey,
    JSON.stringify(["7d", "", ""]),
  );
  assert.equal(sent[0].action, "backgroundUsageQuery");
  assert.deepEqual(sent[0].filters, {{
    range: "7d",
    feature: "",
    model: "",
    eventId: "",
  }});
  console.log("background-usage-filter-cache-ok");
}})().catch((error) => {{
  console.error(error.stack || error);
  process.exitCode = 1;
}});
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "background-usage-filter-cache-ok" in completed.stdout


def test_background_usage_policy_action_is_exported_and_sends_command() -> None:
    background_usage_factory = BACKGROUND_USAGE.split(
        "  const backgroundUsageDomain = ctx.domains.register(", 1
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{ location: {{ href: "app://-/index.html" }} }};
global.document = {{ getElementById: () => null }};
global.location = window.location;
const settingsCommandBindingName = "codexUsageHudSettingsCommand";
const settingsModalId = "codex-usage-hud-settings";
const backgroundUsageRequestTimeoutMs = 5000;
const backgroundUsageState = {{
  policy: {{ desiredState: "enabled", policyRevision: 3 }},
  policyPending: false,
  policyError: "",
  detail: {{ featureKey: "memory_consolidation", eventId: "event-1" }},
}};
const sent = [];
const ctx = {{
  bindings: {{
    available: () => true,
    send: (name, payload) => sent.push({{ name, payload }}),
  }},
  lifecycle: {{ active: () => true, clearTimeout: () => {{}}, timeout: () => 1 }},
}};
const shared = {{}};
{background_usage_factory}
const domain = createBackgroundUsageDomain(ctx, shared);
assert.equal(typeof domain.backgroundUsagePolicyConfirm, "function");
assert.equal(typeof domain.applyBackgroundUsagePolicy, "function");
domain.applyBackgroundUsagePolicy(
  "memory_consolidation", "event-1", "disabled",
);
assert.equal(sent.length, 1);
assert.equal(sent[0].name, settingsCommandBindingName);
assert.equal(sent[0].payload.action, "backgroundUsagePolicySet");
assert.equal(sent[0].payload.featureKey, "memory_consolidation");
assert.equal(sent[0].payload.desiredState, "disabled");
assert.equal(sent[0].payload.expectedPolicyRevision, 3);
assert.ok(backgroundUsageState.policyRequestId);
console.log("background-usage-policy-command-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "background-usage-policy-command-ok" in completed.stdout


def test_background_usage_policy_target_and_copy_keep_enable_direction() -> None:
    background_usage_factory = BACKGROUND_USAGE.split(
        "  const backgroundUsageDomain = ctx.domains.register(", 1
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{ location: {{ href: "app://-/index.html" }} }};
global.document = {{ getElementById: () => null }};
global.location = window.location;
const settingsCommandBindingName = "codexUsageHudSettingsCommand";
const settingsModalId = "codex-usage-hud-settings";
const backgroundUsageState = {{ policy: null, policyPending: false, policyError: "" }};
const ctx = {{
  bindings: {{ available: () => false, send: () => true }},
  lifecycle: {{ active: () => true }},
}};
const shared = {{}};
{background_usage_factory}
const domain = createBackgroundUsageDomain(ctx, shared);

const enablePolicy = {{
  desiredState: "enabled",
  effectiveState: "disabled",
  verificationState: "requires_user_action",
}};
assert.equal(domain.backgroundUsagePolicyTargetState(enablePolicy), "enabled");
assert.equal(
  domain.backgroundUsagePolicyCopy(
    {{ featureKey: "memory_consolidation", featureLabel: "记忆整理" }},
    domain.backgroundUsagePolicyTargetState(enablePolicy),
  ).title,
  "启用“记忆整理”",
);

const disablePolicy = {{
  desiredState: "disabled",
  effectiveState: "enabled",
  verificationState: "requires_user_action",
}};
assert.equal(domain.backgroundUsagePolicyTargetState(disablePolicy), "disabled");
assert.equal(
  domain.backgroundUsagePolicyCopy(
    {{ featureKey: "memory_consolidation", featureLabel: "记忆整理" }},
    domain.backgroundUsagePolicyTargetState(disablePolicy),
).title,
  "禁用“记忆整理”",
);

assert.equal(
  domain.backgroundUsagePolicyTargetState({{
    desiredState: "disabled",
    effectiveState: "enabled",
    verificationState: "failed",
  }}),
  "disabled",
);
assert.equal(
  domain.backgroundUsagePolicyTargetState({{
    desiredState: "enabled",
    effectiveState: "disabled",
    verificationState: "failed",
  }}),
  "enabled",
);
console.log("background-usage-policy-direction-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "background-usage-policy-direction-ok" in completed.stdout


def test_background_usage_detail_head_shows_official_en_name_and_purpose() -> None:
    background_usage_factory = BACKGROUND_USAGE.split(
        "  const backgroundUsageDomain = ctx.domains.register(", 1
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{ location: {{ href: "app://-/index.html" }} }};
global.document = {{ getElementById: () => null }};
global.location = window.location;
function escapeHtml(value) {{
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }}[char]));
}}
function humanizeTokens(value) {{
  const n = Math.max(0, Math.round(Number(value) || 0));
  if (n >= 1000000) return `${{(n / 1000000).toFixed(1)}}M`;
  if (n >= 1000) return `${{(n / 1000).toFixed(1)}}k`;
  return String(n);
}}
const settingsCommandBindingName = "codexUsageHudSettingsCommand";
const settingsModalId = "codex-usage-hud-settings";
const backgroundUsageState = {{
  detailLoading: false,
  detail: {{
    featureKey: "memory_consolidation",
    featureLabel: "记忆整理",
    featureEnLabel: "Memory consolidation",
    featurePurpose: "在启动时提取记忆并落盘整合，让 ChatGPT 把过往对话中有用的上下文带入未来的工作",
    eventId: "event-1",
    firstSeenAt: "2025-01-01T00:00:00+08:00",
    lastSeenAt: "2025-01-01T00:00:00+08:00",
    models: ["gpt-5.4"],
    requestCount: 1,
    processUuid: "",
    threadId: "",
    prompt: "",
    requests: [],
  }},
  promptExpanded: false,
  policy: null,
  policyLoading: false,
  policyError: "",
  policyPending: false,
}};
const ctx = {{
  bindings: {{ available: () => false, send: () => true }},
  lifecycle: {{ active: () => true }},
}};
const shared = {{}};
{background_usage_factory}
const domain = createBackgroundUsageDomain(ctx, shared);
const html = domain.backgroundUsageDetailHtml(backgroundUsageState.detail);
assert.match(html, /记忆整理（Memory consolidation）/);
assert.match(html, /由 Codex App 官方 agent 工具在后台发起的请求/);
assert.match(html, /官方作用：在启动时提取记忆/);
assert.ok(!html.includes("Codex App 后台用量 · 本地记录"), html);
const noEnHtml = domain.backgroundUsageDetailHtml({{
  ...backgroundUsageState.detail,
  featureEnLabel: "",
  featurePurpose: "",
}});
assert.match(noEnHtml, /<h3>记忆整理<\\/h3>/);
assert.ok(!noEnHtml.includes("（Memory consolidation）"), noEnHtml);
console.log("background-usage-detail-head-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "background-usage-detail-head-ok" in completed.stdout


def test_background_usage_policy_is_immediate_with_restart_notice() -> None:
    assert "backgroundUsagePolicyNotice" in BACKGROUND_USAGE
    assert "Memories 已禁用" in BACKGROUND_USAGE
    assert "Memories 已启用" in BACKGROUND_USAGE
    assert ">立即重启</button>" not in BACKGROUND_USAGE
    assert "部分 Codex 版本可能需要重启" in BACKGROUND_USAGE
    assert 'data-event-id="${escapeHtml(String(detail?.eventId || ""))}"' in BACKGROUND_USAGE
    assert 'data-event-id="${escapeHtml(String(policy?.desiredState || "disabled"))}"' not in BACKGROUND_USAGE
    assert 'disabled aria-disabled="true"' not in BACKGROUND_USAGE
    assert 'verification === "verified"' in BACKGROUND_USAGE
    assert '["verified", "configured_unverified"].includes(verification)' not in BACKGROUND_USAGE
    assert 'kind === "policyApply" && !responseError' in BACKGROUND_USAGE


def test_settings_modal_preserves_secondary_layers_during_same_tab_refresh() -> None:
    assert "preserveSecondaryLayers" in SETTINGS_SHELL
    assert ".codex-usage-hud-settings-dialog > .codex-usage-hud-settings-confirm-layer" in SETTINGS_SHELL
    assert ".codex-usage-hud-settings-dialog > .codex-usage-hud-codex-cli-layer" in SETTINGS_SHELL
    assert "nextDialog.appendChild(layer)" in SETTINGS_SHELL


def test_router_is_registry_first_and_no_continuous_legacy_asset_remains() -> None:
    assert manifest.ASSET_ORDER[-1] == "15_router"
    assert not Path(manifest.__file__).with_name("legacy.py").exists()
    for index in range(2, 9):
        assert not Path(manifest.__file__).with_name(
            f"fragment_{index:02d}.py"
        ).exists()
    assert not Path(manifest.__file__).with_name("shared_head.py").exists()
    router = manifest.ROUTER
    for name in (
        "usageInsightsDomain",
        "sessionCleanupDomain",
        "backgroundUsageDomain",
        "settingsShellDomain",
        "layoutDomain",
        "composerDomain",
        "activeSessionDomain",
    ):
        assert f"{name}.install();" in router
    assert "settingsShellDomain.apply(root" in router
    assert "backgroundUsageDomain.apply(root" in router
    assert "usageInsightsDomain.apply(root" in router
    assert "sessionCleanupDomain.apply(root" in router

def test_payload_router_keeps_the_frozen_domain_order() -> None:
    script = manifest.RENDERER_HUD_SCRIPT_TEMPLATE
    router = script.split("function applyPayloadDomains", 1)[1].split(
        "window.__codexUsageHudUpdate",
        1,
    )[0]
    expected = (
        'if ("currentSession" in domains)',
        'if ("sessionSwitch" in domains)',
        'if ("budget" in domains)',
        'if ("settings" in domains)',
        'if ("overlay" in domains)',
        'if ("backgroundUsage" in domains)',
        'if ("diagnostics" in domains)',
        'if ("usageInsights" in domains)',
        'if ("sessionCleanup" in domains)',
    )
    positions = [router.index(marker) for marker in expected]
    assert positions == sorted(positions)
    assert "sessionViewDomain.apply" in router
    assert "budgetDomain.apply" in router
    assert "diagnosticsDomain.apply" in router


def test_leaf_domain_registry_contract_executes_in_node() -> None:
    domain_sources = "\n".join(source for _, _, source in DOMAIN_SOURCES)
    expected_names = [name for name, _, _ in DOMAIN_SOURCES]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{}};
const stateName = "__state";
const normalize = (value) => String(value || "").trim();
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const px = (value) => `${{Math.round(value)}}px`;
const visible = () => true;
const cssEscape = String;
const codexModelPickerCatalog = [];
{KERNEL}
{SHARED}
{domain_sources}
assert.deepEqual(ctx.domains.names(), {expected_names!r});
for (const name of ctx.domains.names()) {{
  const domain = ctx.domains.get(name);
  assert.equal(typeof domain.install, "function");
  assert.equal(typeof domain.apply, "function");
  assert.equal(typeof domain.dispose, "function");
}}
console.log("renderer-leaf-domains-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "renderer-leaf-domains-ok" in completed.stdout


def test_session_view_activity_scroll_scopes_to_turn_and_reacquires_virtualized_nodes() -> None:
    session_view_factory = SESSION_VIEW.split(
        "  const sessionViewDomain = ctx.domains.register(",
        1,
    )[0]
    script = """
const assert = require("node:assert/strict");
const rootId = "codex-usage-hud-root";
const warningClass = "warning";
const errorClass = "error";
const runningTimerName = "__running";
const payload = {
  rendererSessionId: "session-test",
  topDetails: {
    activityTaskIndex: 2,
    activityTasks: [
      { index: 1, count: 2, turnId: "target-turn", currentTask: "目标需求" },
      { index: 2, count: 2, turnId: "other-turn", currentTask: "当前需求" },
    ],
  },
};
const events = [];
const timelineOffsets = [];
let mountTarget = false;
let targetMounted = false;
const timers = new Set();
const root = {
  dataset: { activityTaskIndex: "2" },
  isConnected: true,
  contains: () => false,
  querySelector: (selector) => {
    if (selector === '[data-field="topActivityTrail"]') return activityList;
    if (selector === '[data-field="topActivityLoadMore"]') return loadMore;
    return null;
  },
  querySelectorAll: () => [],
};
const activityList = { dataset: { visibleCount: "4" }, scrollTop: 0, clientHeight: 100 };
const loadMore = { dataset: { pageSize: "12" } };
const timeline = {
  scrollHeight: 1000,
  clientHeight: 100,
  _scrollTop: 0,
  get scrollTop() { return this._scrollTop; },
  set scrollTop(value) { this._scrollTop = Number(value); timelineOffsets.push(this._scrollTop); },
  addEventListener() {},
  removeEventListener() {},
};
const makeNode = ({ key = "", turn = null, text = "", user = false, outer = false }) => {
  const node = {
    dataset: {},
    isConnected: true,
    innerText: text,
    textContent: text,
    getAttribute(name) {
      if (name === "data-content-search-unit-key") return key;
      if (name === "data-content-search-turn-key") return turn?.turnId || "";
      if (name === "data-turn-key") return outer ? turn?.outerKey || "" : "";
      return null;
    },
    matches(selector) {
      if (selector.includes("data-local-conversation-user-anchor")) return user;
      if (selector.includes("data-content-search-unit-key")) return !!key;
      return false;
    },
    querySelector(selector) {
      return selector.includes("data-local-conversation-user-anchor") && user ? {} : null;
    },
    querySelectorAll(selector) {
      if (selector.includes("data-content-search-unit-key")) return turn?.units || [];
      return [];
    },
    closest(selector) {
      if (selector.includes("data-content-search-unit-key") && key) return node;
      if (selector.includes("data-content-search-turn-key")) return turn;
      if (selector.includes("data-turn-key")) return turn?.outer || null;
      return null;
    },
    scrollIntoView(options = {}) { events.push({ kind: "scroll", node, behavior: options.behavior || "auto" }); },
    animate() { events.push({ kind: "animate", node }); return { cancel() {} }; },
    getBoundingClientRect() { return { top: user ? 10 : 40, bottom: user ? 30 : 80, height: user ? 20 : 40 }; },
  };
  if (key) node.dataset.contentSearchUnitKey = key;
  return node;
};
const otherTurn = { turnId: "other-turn", outerKey: "outer-other", units: [] };
otherTurn.outer = makeNode({ turn: otherTurn, outer: true, text: "另一个 Req" });
const otherUser = makeNode({
  key: "other-turn:user",
  turn: otherTurn,
  user: true,
  text: "你说：目标输入精确内容 ABCDE",
});
const otherOutput = makeNode({
  key: "other-turn:output",
  turn: otherTurn,
  text: "目标输出精确内容 ABCDE",
});
otherTurn.units = [otherUser, otherOutput];
const targetTurn = { turnId: "target-turn", outerKey: "outer-target", units: [] };
targetTurn.outer = makeNode({ turn: targetTurn, outer: true, text: "目标 Req" });
const targetUser = makeNode({
  key: "target-turn:user",
  turn: targetTurn,
  user: true,
  text: "你说：目标输入精确内容 ABCDE",
});
const targetOutput = makeNode({
  key: "target-turn:output",
  turn: targetTurn,
  text: "目标输出精确内容 ABCDE",
});
targetTurn.units = [targetUser, targetOutput];
for (const turn of [otherTurn, targetTurn]) {
  turn.isConnected = true;
  turn.innerText = turn.units.map((unit) => unit.textContent).join(" ");
  turn.textContent = turn.innerText;
  turn.getAttribute = (name) => (
    name === "data-content-search-turn-key" ? turn.turnId
      : (name === "data-turn-key" ? turn.outerKey : null)
  );
  turn.querySelectorAll = (selector) => (
    selector.includes("data-content-search-unit-key") ? turn.units : []
  );
  turn.closest = (selector) => selector.includes("data-turn-key") ? turn.outer : null;
}
const document = {
  body: {},
  documentElement: {},
  getElementById: (id) => id === rootId ? root : null,
  querySelector(selector) {
    if (selector === "[data-app-action-timeline-scroll]") return timeline;
    return null;
  },
  querySelectorAll(selector) {
    const turns = targetMounted ? [otherTurn, targetTurn] : [otherTurn];
    const units = turns.flatMap((turn) => turn.units);
    if (selector === "[data-content-search-turn-key]") return turns;
    if (selector === "[data-content-search-unit-key]") return units;
    if (selector === "[data-turn-key]") return turns.map((turn) => turn.outer);
    if (selector.includes("data-content-search-turn-key")) return turns;
    return [];
  },
};
global.window = {
  requestAnimationFrame(callback) {
    if (mountTarget && !targetMounted) targetMounted = true;
    callback(0);
    return 0;
  },
  cancelAnimationFrame() {},
  setTimeout,
  clearTimeout,
};
global.document = document;
global.currentPayload = () => payload;
global.clamp = (value, min, max) => Math.max(min, Math.min(max, value));
global.budgetDomain = { refreshProgressRailLabel() {}, refreshCollapsedProgressStrip() {} };
global.diagnosticsDomain = { applyConnectionHealth() {} };
const ctx = {
  lifecycle: {
    active: () => true,
    frame: (_owner, callback) => window.requestAnimationFrame(callback),
    clearFrame() {},
    timeout: (_owner, callback) => { const id = { callback }; timers.add(id); return id; },
    clearTimeout: (id) => timers.delete(id),
    interval: () => 0,
    clearInterval() {},
  },
};
const shared = {};
""" + session_view_factory + r"""
const domain = createSessionViewDomain(ctx, shared);

(async () => {
// A same-text user/output pair in another Req must not be selected while the
// requested stable turn is still virtualized away.
timeline.scrollHeight = 100;
let result = await domain.scrollToActivityRound(
  "输出：\n目标输出精确内容 ABCDE",
  "目标需求",
  3,
  "target-turn",
);
assert.equal(result, false);
assert.equal(events.length, 0);
assert.equal(timelineOffsets.length, 0);

// Once the reverse timeline materializes the target, locate the user anchor,
// then the exact output inside that same Req.  The other Req remains untouched.
events.length = 0;
timelineOffsets.length = 0;
timeline.scrollHeight = 1000;
mountTarget = true;
root.dataset.activityTaskIndex = "1";
result = await domain.scrollToActivityRound(
  "输出：\n目标输出精确内容 ABCDE",
  "目标需求",
  3,
  "target-turn",
);
assert.equal(result, true, JSON.stringify({ result, events, timelineOffsets }));
assert.ok(timelineOffsets.some((value) => value < 0), JSON.stringify(timelineOffsets));
assert.deepEqual(
  events.filter((event) => event.kind === "scroll").map((event) => [event.node, event.behavior]),
  [[targetUser, "auto"], [targetOutput, "smooth"]],
);
assert.ok(events.some((event) => event.kind === "animate" && event.node === targetOutput));
assert.equal(targetOutput.dataset.codexHudLocateRound, "3");
assert.equal(otherOutput.dataset.codexHudLocateRound, undefined);
console.log("session-view-activity-scroll-ok");
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "session-view-activity-scroll-ok" in completed.stdout


def test_session_view_activity_scroll_expands_collapsed_round_output() -> None:
    session_view_factory = SESSION_VIEW.split(
        "  const sessionViewDomain = ctx.domains.register(",
        1,
    )[0]
    script = """
const assert = require("node:assert/strict");
const rootId = "codex-usage-hud-root";
const warningClass = "warning";
const errorClass = "error";
const runningTimerName = "__running";
const payload = {
  rendererSessionId: "session-test",
  topDetails: {
    activityTaskIndex: 1,
    activityTasks: [{ index: 1, count: 1, turnId: "t1", currentTask: "目标需求" }],
  },
};
const events = [];
const timeline = { scrollHeight: 1000, clientHeight: 100, scrollTop: 0 };
const root = { dataset: { activityTaskIndex: "1" }, contains: () => false };

const makeNode = ({ key = "", user = false, text = "", hidden = false }) => {
  let revealed = !hidden;
  const node = {
    dataset: {},
    isConnected: true,
    innerText: hidden ? "" : text,
    textContent: text,
    parentElement: null,
    reveal() { revealed = true; },
    getAttribute(name) {
      if (name === "data-content-search-unit-key") return key;
      return null;
    },
    matches(selector) {
      if (selector.includes("data-local-conversation-user-anchor")) return user;
      if (selector.includes("data-content-search-unit-key")) return !!key;
      return false;
    },
    querySelector(selector) {
      return selector.includes("data-local-conversation-user-anchor") && user ? {} : null;
    },
    querySelectorAll() { return []; },
    closest(selector) {
      if (selector.includes("data-content-search-unit-key") && key) return node;
      return null;
    },
    scrollIntoView(options = {}) { events.push({ kind: "scroll", node, behavior: options.behavior || "auto" }); },
    animate() { events.push({ kind: "animate", node }); return { cancel() {} }; },
    // Codex hides mounted collapsed content in a height:0 box that still
    // reports a client rect, so visibility must hinge on width/height.
    getBoundingClientRect() { return { top: 0, bottom: 10, height: revealed ? 10 : 0, width: revealed ? 100 : 0 }; },
    getClientRects() { return [{}]; },
  };
  return node;
};
const makeToggle = (label, { aria = "false", mounted = null, onExpand = null } = {}) => ({
  dataset: {},
  isConnected: true,
  innerText: label,
  textContent: label,
  parentElement: null,
  ariaExpanded: aria,
  clicked: false,
  mounted,
  onExpand,
  getAttribute(name) { return name === "aria-expanded" ? this.ariaExpanded : null; },
  click() {
    this.clicked = true;
    this.ariaExpanded = "true";
    if (this.mounted) turn.units.push(this.mounted);
    if (this.onExpand) this.onExpand();
  },
  scrollIntoView(options = {}) { events.push({ kind: "scroll", node: this, behavior: options.behavior || "auto" }); },
  animate() { events.push({ kind: "animate", node: this }); return { cancel() {} }; },
  getBoundingClientRect() { return { top: 0, bottom: 10, height: 10, width: 100 }; },
  getClientRects() { return [{}]; },
});
const turn = {
  dataset: {},
  isConnected: true,
  innerText: "",
  textContent: "",
  units: [],
  buttons: [],
  getAttribute(name) {
    if (name === "data-content-search-turn-key") return "t1";
    return null;
  },
  querySelectorAll(selector) {
    if (selector.includes("data-content-search-unit-key")) return turn.units;
    if (selector === "button, [role='button']") return turn.buttons;
    return [];
  },
  closest() { return null; },
};
const document = {
  body: {},
  documentElement: {},
  getElementById: (id) => (id === rootId ? root : null),
  querySelector(selector) {
    return selector === "[data-app-action-timeline-scroll]" ? timeline : null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-content-search-turn-key]") return [turn];
    if (selector === "[data-content-search-unit-key]") return turn.units;
    if (selector === "[data-turn-key]") return [turn];
    return [];
  },
};
global.window = {
  requestAnimationFrame(callback) { callback(0); return 0; },
  cancelAnimationFrame() {},
  setTimeout,
  clearTimeout,
};
global.document = document;
global.currentPayload = () => payload;
global.clamp = (value, min, max) => Math.max(min, Math.min(max, value));
global.budgetDomain = { refreshProgressRailLabel() {}, refreshCollapsedProgressStrip() {} };
global.diagnosticsDomain = { applyConnectionHealth() {} };
const ctx = {
  lifecycle: {
    active: () => true,
    frame: (_owner, callback) => window.requestAnimationFrame(callback),
    clearFrame() {},
    timeout: (_owner, callback, ms = 0) => setTimeout(callback, Math.min(Number(ms) || 0, 8)),
    clearTimeout: (id) => clearTimeout(id),
    interval: () => 0,
    clearInterval() {},
  },
};
const shared = {};
""" + session_view_factory + r"""
const domain = createSessionViewDomain(ctx, shared);

(async () => {
// A needle that matches content Codex keeps mounted but collapsed (height:0)
// must first expand the disclosure, then pulse the now-visible content.
const user = makeNode({ key: "t1:user", user: true, text: "你说：目标需求详细内容" });
const hiddenOutput = makeNode({ key: "t1:round-1", hidden: true, text: "折叠输出精确内容 ABCDEFGH" });
const revealToggle = makeToggle("已处理 3m 45s", { onExpand: () => hiddenOutput.reveal() });
turn.units = [user, hiddenOutput];
turn.buttons = [revealToggle];
events.length = 0;
let result = await domain.scrollToActivityRound(
  "输出：\n折叠输出精确内容 ABCDEFGH",
  "目标需求",
  5,
  "t1",
);
assert.equal(result, true, JSON.stringify({ result, events }));
assert.equal(revealToggle.clicked, true, "精确命中落在折叠隐藏内容时应展开折叠区");
assert.deepEqual(
  events.filter((event) => event.kind === "scroll").map((event) => [event.node, event.behavior]),
  [[user, "auto"], [hiddenOutput, "smooth"]],
  JSON.stringify(events),
);
assert.ok(events.some((event) => event.kind === "animate" && event.node === hiddenOutput));
assert.equal(hiddenOutput.dataset.codexHudLocateRound, "5");

// A collapsed round output that is not mounted until the disclosure expands:
// the locate flow clicks the toggle, waits for the mount, then pulses the
// mounted content itself.
events.length = 0;
const mounted = makeNode({ key: "t1:round-2", text: "展开后输出精确内容 XYZ12345" });
const expandToggle = makeToggle("已处理 3m 45s");
expandToggle.mounted = mounted;
turn.units = [user];
turn.buttons = [expandToggle];
result = await domain.scrollToActivityRound(
  "输出：\n展开后输出精确内容 XYZ12345",
  "目标需求",
  6,
  "t1",
);
assert.equal(result, true, JSON.stringify({ result, events }));
assert.equal(expandToggle.clicked, true, "未命中时应点击折叠开关展开内容");
assert.equal(expandToggle.ariaExpanded, "true");
assert.ok(events.some((event) => event.kind === "animate" && event.node === mounted));
assert.ok(events.some(
  (event) => event.kind === "scroll" && event.node === mounted && event.behavior === "smooth",
));

// When both a hidden search-index copy and the visible rendering contain the
// needle, the visible rendering must win the pulse.
events.length = 0;
const visibleOutput = makeNode({ key: "t1:round-3", text: "可见渲染输出精确内容 MMMMMMMM" });
const indexCopy = makeNode({ hidden: true, text: "前缀 可见渲染输出精确内容 MMMMMMMM 后缀" });
turn.units = [user, visibleOutput, indexCopy];
turn.buttons = [];
result = await domain.scrollToActivityRound(
  "输出：\n可见渲染输出精确内容 MMMMMMMM",
  "目标需求",
  7,
  "t1",
);
assert.equal(result, true, JSON.stringify({ result, events }));
assert.ok(events.some((event) => event.kind === "animate" && event.node === visibleOutput));
assert.ok(events.every((event) => event.kind !== "animate" || event.node === visibleOutput));

// A copyText needle that only exists in the hidden index copy must not win
// over a locateTexts needle that matches the visible paragraph.
events.length = 0;
const indexCopy2 = makeNode({ hidden: true, text: "推理原文全文只在隐藏索引里 NNNNNNNN" });
const visiblePara = makeNode({ key: "t1:round-4", text: "段落可见渲染输出 OOOOOOOO" });
turn.units = [user, indexCopy2, visiblePara];
turn.buttons = [];
result = await domain.scrollToActivityRound(
  "推理：\n推理原文全文只在隐藏索引里 NNNNNNNN",
  "目标需求",
  8,
  "t1",
  ["段落可见渲染输出 OOOOOOOO"],
);
assert.equal(result, true, JSON.stringify({ result, events }));
assert.ok(events.some((event) => event.kind === "animate" && event.node === visiblePara));
assert.ok(events.every((event) => event.kind !== "animate" || event.node === visiblePara));

// A needle that can never match (image-heavy output renders no matching text):
// image-preview buttons must never be clicked, and the pulse falls back to
// the round's own work-summary header by ordinal instead of the request.
events.length = 0;
const imageButton = makeToggle("查看图片大图");
const workHeader = makeToggle("已处理 1m 02s");
turn.units = [user];
turn.buttons = [imageButton, workHeader];
result = await domain.scrollToActivityRound(
  "输出：\n图示输出内容 QQQQQQQQ",
  "目标需求",
  1,
  "t1",
);
assert.equal(result, true, JSON.stringify({ result, events }));
assert.equal(imageButton.clicked, false, "图片预览等非工作摘要按钮绝不能被点击");
assert.equal(workHeader.clicked, true, "未命中时应展开工作摘要折叠区");
assert.ok(events.some((event) => event.kind === "animate" && event.node === workHeader));
assert.ok(events.some(
  (event) => event.kind === "scroll" && event.node === workHeader && event.behavior === "smooth",
));

// An already-expanded disclosure (chevron ∨, no aria-expanded) must not be
// clicked again: toggling it would fold the group shut.
events.length = 0;
const expandedHeader = makeToggle("已处理 2m 00s ∨", { aria: null });
turn.units = [user];
turn.buttons = [expandedHeader];
result = await domain.scrollToActivityRound(
  "输出：\n图示输出内容 QQQQQQQQ",
  "目标需求",
  1,
  "t1",
);
assert.equal(result, true, JSON.stringify({ result, events }));
assert.equal(expandedHeader.clicked, false, "∨ 展开态的折叠头不应被再次点击");
assert.ok(events.some((event) => event.kind === "animate" && event.node === expandedHeader));

// A collapsed disclosure this locator opened in an earlier pass keeps the
// codexHudLocateExpanded marker when no other state signal exists, so the
// next locate pulses it instead of folding it shut.
events.length = 0;
const markerHeader = makeToggle("已处理 4m 05s", { aria: null });
markerHeader.dataset.codexHudLocateExpanded = "true";
turn.units = [user];
turn.buttons = [markerHeader];
result = await domain.scrollToActivityRound(
  "输出：\n图示输出内容 QQQQQQQQ",
  "目标需求",
  1,
  "t1",
);
assert.equal(result, true, JSON.stringify({ result, events }));
assert.equal(markerHeader.clicked, false, "本定位器已展开且无状态信号的折叠头不应被再次点击");
assert.ok(events.some((event) => event.kind === "animate" && event.node === markerHeader));
console.log("session-view-activity-expand-ok");
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "session-view-activity-expand-ok" in completed.stdout


def test_session_view_activity_scroll_round_aware_selection() -> None:
    session_view_factory = SESSION_VIEW.split(
        "  const sessionViewDomain = ctx.domains.register(",
        1,
    )[0]
    script = """
const assert = require("node:assert/strict");
const rootId = "codex-usage-hud-root";
const warningClass = "warning";
const errorClass = "error";
const runningTimerName = "__running";
const payload = {
  rendererSessionId: "session-test",
  topDetails: {
    activityTaskIndex: 1,
    activityTasks: [{ index: 1, count: 1, turnId: "t1", currentTask: "测试需求" }],
  },
};
const events = [];
const timers = new Set();
const timeline = {
  scrollHeight: 2000,
  clientHeight: 300,
  _scrollTop: 0,
  get scrollTop() { return this._scrollTop; },
  set scrollTop(value) { this._scrollTop = Number(value); },
  getBoundingClientRect() { return { top: 0, bottom: 300, height: 300, width: 800 }; },
  addEventListener() {},
  removeEventListener() {},
};
const makeNode = ({ key = "", text = "", user = false, round = 0 }) => {
  const node = {
    dataset: {},
    isConnected: true,
    innerText: text,
    textContent: text,
    round,
    getAttribute(name) {
      if (name === "data-content-search-unit-key") return key;
      if (name === "data-content-search-turn-key") return "t1";
      return null;
    },
    matches(selector) {
      if (selector.includes("data-local-conversation-user-anchor")) return user;
      if (selector.includes("data-content-search-unit-key")) return !!key;
      return false;
    },
    querySelector(selector) {
      return selector.includes("data-local-conversation-user-anchor") && user ? {} : null;
    },
    querySelectorAll() { return []; },
    closest(selector) {
      if (selector.includes("data-content-search-unit-key") && key) return node;
      if (selector.includes("data-content-search-turn-key")) return turn;
      return null;
    },
    contains() { return false; },
    compareDocumentPosition(other) {
      const mine = Number(node.round || 0);
      const theirs = Number(other?.round || 0);
      if (mine === theirs) return 0;
      // node.compareDocumentPosition(other) describes `other` relative to node.
      return mine > theirs ? 2 : 4;
    },
    scrollIntoView(options = {}) { events.push({ kind: "scroll", node, behavior: options.behavior || "auto" }); },
    animate() { events.push({ kind: "animate", node }); return { cancel() {} }; },
    getBoundingClientRect() {
      return user
        ? { top: 10, bottom: 30, height: 20, width: 100 }
        : { top: 100, bottom: 140, height: 40, width: 100 };
    },
  };
  return node;
};
const makeToggle = (label, round) => ({
  dataset: {},
  isConnected: true,
  innerText: label,
  textContent: label,
  parentElement: null,
  round,
  getAttribute(name) { return name === "aria-expanded" ? "true" : null; },
  contains() { return false; },
  click() { this.clicked = true; },
  scrollIntoView(options = {}) { events.push({ kind: "scroll", node: this, behavior: options.behavior || "auto" }); },
  animate() { events.push({ kind: "animate", node: this }); return { cancel() {} }; },
  getBoundingClientRect() { return { top: 80, bottom: 100, height: 20, width: 100 }; },
});
const turn = {
  turnId: "t1",
  units: [],
  buttons: [],
  isConnected: true,
  innerText: "",
  textContent: "",
  getAttribute(name) { return name === "data-content-search-turn-key" ? "t1" : null; },
  querySelectorAll(selector) {
    if (selector.includes("data-content-search-unit-key")) return turn.units;
    if (selector === "button, [role='button']") return turn.buttons;
    return [];
  },
  closest() { return null; },
};
const user = makeNode({ key: "t1:user", user: true, text: "你说：测试需求详细内容" });
const roundOne = makeNode({ key: "t1:round-1", text: "重复输出内容 ABCDEFGH", round: 1 });
const roundTwo = makeNode({ key: "t1:round-2", text: "重复输出内容 ABCDEFGH", round: 2 });
turn.units = [user, roundOne, roundTwo];
turn.buttons = [
  makeToggle("已处理 1m 00s", 1),
  makeToggle("已处理 2m 00s", 2),
];
const root = { dataset: { activityTaskIndex: "1" }, contains: () => false, querySelectorAll: () => [] };
const document = {
  body: {},
  documentElement: {},
  getElementById: (id) => id === rootId ? root : null,
  querySelector(selector) {
    return selector === "[data-app-action-timeline-scroll]" ? timeline : null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-content-search-turn-key]") return [turn];
    if (selector === "[data-content-search-unit-key]") return turn.units;
    if (selector === "[data-turn-key]") return [];
    return [];
  },
};
global.window = {
  requestAnimationFrame(callback) { callback(0); return 0; },
  cancelAnimationFrame() {},
  setTimeout,
  clearTimeout,
};
global.document = document;
global.currentPayload = () => payload;
global.clamp = (value, min, max) => Math.max(min, Math.min(max, value));
global.budgetDomain = { refreshProgressRailLabel() {}, refreshCollapsedProgressStrip() {} };
global.diagnosticsDomain = { applyConnectionHealth() {} };
const ctx = {
  lifecycle: {
    active: () => true,
    frame: (_owner, callback) => window.requestAnimationFrame(callback),
    clearFrame() {},
    timeout: (_owner, callback, ms = 0) => setTimeout(callback, Math.min(Number(ms) || 0, 8)),
    clearTimeout: (id) => clearTimeout(id),
    interval: () => 0,
    clearInterval() {},
  },
};
const shared = {};
""" + session_view_factory + r"""
const domain = createSessionViewDomain(ctx, shared);

(async () => {
// The same output text appears in two rounds of the same task. The tightest
// text match alone would land on round 1; the requested round index must win.
events.length = 0;
let result = await domain.scrollToActivityRound(
  "输出：\n重复输出内容 ABCDEFGH",
  "测试需求",
  2,
  "t1",
);
assert.equal(result, true, JSON.stringify({ result, events }));
const animated = events.filter((event) => event.kind === "animate").map((event) => event.node);
assert.ok(animated.includes(roundTwo), JSON.stringify(events));
assert.ok(!animated.includes(roundOne), JSON.stringify(events));
assert.equal(roundTwo.dataset.codexHudLocateRound, "2");
assert.equal(roundOne.dataset.codexHudLocateRound, undefined);
// The precise scroll lands on round 2's output, never round 1's.
const roundScrolls = events
  .filter((event) => event.kind === "scroll" && (event.node === roundOne || event.node === roundTwo))
  .map((event) => event.node);
assert.deepEqual(roundScrolls, [roundTwo], JSON.stringify(events));
console.log("session-view-activity-round-aware-ok");
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "session-view-activity-round-aware-ok" in completed.stdout


def test_session_view_activity_scroll_corrects_viewport_after_virtualization() -> None:
    session_view_factory = SESSION_VIEW.split(
        "  const sessionViewDomain = ctx.domains.register(",
        1,
    )[0]
    script = """
const assert = require("node:assert/strict");
const rootId = "codex-usage-hud-root";
const warningClass = "warning";
const errorClass = "error";
const runningTimerName = "__running";
const payload = {
  rendererSessionId: "session-test",
  topDetails: {
    activityTaskIndex: 1,
    activityTasks: [{ index: 1, count: 1, turnId: "t1", currentTask: "测试需求" }],
  },
};
const events = [];
const timers = new Set();
const timeline = {
  scrollHeight: 2000,
  clientHeight: 300,
  _scrollTop: 0,
  get scrollTop() { return this._scrollTop; },
  set scrollTop(value) { this._scrollTop = Number(value); },
  getBoundingClientRect() { return { top: 0, bottom: 300, height: 300, width: 800 }; },
  addEventListener() {},
  removeEventListener() {},
};
const makeNode = ({ key = "", text = "", user = false, round = 0, drift = false }) => {
  const node = {
    dataset: {},
    isConnected: true,
    innerText: text,
    textContent: text,
    round,
    getAttribute(name) {
      if (name === "data-content-search-unit-key") return key;
      if (name === "data-content-search-turn-key") return "t1";
      return null;
    },
    matches(selector) {
      if (selector.includes("data-local-conversation-user-anchor")) return user;
      if (selector.includes("data-content-search-unit-key")) return !!key;
      return false;
    },
    querySelector(selector) {
      return selector.includes("data-local-conversation-user-anchor") && user ? {} : null;
    },
    querySelectorAll() { return []; },
    closest(selector) {
      if (selector.includes("data-content-search-unit-key") && key) return node;
      if (selector.includes("data-content-search-turn-key")) return turn;
      return null;
    },
    contains() { return false; },
    compareDocumentPosition(other) {
      const mine = Number(node.round || 0);
      const theirs = Number(other?.round || 0);
      if (mine === theirs) return 0;
      // node.compareDocumentPosition(other) describes `other` relative to node.
      return mine > theirs ? 2 : 4;
    },
    scrollIntoView(options = {}) {
      events.push({ kind: "scroll", node, behavior: options.behavior || "auto" });
      // Simulate virtualization re-mounting the turn elsewhere after the smooth
      // scroll: only an explicit auto scroll actually lands the node in view.
      if (options.behavior === "auto") node.viewportLanded = true;
    },
    animate() { events.push({ kind: "animate", node }); return { cancel() {} }; },
    getBoundingClientRect() {
      if (user) return { top: 10, bottom: 30, height: 20, width: 100 };
      if (!drift) return { top: 100, bottom: 140, height: 40, width: 100 };
      return node.viewportLanded
        ? { top: 100, bottom: 140, height: 40, width: 100 }
        : { top: -500, bottom: -460, height: 40, width: 100 };
    },
  };
  return node;
};
const makeToggle = (label, round) => ({
  dataset: {},
  isConnected: true,
  innerText: label,
  textContent: label,
  parentElement: null,
  round,
  getAttribute(name) { return name === "aria-expanded" ? "true" : null; },
  contains() { return false; },
  click() { this.clicked = true; },
  scrollIntoView(options = {}) { events.push({ kind: "scroll", node: this, behavior: options.behavior || "auto" }); },
  animate() { events.push({ kind: "animate", node: this }); return { cancel() {} }; },
  getBoundingClientRect() { return { top: 80, bottom: 100, height: 20, width: 100 }; },
});
const turn = {
  turnId: "t1",
  units: [],
  buttons: [],
  isConnected: true,
  innerText: "",
  textContent: "",
  getAttribute(name) { return name === "data-content-search-turn-key" ? "t1" : null; },
  querySelectorAll(selector) {
    if (selector.includes("data-content-search-unit-key")) return turn.units;
    if (selector === "button, [role='button']") return turn.buttons;
    return [];
  },
  closest() { return null; },
};
const user = makeNode({ key: "t1:user", user: true, text: "你说：测试需求详细内容" });
const roundOne = makeNode({ key: "t1:round-1", text: "唯一输出内容 XYZABC", round: 1, drift: true });
turn.units = [user, roundOne];
turn.buttons = [makeToggle("已处理 1m 00s", 1)];
const root = { dataset: { activityTaskIndex: "1" }, contains: () => false, querySelectorAll: () => [] };
const document = {
  body: {},
  documentElement: {},
  getElementById: (id) => id === rootId ? root : null,
  querySelector(selector) {
    return selector === "[data-app-action-timeline-scroll]" ? timeline : null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-content-search-turn-key]") return [turn];
    if (selector === "[data-content-search-unit-key]") return turn.units;
    if (selector === "[data-turn-key]") return [];
    return [];
  },
};
global.window = {
  requestAnimationFrame(callback) { callback(0); return 0; },
  cancelAnimationFrame() {},
  setTimeout,
  clearTimeout,
};
global.document = document;
global.currentPayload = () => payload;
global.clamp = (value, min, max) => Math.max(min, Math.min(max, value));
global.budgetDomain = { refreshProgressRailLabel() {}, refreshCollapsedProgressStrip() {} };
global.diagnosticsDomain = { applyConnectionHealth() {} };
const ctx = {
  lifecycle: {
    active: () => true,
    frame: (_owner, callback) => window.requestAnimationFrame(callback),
    clearFrame() {},
    timeout: (_owner, callback, ms = 0) => setTimeout(callback, Math.min(Number(ms) || 0, 8)),
    clearTimeout: (id) => clearTimeout(id),
    interval: () => 0,
    clearInterval() {},
  },
};
const shared = {};
""" + session_view_factory + r"""
const domain = createSessionViewDomain(ctx, shared);

(async () => {
// The smooth scroll alone leaves the round output out of the timeline viewport
// (virtualization re-mount shifted it). The locate flow must detect it and run
// one corrective auto scroll before pulsing.
events.length = 0;
let result = await domain.scrollToActivityRound(
  "输出：\n唯一输出内容 XYZABC",
  "测试需求",
  1,
  "t1",
);
assert.equal(result, true, JSON.stringify({ result, events }));
const roundScrolls = events
  .filter((event) => event.kind === "scroll" && event.node === roundOne)
  .map((event) => event.behavior);
assert.ok(roundScrolls.includes("smooth"), JSON.stringify(events));
assert.ok(roundScrolls.includes("auto"), JSON.stringify(events));
assert.equal(roundScrolls[roundScrolls.length - 1], "auto",
  "应在平滑滚动后补一次自动滚动把轮次拉回视口 " + JSON.stringify(events));
const scrollIndex = events.findIndex(
  (event) => event.kind === "scroll" && event.node === roundOne && event.behavior === "auto",
);
const animateIndex = events.findIndex(
  (event) => event.kind === "animate" && event.node === roundOne,
);
assert.ok(animateIndex > scrollIndex, JSON.stringify(events));
assert.equal(roundOne.dataset.codexHudLocateRound, "1");
console.log("session-view-activity-viewport-ok");
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "session-view-activity-viewport-ok" in completed.stdout


def test_leaf_bundle_keeps_one_iife_and_one_boot_placeholder() -> None:
    script = manifest.RENDERER_HUD_SCRIPT_TEMPLATE
    assert script.lstrip().startswith("(() => {")
    assert script.count('const version = "69";') == 1
    assert script.count("__CODEX_MODEL_PICKER_CATALOG__") == 1
    assert script.rstrip().endswith("})()")
