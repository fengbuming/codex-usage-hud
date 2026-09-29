"""Renderer budget JavaScript domain."""

TEXT = r"""
  function createBudgetDomain(ctx, shared) {
  let badgeLayoutCache = new WeakMap();
  let badgeResizeObserver = null;
  let stopFontWatch = null;
  function progressStripViewport(node) {
    const parent = node?.parentElement;
    if (!parent?.classList?.contains("codex-usage-hud-progress-strip-viewport")) return null;
    return parent;
  }

  function measureProgressRailWidth(rail) {
    const probe = rail?.querySelector?.(":scope > .codex-usage-hud-progress-size-probe");
    if (!probe) return 0;
    const measured = Math.max(
      probe.scrollWidth || 0,
      probe.getBoundingClientRect?.().width || 0,
    );
    return Math.max(0, Math.ceil(measured + 2));
  }

  function progressBadgeCandidates(rail) {
    const full = String(rail?.dataset?.overflowBadge || "").trim();
    const compact = String(rail?.dataset?.overflowBadgeCompact || "").trim();
    const candidates = [];
    if (full) candidates.push(full);
    if (compact && compact !== full) candidates.push(compact);
    return candidates;
  }

  function setProgressBadgeText(rail, text) {
    const badge = rail?.querySelector?.(":scope > .codex-usage-hud-progress-badge");
    if (!badge) return;
    const copy = badge.querySelector(":scope > .codex-usage-hud-progress-badge-copy");
    const value = String(text || "");
    if (copy && copy.textContent !== value) copy.textContent = value;
  }

  function progressRailLeftLabelFits(rail) {
    const label = rail?.querySelector?.(
      ":scope > .codex-usage-hud-progress-track-text .codex-usage-hud-progress-text",
    );
    if (!label) return true;
    const available = label.clientWidth || label.getBoundingClientRect?.().width || 0;
    if (available <= 0) return true;
    return label.scrollWidth <= available + 0.5;
  }

  function applyProgressBadgePad(rail) {
    if (!rail) return;
    const badge = rail.querySelector(":scope > .codex-usage-hud-progress-badge");
    if (!badge) {
      rail.style.removeProperty("--codex-usage-hud-progress-badge-pad");
      return;
    }
    const styles = getComputedStyle(badge);
    const right = Number.parseFloat(styles.right || "0") || 0;
    const width = Math.ceil(Math.max(
      badge.scrollWidth || 0,
      badge.getBoundingClientRect?.().width || 0,
    ));
    // Keep a small gap between fixed left usage text and the shrinkable badge.
    const pad = Math.max(52, width + right + 10);
    const value = `${pad}px`;
    if (rail.style.getPropertyValue("--codex-usage-hud-progress-badge-pad") !== value) {
      rail.style.setProperty("--codex-usage-hud-progress-badge-pad", value);
    }
  }

  function refreshProgressRailBadge(rail) {
    if (!rail) return;
    const candidates = progressBadgeCandidates(rail);
    if (!candidates.length) {
      badgeLayoutCache.delete(rail);
      rail.style.removeProperty("--codex-usage-hud-progress-badge-pad");
      return;
    }
    const badge = rail.querySelector(":scope > .codex-usage-hud-progress-badge");
    if (!badge) return;

    const panel = rail.closest?.("[data-panel]");
    const layoutKey = JSON.stringify([
      candidates,
      rail.style.width,
      panel?.style.width,
      panel?.dataset?.expanded,
      globalThis.innerWidth || 0,
      globalThis.devicePixelRatio || 1,
    ]);
    if (badgeLayoutCache.get(rail) === layoutKey) return;

    // Prefer full badge copy. Fall back to cost-only only when the full badge
    // squeezes the fixed left usage/amount label.
    badge.style.display = "";
    rail.dataset.badge = "true";
    let fits = false;
    for (const candidate of candidates) {
      setProgressBadgeText(rail, candidate);
      applyProgressBadgePad(rail);
      if (progressRailLeftLabelFits(rail)) {
        fits = true;
        break;
      }
    }
    if (!fits) {
      badge.style.display = "none";
      delete rail.dataset.badge;
      rail.style.removeProperty("--codex-usage-hud-progress-badge-pad");
    }
    badgeLayoutCache.set(rail, layoutKey);
  }

  function refreshProgressRailLabel(rail) {
    // Left usage/amount stays fixed; only the overflow badge shrinks under pressure.
    refreshProgressRailBadge(rail);
  }

  function clearCollapsedProgressStrip(node) {
    if (!node) return;
    delete node.dataset.overflow;
    node.style.removeProperty("--codex-usage-hud-progress-strip-distance");
    node.style.removeProperty("--codex-usage-hud-progress-strip-duration");
    node.style.removeProperty("grid-template-columns");
    node.style.removeProperty("width");
    node.style.removeProperty("min-width");
    node.querySelectorAll(":scope > .codex-usage-hud-progress-rail").forEach((rail) => {
      rail.style.removeProperty("width");
    });
  }

  function collapsedProgressStripSignature(widths, overflow) {
    return `${widths.join(",")}|${overflow}`;
  }

  function refreshCollapsedProgressStrip(node) {
    if (!node?.classList?.contains("codex-usage-hud-progress-strip") || !node.isConnected) return;
    const viewport = progressStripViewport(node);
    if (!viewport) return;
    const rails = Array.from(node.querySelectorAll(":scope > .codex-usage-hud-progress-rail"));
    if (rails.length <= 1) {
      clearCollapsedProgressStrip(node);
      delete node.dataset.layoutSignature;
      return;
    }

    const widths = rails.map(measureProgressRailWidth);
    if (widths.some((width) => width <= 0)) {
      clearCollapsedProgressStrip(node);
      delete node.dataset.layoutSignature;
      return;
    }

    const available = Math.max(1, viewport.clientWidth || viewport.getBoundingClientRect().width || 0);
    const gapStyle = getComputedStyle(node);
    const gap = Number.parseFloat(gapStyle.columnGap || gapStyle.gap || "0") || 0;
    const collapsedTailPeekWidth = 40;
    const remainingAfterSession = Math.max(0, available - widths[0] - (gap * Math.max(0, rails.length - 1)));
    const tailShare = remainingAfterSession / Math.max(1, rails.length - 1);
    const required = widths.reduce((sum, width) => sum + width, 0) + (gap * Math.max(0, rails.length - 1));
    if (required <= available + 1 || tailShare > collapsedTailPeekWidth) {
      clearCollapsedProgressStrip(node);
      delete node.dataset.layoutSignature;
      rails[0].style.width = `${widths[0]}px`;
      return;
    }

    const overflow = Math.ceil(required - available);
    const signature = collapsedProgressStripSignature(widths, overflow);
    if (node.dataset.layoutSignature === signature) return;

    clearCollapsedProgressStrip(node);
    node.dataset.layoutSignature = signature;
    rails.forEach((rail, index) => {
      rail.style.width = `${widths[index]}px`;
    });
    node.dataset.overflow = "true";
    node.style.gridTemplateColumns = widths.map((width) => `${width}px`).join(" ");
    node.style.width = `${required}px`;
    node.style.minWidth = `${required}px`;
    node.style.setProperty("--codex-usage-hud-progress-strip-distance", `${overflow}px`);
    node.style.setProperty(
      "--codex-usage-hud-progress-strip-duration",
      `${Math.max(5000, 3200 + (overflow * 55))}ms`,
    );
  }


  function normalizeProgressRatio(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return 0;
    return clamp(number, 0, 1);
  }

  function progressRail(metric) {
    const rail = document.createElement("span");
    rail.className = "codex-usage-hud-progress-rail";
    rail.dataset.tone = String(metric?.tone || "day");
    const label = String(metric?.label || "");
    const rightText = String(metric?.rightText || "");
    const overflowBadge = String(metric?.overflowBadge || "");
    const overflowBadgeCompact = String(metric?.overflowBadgeCompact || "");
    const overflowBadgeIcon = String(metric?.overflowBadgeIcon || "");
    const ratio = normalizeProgressRatio(metric?.ratio);
    const overflowRatio = normalizeProgressRatio(metric?.overflowRatio);
    const hasOverflow = overflowRatio > 0;
    if (hasOverflow) rail.dataset.overflow = "true";
    else delete rail.dataset.overflow;
    if (overflowBadge) rail.dataset.badge = "true";
    else delete rail.dataset.badge;
    if (rightText) rail.dataset.rightText = rightText;
    else delete rail.dataset.rightText;
    if (overflowBadge) rail.dataset.overflowBadge = overflowBadge;
    else delete rail.dataset.overflowBadge;
    if (overflowBadgeCompact) rail.dataset.overflowBadgeCompact = overflowBadgeCompact;
    else delete rail.dataset.overflowBadgeCompact;
    const fullText = rightText ? `${label} / ${rightText}` : label;
    const badgeTooltip = [overflowBadgeIcon, overflowBadge].filter(Boolean).join(" ");
    const tooltip = overflowBadge ? `${fullText || label} | ${badgeTooltip}` : fullText;
    rail.title = tooltip;
    rail.setAttribute("aria-label", tooltip);

    function progressTextLayer(className, textClass = "codex-usage-hud-progress-text") {
      const layer = document.createElement("span");
      layer.className = className;
      layer.title = tooltip;
      if (rightText && textClass === "codex-usage-hud-progress-text") {
        const leftNode = document.createElement("span");
        leftNode.className = textClass;
        leftNode.dataset.progressLabel = "true";
        leftNode.textContent = label;
        leftNode.title = tooltip;
        const rightNode = document.createElement("span");
        rightNode.className = "codex-usage-hud-progress-right-text";
        rightNode.textContent = rightText;
        rightNode.title = tooltip;
        layer.append(leftNode, rightNode);
        return layer;
      }
      const textNode = document.createElement("span");
      textNode.className = textClass;
      textNode.dataset.progressLabel = "true";
      textNode.textContent = fullText;
      textNode.title = tooltip;
      layer.appendChild(textNode);
      return layer;
    }

    rail.appendChild(progressTextLayer("codex-usage-hud-progress-size-probe", "codex-usage-hud-progress-probe-text"));

    const fill = document.createElement("span");
    fill.className = "codex-usage-hud-progress-fill";
    fill.style.width = `${Math.round(ratio * 1000) / 10}%`;
    rail.appendChild(fill);
    rail.appendChild(progressTextLayer("codex-usage-hud-progress-track-text"));
    if (hasOverflow) {
      const overflow = document.createElement("span");
      overflow.className = "codex-usage-hud-progress-overflow";
      const overflowWidth = `${Math.round(overflowRatio * 1000) / 10}%`;
      overflow.style.width = overflowWidth;
      rail.style.setProperty("--codex-usage-hud-progress-overflow-width", overflowWidth);
      rail.appendChild(overflow);

      const anchor = document.createElement("span");
      anchor.className = "codex-usage-hud-progress-overflow-anchor";
      rail.appendChild(anchor);
    }
    if (overflowBadge) {
      const badge = document.createElement("span");
      badge.className = "codex-usage-hud-progress-badge";
      if (overflowBadgeIcon) {
        const icon = document.createElement("span");
        icon.className = "codex-usage-hud-progress-badge-icon";
        icon.textContent = overflowBadgeIcon;
        icon.setAttribute("aria-hidden", "true");
        badge.appendChild(icon);
      }
      const copy = document.createElement("span");
      copy.className = "codex-usage-hud-progress-badge-copy";
      copy.textContent = overflowBadge;
      badge.appendChild(copy);
      rail.appendChild(badge);
    }
    return rail;
  }

  function renderProgressList(container, metrics) {
    if (!container) return false;
    const items = Array.isArray(metrics) ? metrics.filter((item) => item && item.label) : [];
    container.querySelectorAll(":scope > .codex-usage-hud-progress-rail").forEach((rail) => {
      badgeResizeObserver?.unobserve(rail);
    });
    container.replaceChildren();
    if (items.length > 0) container.dataset.count = String(items.length);
    else delete container.dataset.count;
    for (const item of items) {
      const rail = progressRail(item);
      container.appendChild(rail);
      badgeResizeObserver?.observe(rail);
    }
    ctx.lifecycle.frame("budget", () => {
      container.querySelectorAll(":scope > .codex-usage-hud-progress-rail").forEach(refreshProgressRailLabel);
      if (container.classList.contains("codex-usage-hud-progress-strip")) {
        refreshCollapsedProgressStrip(container);
      }
    });
    return items.length > 0;
  }

  function renderTopProgress(root, payload) {
    const progress = payload?.topProgress || {};
    const main = root.querySelector(`.${topClass} .codex-usage-hud-main`);
    const collapsed = root.querySelector('[data-field="topCollapsedProgress"]');
    const hasCollapsed = renderProgressList(collapsed, progress.collapsed || []);
    if (main) main.dataset.progress = hasCollapsed ? "true" : "false";
    renderProgressList(root.querySelector('[data-field="topCacheProgress"]'), progress.cache ? [progress.cache] : []);
    renderProgressList(root.querySelector('[data-field="topBudgetProgress"]'), progress.budget || []);
  }

    let installed = false;

    function install() {
      if (installed) return false;
      installed = true;
      if (typeof ResizeObserver === "function" && ctx.observers?.set) {
        badgeResizeObserver = ctx.observers.set("budget_resize", new ResizeObserver((entries) => {
          for (const { target } of entries) {
            if (!target?.isConnected) continue;
            badgeLayoutCache.delete(target);
            refreshProgressRailBadge(target);
          }
        }));
      }
      if (document.fonts?.addEventListener && ctx.lifecycle?.listen) {
        stopFontWatch = ctx.lifecycle.listen("budget_fonts", document.fonts, "loadingdone", () => {
          badgeLayoutCache = new WeakMap();
          refresh(document.getElementById(rootId));
        });
      }
      return true;
    }

    function apply(root, payload) {
      if (!installed) install();
      renderTopProgress(root, payload || {});
    }

    function refresh(root) {
      if (!root) return;
      root.querySelectorAll(".codex-usage-hud-progress-rail").forEach(refreshProgressRailLabel);
      root.querySelectorAll(".codex-usage-hud-progress-strip").forEach(refreshCollapsedProgressStrip);
    }

    function dispose() {
      const wasInstalled = installed;
      installed = false;
      ctx.frames.cancel("budget");
      ctx.observers?.clear("budget_resize");
      badgeResizeObserver = null;
      stopFontWatch?.();
      stopFontWatch = null;
      badgeLayoutCache = new WeakMap();
      return wasInstalled;
    }

    return {
      install,
      apply,
      dispose,
      refresh,
      refreshProgressRailLabel,
      refreshCollapsedProgressStrip,
    };
  }

  const budgetDomain = ctx.domains.register(
    "budget",
    createBudgetDomain(ctx, shared),
  );
"""

__all__ = ["TEXT"]
