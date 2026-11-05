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

  function progressBadgeIntrinsicPad(rail) {
    const fullText = String(rail?.dataset?.overflowBadge || "").trim();
    if (!fullText) return 0;
    const badge = rail?.querySelector?.(":scope > .codex-usage-hud-progress-badge");
    if (!badge) return 0;
    const copy = badge.querySelector?.(":scope > .codex-usage-hud-progress-badge-copy");
    const previousText = copy?.textContent;
    const previousDisplay = badge.style.display;
    const previousWidth = badge.style.width;
    // Measure full copy independently of both the rail width and its current
    // hidden/compact state. Restore all presentation before yielding to paint.
    try {
      if (copy) copy.textContent = fullText;
      badge.style.display = "inline-flex";
      badge.style.width = "max-content";
      const width = Math.ceil(Math.max(
        badge.scrollWidth || 0,
        badge.getBoundingClientRect?.().width || 0,
      ));
      const right = Number.parseFloat(getComputedStyle(badge).right || "0") || 0;
      return width > 0 ? Math.max(52, width + right + 10) : 0;
    } finally {
      badge.style.display = previousDisplay;
      badge.style.width = previousWidth;
      if (copy) copy.textContent = previousText;
    }
  }

  function measureProgressRailWidth(rail) {
    const probe = rail?.querySelector?.(":scope > .codex-usage-hud-progress-size-probe");
    if (!probe) return 0;
    // The probe stretches with its rail; only its unshrunk copies are intrinsic.
    const copies = Array.from(probe.children || []);
    if (copies.length) {
      const style = getComputedStyle(probe);
      const badgePad = progressBadgeIntrinsicPad(rail);
      const padding = (Number.parseFloat(style.paddingLeft) || 0)
        + (badgePad || Number.parseFloat(style.paddingRight) || 0);
      const gap = Number.parseFloat(style.columnGap || style.gap) || 0;
      const textWidth = copies.reduce((sum, copy) => (
        sum + Math.max(copy.scrollWidth || 0, copy.getBoundingClientRect().width)
      ), 0);
      return Math.ceil(textWidth + padding + gap * (copies.length - 1) + 2);
    }
    const measured = Math.max(
      probe.scrollWidth || 0,
      probe.getBoundingClientRect?.().width || 0,
    );
    const style = getComputedStyle(probe);
    const currentPadding = Number.parseFloat(style.paddingRight) || 0;
    const badgePad = progressBadgeIntrinsicPad(rail);
    return Math.max(0, Math.ceil(measured + (badgePad ? badgePad - currentPadding : 0) + 2));
  }

  function progressRailContentClipped(rail) {
    const text = rail?.querySelector?.(
      ":scope > .codex-usage-hud-progress-track-text",
    );
    if (!text) return null;
    const copies = Array.from(text.children || []);
    if (!copies.length) return null;
    let measured = false;
    for (const copy of copies) {
      const width = copy.clientWidth || copy.getBoundingClientRect?.().width || 0;
      if (width <= 0) continue;
      measured = true;
      if ((copy.scrollWidth || 0) > width + 0.5) return true;
    }
    return measured ? false : null;
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
    delete node.dataset.overflowAvailable;
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

  function measureCollapsedProgressStrip(node) {
    if (!node?.classList?.contains("codex-usage-hud-progress-strip") || !node.isConnected) return;
    const viewport = progressStripViewport(node);
    if (!viewport) return;
    const rails = Array.from(node.querySelectorAll(":scope > .codex-usage-hud-progress-rail"));
    const widths = rails.map(measureProgressRailWidth);
    if (!widths.length || widths.some((width) => width <= 0)) return;

    const available = Math.max(1, viewport.clientWidth || viewport.getBoundingClientRect().width || 0);
    const gapStyle = getComputedStyle(node);
    const gap = Number.parseFloat(gapStyle.columnGap || gapStyle.gap || "0") || 0;
    const collapsedTailPeekWidth = 40;
    const remainingAfterSession = Math.max(0, available - widths[0] - (gap * Math.max(0, rails.length - 1)));
    const tailShare = remainingAfterSession / Math.max(1, rails.length - 1);
    const required = widths.reduce((sum, width) => sum + width, 0) + (gap * Math.max(0, rails.length - 1));
    // Normal layout fixes the session rail and gives every tail rail the same
    // share. A sum of unequal tail widths still clips the longest label. Keep
    // the intrinsic sum for marquee distance, and size auto-fit for that grid.
    const fitRequired = widths[0]
      + (Math.max(0, ...widths.slice(1)) * (rails.length - 1))
      + (gap * Math.max(0, rails.length - 1));
    const renderedClipped = rails.map(progressRailContentClipped);
    const renderedClippedKnown = renderedClipped.every((value) => value !== null);
    const clipped = renderedClippedKnown
      ? renderedClipped.some(Boolean)
      : required > available + 1;
    return {
      rails, widths, available, required, fitRequired,
      clipped,
      scrolling: rails.length > 1 && clipped && !(tailShare > collapsedTailPeekWidth),
    };
  }

  function refreshCollapsedProgressStrip(node) {
    if (!node?.classList?.contains("codex-usage-hud-progress-strip") || !node.isConnected) return;
    const layout = measureCollapsedProgressStrip(node);
    if (!layout) {
      if (node) {
        clearCollapsedProgressStrip(node);
        delete node.dataset.layoutSignature;
      }
      return;
    }
    const { rails, widths, available, required, scrolling, clipped } = layout;
    const forceScroll = node.closest?.('[data-panel="top"]')?.dataset.topContentScroll === "true";
    const wasOverflowing = node.dataset.overflow === "true";
    const previousAvailable = Number(node.dataset.overflowAvailable);
    const overflowExitSlack = 16;
    const keepOverflowing = wasOverflowing
      && (
        clipped
        || available <= previousAvailable
        || required > available - overflowExitSlack
      );
    if (!scrolling && !(clipped && forceScroll) && !keepOverflowing) {
      clearCollapsedProgressStrip(node);
      delete node.dataset.layoutSignature;
      if (rails.length > 1) {
        rails[0].style.width = `${widths[0]}px`;
        // Keep the session total fixed and split all remaining space evenly.
        node.style.gridTemplateColumns = widths.map((width, index) => (
          index === 0 ? `${width}px` : "minmax(0, 1fr)"
        )).join(" ");
      }
      return;
    }

    const overflow = Math.max(0, Math.ceil(required - available));
    const signature = collapsedProgressStripSignature(widths, overflow);
    if (node.dataset.layoutSignature === signature) return;

    if (!wasOverflowing) clearCollapsedProgressStrip(node);
    if (!wasOverflowing) node.dataset.overflowAvailable = String(available);
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

  function cacheProgressColors(value, tokens = {}) {
    const ratio = normalizeProgressRatio(value);
    function hue(hex) {
      if (!/^#[0-9a-f]{6}$/i.test(String(hex || ""))) return null;
      const [r, g, b] = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255);
      const max = Math.max(r, g, b), min = Math.min(r, g, b), delta = max - min;
      if (delta < .08) return null;
      const sector = max === r ? (g - b) / delta : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
      return (sector * 60 + 360) % 360;
    }
    // Cache is accumulated, reusable memory: misty violet gains clarity as
    // hits accumulate. Keep its hue separate from the two budget tabs, even
    // when a custom Codex theme makes those tabs purple.
    const budgetHues = [tokens.progressDay || "#f3d27a", tokens.progressDayEnd,
      tokens.progressWeek || "#b5dd92", tokens.progressWeekEnd].map(hue).filter((value) => value !== null);
    const distance = (a, b) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b));
    const candidates = [270, 300, 240, 195, 330, 30, 150];
    const separation = (candidate) => Math.min(180, ...budgetHues.map((other) => distance(candidate, other)));
    const selected = candidates.find((candidate) => separation(candidate) >= 42)
      ?? candidates.reduce((best, candidate) => separation(candidate) > separation(best) ? candidate : best, 270);
    const saturation = Math.round(18 + ratio * 50);
    const lightness = Math.round(48 + ratio * 24);
    return {
      start: `hsl(${selected} ${saturation}% ${lightness}%)`,
      end: `hsl(${(selected + 8) % 360} ${saturation + 8}% ${lightness - 6}%)`,
    };
  }

  const progressPaletteCache = new Map();

  function progressColorRgb(value, fallback = [128, 128, 128]) {
    const hex = String(value || "").match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (hex) {
      const expanded = hex[1].length === 3 ? [...hex[1]].map((digit) => digit + digit).join("") : hex[1];
      return [0, 2, 4].map((offset) => parseInt(expanded.slice(offset, offset + 2), 16));
    }
    const hsl = String(value || "").match(/^hsl\(([\d.]+) ([\d.]+)% ([\d.]+)%\)$/);
    if (!hsl) return fallback;
    const h = Number(hsl[1]) / 360, s = Number(hsl[2]) / 100, l = Number(hsl[3]) / 100;
    const channel = (n) => {
      const k = (n + h * 12) % 12;
      return Math.round(255 * (l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
    };
    return [channel(0), channel(8), channel(4)];
  }

  function progressLuminance(rgb) {
    return rgb.reduce((sum, channel, index) => {
      const value = channel / 255;
      return sum + [0.2126, 0.7152, 0.0722][index]
        * (value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    }, 0);
  }

  function readableProgressPalette(start, end) {
    const key = `${start}|${end}`;
    if (progressPaletteCache.has(key)) return progressPaletteCache.get(key);
    const first = progressColorRgb(start), last = progressColorRgb(end);
    const blend = (a, b, ratio) => a.map((value, index) => Math.round(value + (b[index] - value) * ratio));
    const contrast = (a, b, lightInk) => Math.min(...Array.from({ length: 17 }, (_, index) => {
      const luminance = progressLuminance(blend(a, b, index / 16));
      return lightInk ? 1.05 / (luminance + .05) : (luminance + .05) / .05;
    }));
    const lightInk = contrast(first, last, true) >= contrast(first, last, false);
    const target = lightInk ? [0, 0, 0] : [255, 255, 255];
    let amount = 0;
    // Keep the chosen hue. Only nudge a marginal gradient toward light/dark
    // enough for small text, with headroom above WCAG's 4.5:1 minimum.
    if (contrast(first, last, lightInk) < 4.8) {
      let lo = 0, hi = 1;
      for (let step = 0; step < 12; step++) {
        const mid = (lo + hi) / 2;
        if (contrast(blend(first, target, mid), blend(last, target, mid), lightInk) >= 4.8) hi = mid;
        else lo = mid;
      }
      amount = hi;
    }
    const css = (rgb) => `rgb(${rgb.join(", ")})`;
    const palette = { start: css(blend(first, target, amount)), end: css(blend(last, target, amount)),
      ink: lightInk ? "#ffffff" : "#000000" };
    if (progressPaletteCache.size >= 64) progressPaletteCache.delete(progressPaletteCache.keys().next().value);
    progressPaletteCache.set(key, palette);
    return palette;
  }

  function progressRail(metric, themeTokens = {}) {
    const rail = document.createElement("span");
    rail.className = "codex-usage-hud-progress-rail";
    rail.dataset.tone = String(metric?.tone || "day");
    const label = String(metric?.label || "");
    const rightText = String(metric?.rightText || "");
    const overflowBadge = String(metric?.overflowBadge || "");
    const overflowBadgeCompact = String(metric?.overflowBadgeCompact || "");
    const overflowBadgeIcon = String(metric?.overflowBadgeIcon || "");
    const ratio = normalizeProgressRatio(metric?.ratio);
    let colors;
    if (["session", "cache"].includes(rail.dataset.tone)) {
      colors = cacheProgressColors(ratio, themeTokens);
      rail.style.setProperty("--codex-usage-hud-cache-fill", colors.start);
      rail.style.setProperty("--codex-usage-hud-cache-fill-end", colors.end);
    } else {
      const defaults = { day: ["#f3d27a", "#f3d37f"], week: ["#b5dd92", "#aede95"], error: ["#ff875a", "#ff6b6b"] };
      const tone = rail.dataset.tone;
      const prefix = tone === "week" ? "progressWeek" : tone === "error" ? "progressOverflow" : "progressDay";
      const fallback = defaults[tone] || defaults.day;
      colors = { start: themeTokens[prefix] || fallback[0],
        end: themeTokens[tone === "error" ? "error" : `${prefix}End`] || fallback[1] };
    }
    const palette = readableProgressPalette(colors.start, colors.end);
    const track = progressColorRgb(themeTokens.progressTrack || "#262c33");
    rail.style.setProperty("--codex-usage-hud-progress-track-ink", progressLuminance(track) > .179 ? "#000000" : "#ffffff");
    rail.style.setProperty("--codex-usage-hud-progress-fill-ink", palette.ink);
    const fillWidth = `${Math.round(ratio * 1000) / 10}%`;
    rail.style.setProperty("--codex-usage-hud-progress-fill-width", fillWidth);
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
    const tooltip = String(metric?.tooltip || (overflowBadge ? `${fullText || label} | ${badgeTooltip}` : fullText));
    rail.title = tooltip;
    rail.setAttribute("aria-label", tooltip);

    function progressTextLayer(className, textClass = "codex-usage-hud-progress-text") {
      const layer = document.createElement("span");
      layer.className = className;
      layer.title = tooltip;
      if (rightText) {
        const leftNode = document.createElement("span");
        leftNode.className = textClass;
        leftNode.dataset.progressLabel = "true";
        leftNode.textContent = label;
        leftNode.title = tooltip;
        const rightNode = document.createElement("span");
        rightNode.className = textClass === "codex-usage-hud-progress-text"
          ? "codex-usage-hud-progress-right-text"
          : textClass;
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
    fill.style.width = fillWidth;
    fill.style.background = `linear-gradient(90deg in srgb, ${palette.start}, ${palette.end})`;
    rail.appendChild(fill);
    rail.appendChild(progressTextLayer("codex-usage-hud-progress-track-text"));
    const filledText = progressTextLayer("codex-usage-hud-progress-track-text codex-usage-hud-progress-fill-text");
    filledText.setAttribute("aria-hidden", "true");
    rail.appendChild(filledText);
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

  function renderProgressList(container, metrics, themeTokens = {}) {
    if (!container) return false;
    const items = Array.isArray(metrics) ? metrics.filter((item) => item && item.label) : [];
    container.querySelectorAll(":scope > .codex-usage-hud-progress-rail").forEach((rail) => {
      badgeResizeObserver?.unobserve(rail);
    });
    container.replaceChildren();
    if (items.length > 0) container.dataset.count = String(items.length);
    else delete container.dataset.count;
    for (const item of items) {
      const rail = progressRail(item, themeTokens);
      container.appendChild(rail);
      badgeResizeObserver?.observe(rail);
    }
    ctx.lifecycle.frame("budget", () => {
      container.querySelectorAll(":scope > .codex-usage-hud-progress-rail").forEach(refreshProgressRailLabel);
      if (container.classList.contains("codex-usage-hud-progress-strip")) {
        refreshCollapsedProgressStrip(container);
        ctx.domains?.get("layout")?.refreshTopContentFit();
      }
    });
    return items.length > 0;
  }

  function renderTopProgress(root, payload) {
    const progress = payload?.topProgress || {};
    const themeTokens = payload?.theme?.tokens || {};
    const main = root.querySelector(`.${topClass} .codex-usage-hud-main`);
    const collapsed = root.querySelector('[data-field="topCollapsedProgress"]');
    const hasCollapsed = renderProgressList(collapsed, progress.collapsed || [], themeTokens);
    if (main) main.dataset.progress = hasCollapsed ? "true" : "false";
    renderProgressList(root.querySelector('[data-field="topCacheProgress"]'), progress.cache ? [progress.cache] : [], themeTokens);
    renderProgressList(root.querySelector('[data-field="topBudgetProgress"]'), progress.budget || [], themeTokens);
    // The three lists share one coalesced budget frame. Refresh the entire
    // domain last, so the collapsed strip cannot lose its callback to a list.
    ctx.lifecycle.frame("budget", () => refresh(root));
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
      ctx.domains?.get("layout")?.refreshTopContentFit();
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
      progressPaletteCache.clear();
      return wasInstalled;
    }

    return {
      install,
      apply,
      dispose,
      refresh,
      refreshProgressRailLabel,
      measureCollapsedProgressStrip,
      refreshCollapsedProgressStrip,
    };
  }

  const budgetDomain = ctx.domains.register(
    "budget",
    createBudgetDomain(ctx, shared),
  );
"""

__all__ = ["TEXT"]
