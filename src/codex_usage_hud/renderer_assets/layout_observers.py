"""Static raw Renderer layout asset fragment."""

TEXT = r"""
      function syncPosition(names = Object.keys(PANEL), { forceAutoFit = false } = {}) {
        if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
        const root = document.getElementById(rootId);
        if (!root) return;
        positionStartupBubble(root);
        positionRestReminderBubble(root);
        applyPanelStates(root);
        const chromeMissing = desktopChromeMissing();
        const panelNames = Array.isArray(names) ? names.filter((name) => PANEL[name]) : Object.keys(PANEL);
        for (const name of panelNames) {
          const panel = root.querySelector(`[data-panel="${name}"]`);
          if (!panel) continue;
          if (chromeMissing) {
            panel.dataset.awaitingChrome = "true";
            continue;
          }
          delete panel.dataset.awaitingChrome;
          const state = getPanelState(name);
          const expanded = panel.dataset.expanded === "true";
          const height = desiredHeight(name, state, expanded);
          // Request HUD auto geometry is derived from the live composer gap.
          // A persisted width belongs only to an active manual drag/resize;
          // feeding it into requestAnchor recreates stale narrow geometry after
          // a session switch or composer rebuild.
          const widthOverride = forceAutoFit || name === "request"
            ? null
            : state.width;
          const anchor = name === "top"
            ? topAnchor(height, widthOverride)
            : requestAnchor(height, widthOverride);
          let { left, top, width } = anchor;
          if (state.manual && !forceAutoFit) {
            const relativeRequest = name === "request"
              ? manualRequestRect(state, anchor, expanded, height)
              : null;
            const relativeTop = name === "top"
              ? manualTopRect(state, anchor, expanded, height)
              : null;
            if (relativeRequest || relativeTop) {
              ({ left, top, width } = relativeRequest || relativeTop);
            } else {
              width = desiredWidth(name, state, expanded, anchor.width, anchor.maxWidth);
              left = clamp(Number(state.x ?? anchor.left), 8, Math.max(8, innerWidth - width - 8));
              top = clamp(Number(state.y ?? anchor.top), 8, Math.max(8, innerHeight - height - 8));
            }
          }
          applyRect(panel, left, top, width, height);
        }
        refreshAllMarquees(root);
        applyRuntimeErrorsPanelState();
        refreshLayoutObservers();
        startBootstrapObserver();
      }

      function applyPanelToggleGeometry(name) {
        if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
        const root = document.getElementById(rootId);
        if (!root || !PANEL[name]) return;
        const panel = root.querySelector(`[data-panel="${name}"]`);
        if (!panel) return;
        const state = getPanelState(name);
        const expanded = panel.dataset.expanded === "true";
        const height = desiredHeight(name, state, expanded);
        const rect = panel.getBoundingClientRect();
        // Geometry is session-owned: expanding or collapsing changes only the
        // panel height. Left/width keep the session-settled rect, the top
        // panel grows downward from its title-bar slot, and the request panel
        // grows upward with its bottom edge anchored to the footer row, so a
        // collapse restores the exact pre-expand rect.
        const top = name === "top"
          ? clamp(rect.top, 8, Math.max(8, innerHeight - height - 8))
          : clamp(rect.bottom - height, 8, Math.max(8, innerHeight - height - 8));
        applyRect(panel, rect.left, top, rect.width, height);
        refreshAllMarquees(root);
      }

      function syncPositionSettled(names = Object.keys(PANEL), options = {}) {
        if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
        for (const timer of (window[settleTimerName] || [])) ctx.lifecycle.clearTimeout(timer);
        window[settleTimerName] = [
          ctx.lifecycle.timeout("layout_settle", () => {
            if (runtimeIsCurrent() && ctx.lifecycle.active()) syncPosition(names, options);
          }, 50),
          ctx.lifecycle.timeout("layout_settle", () => {
            if (runtimeIsCurrent() && ctx.lifecycle.active()) syncPosition(names, options);
          }, 140),
          ctx.lifecycle.timeout("layout_settle", () => {
            if (runtimeIsCurrent() && ctx.lifecycle.active()) syncPosition(names, options);
          }, 260),
        ];
      }

      function scheduleForPanels(names = Object.keys(PANEL), { invalidateTop = false, forceAutoFit = false } = {}) {
        if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
        const panelNames = Array.isArray(names) ? names.filter((name) => PANEL[name]) : Object.keys(PANEL);
        if (invalidateTop || panelNames.includes("top")) topSlotCache = null;
        pendingForceAutoFit = pendingForceAutoFit || forceAutoFit;
        if (!pendingSyncPanels) pendingSyncPanels = new Set();
        for (const name of panelNames) pendingSyncPanels.add(name);
        ctx.frames.cancel("layout");
        window[rafName] = ctx.frames.schedule("layout", () => {
          if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
          const nextPanels = Array.from(pendingSyncPanels || Object.keys(PANEL));
          pendingSyncPanels = null;
          const nextForceAutoFit = pendingForceAutoFit;
          pendingForceAutoFit = false;
          syncPosition(nextPanels, { forceAutoFit: nextForceAutoFit });
        });
      }

      function scheduleRequestAfterComposerSettles() {
        ctx.lifecycle.clearTimeout(window[composerSettleTimerName] || 0);
        window[composerSettleTimerName] = ctx.lifecycle.timeout("composer_layout", () => {
          window[composerSettleTimerName] = 0;
          scheduleForPanels(["request"]);
        }, 180);
      }

      function layoutMutationTouchesTextInput(mutation) {
        const element = elementFromMutationNode(mutation.target);
        return !!element?.closest?.("textarea, [contenteditable='true'], [role='textbox']");
      }

      function headerTitleScopeSelector() {
        return [
          "[data-thread-title]",
          "[data-testid*='thread-title' i]",
          "[data-testid*='conversation-title' i]",
          "[data-testid='app-shell-header-context-menu-surface'] .truncate",
          // Codex Desktop's current title button is nested under the title
          // row. Keep this structural fallback narrow so arbitrary header
          // headings and truncation changes do not retrigger layout.
          ".text-md > .flex > .min-w-0.truncate > span > button",
        ].join(", ");
      }

      function nodeTouchesHeaderTitleScope(node) {
        const element = elementFromMutationNode(node);
        if (!element || element.closest?.(`#${rootId}`)) return false;
        const selector = headerTitleScopeSelector();
        const titleButton = element.closest?.("button");
        return !!(
          element.matches?.(selector)
          || element.closest?.(selector)
          // A characterData mutation can target a node inside the title
          // button, so test that button against the narrow title structure.
          || titleButton?.matches?.(selector)
        );
      }

      function mutationTouchesHeaderTitleScope(mutation) {
        if (nodeTouchesHeaderTitleScope(mutation.target)) return true;
        const selector = headerTitleScopeSelector();
        for (const node of [...(mutation.addedNodes || []), ...(mutation.removedNodes || [])]) {
          if (nodeTouchesHeaderTitleScope(node)) return true;
          const element = elementFromMutationNode(node);
          if (element?.querySelector?.(selector)) return true;
        }
        return false;
      }

      function isComposerModeControl(element) {
        const control = element?.closest?.("button, [role='button'], [role='radio'], input[type='radio']");
        if (!control || control.closest?.(`#${rootId}`)) return false;
        const identity = [
          control.getAttribute?.("data-testid"),
          control.getAttribute?.("aria-label"),
          control.getAttribute?.("title"),
          control.dataset?.mode,
          control.dataset?.composerMode,
          control.textContent,
        ].filter(Boolean).join(" ").trim();
        return /(?:^|[\\s_-])(plan|goal)(?:$|[\\s_-])|计划|目标/i.test(identity);
      }

      function mutationTouchesComposerModeControl(mutation) {
        if (isComposerModeControl(elementFromMutationNode(mutation.target))) return true;
        for (const node of [...(mutation.addedNodes || []), ...(mutation.removedNodes || [])]) {
          const element = elementFromMutationNode(node);
          if (isComposerModeControl(element)) return true;
          if (element?.querySelector?.("[data-testid*='plan' i], [data-testid*='goal' i], [data-mode*='plan' i], [data-mode*='goal' i], [data-composer-mode]")) return true;
        }
        return false;
      }

      function isComposerGeometryControl(element) {
        const control = element?.closest?.("button, [role='button'], [role='radio'], [role='img']");
        if (!control || control.closest?.(`#${rootId}`)) return false;
        const identity = [
          control.getAttribute?.("data-testid"),
          control.getAttribute?.("aria-label"),
          control.getAttribute?.("title"),
          control.textContent,
        ].filter(Boolean).join(" ").trim();
        if (/\b(?:plan|goal|model|reasoning|effort|permission|access)\b|计划|目标|模型|权限|访问/i.test(identity)) {
          return true;
        }
        // Desktop's model button is intentionally unlabeled in some builds;
        // its visible model/effort text is the only signal. Restrict this
        // fallback to a non-empty, unlabeled button in the composer's footer
        // band so a changing context-ring aria-label cannot trigger reflow.
        if (control.tagName !== "BUTTON"
          || control.getAttribute?.("aria-label")
          || control.getAttribute?.("title")
          || control.getAttribute?.("data-testid")
          || !normalize(control.textContent)) return false;
        const composerRoot = control.closest?.("[class*='ComposerLayoutRoot'], [class*='ComposerLayoutFooter']");
        if (!composerRoot) return false;
        const composerRect = composerRoot.getBoundingClientRect();
        const controlRect = control.getBoundingClientRect();
        return controlRect.bottom >= composerRect.bottom - 48;
      }

      function mutationTouchesComposerGeometryControl(mutation) {
        if (isComposerGeometryControl(elementFromMutationNode(mutation.target))) return true;
        for (const node of [...(mutation.addedNodes || []), ...(mutation.removedNodes || [])]) {
          if (isComposerGeometryControl(elementFromMutationNode(node))) return true;
        }
        return false;
      }

      function layoutMutationTarget(mutation, headerNode, composerNode) {
        const element = elementFromMutationNode(mutation.target);
        if (!element || element.closest?.(`#${rootId}`)) return "";
        if (headerNode && (element === headerNode || headerNode.contains(element))) return "header";
        if (composerNode && (element === composerNode || composerNode.contains(element))) return "composer";
        return "";
      }

      function handleLayoutMutations(mutations) {
        if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
        const headerNode = cachedHeaderNode;
        const composerNode = cachedComposerNode;
        let touchesHeaderTitle = false;
        let touchesComposerMode = false;
        for (const mutation of mutations) {
          const target = layoutMutationTarget(mutation, headerNode, composerNode);
          if (target === "header" && mutationTouchesHeaderTitleScope(mutation)) {
            touchesHeaderTitle = true;
          }
          if (target === "composer") {
            // Typing in the active conversation must not repeatedly measure
            // HUD geometry. Non-text composer mutations still cover mode
            // controls such as Plan/Goal, whose footer layout can change.
            if (!layoutMutationTouchesTextInput(mutation) && (
              mutationTouchesComposerModeControl(mutation)
              || mutationTouchesComposerGeometryControl(mutation)
            )) {
              touchesComposerMode = true;
            }
          }
        }
        // Geometry is session-owned. Controls may change their text/width in
        // an automatic panel, so refresh its anchor; preserve a user's
        // manually dragged/resized geometry for the entire session.
        // Legacy contract markers: scheduleForPanels(["top"], { invalidateTop: true });
        // scheduleForPanels(["request"]);
        // Legacy contract marker: if (!layoutMutationTouchesTextInput(mutation) && mutationTouchesComposerModeControl(mutation))
        if (touchesHeaderTitle) invalidateNativeTitleReveal();
        if (touchesHeaderTitle && !getPanelState("top").manual) {
          invalidateHeaderAnchor();
          scheduleForPanels(["top"], { invalidateTop: true });
          syncPositionSettled(["top"]);
        }
        if (touchesComposerMode && !getPanelState("request").manual) {
          invalidateComposerAnchor();
          scheduleForPanels(["request"]);
        }
      }

      function refreshLayoutObservers() {
        const headerNode = conversationHeaderElement();
        const composerNode = composerElement();
        if (
          headerNode === observedHeaderNode
          && composerNode === observedComposerNode
          && window[mutationObserverName]
          && window[resizeObserverName]
        ) return;
        observedHeaderNode = headerNode;
        observedComposerNode = composerNode;
        ctx.observers.clear("layout");
        ctx.observers.clear("layout_resize");
        window[mutationObserverName] = ctx.observers.set("layout", new MutationObserver(handleLayoutMutations));
        const mutationOptions = {
          childList: true,
          subtree: true,
          characterData: true,
          attributes: true,
          attributeFilter: ["aria-label", "title", "data-thread-title", "class"],
        };
        if (headerNode) window[mutationObserverName].observe(headerNode, mutationOptions);
        if (composerNode && composerNode !== headerNode) window[mutationObserverName].observe(composerNode, mutationOptions);
        // Session switch, async title completion, and Plan/Goal selection
        // are handled by the targeted observers above. A generic resize
        // observer also fires while typing, which violates the per-session
        // no-relayout contract.
        // Legacy contract marker: ctx.observers.set("layout_resize", new ResizeObserver(() => {}));
        window[resizeObserverName] = { disconnect() {} };
        ensureComposerInputWatchers();
      }

      function stopBootstrapObserver() {
        ctx.observers.clear("layout_bootstrap");
        ctx.lifecycle.clearTimeout(window[bootstrapTimerName] || 0);
        delete window[bootstrapObserverName];
        delete window[bootstrapTimerName];
      }

      function startBootstrapObserver() {
        if (cachedHeaderNode && cachedComposerNode) {
          stopBootstrapObserver();
          return;
        }
        if (window[bootstrapObserverName] || !document.body) return;
        window[bootstrapObserverName] = ctx.observers.set("layout_bootstrap", new MutationObserver(() => {
          if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
          invalidateHeaderAnchor();
          invalidateComposerAnchor();
          const headerNode = conversationHeaderElement();
          const composerNode = composerElement();
          if (!headerNode && !composerNode) {
            // While Codex Desktop is still on its splash screen the panels are
            // hidden and this observer is the only wake-up for their reveal,
            // so re-arm its lifetime instead of letting the 5s guard kill it.
            ctx.lifecycle.clearTimeout(window[bootstrapTimerName] || 0);
            window[bootstrapTimerName] = ctx.lifecycle.timeout(
              "layout_bootstrap",
              stopBootstrapObserver,
              5000,
            );
            return;
          }
          scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
          if (headerNode && composerNode) stopBootstrapObserver();
        }));
        window[bootstrapObserverName].observe(document.body, { childList: true, subtree: true });
        window[bootstrapTimerName] = ctx.lifecycle.timeout(
          "layout_bootstrap",
          stopBootstrapObserver,
          5000,
        );
      }

      function headerScopeSelector() {
        return [
          "header.app-header-tint",
          "[data-testid='app-shell-header']",
          "[data-testid='app-shell-header-context-menu-surface']",
          ".app-header-tint",
        ].join(", ");
      }

      function elementFromMutationNode(node) {
        if (!node) return null;
        return node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
      }

      function nodeTouchesHeaderScope(node) {
        const element = elementFromMutationNode(node);
        if (!element || element.closest?.(`#${rootId}`)) return false;
        const selector = headerScopeSelector();
        return !!(
          element.closest?.(selector)
          || element.matches?.(selector)
          || element.querySelector?.(selector)
        );
      }

      function composerScopeSelector() {
        return [
          ".composer-footer",
          "textarea",
          "[contenteditable='true']",
          "[role='textbox']",
          ".bg-token-input-background\\/90",
        ].join(", ");
      }

      function nodeTouchesComposerScope(node) {
        const element = elementFromMutationNode(node);
        if (!element || element.closest?.(`#${rootId}`)) return false;
        const selector = composerScopeSelector();
        return !!(
          element.closest?.(selector)
          || element.matches?.(selector)
          || element.querySelector?.(selector)
        );
      }

      function mutationTouchesComposerScope(mutation) {
        if (nodeTouchesComposerScope(mutation.target)) return true;
        for (const node of mutation.addedNodes || []) {
          if (nodeTouchesComposerScope(node)) return true;
        }
        for (const node of mutation.removedNodes || []) {
          if (nodeTouchesComposerScope(node)) return true;
        }
        return false;
      }

      function mutationTouchesTextInput(mutation) {
        const element = elementFromMutationNode(mutation.target);
        return !!element?.closest?.("textarea, [contenteditable='true'], [role='textbox']");
      }

      function mutationTouchesHeaderScope(mutation) {
        if (nodeTouchesHeaderScope(mutation.target)) return true;
        for (const node of mutation.addedNodes || []) {
          if (nodeTouchesHeaderScope(node)) return true;
        }
        for (const node of mutation.removedNodes || []) {
          if (nodeTouchesHeaderScope(node)) return true;
        }
        return false;
      }

    function install() {
      installNativeTitleReveal();
      return true;
    }

    function apply(_root, _payload) {
      return true;
    }

    function dispose() {
      resetNativeTitleReveal();
      ctx.lifecycle.disposeScope("native_title_reveal");
      ctx.observers.clear("native_title_reveal");
      return true;
    }

    return {
      install,
      apply,
      dispose,
      ensureStyle,
      resizeEdgesMarkup,
      topExpandedResizeMarkup,
      requestExpandedResizeMarkup,
      backgroundUsageNotificationMarkup,
      panelMarkup,
      topExpandedMarkup,
      requestExpandedMarkup,
      beginGesture,
      beginRuntimeErrorsGesture,
      minWidthFor,
      minHeightFor,
      manualPatchFor,
      desiredWidth,
      desiredHeight,
      applyRect,
      applyRuntimeErrorsPanelState,
      anchorUsable,
      invalidateHeaderAnchor,
      invalidateComposerAnchor,
      candidateHeaders,
      scoreHeader,
      conversationHeaderElement,
      conversationHeaderRect,
      hasAllClasses,
      scoreComposer,
      headerLeftControlEdge,
      headerTitleTextEdge,
      headerRightControlStart,
      headerControlButtons,
      headerLayoutSignature,
      topTitlebarSlot,
      topHeaderSlot,
      topAnchor,
      footerControlRects,
      footerGapSlot,
      requestFallbackAnchor,
      requestAnchor,
      manualRequestRect,
      manualTopRect,
      syncPosition,
      applyPanelToggleGeometry,
      syncPositionSettled,
      scheduleForPanels,
      scheduleRequestAfterComposerSettles,
      layoutMutationTouchesTextInput,
      headerTitleScopeSelector,
      nodeTouchesHeaderTitleScope,
      mutationTouchesHeaderTitleScope,
      isComposerModeControl,
      mutationTouchesComposerModeControl,
      isComposerGeometryControl,
      mutationTouchesComposerGeometryControl,
      layoutMutationTarget,
      handleLayoutMutations,
      refreshLayoutObservers,
      stopBootstrapObserver,
      startBootstrapObserver,
      headerScopeSelector,
      elementFromMutationNode,
      nodeTouchesHeaderScope,
      composerScopeSelector,
      nodeTouchesComposerScope,
      mutationTouchesComposerScope,
      mutationTouchesTextInput,
      mutationTouchesHeaderScope,
    };
  }

  const layoutDomain = ctx.domains.register(
    "layout",
    createLayoutDomain(ctx, shared),
  );
  const {
    ensureStyle,
    resizeEdgesMarkup,
    topExpandedResizeMarkup,
    requestExpandedResizeMarkup,
    backgroundUsageNotificationMarkup,
    panelMarkup,
    topExpandedMarkup,
    requestExpandedMarkup,
    beginGesture,
    beginRuntimeErrorsGesture,
    minWidthFor,
    minHeightFor,
    manualPatchFor,
    desiredWidth,
    desiredHeight,
    applyRect,
    applyRuntimeErrorsPanelState,
    anchorUsable,
    invalidateHeaderAnchor,
    invalidateComposerAnchor,
    candidateHeaders,
    scoreHeader,
    conversationHeaderElement,
    conversationHeaderRect,
    hasAllClasses,
    scoreComposer,
    headerLeftControlEdge,
    headerTitleTextEdge,
    headerRightControlStart,
    headerControlButtons,
    headerLayoutSignature,
    topTitlebarSlot,
    topHeaderSlot,
    topAnchor,
    footerControlRects,
    footerGapSlot,
    requestFallbackAnchor,
    requestAnchor,
    manualRequestRect,
    manualTopRect,
    syncPosition,
    applyPanelToggleGeometry,
    syncPositionSettled,
    scheduleForPanels,
    scheduleRequestAfterComposerSettles,
    layoutMutationTouchesTextInput,
    headerTitleScopeSelector,
    nodeTouchesHeaderTitleScope,
    mutationTouchesHeaderTitleScope,
    isComposerModeControl,
    mutationTouchesComposerModeControl,
    layoutMutationTarget,
    handleLayoutMutations,
    refreshLayoutObservers,
    stopBootstrapObserver,
    startBootstrapObserver,
    headerScopeSelector,
    elementFromMutationNode,
    nodeTouchesHeaderScope,
    composerScopeSelector,
    nodeTouchesComposerScope,
    mutationTouchesComposerScope,
    mutationTouchesTextInput,
    mutationTouchesHeaderScope,
  } = layoutDomain;
"""

__all__ = ["TEXT"]
