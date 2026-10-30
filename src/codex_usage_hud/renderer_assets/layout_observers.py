"""Static raw Renderer layout asset fragment."""

TEXT = r"""
      function syncPosition(names = Object.keys(PANEL), { forceAutoFit = false } = {}) {
        if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
        if (layoutGeometryFrozen()) return;
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
          const widthOverride = forceAutoFit || name === "request" || !state.manual
            ? null
            : state.width;
          let anchor = name === "top"
            ? topAnchor(height, widthOverride)
            : requestAnchor(height, widthOverride);
          // Let automatic top geometry start from the complete title-bar slot.
          // Badge layout is width-sensitive: measuring while the panel still
          // has its previous narrow width can hide long badges, after which
          // content fitting never sees their intrinsic width. The first
          // applyRect below establishes the slot width; refreshAllMarquees then
          // measures badges at that width before refreshTopContentFit shrinks
          // the panel back to the settled content width.
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
              if (name === "top" && relativeTop) left = alignFittingManualTopRight(panel, anchor, left, width, expanded);
            } else {
              width = desiredWidth(name, state, expanded, anchor.width, anchor.maxWidth);
              left = clamp(Number(state.x ?? anchor.left), 8, Math.max(8, innerWidth - width - 8));
              top = clamp(Number(state.y ?? anchor.top), 8, Math.max(8, innerHeight - height - 8));
            }
          }
          applyRect(panel, left, top, width, height);
          if (name === "top") {
            // Keep clipped progress labels readable inside the fixed title slot.
            const layout = budgetDomain.measureCollapsedProgressStrip(
              panel.querySelector('[data-field="topCollapsedProgress"]'),
            );
            if (layout?.clipped) panel.dataset.topContentScroll = "true";
            else delete panel.dataset.topContentScroll;
          }
        }
        refreshAllMarquees(root);
        if (panelNames.includes("top")) refreshTopContentFit({ refreshProgress: false });
        applyRuntimeErrorsPanelState();
        refreshLayoutObservers();
        startBootstrapObserver();
      }

      function refreshTopContentFit({ refreshProgress = true, forceCollapsed = false } = {}) {
        const panel = document.querySelector(`#${rootId} [data-panel="top"]`);
        if (!panel) return;
        const wasExpanded = panel.dataset.expanded === "true";
        if (wasExpanded && !forceCollapsed) return;
        if (wasExpanded) panel.dataset.expanded = "false";
        try {
          const state = typeof getPanelState === "function" ? getPanelState("top") : null;
          const autoFit = !wasExpanded
            && !state?.manual
            && typeof topAnchor === "function"
            && typeof applyRect === "function";
          // Budget-only updates arrive while the previous automatic width is
          // still applied. Establish the complete title-bar slot before
          // refreshing badge visibility so a long badge cannot be hidden by
          // the old narrow width and disappear from the intrinsic measurement.
          if (refreshProgress && autoFit) {
            const rect = panel.getBoundingClientRect?.();
            const anchor = topAnchor(rect?.height || PANEL.top.collapsedHeight);
            if (rect && anchor && (
              Math.abs((rect.left || 0) - anchor.left) > 1
              || Math.abs((rect.width || 0) - anchor.width) > 1
            )) {
              applyRect(panel, anchor.left, rect.top, anchor.width, rect.height);
            }
          }
          const strip = panel.querySelector('[data-field="topCollapsedProgress"]');
          if (refreshProgress) {
            const refreshRail = budgetDomain.refreshProgressRailLabel;
            if (typeof refreshRail === "function") {
              panel.querySelectorAll?.(".codex-usage-hud-progress-rail")?.forEach(refreshRail);
            }
            budgetDomain.refreshCollapsedProgressStrip(strip);
          }
          const layout = budgetDomain.measureCollapsedProgressStrip(strip);
          if (layout?.clipped) panel.dataset.topContentScroll = "true";
          else delete panel.dataset.topContentScroll;
          const resetOverflowSnapshot = () => {
            if (!strip) return null;
            delete strip.dataset.overflow;
            delete strip.dataset.overflowAvailable;
            delete strip.dataset.layoutSignature;
            budgetDomain.refreshCollapsedProgressStrip(strip);
            return budgetDomain.measureCollapsedProgressStrip(strip);
          };
          if (strip && layout && !layout.clipped && strip.dataset.overflow === "true") {
            const settledLayout = resetOverflowSnapshot();
            if (settledLayout?.clipped) panel.dataset.topContentScroll = "true";
            else delete panel.dataset.topContentScroll;
          }
          if (wasExpanded) return;
          if (
            !state?.manual
            && typeof fitTopContentRect === "function"
            && typeof topAnchor === "function"
            && typeof applyRect === "function"
          ) {
            const rect = panel.getBoundingClientRect?.();
            const anchor = fitTopContentRect(
              panel,
              topAnchor(rect?.height || PANEL.top.collapsedHeight),
            );
            if (rect && anchor && (
              Math.abs((rect.left || 0) - anchor.left) > 1
              || Math.abs((rect.width || 0) - anchor.width) > 1
            )) {
              applyRect(panel, anchor.left, rect.top, anchor.width, rect.height);
              // The previous viewport can leave a short marquee snapshot after
              // automatic geometry changes; remeasure from the settled width.
              if (strip) {
                const settledLayout = resetOverflowSnapshot();
                if (settledLayout?.clipped) panel.dataset.topContentScroll = "true";
                else delete panel.dataset.topContentScroll;
              }
            }
          }
        } finally {
          if (wasExpanded) panel.dataset.expanded = "true";
        }
      }

      function applyPanelToggleGeometry(name) {
        if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
        if (layoutGeometryFrozen()) return;
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
        if (name === "top" && !expanded) refreshTopContentFit();
      }

      function syncPositionSettled(names = Object.keys(PANEL), options = {}) {
        if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
        if (layoutGeometryFrozen()) return;
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
        if (layoutGeometryFrozen()) return;
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

      function horizontalLayoutSignature(headerNode, composerNode) {
        return [headerNode, composerNode].map((node) => {
          if (!visible(node)) return "";
          const rect = node.getBoundingClientRect();
          return `${Math.round(rect.left)}:${Math.round(rect.right)}`;
        }).join("|");
      }

      function alignFittingManualTopRight(panel, anchor, left, width, expanded) {
        if (expanded || !anchor?.area || anchor.overlayTitle) return left;
        const progress = budgetDomain.measureCollapsedProgressStrip(
          panel.querySelector('[data-field="topCollapsedProgress"]'),
        );
        if (!progress || progress.clipped) return left;
        const right = Number(anchor.area.right ?? (anchor.area.left + anchor.area.width));
        return Math.max(anchor.area.left, right - width);
      }

      let columnResizePointerId = null;
      let columnResizeWatchersInstalled = false;
      let previewGeometryFrozen = false;
      let previewCloseTimer = 0;
      let previewObserver = null;
      let previewListenersInstalled = false;
      let previewCloseUntil = 0;

      function layoutGeometryFrozen() {
        return previewGeometryFrozen || Date.now() < previewCloseUntil;
      }

      const imagePreviewSurfaceSelector = [
        '[role="dialog"]',
        '[aria-modal="true"]',
        '[data-testid*="image-viewer" i]',
        '[data-testid*="media-viewer" i]',
        '[data-testid*="image-preview" i]',
        '[aria-label*="image preview" i]',
        '[aria-label*="图片预览"]',
      ].join(", ");

      function hasLargePreviewImage(node) {
        if (!node?.querySelectorAll) return false;
        return Array.from(node.querySelectorAll("img, [role='img']")).some((image) => {
          const rect = image.getBoundingClientRect();
          return rect.width >= innerWidth * 0.24 && rect.height >= innerHeight * 0.24;
        });
      }

      function hasCodexImagePreview() {
        const dialogs = Array.from(document.querySelectorAll(imagePreviewSurfaceSelector))
          .filter((node) => node.isConnected && !node.closest?.(`#${rootId}`));
        if (dialogs.some((dialog) => {
          const rect = dialog.getBoundingClientRect();
          if (rect.width < innerWidth * 0.72 || rect.height < innerHeight * 0.72) return false;
          const style = getComputedStyle(dialog);
          if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0) return false;
          return hasLargePreviewImage(dialog);
        })) return true;
        return Array.from(document.querySelectorAll("img"))
          .filter((image) => image.isConnected && !image.closest?.(`#${rootId}`))
          .some((image) => {
            if (!hasLargePreviewImage(image.parentElement || image)) return false;
            for (let node = image.parentElement, depth = 0; node && depth < 10; node = node.parentElement, depth += 1) {
              const rect = node.getBoundingClientRect();
              const style = getComputedStyle(node);
              const zIndex = Number.parseInt(style.zIndex, 10);
              if (style.position === "fixed" && zIndex >= 10
                && rect.width >= innerWidth * 0.72 && rect.height >= innerHeight * 0.72) return true;
            }
            return false;
          });
      }

      function isImagePreviewTrigger(event) {
        const path = event.composedPath?.() || [event.target];
        return path.some((node) => {
          if (!(node instanceof Element) || node.closest?.(`#${rootId}`)) return false;
          const label = normalize([
            node.getAttribute?.("aria-label"),
            node.getAttribute?.("title"),
            node.getAttribute?.("data-testid"),
          ].filter(Boolean).join(" "));
          if (/^(?:open|view|preview)\s+(?:image|attachment)|(?:图片预览|查看图片|预览图片)/i.test(label)) return true;
          if (node.tagName !== "IMG") return false;
          const rect = node.getBoundingClientRect();
          return rect.width >= 96 && rect.height >= 72;
        });
      }

      function cancelScheduledLayoutGeometry() {
        ctx.frames.cancel("layout");
        pendingSyncPanels = null;
        pendingForceAutoFit = false;
        ctx.lifecycle.clearTimeout(window[composerSettleTimerName] || 0);
        window[composerSettleTimerName] = 0;
        for (const timer of (window[settleTimerName] || [])) ctx.lifecycle.clearTimeout(timer);
        window[settleTimerName] = [];
      }

      function refreshPreviewGeometryGate() {
        if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
        const wasFrozen = previewGeometryFrozen;
        previewGeometryFrozen = hasCodexImagePreview();
        if (previewGeometryFrozen) {
          ctx.lifecycle.clearTimeout(previewCloseTimer);
          previewCloseTimer = 0;
          previewCloseUntil = 0;
          cancelScheduledLayoutGeometry();
          return;
        }
        if (wasFrozen) {
          previewCloseUntil = Date.now() + 700;
          ctx.lifecycle.clearTimeout(previewCloseTimer);
          previewCloseTimer = ctx.lifecycle.timeout("layout_preview", () => {
            previewCloseTimer = 0;
            previewCloseUntil = 0;
          }, 700);
        }
      }

      function installPreviewGeometryGate() {
        if (previewListenersInstalled || !document) return;
        previewListenersInstalled = true;
        const scheduleCheck = () => ctx.frames.schedule("layout_preview", refreshPreviewGeometryGate);
        ctx.lifecycle.listen("layout_preview", document, "pointerdown", (event) => {
          if (!isImagePreviewTrigger(event)) return;
          previewGeometryFrozen = true;
          previewCloseUntil = 0;
          ctx.lifecycle.clearTimeout(previewCloseTimer);
          previewCloseTimer = 0;
          cancelScheduledLayoutGeometry();
          scheduleCheck();
        }, true);
        ctx.lifecycle.listen("layout_preview", document, "click", scheduleCheck, true);
        ctx.lifecycle.listen("layout_preview", document, "keydown", scheduleCheck, true);
        if (typeof MutationObserver === "function" && document.documentElement) {
          previewObserver = ctx.observers.set("layout_preview", new MutationObserver((mutations) => {
            const relevant = mutations.some((mutation) => {
              const target = mutation.target?.nodeType === Node.ELEMENT_NODE
                ? mutation.target
                : mutation.target?.parentElement;
              if (target?.closest?.(`#${rootId}`)) return false;
              if (target?.closest?.(imagePreviewSurfaceSelector)
                || target?.matches?.(imagePreviewSurfaceSelector)) return true;
              return [...(mutation.addedNodes || []), ...(mutation.removedNodes || [])]
                .some((node) => node.nodeType === Node.ELEMENT_NODE
                  && (node.matches?.(imagePreviewSurfaceSelector)
                    || node.querySelector?.(imagePreviewSurfaceSelector)));
            });
            if (relevant) scheduleCheck();
          }));
          previewObserver.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ["aria-label", "aria-hidden", "aria-modal", "class", "data-testid", "role", "style"]});
        }
        refreshPreviewGeometryGate();
      }

      function isColumnResizeHandle(event) {
        const path = event.composedPath?.() || [event.target];
        return path.some((node) => {
          if (!(node instanceof Element) || node.closest?.(`#${rootId}`)) return false;
          if (node.getAttribute?.("role") === "separator"
            && node.getAttribute?.("aria-orientation") === "vertical") return true;
          const cursor = getComputedStyle(node).cursor;
          return cursor === "col-resize" || cursor === "ew-resize";
        });
      }

      function scheduleColumnResizeFrame() {
        invalidateHeaderAnchor();
        invalidateComposerAnchor();
        scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
      }

      function handleColumnResizePointerDown(event) {
        if (event.button !== 0 || !isColumnResizeHandle(event)) return;
        columnResizePointerId = event.pointerId;
        scheduleColumnResizeFrame();
      }

      function handleColumnResizePointerMove(event) {
        if (columnResizePointerId == null || event.pointerId !== columnResizePointerId) return;
        scheduleColumnResizeFrame();
      }

      function finishColumnResizePointer(event) {
        if (columnResizePointerId == null
          || (event?.pointerId != null && event.pointerId !== columnResizePointerId)) return;
        columnResizePointerId = null;
        scheduleColumnResizeFrame();
        syncPositionSettled();
      }

      function installColumnResizeWatchers() {
        if (columnResizeWatchersInstalled || !document) return;
        columnResizeWatchersInstalled = true;
        ctx.lifecycle.listen("layout", document, "pointerdown", handleColumnResizePointerDown, true);
        ctx.lifecycle.listen("layout", document, "pointermove", handleColumnResizePointerMove, true);
        ctx.lifecycle.listen("layout", document, "pointerup", finishColumnResizePointer, true);
        ctx.lifecycle.listen("layout", document, "pointercancel", finishColumnResizePointer, true);
        ctx.lifecycle.listen("layout", window, "blur", finishColumnResizePointer);
      }

      function refreshLayoutObservers() {
        installPreviewGeometryGate();
        installColumnResizeWatchers();
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
        // Side views and tabs can resize the conversation column without a
        // window resize. Observe anchor widths only so composer height changes
        // while typing do not trigger panel reflow.
        if (typeof ResizeObserver === "function") {
          let signature = horizontalLayoutSignature(headerNode, composerNode);
          window[resizeObserverName] = ctx.observers.set("layout_resize", new ResizeObserver(() => {
            if (!runtimeIsCurrent() || !ctx.lifecycle.active()) return;
            const next = horizontalLayoutSignature(headerNode, composerNode);
            if (next === signature) return;
            signature = next;
            invalidateHeaderAnchor();
            invalidateComposerAnchor();
            scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
            syncPositionSettled();
          }));
          if (headerNode) window[resizeObserverName].observe(headerNode);
          if (composerNode && composerNode !== headerNode) window[resizeObserverName].observe(composerNode);
        } else {
          window[resizeObserverName] = { disconnect() {} };
        }
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
      installPreviewGeometryGate();
      installColumnResizeWatchers();
      installNativeTitleReveal();
      return true;
    }

    function apply(_root, _payload) {
      return true;
    }

    function dispose() {
      resetNativeTitleReveal();
      ctx.lifecycle.clearTimeout(previewCloseTimer);
      ctx.lifecycle.disposeScope("layout_preview");
      ctx.observers.clear("layout_preview");
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
      collapsedTopContentWidth,
      fitTopContentRect,
      topAnchor,
      footerControlRects,
      footerGapSlot,
      requestFallbackAnchor,
      requestAnchor,
      manualRequestRect,
      manualTopRect,
      syncPosition,
      refreshTopContentFit,
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
      alignFittingManualTopRight,
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
    collapsedTopContentWidth,
    fitTopContentRect,
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
