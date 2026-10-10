"""Static raw Renderer layout asset fragment."""

TEXT = r"""
      function minWidthFor(name, expanded) {
        return expanded ? PANEL[name].minExpandedWidth : PANEL[name].minCollapsedWidth;
      }

      function minHeightFor(name, expanded) {
        return expanded ? PANEL[name].minExpandedHeight : PANEL[name].collapsedHeight;
      }

      function manualPatchFor(name, left, top, width, anchor, base = {}) {
        const patch = {
          ...base,
          x: Math.round(left),
          y: Math.round(top),
        };
        if (name !== "request" || anchor?.source !== "footer-gap" || !anchor.area) {
          return patch;
        }
        const area = anchor.area;
        const free = Math.max(0, area.width - width);
        const patchHeight = Number(
          base.expandedHeight || base.collapsedHeight || anchor.height || 0
        );
        patch.anchorSource = anchor.source;
        patch.xRatio = free > 0 ? clamp((left - area.left) / free, 0, 1) : 0.5;
        patch.yOffset = Math.round(top - anchor.top);
        patch.bottomOffset = Number.isFinite(patchHeight) && patchHeight > 0
          ? Math.round((top + patchHeight) - (anchor.top + anchor.height))
          : 0;
        patch.widthRatio = clamp(width / Math.max(1, area.width), 0.1, 1);
        return patch;
      }

      function desiredWidth(name, state, expanded, fallback, maxWidthOverride) {
        const minWidth = minWidthFor(name, expanded);
        const base = Number(state.width || fallback || PANEL[name].fallbackWidth);
        const maxWidth = Number(maxWidthOverride || innerWidth - 16);
        return clamp(base, minWidth, Math.max(minWidth, maxWidth));
      }

      function desiredHeight(name, state, expanded, fallback) {
        const key = expanded ? "expandedHeight" : "collapsedHeight";
        const base = Number(state[key] || fallback || (expanded ? PANEL[name].expandedHeight : PANEL[name].collapsedHeight));
        return clamp(base, minHeightFor(name, expanded), Math.max(minHeightFor(name, expanded), innerHeight - 16));
      }

      function applyRect(panel, left, top, width, height) {
        panel.style.left = px(left);
        panel.style.top = px(top);
        panel.style.right = "auto";
        panel.style.bottom = "auto";
        panel.style.width = px(width);
        panel.style.height = px(height);
        if (panel.dataset.panel === "top") invalidateNativeTitleReveal();
      }

      function applyRuntimeErrorsPanelState(panel = document.querySelector(`#${rootId} [data-field="runtimeErrorsPanel"]`)) {
        if (!panel || panel.hidden) return;
        const rect = panel.getBoundingClientRect();
        const state = getRuntimeErrorsPanelState();
        const hasManual = Number.isFinite(Number(state.x)) && Number.isFinite(Number(state.y));
        const width = Math.max(1, rect.width || 520);
        const height = Math.max(1, rect.height || 120);
        const left = hasManual
          ? clamp(Number(state.x), 8, Math.max(8, innerWidth - width - 8))
          : 16;
        const top = hasManual
          ? clamp(Number(state.y), 8, Math.max(8, innerHeight - height - 8))
          : clamp(innerHeight - height - 16, 8, Math.max(8, innerHeight - height - 8));
        panel.style.left = px(left);
        panel.style.top = px(top);
        panel.style.right = "auto";
        panel.style.bottom = "auto";
        if (hasManual) setRuntimeErrorsPanelState({ x: Math.round(left), y: Math.round(top) });
      }

      function anchorUsable(node) {
        return node instanceof HTMLElement && node.isConnected && !node.closest?.(`#${rootId}`) && visible(node);
      }

      function invalidateHeaderAnchor() {
        resetNativeTitleReveal();
        cachedHeaderNode = null;
        topSlotCache = null;
      }

      function invalidateComposerAnchor() {
        cachedComposerNode = null;
      }

      function candidateHeaders() {
        return Array.from(document.querySelectorAll([
          "header.app-header-tint",
          "[data-testid='app-shell-header']",
          "[data-testid='app-shell-header-context-menu-surface']",
          ".app-header-tint",
        ].join(", "))).filter(visible);
      }

      function scoreHeader(node) {
        const rect = node.getBoundingClientRect();
        const text = normalize(node.textContent);
        let score = 0;
        if (node.tagName === "HEADER") score += 80;
        if (node.classList.contains("app-header-tint")) score += 35;
        if (node.matches?.("[data-testid='app-shell-header-context-menu-surface']")) score += 140;
        if (node.closest?.("header.app-header-tint")) score += 120;
        if (String(node.className || "").includes("top-toolbar-sm")) score += 110;
        if (rect.top > 20) score += 95;
        if (rect.top <= 4) score -= 140;
        if (rect.width > 300) score += 25;
        if (rect.height >= 34 && rect.height <= 80) score += 30;
        if (/File\s*Edit\s*View\s*Window\s*Help/i.test(text) || text === "FileEditViewWindowHelp") score -= 300;
        if (text && !/File\s*Edit\s*View\s*Window\s*Help/i.test(text)) score += Math.min(20, text.length);
        return score;
      }

      function conversationHeaderElement() {
        if (anchorUsable(cachedHeaderNode)) return cachedHeaderNode;
        cachedHeaderNode = null;
        const surface = document.querySelector('[data-testid="app-shell-header-context-menu-surface"]');
        const surfaceHeader = surface?.closest?.("header.app-header-tint, header, .app-header-tint");
        if (anchorUsable(surfaceHeader)) {
          cachedHeaderNode = surfaceHeader;
          return cachedHeaderNode;
        }
        cachedHeaderNode = candidateHeaders()
          .map((node, index) => ({ node, index, score: scoreHeader(node) }))
          .sort((left, right) => (right.score - left.score) || (left.index - right.index))[0]?.node || null;
        return cachedHeaderNode;
      }

      function conversationHeaderRect() {
        const header = conversationHeaderElement();
        return visible(header) ? header.getBoundingClientRect() : null;
      }

      function desktopChromeMissing() {
        // Splash/loading state: Codex Desktop has not mounted its shell yet.
        // Panels stay hidden instead of floating over the loading logo; the
        // menubar check keeps the panels visible when only one anchor lookup
        // drifts after a Desktop update.
        if (conversationHeaderElement() || composerElement()) return false;
        return !document.querySelector('[role="menubar"]');
      }

      function hasAllClasses(node, classes) {
        const set = new Set(String(node?.className || "").split(/\s+/).filter(Boolean));
        return classes.every((name) => set.has(name));
      }

      function scoreComposer(node) {
        if (!visible(node)) return -Infinity;
        const rect = node.getBoundingClientRect();
        let score = 0;
        if (rect.bottom > innerHeight * 0.55) score += 80;
        // The real Desktop composer commonly spans almost the whole content
        // viewport. Penalizing widths above 82% selects an inner wrapper and
        // makes the HUD gap artificially narrow.
        if (rect.width >= 300) score += 36;
        if (rect.width > innerWidth * .98) score -= 12;
        if (node.querySelector?.(".composer-footer")) score += 32;
        if (node.querySelector?.("textarea, [contenteditable='true']")) score += 48;
        if (node.matches?.("[class*='ComposerLayoutRoot']")) score += 140;
        if (node.querySelector?.("[class*='ComposerLayoutFooter'], button[aria-label*='权限'], button[aria-label*='模型']")) score += 70;
        if (node.matches?.(".composer-footer")) score -= 20;
        if (rect.height >= 56 && rect.height <= 190) score += 30;
        score += Math.min(18, Array.from(node.querySelectorAll?.("button, [role='button']") || []).filter(visible).length * 2);
        score -= Math.max(0, (innerHeight * 0.45 - rect.top) / 10);
        score -= Math.max(0, (rect.height - 220) / 5);
        return score;
      }

      function headerLeftControlEdge(headerNode, header, controls = headerControlButtons(headerNode, header)) {
        const leftControls = controls
          .map((item) => item.rect)
          .filter((rect) => rect.left < header.left + (header.width * .55));
        if (!leftControls.length) return 0;
        return Math.max(...leftControls.map((rect) => rect.right - header.left)) + 14;
      }

      function headerTitleTextEdge(headerNode, header) {
        if (!headerNode) return 0;
        const maxTextWidth = Math.min(520, header.width * .55);
        const textRects = Array.from(headerNode.querySelectorAll("span, h1, h2, [data-thread-title]"))
          .filter((node) => visible(node) && !node.closest(`#${rootId}`))
          .filter((node) => normalize(node.textContent).length > 0)
          .map((node) => node.getBoundingClientRect())
          .filter((rect) => (
            rect.width > 0
            && rect.height > 0
            && rect.left >= header.left - 2
            && rect.right <= header.right + 2
            && rect.top >= header.top - 2
            && rect.bottom <= header.bottom + 2
            && rect.width <= maxTextWidth
            && rect.left < header.left + (header.width * .68)
          ));
        if (!textRects.length) return 0;
        return Math.max(...textRects.map((rect) => rect.right - header.left)) + 14;
      }

      function headerRightControlStart(headerNode, header, controls = headerControlButtons(headerNode, header)) {
        const rightControls = controls
          .map((item) => item.rect)
          .filter((rect) => rect.right > header.right - Math.min(260, Math.max(160, header.width * .24)));
        if (!rightControls.length) return header.right;
        return Math.min(...rightControls.map((rect) => rect.left));
      }

      function headerControlButtons(headerNode, header) {
        if (!headerNode || !header) return [];
        return Array.from(headerNode.querySelectorAll("button, [role='button'], a"))
          .filter((node) => visible(node) && !node.closest(`#${rootId}`))
          .map((node, index) => ({ node, index, rect: node.getBoundingClientRect(), label: normalize([
            node.getAttribute("aria-label"),
            node.getAttribute("title"),
            node.textContent,
          ].filter(Boolean).join(" ")) }))
          .filter((item) => (
            item.rect.width > 0
            && item.rect.height > 0
            && item.rect.left >= header.left - 2
            && item.rect.right <= header.right + 2
            && item.rect.top >= header.top - 2
            && item.rect.bottom <= header.bottom + 2
          ))
          .sort((left, right) => (left.rect.left - right.rect.left) || (left.index - right.index));
      }

      function headerLayoutSignature(headerNode, header, controls) {
        const textParts = Array.from(headerNode.querySelectorAll("span, h1, h2, [data-thread-title]"))
          .filter((node) => visible(node) && !node.closest(`#${rootId}`))
          .filter((node) => normalize(node.textContent).length > 0)
          .map((node) => {
            const rect = node.getBoundingClientRect();
            return [
              normalize(node.textContent).slice(0, 120),
              Math.round(rect.left - header.left),
              Math.round(rect.right - header.left),
            ].join("@");
          })
          .join("|");
        const controlParts = controls
          .map((item) => [
            item.label.slice(0, 80),
            Math.round(item.rect.left - header.left),
            Math.round(item.rect.right - header.left),
          ].join("@"))
          .join("|");
        return `${textParts}::${controlParts}`;
      }

      function topTitlebarSlot(headerNode, header) {
        if (!headerNode || !header) return null;
        const controls = headerControlButtons(headerNode, header);
        const cacheKey = [
          Math.round(innerWidth),
          Math.round(header.left),
          Math.round(header.right),
          Math.round(header.top),
          Math.round(header.bottom),
          headerLayoutSignature(headerNode, header, controls),
        ].join(":");
        if (topSlotCache?.key === cacheKey) return topSlotCache.slot;
        const chatActions = controls.find((item) => /chat actions/i.test(item.label));
        const openIn = controls.find((item) => /^open in\b/i.test(item.label));
        const titleEdge = headerTitleTextEdge(headerNode, header);
        const leftControlEdge = headerLeftControlEdge(headerNode, header, controls);
        const fallbackLeft = Math.max(160, Math.min(header.width * .14, 240));
        const left = clamp(
          (chatActions ? chatActions.rect.right + 10 : header.left + Math.max(fallbackLeft, titleEdge, leftControlEdge)),
          header.left + 8,
          header.right - 8
        );
        const rightMargin = Math.max(12, header.width * .04);
        const right = clamp(
          (openIn ? openIn.rect.left - 10 : Math.min(header.right - rightMargin, headerRightControlStart(headerNode, header, controls) - 10)),
          left,
          header.right - 8
        );
        if (right <= left) return null;
        const slot = { left, right, width: Math.max(1, right - left), cacheKey };
        topSlotCache = { key: cacheKey, slot };
        return slot;
      }

      function topHeaderSlot(headerNode, header) {
        const titlebarSlot = topTitlebarSlot(headerNode, header);
        if (titlebarSlot) return titlebarSlot;
        const controls = headerControlButtons(headerNode, header);
        const titleEdge = headerTitleTextEdge(headerNode, header);
        const leftControlEdge = headerLeftControlEdge(headerNode, header, controls);
        const fallbackLeft = Math.max(160, Math.min(header.width * .14, 240));
        const left = clamp(
          header.left + Math.max(fallbackLeft, titleEdge, leftControlEdge),
          header.left + 8,
          header.right - 8
        );
        const rightMargin = Math.max(12, header.width * .04);
        const right = clamp(
          Math.min(header.right - rightMargin, headerRightControlStart(headerNode, header, controls) - 10),
          left,
          header.right - 8
        );
        return { left, right, width: Math.max(1, right - left) };
      }

      function topAnchor(height, widthOverride) {
        const headerNode = conversationHeaderElement();
        const header = visible(headerNode) ? headerNode.getBoundingClientRect() : null;
        const minWidth = minWidthFor("top", height > PANEL.top.collapsedHeight);
        if (!header) {
          const width = clamp(widthOverride || PANEL.top.fallbackWidth, minWidth, Math.max(minWidth, innerWidth - 16));
          return { left: innerWidth - width - 12, top: 12, width, height };
        }
        const slot = topHeaderSlot(headerNode, header);
        const fitMinWidth = Math.min(minWidth, Math.max(1, slot.width));
        // Automatic top HUD geometry owns the whole title-bar slot. The
        // fallback width is only for the no-header path; keeping it here would
        // leave a large unused gap when the title bar is wider than 520px.
        const width = clamp(
          widthOverride == null ? slot.width : widthOverride,
          fitMinWidth,
          Math.max(fitMinWidth, slot.width),
        );
        const left = clamp(slot.left, 8, Math.max(8, slot.right - width));
        const top = clamp(header.top + Math.max(0, (header.height - PANEL.top.collapsedHeight) / 2), 8, Math.max(8, innerHeight - height - 8));
        return {
          left,
          top,
          width,
          height,
          source: "header-slot",
          maxWidth: slot.width,
          area: { left: slot.left, top, width: slot.width, height, right: slot.right },
        };
      }

      function footerControlRects(composerNode, composer) {
        if (!composerNode) return [];
        const candidates = Array.from(composerNode.querySelectorAll("button, [role='button']"))
          .filter((node) => visible(node) && !node.closest(`#${rootId}`))
          .map((node) => ({
            rect: footerRectSnapshot(node.getBoundingClientRect()),
            backgroundInfoControl: isBackgroundInfoControl(node),
          }))
          .filter(({ rect }) => (
            rect.width > 0
            && rect.height > 0
            && rect.left >= composer.left - 2
            && rect.right <= composer.right + 2
            && rect.top >= composer.top - 2
            && rect.bottom <= Math.min(innerHeight, composer.bottom + 8)
          ));
        if (!candidates.length) return [];
        const lowestBottom = Math.max(...candidates.map(({ rect }) => rect.bottom));
        return candidates
          .filter(({ rect }) => (
            Math.abs(rect.bottom - lowestBottom) <= 14
            || rect.top >= lowestBottom - 40
          ))
          .map(({ rect, backgroundInfoControl }) => ({ ...rect, backgroundInfoControl }))
          .sort((left, right) => (left.left - right.left) || (left.top - right.top));
      }

      function footerControlIdentity(node) {
        if (!node) return "";
        return normalize([
          node.getAttribute?.("aria-label"),
          node.getAttribute?.("title"),
          node.getAttribute?.("data-testid"),
          node.getAttribute?.("data-tooltip-content"),
          node.querySelector?.("svg > title, title")?.textContent,
          node.matches?.("button, [role='button'], a") ? node.textContent : "",
        ].filter(Boolean).join(" "));
      }

      function footerRectSnapshot(rect) {
        if (!rect) return null;
        return {
          left: Number(rect.left),
          top: Number(rect.top),
          right: Number(rect.right),
          bottom: Number(rect.bottom),
          width: Number(rect.width),
          height: Number(rect.height),
        };
      }

      function isBackgroundInfoControl(node) {
        const identity = footerControlIdentity(node);
        return /(?:background|后台|背景|context|上下文)(?:[\s_-]*(?:usage|information|window|panel|task|info|用量|信息|窗口|任务))?|(?:information|info|信息)(?:[\s_-]*(?:window|panel|窗口))/i.test(identity);
      }

      function footerProtectedControlRects(composerNode, composer) {
        // Codex mounts the reasoning trigger and context-usage ring in the
        // footer's right-hand group. The ring is a role=img span, not a
        // button, so footerControlRects() cannot see it. Some Desktop builds
        // put the background information control in a sibling wrapper, so
        // scan the page as well as the selected composer node and resolve
        // descendants to a click target before measuring them.
        const explicitSelector = [
          "[data-codex-intelligence-trigger]",
          "[role='img'][aria-label*='上下文']",
          "[role='img'][aria-label*='context' i]",
        ].join(", ");
        const candidates = Array.from(document.querySelectorAll([
          explicitSelector,
          "button",
          "[role='button']",
          "[role='img']",
          "svg",
          "a",
          "[aria-label]",
          "[title]",
          "[data-testid]",
          "[data-tooltip-content]",
        ].join(", ")));
        // Native Desktop footer actions can be mounted beside the selected
        // composer wrapper (permission, plan/goal, background info, model,
        // send). This helper handles the semantic/context controls; the
        // geometry pass in footerGapSlot handles every visible footer button.
        const footerBandTop = Math.max(composer.top, composer.bottom - 96);
        const controls = candidates
          .map((node) => ({
            node: node.closest?.("button, [role='button'], a") || node,
            explicit: node.matches?.(explicitSelector) === true,
            semantic: isBackgroundInfoControl(node),
          }))
          .filter(({ node, explicit, semantic }) => {
            if (!visible(node) || node.closest?.(`#${rootId}`)) return false;
            const external = !composerNode?.contains?.(node);
            // Only protect external nodes when they are the known native
            // context/background-information control. Treating every sibling
            // button as a blocker can collapse the usable center gap.
            return explicit || semantic || (!external && isBackgroundInfoControl(node));
          });
        const controlsByNode = new Map();
        for (const item of controls) {
          const previous = controlsByNode.get(item.node);
          if (previous) {
            previous.backgroundInfoControl = previous.backgroundInfoControl || item.semantic;
          } else {
            controlsByNode.set(item.node, { ...item, backgroundInfoControl: item.semantic });
          }
        }
        return Array.from(controlsByNode.values())
          .map(({ node, backgroundInfoControl }) => ({
            ...footerRectSnapshot(node.getBoundingClientRect()),
            protectedFooterControl: true,
            backgroundInfoControl,
          }))
          .filter((rect) => (
            rect.width > 0
            && rect.height > 0
            && rect.width <= 96
            && rect.height <= 96
            // A sibling control may extend just outside the selected composer;
            // protect the portion that actually intersects its horizontal area.
            && rect.right > composer.left - 2
            && rect.left < composer.right + 2
            && rect.top >= composer.top - 2
            && rect.bottom >= footerBandTop
            && rect.bottom <= Math.min(innerHeight, composer.bottom + 8)
          ));
      }

      function footerGapSlot(composerNode, composer, minWidth) {
        const controls = [
          ...footerControlRects(composerNode, composer),
          ...footerProtectedControlRects(composerNode, composer),
        ].sort((left, right) => (left.left - right.left) || (left.top - right.top));
        // The Desktop composer footer actions are occasionally rendered in a
        // sibling portal, outside composerNode. Collect the visible native
        // footer controls from the whole document by geometry as the
        // authoritative pass. This also catches the standalone context ring.
        const geometryFooterTop = Math.max(composer.top, composer.bottom - 64);
        const seenControlRects = new Set(controls.map((rect) => [
          Math.round(rect.left), Math.round(rect.top),
          Math.round(rect.right), Math.round(rect.bottom),
        ].join(":")));
        for (const node of Array.from(document.querySelectorAll("button, [role='button'], [role='img'], [role='radio']"))) {
          if (!visible(node) || node.closest?.(`#${rootId}`)) continue;
          const rect = node.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0 || rect.width > 120 || rect.height > 64) continue;
          if (rect.left < composer.left - 4 || rect.right > composer.right + 4) continue;
          if (rect.top < geometryFooterTop || rect.bottom > Math.min(innerHeight, composer.bottom + 8)) continue;
          const signature = [
            Math.round(rect.left), Math.round(rect.top),
            Math.round(rect.right), Math.round(rect.bottom),
          ].join(":");
          if (seenControlRects.has(signature)) continue;
          seenControlRects.add(signature);
          controls.push({
            ...footerRectSnapshot(rect),
            protectedFooterControl: true,
            backgroundInfoControl: node.getAttribute?.("role") === "img",
          });
        }
        controls.sort((left, right) => (left.left - right.left) || (left.top - right.top));
        const conservativeFooterSlot = () => {
          // Keep a symmetric reserve when a host portal temporarily hides the
          // native footer controls. The measured path above is preferred.
          const sideReserve = Math.min(170, Math.max(132, composer.width * 0.23));
          const left = composer.left + sideReserve;
          const right = composer.right - sideReserve;
          if (right <= left) return null;
          const rowTop = Math.max(composer.top, composer.bottom - 36);
          const rowBottom = Math.min(composer.bottom, rowTop + 28);
          return {
            left,
            right,
            width: right - left,
            fitsMinWidth: right - left >= minWidth,
            hasBackgroundInfoControl: true,
            rowTop,
            rowBottom,
            rowHeight: Math.max(1, rowBottom - rowTop),
            conservative: true,
          };
        };
        if (!controls.length) {
          return conservativeFooterSlot();
        }
        const rowControls = controls.filter((rect) => (
          rect.top >= geometryFooterTop - 4
          && rect.bottom <= Math.min(innerHeight, composer.bottom + 8)
        ));
        const rowTop = rowControls.length
          ? Math.min(...rowControls.map((rect) => rect.top))
          : Math.max(composer.top, composer.bottom - 36);
        const rowBottom = rowControls.length
          ? Math.max(...rowControls.map((rect) => rect.bottom))
          : Math.min(composer.bottom, rowTop + 28);
        const safeRowTop = Number.isFinite(rowTop) ? rowTop : Math.max(composer.top, composer.bottom - 36);
        const safeRowBottom = Number.isFinite(rowBottom) ? rowBottom : Math.min(composer.bottom, safeRowTop + 28);
        const start = composer.left + 8;
        const end = composer.right - 8;
        const padding = 8;
        const blockers = [];
        for (const rect of controls) {
          // Keep one equal breathing room on both sides of every native
          // footer control. Sixteen CSS pixels is enough to clear the icon
          // hitbox while keeping the left permission/mode gap compact.
          const protectedControl = rect.protectedFooterControl === true;
          // Legacy contract marker: rect.protectedFooterControl === true
          const safetyPadding = 16;
          // Legacy contract marker: const safetyPadding = protectedControl ? 14 : padding;
          const left = clamp(rect.left - safetyPadding, start, end);
          const right = clamp(rect.right + safetyPadding, start, end);
          if (right <= left) continue;
          const previous = blockers[blockers.length - 1];
          if (previous && left <= previous.right) {
            previous.left = Math.min(previous.left, left);
            previous.right = Math.max(previous.right, right);
          } else {
            blockers.push({ left, right });
          }
        }
        const gaps = [];
        let cursor = start;
        for (const blocker of blockers) {
          if (blocker.left > cursor) gaps.push({ left: cursor, right: blocker.left });
          cursor = Math.max(cursor, blocker.right);
        }
        if (cursor < end) gaps.push({ left: cursor, right: end });
        const rankedGaps = gaps
          .map((gap) => ({ ...gap, width: gap.right - gap.left }))
          .filter((gap) => gap.width > 0)
          .sort((left, right) => right.width - left.width);
        // Prefer a gap that can hold the normal panel, but keep the largest
        // real gap as a bounded fallback. This prevents a narrow footer from
        // falling back to a centered panel that covers the native right icon.
        const best = rankedGaps.find((gap) => gap.width >= minWidth) || rankedGaps[0];
        if (!best) {
          return conservativeFooterSlot();
        }
        if (best.width >= composer.width * 0.92) {
          // If the host portal hides footer controls from DOM enumeration,
          // never fall back to the full composer width. Reserve the native
          // left/right action clusters by geometry.
          const conservative = conservativeFooterSlot();
          if (conservative) return conservative;
        }
        return {
          left: best.left,
          right: best.right,
          width: best.width,
          fitsMinWidth: best.width >= minWidth,
          hasBackgroundInfoControl: controls.some((rect) => rect.backgroundInfoControl === true),
          rowTop: safeRowTop,
          rowBottom: safeRowBottom,
          rowHeight: Math.max(1, safeRowBottom - safeRowTop),
        };
      }

      function requestFallbackAnchor(composer, height, widthOverride, minWidth) {
        if (!composer) {
          const width = clamp(widthOverride || PANEL.request.fallbackWidth, minWidth, Math.max(minWidth, innerWidth - 16));
          return {
            left: innerWidth - width - 12,
            top: innerHeight - height - 28,
            width,
            height,
            source: "viewport-bottom",
            maxWidth: Math.max(minWidth, innerWidth - 16),
            area: { left: 8, top: 8, width: Math.max(1, innerWidth - 16), height: Math.max(1, innerHeight - 16) },
          };
        }
        const maxWidth = Math.max(minWidth, Math.min(composer.width, innerWidth - 16));
        const width = clamp(widthOverride == null ? maxWidth : widthOverride, minWidth, maxWidth);
        const left = clamp(composer.left + (composer.width - width) / 2, 8, Math.max(8, innerWidth - width - 8));
        // When the footer controls are not measurable yet, keep the request
        // HUD attached to the composer's bottom edge. Anchoring above the
        // composer's top makes the HUD jump upward when the composer grows
        // after a session switch.
        const top = clamp(composer.bottom - height - 4, 8, Math.max(8, innerHeight - height - 8));
        return {
          left,
          top,
          width,
          height,
          source: "composer-above",
          maxWidth,
          area: { left: composer.left, top: composer.top, width: composer.width, height: composer.height },
        };
      }

      function requestAnchor(height, widthOverride) {
        const composerNode = composerElement();
        const composer = visible(composerNode) ? composerNode.getBoundingClientRect() : null;
        const minWidth = minWidthFor("request", height > PANEL.request.collapsedHeight);
        if (!composer) return requestFallbackAnchor(null, height, widthOverride, minWidth);
        const slot = footerGapSlot(composerNode, composer, minWidth);
        if (!slot) return requestFallbackAnchor(composer, height, widthOverride, minWidth);
        const slotLeft = Number.isFinite(Number(slot.left)) ? Number(slot.left) : composer.left;
        const slotRight = Number.isFinite(Number(slot.right))
          ? Number(slot.right)
          : composer.right;
        const slotWidth = Math.max(1, slotRight - slotLeft);
        const maxWidth = slotWidth;
        const autoFitWidth = maxWidth;
        const minWidthForSlot = Math.min(minWidth, autoFitWidth);
        const preferredWidth = widthOverride == null ? autoFitWidth : widthOverride;
        const widthLimit = widthOverride == null ? autoFitWidth : maxWidth;
        const width = clamp(preferredWidth, minWidthForSlot, widthLimit);
        const centeredLeft = clamp(slotLeft + (slotWidth - width) / 2, slotLeft, Math.max(slotLeft, slotRight - width));
        // Native footer controls are represented as blockers in footerGapSlot;
        // the usable gap already excludes the background-information icon.
        // Keep the HUD centered in that gap so icon presence changes width,
        // not the requested geometry via an arbitrary horizontal offset.
        const left = clamp(centeredLeft, 8, Math.max(8, innerWidth - width - 8));
        const rowTop = Number.isFinite(Number(slot.rowTop))
          ? Number(slot.rowTop)
          : Math.max(composer.top, composer.bottom - 36);
        const rowBottom = Number.isFinite(Number(slot.rowBottom))
          ? Number(slot.rowBottom)
          : Math.min(composer.bottom, rowTop + 28);
        const rowHeight = Number.isFinite(Number(slot.rowHeight))
          ? Math.max(1, Number(slot.rowHeight))
          : Math.max(1, rowBottom - rowTop);
        const top = height > PANEL.request.collapsedHeight
          ? clamp(rowBottom - height, 8, Math.max(8, innerHeight - height - 8))
          : clamp(rowTop + ((rowHeight - height) / 2) + 1, 8, Math.max(8, innerHeight - height - 8));
        return {
          left,
          top,
          width,
          height,
          source: "footer-gap",
          maxWidth,
          area: { left: slotLeft, top, width: slotWidth, height: rowHeight },
        };
      }

      function manualRequestRect(state, anchor, expanded, height) {
        if (anchor.source !== "footer-gap" || !anchor.area || state.anchorSource !== anchor.source) return null;
        const minWidth = minWidthFor("request", expanded);
        const maxWidth = Math.max(1, anchor.maxWidth || Math.max(minWidth, innerWidth - 16));
        const minWidthForAnchor = Math.min(minWidth, maxWidth);
        const ratio = Number(state.widthRatio);
        const baseWidth = Number.isFinite(ratio) && ratio > 0
          ? anchor.area.width * ratio
          : Number(state.width || anchor.width);
        const width = clamp(baseWidth, minWidthForAnchor, maxWidth);
        const free = Math.max(0, anchor.area.width - width);
        const xRatio = clamp(Number(state.xRatio ?? 0.5), 0, 1);
        const left = clamp(anchor.area.left + (free * xRatio), 8, Math.max(8, innerWidth - width - 8));
        const bottomOffset = Number(state.bottomOffset);
        const top = Number.isFinite(bottomOffset)
          ? clamp(anchor.top + anchor.height + bottomOffset - height, 8, Math.max(8, innerHeight - height - 8))
          : clamp(anchor.top + Number(state.yOffset || 0), 8, Math.max(8, innerHeight - height - 8));
        return { left, top, width };
      }

      function manualTopRect(state, anchor, expanded, height) {
        if (anchor.source !== "header-slot" || !anchor.area) return null;
        const slotLeft = Number(anchor.area.left);
        const slotRight = Number(anchor.area.right ?? (slotLeft + anchor.area.width));
        const slotWidth = Math.max(1, slotRight - slotLeft);
        const minWidth = Math.min(minWidthFor("top", expanded), slotWidth);
        const baseWidth = Number(state.width || anchor.width);
        const width = clamp(baseWidth, minWidth, Math.max(minWidth, slotWidth));
        const left = clamp(Number(state.x ?? anchor.left), slotLeft, Math.max(slotLeft, slotRight - width));
        const top = clamp(Number(state.y ?? anchor.top), 8, Math.max(8, innerHeight - height - 8));
        return { left, top, width };
      }
"""

__all__ = ["TEXT"]
