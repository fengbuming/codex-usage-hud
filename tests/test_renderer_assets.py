from __future__ import annotations

import hashlib

from codex_usage_hud.renderer_assets import manifest
from codex_usage_hud.renderer_assets import layout
from codex_usage_hud.renderer_assets.layout_anchors import TEXT as LAYOUT_ANCHORS
from codex_usage_hud.renderer_assets.layout_gestures import TEXT as LAYOUT_GESTURES
from codex_usage_hud.renderer_assets.layout_markup import TEXT as LAYOUT_MARKUP
from codex_usage_hud.renderer_assets.layout_observers import TEXT as LAYOUT_OBSERVERS
from codex_usage_hud.renderer_assets.layout_style import TEXT as LAYOUT_STYLE
from codex_usage_hud.renderer_assets.router import TEXT as ROUTER
from codex_usage_hud.renderer_assets.session_cleanup import TEXT as SESSION_CLEANUP
from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS_SHELL
from codex_usage_hud.renderer_assets.settings_support_panels import (
    TEXT as SETTINGS_SUPPORT_PANELS,
)
from codex_usage_hud.ui import renderer_script


def test_renderer_asset_manifest_is_ordered_and_byte_identical() -> None:
    assert manifest.P6_1_TEMPLATE_BYTE_LENGTH == 539347
    assert manifest.P6_1_TEMPLATE_SHA256 == (
        "be4417fa105f6809200bf626835f86a35e9b3b7b247f8fca4df84660ad2afbdf"
    )


def test_renderer_kernel_manifest_and_shared_contract_are_explicit() -> None:
    assert manifest.ASSET_ORDER[:10] == (
        "00_bootstrap",
        "00_kernel",
        "01_shared_head",
        "01_shared",
        "02_model_picker",
        "03_theme",
        "04_diagnostics",
        "05_budget",
        "06_rest_reminder",
        "07_session_view",
    )
    assert manifest.ASSET_ORDER == (
        "00_bootstrap",
        "00_kernel",
        "01_shared_head",
        "01_shared",
        "02_model_picker",
        "03_theme",
        "04_diagnostics",
        "05_budget",
        "06_rest_reminder",
        "07_session_view",
        "08_usage_insights",
        "09_session_cleanup",
        "10_background_usage",
        "11_settings_shell",
        "12_layout",
        "13_composer",
        "14_active_session",
        "15_router",
    )
    assert len(manifest.ASSETS) == 18
    assert all(source for _, source in manifest.ASSETS)
    assert renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE == manifest.RENDERER_HUD_SCRIPT_TEMPLATE

    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    encoded = script.encode("utf-8")
    actual_length = len(encoded)
    actual_hash = hashlib.sha256(encoded).hexdigest()
    assert actual_length == manifest.P6_7_TEMPLATE_BYTE_LENGTH, (
        "Renderer template contract is stale: "
        f"expected {manifest.P6_7_TEMPLATE_BYTE_LENGTH}, actual {actual_length}. "
        "Run `python tools/update_renderer_contract.py --update` after review."
    )
    assert actual_hash == manifest.P6_7_TEMPLATE_SHA256, (
        "Renderer template hash contract is stale: "
        f"expected {manifest.P6_7_TEMPLATE_SHA256}, actual {actual_hash}. "
        "Run `python tools/update_renderer_contract.py --update` after review."
    )
    assert script.count("function createRendererContext()") == 1
    assert script.count("const ctx = createRendererContext();") == 1
    assert "registeredDomains = new Map()" in script
    assert "for (let index = teardownEntries.length - 1; index >= 0" in script
    assert 'ctx.lifecycle.listen("layout", window, "resize"' in script
    assert "const previousState = ctx.state.read();" in script
    assert "ctx.state.write({" in script
    assert "ctx.teardown.run();" in script
    assert 'ctx.lifecycle.listen("model_picker", node' not in script
    assert "function createModelPickerDomain(ctx, shared)" in script
    assert 'ctx.domains.register(\n    "model_picker",' in script
    assert "return { install, apply, dispose };" in script
    assert "modelPickerDomain.install();" in script
    assert "modelPickerDomain.apply();" in script
    assert "node[syntheticSelect] = (event) =>" in script
    assert 'ctx.lifecycle.listen("model_picker", document' in script
    assert "removeSyntheticItems();" in script
    assert "const releaseAbort = ctx.teardown.add(" in script
    assert "if (!ctx.lifecycle.active()) return;" in script
    for domain in ("theme", "diagnostics", "budget", "rest_reminder", "session_view"):
        assert f'ctx.domains.register(\n    "{domain}",' in script
        assert f"function create{''.join(part.title() for part in domain.split('_'))}Domain(ctx, shared)" in script
    assert script.count("function install()") >= 6
    assert script.count("function apply(root, payload)") >= 4
    assert script.count("function dispose()") >= 6
    assert "themeDomain.apply(root, payload || {});" in script
    assert "budgetDomain.apply(root, { ...(payload || {}), ...(domains.budget || {}) });" in script
    assert "restReminderDomain.apply(root, payload || {});" in script
    assert "sessionViewDomain.apply(root" in script
    assert "diagnosticsDomain.apply(root" in script
    assert script.index('"model_picker"') < script.index('"theme"') < script.index('"diagnostics"') < script.index('"budget"') < script.index('"rest_reminder"') < script.index('"session_view"')
    for domain in ("usage_insights", "session_cleanup", "background_usage", "settings_shell"):
        assert f'ctx.domains.register(\n    "{domain}",' in script
    for domain in ("layout", "composer"):
        assert f'ctx.domains.register(\n    "{domain}",' in script
    assert 'ctx.domains.register(\n    "active_session",' in script


def test_session_cleanup_payload_keeps_revision_state_in_function_scope() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    start = script.index("function applySessionCleanupPayload")
    end = script.index("function install()", start)
    source = script[start:end]

    assert source.index("let data = sessionCleanupState.data;") < source.index(
        'if (incoming && typeof incoming === "object")'
    )
    assert source.index("const previousRevision") < source.index(
        'if (incoming && typeof incoming === "object")'
    )
    assert "const data = sessionCleanupPayloadWithInventory(incoming);" not in source


def test_session_index_event_chain_and_status_sync_are_wired() -> None:
    shell = SETTINGS_SHELL
    assert "function refreshSessionIndexIfStale" in shell
    assert "function detachSessionIndexUi" in shell
    assert "refreshSessionIndexIfStale," in shell
    assert "sessionIndexUiAttached" in shell
    assert "startSessionIndexProgressPolling" not in shell
    assert "sessionIndexProgressTimer" not in shell
    assert "sessionIndexControlRequestId" in shell
    assert 'const isControlResponse = Object.prototype.hasOwnProperty.call(sessionIndex, "accepted");' in shell

    cleanup = SESSION_CLEANUP
    assert "function sessionIndexScanMergeHtml" in cleanup
    assert "function sessionIndexExtendOptions" in cleanup
    assert "sessionIndexEstimateForRange" not in cleanup
    assert "item.estimate" not in cleanup
    assert "sessionIndexControlRequestId" in cleanup
    assert 'data-action="session-index-pause"' not in cleanup
    assert 'data-action="session-index-background"' not in cleanup
    assert "sessionIndexToggleHtml" in cleanup
    assert "session-index-toggle-track" in cleanup
    assert 'data-session-index-enabled="true"' in cleanup
    assert 'data-action="session-index-clear"' in cleanup
    assert "sessionIndexBuildOptions" in cleanup
    assert "diskBytes" in cleanup
    assert "const sessionIndex = sessionIndexDomainState();" in cleanup
    # A status response must replace the cached state before rebuilding the
    # visible panel.
    status_merge = cleanup.index("sessionCleanupState.sessionIndex = { ...statusIndex };")
    unlock = cleanup.index(
        "sessionCleanupState.sessionIndexRefreshing = false;",
        status_merge,
    )
    assert status_merge < unlock

    assert "requestSessionIndexEnabled" in ROUTER
    assert "requestSessionIndexClear" in ROUTER
    assert 'control: desired ? "enable" : "disable"' in cleanup


def test_session_index_extend_entry_matches_hint_container_and_copy() -> None:
    # regression: the extend button has one owner (the empty-result coverage
    # hint), and the router must read its select instead of silently falling
    # back to the current range (a no-op).
    assert (
        'action.closest(".codex-usage-hud-session-coverage-hint")' in ROUTER
    )
    hint = SESSION_CLEANUP.index("codex-usage-hud-session-coverage-hint")
    extend_select = SESSION_CLEANUP.index(
        'data-session-index-extend="true"',
        hint,
    )
    assert hint < extend_select
    # regression: range labels already carry 「最近」; templates must not
    # duplicate it (「最近 最近 1 个月」).
    assert "可搜索最近 ${rangeLabelText}" not in SESSION_CLEANUP
    assert "覆盖了最近 ${rangeLabel}" not in SESSION_CLEANUP
    assert "（${item.estimate}）" not in SESSION_CLEANUP


def test_session_index_button_matches_adjacent_control_height() -> None:
    start = LAYOUT_STYLE.index(".codex-usage-hud-session-index-toggle")
    end = LAYOUT_STYLE.index(".codex-usage-hud-session-index-toggle[aria-expanded", start)
    source = LAYOUT_STYLE[start:end]
    assert "height: 32px;" in source
    assert "min-height: 32px;" in source
    assert "min-height: 32px;" in LAYOUT_STYLE[LAYOUT_STYLE.index(".codex-usage-hud-session-date-trigger,"):]
    assert 'data-action="session-index-toggle"' in SESSION_CLEANUP
    assert 'aria-expanded="${expanded ? "true" : "false"}"' in SESSION_CLEANUP


def test_layout_static_fragments_join_without_changing_manifest_asset() -> None:
    fragments = (
        LAYOUT_STYLE,
        LAYOUT_MARKUP,
        LAYOUT_GESTURES,
        LAYOUT_ANCHORS,
        LAYOUT_OBSERVERS,
    )

    assert layout.TEXT == "".join(fragments)
    assert manifest.ASSETS[14] == ("12_layout", layout.TEXT)
    assert LAYOUT_STYLE.startswith("\n  function createLayoutDomain(ctx, shared) {")
    assert LAYOUT_STYLE.rstrip().endswith(
        "document.documentElement.appendChild(style);\n      }"
    )
    assert LAYOUT_MARKUP.startswith("\n      function resizeEdgesMarkup()")
    assert LAYOUT_GESTURES.startswith("\n      function beginGesture(")
    assert LAYOUT_ANCHORS.startswith("\n      function minWidthFor(")
    assert LAYOUT_OBSERVERS.startswith("\n      function syncPosition(")
    assert LAYOUT_OBSERVERS.rstrip().endswith("} = layoutDomain;")


def test_settings_support_panels_are_a_static_subdomain_fragment() -> None:
    assert "function supportPanelHtml(settings, path)" in SETTINGS_SUPPORT_PANELS
    assert "function aboutPanelHtml(path)" in SETTINGS_SUPPORT_PANELS
    assert "ctx.domains.register" not in SETTINGS_SUPPORT_PANELS
    assert "ctx.bindings" not in SETTINGS_SUPPORT_PANELS
    assert "ctx.lifecycle" not in SETTINGS_SUPPORT_PANELS
    assert SETTINGS_SHELL.count("function supportPanelHtml(settings, path)") == 1
    assert SETTINGS_SHELL.count("function aboutPanelHtml(path)") == 1
    assert SETTINGS_SHELL.index("function supportPanelHtml") < SETTINGS_SHELL.index(
        "function setSettingsStatus"
    )


def test_provider_settings_expose_session_copy_and_transfer_workflow() -> None:
    assert 'data-action="settings-transfer-provider"' in SETTINGS_SHELL
    assert "function openSessionTransferDialog" in SETTINGS_SHELL
    assert "function submitSessionTransfer" in SETTINGS_SHELL
    assert 'data-session-transfer-mode="copy"' in SETTINGS_SHELL
    assert 'data-session-transfer-mode="migrate"' in SETTINGS_SHELL
    assert 'action: "sessionTransfer"' in SETTINGS_SHELL
    assert "sessionTransferState.selectedIds" in SETTINGS_SHELL
    assert "codex-usage-hud-session-transfer-card" in LAYOUT_STYLE
    assert 'data-action="session-transfer-resume"' not in SETTINGS_SHELL
    assert "function resumeSessionTransferTarget" not in SETTINGS_SHELL
    assert "targetVisible" in SETTINGS_SHELL
    assert "targetResumable" in SETTINGS_SHELL
    assert "勾选“来自迁移”可选择对应工作目录" in SETTINGS_SHELL
    assert "refreshCodexDesktopSessionList" not in SETTINGS_SHELL
    assert "restartCodexDesktop" not in SETTINGS_SHELL
    assert "const sessionTransferPageSize = 50" in SETTINGS_SHELL
    assert 'data-action="session-transfer-page"' in SETTINGS_SHELL
    assert "function moveSessionTransferPage" in SETTINGS_SHELL
    assert "目标 Provider 的会话列表确认落盘" in SETTINGS_SHELL
    assert "operationSource" in SETTINGS_SHELL
    assert "function sessionTransferCompletedSourceIds" in SETTINGS_SHELL
    assert "targetVisible === true && item?.targetResumable === true" in SETTINGS_SHELL
    # Sources with a verified target copy but a retained source must never be
    # re-offered for a duplicate copy after the dialog reopens.
    assert "pendingSourceCleanup" in SETTINGS_SHELL
    assert "item?.pendingSourceCleanup !== true" in SETTINGS_SHELL
    assert 'availability: "all"' in SETTINGS_SHELL
    assert "item?.transferable !== false || item?.archived === true" in SETTINGS_SHELL
    assert "已归档，请先解除归档后再复制或迁移" in SETTINGS_SHELL
    assert "return !!source && !!target && !!operationSource && !!operationTarget" in SETTINGS_SHELL
    assert "function normaliseSessionTransferTargetProvider" in SETTINGS_SHELL
    assert "function sessionTransferOperationMatchesCurrentPair" in SETTINGS_SHELL
    assert "function sessionTransferProgressModel" in SETTINGS_SHELL
    assert "function sessionTransferProgressHtml" in SETTINGS_SHELL
    assert 'data-session-transfer-list="true"' in SETTINGS_SHELL
    assert 'data-session-transfer-progress-state="true"' in SETTINGS_SHELL
    assert "function requestSessionTransferCancel" in SETTINGS_SHELL
    assert "sessionTransferElapsedTimer" in SETTINGS_SHELL
    assert "sessionTransferState.startedAt" in SETTINGS_SHELL
    assert "operationStartedAt" in SETTINGS_SHELL
    assert "persistedStartedAt" in SETTINGS_SHELL
    assert "sourceCleanupCompletedCount" in SETTINGS_SHELL
    assert "operationValue?.phaseLabel" in SETTINGS_SHELL
    assert "sourceCleanupPendingCount" in SETTINGS_SHELL
    assert "个源会话待清理" in SETTINGS_SHELL
    assert 'data-state="progress"' in SETTINGS_SHELL
    assert '.codex-usage-hud-session-transfer-list[data-state="progress"]' in LAYOUT_STYLE
    assert 'new Set(["completed", "partial", "failed", "cancelled"])' in SETTINGS_SHELL
    assert "list.dataset.state !== nextState" in SETTINGS_SHELL
    assert "stopSessionTransferElapsedTicker();" in SETTINGS_SHELL
    assert "const pairMatches = !sessionTransferState.open" in SETTINGS_SHELL
    assert "已可在目标 Provider 继续工作" in SETTINGS_SHELL
    assert 'parts.push("Codex App · 必选")' not in SETTINGS_SHELL

    for function_name in (
        "syncSessionTransferDialogControls",
        "sessionTransferDialogHtml",
    ):
        function_start = SETTINGS_SHELL.index(f"function {function_name}")
        function_end = SETTINGS_SHELL.index("\n      function ", function_start + 1)
        function_source = SETTINGS_SHELL[function_start:function_end]
        assert function_source.index(
            "normaliseSessionTransferTargetProvider(settings, source)"
        ) < function_source.index("const operation = sessionTransferOperation();")


def test_session_transfer_failure_errors_explain_thread_store_ordinal_issue() -> None:
    # Codex upstream rejects thread/fork for rollouts whose history ordinals
    # are inconsistent (openai/codex#38248, #38317).  The dialog must append
    # an actionable Chinese hint instead of only echoing the raw error.
    assert "function sessionTransferErrorHint" in SETTINGS_SHELL
    assert "function sessionTransferErrorHtml" in SETTINGS_SHELL
    assert "/failed to prepare paginated fork/i" in SETTINGS_SHELL
    assert "/thread history projection/i" in SETTINGS_SHELL
    assert "/expected ordinal/i" in SETTINGS_SHELL
    assert "目前无法被复制或迁移" in SETTINGS_SHELL
    assert "仍可在原 Provider 打开继续使用" in SETTINGS_SHELL
    assert '<small data-kind="hint">' in SETTINGS_SHELL
    assert ".codex-usage-hud-session-transfer-error small[data-kind=\"hint\"]" in (
        LAYOUT_STYLE
    )
    hint_start = SETTINGS_SHELL.index("function sessionTransferErrorHint")
    hint_end = SETTINGS_SHELL.index("\n      function ", hint_start + 1)
    hint_source = SETTINGS_SHELL[hint_start:hint_end]
    # Non-matching errors must keep the original rendering untouched.
    assert 'return "";' in hint_source
    error_html_start = SETTINGS_SHELL.index("function sessionTransferErrorHtml")
    error_html_end = SETTINGS_SHELL.index("\n      function ", error_html_start + 1)
    error_html_source = SETTINGS_SHELL[error_html_start:error_html_end]
    assert "sessionTransferErrorHint(errorText)" in error_html_source
    assert 'escapeHtml(title)' in error_html_source


def test_session_transfer_reuses_session_management_scan_state_and_controls() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    assert 'const sessionTransferStateName = "__codexUsageHudSessionTransferState";' in script
    assert "const retainedSessionTransferState = window[sessionTransferStateName];" in script
    assert "window[sessionTransferStateName] = sessionTransferState;" in script
    assert "delete window[sessionTransferStateName];" in script
    assert "function activeSessionCleanupScanRequestId" in script
    assert "requestSessionCleanupScan({ preserveTransfer: true })" in script
    assert "function sessionTransferSelectableIds" in script
    assert "function syncSessionTransferSelection" in script
    assert "function syncSessionTransferSelectAll" in script
    assert "sessionCleanupPayloadWithInventory" in script
    assert "function syncSessionTransferSubmitButton" in script
    assert "function bindSessionTransferModeControls" in script
    assert 'input.addEventListener("change", apply);' in script
    assert "function syncSessionTransferModeFromDialog" in script
    assert "function requestSessionTransferCancel" in script
    assert 'action.dataset.action === "session-transfer-cancel"' in script
    assert "sessionTransferState.cancelledRequestId" in script
    assert "syncSessionTransferModeFromDialog();" in script
    assert "const syncSessionTransferModeFromEvent = (event) =>" in script
    assert 'rootScope.listen(document, "click"' in script
    assert 'rootScope.listen(document, "change"' in script
    assert "sessionTransferState.mode = sessionTransferModeValue(sessionTransferMode);" in script
    assert 'action.dataset.action === "session-transfer-resume"' not in script
    assert "for (const id of sessionTransferSelectableIds())" in script
    assert "function sessionTransferModeValue" in script
    assert 'const sessionTransferMode = event.target?.closest?.(\'[data-session-transfer-mode]\');' in script
    assert "Radio input events fire as soon as the native selection changes." in script
    assert "if (sessionTransferState.open) applySessionTransferPayload(payload);" in script
    assert "sessionTransferResumeId" not in SETTINGS_SHELL
    assert "function scheduleSessionCleanupScanWatchdog(requestId)" in script
    assert 'const bindingAvailable = !!ctx.bindings?.available?.(settingsCommandBindingName);' in SETTINGS_SHELL
    assert "sessionCleanupScanWatchdogTimer" in script


def test_codex_cli_dialog_is_compact_and_persists_profile_scoped_launches() -> None:
    form_start = SETTINGS_SHELL.index("function codexCliFormHtml()")
    form_end = SETTINGS_SHELL.index("function renderCodexCliDialog()", form_start)
    form = SETTINGS_SHELL[form_start:form_end]
    launch_start = SETTINGS_SHELL.index("function launchCodexCliWithState(command)")
    launch_end = SETTINGS_SHELL.index("function applyCodexCliCommandStatus", launch_start)
    launch = SETTINGS_SHELL[launch_start:launch_end]

    assert "codexCliLaunchStorageKey" in renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    assert "function codexCliLaunchStateKey(" in SETTINGS_SHELL
    assert "profile:" in SETTINGS_SHELL
    assert "|provider:" in SETTINGS_SHELL
    assert "const persistedLaunchState = codexCliPersistedLaunchState();" in SETTINGS_SHELL
    assert "function codexCliValidatedQuickLaunchState(" in SETTINGS_SHELL
    assert "if (saved.commandEdited === true) return null;" in SETTINGS_SHELL
    assert "applyCodexCliPersistedLaunchState(persistedLaunchState);" in SETTINGS_SHELL
    assert "codexCliState.useProxy = false;" in SETTINGS_SHELL
    assert 'data-codex-cli-proxy-port="true"' in form
    assert 'proxyPort.hidden = codexCliState.useProxy !== true;' in SETTINGS_SHELL
    assert "function codexCliLaunchTitle" in SETTINGS_SHELL
    assert 'return ["启动 Codex", ...args].join(" ");' in SETTINGS_SHELL
    assert '<strong>${escapeHtml(codexCliLaunchTitle())}</strong>' in SETTINGS_SHELL
    assert '<option value="" disabled' in form
    assert "请选择工作目录" in form
    assert 'codexCliState.workdir = "";' in SETTINGS_SHELL
    assert 'launch.disabled = !codexCliTerminal().id;' in SETTINGS_SHELL
    assert "请先选择工作目录后再启动 Codex CLI" in SETTINGS_SHELL
    assert 'data-codex-cli-field="resume"' in form
    assert 'data-codex-cli-field="migratedWorkdirs"' in form
    assert "来自迁移" in form
    assert "codex-usage-hud-codex-cli-field-head" in form
    assert "codex-usage-hud-codex-cli-check-compact" in form
    assert "justify-content: flex-start;" in LAYOUT_STYLE
    assert "align-items: baseline;" in LAYOUT_STYLE
    assert ".codex-usage-hud-codex-cli-field .codex-usage-hud-codex-cli-check-compact input" in LAYOUT_STYLE
    assert "function codexCliNormaliseWorkdirPath(" in SETTINGS_SHELL
    assert 'const longPathPrefix = "\\\\\\\\?\\\\";' in SETTINGS_SHELL
    assert "codexCliPersistTransferWorkdirs(operation);" in SETTINGS_SHELL
    assert "function codexCliTransferWorkdirStorageKey(" in SETTINGS_SHELL
    assert 'return provider ? `provider:${provider}` : "";' in SETTINGS_SHELL
    assert "function codexCliTransferWorkdirs()" in SETTINGS_SHELL
    assert "const seen = new Set();" in SETTINGS_SHELL
    assert form.index('data-codex-cli-field="resume"') < form.index("启动终端")
    assert "codex-usage-hud-codex-cli-check codex-usage-hud-codex-cli-wide" not in form
    assert "codexCliPersistLaunchState(pendingLaunchState);" in SETTINGS_SHELL
    assert "noProject: codexCliState.noProject === true" in SETTINGS_SHELL
    assert "noProjectWorkdir" in SETTINGS_SHELL
    assert "noProjectWorkdirOption" in SETTINGS_SHELL
    assert 'value="__no_project__"' in SETTINGS_SHELL
    assert "if (workdir && codexCliState.noProject !== true)" in SETTINGS_SHELL
    assert "codexCliState.pendingLaunchState = codexCliLaunchState(command);" in launch
    assert SETTINGS_SHELL.index("codexCliPersistLaunchState(pendingLaunchState);") > SETTINGS_SHELL.index(
        'if (action === "codexCliLaunch"'
    )
    assert 'const requestId = typedSettingsRequestId("codex-cli-launch");' in launch
    assert 'mode: "codex-cli-launch"' in launch
    assert launch.index("openSettingsLoading(") < launch.index(
        'ctx.lifecycle.frame("codex_cli_launch_submit"'
    ) < launch.index("submitSettingsCommand(")
    assert '"codex_cli_launch_timeout"' in launch
    assert "codexCliLaunchMinVisibleMs = 240" in SETTINGS_SHELL
    assert '"codex_cli_launch_min_visible"' in SETTINGS_SHELL
    assert "clearCodexCliLaunchLifecycle();" in SETTINGS_SHELL
    assert "closeSettingsConfirm();" in launch
    assert "codexCliState.launchRequestId" in launch
    assert "codex-usage-hud-codex-cli-proxy {" in LAYOUT_STYLE
    assert "width: 76px;" in LAYOUT_STYLE


def test_codex_cli_models_are_loaded_on_dropdown_interaction_without_extra_status_row() -> None:
    open_start = SETTINGS_SHELL.index("function openCodexCliDialog(provider = \"\")")
    open_end = SETTINGS_SHELL.index("function openCodexCliQuickLaunch", open_start)
    open_dialog = SETTINGS_SHELL[open_start:open_end]
    refresh_start = SETTINGS_SHELL.index("function refreshCodexCliDialog()")
    refresh_end = SETTINGS_SHELL.index("function codexCliCopyCommand()", refresh_start)
    refresh_dialog = SETTINGS_SHELL[refresh_start:refresh_end]
    chat_state_start = SETTINGS_SHELL.index("function codexCliChatTestState()")
    chat_state_end = SETTINGS_SHELL.index("function reopenCodexCliModelPicker(select)", chat_state_start)
    chat_state = SETTINGS_SHELL[chat_state_start:chat_state_end]
    status_start = SETTINGS_SHELL.index('if (action === "codexCliFetchModels"')
    status_end = SETTINGS_SHELL.index('if (action === "codexCliChatTest"', status_start)
    model_status = SETTINGS_SHELL[status_start:status_end]

    assert "requestCodexCliModels();" not in open_dialog
    assert "requestCodexCliModels();" not in refresh_dialog
    assert 'rootScope.listen(root, "pointerdown"' in ROUTER
    assert 'data-codex-cli-field="model"' in ROUTER
    assert '["Enter", " ", "ArrowDown", "F4"]' in ROUTER
    assert "requestCodexCliModels({ reopenPicker: true });" in ROUTER
    assert 'action.dataset.action === "codex-cli-model-refresh"' in ROUTER
    assert "requestCodexCliModels({ force: true });" in ROUTER
    assert 'data-codex-cli-model-note="true"' in SETTINGS_SHELL
    assert 'data-action="codex-cli-model-refresh"' in SETTINGS_SHELL
    assert "点击模型下拉框获取当前 Provider 的模型列表。" in SETTINGS_SHELL
    assert 'class="codex-usage-hud-codex-cli-model-note"' not in SETTINGS_SHELL
    assert "function reopenCodexCliModelPicker(select)" in SETTINGS_SHELL
    assert 'select.matches(":open")' in SETTINGS_SHELL
    assert "select.showPicker();" in SETTINGS_SHELL
    assert "renderCodexCliDialog();" not in model_status
    assert "syncCodexCliModelDiscoveryState({ syncOptions: true, reopenPicker });" in model_status
    assert "codexCliState.models = [];" not in model_status
    assert 'if (count) return "hidden";' not in chat_state
    assert "codex-usage-hud-codex-cli-model-control" in LAYOUT_STYLE
    assert "grid-template-columns: minmax(0, 1fr) 30px;" in LAYOUT_STYLE
    assert "text-overflow: ellipsis;" in LAYOUT_STYLE
    assert "white-space: nowrap;" in LAYOUT_STYLE
    assert "调整上方选项会重新生成命令" not in SETTINGS_SHELL


def test_codex_cli_quick_launch_menu_is_idempotent_and_provider_scoped() -> None:
    assert 'quick_launch_providers: []' in SETTINGS_SHELL
    assert 'data-provider-quick-launch="true"' in SETTINGS_SHELL
    assert 'data-codex-usage-hud-cli-menu-toggle="true"' in SETTINGS_SHELL
    assert 'data-codex-usage-hud-cli-provider="true"' in SETTINGS_SHELL
    assert 'role="menubar"' in SETTINGS_SHELL
    assert "function syncCodexCliQuickLaunchMenu()" in SETTINGS_SHELL
    assert "function openCodexCliFromApplicationMenu(provider)" in SETTINGS_SHELL
    assert "openCodexCliQuickLaunch(normalizedProvider);" in SETTINGS_SHELL
    assert 'data-codex-cli-quick-launch="true"' in SETTINGS_SHELL
    assert 'data-codex-cli-quick-action="stop"' in SETTINGS_SHELL
    assert 'action: "codexCliLaunchCancel"' in SETTINGS_SHELL
    assert "codex-usage-hud-codex-cli-menu-style" in SETTINGS_SHELL
    assert "MutationObserver" in SETTINGS_SHELL
    assert "function codexCliRecentWorkdirs(provider" in SETTINGS_SHELL
    assert "function codexCliDefaultWorkdir(provider)" in SETTINGS_SHELL
    assert "function codexCliWorkdirScopeKey(provider, profile)" in SETTINGS_SHELL
    assert "function codexCliActiveProfile(provider)" in SETTINGS_SHELL
    assert "codexCliPersistActiveProfile(savedState.provider, savedState.profile);" in SETTINGS_SHELL
    assert "stored[scopeKey] = { workdirs, savedAt: now };" in SETTINGS_SHELL
    assert ".slice(0, expanded ? 10 : 3)" in SETTINGS_SHELL
    assert 'data-codex-usage-hud-cli-workdir-more="true"' in SETTINGS_SHELL
    assert 'data-codex-usage-hud-cli-workdir="true"' in SETTINGS_SHELL
    assert 'data-no-project="true"' in SETTINGS_SHELL
    assert 'data-codex-usage-hud-cli-provider-workdirs="true"' in SETTINGS_SHELL
    assert 'data-codex-usage-hud-cli-workdir-default="true"' in SETTINGS_SHELL
    assert 'data-default="true"' in SETTINGS_SHELL
    assert "codexCliPersistRecentWorkdir(pendingLaunchState);" in SETTINGS_SHELL
    assert "function toggleCodexCliQuickLaunchWorkdirMenu(provider, toggle)" in SETTINGS_SHELL
    assert "closeCodexCliQuickLaunchWorkdirMenu();" in SETTINGS_SHELL
    assert "codexCliQuickLaunchMenuState.workdirExpanded" in SETTINGS_SHELL
    assert "清除 Codex 项目上下文" in SETTINGS_SHELL


def test_codex_cli_menu_shows_active_session_provider_label() -> None:
    assert "function syncCodexCliQuickLaunchProviderLabel(menubar, toggle)" in SETTINGS_SHELL
    assert 'data-codex-usage-hud-cli-provider-label="true"' in SETTINGS_SHELL
    assert "function codexCliQuickLaunchProviderLabelState()" in SETTINGS_SHELL
    assert "currentPayload()?.activeSessionProvider" in SETTINGS_SHELL
    assert "hudSettingsFromPayload().app_provider" in SETTINGS_SHELL
    # 供应商标签显示 config.toml 的 name 键（缺省回退 Provider ID）。
    assert '供应商 ${activeName}' in SETTINGS_SHELL
    assert '（默认）' in SETTINGS_SHELL
    assert ".codex-usage-hud-cli-provider-label[data-mismatch=\"true\"] {" in SETTINGS_SHELL
    assert "syncCodexCliQuickLaunchProviderLabel(menubar, toggle);" in SETTINGS_SHELL
    assert 'document.querySelector(\'[data-codex-usage-hud-cli-provider-label="true"]\')?.remove();' in SETTINGS_SHELL
    # The label sync runs inside the body-wide MutationObserver callback, so
    # steady state must be DOM-write free or the renderer freezes in a
    # mutation feedback loop.
    assert (
        "label.parentElement !== menubar || label.previousElementSibling !== toggle"
    ) in SETTINGS_SHELL
    assert "toggle.insertAdjacentElement(\"afterend\", label);" in SETTINGS_SHELL


def test_hud_panels_hide_while_desktop_chrome_is_missing() -> None:
    assert "function desktopChromeMissing()" in LAYOUT_ANCHORS
    assert "return !document.querySelector('[role=\"menubar\"]');" in LAYOUT_ANCHORS
    assert "const chromeMissing = desktopChromeMissing();" in LAYOUT_OBSERVERS
    assert 'panel.dataset.awaitingChrome = "true";' in LAYOUT_OBSERVERS
    assert "delete panel.dataset.awaitingChrome;" in LAYOUT_OBSERVERS
    # While the splash screen is up, the bootstrap observer is the only
    # wake-up for the panel reveal and must not be killed by its 5s guard.
    assert "ctx.lifecycle.clearTimeout(window[bootstrapTimerName] || 0);" in LAYOUT_OBSERVERS
    assert '.codex-usage-hud-panel[data-awaiting-chrome="true"] {' in LAYOUT_STYLE
    # Session-domain payloads must refresh the label without a settings push.
    assert "settingsShellDomain.syncCodexCliQuickLaunchMenu?.();" in ROUTER


def test_codex_cli_quick_launch_menu_controls_exit_desktop_drag_region() -> None:
    style_start = SETTINGS_SHELL.index('[data-codex-usage-hud-cli-menu-surface="true"] {')
    style_end = SETTINGS_SHELL.index("@keyframes codexUsageHudCliQuickFade", style_start)
    style = SETTINGS_SHELL[style_start:style_end]

    selectors = (
        '[data-codex-usage-hud-cli-menu-surface="true"] {',
        '[data-codex-usage-hud-cli-menu-surface="true"] [role="menuitem"] {',
        '[data-codex-usage-hud-cli-menu-toggle="true"] {',
    )
    for selector in selectors:
        rule_start = style.index(selector)
        rule = style[rule_start:style.index("}", rule_start)]
        assert "pointer-events: auto;" in rule
        assert "-webkit-app-region: no-drag;" in rule


def test_codex_cli_quick_launch_auto_launch_requires_exact_saved_state() -> None:
    key_start = SETTINGS_SHELL.index("function codexCliLaunchStateKey(")
    key_end = SETTINGS_SHELL.index("function codexCliStoredLaunchStates()", key_start)
    key = SETTINGS_SHELL[key_start:key_end]
    validation_start = SETTINGS_SHELL.index("function codexCliValidatedQuickLaunchState(")
    validation_end = SETTINGS_SHELL.index("function applyCodexCliPersistedLaunchState", validation_start)
    validation = SETTINGS_SHELL[validation_start:validation_end]
    discover_start = SETTINGS_SHELL.index('if (action === "codexCliDiscover"')
    discover_end = SETTINGS_SHELL.index('if (action === "codexCliLaunch"', discover_start)
    discover = SETTINGS_SHELL[discover_start:discover_end]

    assert '"profile:" + (profile || "default")' in key
    assert '"|provider:" + (provider || "default")' in key
    assert "savedProvider !== provider || savedProfile !== profile" in validation
    assert 'Object.prototype.hasOwnProperty.call(saved, "provider")' in validation
    assert 'Object.prototype.hasOwnProperty.call(saved, "profile")' in validation
    assert "if (saved.commandEdited === true) return null;" in validation
    assert "terminals.some" in validation
    assert "permissions.some" in validation
    assert "if (!workdir) return null;" in validation
    assert "proxyPort < 1 || proxyPort > 65535" in validation
    assert discover.index("const validatedLaunchState = codexCliValidatedQuickLaunchState(") < discover.index(
        "if (!validatedLaunchState)"
    ) < discover.index("openCodexCliQuickLaunchConfiguration();")
    assert discover.index("applyCodexCliPersistedLaunchState(validatedLaunchState);") < discover.index(
        'ctx.lifecycle.frame("codex_cli_quick_launch"'
    )
    assert "if (codexCliState.open && codexCliIsQuickLaunch()) launchCodexCliFromQuickState();" in discover


def test_codex_cli_quick_launch_invalid_runtime_configuration_reopens_dialog() -> None:
    invalid_start = SETTINGS_SHELL.index("function codexCliQuickLaunchConfigurationInvalid(")
    invalid_end = SETTINGS_SHELL.index("function applyCodexCliPersistedLaunchState", invalid_start)
    invalid = SETTINGS_SHELL[invalid_start:invalid_end]
    launch_status_start = SETTINGS_SHELL.index('if (action === "codexCliLaunch"')
    launch_status_end = SETTINGS_SHELL.index("function settingsChromeMarkup", launch_status_start)
    launch_status = SETTINGS_SHELL[launch_status_start:launch_status_end]

    assert "工作目录不存在或不可访问" in invalid
    assert "所选终端当前不可用" in invalid
    assert "codexCliQuickLaunchConfigurationInvalid(status)" in launch_status
    assert "quickLaunch && !quickDismissed && codexCliQuickLaunchConfigurationInvalid(status)" in launch_status
    assert launch_status.index("quickLaunch && !quickDismissed && codexCliQuickLaunchConfigurationInvalid(status)") < launch_status.index(
        'renderCodexCliQuickLaunch(\n                "error"'
    )
    assert "openCodexCliQuickLaunchConfiguration();" in launch_status


def test_renderer_reinject_captures_active_session_sequences_before_remove() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    selection_capture = script.index("const previousActiveSessionSelectionSeq")
    applied_capture = script.index("const previousActiveSessionAppliedSeq")
    previous_remove = script.index(
        'window.__codexUsageHudRemove({ preserveState: true });'
    )

    assert selection_capture < applied_capture < previous_remove
    # Same-version reinjection now exits before touching the old runtime.  The
    # fallback preserve-state remove remains below the sequence handoff.
    assert "window.__codexUsageHudRemove({ preserveState: true });" not in script[:selection_capture]


def test_renderer_same_version_reinject_reuses_live_runtime_before_teardown() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    guard_start = script.index('const existingRoot = document.getElementById(rootId);')
    guard_end = script.index('const styleId = "codex-usage-hud-style";', guard_start)
    guard = script[guard_start:guard_end]

    assert 'existingRoot?.dataset.version === version' in guard
    assert 'existingRoot?.dataset.bundleFingerprint === bundleFingerprint' in guard
    assert 'window.__codexUsageHudRemove({ preserveState: false });' in guard
    assert 'typeof window.__codexUsageHudUpdate === "function"' in guard
    assert 'typeof window.__codexUsageHudRemove === "function"' in guard
    assert "return;" in guard
    assert script.index("return;", guard_start) < script.index(
        'const styleId = "codex-usage-hud-style";', guard_start
    )
    assert script.index('const styleId = "codex-usage-hud-style";') < script.index(
        'window.__codexUsageHudRemove({ preserveState: true });'
    )


def test_active_session_container_prefers_thread_role_list() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    container_start = script.index("function activeSessionContainer()")
    container_end = script.index("function postActiveSession", container_start)
    container = script[container_start:container_end]

    assert 'row?.closest?.("[role=\'list\']")' in container
    assert 'document.querySelector("[role=\'list\']")' in container
    assert container.index('row?.closest?.("[role=\'list\']")') < container.index(
        'row?.closest?.("aside, nav'
    )
    assert container.index('document.querySelector("[role=\'list\']")') < container.index(
        'document.querySelector("aside, nav'
    )


def test_active_session_reports_native_current_work_disclosure_title() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE

    assert "function activeSessionCollapsedWorkTitle()" in script
    assert "function activeSessionCollapsedWorkState()" in script
    assert "function activeSessionDisclosureTitleLine(value)" in script
    assert "function activeSessionDisclosureTitleIsActive(value)" in script
    assert "const activeEntries = groups.flatMap" in script
    assert "const collapsedWork = (newSession || pendingSession)" in script
    assert "const collapsedDisclosureAmbiguous = !!collapsedWork.ambiguous;" in script
    assert "collapsedTitle," in script
    observer_start = script.index("function refreshActiveSessionObserver()")
    observer_end = script.index("function startActiveSessionBootstrapObserver()", observer_start)
    observer = script[observer_start:observer_end]
    assert "const conversation = activeSessionConversationScope();" in observer
    assert "aria-expanded" in observer


def test_active_session_groups_disclosures_per_turn_and_picks_latest() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE

    groups_start = script.index("function activeSessionCollapsedDisclosureGroups(")
    state_start = script.index("function activeSessionCollapsedWorkState()")
    groups_body = script[groups_start:state_start]
    state_end = script.index("function activeSessionCollapsedWorkTitle()", state_start)
    state = script[state_start:state_end]

    # Toggles are grouped by their innermost turn node, so a running turn
    # without a disclosure cannot wipe the previous turn's collapsed title.
    assert 'node.closest?.("[data-content-search-turn-key], [data-turn-key]")' in groups_body
    assert "const latest = groups[groups.length - 1];" in state
    assert "latest.entries.length !== 1" in state
    assert "ambiguous: latest.entries.length > 1" in state
    assert "return { title: latest.entries[0].title, ambiguous: false };" in state
    # Multiple matching disclosures inside the same latest turn still fail closed.
    assert "return { title: \"\", ambiguous: latest.entries.length > 1 };" in state


def test_active_session_conversation_scope_does_not_depend_on_zero_size_turn() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE

    scope_start = script.index("function activeSessionConversationScope()")
    scope_end = script.index("function activeSessionTurnKeyOf(", scope_start)
    scope = script[scope_start:scope_end]

    # Per-turn nodes use display:contents (zero-size); scope must prefer main and
    # must never filter turn nodes by visibility.
    assert 'header?.closest?.("main, [role=\'main\']")' in scope
    assert ".filter((node) => visible(node)" not in scope
    assert ".filter((node) => node.isConnected" in scope


def test_active_session_container_uses_selected_identity_row_list() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    container_start = script.index("function activeSessionContainer()")
    container_end = script.index("function postActiveSession", container_start)
    container = script[container_start:container_end]

    assert "const selectedIdentityRow = identityRows.find(activeSessionRowSelected)" in container
    selected_branch = container[container.index("if (selectedIdentityRow)") :]
    assert selected_branch.index('row?.closest?.("[role=\'list\']")') < selected_branch.index(
        'row?.closest?.("aside, nav'
    )


def test_active_session_guards_header_title_transition_after_canonical_id() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    guard_start = script.index("const headerTitleTransition = (")
    guard_end = script.index("const selectionKey =", guard_start)
    guard = script[guard_start:guard_end]

    assert "!canonicalSessionId" in guard
    assert "!newSession" in guard
    assert "!pendingSession" in guard
    assert 'ref.matchedBy === "header-title"' in guard
    assert "reason !== \"click\"" in guard
    assert "lastCanonicalSessionId" in guard
    assert "Date.now() - lastCanonicalAt < 2500" in guard
    assert guard.index("headerTitleTransition") < guard.index(
        "const transientWithoutCanonicalId"
    )


def test_active_session_selection_identity_prefers_canonical_id() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    selection_start = script.index("const selectionKey =")
    selection_end = script.index("const selectionSeq =", selection_start)
    selection = script[selection_start:selection_end]

    assert "canonicalSessionId" in selection
    assert "JSON.stringify([canonicalSessionId, newSession, pendingSession])" in selection
    assert "rawRendererSessionId" in selection
    assert "ref.title" in selection


def test_renderer_script_is_now_only_a_small_facade() -> None:
    source_lines = open(renderer_script.__file__, encoding="utf-8").read().splitlines()
    assert len(source_lines) <= 20
