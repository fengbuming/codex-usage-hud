"""Remaining renderer asset during P6 domain migration."""

TEXT = r"""
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  const SESSION_JUMP_INFLIGHT_TTL_MS = 1500;
  const SESSION_JUMP_INFLIGHT_ATTR = "data-session-cleanup-jump-inflight";

  function setSessionCleanupJumpInflightAttributes(itemId, kind, enabled) {
    if (!itemId) return;
    const selector = `[data-session-cleanup-jump-id="${CSS.escape(itemId)}"]`;
    for (const node of document.querySelectorAll(selector)) {
      const button = node;
      if (!(button instanceof HTMLButtonElement)) continue;
      if (kind) {
        button.dataset.kind = kind;
      }
      if (enabled) {
        button.disabled = true;
        button.classList.add("codex-usage-hud-session-jump-inflight");
        button.setAttribute(SESSION_JUMP_INFLIGHT_ATTR, "true");
      } else {
        button.disabled = false;
        button.classList.remove("codex-usage-hud-session-jump-inflight");
        button.removeAttribute(SESSION_JUMP_INFLIGHT_ATTR);
      }
    }
  }

  function markSessionCleanupJumpInflight(itemId, kind) {
    if (!itemId || typeof window === "undefined") return;
    setSessionCleanupJumpInflightAttributes(itemId, kind, true);
    const previousToken = window.__codexUsageHudSessionJumpInflightTokens?.[itemId] || 0;
    const token = previousToken + 1;
    window.__codexUsageHudSessionJumpInflightTokens = window.__codexUsageHudSessionJumpInflightTokens || {};
    window.__codexUsageHudSessionJumpInflightTokens[itemId] = token;
    const clear = () => {
      const current = window.__codexUsageHudSessionJumpInflightTokens?.[itemId];
      if (current !== token) return;
      setSessionCleanupJumpInflightAttributes(itemId, "", false);
    };
    setTimeout(clear, SESSION_JUMP_INFLIGHT_TTL_MS);
  }

  function currentPayload() {
    const payload = ctx.state.payload();
    if (Array.isArray(payload.supportImages) && payload.supportImages.length) {
      return payload;
    }
    const persistedSupportImages = loadPersistedSupportImages();
    if (!persistedSupportImages.length) return payload;
    return { ...payload, supportImages: persistedSupportImages };
  }

  function ensureRoot() {
    ensureStyle();
    let root = document.getElementById(rootId);
    if (
      root?.dataset.version === version
      && root?.dataset.bundleFingerprint === bundleFingerprint
    ) {
      // A renderer script can be reinjected while the page is still alive.
      // The previous runtime has already disposed its delegated listeners, so
      // keep the existing DOM (including settings dialogs and their drafts)
      // but bind it to this runtime again.
      if (root.dataset.bound !== "true") {
        applyPanelStates(root);
        bindRoot(root);
      }
      return root;
    }
    if (!document.body) return null;
    root?.remove();
    root = document.createElement("div");
    root.id = rootId;
    root.dataset.version = version;
    root.dataset.bundleFingerprint = bundleFingerprint;
    root.dataset.hudReady = "false";
    root.innerHTML = `
      <div class="codex-usage-hud-startup-bubble" data-field="startupBubble" role="status" aria-live="polite" hidden>
        <div class="codex-usage-hud-startup-step" data-field="startupStep"></div>
        <div class="codex-usage-hud-startup-title" data-field="startupTitle"></div>
        <div class="codex-usage-hud-startup-detail" data-field="startupDetail"></div>
        <div class="codex-usage-hud-startup-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" data-field="startupProgressTrack">
          <div class="codex-usage-hud-startup-progress-fill" data-field="startupProgressFill"></div>
        </div>
        <div class="codex-usage-hud-startup-progress-label" data-field="startupProgressLabel"></div>
      </div>
    ` + panelMarkup("top", "", "展开顶部 HUD") + panelMarkup("request", "", "展开请求 HUD") + settingsChromeMarkup();
    document.body.appendChild(root);
    applyPanelStates(root);
    bindRoot(root);
    return root;
  }

  function loadStates() {
    try {
      const data = JSON.parse(ctx.storage.read(localStorage, storageKey, "{}"));
      const requestState = data && typeof data === "object" ? data.request : null;
      const staleFullComposerGeometry = requestState && (
        Number(requestState.widthRatio) >= 0.9
        || Number(requestState.width) >= Math.max(700, innerWidth * 0.9)
      );
      const orphanedManualRequest = requestState && requestState.manual === true
        && !Number.isFinite(Number(requestState.width))
        && !requestState.anchorSource;
      const needsFooterGapMigration = data && typeof data === "object"
        && requestState
        && !data.__footerGapV3;
      if (needsFooterGapMigration) {
        const request = { ...data.request };
        if (staleFullComposerGeometry || orphanedManualRequest) {
          // Discard only the old request geometry. Top HUD and expansion state
          // remain user-owned; the request panel will auto-fit the new footer
          // slot once, then subsequent drags are persisted normally.
          ["x", "y", "width", "xRatio", "yOffset", "bottomOffset", "widthRatio", "anchorSource", "manual"].forEach((key) => delete request[key]);
        }
        data.request = request;
        data.__footerGapV2 = true;
        data.__footerGapV3 = true;
        ctx.storage.write(localStorage, storageKey, JSON.stringify(data));
      }
      return data && typeof data === "object" ? data : {};
    } catch (_) {
      return {};
    }
  }

  function saveStates(states) {
    try {
      ctx.storage.write(localStorage, storageKey, JSON.stringify(states));
    } catch (_) {}
  }

  function clearSessionManualGeometry() {
    // Panel geometry is session-owned: the session switch is the one moment a
    // fresh automatic layout is computed, so the previous session's manual
    // drag/resize values must not survive in storage. Any later in-session
    // sync (panel toggle, anchor refresh) would otherwise resurrect those
    // stale coordinates against the new conversation geometry.
    const states = loadStates();
    let changed = false;
    for (const name of Object.keys(PANEL)) {
      const state = states[name];
      if (!state) continue;
      for (const key of [
        "manual",
        "x",
        "y",
        "width",
        "xRatio",
        "yOffset",
        "bottomOffset",
        "widthRatio",
        "anchorSource",
        "collapsedHeight",
        "expandedHeight",
      ]) {
        if (key in state) {
          delete state[key];
          changed = true;
        }
      }
    }
    if (changed) saveStates(states);
  }

  function getPanelState(name) {
    return { ...(loadStates()[name] || {}) };
  }

  function setPanelState(name, patch) {
    const states = loadStates();
    states[name] = { ...(states[name] || {}), ...patch };
    saveStates(states);
    return states[name];
  }

  function getRuntimeErrorsPanelState() {
    return { expanded: false, ...(loadStates().runtimeErrors || {}) };
  }

  function setRuntimeErrorsPanelState(patch) {
    const states = loadStates();
    states.runtimeErrors = { ...(states.runtimeErrors || {}), ...patch };
    saveStates(states);
    return states.runtimeErrors;
  }

  function applyPanelStates(root) {
    for (const name of Object.keys(PANEL)) {
      const panel = root.querySelector(`[data-panel="${name}"]`);
      const expanded = !!getPanelState(name).expanded;
      if (panel) panel.dataset.expanded = String(expanded);
    }
  }

  function bindRoot(root) {
    if (root.dataset.bound === "true") return;
    root.dataset.bound = "true";
    const rootScope = ctx.lifecycle.scope("root");
    // 悬浮徽章 → 展开 A/B/C/D/F 明细；移开 → 收起（事件委托，徽章重建也生效）。
    rootScope.listen(root, "mouseover", (event) => {
      if (event.target?.closest?.(".codex-usage-hud-token-badge")) {
        showComposerBreakdown(root);
      }
    });
    rootScope.listen(root, "mouseout", (event) => {
      const from = event.target?.closest?.(".codex-usage-hud-token-badge");
      if (from && !from.contains(event.relatedTarget)) {
        hideComposerBreakdown(root);
      }
    });
    rootScope.listen(root, "wheel", (event) => {
      const select = event.target?.closest?.(`#${settingsModalId} select[data-setting-key]`);
      if (!select || !root.contains(select)) return;
      event.preventDefault();
      event.stopPropagation();
      select.closest(".codex-usage-hud-settings-dialog")?.scrollBy({
        left: event.deltaX,
        top: event.deltaY,
      });
    }, { capture: true, passive: false });
    rootScope.listen(root, "scroll", (event) => {
      const tabs = event.target?.closest?.('[data-provider-tabs="true"]');
      if (!tabs || !root.contains(tabs)) return;
      syncSettingsProviderTabNavigation(tabs);
    }, { capture: true, passive: true });
    rootScope.listen(window, "resize", () => {
      syncSettingsProviderTabNavigation();
    }, { passive: true });
    rootScope.listen(root, "pointerdown", (event) => {
      const modelSelect = event.target?.closest?.('[data-codex-cli-field="model"]');
      if (!modelSelect || !root.contains(modelSelect)) return;
      requestCodexCliModels({ reopenPicker: true });
    });
    const sessionTransferModeInputFromEvent = (event) => {
      const direct = event.target?.closest?.('[data-session-transfer-mode]');
      if (direct) return direct;
      return event.target?.closest?.('.codex-usage-hud-session-transfer-mode label')
        ?.querySelector?.('[data-session-transfer-mode]') || null;
    };
    const syncSessionTransferModeFromEvent = (event) => {
      const input = sessionTransferModeInputFromEvent(event);
      if (!input || !document.contains(input)) return false;
      sessionTransferState.mode = sessionTransferModeValue(input);
      syncSessionTransferSubmitButton(sessionTransferState.mode);
      return true;
    };
    // The settings dialog may be mounted outside the HUD event root. Keep the
    // primary action label live for mouse, keyboard, and native radio changes.
    rootScope.listen(document, "click", (event) => {
      syncSessionTransferModeFromEvent(event);
    }, true);
    rootScope.listen(document, "change", (event) => {
      syncSessionTransferModeFromEvent(event);
    }, true);
    const commitSessionCleanupSearch = (sessionSearch) => {
      if (!sessionSearch || !root.contains(sessionSearch)) return false;
      const search = String(sessionSearch.value || "");
      if (
        search === sessionCleanupState.search
        && !sessionCleanupState.searchRequestId
        && !sessionCleanupSearchTimer
        && sessionCleanupState.searchResultQuery === search
      ) return false;
      sessionCleanupState.search = search;
      sessionCleanupState.searchDraft = search;
      sessionCleanupState.indexPanelHidden = false;
      sessionCleanupState.page = 0;
      sessionCleanupState.selectedIds.clear();
      persistSessionCleanupFilters();
      stopSessionCleanupSearchTimer();
      requestSessionCleanupSearch(search);
      return true;
    };
    const commitSessionTransferSearch = (sessionSearch) => {
      if (!sessionSearch || !root.contains(sessionSearch)) return false;
      const search = String(sessionSearch.value || "");
      if (search === sessionTransferState.search) return false;
      sessionTransferState.search = search;
      sessionTransferState.selectedIds.clear();
      sessionTransferState.page = 0;
      renderSessionTransferDialog();
      return true;
    };
    rootScope.listen(root, "keydown", (event) => {
      const sessionSearch = event.target?.closest?.('[data-session-cleanup-search="true"]');
      if (sessionSearch && root.contains(sessionSearch)) {
        if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
        event.preventDefault();
        commitSessionCleanupSearch(sessionSearch);
        return;
      }
      const sessionTransferSearch = event.target?.closest?.('[data-session-transfer-search="true"]');
      if (!sessionTransferSearch || root.contains(sessionTransferSearch) === false) return;
      if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      commitSessionTransferSearch(sessionTransferSearch);
    });
    rootScope.listen(root, "click", (event) => {
      const action = event.target?.closest?.('[data-action="session-cleanup-search-submit"]');
      if (action && root.contains(action)) {
        event.preventDefault();
        const input = action.closest(".codex-usage-hud-session-search")?.querySelector?.('[data-session-cleanup-search="true"]');
        commitSessionCleanupSearch(input);
        return;
      }
    });
    rootScope.listen(root, "focusout", (event) => {
      const sessionTransferSearch = event.target?.closest?.('[data-session-transfer-search="true"]');
      if (!sessionTransferSearch || root.contains(sessionTransferSearch) === false) return;
      commitSessionTransferSearch(sessionTransferSearch);
    });
    rootScope.listen(root, "input", (event) => {
      const codexCliField = event.target?.closest?.("[data-codex-cli-field]");
      if (codexCliField && root.contains(codexCliField)) {
        codexCliFieldInput(String(codexCliField.dataset.codexCliField || ""));
        return;
      }
      const sessionTransferMode = event.target?.closest?.('[data-session-transfer-mode]');
      if (sessionTransferMode && root.contains(sessionTransferMode)) {
        // Radio input events fire as soon as the native selection changes.
        // Update the action label without waiting for a renderer payload or a
        // later change event.
        sessionTransferState.mode = sessionTransferModeValue(sessionTransferMode);
        syncSessionTransferSubmitButton(sessionTransferState.mode);
        return;
      }
      const restStartInput = event.target?.closest?.('[data-rest-reminder-start-time="true"]');
      if (restStartInput && root.contains(restStartInput)) {
        restStartInput.dataset.userEdited = "true";
        return;
      }
      const sessionDateStart = event.target?.closest?.('[data-session-cleanup-date-start="true"]');
      if (sessionDateStart && root.contains(sessionDateStart)) {
        sessionCleanupState.dateDraftStart = String(sessionDateStart.value || "");
        return;
      }
      const sessionDateEnd = event.target?.closest?.('[data-session-cleanup-date-end="true"]');
      if (sessionDateEnd && root.contains(sessionDateEnd)) {
        sessionCleanupState.dateDraftEnd = String(sessionDateEnd.value || "");
        return;
      }
      const sessionSearch = event.target?.closest?.('[data-session-cleanup-search="true"]');
      if (sessionSearch && root.contains(sessionSearch)) {
        const search = String(sessionSearch.value || "");
        sessionCleanupState.searchDraft = search;
        // IME composition emits input events before the final text is committed.
        // Keep the input stable while composing; the final input event schedules
        // an asynchronous indexed query without replacing the DOM node.
        if (event.isComposing || sessionSearch.isComposing) return;
        // Keep an unsubmitted draft separate from the active query. Storage
        // progress updates rebuild the panel; rendering from this draft keeps
        // the user's text and caret intact while they are still typing.
        if (search === sessionCleanupState.search) return;
        sessionCleanupState.search = search;
        sessionCleanupState.indexPanelHidden = false;
        sessionCleanupState.page = 0;
        sessionCleanupState.selectedIds.clear();
        persistSessionCleanupFilters();
        stopSessionCleanupSearchTimer();
        sessionCleanupSearchTimer = ctx.lifecycle.timeout(
          "session_cleanup_search",
          () => {
            sessionCleanupSearchTimer = 0;
            requestSessionCleanupSearch(search);
          },
          220,
        );
        return;
      }
      const sessionTransferSearch = event.target?.closest?.('[data-session-transfer-search="true"]');
      if (sessionTransferSearch && root.contains(sessionTransferSearch)) {
        // Keep the input stable while an IME composition is in progress.
        return;
      }
      const priceRow = event.target?.closest?.('[data-price-row="true"]');
      if (priceRow && root.contains(priceRow)) markSettingsProviderDirty();
    });
    rootScope.listen(root, "focusout", (event) => {
      const sessionSearch = event.target?.closest?.('[data-session-cleanup-search="true"]');
      // Search commits only on the dedicated button or Enter; leaving the field
      // must not issue an unexpected query.
      if (sessionSearch && root.contains(sessionSearch)) return;
      const settingControl = event.target?.closest?.('[data-setting-key]');
      if (!settingControl || !root.contains(settingControl)) return;
      if (settingControl.closest?.('.codex-usage-hud-rest-reminder-card')) return;
      if (
        settingControl instanceof HTMLSelectElement
        || (settingControl instanceof HTMLInputElement && ["checkbox", "radio"].includes(settingControl.type))
      ) return;
      void autoSaveNonPricingSettingsFromModal();
    });
    rootScope.listen(document, "paste", (event) => {
      const row = event.target?.closest?.('[data-price-row="true"]');
      if (!row || !root.contains(row)) return;
      const clipboard = event.clipboardData;
      const text = clipboard?.getData("text/plain") || clipboard?.getData("text") || "";
      if (!fillPriceRowFromClipboard(row, text)) return;
      event.preventDefault();
      event.stopPropagation();
      markSettingsProviderDirty();
    }, true);
    rootScope.listen(root, "change", (event) => {
      const codexCliField = event.target?.closest?.("[data-codex-cli-field]");
      if (codexCliField && root.contains(codexCliField)) {
        codexCliFieldChange(String(codexCliField.dataset.codexCliField || ""));
        return;
      }
      const restStartInput = event.target?.closest?.('[data-rest-reminder-start-time="true"]');
      if (restStartInput && root.contains(restStartInput)) {
        restStartInput.dataset.userEdited = "true";
        return;
      }
      const pricingImportFile = event.target?.closest?.('[data-pricing-import-file="true"]');
      if (pricingImportFile && root.contains(pricingImportFile)) {
        void readPricingImportFile(pricingImportFile);
        return;
      }
      const pricingApplyAll = event.target?.closest?.('[data-pricing-apply-all="true"]');
      if (pricingApplyAll && root.contains(pricingApplyAll)) {
        updatePricingApplyAllPreview(!!pricingApplyAll.checked);
        return;
      }
      const sessionIndexEnabled = event.target?.closest?.('[data-session-index-enabled="true"]');
      if (sessionIndexEnabled && root.contains(sessionIndexEnabled)) {
        requestSessionIndexEnabled(sessionIndexEnabled.checked === true);
        return;
      }
      const settingControl = event.target?.closest?.('[data-setting-key]');
      const providerSettingControl = event.target?.closest?.(
        '[data-provider-enabled="true"], [data-provider-notification-only="true"], [data-provider-quick-launch="true"]',
      );
      const immediateSettingControl = settingControl || providerSettingControl;
      const isImmediateChoice = immediateSettingControl instanceof HTMLSelectElement
        || (immediateSettingControl instanceof HTMLInputElement
          && ["checkbox", "radio"].includes(immediateSettingControl.type));
      if (
        isImmediateChoice
        && root.contains(immediateSettingControl)
        && !immediateSettingControl.closest?.('.codex-usage-hud-rest-reminder-card')
      ) {
        if (providerSettingControl?.checked && providerSettingControl.matches(
          '[data-provider-enabled="true"], [data-provider-notification-only="true"]',
        )) {
          const editor = providerSettingControl.closest?.('[data-provider-editor="true"]');
          const counterpartSelector = providerSettingControl.matches('[data-provider-enabled="true"]')
            ? '[data-provider-notification-only="true"]'
            : '[data-provider-enabled="true"]';
          const counterpart = editor?.querySelector(counterpartSelector);
          if (counterpart) counterpart.checked = false;
        }
        void autoSaveNonPricingSettingsFromModal();
        return;
      }
      const sessionTransferTarget = event.target?.closest?.('[data-session-transfer-target="true"]');
      if (sessionTransferTarget && root.contains(sessionTransferTarget)) {
        sessionTransferState.targetProvider = String(sessionTransferTarget.value || "").trim().toLowerCase();
        syncSessionTransferDialogControls();
        return;
      }
      const sessionTransferMode = event.target?.closest?.('[data-session-transfer-mode]');
      if (sessionTransferMode && root.contains(sessionTransferMode)) {
        sessionTransferState.mode = sessionTransferModeValue(sessionTransferMode);
        syncSessionTransferSubmitButton(sessionTransferState.mode);
        return;
      }
      const sessionTransferSelectAll = event.target?.closest?.('[data-session-transfer-select-all="true"]');
      if (sessionTransferSelectAll && root.contains(sessionTransferSelectAll)) {
        for (const id of sessionTransferSelectableIds()) {
          if (sessionTransferSelectAll.checked) sessionTransferState.selectedIds.add(id);
          else sessionTransferState.selectedIds.delete(id);
        }
        syncSessionTransferDialogControls();
        return;
      }
      const sessionTransferItem = event.target?.closest?.('[data-session-transfer-id]');
      if (sessionTransferItem && root.contains(sessionTransferItem)) {
        const id = String(sessionTransferItem.dataset.sessionTransferId || "");
        if (sessionTransferItem.checked) sessionTransferState.selectedIds.add(id);
        else sessionTransferState.selectedIds.delete(id);
        syncSessionTransferDialogControls();
        return;
      }
      const sessionFilter = event.target?.closest?.("[data-session-cleanup-filter]");
      if (sessionFilter && root.contains(sessionFilter)) {
        const key = String(sessionFilter.dataset.sessionCleanupFilter || "");
        if (!new Set(["archive", "availability", "clientKind", "modelProvider", "sort", "workdirId"]).has(key)) return;
        sessionCleanupState[key] = String(sessionFilter.value || "all");
        sessionCleanupState.page = 0;
        if (key !== "sort") sessionCleanupState.selectedIds.clear();
        persistSessionCleanupFilters();
        if (key === "workdirId") {
          stopSessionCleanupSearchTimer();
          requestSessionCleanupSearch(sessionCleanupState.search);
        } else {
          renderSettingsModal("storage");
        }
        return;
      }

      const sessionSelectAll = event.target?.closest?.('[data-session-cleanup-select-all="true"]');
      if (sessionSelectAll && root.contains(sessionSelectAll)) {
        for (const item of sessionCleanupPageRows()) {
          const id = String(item?.id || "");
          if (!id || item?.selectable !== true) continue;
          if (sessionSelectAll.checked) sessionCleanupState.selectedIds.add(id);
          else sessionCleanupState.selectedIds.delete(id);
        }
        renderSettingsModal("storage");
        return;
      }
      const sessionItem = event.target?.closest?.('[data-session-cleanup-id]');
      if (sessionItem && root.contains(sessionItem)) {
        const id = String(sessionItem.dataset.sessionCleanupId || "");
        if (sessionItem.checked) sessionCleanupState.selectedIds.add(id);
        else sessionCleanupState.selectedIds.delete(id);
        renderSettingsModal("storage");
        return;
      }
      const filter = event.target?.closest?.("[data-background-usage-filter]");
      if (!filter || !root.contains(filter)) return;
      const key = String(filter.dataset.backgroundUsageFilter || "");
      if (key === "feature") backgroundUsageState.feature = String(filter.value || "");
      if (key === "model") backgroundUsageState.model = String(filter.value || "");
      if (backgroundUsageSessionRankingMode()) {
        backgroundUsageState.model = "";
        if (backgroundUsageState.range === "all") {
          backgroundUsageState.range = "7d";
        }
      }
      backgroundUsageState.selectedEventId = "";
      backgroundUsageState.selectedSessionId = "";
      backgroundUsageState.detail = null;
      backgroundUsageState.error = "";
      void loadBackgroundUsage();
    });
    rootScope.listen(root, "keydown", (event) => {
      if (event.key === "Escape") {
        const modal = document.getElementById(settingsModalId);
        if (modal && !modal.hidden) {
          const codexCli = modal.querySelector('[data-codex-cli-dialog="true"]');
          const sessionTransfer = modal.querySelector('[data-session-transfer-dialog="true"]');
          const confirm = modal.querySelector('[data-settings-confirm="true"]');
          event.preventDefault();
          event.stopPropagation();
          if (codexCli) closeCodexCliDialog();
          else if (sessionTransfer) closeSessionTransferDialog();
          else if (confirm) closeSettingsConfirm();
          else closeSettingsModal();
          return;
        }
      }
      const modelSelect = event.target?.closest?.('[data-codex-cli-field="model"]');
      if (modelSelect && root.contains(modelSelect) && ["Enter", " ", "ArrowDown", "F4"].includes(event.key)) {
        requestCodexCliModels({ reopenPicker: true });
      }
      const workdirButton = event.target?.closest?.('[data-action="usage-insights-open-workdir"]');
      if (workdirButton && root.contains(workdirButton) && (event.key === "Enter" || event.key === " ")) {
        return;
      }
      const sessionRankingRow = event.target?.closest?.('[data-action="background-usage-session-select"]');
      if (sessionRankingRow && root.contains(sessionRankingRow) && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        sessionRankingRow.click();
        return;
      }
      const tab = event.target?.closest?.('[data-provider-tab="true"]');
      if (!tab || !root.contains(tab) || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      const providers = settingsProviderDraft?.order || [];
      if (!providers.length) return;
      event.preventDefault();
      event.stopPropagation();
      const currentIndex = Math.max(0, providers.indexOf(settingsProviderDraft.activeProvider));
      const offset = event.key === "ArrowRight" ? 1 : -1;
      const nextIndex = (currentIndex + offset + providers.length) % providers.length;
      switchSettingsProvider(providers[nextIndex], { focusTab: true });
    });
    rootScope.listen(root, "click", (event) => {
      // 设置弹窗只能通过右上角 × 或底部“关闭”按钮关闭，点击遮罩空白区域不应关闭。
      const copyNode = event.target?.closest?.("[data-copyable='true']");
      if (copyNode && root.contains(copyNode)) {
        event.preventDefault();
        event.stopPropagation();
        void copyHudText(copyNode.dataset.copyText || "").then((ok) => {
          flashCopyState(copyNode, ok);
        });
        if (copyNode.dataset.copyField === "heavy") {
          const taskIndex = Number(copyNode.dataset.activityTaskIndex || 0);
          const roundIndex = Number(copyNode.dataset.activityRoundIndex || 0);
          sessionViewDomain.selectActivityTaskIndex(
            root,
            taskIndex,
          );
          sessionViewDomain.locateActivityTrailRound(root, taskIndex, roundIndex);
          // 已回滚的轮次在 Codex 聊天里没有对应消息，materialize 扫描只会
          // 白白拖动视口约 6 秒后失败，因此只复制并高亮 HUD 内轨迹。
          if (copyNode.dataset.rolledBack !== "true") {
            let locateTexts = [];
            try {
              const parsed = JSON.parse(copyNode.dataset.locateTexts || "[]");
              if (Array.isArray(parsed)) {
                locateTexts = parsed.filter((text) => typeof text === "string" && text.trim());
              }
            } catch (_) {
              locateTexts = [];
            }
            void sessionViewDomain.scrollToActivityRound(
              copyNode.dataset.copyText || "",
              copyNode.dataset.activityTaskPrompt || "",
              roundIndex,
              copyNode.dataset.activityTaskTurnId || "",
              locateTexts,
            );
          }
        }
        return;
      }
      const sessionCleanupRow = event.target?.closest?.(".codex-usage-hud-session-row");
      if (sessionCleanupRow && root.contains(sessionCleanupRow)) {
        const interactiveTarget = event.target?.closest?.("input, button, select, textarea, a, label");
        if (!interactiveTarget) {
          const checkbox = sessionCleanupRow.querySelector('[data-session-cleanup-id]');
          if (checkbox && !checkbox.disabled) {
            event.preventDefault();
            checkbox.click();
          }
          return;
        }
      }
      const sessionTransferItem = event.target?.closest?.('[data-session-transfer-id]');
      if (sessionTransferItem && root.contains(sessionTransferItem)) {
        // Keep the native checkbox interaction and reconcile after its default action.
        ctx.lifecycle.timeout("session_transfer_selection", () => {
          syncSessionTransferSelection(sessionTransferItem);
        }, 0);
        return;
      }
      const sessionTransferModeInput = event.target?.closest?.('[data-session-transfer-mode]');
      const sessionTransferModeLabel = event.target?.closest?.('.codex-usage-hud-session-transfer-mode label');
      const sessionTransferMode = sessionTransferModeInput
        || sessionTransferModeLabel?.querySelector?.('[data-session-transfer-mode]');
      if (sessionTransferMode && root.contains(sessionTransferMode)) {
        // Update the primary action in the same click task, before any list
        // synchronization can run.
        sessionTransferState.mode = sessionTransferModeValue(sessionTransferMode);
        syncSessionTransferSubmitButton(sessionTransferState.mode);
        return;
      }
      const sessionTransferSelectAll = event.target?.closest?.('[data-session-transfer-select-all="true"]');
      if (sessionTransferSelectAll && root.contains(sessionTransferSelectAll)) {
        ctx.lifecycle.timeout("session_transfer_select_all", () => {
          syncSessionTransferSelectAll(sessionTransferSelectAll);
        }, 0);
        return;
      }
      const sessionTransferRow = event.target?.closest?.('[data-session-transfer-row="true"]');
      if (sessionTransferRow && root.contains(sessionTransferRow)) {
        const checkbox = sessionTransferRow.querySelector('[data-session-transfer-id]');
        if (checkbox && !checkbox.disabled) {
          ctx.lifecycle.timeout("session_transfer_selection", () => {
            syncSessionTransferSelection(checkbox);
          }, 0);
        }
        return;
      }
      const action = event.target?.closest?.("[data-action]");
      if (!action || !root.contains(action)) return;
      if (action.dataset.action === "active-session-candidate") {
        event.preventDefault();
        event.stopPropagation();
        const sessionId = String(action.dataset.sessionId || "").trim();
        if (!sessionId) return;
        submitSettingsCommand(
          {
            action: "resolveActiveSession",
            sessionId,
            selectionSeq: Number(action.dataset.selectionSeq || 0),
          },
          "正在按你的选择匹配未归档会话...",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "settings-codex-cli-open") {
        event.preventDefault();
        event.stopPropagation();
        openCodexCliDialog(action.dataset.provider || "");
        return;
      }
      if (action.dataset.action === "settings-transfer-provider") {
        event.preventDefault();
        event.stopPropagation();
        openSessionTransferDialog(action.dataset.provider || "");
        return;
      }
      if (action.dataset.action === "codex-cli-close") {
        event.preventDefault();
        event.stopPropagation();
        closeCodexCliDialog();
        return;
      }
      if (action.dataset.action === "codex-cli-refresh") {
        event.preventDefault();
        event.stopPropagation();
        refreshCodexCliDialog();
        return;
      }
      if (action.dataset.action === "codex-cli-model-refresh") {
        event.preventDefault();
        event.stopPropagation();
        requestCodexCliModels({ force: true });
        return;
      }
      if (action.dataset.action === "codex-cli-copy") {
        event.preventDefault();
        event.stopPropagation();
        codexCliCopyCommand();
        return;
      }
      if (action.dataset.action === "codex-cli-launch") {
        event.preventDefault();
        event.stopPropagation();
        launchCodexCliFromDialog();
        return;
      }
      if (action.dataset.action === "codex-cli-chat-test") {
        event.preventDefault();
        event.stopPropagation();
        chatTestCodexCliFromDialog();
        return;
      }
      if (action.dataset.action === "runtime-errors-toggle") {
        event.preventDefault();
        event.stopPropagation();
        const runtimeErrorsPanel = action.closest('[data-field="runtimeErrorsPanel"]');
        if (!runtimeErrorsPanel) return;
        const expanded = runtimeErrorsPanel.dataset.expanded !== "true";
        runtimeErrorsPanel.dataset.expanded = String(expanded);
        const body = runtimeErrorsPanel.querySelector(".codex-usage-hud-runtime-errors-body");
        if (body) body.hidden = !expanded;
        action.setAttribute("aria-label", expanded ? "收缩 Runtime errors 面板" : "展开 Runtime errors 面板");
        action.setAttribute("aria-expanded", String(expanded));
        action.title = expanded ? "收缩 Runtime errors 面板" : "展开 Runtime errors 面板";
        action.textContent = expanded ? "v" : ">";
        setRuntimeErrorsPanelState({ expanded });
        applyRuntimeErrorsPanelState(runtimeErrorsPanel);
        return;
      }
      if (action.dataset.action === "activity-load-more") {
        event.preventDefault();
        event.stopPropagation();
        const list = root.querySelector('[data-field="topActivityTrail"]');
        if (list) {
          const current = Number(list.dataset.visibleCount || 4);
          const button = root.querySelector('[data-field="topActivityLoadMore"]');
          const pageSize = Math.max(1, Number(button?.dataset.pageSize || 12));
          list.dataset.visibleCount = String(current + pageSize);
        }
        sessionViewDomain.renderTopDetails(root, currentPayload() || {});
        return;
      }
      if (
        action.dataset.action === "activity-task-prev"
        || action.dataset.action === "activity-task-next"
      ) {
        event.preventDefault();
        event.stopPropagation();
        const delta = action.dataset.action === "activity-task-prev" ? -1 : 1;
        sessionViewDomain.selectActivityTask(root, delta);
        return;
      }
      if (action.dataset.action === "background-usage-open-notification") {
        event.preventDefault();
        event.stopPropagation();
        const eventId = String(action.dataset.eventId || "").trim();
        if (!eventId) return;
        const requestId = submitBackgroundUsageCommand(
          "openBackgroundUsage",
          { eventId },
        );
        if (!requestId) {
          backgroundUsageState.range = normalizeBackgroundUsageRange(
            action.dataset.backgroundRange,
          );
          backgroundUsageState.feature = "";
          backgroundUsageState.model = "";
          backgroundUsageState.selectedSessionId = "";
          backgroundUsageState.selectedEventId = eventId;
          backgroundUsageState.data = null;
          backgroundUsageState.detail = null;
          backgroundUsageState.loadedRevision = -1;
          renderSettingsModal("backgroundUsage");
          // No binding path: open + auto-locate still counts as viewing.
          void loadBackgroundUsageDetail(eventId, { markViewed: true });
        }
        return;
      }
      if (action.dataset.action === "background-usage-policy") {
        event.preventDefault(); event.stopPropagation();
        backgroundUsagePolicyConfirm(backgroundUsageState.detail);
        return;
      }
      if (action.dataset.action === "background-usage-policy-cancel") {
        event.preventDefault(); event.stopPropagation(); closeSettingsConfirm(); return;
      }
      if (action.dataset.action === "background-usage-policy-confirm") {
        event.preventDefault(); event.stopPropagation(); closeSettingsConfirm();
        applyBackgroundUsagePolicy(String(action.dataset.featureKey || ""), String(action.dataset.eventId || ""), String(action.dataset.desiredState || "disabled"));
        return;
      }
      if (action.dataset.action === "settings-open") {
        event.preventDefault();
        event.stopPropagation();
        renderSettingsModal("settings", "", { resetProviderDraft: true });
        return;
      }
      if (action.dataset.action === "settings-close") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsModal();
        return;
      }
      if (action.dataset.action === "settings-tab") {
        event.preventDefault();
        event.stopPropagation();
        const tab = action.dataset.tab || "settings";
        renderSettingsModal(tab);
        return;
      }
      if (action.dataset.action === "session-transfer-close") {
        event.preventDefault();
        event.stopPropagation();
        closeSessionTransferDialog();
        return;
      }
      if (action.dataset.action === "session-transfer-scan") {
        event.preventDefault();
        event.stopPropagation();
        requestSessionTransferScan();
        return;
      }
      if (action.dataset.action === "session-transfer-cancel") {
        event.preventDefault();
        event.stopPropagation();
        requestSessionTransferCancel();
        return;
      }
      if (action.dataset.action === "session-transfer-submit") {
        event.preventDefault();
        event.stopPropagation();
        submitSessionTransfer();
        return;
      }
      if (action.dataset.action === "session-transfer-page") {
        event.preventDefault();
        event.stopPropagation();
        moveSessionTransferPage(action.dataset.direction === "prev" ? -1 : 1);
        return;
      }
      if (action.dataset.action === "session-cleanup-page") {
        event.preventDefault();
        event.stopPropagation();
        moveSessionCleanupPage(action.dataset.direction === "prev" ? -1 : 1);
        return;
      }
      if (action.dataset.action === "session-cleanup-date-toggle") {
        event.preventDefault();
        event.stopPropagation();
        const opening = !sessionCleanupState.datePickerOpen;
        sessionCleanupState.datePickerOpen = opening;
        if (opening) {
          sessionCleanupState.dateDraftStart = sessionCleanupState.dateStart;
          sessionCleanupState.dateDraftEnd = sessionCleanupState.dateEnd;
        }
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-date-preset") {
        event.preventDefault();
        event.stopPropagation();
        const range = sessionCleanupDatePresetValues(action.dataset.sessionCleanupDatePreset);
        sessionCleanupState.dateDraftStart = range.start;
        sessionCleanupState.dateDraftEnd = range.end;
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-date-reset") {
        event.preventDefault();
        event.stopPropagation();
        sessionCleanupState.dateStart = "";
        sessionCleanupState.dateEnd = "";
        sessionCleanupState.dateDraftStart = "";
        sessionCleanupState.dateDraftEnd = "";
        sessionCleanupState.datePickerOpen = false;
        sessionCleanupState.page = 0;
        sessionCleanupState.selectedIds.clear();
        persistSessionCleanupFilters();
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-date-confirm") {
        event.preventDefault();
        event.stopPropagation();
        if (sessionCleanupDateRangeError(sessionCleanupState.dateDraftStart, sessionCleanupState.dateDraftEnd)) {
          renderSettingsModal("storage");
          return;
        }
        sessionCleanupState.dateStart = sessionCleanupState.dateDraftStart;
        sessionCleanupState.dateEnd = sessionCleanupState.dateDraftEnd;
        sessionCleanupState.datePickerOpen = false;
        sessionCleanupState.page = 0;
        sessionCleanupState.selectedIds.clear();
        persistSessionCleanupFilters();
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-filters-clear") {
        event.preventDefault();
        event.stopPropagation();
        sessionCleanupState.search = "";
        sessionCleanupState.searchDraft = "";
        sessionCleanupState.indexPanelHidden = false;
        sessionCleanupState.workdirId = "";
        sessionCleanupState.page = 0;
        sessionCleanupState.searchRequestId = "";
        sessionCleanupState.searchResultQuery = "";
        sessionCleanupState.searchResultRevision = "";
        sessionCleanupState.searchResultState = "idle";
        sessionCleanupState.searchResultGeneration = 0;
        sessionCleanupState.searchResultMatches = new Set();
        sessionCleanupState.searchResultDetails = new Map();
        stopSessionCleanupSearchTimer();
        sessionCleanupState.dateStart = "";
        sessionCleanupState.dateEnd = "";
        sessionCleanupState.dateDraftStart = "";
        sessionCleanupState.dateDraftEnd = "";
        sessionCleanupState.datePickerOpen = false;
        sessionCleanupState.archive = "all";
        sessionCleanupState.availability = "all";
        sessionCleanupState.clientKind = "all";
        sessionCleanupState.modelProvider = "all";
        sessionCleanupState.sort = "recent";
        sessionCleanupState.selectedIds.clear();
        persistSessionCleanupFilters();
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "background-usage-range") {
        event.preventDefault();
        event.stopPropagation();
        backgroundUsageState.range = normalizeBackgroundUsageRange(
          action.dataset.backgroundRange,
        );
        backgroundUsageState.selectedEventId = "";
        backgroundUsageState.selectedSessionId = "";
        backgroundUsageState.detail = null;
        void loadBackgroundUsage();
        return;
      }
      if (action.dataset.action === "background-usage-session-select") {
        event.preventDefault();
        event.stopPropagation();
        backgroundUsageState.selectedSessionId = String(action.dataset.usageSessionId || "").trim();
        syncBackgroundUsagePanel();
        return;
      }
      if (action.dataset.action === "background-usage-open-workdir") {
        event.preventDefault();
        event.stopPropagation();
        const eventId = String(action.dataset.eventId || "").trim();
        if (!eventId) return;
        submitSettingsCommand(
          { action: "openBackgroundUsageWorkdir", eventId },
          "正在打开工作目录...",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "background-usage-select") {
        event.preventDefault();
        event.stopPropagation();
        const eventId = String(action.dataset.eventId || "").trim();
        if (!eventId) return;
        backgroundUsageState.selectedEventId = eventId;
        backgroundUsageState.detail = null;
        backgroundUsageState.promptExpanded = false;
        syncBackgroundUsagePanel();
        void loadBackgroundUsageDetail(eventId, { markViewed: true });
        return;
      }
      if (action.dataset.action === "background-usage-toggle-prompt") {
        event.preventDefault();
        event.stopPropagation();
        backgroundUsageState.promptExpanded = !backgroundUsageState.promptExpanded;
        syncBackgroundUsagePanel();
        return;
      }
      if (action.dataset.action === "background-usage-copy-prompt") {
        event.preventDefault();
        event.stopPropagation();
        const rawPrompt = String(backgroundUsageState.detail?.prompt || "");
        void copyHudText(rawPrompt).then((ok) => {
          setSettingsStatus(ok ? "已复制请求原文。" : "请求原文复制失败。", ok ? "" : "error");
          flashCopyState(action, ok);
        });
        return;
      }
      if (action.dataset.action === "background-usage-refresh") {
        event.preventDefault();
        event.stopPropagation();
        void loadBackgroundUsage({ force: true });
        return;
      }
      if (action.dataset.action === "usage-insights-session") {
        event.preventDefault();
        event.stopPropagation();
        const sessionId = String(action.dataset.usageSessionId || "").trim();
        if (!sessionId) return;
        const submitted = submitSettingsCommand(
          {
            action: "openUsageInsightsSession",
            sessionId,
            targetTitle: String(action.dataset.targetTitle || "").trim(),
            workdir: String(action.dataset.workdir || "").trim(),
          },
          "正在打开所选会话...",
          { preserveOverlay: true },
        );
        if (submitted) closeSettingsModal();
        return;
      }
      if (action.dataset.action === "usage-insights-open-workdir") {
        event.preventDefault();
        event.stopPropagation();
        const sessionId = String(action.dataset.usageSessionId || "").trim();
        if (!sessionId) return;
        submitSettingsCommand(
          { action: "openUsageInsightsWorkdir", sessionId },
          "正在打开工作目录...",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "session-cleanup-open-workdir") {
        event.preventDefault();
        event.stopPropagation();
        const itemId = String(action.dataset.sessionCleanupWorkdirId || "").trim();
        const inventoryRevision = String(action.dataset.sessionCleanupInventoryRevision || "").trim();
        if (!itemId || !inventoryRevision) return;
        submitSettingsCommand(
          { action: "openSessionCleanupWorkdir", itemId, inventoryRevision },
          "正在打开工作目录...",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "session-cleanup-open-session") {
        event.preventDefault();
        event.stopPropagation();
        const itemId = String(action.dataset.sessionCleanupJumpId || "").trim();
        const inventoryRevision = String(action.dataset.sessionCleanupInventoryRevision || "").trim();
        if (!itemId || !inventoryRevision) return;
        const submitted = submitSettingsCommand(
          { action: "openSessionCleanupSession", itemId, inventoryRevision,
            searchQuery: sessionCleanupState.searchResultMatches.has(itemId)
              ? String(sessionCleanupState.searchResultQuery || "") : "" },
          "正在打开会话...",
          { preserveOverlay: true },
        );
        if (submitted) {
          sessionViewDomain.prepareSearchJump(itemId);
          markSessionCleanupJumpInflight(itemId, "jump");
          // 跳转意图是离开设置页、去到 Codex 会话，提交成功后即收起设置页让位。
          closeSettingsModal();
        }
        return;
      }
      if (action.dataset.action === "session-cleanup-resume-session") {
        event.preventDefault();
        event.stopPropagation();
        const itemId = String(action.dataset.sessionCleanupJumpId || "").trim();
        const inventoryRevision = String(action.dataset.sessionCleanupInventoryRevision || "").trim();
        if (!itemId || !inventoryRevision) return;
        const submitted = submitSettingsCommand(
          { action: "resumeSessionCleanupSession", itemId, inventoryRevision },
          "正在终端恢复会话...",
          { preserveOverlay: true },
        );
        if (submitted) {
          markSessionCleanupJumpInflight(itemId, "fallback");
          // 终端恢复同样是「离开设置页去到该会话」，一并收起设置页。
          closeSettingsModal();
        }
        return;
      }
      if (action.dataset.action === "session-cleanup-cancel") {
        event.preventDefault();
        event.stopPropagation();
        requestSessionCleanupCancel();
        return;
      }
      if (action.dataset.action === "session-cleanup-scan") {
        event.preventDefault();
        event.stopPropagation();
        requestSessionCleanupScan();
        return;
      }
      if (action.dataset.action === "session-index-clear") {
        event.preventDefault();
        event.stopPropagation();
        openSessionIndexClearConfirm();
        return;
      }
      if (action.dataset.action === "session-index-clear-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        return;
      }
      if (action.dataset.action === "session-index-clear-confirm") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        requestSessionIndexClear();
        return;
      }
      if (action.dataset.action === "session-index-toggle") {
        event.preventDefault();
        event.stopPropagation();
        settingsShellDomain.refreshSessionIndexIfStale?.();
        const index = sessionIndexDomainState();
        const coverage = String(index?.coverage || "");
        const searchHasNoRows = Boolean(
          String(sessionCleanupState.search || "").trim()
          && !sessionCleanupRows().length
          && coverage !== "full"
        );
        if (searchHasNoRows) {
          sessionCleanupState.indexPanelHidden = !sessionCleanupState.indexPanelHidden;
        } else {
          sessionCleanupState.indexPanelOpen = !sessionCleanupState.indexPanelOpen;
          sessionCleanupState.indexPanelHidden = false;
        }
        // Collapsing the details leaves the single warm job running while its
        // compact progress stays visible in this button.
        refreshStoragePanelIfVisible();
        return;
      }
      if (action.dataset.action === "session-cleanup-preview") {
        event.preventDefault();
        event.stopPropagation();
        requestSessionCleanupPreview();
        return;
      }
      if (action.dataset.action === "session-cleanup-confirm-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        submitSettingsCommand(
          { action: "sessionCleanupCancel", requestId: typedSettingsRequestId("session-cleanup-cancel") },
          "已取消永久删除确认。",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "session-cleanup-execute") {
        event.preventDefault();
        event.stopPropagation();
        executeSessionCleanup();
        return;
      }
      if (String(action.dataset.action || "").indexOf("session-index-") === 0) {
        event.preventDefault();
        event.stopPropagation();
        submitSessionIndexAction(action, event);
        return;
      }
      function submitSessionIndexAction(action, event) {
        // Warm-index coverage controls (PRD §7.2). The visible actions are
        // start / continue / extend. Collapsing the status block is the
        // background-run action and is handled by the toggle above.
        const kind = String(action?.dataset?.action || "").slice("session-index-".length);
        const controlByKind = {
          start: "start",
          resume: "resume",
          extend: "extend",
        };
        const control = controlByKind[kind];
        if (!control) return;
        const requestId = typedSettingsRequestId("session-index");
        const command = {
          action: "sessionIndexControl",
          requestId,
          control,
        };
        if (control === "extend") {
          // Both the optional status panel and the empty-result hint own a
          // range select. Read the nearest one so the chosen range is kept.
          const coverage = action.closest(".codex-usage-hud-session-coverage-hint")
            || action.closest(".codex-usage-hud-session-index-coverage");
          const select = coverage?.querySelector?.(
            'select[data-session-index-extend="true"]',
          );
          const range = String(
            select?.value
            || sessionCleanupState?.sessionIndex?.selectedRange
            || "3m",
          ).trim().toLowerCase();
          if (!range) return;
          command.range = range;
        } else if (control === "start") {
          const coverage = action.closest(".codex-usage-hud-session-index-coverage");
          const select = coverage?.querySelector?.(
            'select[data-session-index-start="true"]',
          );
          command.range = String(
            select?.value
            || sessionCleanupState?.sessionIndex?.selectedRange
            || "1m",
          ).trim().toLowerCase();
        }
        const pendingLabel = {
          start: "正在建立搜索索引...",
          resume: "正在继续建立搜索索引...",
          extend: "正在扩展索引范围...",
        }[kind] || "正在更新搜索索引...";
        requestSessionIndexControl(command, pendingLabel);
      }
      if (action.dataset.action === "settings-provider-tab") {
        event.preventDefault();
        event.stopPropagation();
        activateSettingsProviderTab(action);
        return;
      }
      if (action.dataset.action === "settings-provider-nav") {
        event.preventDefault();
        event.stopPropagation();
        scrollSettingsProviderRail(
          action.dataset.direction === "prev" ? -1 : 1,
          action,
        );
        return;
      }
      if (action.dataset.action === "settings-add-provider") {
        event.preventDefault();
        event.stopPropagation();
        openProviderConfigDialog("", { isNew: true });
        return;
      }
      if (action.dataset.action === "settings-edit-provider") {
        event.preventDefault();
        event.stopPropagation();
        openProviderConfigDialog(action.dataset.provider || "", { isNew: false });
        return;
      }
      if (action.dataset.action === "settings-delete-provider") {
        event.preventDefault();
        event.stopPropagation();
        openProviderDeleteDialog(action.dataset.provider || "");
        return;
      }
      if (action.dataset.action === "settings-provider-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        return;
      }
      if (action.dataset.action === "settings-provider-delete-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        return;
      }
      if (action.dataset.action === "settings-provider-delete-confirm") {
        event.preventDefault();
        event.stopPropagation();
        confirmProviderDeleteDialog();
        return;
      }
      if (action.dataset.action === "settings-provider-apply") {
        event.preventDefault();
        event.stopPropagation();
        applyProviderConfigDialog();
        return;
      }
      if (action.dataset.action === "settings-provider-restart-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeProviderRestartConfirmDialog();
        return;
      }
      if (action.dataset.action === "settings-provider-restart-later") {
        event.preventDefault();
        event.stopPropagation();
        applyProviderConfigWithRestartDecision("later");
        return;
      }
      if (action.dataset.action === "settings-provider-restart-now") {
        event.preventDefault();
        event.stopPropagation();
        applyProviderConfigWithRestartDecision("now");
        return;
      }
      if (action.dataset.action === "settings-provider-toggle-api-key") {
        event.preventDefault();
        event.stopPropagation();
        toggleProviderApiKeyVisibility();
        return;
      }
      if (action.dataset.action === "settings-provider-test-connectivity") {
        event.preventDefault();
        event.stopPropagation();
        testProviderConnectivityFromDialog();
        return;
      }
      if (action.dataset.action === "settings-provider-chat-test") {
        event.preventDefault();
        event.stopPropagation();
        chatTestProviderFromDialog();
        return;
      }
      if (action.dataset.action === "settings-add-model") {
        event.preventDefault();
        event.stopPropagation();
        addModelPriceRow();
        return;
      }
      if (action.dataset.action === "settings-add-detected-model") {
        event.preventDefault();
        event.stopPropagation();
        addModelPriceRow(action.dataset.model || "");
        return;
      }
      if (action.dataset.action === "settings-pricing-apply") {
        event.preventDefault();
        event.stopPropagation();
        void applyModelPricesFromModal();
        return;
      }
      if (action.dataset.action === "rest-reminder-save") {
        event.preventDefault();
        event.stopPropagation();
        void saveSettingsFromModal({ section: "restReminder" });
        return;
      }
      if (action.dataset.action === "rest-reminder-ack") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand({ action: "restReminderAck" }, "正在关闭提醒预览...");
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
        const mask = document.querySelector(`#${rootId} [data-rest-reminder-mask="true"]`);
        if (mask) {
          mask.dataset.visible = "false";
          mask.setAttribute("aria-hidden", "true");
        }
        return;
      }
      if (action.dataset.action === "rest-reminder-start") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand({ action: "restReminderStart" }, "正在开始休息计时...");
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
        const mask = document.querySelector(`#${rootId} [data-rest-reminder-mask="true"]`);
        if (mask) {
          mask.dataset.visible = "false";
          mask.setAttribute("aria-hidden", "true");
        }
        return;
      }
      if (action.dataset.action === "rest-reminder-credit-more") {
        event.preventDefault();
        event.stopPropagation();
        // Expand an inline minute input for embedded renderer contexts.
        // unreliable inside the Codex renderer (Electron disables it).
        const custom = event.target?.closest?.("[data-rest-reminder-credit-custom='true']");
        const target = action.closest?.("[data-rest-reminder-early-actions='true']")
          ?.querySelector?.("[data-rest-reminder-credit-custom='true']")
          || custom;
        if (!target) return;
        target.hidden = !target.hidden;
        if (!target.hidden) {
          const input = target.querySelector("[data-rest-reminder-credit-custom-input='true']");
          if (input) {
            input.focus({ preventScroll: true });
            input.select();
          }
        }
        return;
      }
      if (action.dataset.action === "rest-reminder-credit-custom-confirm") {
        event.preventDefault();
        event.stopPropagation();
        const custom = action.closest?.("[data-rest-reminder-credit-custom='true']");
        const input = custom?.querySelector?.("[data-rest-reminder-credit-custom-input='true']");
        const minutes = Number(input?.value || 0);
        if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
          setSettingsStatus("请输入 1–1440 之间的整数分钟数。", "error");
          return;
        }
        submitSettingsCommand(
          { action: "restReminderCredit", minutes },
          `正在记录提前休息 ${minutes} 分钟...`,
        );
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
        const mask = document.querySelector(`#${rootId} [data-rest-reminder-mask="true"]`);
        if (mask) {
          mask.dataset.visible = "false";
          mask.setAttribute("aria-hidden", "true");
        }
        return;
      }
      if (action.dataset.action === "rest-reminder-credit-custom-cancel") {
        event.preventDefault();
        event.stopPropagation();
        const custom = action.closest?.("[data-rest-reminder-credit-custom='true']");
        if (custom) custom.hidden = true;
        return;
      }
      if (action.dataset.action === "rest-reminder-credit") {
        event.preventDefault();
        event.stopPropagation();
        const minutes = Number(action.dataset.minutes || 0);
        if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
          setSettingsStatus("请输入 1–1440 之间的整数分钟数。", "error");
          return;
        }
        submitSettingsCommand(
          { action: "restReminderCredit", minutes },
          `正在记录提前休息 ${minutes} 分钟...`,
        );
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
        const mask = document.querySelector(`#${rootId} [data-rest-reminder-mask="true"]`);
        if (mask) {
          mask.dataset.visible = "false";
          mask.setAttribute("aria-hidden", "true");
        }
        return;
      }
      if (action.dataset.action === "rest-reminder-finish") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand({ action: "restReminderFinish" }, "正在结束本次休息...");
        return;
      }
      if (action.dataset.action === "rest-reminder-postpone") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand({ action: "restReminderPostpone" }, "正在安排稍后提醒...");
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
        const mask = document.querySelector(`#${rootId} [data-rest-reminder-mask="true"]`);
        if (mask) {
          mask.dataset.visible = "false";
          mask.setAttribute("aria-hidden", "true");
        }
        return;
      }
      if (action.dataset.action === "rest-reminder-test-notification") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand(
          { action: "restReminderTestNotification" },
          "正在发送系统通知并打开实际提醒预览..."
        );
        return;
      }
      if (action.dataset.action === "settings-exit") {
        event.preventDefault();
        event.stopPropagation();
        openSettingsExitConfirm();
        return;
      }
      if (action.dataset.action === "settings-exit-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        setSettingsStatus("已取消退出。");
        return;
      }
      if (action.dataset.action === "settings-discard-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        updateSettingsProviderDraftStatus();
        return;
      }
      if (action.dataset.action === "settings-discard-confirm") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsModal({ force: true });
        return;
      }
      if (action.dataset.action === "settings-exit-confirm") {
        event.preventDefault();
        event.stopPropagation();
        void exitHudFromModal();
        return;
      }
      if (action.dataset.action === "settings-restart") {
        event.preventDefault();
        event.stopPropagation();
        if (String(action.dataset.restartTarget || "") === "codex") {
          void restartCodexFromModal();
        } else {
          void restartHudFromModal();
        }
        return;
      }
      if (action.dataset.action === "settings-install-desktop-overlay") {
        event.preventDefault();
        event.stopPropagation();
        void installDesktopOverlayFromModal();
        return;
      }
      if (action.dataset.action === "settings-enable-desktop-overlay") {
        event.preventDefault();
        event.stopPropagation();
        void enableDesktopOverlayFromModal();
        return;
      }
      if (action.dataset.action === "settings-fetch-prices") {
        event.preventDefault();
        event.stopPropagation();
        void fetchPricesFromModal();
        return;
      }
      if (action.dataset.action === "settings-sync-provider-prices") {
        event.preventDefault();
        event.stopPropagation();
        syncCurrentProviderPricesToOthers();
        return;
      }
      if (action.dataset.action === "pricing-export") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand(
          { action: "pricingExport" },
          "正在生成价格 JSON 到 HUD 程序根目录...",
        );
        return;
      }
      if (action.dataset.action === "pricing-open-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        setSettingsStatus("价格 JSON 已保留在 HUD 程序根目录。", "");
        return;
      }
      if (action.dataset.action === "pricing-open") {
        event.preventDefault();
        event.stopPropagation();
        const prompt = action.closest('[data-pricing-export-prompt="true"]');
        const filename = String(prompt?.dataset.pricingFilename || "").trim();
        submitSettingsCommand(
          { action: "pricingOpen", filename },
          "正在打开价格 JSON...",
        );
        return;
      }
      if (action.dataset.action === "pricing-copy-example") {
        event.preventDefault();
        event.stopPropagation();
        copyPricingExample();
        return;
      }
      if (action.dataset.action === "pricing-import-open") {
        event.preventDefault();
        event.stopPropagation();
        openPricingImportDialog();
        return;
      }
      if (action.dataset.action === "pricing-import-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        setSettingsStatus("已取消价格导入。");
        return;
      }
      if (action.dataset.action === "pricing-import-preview") {
        event.preventDefault();
        event.stopPropagation();
        previewPricingImport();
        return;
      }
      if (action.dataset.action === "pricing-import-commit") {
        event.preventDefault();
        event.stopPropagation();
        commitPricingImport();
        return;
      }
      if (action.dataset.action === "pricing-effective-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        setSettingsStatus("已取消价格保存。");
        return;
      }
      if (action.dataset.action === "pricing-effective-confirm") {
        event.preventDefault();
        event.stopPropagation();
        confirmPricingEffectiveAt();
        return;
      }
      if (action.dataset.action === "settings-check-update") {
        event.preventDefault();
        event.stopPropagation();
        void checkUpdateFromModal();
        return;
      }
      if (action.dataset.action === "settings-install-update") {
        event.preventDefault();
        event.stopPropagation();
        void installUpdateFromModal();
        return;
      }
      if (action.dataset.action === "update-action") {
        event.preventDefault();
        event.stopPropagation();
        void runUpdateAction();
        return;
      }
      if (action.dataset.action === "dismiss-warnings-today") {
        event.preventDefault();
        event.stopPropagation();
        dismissWarningsToday();
        return;
      }
      if (action.dataset.action !== "toggle") return;
      const panel = action.closest("[data-panel]");
      const name = panel?.dataset.panel;
      if (!name || !PANEL[name]) return;
      // A drag on a collapsed panel emits a trailing click; swallow it so the
      // panel does not toggle after being moved.
      if (window.__codexHudDragSuppressClick) {
        window.__codexHudDragSuppressClick = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const expanded = panel.dataset.expanded !== "true";
      panel.dataset.expanded = String(expanded);
      setPanelState(name, { expanded });
      // Geometry is session-owned: a toggle only adjusts the panel height and
      // keeps the settled left/width. Re-deriving anchors here (or settling
      // again) would recalculate width/position mid-session and resurrect
      // pre-switch manual state.
      applyPanelToggleGeometry(name);
      scheduleLayoutReport(expanded ? "toggle-expand" : "toggle-collapse", name);
    });
    rootScope.listen(root, "pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      const settingsModal = event.target?.closest?.(`#${settingsModalId}`);
      if (settingsModal && root.contains(settingsModal)) {
        // Codex also listens for pointerdown outside its own surfaces. Do not
        // let interactions inside the HUD settings dialog trigger that path;
        // the delegated HUD click handler still owns buttons and backdrop.
        event.stopPropagation();
        return;
      }
      const runtimeToggle = event.target?.closest?.("[data-action='runtime-errors-toggle']");
      if (runtimeToggle && root.contains(runtimeToggle)) return;
      const runtimeMove = event.target?.closest?.("[data-action='runtime-errors-move']");
      if (runtimeMove && root.contains(runtimeMove)) {
        event.preventDefault();
        event.stopPropagation();
        beginRuntimeErrorsGesture(event);
        return;
      }
      const action = event.target?.closest?.("[data-action='move'], [data-action='resize'], [data-action='toggle']");
      if (!action || !root.contains(action)) return;
      const panel = action.closest("[data-panel]");
      const name = panel?.dataset.panel;
      if (!name || !PANEL[name]) return;
      const rawAction = action.dataset.action;
      if (rawAction === "resize") {
        event.preventDefault();
        event.stopPropagation();
        beginGesture(event, name, "resize", action.dataset.edge || "", false);
        return;
      }
      const collapsed = panel.dataset.expanded !== "true";
      // Collapsed panels drag from anywhere; a tap without movement still toggles.
      // Expanded panels keep dragging via the header handle only.
      if (rawAction === "toggle" && !collapsed) return;
      // Reset any stale suppress flag from a prior interrupted gesture.
      window.__codexHudDragSuppressClick = false;
      // For the tap-toggle target we must NOT preventDefault here — doing so
      // suppresses the compatibility click event and would break toggling.
      // A real drag is detected via the movement threshold in beginGesture.
      if (rawAction !== "toggle") {
        event.preventDefault();
        event.stopPropagation();
      }
      beginGesture(event, name, "move", "", rawAction === "toggle");
    });
  }

  function markHudStale() {
    const root = document.getElementById(rootId);
    const state = ctx.state.read();
    const payload = state.payload || {};
    const updatedAt = Number(state.updatedAt || 0);
    if (!root || !updatedAt || Date.now() - updatedAt < staleUpdateMs) return;
    if (!payloadNeedsStaleGuard(payload)) return;
    const ageSeconds = Math.max(10, Math.floor((Date.now() - updatedAt) / 1000));
    const existingWarning = String(payload?.topDetails?.warnings || "").trim();
    const staleWarning = `数据可能不是最新，已 ${ageSeconds}s 未同步`;
    setText(root, "topWarnings", existingWarning ? `${existingWarning}\n${staleWarning}` : staleWarning);
    root.querySelectorAll('[data-field-panel="topWarnings"]').forEach((node) => {
      node.hidden = false;
    });
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.add(warningClass);
    });
  }

  function payloadNeedsStaleGuard(payload) {
    const requestStatus = String(payload?.requestStatus || "").toLowerCase();
    const updatePhase = String(payload?.updateState?.phase || "").toLowerCase();
    return requestStatus === "running" || updatePhase === "downloading" || updatePhase === "installing";
  }

  function scheduleStaleGuard(payload) {
    ctx.lifecycle.clearTimeout(window[staleTimerName] || 0);
    if (!payloadNeedsStaleGuard(payload)) return;
    window[staleTimerName] = ctx.lifecycle.timeout(
      "stale_guard",
      markHudStale,
      staleUpdateMs + 250,
    );
  }

  function normalizePayloadDomains(payload) {
    const provided = payload?.payloadDomains && typeof payload.payloadDomains === "object"
      ? payload.payloadDomains
      : {};
    const allDomains = ["startup", "currentSession", "sessionSwitch", "budget", "settings", "overlay", "backgroundUsage", "diagnostics", "usageInsights", "sessionCleanup"];
    const domains = {};
    if (Object.keys(provided).length > 0) {
      for (const name of allDomains) {
        if (provided[name] && typeof provided[name] === "object") domains[name] = provided[name];
      }
      return domains;
    }
    for (const name of allDomains) {
      domains[name] = payload || {};
    }
    return domains;
  }

  function renderStartupBubble(root, startup) {
    const bubble = root.querySelector('[data-field="startupBubble"]');
    if (!bubble) return;
    const active = !!startup && typeof startup === "object";
    bubble.hidden = !active;
    if (!active) return;
    const progress = clamp(Number(startup.progress ?? 0) || 0, 0, 100);
    setText(root, "startupStep", startup.step || "正在启动");
    setText(root, "startupTitle", startup.title || "正在打开 Codex HUD");
    setText(root, "startupDetail", startup.detail || "正在准备会话信息");
    setText(root, "startupProgressLabel", `${Math.round(progress)}%`);
    const track = root.querySelector('[data-field="startupProgressTrack"]');
    const fill = root.querySelector('[data-field="startupProgressFill"]');
    if (track) {
      track.setAttribute("aria-valuenow", String(Math.round(progress)));
      track.setAttribute("aria-label", `${startup.step || "启动进度"} ${Math.round(progress)}%`);
    }
    if (fill) fill.style.width = `${progress}%`;
    positionStartupBubble(root);
  }

  function positionStartupBubble(root = document.getElementById(rootId)) {
    const bubble = root?.querySelector?.('[data-field="startupBubble"]');
    if (!bubble || bubble.hidden) return;
    const header = activeSessionHeaderElement();
    const rect = visible(header) ? header.getBoundingClientRect() : null;
    const top = clamp((rect?.bottom || 62) + 10, 12, Math.max(12, innerHeight - 180));
    const right = rect
      ? clamp(innerWidth - rect.right + 14, 12, 28)
      : 16;
    bubble.style.top = px(top);
    bubble.style.right = px(right);
    bubble.style.bottom = "auto";
  }

  function applyOverlayPayload(_root, _payload) {
    // Overlay payload is currently consumed by Python/desktop IPC. Keeping this
    // domain explicit lets renderer updates skip unrelated DOM work.
  }

  function applyPayloadDomains(root, payload, domains) {
    if ("currentSession" in domains) {
      sessionViewDomain.apply(root, { ...(payload || {}), ...(domains.currentSession || {}) }, "currentSession");
    }
    if ("sessionSwitch" in domains) {
      sessionViewDomain.apply(root, { ...(payload || {}), ...(domains.sessionSwitch || {}) }, "sessionSwitch");
    }
    // A session switch is exactly when the active conversation's provider can
    // change (e.g. migrated threads); keep the menubar label in step without
    // waiting for the next settings-domain payload.
    if ("currentSession" in domains || "sessionSwitch" in domains) {
      settingsShellDomain.syncCodexCliQuickLaunchMenu?.();
    }
    if ("budget" in domains) {
      budgetDomain.apply(root, { ...(payload || {}), ...(domains.budget || {}) });
    }
    if ("settings" in domains) {
      settingsShellDomain.apply(root, { ...(payload || {}), ...(domains.settings || {}) });
    }
    if ("overlay" in domains) {
      applyOverlayPayload(root, { ...(payload || {}), ...(domains.overlay || {}) });
    }
    if ("backgroundUsage" in domains) {
      backgroundUsageDomain.apply(root, { ...(payload || {}), ...(domains.backgroundUsage || {}) });
    }
    if ("diagnostics" in domains) {
      diagnosticsDomain.apply(root, { ...(payload || {}), ...(domains.diagnostics || {}) });
    }
    if ("usageInsights" in domains) {
      usageInsightsDomain.apply(root, { ...(payload || {}), ...(domains.usageInsights || {}) });
    }
    if ("sessionCleanup" in domains) {
      sessionCleanupDomain.apply(root, { ...(payload || {}), ...(domains.sessionCleanup || {}) });
    }
  }

  window.__codexUsageHudUpdate = (payload) => {
    const previousState = ctx.state.read();
    const previousPayload = currentPayload() || {};
    const nextPayload = { ...previousPayload, ...(payload || {}) };
    const domains = normalizePayloadDomains(nextPayload);
    const hasSessionPayload = "currentSession" in domains || "sessionSwitch" in domains;
    const hudHydrated = previousState.hydrated === true || (
      "currentSession" in domains && "budget" in domains
    );
    const previousDomains = previousState.domains && typeof previousState.domains === "object"
      ? previousState.domains
      : (Object.keys(previousPayload).length > 0
        ? normalizePayloadDomains(previousPayload)
        : {});
    const retainedDomains = { ...previousDomains, ...domains };
    if ("sessionSwitch" in domains) cacheActiveSessionPayload(domains.sessionSwitch);
    if ("currentSession" in domains) cacheActiveSessionPayload(domains.currentSession);
    if (
      (!payload?.supportImages || !payload.supportImages.length) &&
      previousPayload.supportImages?.length
    ) {
      nextPayload.supportImages = previousPayload.supportImages;
    }
    const persistedSupportImages = loadPersistedSupportImages();
    if (
      (!nextPayload.supportImages || !nextPayload.supportImages.length) &&
      persistedSupportImages.length
    ) {
      nextPayload.supportImages = persistedSupportImages;
    }
    for (const domainPayload of Object.values(domains)) {
      if (domainPayload && typeof domainPayload === "object") {
        Object.assign(nextPayload, domainPayload);
      }
    }
    if ("startup" in domains) nextPayload.startup = domains.startup;
    if (hasSessionPayload) {
      delete nextPayload.startup;
      delete retainedDomains.startup;
    }
    if (Array.isArray(nextPayload.supportImages) && nextPayload.supportImages.length) {
      persistSupportImages(nextPayload.supportImages);
    }
    ctx.state.write({
      payload: nextPayload,
      domains: retainedDomains,
      hydrated: hudHydrated,
      updatedAt: Date.now(),
    });
    try {
      ensureActiveSessionWatchers();
    } catch (_) {}
    const previousRoot = document.getElementById(rootId);
    const root = ensureRoot();
    if (!root) return false;
    // Codex can replace renderer DOM anchors while the page/JS realm remains
    // alive during cold startup. If our root was removed, hydrate the new root
    // from every retained domain before applying future lightweight updates;
    // otherwise a sessionSwitch-only update produces a text-only HUD with an
    // empty expanded panel.
    const renderedDomains = root === previousRoot ? domains : retainedDomains;
    const renderedSessionPayload = hudHydrated && (
      "currentSession" in renderedDomains || "sessionSwitch" in renderedDomains
    );
    applyPayloadDomains(root, nextPayload, renderedDomains);
    renderStartupBubble(root, nextPayload.startup);
    if (root !== previousRoot) restoreOpenSettingsModal();
    const wasReady = root.dataset.hudReady === "true";
    // A settings/theme/diagnostics partial update follows the first complete
    // payload during startup. It must preserve visible HUD panels rather than
    // treating the absence of a session domain as a new startup state.
    if (renderedSessionPayload) root.dataset.hudReady = "true";
    else if (!wasReady && "startup" in domains) root.dataset.hudReady = "false";
    // Normal session payloads update text in place, but a session switch can
    // change both the title-bar slot and the composer footer gap before the
    // host DOM mutation is observable. Invalidate both cached anchors and
    // settle the HUD against the new conversation geometry immediately.
    if (renderedSessionPayload && !wasReady) {
      syncPosition();
      if (!cachedHeaderNode || !cachedComposerNode) syncPositionSettled();
    }
    if ("sessionSwitch" in domains && wasReady) {
      invalidateHeaderAnchor();
      invalidateComposerAnchor();
      clearSessionManualGeometry();
      scheduleForPanels(Object.keys(PANEL), { invalidateTop: true, forceAutoFit: true });
      syncPositionSettled(Object.keys(PANEL), { forceAutoFit: true });
    }
    scheduleStaleGuard(nextPayload);
    return true;
  };

  window.__codexUsageHudRemove = ({ preserveState = false } = {}) => {
    const root = document.getElementById(rootId);
    root?.querySelectorAll(".codex-usage-hud-line").forEach(cancelNumericAnimation);
    ctx.teardown.run();
    if (preserveState) {
      // Reinstallation must not make an open settings child dialog vanish.
      // The DOM is the only place holding unsaved form values, so retain it in
      // memory and let the next runtime rebind/re-hydrate it.
      if (root) root.dataset.bound = "false";
      document.getElementById(styleId)?.remove();
    } else {
      root?.remove();
      document.getElementById(styleId)?.remove();
    }
    observedHeaderNode = null;
    observedComposerNode = null;
    restReminderCountdownTimer = 0;
    sessionCleanupElapsedTimer = 0;
    storageRefreshRaf = 0;
    storageRefreshTimer = 0;
    delete window[mutationObserverName];
    delete window[resizeObserverName];
    delete window[bootstrapObserverName];
    delete window[bootstrapTimerName];
    delete window[activeSessionBootstrapTimerName];
    delete window[resizeHandlerName];
    delete window[scrollHandlerName];
    delete window[scheduleName];
    delete window[rafName];
    delete window[runningTimerName];
    delete window[staleTimerName];
    delete window[themeObserverName];
    delete window[themeMediaQueryName];
    delete window[themeMediaQueryHandlerName];
    delete window[themeStorageHandlerName];
    delete window[themeTimerName];
    delete window[themeSignatureName];
    delete window[composerSettleTimerName];
    delete window[settleTimerName];
    delete window[composerInputNodeName];
    delete window[composerInputHandlersName];
    delete window[composerFocusStateName];
    delete window[composerBadgeRafName];
    delete window[modelPickerPatchHandlerName];
    delete window[modelPickerPatchRafName];
    delete window[modelPickerPatchTimersName];
    delete window[modelPickerSelectionName];
    delete window.__codexUsageHudReportActiveSession;
    delete window.__codexUsageHudReportTheme;
    delete window.__codexUsageHudUpdate;
    delete window.__codexUsageHudRemove;
    return true;
  };

  // Keep user-controlled geometry stable for the lifetime of a session.
  // Session-switch payloads explicitly request the only automatic re-fit.
  // Legacy contract marker: window[scrollHandlerName] = () => scheduleForPanels(["request"])
  window[scheduleName] = () => {};
  window[resizeHandlerName] = window[scheduleName];
  window[scrollHandlerName] = () => {};
  ctx.lifecycle.listen("layout", window, "resize", window[resizeHandlerName]);
  ctx.lifecycle.listen("layout", window, "scroll", window[scrollHandlerName], true);
  modelPickerDomain.install();
  themeDomain.install();
  diagnosticsDomain.install();
  budgetDomain.install();
  restReminderDomain.install();
  sessionViewDomain.install();
  usageInsightsDomain.install();
  sessionCleanupDomain.install();
  backgroundUsageDomain.install();
  settingsShellDomain.install();
  layoutDomain.install();
  composerDomain.install();
  activeSessionDomain.install();
  // Legacy contract marker: ctx.teardown.add("theme", stopRendererThemeObserver);
  ctx.teardown.add("active_session", () => {
    try {
      removeActiveSessionWatchers();
    } catch (_) {}
  });
  ctx.teardown.add("composer", detachComposerInputWatchers);
  ctx.teardown.add("layout", () => {
    stopBootstrapObserver();
  });
  const boot = () => {
    const state = ctx.state.read();
    if (state?.payload) {
      window.__codexUsageHudUpdate(state.payload);
    } else {
      // The new-document script can run before Python has a real session
      // payload. Do not create top/bottom panels here: a visible empty HUD is
      // both misleading and needlessly causes layout work while Codex loads.
      startBootstrapObserver();
    }
    restoreOpenSettingsModal();
    modelPickerDomain.apply();
  };
  if (document.body) {
    boot();
  } else {
    ctx.lifecycle.listen("bootstrap", document, "DOMContentLoaded", boot, { once: true });
  }
})()
"""

__all__ = ["TEXT"]
