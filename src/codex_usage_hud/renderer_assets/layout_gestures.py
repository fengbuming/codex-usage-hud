"""Static raw Renderer layout asset fragment."""

TEXT = r"""
      function beginGesture(event, name, action, edge = "", toggleOnTap = false) {
        const panel = document.querySelector(`#${rootId} [data-panel="${name}"]`);
        if (!panel) return;
        const rect = panel.getBoundingClientRect();
        const expanded = panel.dataset.expanded === "true";
        const startState = getPanelState(name);
        // A session switch queues a few settling passes so the native footer
        // can finish mounting. Once the user starts a gesture, those passes
        // must yield to the user's geometry for this session.
        for (const timer of (window[settleTimerName] || [])) {
          ctx.lifecycle.clearTimeout(timer);
        }
        window[settleTimerName] = [];
        const startHeight = desiredHeight(name, startState, expanded, rect.height);
        const startAnchor = name === "top"
          ? topAnchor(startHeight, startState.width)
          : requestAnchor(startHeight, startState.width);
        const gestureScope = ctx.lifecycle.scope("layout_gesture");
        const DRAG_THRESHOLD = 4;
        const gesture = {
          action,
          edge: edge || "right",
          name,
          expanded,
          anchor: startAnchor,
          startX: event.clientX,
          startY: event.clientY,
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
          toggleOnTap,
          moved: !toggleOnTap,
        };
        const move = (nextEvent) => {
          const dx = nextEvent.clientX - gesture.startX;
          const dy = nextEvent.clientY - gesture.startY;
          // When the gesture started on a tap-toggle target, wait for real movement
          // before treating it as a drag, so a plain click still toggles.
          if (!gesture.moved) {
            if (Math.abs(dx) <= DRAG_THRESHOLD && Math.abs(dy) <= DRAG_THRESHOLD) return;
            gesture.moved = true;
            // Real drag started: swallow the trailing click so it doesn't toggle.
            if (gesture.toggleOnTap) window.__codexHudDragSuppressClick = true;
          }
          if (gesture.action === "move") {
            // The rect under the pointer is authoritative during a move. A
            // stale persisted width must not snap request back before the
            // user can drag it.
            const width = desiredWidth(name, {}, gesture.expanded, gesture.width, gesture.anchor.maxWidth);
            const height = desiredHeight(name, getPanelState(name), gesture.expanded, gesture.height);
            const left = clamp(gesture.left + dx, 8, Math.max(8, innerWidth - width - 8));
            const top = clamp(gesture.top + dy, 8, Math.max(8, innerHeight - height - 8));
            applyRect(panel, left, top, width, height);
            setPanelState(name, manualPatchFor(name, left, top, width, gesture.anchor, {
              manual: true,
              width: Math.round(width),
            }));
          } else {
            const minWidth = minWidthFor(name, gesture.expanded);
            const minHeight = minHeightFor(name, gesture.expanded);
            const resizeFromLeft = gesture.edge === "left" || gesture.edge.endsWith("-left");
            const maxWidthFromViewport = resizeFromLeft
              ? Math.max(minWidth, gesture.width + gesture.left - 8)
              : Math.max(minWidth, innerWidth - gesture.left - 8);
            const maxWidth = gesture.anchor.maxWidth || maxWidthFromViewport;
            const widthBase = resizeFromLeft ? (gesture.width - dx) : (gesture.width + dx);
            const width = clamp(widthBase, minWidth, Math.max(minWidth, maxWidth));
            const left = resizeFromLeft ? (gesture.left + gesture.width - width) : gesture.left;
            let height = gesture.height;
            let top = gesture.top;
            if (gesture.expanded) {
              if (name === "request" && gesture.edge.startsWith("top")) {
                const bottom = gesture.top + gesture.height;
                height = clamp(gesture.height - dy, minHeight, Math.max(minHeight, bottom - 8));
                top = bottom - height;
              } else if (name === "top" && gesture.edge.startsWith("bottom")) {
                height = clamp(gesture.height + dy, minHeight, Math.max(minHeight, innerHeight - gesture.top - 8));
              }
            }
            applyRect(panel, left, top, width, height);
            const current = getPanelState(name);
            const patch = manualPatchFor(name, left, top, width, gesture.anchor, {
              manual: true,
              width: Math.round(width),
              [gesture.expanded ? "expandedHeight" : "collapsedHeight"]: Math.round(height),
            });
            if (current.manual) patch.y = Math.round(top);
            setPanelState(name, patch);
          }
        };
        const done = () => {
          gestureScope.dispose();
          // A pure tap (no movement) falls through to the click handler, which
          // toggles the panel. Only a real drag persists a new position, and
          // only a real drag re-syncs geometry — re-deriving anchors on a tap
          // would recalculate the session-settled width/position and resurrect
          // pre-switch manual state before the toggle even runs.
          // A manual gesture owns only its panel. Reflowing both panels here
          // can recalculate the unrelated top HUD width after moving request.
          if (!gesture.moved) return;
          syncPosition([gesture.name]);
          scheduleLayoutReport(
            gesture.action === "resize" ? "resize" : "move",
            gesture.name,
          );
        };
        gestureScope.listen(document, "pointermove", move, true);
        gestureScope.listen(document, "pointerup", done, true);
        gestureScope.listen(document, "pointercancel", done, true);
      }

      function beginRuntimeErrorsGesture(event) {
        const panel = document.querySelector(`#${rootId} [data-field="runtimeErrorsPanel"]`);
        if (!panel || panel.hidden) return;
        const rect = panel.getBoundingClientRect();
        const gestureScope = ctx.lifecycle.scope("runtime_errors_gesture");
        const gesture = {
          startX: event.clientX,
          startY: event.clientY,
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
        };
        const move = (nextEvent) => {
          const left = clamp(
            gesture.left + nextEvent.clientX - gesture.startX,
            8,
            Math.max(8, innerWidth - gesture.width - 8),
          );
          const top = clamp(
            gesture.top + nextEvent.clientY - gesture.startY,
            8,
            Math.max(8, innerHeight - gesture.height - 8),
          );
          panel.style.left = px(left);
          panel.style.top = px(top);
          panel.style.right = "auto";
          panel.style.bottom = "auto";
          setRuntimeErrorsPanelState({ x: Math.round(left), y: Math.round(top) });
        };
        const done = () => {
          gestureScope.dispose();
          applyRuntimeErrorsPanelState(panel);
        };
        gestureScope.listen(document, "pointermove", move, true);
        gestureScope.listen(document, "pointerup", done, true);
        gestureScope.listen(document, "pointercancel", done, true);
      }

      let nativeTitleRevealGeometry;
      let nativeTitleRevealPanel = null;
      let nativeTitleRevealPointer = null;
      let nativeTitleRevealTimer = 0;
      let nativeTitleRevealObserver = null;
      let nativeTitleRevealObserved = [];

      function clearNativeTitleReveal() {
        ctx.lifecycle.clearTimeout(nativeTitleRevealTimer);
        nativeTitleRevealTimer = 0;
        nativeTitleRevealPanel?.removeAttribute("data-native-title-reveal");
        nativeTitleRevealPanel = null;
      }

      function resetNativeTitleReveal() {
        clearNativeTitleReveal();
        nativeTitleRevealPointer = null;
        nativeTitleRevealGeometry = undefined;
        ctx.frames.cancel("native_title_reveal");
      }

      function invalidateNativeTitleReveal() {
        clearNativeTitleReveal();
        nativeTitleRevealGeometry = undefined;
        if (nativeTitleRevealPointer) {
          ctx.frames.schedule("native_title_reveal", updateNativeTitleReveal);
        }
      }

      function nativeTitleRectIntersection(left, right) {
        const rect = {
          left: Math.max(left.left, right.left),
          top: Math.max(left.top, right.top),
          right: Math.min(left.right, right.right),
          bottom: Math.min(left.bottom, right.bottom),
        };
        return rect.right - rect.left > 1 && rect.bottom - rect.top > 1 ? rect : null;
      }

      function nativeTitlePointInside(point, rect) {
        return point && rect && point.x >= rect.left && point.x < rect.right
          && point.y >= rect.top && point.y < rect.bottom;
      }

      function measureNativeTitleReveal() {
        const panel = document.querySelector(`#${rootId} [data-panel="top"]`);
        const header = conversationHeaderElement();
        if (!visible(panel) || !visible(header)) return null;
        const candidates = Array.from(header.querySelectorAll([
          "[data-thread-title]",
          "[data-testid*='thread-title' i]",
          "[data-testid*='conversation-title' i]",
          ".truncate", "h1", "h2",
        ].join(", "))).filter((node) => (
          visible(node) && !node.closest(`#${rootId}`) && normalize(node.textContent)
        ));
        const titles = candidates.filter((node) => !candidates.some((other) => (
          other !== node && node.contains(other)
        )));
        // Ambiguous chrome must never make an unrelated HUD disappear.
        if (titles.length !== 1) return null;
        const title = titles[0];
        const observed = [header, title, panel];
        if (observed.some((node, index) => node !== nativeTitleRevealObserved[index])) {
          nativeTitleRevealObserver?.disconnect();
          observed.forEach((node) => nativeTitleRevealObserver?.observe(node));
          nativeTitleRevealObserved = observed;
        }
        let clip = nativeTitleRectIntersection(title.getBoundingClientRect(), {
          left: 0, top: 0, right: innerWidth, bottom: innerHeight,
        });
        for (let node = title.parentElement; clip && node; node = node.parentElement) {
          const style = getComputedStyle(node);
          if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return null;
          const rect = node.getBoundingClientRect();
          clip = nativeTitleRectIntersection(clip, {
            left: /hidden|clip|auto|scroll/.test(style.overflowX) ? rect.left : clip.left,
            right: /hidden|clip|auto|scroll/.test(style.overflowX) ? rect.right : clip.right,
            top: /hidden|clip|auto|scroll/.test(style.overflowY) ? rect.top : clip.top,
            bottom: /hidden|clip|auto|scroll/.test(style.overflowY) ? rect.bottom : clip.bottom,
          });
        }
        if (!clip) return null;
        // Measure glyphs rather than a flex title container's empty space.
        // Clip intrinsic text widths to the native ellipsis/overflow region;
        // revealing the HUD never changes Desktop's own truncation.
        const walker = document.createTreeWalker(title, NodeFilter.SHOW_TEXT);
        const range = document.createRange();
        const textRects = [];
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          if (!normalize(node.nodeValue) || node.parentElement?.closest("svg")) continue;
          range.selectNodeContents(node);
          for (const rect of range.getClientRects()) {
            const clipped = nativeTitleRectIntersection(rect, clip);
            if (clipped) textRects.push(clipped);
          }
        }
        const cover = panel.getBoundingClientRect();
        if (!textRects.some((rect) => nativeTitleRectIntersection(rect, cover))) return null;
        return { title, panel, textRects, cover };
      }

      function updateNativeTitleReveal() {
        const point = nativeTitleRevealPointer;
        if (!point || !ctx.lifecycle.active() || !runtimeIsCurrent()) return;
        if (nativeTitleRevealGeometry?.title.isConnected === false
          || nativeTitleRevealGeometry?.panel.isConnected === false) {
          clearNativeTitleReveal();
          nativeTitleRevealGeometry = undefined;
        }
        if (nativeTitleRevealGeometry === undefined) {
          nativeTitleRevealGeometry = measureNativeTitleReveal();
        }
        const geometry = nativeTitleRevealGeometry;
        const hit = document.elementFromPoint(point.x, point.y);
        const blocked = hit?.closest(`#${rootId}, [role="dialog"], [role="menu"]`);
        const overTitle = geometry && !blocked
          && geometry.textRects.some((rect) => nativeTitlePointInside(point, rect));
        // After revealing, keep the entire native text area available as the
        // pointer crosses into the formerly covered part. The cached cover
        // remains authoritative even though the HUD is now transparent.
        const reveal = overTitle && (nativeTitleRevealPanel
          || !nativeTitlePointInside(point, geometry.cover));
        if (!reveal) {
          if (!nativeTitleRevealPanel) {
            clearNativeTitleReveal();
          } else if (!nativeTitleRevealTimer) {
            nativeTitleRevealTimer = ctx.lifecycle.timeout("native_title_restore", () => {
              clearNativeTitleReveal();
            }, 150);
          }
          return;
        }
        if (nativeTitleRevealPanel) {
          ctx.lifecycle.clearTimeout(nativeTitleRevealTimer);
          nativeTitleRevealTimer = 0;
        } else if (!nativeTitleRevealTimer) {
          nativeTitleRevealTimer = ctx.lifecycle.timeout("native_title_reveal", () => {
            nativeTitleRevealTimer = 0;
            if (!geometry.title.isConnected || !geometry.panel.isConnected) return;
            nativeTitleRevealPanel = geometry.panel;
            nativeTitleRevealPanel.dataset.nativeTitleReveal = "true";
          }, 150);
        }
      }

      function installNativeTitleReveal() {
        const scope = ctx.lifecycle.scope("native_title_reveal");
        nativeTitleRevealObserver = ctx.observers.set("native_title_reveal", new ResizeObserver(invalidateNativeTitleReveal));
        scope.listen(document, "pointermove", (event) => {
          if (event.buttons || (event.pointerType && event.pointerType !== "mouse")) {
            resetNativeTitleReveal();
            return;
          }
          nativeTitleRevealPointer = { x: event.clientX, y: event.clientY };
          updateNativeTitleReveal();
        }, { capture: true, passive: true });
        scope.listen(document, "pointerout", (event) => {
          if (!event.relatedTarget) resetNativeTitleReveal();
        }, true);
        scope.listen(document, "pointerdown", resetNativeTitleReveal, true);
        scope.listen(document, "pointercancel", resetNativeTitleReveal, true);
        scope.listen(window, "blur", resetNativeTitleReveal);
        scope.listen(window, "resize", invalidateNativeTitleReveal);
        scope.listen(window, "scroll", invalidateNativeTitleReveal, true);
        scope.listen(document, "visibilitychange", () => {
          if (document.hidden) resetNativeTitleReveal();
        });
      }
"""

__all__ = ["TEXT"]
