"""Renderer theme JavaScript domain."""

TEXT = r"""
  function createThemeDomain(ctx, shared) {
  function applyTheme(root, payload) {
    if (!root) return;
    const themePayload = payload?.theme;
    if (!themePayload || typeof themePayload !== "object" || Object.keys(themePayload).length === 0) {
      return;
    }
    const tokens = themePayload.tokens || {};
    const variant = String(themePayload.variant || "dark").toLowerCase() === "light" ? "light" : "dark";
    const defaults = {
      surface: "#10161d",
      panelSurface: "#141b24",
      panelBorder: "#3a485a",
      headerSurface: "#202833",
      divider: "#273241",
      text: "#e8eef7",
      muted: "#8492a6",
      accent: "#f3d27a",
      info: "#9ccbff",
      warning: "#ffb86b",
      error: "#ff6b6b",
      success: "#8fe3a1",
      requestSurface: "#0b1016",
      requestHeaderSurface: "#151d27",
      requestPanelSurface: "#101821",
      requestText: "#dce7f2",
      requestMuted: "#718095",
      progressTrack: "#262c33",
      progressTrackBorder: "#3b4149",
      progressTrackText: "#c1c7d0",
      progressCache: "#b5a0d4",
      progressCacheEnd: "#9876c5",
      progressCacheText: "#07131f",
      progressDay: "#f3d27a",
      progressDayEnd: "#f3d37f",
      progressDayText: "#111111",
      progressWeek: "#b5dd92",
      progressWeekEnd: "#aede95",
      progressWeekText: "#111111",
      progressOverflow: "#ff875a",
      progressOverflowHighlight: "#ffd8bd",
      progressOverflowAnchor: "#ff6b64",
      progressOverflowAnchorEdge: "#ffc3a4",
      progressOverflowBadge: "#7f3e3a",
      progressOverflowBadgeEdge: "#ff875a",
      progressOverflowBadgeText: "#ffd7ca",
    };
    const resolved = { ...defaults, ...(tokens || {}) };
    const variableEntries = [
      ["--codex-usage-hud-surface", resolved.surface],
      ["--codex-usage-hud-panel-surface", resolved.panelSurface],
      ["--codex-usage-hud-panel-border", resolved.panelBorder],
      ["--codex-usage-hud-header-surface", resolved.headerSurface],
      ["--codex-usage-hud-divider", resolved.divider],
      ["--codex-usage-hud-text", resolved.text],
      ["--codex-usage-hud-muted", resolved.muted],
      ["--codex-usage-hud-accent", resolved.accent],
      ["--codex-usage-hud-info", resolved.info],
      ["--codex-usage-hud-warning", resolved.warning],
      ["--codex-usage-hud-error", resolved.error],
      ["--codex-usage-hud-success", resolved.success],
      ["--codex-usage-hud-request-surface", resolved.requestSurface],
      ["--codex-usage-hud-request-header-surface", resolved.requestHeaderSurface],
      ["--codex-usage-hud-request-panel-surface", resolved.requestPanelSurface],
      ["--codex-usage-hud-request-text", resolved.requestText],
      ["--codex-usage-hud-request-muted", resolved.requestMuted],
      ["--codex-usage-hud-progress-track", resolved.progressTrack],
      ["--codex-usage-hud-progress-track-border", resolved.progressTrackBorder],
      ["--codex-usage-hud-progress-track-text", resolved.progressTrackText],
      ["--codex-usage-hud-progress-cache", resolved.progressCache],
      ["--codex-usage-hud-progress-cache-end", resolved.progressCacheEnd],
      ["--codex-usage-hud-progress-cache-text", resolved.progressCacheText],
      ["--codex-usage-hud-progress-day", resolved.progressDay],
      ["--codex-usage-hud-progress-day-end", resolved.progressDayEnd],
      ["--codex-usage-hud-progress-day-text", resolved.progressDayText],
      ["--codex-usage-hud-progress-week", resolved.progressWeek],
      ["--codex-usage-hud-progress-week-end", resolved.progressWeekEnd],
      ["--codex-usage-hud-progress-week-text", resolved.progressWeekText],
      ["--codex-usage-hud-progress-overflow", resolved.progressOverflow],
      ["--codex-usage-hud-progress-overflow-highlight", resolved.progressOverflowHighlight],
      ["--codex-usage-hud-progress-overflow-anchor", resolved.progressOverflowAnchor],
      ["--codex-usage-hud-progress-overflow-anchor-edge", resolved.progressOverflowAnchorEdge],
      ["--codex-usage-hud-progress-overflow-badge", resolved.progressOverflowBadge],
      ["--codex-usage-hud-progress-overflow-badge-edge", resolved.progressOverflowBadgeEdge],
      ["--codex-usage-hud-progress-overflow-badge-text", resolved.progressOverflowBadgeText],
    ];
    root.dataset.themeVariant = variant;
    for (const [name, value] of variableEntries) {
      root.style.setProperty(name, String(value || ""));
    }
  }


  function readThemeStorage(key) {
    return ctx.storage.read(localStorage, key);
  }

  function parseThemeStorageJson(value) {
    const text = String(value || "").trim();
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (_) {
      return null;
    }
  }

  function normalizeThemeHex(value) {
    const text = String(value || "").trim().toLowerCase();
    if (!text) return "";
    const shortHex = text.match(/^#([0-9a-f]{3})$/i);
    if (shortHex) {
      return `#${shortHex[1][0]}${shortHex[1][0]}${shortHex[1][1]}${shortHex[1][1]}${shortHex[1][2]}${shortHex[1][2]}`;
    }
    const longHex = text.match(/^#([0-9a-f]{6})$/i);
    if (longHex) return `#${longHex[1]}`;
    const rgbMatch = text.match(/^rgba?\(([^)]+)\)$/i);
    if (!rgbMatch) return "";
    const parts = rgbMatch[1].split(",").map((item) => item.trim());
    if (parts.length < 3) return "";
    const channels = parts.slice(0, 3).map((item) => {
      if (item.endsWith("%")) {
        const numeric = Number.parseFloat(item.slice(0, -1));
        if (!Number.isFinite(numeric)) return null;
        return Math.max(0, Math.min(255, Math.round((numeric / 100) * 255)));
      }
      const numeric = Number.parseFloat(item);
      if (!Number.isFinite(numeric)) return null;
      return Math.max(0, Math.min(255, Math.round(numeric)));
    });
    if (channels.some((channel) => channel == null)) return "";
    return `#${channels.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
  }

  function rendererThemeSnapshot() {
    const root = document.documentElement;
    if (!root) return null;
    const css = getComputedStyle(root);
    const cssValue = (...names) => {
      for (const name of names) {
        const value = String(css.getPropertyValue(name) || "").trim();
        if (value) return value;
      }
      return "";
    };
    const colorValue = (...names) => normalizeThemeHex(cssValue(...names));
    const rawMode = String(readThemeStorage("appearanceTheme") || "").trim().toLowerCase();
    const mode = ["system", "light", "dark"].includes(rawMode) ? rawMode : "system";
    const classList = Array.from(root.classList || []);
    const classText = classList.join(" ").toLowerCase();
    const colorScheme = String(css.colorScheme || "").trim().toLowerCase();
    const prefersDark = !!window.matchMedia?.("(prefers-color-scheme: dark)")?.matches;
    let effectiveVariant = prefersDark ? "dark" : "light";
    if (colorScheme.includes("dark") || classText.includes("dark")) effectiveVariant = "dark";
    else if (colorScheme.includes("light") || classText.includes("light")) effectiveVariant = "light";
    return {
      mode,
      lightCodeThemeId: String(readThemeStorage("appearanceLightCodeThemeId") || "").trim(),
      darkCodeThemeId: String(readThemeStorage("appearanceDarkCodeThemeId") || "").trim(),
      lightTheme: parseThemeStorageJson(readThemeStorage("appearanceLightChromeTheme")),
      darkTheme: parseThemeStorageJson(readThemeStorage("appearanceDarkChromeTheme")),
      effectiveVariant,
      classList,
      colorScheme,
      cssTheme: {
        accent: colorValue("--codex-base-accent", "--color-text-accent", "--vscode-focusBorder", "--vscode-button-background", "--vscode-textLink-foreground"),
        surface: colorValue("--codex-base-surface", "--color-background-surface", "--vscode-editor-background", "--vscode-sideBar-background", "--vscode-panel-background", "--vscode-activityBar-background"),
        ink: colorValue("--codex-base-ink", "--color-text-foreground", "--vscode-editor-foreground", "--vscode-foreground", "--vscode-sideBarTitle-foreground"),
        diffAdded: colorValue("--color-decoration-added", "--vscode-gitDecoration-addedResourceForeground", "--vscode-terminal-ansiGreen"),
        diffRemoved: colorValue("--color-decoration-deleted", "--vscode-gitDecoration-deletedResourceForeground", "--vscode-terminal-ansiRed"),
        skill: colorValue("--color-accent-purple", "--vscode-terminal-ansiMagenta", "--vscode-textLink-foreground", "--vscode-terminal-ansiBlue"),
      },
    };
  }

  function reportRendererTheme(reason = "event") {
    const snapshot = rendererThemeSnapshot();
    if (!snapshot || !ctx.bindings.available(themeBindingName)) return false;
    const signature = JSON.stringify(snapshot);
    if (window[themeSignatureName] === signature) return false;
    try {
      ctx.bindings.send(themeBindingName, {
        ...snapshot,
        reason: String(reason || "event"),
        observedAt: Date.now(),
      });
      window[themeSignatureName] = signature;
      return true;
    } catch (_) {
      return false;
    }
  }

  function scheduleRendererThemeReport(reason = "event") {
    ctx.lifecycle.clearTimeout(window[themeTimerName] || 0);
    window[themeTimerName] = ctx.lifecycle.timeout("theme", () => {
      reportRendererTheme(reason);
    }, 0);
  }

  function stopRendererThemeObserver() {
    ctx.lifecycle.disposeScope("theme_listeners");
    ctx.observers.clear("theme");
    ctx.lifecycle.clearTimeout(window[themeTimerName] || 0);
    delete window[themeObserverName];
    delete window[themeMediaQueryName];
    delete window[themeMediaQueryHandlerName];
    delete window[themeStorageHandlerName];
    delete window[themeTimerName];
  }

  function startRendererThemeObserver() {
    const root = document.documentElement;
    if (!root) return false;
    stopRendererThemeObserver();
    window[themeObserverName] = ctx.observers.set("theme", new MutationObserver(() => {
      scheduleRendererThemeReport("dom-theme-change");
    }));
    window[themeObserverName].observe(root, {
      attributes: true,
      attributeFilter: ["class", "style", "data-theme", "data-color-scheme"],
    });
    if (document.body) {
      window[themeObserverName].observe(document.body, {
        attributes: true,
        attributeFilter: ["class", "style", "data-theme", "data-color-scheme"],
      });
    }
    const mediaQuery = window.matchMedia?.("(prefers-color-scheme: dark)");
    const themeScope = ctx.lifecycle.scope("theme_listeners");
    if (mediaQuery) {
      const handler = () => scheduleRendererThemeReport("system-theme-change");
      window[themeMediaQueryName] = mediaQuery;
      window[themeMediaQueryHandlerName] = handler;
      themeScope.listen(mediaQuery, "change", handler);
    }
    const storageHandler = (event) => {
      if (!event?.key || [
        "appearanceTheme",
        "appearanceLightChromeTheme",
        "appearanceDarkChromeTheme",
        "appearanceLightCodeThemeId",
        "appearanceDarkCodeThemeId",
      ].includes(String(event.key))) {
        scheduleRendererThemeReport("storage-theme-change");
      }
    };
    window[themeStorageHandlerName] = storageHandler;
    themeScope.listen(window, "storage", storageHandler);
    scheduleRendererThemeReport("bootstrap");
    return true;
  }

  window.__codexUsageHudReportTheme = (reason = "manual") => {
    startRendererThemeObserver();
    return reportRendererTheme(String(reason || "manual"));
  };

    let installed = false;

    function install() {
      if (installed) return false;
      installed = true;
      if (startRendererThemeObserver()) return true;
      installed = false;
      return false;
    }

    function apply(root, payload) {
      applyTheme(root, payload || {});
    }

    function dispose() {
      const wasInstalled = installed;
      installed = false;
      stopRendererThemeObserver();
      delete window.__codexUsageHudReportTheme;
      return wasInstalled;
    }

    return { install, apply, dispose };
  }

  const themeDomain = ctx.domains.register(
    "theme",
    createThemeDomain(ctx, shared),
  );
"""

__all__ = ["TEXT"]
