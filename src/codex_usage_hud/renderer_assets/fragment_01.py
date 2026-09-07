HEAD = r"""
(() => {
  const version = "69";
  const bundleFingerprint = "__CODEX_USAGE_HUD_BUNDLE_FINGERPRINT__";
  const rootId = "codex-usage-hud-root";
  // Reinstalling the same Renderer bundle in a live document must be
  // idempotent.  In particular, do not tear down the old runtime while a
  // settings child dialog is open: its unsaved form values and pending
  // command state live in that runtime's closure.  The new-document script
  // still runs normally on a fresh document where no root exists.
  const existingRoot = document.getElementById(rootId);
  if (
    existingRoot?.dataset.version === version
    && existingRoot?.dataset.bundleFingerprint === bundleFingerprint
    && typeof window.__codexUsageHudUpdate === "function"
    && typeof window.__codexUsageHudRemove === "function"
  ) {
    return;
  }
  if (existingRoot && typeof window.__codexUsageHudRemove === "function") {
    try {
      window.__codexUsageHudRemove({ preserveState: false });
    } catch (_) {}
  }
  const runtimeGenerationName = "__codexUsageHudRuntimeGeneration";
  const previousRuntimeGeneration = Number(window[runtimeGenerationName] || 0);
  const runtimeGeneration = Number.isFinite(previousRuntimeGeneration)
    ? previousRuntimeGeneration + 1
    : 1;
  window[runtimeGenerationName] = runtimeGeneration;
  const runtimeIsCurrent = () => window[runtimeGenerationName] === runtimeGeneration;
  const styleId = "codex-usage-hud-style";
  const topClass = "codex-usage-hud-top";
  const requestClass = "codex-usage-hud-request";
  const warningClass = "codex-usage-hud-warning";
  const errorClass = "codex-usage-hud-error";
  const resizeHandlerName = "__codexUsageHudResize";
  const scrollHandlerName = "__codexUsageHudScroll";
  const mutationObserverName = "__codexUsageHudObserver";
  const resizeObserverName = "__codexUsageHudResizeObserver";
  const bootstrapObserverName = "__codexUsageHudBootstrapObserver";
  const bootstrapTimerName = "__codexUsageHudBootstrapTimer";
  const scheduleName = "__codexUsageHudSchedule";
  const stateName = "__codexUsageHudState";
  const rafName = "__codexUsageHudRaf";
  const settleTimerName = "__codexUsageHudSettleTimers";
  const composerSettleTimerName = "__codexUsageHudComposerSettleTimer";
  const composerInputNodeName = "__codexUsageHudComposerInputNode";
  const composerInputHandlersName = "__codexUsageHudComposerInputHandlers";
  const composerFocusStateName = "__codexUsageHudComposerFocused";
  const composerBadgeRafName = "__codexUsageHudComposerBadgeRaf";
  const composerAttachmentsTimerName = "__codexUsageHudComposerAttachmentsTimer";
  const composerAttachmentsSignatureName = "__codexUsageHudComposerAttachmentsSignature";
  const composerAttachmentsObserverName = "__codexUsageHudComposerAttachmentsObserver";
  const themeObserverName = "__codexUsageHudThemeObserver";
  const themeMediaQueryName = "__codexUsageHudThemeMediaQuery";
  const themeMediaQueryHandlerName = "__codexUsageHudThemeMediaQueryHandler";
  const themeStorageHandlerName = "__codexUsageHudThemeStorageHandler";
  const themeTimerName = "__codexUsageHudThemeTimer";
  const themeSignatureName = "__codexUsageHudThemeSignature";
  const runningTimerName = "__codexUsageHudRunningTimer";
  const staleTimerName = "__codexUsageHudStaleTimer";
  const storageKey = "codexUsageHudPanelState:v5";
  const supportImagesStorageKey = "codexUsageHudSupportImages:v1";
  const codexCliLaunchStorageKey = "codexUsageHudCodexCliLaunches:v1";
  const codexCliTransferWorkdirStorageKey = "codexUsageHudCodexCliTransferWorkdirs:v1";
const settingsModalId = `codex-usage-hud-settings-modal-${version}`;
const settingsProviderName = "__codexUsageHudSettingsProvider";
  const settingsUiStateName = "__codexUsageHudSettingsUiState";
  const settingsUiStorageKey = "codexUsageHudSettingsUiState:v1";
  const sessionCleanupFiltersStateName = "__codexUsageHudSessionCleanupFilters";
  const sessionCleanupFiltersStorageKey = "codexUsageHudSessionCleanupFilters:v1";
  const sessionTransferStateName = "__codexUsageHudSessionTransferState";
  const activeSessionObserverName = "__codexUsageHudActiveSessionObserver";
  const activeSessionBootstrapObserverName = "__codexUsageHudActiveSessionBootstrapObserver";
  const activeSessionBootstrapTimerName = "__codexUsageHudActiveSessionBootstrapTimer";
  const activeSessionTimerName = "__codexUsageHudActiveSessionTimer";
  const activeSessionDraftTimerName = "__codexUsageHudActiveSessionDraftTimer";
  const activeSessionSendFollowupTimersName = "__codexUsageHudActiveSessionSendTimers";
  const activeSessionComposerHandlerName = "__codexUsageHudActiveSessionComposerHandler";
  const activeSessionClickHandlerName = "__codexUsageHudActiveSessionClick";
  const activeSessionHistoryPatchName = "__codexUsageHudActiveSessionHistoryPatch";
  const activeSessionCanonicalIdName = "__codexUsageHudActiveSessionCanonicalId";
  const activeSessionCanonicalAtName = "__codexUsageHudActiveSessionCanonicalAt";
  const activeSessionSettledTimerName = "__codexUsageHudActiveSessionSettledTimer";
  const activeSessionSelectionKeyName = "__codexUsageHudActiveSessionSelectionKey";
  const activeSessionSelectionSeqName = "__codexUsageHudActiveSessionSelectionSeq";
  const activeSessionAppliedSeqName = "__codexUsageHudActiveSessionAppliedSeq";
  const activeSessionPayloadCacheName = "__codexUsageHudActiveSessionPayloadCache";
    const activeSessionLastSignatureName = "__codexUsageHudActiveSessionLastSignature";
    const activeSessionBindingName = "codexUsageHudActiveSession";
    const settingsCommandBindingName = "codexUsageHudSettingsCommand";
    const composerAttachmentsBindingName = "codexUsageHudComposerAttachments";
    const layoutBindingName = "codexUsageHudLayout";
    const themeBindingName = "codexUsageHudTheme";
  const layoutReportTimerName = "__codexUsageHudLayoutTimer";
  const layoutReportSignatureName = "__codexUsageHudLayoutSignature";
  const staleUpdateMs = 10000;
  const composerAttachmentsDebounceMs = 80;
  const composerBadgeEnabled = __COMPOSER_TIKTOKEN_BADGE_ENABLED__;
  const codexModelPickerCatalog = __CODEX_MODEL_PICKER_CATALOG__;
  const modelPickerPatchHandlerName = "__codexUsageHudModelPickerPatchHandler";
  const modelPickerPatchRafName = "__codexUsageHudModelPickerPatchRaf";
  const modelPickerPatchTimersName = "__codexUsageHudModelPickerPatchTimers";
  const modelPickerSelectionName = "__codexUsageHudModelPickerSelection";
"""

SHARED_HEAD = r"""
  let topSlotCache = null;
  let pendingSyncPanels = null;
  let pendingForceAutoFit = false;
  function readSettingsUiState() {
    const runtimeState = window[settingsUiStateName];
    if (runtimeState && typeof runtimeState === "object") return runtimeState;
    try {
      const stored = JSON.parse(ctx.storage.read(sessionStorage, settingsUiStorageKey, "null"));
      if (stored && typeof stored === "object") return stored;
    } catch (_) {}
    return { open: false, tab: "settings" };
  }

  function writeSettingsUiState(open, tab) {
    const state = { open: open === true, tab: String(tab || "settings") };
    window[settingsUiStateName] = state;
    ctx.storage.write(sessionStorage, settingsUiStorageKey, JSON.stringify(state));
    return state;
  }

  function readSessionCleanupFilters() {
    const defaults = {
      search: "",
      dateStart: "",
      dateEnd: "",
      workdirId: "",
      archive: "all",
      availability: "all",
      clientKind: "all",
      modelProvider: "all",
      sort: "recent",
    };
    let value = window[sessionCleanupFiltersStateName];
    if (!value || typeof value !== "object") {
      try {
        value = JSON.parse(ctx.storage.read(sessionStorage, sessionCleanupFiltersStorageKey, "null"));
      } catch (_) {
        value = null;
      }
    }
    if (!value || typeof value !== "object") return defaults;
    const valid = (candidate, values, fallback) => values.includes(candidate) ? candidate : fallback;
    return {
      search: String(value.search || ""),
      dateStart: String(value.dateStart || ""),
      dateEnd: String(value.dateEnd || ""),
      workdirId: String(value.workdirId || ""),
      archive: valid(String(value.archive || ""), ["all", "archived", "unarchived"], "all"),
      availability: valid(String(value.availability || ""), ["all", "selectable", "protected", "current", "running", "unresolved", "unavailable"], "all"),
      clientKind: valid(String(value.clientKind || ""), ["all", "app", "cli", "unknown"], "all"),
      modelProvider: String(value.modelProvider || "all"),
      // "recommended" used to be the implicit default. Treat old persisted
      // values as the new chronological default rather than preserving an
      // unrequested cleanup-oriented order.
      sort: valid(String(value.sort || ""), ["oldest", "recent", "largest"], "recent"),
    };
  }

  function persistSessionCleanupFilters() {
    const state = {
      search: String(sessionCleanupState.search || ""),
      dateStart: String(sessionCleanupState.dateStart || ""),
      dateEnd: String(sessionCleanupState.dateEnd || ""),
      workdirId: String(sessionCleanupState.workdirId || ""),
      archive: String(sessionCleanupState.archive || "all"),
      availability: String(sessionCleanupState.availability || "all"),
      clientKind: String(sessionCleanupState.clientKind || "all"),
      modelProvider: String(sessionCleanupState.modelProvider || "all"),
      sort: String(sessionCleanupState.sort || "recent"),
    };
    window[sessionCleanupFiltersStateName] = state;
    ctx.storage.write(sessionStorage, sessionCleanupFiltersStorageKey, JSON.stringify(state));
    return state;
  }

  window[settingsUiStateName] = readSettingsUiState();
  let settingsActiveTab = String(window[settingsUiStateName]?.tab || "settings");
  let storageBodyScrollTop = 0;
  let cleanupContentScrollTop = 0;
  let sessionTableScrollTop = 0;
  const usageInsightsState = {
    data: null,
    refreshRequestId: "",
    error: "",
  };
  let storageRefreshRaf = 0;
  let storageRefreshTimer = 0;
  let storageRefreshLastAt = 0;
  let sessionCleanupElapsedTimer = 0;
  let sessionCleanupScanWatchdogTimer = 0;
  let sessionCleanupSearchTimer = 0;
  let restReminderCountdownTimer = 0;
  let restReminderSavedRequestId = "";
const _sessionCleanupFilterSnapshot = readSessionCleanupFilters();
// Legacy contract marker: ...readSessionCleanupFilters(),
const sessionCleanupState = {
data: null,
sessionIndex: null,
sessionIndexRefreshing: false,
sessionIndexUiAttached: false,
sessionIndexControlRequestId: "",
sessionIndexControlLabel: "",
sessionIndexElapsedTimer: 0,
pendingRequestId: "",
searchRequestId: "",
searchResultQuery: "",
searchResultRevision: "",
searchResultState: "idle",
searchResultGeneration: 0,
searchResultMatches: new Set(),
searchResultDetails: new Map(),
searchResultTokens: [],
page: 0,
workdirOptions: [],
workdirOptionsRequestId: "",
workdirOptionsRetryBlocked: false,
selectedIds: new Set(),
sessionJumpInflight: new Set(),
sessionJumpUnavailableIds: new Set(),
search: "",
searchDraft: "",
dateStart: "",
dateEnd: "",
dateDraftStart: "",
dateDraftEnd: "",
datePickerOpen: false,
archive: "all",
availability: "all",
clientKind: "all",
modelProvider: "all",
sort: "recent",
indexPanelOpen: false,
indexPanelHidden: false,
previewTokenShown: "",
scanStartedAt: 0,
..._sessionCleanupFilterSnapshot,
searchDraft: String(_sessionCleanupFilterSnapshot.search || ""),
};
  const retainedSessionTransferState = window[sessionTransferStateName];
  const sessionTransferState = retainedSessionTransferState && typeof retainedSessionTransferState === "object"
    ? retainedSessionTransferState
    : {
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
      resumeRequestId: "",
      resumeSessionId: "",
      resumeState: "",
      resumeMessage: "",
      page: 0,
    };
  sessionTransferState.open = sessionTransferState.open === true;
  sessionTransferState.sourceProvider = String(sessionTransferState.sourceProvider || "");
  sessionTransferState.targetProvider = String(sessionTransferState.targetProvider || "");
  sessionTransferState.mode = String(sessionTransferState.mode || "copy").toLowerCase() === "migrate"
    ? "migrate"
    : "copy";
  sessionTransferState.search = String(sessionTransferState.search || "");
  sessionTransferState.selectedIds = sessionTransferState.selectedIds instanceof Set
    ? sessionTransferState.selectedIds
    : new Set(Array.isArray(sessionTransferState.selectedIds) ? sessionTransferState.selectedIds : []);
  sessionTransferState.scanRequestId = String(sessionTransferState.scanRequestId || "");
  sessionTransferState.requestId = String(sessionTransferState.requestId || "");
  sessionTransferState.startedAt = Math.max(0, Number(sessionTransferState.startedAt || 0));
  sessionTransferState.cancelledRequestId = String(sessionTransferState.cancelledRequestId || "");
  sessionTransferState.page = Math.max(0, Number(sessionTransferState.page || 0));
  window[sessionTransferStateName] = sessionTransferState;
  let backgroundUsageFetchSeq = 0;
  let backgroundUsageDetailSeq = 0;
  const backgroundUsageRequestTimeoutMs = 5000;
  let backgroundUsageQueryTimeoutId = 0;
  let backgroundUsageDetailTimeoutId = 0;
  const backgroundUsageBodyScrollTops = new Map();
  const backgroundUsageHistoryScrollTops = new Map();
  const backgroundUsageDetailScrollTops = new Map();
  const backgroundUsageState = {
    range: "today",
    feature: "",
    model: "",
    selectedEventId: "",
    selectedSessionId: "",
    data: null,
    detail: null,
    loading: false,
    detailLoading: false,
    error: "",
    loadedRevision: -1,
    loadedFilterKey: "",
    promptExpanded: false,
    queryRequestId: "",
    queryFilterKey: "",
    detailRequestId: "",
    policy: null,
    policyRequestId: "",
    policyPending: false,
    policyLoading: false,
    policyError: "",
  };
  let cachedHeaderNode = null;
  let cachedComposerNode = null;
  let observedHeaderNode = null;
  let observedComposerNode = null;
  let settingsProviderDraft = null;
  const settingsDirtyProviders = new Set();
  const previousActiveSessionSelectionSeq = Number(
    window[activeSessionSelectionSeqName] || 0
  );
  const previousActiveSessionAppliedSeq = Number(
    window[activeSessionAppliedSeqName] || 0
  );

  try {
    if (typeof window.__codexUsageHudRemove === "function") {
      window.__codexUsageHudRemove({ preserveState: true });
    }
  } catch (_) {}
  const restoredActiveSessionSelectionSeq = Math.max(
    previousActiveSessionSelectionSeq,
    previousActiveSessionAppliedSeq
  );
  if (restoredActiveSessionSelectionSeq > 0) {
    window[activeSessionSelectionSeqName] = restoredActiveSessionSelectionSeq;
  }
  if (previousActiveSessionAppliedSeq > 0) {
    window[activeSessionAppliedSeqName] = previousActiveSessionAppliedSeq;
  }

  const PANEL = {
    top: {
      className: topClass,
      collapsedHeight: 36,
      expandedHeight: 390,
      minCollapsedWidth: 120,
      minExpandedWidth: 220,
      minExpandedHeight: 240,
      fallbackWidth: 520,
    },
    request: {
      className: requestClass,
      collapsedHeight: 32,
      expandedHeight: 180,
      minCollapsedWidth: 120,
      minExpandedWidth: 220,
      minExpandedHeight: 120,
      fallbackWidth: 380,
    },
  };

  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const px = (value) => `${Math.round(value)}px`;
  const visible = (node) => {
    // Native footer icons may be standalone SVG elements rather than HTML
    // controls, so use the shared Element base for geometry visibility.
    if (!(node instanceof Element) || !node.isConnected) return false;
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0) return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const cssEscape = (value) => {
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(String(value));
    }
    return String(value).replace(/["\\]/g, "\\$&");
  };
"""

STYLE_HEAD = r"""
  function ensureStyle() {
    const existing = document.getElementById(styleId);
    if (
      existing?.dataset.version === version
      && existing?.dataset.bundleFingerprint === bundleFingerprint
    ) return;
    existing?.remove();
    const style = document.createElement("style");
    style.id = styleId;
    style.dataset.version = version;
    style.dataset.bundleFingerprint = bundleFingerprint;
    style.textContent = `
      #${rootId} {
        --codex-usage-hud-surface: #10161d;
        --codex-usage-hud-panel-surface: #141b24;
        --codex-usage-hud-panel-border: #3a485a;
        --codex-usage-hud-header-surface: #202833;
        --codex-usage-hud-divider: #273241;
        --codex-usage-hud-text: #e8eef7;
        --codex-usage-hud-muted: #8492a6;
        --codex-usage-hud-accent: #f3d27a;
        --codex-usage-hud-info: #9ccbff;
        --codex-usage-hud-warning: #ffb86b;
        --codex-usage-hud-error: #ff6b6b;
        --codex-usage-hud-success: #8fe3a1;
        --codex-usage-hud-request-surface: #0b1016;
        --codex-usage-hud-request-header-surface: #151d27;
        --codex-usage-hud-request-panel-surface: #101821;
        --codex-usage-hud-request-text: #dce7f2;
        --codex-usage-hud-request-muted: #718095;
        --codex-usage-hud-progress-track: #262c33;
        --codex-usage-hud-progress-track-border: #3b4149;
        --codex-usage-hud-progress-track-text: #c1c7d0;
        --codex-usage-hud-progress-cache: #9ccbff;
        --codex-usage-hud-progress-cache-end: #5ea7ff;
        --codex-usage-hud-progress-cache-text: #07131f;
        --codex-usage-hud-progress-day: #f3d27a;
        --codex-usage-hud-progress-day-end: #f3d37f;
        --codex-usage-hud-progress-day-text: #111111;
        --codex-usage-hud-progress-week: #b5dd92;
        --codex-usage-hud-progress-week-end: #aede95;
        --codex-usage-hud-progress-week-text: #111111;
        --codex-usage-hud-progress-overflow: #ff875a;
        --codex-usage-hud-progress-overflow-highlight: #ffd8bd;
        --codex-usage-hud-progress-overflow-anchor: #ff6b64;
        --codex-usage-hud-progress-overflow-anchor-edge: #ffc3a4;
        --codex-usage-hud-progress-overflow-badge: #7f3e3a;
        --codex-usage-hud-progress-overflow-badge-edge: #ff875a;
        --codex-usage-hud-progress-overflow-badge-text: #ffd7ca;
        position: fixed;
        inset: 0;
        z-index: 2147482600;
        pointer-events: none;
        user-select: none;
        color-scheme: dark;
        font-family: "Microsoft YaHei UI", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      #${rootId}[data-hud-ready="false"] .codex-usage-hud-panel {
        display: none !important;
      }
      #${rootId} .codex-usage-hud-startup-bubble {
        position: fixed;
        top: 72px;
        right: 18px;
        bottom: auto;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        width: 196px;
        min-height: 146px;
        padding: 15px 16px 14px;
        border: 1px solid rgba(243, 210, 122, .52);
        border-radius: 12px;
        background: linear-gradient(145deg, rgba(32, 40, 51, .96), rgba(16, 22, 29, .96));
        box-shadow: 0 16px 38px rgba(0, 0, 0, .34), inset 0 1px 0 rgba(255,255,255,.06);
        color: var(--codex-usage-hud-text, #e8eef7);
        text-align: center;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-startup-bubble[hidden] {
        display: none !important;
      }
      #${rootId} .codex-usage-hud-startup-bubble::before {
        content: "";
        width: 30px;
        height: 30px;
        margin-bottom: 8px;
        border: 3px solid rgba(243, 210, 122, .24);
        border-top-color: var(--codex-usage-hud-accent, #f3d27a);
        border-radius: 50%;
        animation: codex-usage-hud-startup-spin .85s linear infinite;
      }
      #${rootId} .codex-usage-hud-startup-step {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: .04em;
      }
      #${rootId} .codex-usage-hud-startup-title {
        color: var(--codex-usage-hud-accent, #f3d27a);
        font-size: 12px;
        font-weight: 800;
        line-height: 1.25;
      }
      #${rootId} .codex-usage-hud-startup-detail {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        line-height: 1.35;
      }
      #${rootId} .codex-usage-hud-startup-progress-track {
        width: 100%;
        height: 6px;
        margin-top: 11px;
        overflow: hidden;
        border-radius: 999px;
        background: rgba(255,255,255,.10);
      }
      #${rootId} .codex-usage-hud-startup-progress-fill {
        width: 0%;
        height: 100%;
        border-radius: inherit;
        background: linear-gradient(90deg, var(--codex-usage-hud-accent, #f3d27a), #ffe7a0);
        transition: width .22s ease;
      }
      #${rootId} .codex-usage-hud-startup-progress-label {
        align-self: flex-end;
        margin-top: 5px;
        color: var(--codex-usage-hud-muted, #8492a6);
        font: 700 10px Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      @keyframes codex-usage-hud-startup-spin {
        to { transform: rotate(360deg); }
      }
      #${rootId} .codex-usage-hud-panel {
        position: fixed;
        box-sizing: border-box;
        border: 1px solid rgba(140, 153, 174, .24);
        border-radius: 7px;
        color: #e8eef7;
        box-shadow: 0 10px 28px rgba(0, 0, 0, .24);
        backdrop-filter: blur(12px);
        pointer-events: none;
        overflow: hidden;
        letter-spacing: 0;
        -webkit-app-region: no-drag;
      }
      #${rootId} .codex-usage-hud-panel * {
        -webkit-app-region: no-drag;
      }
      #${rootId} .codex-usage-hud-handle,
      #${rootId} .codex-usage-hud-update-button,
      #${rootId} .codex-usage-hud-settings-button,
      #${rootId} .codex-usage-hud-background-notification,
      #${rootId} .codex-usage-hud-resize-zone,
      #${rootId} .codex-usage-hud-main,
      #${rootId} .codex-usage-hud-panel-header,
      #${rootId} .codex-usage-hud-top-body,
      #${rootId} .codex-usage-hud-request-list,
      #${rootId} .codex-usage-hud-settings-modal,
      #${rootId} .codex-usage-hud-rest-mask,
      #${rootId} .codex-usage-hud-rest-toast,
      #${rootId} .codex-usage-hud-rest-bubble {
        pointer-events: auto;
      }
      #${rootId} .${topClass} {
        background: rgba(16, 22, 29, .94);
      }
      #${rootId} .${requestClass} {
        background: rgba(11, 16, 22, .92);
      }
      #${rootId} .codex-usage-hud-collapsed,
      #${rootId} .codex-usage-hud-expanded-shell {
        box-sizing: border-box;
        width: 100%;
        height: 100%;
      }
      #${rootId} .codex-usage-hud-collapsed {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        align-items: center;
        gap: 6px;
        padding: 4px 7px 4px 8px;
      }
      #${rootId} .codex-usage-hud-collapsed[data-has-settings="true"] {
        grid-template-columns: auto minmax(0, 1fr) 22px;
      }
      #${rootId} .codex-usage-hud-expanded-shell {
        display: none;
        position: relative;
        grid-template-rows: auto minmax(0, 1fr);
        padding: 5px 8px 8px;
      }
      #${rootId} .codex-usage-hud-panel[data-expanded="true"] .codex-usage-hud-collapsed {
        display: none;
      }
      #${rootId} .codex-usage-hud-panel[data-expanded="true"] .codex-usage-hud-expanded-shell {
        display: grid;
      }
      #${rootId} .${requestClass} .codex-usage-hud-expanded-shell {
        grid-template-rows: auto minmax(0, 1fr) auto;
      }
      #${rootId} button {
        font: inherit;
      }
      #${rootId} .codex-usage-hud-left-controls {
        min-width: 0;
        display: inline-flex;
        align-items: center;
        gap: 4px;
        flex: 0 0 auto;
      }
      #${rootId} .${requestClass} .codex-usage-hud-panel-header .codex-usage-hud-left-controls {
        /* Keep the handle + connection light from stealing the totals column. */
        min-width: auto;
      }
      #${rootId} .${requestClass} .codex-usage-hud-panel-header .codex-usage-hud-title {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-connection-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex: 0 0 auto;
        background: #3ecf8e;
        box-shadow: 0 0 0 0 rgba(62, 207, 142, .55);
        animation: codex-usage-hud-connection-breathe 1.4s ease-out infinite;
      }
      #${rootId} .codex-usage-hud-connection-dot[data-state="recovering"] {
        background: #ffb86b;
        box-shadow: 0 0 0 0 rgba(255, 184, 107, .55);
        animation-name: codex-usage-hud-connection-breathe-warn;
      }
      #${rootId} .codex-usage-hud-connection-dot[data-state="failed"] {
        background: #ff6b6b;
        box-shadow: 0 0 0 0 rgba(255, 107, 107, .55);
        animation-name: codex-usage-hud-connection-breathe-error;
      }
      @keyframes codex-usage-hud-connection-breathe {
        0% { box-shadow: 0 0 0 0 rgba(62, 207, 142, .55); }
        70% { box-shadow: 0 0 0 7px rgba(62, 207, 142, 0); }
        100% { box-shadow: 0 0 0 0 rgba(62, 207, 142, 0); }
      }
      @keyframes codex-usage-hud-connection-breathe-warn {
        0% { box-shadow: 0 0 0 0 rgba(255, 184, 107, .55); }
        70% { box-shadow: 0 0 0 7px rgba(255, 184, 107, 0); }
        100% { box-shadow: 0 0 0 0 rgba(255, 184, 107, 0); }
      }
      @keyframes codex-usage-hud-connection-breathe-error {
        0% { box-shadow: 0 0 0 0 rgba(255, 107, 107, .55); }
        70% { box-shadow: 0 0 0 7px rgba(255, 107, 107, 0); }
        100% { box-shadow: 0 0 0 0 rgba(255, 107, 107, 0); }
      }
      #${rootId} .codex-usage-hud-collapsed[data-has-settings="false"] {
        grid-template-columns: minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-collapsed[data-has-badge="true"] {
        grid-template-columns: minmax(0, 1fr) auto;
      }
      #${rootId} .${requestClass} .codex-usage-hud-collapsed {
        grid-template-columns: auto minmax(0, 1fr) 22px;
      }
      #${rootId} .${requestClass} .codex-usage-hud-collapsed[data-has-badge="true"] {
        grid-template-columns: auto minmax(0, 1fr) auto 22px;
      }
      #${rootId} .codex-usage-hud-token-badge {
        position: relative;
        display: none;
        align-items: center;
        box-sizing: border-box;
        max-width: 160px;
        height: 20px;
        padding: 0 8px;
        border: 1px solid rgba(156, 203, 255, .28);
        border-radius: 999px;
        background: rgba(156, 203, 255, .12);
        color: #9ccbff;
        font: 700 11px/1 Consolas, "Cascadia Mono", ui-monospace, monospace;
        white-space: nowrap;
        cursor: default;
        pointer-events: auto;
      }
      #${rootId} .codex-usage-hud-token-badge[data-composer-badge="active"] {
        display: inline-flex;
      }
      #${rootId} .codex-usage-hud-token-badge[data-badge-state="warning"] {
        display: inline-flex;
        max-width: 240px;
        border-color: rgba(255, 196, 84, .55);
        background: rgba(255, 196, 84, .16);
        color: #ffca54;
        animation: codex-usage-hud-badge-pulse 1.4s ease-in-out infinite;
      }
      @keyframes codex-usage-hud-badge-pulse {
        0%, 100% { opacity: .62; }
        50% { opacity: 1; }
      }
      #${rootId} .codex-usage-hud-token-badge-text {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-token-breakdown {
        position: fixed !important;
        z-index: 2147483647 !important;
        display: block !important;
        width: max-content !important;
        min-width: 208px !important;
        max-width: 340px !important;
        box-sizing: border-box !important;
        margin: 0 !important;
        padding: 8px 11px !important;
        border: 1px solid rgba(156, 203, 255, .35) !important;
        border-radius: 9px !important;
        background: rgba(20, 26, 34, .98) !important;
        box-shadow: 0 8px 24px rgba(0, 0, 0, .55) !important;
        color: #c7d4e4 !important;
        font: 600 10.5px/1.55 Consolas, "Cascadia Mono", ui-monospace, monospace !important;
        white-space: nowrap !important;
        pointer-events: none !important;
        opacity: 0;
        transform: translateY(4px);
        transition: opacity .12s ease, transform .12s ease;
      }
      #${rootId} .codex-usage-hud-token-breakdown[hidden] {
        display: none !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown[data-open="true"] {
        opacity: 1;
        transform: translateY(0);
      }
      /* 全量样式重置 + !important，彻底隔离 Codex 页面样式污染 */
      #${rootId} .codex-usage-hud-token-breakdown > * {
        box-sizing: border-box !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        min-width: 0 !important;
        max-width: none !important;
        float: none !important;
        position: static !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-title {
        display: flex !important;
        flex-direction: row !important;
        align-items: baseline !important;
        justify-content: space-between !important;
        gap: 18px !important;
        width: 100% !important;
        color: #9ccbff !important;
        font: 700 11px/1.35 Consolas, "Cascadia Mono", ui-monospace, monospace !important;
        margin: 0 0 6px !important;
        padding: 0 0 5px !important;
        border-bottom: 1px solid rgba(156, 203, 255, .22) !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-title-cost {
        color: #7fd7a6 !important;
        font-weight: 700 !important;
        font-variant-numeric: tabular-nums !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-row {
        display: flex !important;
        flex-direction: row !important;
        align-items: baseline !important;
        column-gap: 12px !important;
        width: 100% !important;
        line-height: 1.6 !important;
        text-align: left !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-row[data-total="true"] {
        margin-top: 4px !important;
        padding-top: 5px !important;
        border-top: 1px solid rgba(156, 203, 255, .25) !important;
        color: #9ccbff !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-name {
        flex: 1 1 auto !important;
        color: #b8c4d4 !important;
        font-weight: 600 !important;
        line-height: inherit !important;
        text-align: left !important;
        white-space: nowrap !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-note {
        margin-left: 6px !important;
        color: #6f7d90 !important;
        font-size: 9.5px !important;
        font-weight: 600 !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-row[data-total="true"] .codex-usage-hud-token-breakdown-name {
        color: #9ccbff !important;
        font-weight: 700 !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-tok {
        flex: 0 0 auto !important;
        margin-left: auto !important;
        color: #8492a6 !important;
        font-variant-numeric: tabular-nums !important;
        line-height: inherit !important;
        text-align: right !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-cost {
        flex: 0 0 auto !important;
        min-width: 54px !important;
        color: #7fd7a6 !important;
        font-variant-numeric: tabular-nums !important;
        line-height: inherit !important;
        text-align: right !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-row[data-total="true"] .codex-usage-hud-token-breakdown-cost {
        font-weight: 700 !important;
      }
      #${rootId} .codex-usage-hud-handle,
      #${rootId} .codex-usage-hud-update-button,
      #${rootId} .codex-usage-hud-settings-button,
      #${rootId} .codex-usage-hud-background-notification {
        display: inline-grid;
        place-items: center;
        box-sizing: border-box;
        width: 18px;
        height: 18px;
        border: 0;
        border-radius: 4px;
        padding: 0;
        background: rgba(46, 56, 70, .78);
        color: #c7d4e4;
        font: 700 11px/1 "Segoe UI Symbol", "Microsoft YaHei UI", sans-serif;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-background-notification {
        position: relative;
        width: 22px;
        height: 22px;
        color: var(--codex-usage-hud-info, #9ccbff);
        font-size: 13px;
        visibility: hidden;
        opacity: 0;
        pointer-events: none;
        transition: opacity .12s ease;
      }
      #${rootId} .codex-usage-hud-background-notification[data-visible="true"] {
        visibility: visible;
        opacity: 1;
        pointer-events: auto;
      }
      #${rootId} .codex-usage-hud-background-notification:hover {
        background: rgba(156, 203, 255, .16);
        color: #c8e3ff;
      }
      #${rootId} .codex-usage-hud-background-notification-count {
        position: absolute;
        top: -5px;
        right: -5px;
        min-width: 13px;
        height: 13px;
        display: inline-grid;
        place-items: center;
        box-sizing: border-box;
        padding: 0 3px;
        border: 1px solid var(--codex-usage-hud-request-surface, #0b1016);
        border-radius: 7px;
        background: var(--codex-usage-hud-error, #ff6b6b);
        color: #fff;
        font: 700 8px/1 Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-variant-numeric: tabular-nums;
      }
      #${rootId} .codex-usage-hud-background-notification-count[hidden] {
        display: none;
      }
      #${rootId} .codex-usage-hud-handle {
        cursor: grab;
        color: #8492a6;
      }
      #${rootId} .codex-usage-hud-handle:active {
        cursor: grabbing;
      }
      #${rootId} .codex-usage-hud-resize-zone {
        position: absolute;
        z-index: 5;
        background: transparent;
      }
      #${rootId} .codex-usage-hud-resize-edge-left {
        top: 0;
        left: 0;
        bottom: 0;
        width: 6px;
        cursor: ew-resize;
      }
      #${rootId} .codex-usage-hud-resize-edge-right {
        top: 0;
        right: 0;
        bottom: 0;
        width: 6px;
        cursor: ew-resize;
      }
      #${rootId} .codex-usage-hud-resize-corner {
        width: 12px;
        height: 12px;
      }
      #${rootId} .codex-usage-hud-resize-corner-top-left {
        top: 0;
        left: 0;
        cursor: nwse-resize;
      }
      #${rootId} .codex-usage-hud-resize-corner-top-right {
        top: 0;
        right: 0;
        cursor: nesw-resize;
      }
      #${rootId} .codex-usage-hud-resize-corner-bottom-left {
        left: 0;
        bottom: 0;
        cursor: nesw-resize;
      }
      #${rootId} .codex-usage-hud-resize-corner-bottom-right {
        right: 0;
        bottom: 0;
        cursor: nwse-resize;
      }
      #${rootId} .codex-usage-hud-update-button {
        background: rgba(46, 56, 70, .82);
        color: #9ccbff;
      }
      #${rootId} .codex-usage-hud-update-button[data-state="paused"],
      #${rootId} .codex-usage-hud-update-button[data-state="error"] {
        color: #ffb86b;
      }
      #${rootId} .codex-usage-hud-update-button[data-icon="install"] {
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-settings-button {
        width: 22px;
        height: 22px;
        border-radius: 5px;
        background: transparent;
        color: #a9bcd2;
        font-size: 13px;
      }
      #${rootId} .codex-usage-hud-handle:hover {
        background: rgba(62, 74, 92, .92);
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-update-button:hover,
      #${rootId} .codex-usage-hud-settings-button:hover {
        background: rgba(62, 74, 92, .92);
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-main {
        min-width: 0;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        align-items: center;
        gap: 8px;
        height: 100%;
        padding: 0;
        border: 0;
        background: transparent;
        color: inherit;
        text-align: left;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-main[data-has-glyph="false"] {
        grid-template-columns: minmax(0, 1fr);
        gap: 0;
      }
      #${rootId} .${topClass} .codex-usage-hud-main[data-progress="true"] {
        grid-template-columns: minmax(0, 1fr);
      }
      #${rootId} .${topClass} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-glyph,
      #${rootId} .${topClass} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-line {
        display: none;
      }
      #${rootId} .codex-usage-hud-progress-strip-viewport {
        min-width: 0;
        width: 100%;
        display: none;
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-progress-strip {
        min-width: 0;
        width: 100%;
        height: 26px;
        display: none;
        grid-template-columns: max-content minmax(0, 1fr) minmax(0, 1fr);
        gap: 7px;
        align-items: center;
        transform: translateX(0);
        will-change: transform;
      }
      #${rootId} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-progress-strip-viewport,
      #${rootId} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-progress-strip {
        display: block;
      }
      #${rootId} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-progress-strip {
        display: grid;
      }
      #${rootId} .codex-usage-hud-progress-strip[data-count="1"] {
        grid-template-columns: minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-progress-strip[data-overflow="true"] {
        width: max-content;
        min-width: max-content;
        animation-name: codex-usage-hud-progress-strip-marquee;
        animation-duration: var(--codex-usage-hud-progress-strip-duration, 6000ms);
        animation-timing-function: linear;
        animation-iteration-count: infinite;
      }
      @keyframes codex-usage-hud-progress-strip-marquee {
        0%, 22% {
          transform: translateX(0);
        }
        48%, 70% {
          transform: translateX(calc(var(--codex-usage-hud-progress-strip-distance, 0px) * -1));
        }
        100% {
          transform: translateX(0);
        }
      }
      #${rootId} .codex-usage-hud-progress-strip:not([data-count="1"]) .codex-usage-hud-progress-rail:first-child {
        justify-self: start;
        width: max-content;
        max-width: 100%;
      }
      #${rootId} .codex-usage-hud-progress-rail {
        position: relative;
        display: block;
        width: 100%;
        min-width: 0;
        height: 100%;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,.06);
        background: linear-gradient(180deg, rgba(255,255,255,.032), rgba(255,255,255,0)), #111822;
        overflow: hidden;
        box-shadow: inset 0 1px 0 rgba(255,255,255,.04), inset 0 -8px 16px rgba(0,0,0,.16);
      }
      #${rootId} .codex-usage-hud-progress-track-text {
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        min-width: 0;
        padding: 0 11px;
        overflow: hidden;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0;
      }
      #${rootId} .codex-usage-hud-progress-text {
        min-width: 0;
        flex: 1 1 auto;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-progress-right-text {
        flex: 0 0 auto;
        max-width: 42%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-progress-probe-text {
        flex: 0 0 auto;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-progress-size-probe {
        box-sizing: border-box;
        display: flex;
        align-items: center;
        height: 0;
        overflow: hidden;
        padding: 0 11px;
        visibility: hidden;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-strip .codex-usage-hud-progress-track-text {
        padding: 0 10px;
        font-size: 11px;
      }
      #${rootId} .codex-usage-hud-progress-strip .codex-usage-hud-progress-size-probe {
        padding: 0 10px;
        font-size: 11px;
      }
      #${rootId} .codex-usage-hud-progress-track-text {
        /* Keep labels above the overflow sub-rail and its anchor. */
        z-index: 6;
        color: #ffffff;
        mix-blend-mode: difference;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-fill {
        position: absolute;
        inset: 0 auto 0 0;
        min-width: 0;
        max-width: 100%;
        z-index: 2;
        border-radius: inherit;
        overflow: hidden;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-fill::before {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(180deg, rgba(255,255,255,.26), rgba(255,255,255,.07) 28%, rgba(255,255,255,0) 52%);
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-fill::after {
        content: "";
        position: absolute;
        top: 4px;
        right: 7px;
        width: min(64px, 42%);
        height: calc(100% - 8px);
        border-radius: inherit;
        background: linear-gradient(90deg, rgba(255,255,255,0), rgba(255,255,255,.18));
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-rail[data-overflow="true"] {
        border-color: rgba(255,136,92,.18);
      }
      #${rootId} .codex-usage-hud-progress-rail[data-overflow="true"] .codex-usage-hud-progress-track-text,
      #${rootId} .codex-usage-hud-progress-rail[data-overflow="true"] .codex-usage-hud-progress-size-probe {
        padding-right: 28px;
      }
      #${rootId} .codex-usage-hud-progress-rail[data-badge="true"] .codex-usage-hud-progress-track-text,
      #${rootId} .codex-usage-hud-progress-rail[data-badge="true"] .codex-usage-hud-progress-size-probe {
        padding-right: var(--codex-usage-hud-progress-badge-pad, 96px);
      }
      #${rootId} .codex-usage-hud-progress-overflow {
        position: absolute;
        right: 0;
        bottom: 0;
        height: 4px;
        border-radius: 4px 0 0 0;
        background: linear-gradient(90deg, #ffcfaa, #ff875a 60%, #ff5b64);
        box-shadow: 0 6px 14px rgba(255,91,100,.14);
        z-index: 3;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-overflow::before {
        content: "";
        position: absolute;
        inset: 0;
        border-radius: inherit;
        background: linear-gradient(180deg, rgba(255,255,255,.28), rgba(255,255,255,0) 70%);
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-overflow-anchor {
        position: absolute;
        right: max(0px, calc(var(--codex-usage-hud-progress-overflow-width, 0%) - 3px));
        bottom: 0;
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: radial-gradient(circle at 35% 35%, #fff4d9 0%, #ff8e61 58%, #ff5b64 100%);
        box-shadow: 0 0 0 1px rgba(255,107,99,.12), 0 0 8px rgba(255,107,99,.28);
        z-index: 4;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-badge {
        position: absolute;
        top: 2px;
        right: 6px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 3px;
        min-height: 16px;
        padding: 0 7px;
        border-radius: 999px;
        border: 1px solid rgba(255,132,88,.24);
        background: rgba(255,95,92,.12);
        color: #ffd7ca;
        font-size: 9.5px;
        font-weight: 800;
        letter-spacing: 0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        box-shadow: 0 4px 10px rgba(255,91,100,.1);
        backdrop-filter: blur(10px);
        z-index: 5;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-badge-icon {
        flex: 0 0 auto;
        font-size: .9em;
        line-height: 1;
      }
      #${rootId} .codex-usage-hud-progress-badge-copy {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      #${rootId} .codex-usage-hud-progress-strip .codex-usage-hud-progress-badge {
        top: 2px;
        right: 5px;
        min-height: 15px;
        padding: 0 6px;
        font-size: 9px;
      }
      #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-overflow {
        right: 0;
        bottom: 0;
        height: 4px;
      }
      #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-overflow-anchor {
        right: max(0px, calc(var(--codex-usage-hud-progress-overflow-width, 0%) - 3px));
        bottom: 0;
        width: 7px;
        height: 7px;
      }
      #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-badge {
        top: 3px;
        right: 7px;
        min-height: 17px;
        padding: 0 8px;
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="cache"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, #9ccbff, #5ea7ff);
        box-shadow: 0 10px 22px rgba(94,167,255,.18);
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="session"] {
        border-color: rgba(156,203,255,.14);
        background: linear-gradient(180deg, rgba(156,203,255,.07), rgba(255,255,255,0)), #111822;
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="day"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, var(--codex-usage-hud-progress-day), var(--codex-usage-hud-progress-day-end));
        box-shadow: 0 10px 22px color-mix(in srgb, var(--codex-usage-hud-progress-day-end) 18%, transparent);
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="week"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, var(--codex-usage-hud-progress-week), var(--codex-usage-hud-progress-week-end));
        box-shadow: 0 10px 22px color-mix(in srgb, var(--codex-usage-hud-progress-week-end) 18%, transparent);
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="error"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, #ff8a8a, #ff6b6b);
      }
      #${rootId} .codex-usage-hud-glyph {
        display: inline-grid;
        place-items: center;
        width: 18px;
        height: 18px;
        border-radius: 999px;
        background: rgba(243, 210, 122, .16);
        color: #f3d27a;
        font: 700 12px ui-monospace, "Cascadia Mono", Consolas, monospace;
      }
      #${rootId} .codex-usage-hud-line {
        position: relative;
        min-width: 0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0;
      }
      #${rootId} .codex-usage-hud-line-inner {
        display: inline-block;
        max-width: none;
        white-space: nowrap;
        transform: translateX(0);
        will-change: transform;
      }
      #${rootId} .codex-usage-hud-line[data-marquee="true"] .codex-usage-hud-line-inner {
        animation-name: codex-usage-hud-marquee;
        animation-duration: var(--codex-usage-hud-marquee-duration, 6000ms);
        animation-timing-function: linear;
        animation-iteration-count: infinite;
      }
      @keyframes codex-usage-hud-marquee {
        0%, 22% {
          transform: translateX(0);
        }
        48%, 70% {
          transform: translateX(calc(var(--codex-usage-hud-marquee-distance, 0px) * -1));
        }
        100% {
          transform: translateX(0);
        }
      }
      #${rootId} .${requestClass} .codex-usage-hud-line {
        color: #f3d27a;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 13px;
      }
      #${rootId} .codex-usage-hud-panel-header {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        align-items: center;
        gap: 6px;
        min-height: 27px;
        margin-bottom: 7px;
        padding: 2px 5px;
        border-radius: 5px;
        background: #202833;
      }
      #${rootId} .codex-usage-hud-panel-header[data-action="toggle"] {
        cursor: pointer;
      }
      #${rootId} .${topClass} .codex-usage-hud-panel-header {
        grid-template-columns: auto minmax(0, auto) minmax(0, 1fr) minmax(90px, 150px) 22px;
      }
      #${rootId} .${requestClass} .codex-usage-hud-panel-header {
        grid-template-columns: auto minmax(0, 1fr) 22px;
        background: #151d27;
        margin-top: 4px;
        margin-bottom: 0;
      }
      #${rootId} .codex-usage-hud-title {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #e8eef7;
        font-size: 12px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-session-meta {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #8492a6;
        text-align: right;
        font-size: 10.5px;
      }
      #${rootId} .codex-usage-hud-top-body {
        min-height: 0;
        overflow: auto;
        scrollbar-width: thin;
        scrollbar-color: #273241 #10161d;
      }
      #${rootId} .codex-usage-hud-top-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        gap: 12px;
        min-height: 100%;
        align-items: stretch;
      }
      #${rootId} .${topClass} {
        container-type: inline-size;
      }
      #${rootId} .codex-usage-hud-top-column {
        min-width: 0;
        min-height: 0;
        height: 100%;
        display: grid;
        gap: 12px;
        align-content: stretch;
      }
      #${rootId} .codex-usage-hud-top-column-left {
        grid-template-rows: auto auto minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-top-column-right {
        grid-template-rows: minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-top-card,
      #${rootId} .codex-usage-hud-alert {
        box-sizing: border-box;
        min-width: 0;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 7px;
        background: #141b24;
        padding: 10px 12px;
      }
      #${rootId} .codex-usage-hud-card-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        min-height: 18px;
        margin-bottom: 9px;
      }
      #${rootId} .codex-usage-hud-card-title {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #dce7f2;
        font-size: 12px;
        font-weight: 800;
      }
      #${rootId} .codex-usage-hud-card-actions {
        min-width: 0;
        display: inline-flex;
        align-items: center;
        justify-content: flex-end;
        gap: 6px;
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-chip {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 0;
        min-height: 20px;
        padding: 2px 9px;
        border-radius: 999px;
        border: 1px solid rgba(132, 146, 166, .20);
        background: #1c2330;
        color: #dce7f2;
        font-size: 10.5px;
        font-weight: 800;
        line-height: 1.2;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-chip:empty {
        display: none;
      }
      #${rootId} .codex-usage-hud-copy-chip {
        max-width: 116px;
        overflow: hidden;
        text-overflow: ellipsis;
        cursor: default;
      }
      #${rootId} .codex-usage-hud-copy-chip[data-copyable="true"] {
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-copy-chip[data-copyable="true"]:hover {
        border-color: rgba(243, 210, 122, .34);
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-chip[data-tone="warning"] {
        border-color: rgba(255, 184, 107, .34);
        background: rgba(36, 27, 16, .88);
        color: #ffb86b;
      }
      #${rootId} .codex-usage-hud-session-stats {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        align-items: stretch;
      }
      #${rootId} .codex-usage-hud-session-stat {
        min-width: 0;
        display: grid;
        align-content: center;
        min-height: 48px;
      }
      #${rootId} .codex-usage-hud-stat-value {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #e8eef7;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 24px;
        font-weight: 800;
        line-height: 1.08;
      }
      #${rootId} .codex-usage-hud-stat-value.info {
        color: #9ccbff;
      }
      #${rootId} .codex-usage-hud-stat-value.cache {
        color: #8fe3a1;
      }
      #${rootId} .codex-usage-hud-stat-label {
        margin-top: 3px;
        color: #8492a6;
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-session-insight {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        align-items: center;
        gap: 10px;
        min-height: 32px;
        margin-top: 11px;
        padding: 5px 10px;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 6px;
        background: #101821;
      }
      #${rootId} .codex-usage-hud-session-insight .codex-usage-hud-value:last-child {
        text-align: right;
      }
      #${rootId} .codex-usage-hud-token-breakdown {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 5px;
        margin-top: 7px;
      }
      #${rootId} .codex-usage-hud-session-composition {
        min-width: 0;
        display: block;
        margin-top: 7px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #b8c6d8;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 10.5px;
        line-height: 1.35;
      }
      #${rootId} .codex-usage-hud-session-composition:empty {
        display: none;
      }
      #${rootId} .codex-usage-hud-heavy-rounds-card {
        min-height: 150px;
        display: grid;
        grid-template-rows: auto minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-heavy-rounds {
        min-width: 0;
        min-height: 0;
        display: grid;
        grid-template-rows: repeat(3, minmax(32px, 1fr));
        align-content: start;
        gap: 6px;
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-heavy-rounds[data-empty="true"] {
        align-content: stretch;
      }
      #${rootId} .codex-usage-hud-heavy-round {
        min-width: 0;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        align-items: center;
        gap: 8px;
        padding: 6px 8px;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 5px;
        background: #101821;
      }
      #${rootId} .codex-usage-hud-heavy-round[data-placeholder="true"] {
        grid-template-columns: minmax(0, 1fr);
        align-content: center;
        opacity: .72;
      }
      #${rootId} .codex-usage-hud-heavy-round[data-copyable="true"] {
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-heavy-round[data-copyable="true"]:hover {
        border-color: rgba(243, 210, 122, .30);
        background: rgba(243, 210, 122, .06);
      }
      #${rootId} .codex-usage-hud-heavy-round[data-rolled-back="true"] {
        border-style: dashed;
        opacity: .78;
      }
      #${rootId} .codex-usage-hud-heavy-round[data-rolled-back="true"] .codex-usage-hud-heavy-round-title {
        color: #b7a06a;
      }
      #${rootId} .codex-usage-hud-heavy-round-title,
      #${rootId} .codex-usage-hud-heavy-round-detail {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-heavy-round-title {
        color: #f3d27a;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 10.5px;
        font-weight: 800;
      }
      #${rootId} .codex-usage-hud-heavy-round-detail {
        color: #8492a6;
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-token-chip {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        padding: 5px 7px;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 5px;
        background: #101821;
        color: #b8c6d8;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 10.5px;
      }
      #${rootId} .codex-usage-hud-token-chip span:first-child {
        margin-right: 5px;
        color: #718095;
        font-family: "Microsoft YaHei UI", system-ui, sans-serif;
      }
      #${rootId} .codex-usage-hud-alert {
        display: grid;
        grid-template-columns: auto auto minmax(0, 1fr) auto;
        gap: 8px;
        align-items: center;
        margin-bottom: 12px;
        padding: 8px 10px;
        border-color: rgba(255, 135, 90, .46);
        background: rgba(36, 24, 16, .90);
"""

TEXT = HEAD + SHARED_HEAD + STYLE_HEAD

__all__ = ["HEAD", "SHARED_HEAD", "STYLE_HEAD", "TEXT"]
