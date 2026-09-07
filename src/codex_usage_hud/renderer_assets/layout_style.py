"""Static raw Renderer layout asset fragment."""

TEXT = r"""
  function createLayoutDomain(ctx, shared) {
      function ensureStyle() {
        const existing = document.getElementById(styleId);
        if (existing?.dataset.version === version) return;
        existing?.remove();
        const style = document.createElement("style");
        style.id = styleId;
        style.dataset.version = version;
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
          #${rootId} .codex-usage-hud-panel[data-awaiting-chrome="true"] {
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
          #${rootId} .codex-usage-hud-activity-task-nav {
            min-width: 0;
            display: inline-flex;
            align-items: center;
            gap: 4px;
          }
          #${rootId} .codex-usage-hud-activity-task-nav[hidden] {
            display: none;
          }
          #${rootId} .codex-usage-hud-activity-task-button {
            box-sizing: border-box;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 22px;
            width: 22px;
            height: 22px;
            padding: 0;
            border: 1px solid rgba(132, 146, 166, .24);
            border-radius: 5px;
            background: #1c2330;
            color: #c9d8e8;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-activity-task-button svg {
            width: 14px;
            height: 14px;
            fill: none;
            stroke: currentColor;
            stroke-linecap: round;
            stroke-linejoin: round;
            stroke-width: 2;
          }
          #${rootId} .codex-usage-hud-activity-task-button:hover:not(:disabled) {
            border-color: rgba(243, 210, 122, .52);
            color: #f3d27a;
          }
          #${rootId} .codex-usage-hud-activity-task-button:disabled {
            opacity: .36;
            cursor: default;
          }
          #${rootId} .codex-usage-hud-activity-task-index {
            min-width: 54px;
            padding-left: 7px;
            padding-right: 7px;
            overflow: hidden;
            text-overflow: ellipsis;
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
          }
          #${rootId} .codex-usage-hud-alert[hidden] {
            display: none !important;
          }
          #${rootId} .codex-usage-hud-alert-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #ffb86b;
          }
          #${rootId} .codex-usage-hud-alert-title {
            color: #ffb86b;
            font-size: 11px;
            font-weight: 800;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-alert [data-field="topWarnings"] {
            min-width: 0;
            line-height: 1.35;
          }
          #${rootId} .codex-usage-hud-alert-close {
            width: 22px;
            height: 22px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border: 0;
            border-radius: 5px;
            background: transparent;
            color: #ffb86b;
            font-family: "Microsoft YaHei UI", system-ui, sans-serif;
            font-size: 16px;
            font-weight: 800;
            line-height: 1;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-alert-close:hover {
            background: rgba(255, 184, 107, .12);
          }
          #${rootId} .codex-usage-hud-cache-pill {
            min-width: 0;
            height: 24px;
          }
          #${rootId} .codex-usage-hud-cache-pill:empty {
            display: none;
          }
          #${rootId} .codex-usage-hud-budget-rails {
            display: grid;
            gap: 7px;
          }
          #${rootId} .codex-usage-hud-budget-rails:empty {
            display: none;
          }
          #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-rail {
            height: 34px;
          }
          #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-track-text {
            padding: 0 13px;
            font-size: 12.5px;
          }
          #${rootId} .codex-usage-hud-activity-card {
            display: grid;
            grid-template-rows: auto auto auto auto minmax(0, 1fr);
            height: 100%;
            min-height: 0;
          }
          #${rootId} .codex-usage-hud-activity-main {
            box-sizing: border-box;
            min-width: 0;
            display: grid;
            gap: 4px;
            margin-bottom: 8px;
            padding: 10px 12px;
            border: 1px solid rgba(132, 146, 166, .16);
            border-radius: 6px;
            background: #101821;
          }
          #${rootId} .codex-usage-hud-activity-main strong {
            margin-right: 10px;
            color: #9ccbff;
            font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
            font-size: 12.5px;
          }
          #${rootId} .codex-usage-hud-activity-step {
            box-sizing: border-box;
            min-width: 0;
            display: grid;
            gap: 4px;
            margin-bottom: 12px;
            padding: 8px 10px;
            border: 1px solid rgba(132, 146, 166, .16);
            border-radius: 6px;
            background: #101821;
          }
          #${rootId} .codex-usage-hud-activity-metrics {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px;
            margin-bottom: 12px;
          }
          #${rootId} .codex-usage-hud-activity-metric {
            box-sizing: border-box;
            min-width: 0;
            padding: 8px 10px;
            border: 1px solid rgba(132, 146, 166, .16);
            border-radius: 6px;
            background: #101821;
          }
          #${rootId} .codex-usage-hud-activity-metric .codex-usage-hud-value {
            color: #f3d27a;
            font-size: 15px;
            font-weight: 800;
          }
          #${rootId} .codex-usage-hud-activity-trail {
            display: grid;
            grid-template-rows: auto auto auto;
            gap: 8px;
            min-width: 0;
            min-height: 0;
          }
          #${rootId} .codex-usage-hud-activity-trail-head {
            min-width: 0;
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-activity-trail .codex-usage-hud-value {
            font-size: 10.5px;
          }
          #${rootId} .codex-usage-hud-activity-timeline {
            min-width: 0;
            min-height: calc(34px * 4 + 7px * 3);
            height: calc(34px * 4 + 7px * 3);
            max-height: calc(34px * 4 + 7px * 3);
            display: grid;
            grid-auto-rows: minmax(34px, auto);
            align-content: start;
            gap: 7px;
            overflow-y: auto;
            padding-right: 3px;
            scrollbar-width: thin;
            scrollbar-color: #273241 #101821;
          }
          #${rootId} .codex-usage-hud-activity-timeline[data-fill="spread"] {
            align-content: start;
          }
          #${rootId} .codex-usage-hud-activity-load-more {
            width: 100%;
            height: 22px;
            border: 1px solid rgba(132, 146, 166, .18);
            border-radius: 5px;
            background: rgba(156, 203, 255, .06);
            color: #9ccbff;
            font-size: 10.5px;
            font-weight: 800;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-activity-load-more:disabled {
            opacity: .58;
            cursor: default;
          }
          #${rootId} .codex-usage-hud-activity-load-more:hover {
            border-color: rgba(156, 203, 255, .36);
            background: rgba(156, 203, 255, .10);
          }
          #${rootId} .codex-usage-hud-activity-load-more:disabled:hover {
            border-color: rgba(132, 146, 166, .18);
            background: rgba(156, 203, 255, .06);
          }
          #${rootId} .codex-usage-hud-activity-node {
            position: relative;
            display: grid;
            grid-template-columns: 48px 10px minmax(0, 1fr);
            gap: 8px;
            align-items: start;
            min-width: 0;
            min-height: 34px;
          }
          #${rootId} .codex-usage-hud-activity-node > span:last-child {
            min-width: 0;
            display: grid;
            gap: 1px;
          }
          #${rootId} .codex-usage-hud-activity-node-time {
            color: #718095;
            font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
            font-size: 10px;
            line-height: 1.4;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-activity-node-dot {
            position: relative;
            width: 7px;
            height: 7px;
            margin-top: 4px;
            border-radius: 50%;
            background: #9ccbff;
            box-shadow: 0 0 0 3px rgba(156,203,255,.10);
          }
          #${rootId} .codex-usage-hud-activity-node:not(:last-child)::after {
            content: "";
            position: absolute;
            top: 11px;
            bottom: -15px;
            left: 55px;
            width: 1px;
            background: rgba(132, 146, 166, .24);
          }
          #${rootId} .codex-usage-hud-activity-node[data-active="true"] .codex-usage-hud-activity-node-dot {
            background: #f3d27a;
            box-shadow: 0 0 0 3px rgba(243,210,122,.12);
          }
          #${rootId} .codex-usage-hud-activity-node-title {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: #dce7f2;
            font-size: 10.5px;
            font-weight: 800;
            line-height: 1.35;
          }
          #${rootId} .codex-usage-hud-activity-node-detail {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: #8492a6;
            font-size: 10px;
            line-height: 1.35;
          }
          #${rootId} .codex-usage-hud-section {
            margin: 0 0 6px;
          }
          #${rootId} .codex-usage-hud-section-title {
            margin-bottom: 1px;
            color: #8492a6;
            font-size: 10px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-value {
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            color: #dde7f2;
            font-size: 11px;
            line-height: 1.45;
          }
          #${rootId} .codex-usage-hud-activity-main .codex-usage-hud-value,
          #${rootId} .codex-usage-hud-activity-step .codex-usage-hud-value,
          #${rootId} .codex-usage-hud-activity-metric .codex-usage-hud-value {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-value.mono {
            font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
          }
          #${rootId} .codex-usage-hud-value.accent {
            color: #f3d27a;
            font-size: 13px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-value.blue {
            color: #9ccbff;
          }
          #${rootId} .codex-usage-hud-value.warn {
            color: #ffb86b;
          }
          #${rootId} .codex-usage-hud-value.muted {
            color: #a9bcd2;
          }
          #${rootId} .codex-usage-hud-value[data-copyable="true"] {
            cursor: pointer;
            border-radius: 4px;
            transition: color .12s ease, background .12s ease;
          }
          #${rootId} .codex-usage-hud-value[data-copyable="true"][data-copy-field="slow"] {
            color: #f3d27a;
          }
          #${rootId} .codex-usage-hud-value[data-copyable="true"][data-copy-field="gap"] {
            color: #bcd7ff;
          }
          #${rootId} .codex-usage-hud-value[data-copyable="true"]:hover {
            background: rgba(243, 210, 122, .08);
          }
          #${rootId} .codex-usage-hud-value[data-copied="true"] {
            color: #8fe3a1 !important;
          }
          #${rootId} .codex-usage-hud-divider {
            height: 1px;
            margin: 5px 0 6px;
            background: #273241;
          }
          #${rootId} .codex-usage-hud-request-subhead {
            display: flex;
            align-items: center;
            justify-content: space-between;
            min-height: 14px;
            margin-bottom: 2px;
            padding: 0 2px;
            color: #718095;
            font-size: 10px;
            font-weight: 700;
            line-height: 14px;
          }
          #${rootId} .codex-usage-hud-request-subhead span:last-child {
            color: #566477;
            font-weight: 500;
          }
          #${rootId} .codex-usage-hud-request-list {
            min-height: 0;
            max-height: 100%;
            overflow: auto;
            border-radius: 4px;
            background: #101821;
            padding: 4px;
            scrollbar-width: thin;
            scrollbar-color: #273241 #101821;
          }
          #${rootId} .codex-usage-hud-active-session-candidates {
            margin-top: 6px;
            padding: 7px;
            border: 1px solid rgba(243, 210, 122, .28);
            border-radius: 5px;
            background: rgba(243, 210, 122, .06);
          }
          #${rootId} .codex-usage-hud-active-session-candidates[hidden] {
            display: none !important;
          }
          #${rootId} .codex-usage-hud-active-session-candidates-title {
            color: #f3d27a;
            font-size: 11px;
            font-weight: 700;
            line-height: 16px;
          }
          #${rootId} .codex-usage-hud-active-session-candidates-detail {
            margin-top: 2px;
            color: #9aaabd;
            font-size: 10px;
            line-height: 14px;
          }
          #${rootId} .codex-usage-hud-active-session-candidates-list {
            display: grid;
            gap: 4px;
            margin-top: 6px;
          }
          #${rootId} .codex-usage-hud-active-session-candidate {
            display: grid;
            gap: 1px;
            width: 100%;
            box-sizing: border-box;
            border: 1px solid #354250;
            border-radius: 4px;
            padding: 5px 7px;
            background: #17212c;
            color: #dce7f2;
            cursor: pointer;
            text-align: left;
          }
          #${rootId} .codex-usage-hud-active-session-candidate:hover,
          #${rootId} .codex-usage-hud-active-session-candidate:focus-visible {
            border-color: #f3d27a;
            background: #202c38;
            outline: none;
          }
          #${rootId} .codex-usage-hud-active-session-candidate-label {
            overflow: hidden;
            color: #dce7f2;
            font: 11px/15px Consolas, "Cascadia Mono", ui-monospace, monospace;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-active-session-candidate-meta {
            overflow: hidden;
            color: #9aaabd;
            font-size: 10px;
            line-height: 14px;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-row {
            white-space: pre;
            color: #dce7f2;
            font: 11px/1.45 Consolas, "Cascadia Mono", ui-monospace, monospace;
          }
          #${rootId} .codex-usage-hud-row:first-child {
            color: #f3d27a;
          }
          #${rootId} .codex-usage-hud-row-time[data-running="true"] {
            color: #9ccbff;
          }
          #${rootId} .${warningClass} {
            color: #ffb86b !important;
          }
          #${rootId} .${errorClass} {
            color: #ff6b6b !important;
          }
          #${rootId} .codex-usage-hud-runtime-errors[hidden] {
            display: none !important;
          }
          #${rootId} .codex-usage-hud-runtime-errors {
            position: fixed;
            left: 16px;
            bottom: 16px;
            z-index: 2147482760;
            max-height: min(360px, calc(100vh - 32px));
            overflow: hidden;
            box-sizing: border-box;
            border: 1px solid rgba(255, 107, 107, .45);
            border-radius: 7px;
            background: rgba(16, 22, 29, .97);
            color: #e8eef7;
            box-shadow: 0 18px 48px rgba(0, 0, 0, .46);
            pointer-events: auto;
            padding: 0;
            user-select: text;
            font: 11px/1.45 Consolas, "Cascadia Mono", ui-monospace, monospace;
          }
          #${rootId} .codex-usage-hud-runtime-errors[data-expanded="false"] {
            width: max-content;
            min-width: 88px;
            max-width: calc(100vw - 32px);
          }
          #${rootId} .codex-usage-hud-runtime-errors[data-expanded="true"] {
            width: min(520px, calc(100vw - 32px));
          }
          #${rootId} .codex-usage-hud-runtime-errors-title {
            display: flex;
            align-items: center;
            justify-content: flex-start;
            gap: 7px;
            padding: 7px 9px;
            border-bottom: 1px solid rgba(255, 107, 107, .18);
            background: rgba(32, 40, 51, .82);
            color: #ffb3b3;
            font-weight: 700;
            cursor: grab;
            user-select: none;
            touch-action: none;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-runtime-errors-title:active {
            cursor: grabbing;
          }
          #${rootId} .codex-usage-hud-runtime-errors-toggle {
            display: inline-grid;
            place-items: center;
            flex: 0 0 auto;
            width: 17px;
            height: 17px;
            box-sizing: border-box;
            border: 1px solid rgba(255, 179, 179, .26);
            border-radius: 4px;
            padding: 0;
            background: rgba(255, 107, 107, .10);
            color: #ffd2d2;
            font: 700 10px/1 Consolas, "Cascadia Mono", ui-monospace, monospace;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-runtime-errors-toggle:hover {
            border-color: rgba(255, 179, 179, .46);
            background: rgba(255, 107, 107, .18);
          }
          #${rootId} .codex-usage-hud-runtime-errors-count {
            color: #ffd2d2;
            font-variant-numeric: tabular-nums;
          }
          #${rootId} .codex-usage-hud-runtime-errors[data-expanded="false"] .codex-usage-hud-runtime-errors-title {
            border-bottom: 0;
          }
          #${rootId} .codex-usage-hud-runtime-errors-body {
            max-height: calc(min(360px, calc(100vh - 32px)) - 34px);
            overflow: auto;
            padding: 0 10px 8px;
            scrollbar-width: thin;
            scrollbar-color: #3b4149 #101821;
            user-select: text;
          }
          #${rootId} .codex-usage-hud-runtime-error {
            display: grid;
            gap: 3px;
            padding: 7px 0;
            border-top: 1px solid rgba(255, 107, 107, .18);
          }
          #${rootId} .codex-usage-hud-runtime-error-code {
            color: #ff8f8f;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-runtime-error-meta {
            color: #8492a6;
          }
          #${rootId} .codex-usage-hud-runtime-error-context {
            color: #b8c6d8;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
          }
          #${rootId} .codex-usage-hud-settings-modal[hidden] {
            display: none !important;
          }
          #${rootId} .codex-usage-hud-settings-modal {
            position: fixed;
            inset: 0;
            z-index: 2147482750;
            display: grid;
            place-items: center;
            padding: 24px;
            background: color-mix(in srgb, var(--codex-usage-hud-surface, #10161d) 52%, transparent);
            pointer-events: auto;
          }
          #${rootId} .codex-usage-hud-settings-dialog {
            position: relative;
            width: min(760px, calc(100vw - 48px));
            max-height: min(720px, calc(100vh - 48px));
            display: grid;
            grid-template-rows: auto auto minmax(0, 1fr) auto;
            border: 1px solid var(--codex-usage-hud-panel-border, #3a485a);
            border-radius: 8px;
            background: var(--codex-usage-hud-surface, #10161d);
            color: var(--codex-usage-hud-text, #e8eef7);
            box-shadow: 0 24px 70px rgba(0, 0, 0, .44);
            overflow: hidden;
            font: 12px "Microsoft YaHei UI", system-ui, sans-serif;
          }
          #${rootId} .codex-usage-hud-settings-head,
          #${rootId} .codex-usage-hud-settings-actions {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding: 10px 12px;
            background: var(--codex-usage-hud-header-surface, #151d27);
          }
          #${rootId} .codex-usage-hud-settings-title {
            font-size: 13px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-settings-close {
            box-sizing: border-box;
            flex: 0 0 auto;
            width: 32px;
            height: 32px;
            min-width: 32px;
            min-height: 32px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border: 0;
            border-radius: 6px;
            background: var(--codex-usage-hud-panel-border, #2e3846);
            color: var(--codex-usage-hud-text, #dde7f2);
            padding: 0;
            margin: 0;
            font-size: 18px;
            font-weight: 700;
            line-height: 1;
            cursor: pointer;
            pointer-events: auto;
            -webkit-app-region: no-drag;
          }
          #${rootId} .codex-usage-hud-settings-close:hover {
            background: color-mix(in srgb, var(--codex-usage-hud-panel-border, #2e3846) 72%, #ffffff 28%);
            color: #ffffff;
          }
          #${rootId} .codex-usage-hud-settings-action {
            border: 0;
            border-radius: 5px;
            background: var(--codex-usage-hud-panel-border, #2e3846);
            color: var(--codex-usage-hud-text, #dde7f2);
            min-height: 28px;
            padding: 4px 9px;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-settings-action[data-primary="true"] {
            background: var(--codex-usage-hud-accent, #f3d27a);
            color: var(--codex-usage-hud-progress-day-text, #10161d);
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-settings-action[data-danger="true"] {
            background: var(--codex-usage-hud-error, #ff6b6b);
            color: #160707;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-settings-action:disabled,
          #${rootId} .codex-usage-hud-settings-link:disabled {
            cursor: not-allowed;
            opacity: .52;
          }
          #${rootId} .codex-usage-hud-background-policy-switch {
            display: inline-flex;
            flex: 0 0 auto;
            align-items: center;
            gap: 7px;
            min-height: 28px;
            border: 0;
            padding: 2px 0;
            background: transparent;
            color: var(--codex-usage-hud-text, #dde7f2);
            cursor: pointer;
            font: inherit;
            font-size: 10px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-background-policy-switch-track {
            position: relative;
            display: inline-block;
            width: 34px;
            height: 20px;
            flex: 0 0 34px;
            border-radius: 10px;
            background: var(--codex-usage-hud-divider, #273241);
            transition: background .16s ease;
          }
          #${rootId} .codex-usage-hud-background-policy-switch[aria-checked="true"] .codex-usage-hud-background-policy-switch-track {
            background: var(--codex-usage-hud-success, #62c993);
          }
          #${rootId} .codex-usage-hud-background-policy-switch-thumb {
            position: absolute;
            top: 3px;
            left: 3px;
            width: 14px;
            height: 14px;
            border-radius: 50%;
            background: #ffffff;
            transition: transform .16s ease;
          }
          #${rootId} .codex-usage-hud-background-policy-switch[aria-checked="false"] .codex-usage-hud-background-policy-switch-track {
            background: var(--codex-usage-hud-error, #ff6b6b);
          }
          #${rootId} .codex-usage-hud-background-policy-switch[aria-checked="true"] .codex-usage-hud-background-policy-switch-thumb {
            transform: translateX(14px);
          }
          #${rootId} .codex-usage-hud-background-policy-switch:focus-visible {
            outline: 2px solid var(--codex-usage-hud-accent, #f3d27a);
            outline-offset: 2px;
          }
          #${rootId} .codex-usage-hud-settings-link {
            min-height: 0;
            border: 0;
            border-radius: 0;
            background: transparent;
            color: var(--codex-usage-hud-accent, #f3d27a);
            padding: 0;
            cursor: pointer;
            font: inherit;
            font-size: 11px;
            font-weight: 700;
            text-decoration: underline;
            text-underline-offset: 2px;
          }
          #${rootId} .codex-usage-hud-settings-tabs {
            display: flex;
            gap: 6px;
            padding: 8px 12px;
            border-top: 1px solid var(--codex-usage-hud-divider, #202833);
            border-bottom: 1px solid var(--codex-usage-hud-divider, #202833);
            background: var(--codex-usage-hud-surface, #10161d);
          }
          #${rootId} .codex-usage-hud-settings-tab {
            border: 0;
            border-radius: 5px;
            background: transparent;
            color: var(--codex-usage-hud-muted, #a9bcd2);
            padding: 5px 9px;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-settings-tab[data-active="true"] {
            background: var(--codex-usage-hud-header-surface, #202833);
            color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-settings-body {
            min-height: 0;
            overflow: auto;
            padding: 12px;
            scrollbar-width: thin;
            scrollbar-color: var(--codex-usage-hud-divider, #273241) var(--codex-usage-hud-surface, #10161d);
          }
          #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="backgroundUsage"] {
            width: min(1060px, calc(100vw - 48px));
            height: min(760px, calc(100vh - 48px));
          }
          #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="backgroundUsage"] .codex-usage-hud-settings-body {
            overflow: hidden;
            padding: 0;
          }
          #${rootId} .codex-usage-hud-background {
            min-height: 0;
            height: 100%;
            display: grid;
            grid-template-rows: auto auto minmax(0, 1fr);
            color: var(--codex-usage-hud-text, #e8eef7);
          }
          #${rootId} .codex-usage-hud-background-metrics {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 8px;
            padding: 12px;
            border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-background-metrics > div {
            min-width: 0;
            display: grid;
            gap: 2px;
            padding: 9px 10px;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 6px;
            background: var(--codex-usage-hud-request-panel-surface, #121a23);
          }
          #${rootId} .codex-usage-hud-background-metrics span,
          #${rootId} .codex-usage-hud-background-metrics small {
            min-width: 0;
            overflow: hidden;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-background-metrics strong {
            min-width: 0;
            overflow: hidden;
            color: var(--codex-usage-hud-warning, #ffb86b);
            font-size: 17px;
            line-height: 1.25;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-background-metrics > div:nth-child(2) strong {
            color: var(--codex-usage-hud-info, #9ccbff);
          }
          #${rootId} .codex-usage-hud-background-metrics > div:nth-child(3) strong {
            color: var(--codex-usage-hud-text, #e8eef7);
          }
          #${rootId} .codex-usage-hud-background-metrics > div:nth-child(4) strong {
            color: var(--codex-usage-hud-success, #8fe3a1);
            font-size: 13px;
          }
          #${rootId} .codex-usage-hud-background-toolbar {
            display: grid;
            grid-template-columns: auto minmax(140px, 1fr) minmax(140px, 1fr);
            gap: 8px;
            align-items: center;
            padding: 8px 12px;
            border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-background-range {
            display: inline-flex;
            min-width: 0;
            padding-right: 8px;
            border-right: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-background-range button,
          #${rootId} .codex-usage-hud-background-toolbar select {
            min-height: 30px;
            box-sizing: border-box;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            background: var(--codex-usage-hud-panel-surface, #141b24);
            color: var(--codex-usage-hud-muted, #a9bcd2);
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-background-range button {
            padding: 4px 10px;
            border-right: 0;
          }
          #${rootId} .codex-usage-hud-background-range button:first-child {
            border-radius: 5px 0 0 5px;
          }
          #${rootId} .codex-usage-hud-background-range button:last-child {
            border-right: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 0 5px 5px 0;
          }
          #${rootId} .codex-usage-hud-background-range button[data-active="true"] {
            border-color: var(--codex-usage-hud-warning, #ffb86b);
            background: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 14%, var(--codex-usage-hud-panel-surface, #141b24));
            color: var(--codex-usage-hud-warning, #ffb86b);
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-background-toolbar select {
            min-width: 0;
            width: 100%;
            border-radius: 5px;
            padding: 4px 8px;
          }
          #${rootId} .codex-usage-hud-background-master-detail {
            min-height: 0;
            display: grid;
            grid-template-columns: minmax(280px, 340px) minmax(0, 1fr);
          }
          #${rootId} .codex-usage-hud-background-history,
          #${rootId} .codex-usage-hud-background-detail {
            min-width: 0;
            min-height: 0;
            overflow: auto;
            padding: 12px;
            scrollbar-width: thin;
          }
          #${rootId} .codex-usage-hud-background-history {
            border-right: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-session-ranking-note {
            padding: 0 12px 8px;
          }
          #${rootId} .codex-usage-hud-background-section-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            min-height: 24px;
            color: var(--codex-usage-hud-text, #e8eef7);
            font-size: 11px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-background-section-title > span:last-child {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-weight: 600;
          }
          #${rootId} .codex-usage-hud-background-event-list {
            display: grid;
            gap: 7px;
            margin-top: 7px;
          }
          #${rootId} .codex-usage-hud-background-event-row {
            min-width: 0;
            overflow: hidden;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 6px;
          }
          #${rootId} .codex-usage-hud-background-event {
            position: relative;
            min-width: 0;
            width: 100%;
            display: grid;
            gap: 5px;
            padding: 9px 10px;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 6px;
            background: transparent;
            color: inherit;
            text-align: left;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-background-event-row > .codex-usage-hud-background-event {
            border: 0;
            border-radius: 0;
          }
          #${rootId} .codex-usage-hud-background-unread-dot {
            position: absolute;
            top: 5px;
            right: 5px;
            width: 7px;
            height: 7px;
            border: 1px solid var(--codex-usage-hud-panel-surface, #141b24);
            border-radius: 50%;
            background: var(--codex-usage-hud-error, #ff6b6b);
            box-shadow: 0 0 0 1px color-mix(in srgb, var(--codex-usage-hud-error, #ff6b6b) 22%, transparent);
            pointer-events: none;
          }
          #${rootId} .codex-usage-hud-background-event[data-unread="true"] .codex-usage-hud-background-event-head {
            padding-right: 8px;
          }
          #${rootId} .codex-usage-hud-background-event:hover,
          #${rootId} .codex-usage-hud-background-event[data-selected="true"] {
            border-color: var(--codex-usage-hud-warning, #ffb86b);
            background: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 8%, var(--codex-usage-hud-panel-surface, #141b24));
          }
          #${rootId} .codex-usage-hud-background-event-row:hover,
          #${rootId} .codex-usage-hud-background-event-row[data-selected="true"] {
            border-color: var(--codex-usage-hud-warning, #ffb86b);
            background: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 8%, var(--codex-usage-hud-panel-surface, #141b24));
          }
          #${rootId} .codex-usage-hud-background-event-row > .codex-usage-hud-background-event:hover,
          #${rootId} .codex-usage-hud-background-event-row > .codex-usage-hud-background-event[data-selected="true"] {
            border-color: transparent;
            background: transparent;
          }
          #${rootId} .codex-usage-hud-background-event-head,
          #${rootId} .codex-usage-hud-background-event-totals {
            min-width: 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 7px;
          }
          #${rootId} .codex-usage-hud-background-event-title {
            min-width: 0;
            overflow: hidden;
            color: var(--codex-usage-hud-text, #e8eef7);
            font-weight: 700;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-background-event-meta {
            min-width: 0;
            overflow: hidden;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-background-event-totals {
            justify-content: flex-start;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-background-event-totals strong {
            color: var(--codex-usage-hud-text, #e8eef7);
          }
          #${rootId} .codex-usage-hud-background-event-totals span:last-child {
            margin-left: auto;
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-session-ranking-row .codex-usage-hud-background-event-totals > * {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-ranking-row {
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 6px;
          }
          #${rootId} .codex-usage-hud-session-ranking-row:hover,
          #${rootId} .codex-usage-hud-session-ranking-row[data-selected="true"] {
            border-color: var(--codex-usage-hud-warning, #ffb86b);
            background: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 8%, var(--codex-usage-hud-panel-surface, #141b24));
          }
          #${rootId} .codex-usage-hud-session-ranking-select {
            width: 100%;
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            grid-template-areas: "title cost" "meta model" "totals totals";
            gap: 4px 8px;
            padding: 9px 10px;
            border: 0;
            background: transparent;
            color: inherit;
            text-align: left;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-session-ranking-row:hover .codex-usage-hud-session-ranking-select,
          #${rootId} .codex-usage-hud-session-ranking-row[data-selected="true"] .codex-usage-hud-session-ranking-select {
            background: transparent;
          }
          #${rootId} .codex-usage-hud-session-ranking-select > .codex-usage-hud-background-event-title { grid-area: title; }
          #${rootId} .codex-usage-hud-session-ranking-cost { grid-area: cost; justify-self: end; color: var(--codex-usage-hud-warning, #ffb86b); font-size: 10px; text-align: right; white-space: nowrap; }
          #${rootId} .codex-usage-hud-session-ranking-meta { grid-area: meta; min-width: 0; display: flex; align-items: center; gap: 6px; color: var(--codex-usage-hud-muted, #8492a6); font-size: 10px; }
          #${rootId} .codex-usage-hud-session-ranking-workdir { min-width: 0; overflow: hidden; padding: 0; border: 0; background: transparent; color: var(--codex-usage-hud-text, #e8eef7); font: inherit; text-overflow: ellipsis; white-space: nowrap; }
          #${rootId} button.codex-usage-hud-session-ranking-workdir { cursor: pointer; text-decoration: underline; text-underline-offset: 2px; }
          #${rootId} .codex-usage-hud-session-ranking-time { flex: 0 0 auto; white-space: nowrap; }
          #${rootId} .codex-usage-hud-session-ranking-model { grid-area: model; min-width: 0; overflow: hidden; color: var(--codex-usage-hud-muted, #8492a6); font-size: 10px; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
          #${rootId} .codex-usage-hud-session-ranking-select > .codex-usage-hud-background-event-totals { grid-area: totals; }
          #${rootId} .codex-usage-hud-background-status {
            flex: 0 0 auto;
            padding: 2px 7px;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 4px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 9px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-background-detail-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 10px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-background-detail-head > div {
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-background-detail-head h3 {
            min-width: 0;
            margin: 0;
            overflow: hidden;
            font-size: 15px;
            line-height: 1.35;
            letter-spacing: 0;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-background-detail-head > .codex-usage-hud-settings-action {
            flex: 0 0 auto;
            min-width: 120px;
            min-height: 28px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-background-policy-message {
            margin-top: 9px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            line-height: 1.45;
          }
          #${rootId} .codex-usage-hud-settings-confirm-card > .codex-usage-hud-background-policy-message {
            margin-inline: 18px;
          }
          #${rootId} .codex-usage-hud-background-detail-sub {
            color: var(--codex-usage-hud-info, #9ccbff);
            font-size: 10px;
            line-height: 1.5;
          }
          #${rootId} .codex-usage-hud-background-detail-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 8px 12px;
            padding: 12px 0;
            border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-background-detail-grid > div {
            min-width: 0;
            display: grid;
            gap: 2px;
          }
          #${rootId} .codex-usage-hud-background-detail-grid span {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 9px;
          }
          #${rootId} .codex-usage-hud-background-detail-grid strong {
            min-width: 0;
            overflow: hidden;
            font-size: 10px;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} button.codex-usage-hud-background-workdir-link {
            display: block;
            min-width: 0;
            max-width: 100%;
            overflow: hidden;
            padding: 0;
            border: 0;
            background: transparent;
            color: inherit;
            cursor: pointer;
            font: inherit;
            font-size: 10px;
            font-weight: 700;
            text-align: left;
            text-decoration: underline;
            text-decoration-thickness: 1px;
            text-underline-offset: 2px;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-background-detail-grid .codex-usage-hud-background-detail-wide {
            grid-column: span 2;
          }
          #${rootId} .codex-usage-hud-background-detail-grid .codex-usage-hud-background-detail-full {
            grid-column: 1 / -1;
          }
          #${rootId} .codex-usage-hud-background-requests,
          #${rootId} .codex-usage-hud-background-prompt {
            padding-top: 10px;
          }
          #${rootId} .codex-usage-hud-background-request-list {
            display: grid;
            gap: 4px;
            margin-top: 6px;
          }
          #${rootId} .codex-usage-hud-background-request {
            min-width: 0;
            display: grid;
            grid-template-columns: 74px minmax(100px, 1.2fr) minmax(96px, 1fr) 58px 82px 28px;
            gap: 7px;
            align-items: center;
            min-height: 28px;
            padding: 0 8px;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 4px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 9px;
          }
          #${rootId} .codex-usage-hud-background-request > * {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-background-request strong,
          #${rootId} .codex-usage-hud-background-request-endpoint {
            color: var(--codex-usage-hud-text, #e8eef7);
          }
          #${rootId} .codex-usage-hud-background-request > span:nth-child(5) {
            color: var(--codex-usage-hud-warning, #ffb86b);
            text-align: right;
          }
          #${rootId} .codex-usage-hud-background-request-index {
            text-align: right;
          }
          #${rootId} .codex-usage-hud-background-prompt pre {
            max-height: 150px;
            box-sizing: border-box;
            margin: 6px 0 0;
            overflow: auto;
            padding: 9px 10px;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 5px;
            background: var(--codex-usage-hud-request-panel-surface, #121a23);
            color: var(--codex-usage-hud-request-text, #d8e2ef);
            font: 9px/1.55 Consolas, "Cascadia Mono", ui-monospace, monospace;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            cursor: text;
            user-select: text;
            -webkit-user-select: text;
          }
          #${rootId} .codex-usage-hud-background-prompt pre[data-expanded="true"] {
            max-height: 320px;
          }
          #${rootId} .codex-usage-hud-background-empty,
          #${rootId} .codex-usage-hud-background-error {
            padding: 16px;
            color: var(--codex-usage-hud-muted, #8492a6);
            text-align: center;
          }
          #${rootId} .codex-usage-hud-background-error {
            padding: 7px 12px;
            background: color-mix(in srgb, var(--codex-usage-hud-error, #ff7b86) 8%, transparent);
            color: var(--codex-usage-hud-error, #ff7b86);
            text-align: left;
          }
          #${rootId} .codex-usage-hud-settings-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
          }
          #${rootId} .codex-usage-hud-settings-compact-row {
            grid-column: 1 / -1;
            min-width: 0;
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
          }
          #${rootId} .codex-usage-hud-settings-compact-inputs {
            min-width: 0;
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-settings-compact-inputs > .codex-usage-hud-settings-field,
          #${rootId} .codex-usage-hud-settings-compact-row > .codex-usage-hud-settings-field {
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-settings-compact-row [data-setting-key="work_overlay_max_items"],
          #${rootId} .codex-usage-hud-settings-compact-row [data-setting-key="budget_thresholds"] {
            max-width: 100%;
          }
          #${rootId} .codex-usage-hud-overlay-runtime-row {
            min-width: 0;
            display: grid;
            grid-template-columns: minmax(0, 1fr) 72px;
            gap: 6px;
            align-items: end;
          }
          #${rootId} .codex-usage-hud-overlay-runtime-row [data-desktop-overlay-dependency="true"] {
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-overlay-side-control {
            min-width: 0;
            width: 100%;
          }
          #${rootId} .codex-usage-hud-overlay-side-control select {
            min-width: 0;
            width: 100%;
          }
          #${rootId} .codex-usage-hud-settings-field,
          #${rootId} .codex-usage-hud-price-table {
            min-width: 0;
            display: grid;
            gap: 4px;
          }
          #${rootId} .codex-usage-hud-settings-field label,
          #${rootId} .codex-usage-hud-price-title {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-settings-field input,
          #${rootId} .codex-usage-hud-settings-field select,
          #${rootId} .codex-usage-hud-price-row input {
            min-width: 0;
            box-sizing: border-box;
            width: 100%;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 5px;
            background: var(--codex-usage-hud-panel-surface, #141b24);
            color: var(--codex-usage-hud-text, #e8eef7);
            min-height: 30px;
            padding: 5px 7px;
            outline: none;
          }
          #${rootId} .codex-usage-hud-settings-field input:focus,
          #${rootId} .codex-usage-hud-settings-field select:focus,
          #${rootId} .codex-usage-hud-price-row input:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-overlay-dependency {
            min-height: 30px;
            box-sizing: border-box;
            display: flex;
            flex-wrap: nowrap;
            align-items: center;
            gap: 8px;
            padding: 5px 8px;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 5px;
            background: var(--codex-usage-hud-panel-surface, #141b24);
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-overlay-dependency-state {
            color: var(--codex-usage-hud-text, #e8eef7);
            font-size: 11px;
            font-weight: 700;
            white-space: nowrap;
            flex: 0 0 auto;
          }
          #${rootId} .codex-usage-hud-overlay-dependency-version,
          #${rootId} .codex-usage-hud-overlay-dependency-note {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 11px;
            line-height: 1.2;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            min-width: 0;
            flex: 1 1 auto;
          }
          #${rootId} .codex-usage-hud-overlay-dependency-version {
            color: var(--codex-usage-hud-success, #8fe3a1);
            font: 700 11px Consolas, "Cascadia Mono", ui-monospace, monospace;
            flex: 0 1 auto;
          }
          #${rootId} .codex-usage-hud-overlay-dependency-actions {
            display: inline-flex;
            flex-wrap: nowrap;
            gap: 8px;
            flex: 0 0 auto;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-settings-inline {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 8px;
            align-items: center;
          }
          #${rootId} .codex-usage-hud-settings-inline .codex-usage-hud-settings-action {
            min-height: 30px;
            padding-inline: 12px;
          }
          #${rootId} .codex-usage-hud-settings-visually-hidden {
            position: absolute;
            width: 1px;
            height: 1px;
            padding: 0;
            margin: -1px;
            overflow: hidden;
            clip: rect(0, 0, 0, 0);
            white-space: nowrap;
            border: 0;
          }
          #${rootId} .codex-usage-hud-provider-editor {
            min-width: 0;
            grid-column: 1 / -1;
            display: grid;
            gap: 8px;
            margin-top: 4px;
            border-top: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-provider-editor-head {
            min-width: 0;
            min-height: 42px;
            display: grid;
            grid-template-columns: auto minmax(0, 1fr) auto;
            align-items: center;
            gap: 10px;
            padding-top: 5px;
            border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-provider-navigation {
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 5px;
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-provider-tab-fixed {
            min-width: 0;
            flex: 0 0 auto;
            display: inline-flex;
            align-items: center;
            padding-right: 4px;
            border-right: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-provider-tab-viewport {
            min-width: 0;
            flex: 1 1 auto;
            display: flex;
            align-items: center;
            gap: 4px;
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-provider-tab-viewport[hidden] {
            display: none !important;
          }
          #${rootId} .codex-usage-hud-provider-tabs {
            min-width: 0;
            flex: 1 1 auto;
            display: flex;
            gap: 2px;
            overflow-x: auto;
            overscroll-behavior-inline: contain;
            scrollbar-width: none;
            -ms-overflow-style: none;
          }
          #${rootId} .codex-usage-hud-provider-tabs::-webkit-scrollbar {
            display: none;
          }
          #${rootId} .codex-usage-hud-provider-nav-button {
            box-sizing: border-box;
            flex: 0 0 23px;
            width: 23px;
            height: 29px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 5px;
            background: var(--codex-usage-hud-header-surface, #202833);
            color: var(--codex-usage-hud-muted, #a9bcd2);
            padding: 0;
            font-size: 18px;
            line-height: 1;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-provider-nav-button:hover:not(:disabled) {
            border-color: color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 54%, transparent);
            color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-nav-button:disabled {
            opacity: .28;
            cursor: default;
          }
          #${rootId} .codex-usage-hud-provider-nav-button[hidden] {
            display: none !important;
          }
          #${rootId} .codex-usage-hud-provider-tab {
            position: relative;
            flex: 0 0 auto;
            min-width: 76px;
            min-height: 34px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            border: 0;
            border-bottom: 2px solid transparent;
            background: transparent;
            color: var(--codex-usage-hud-muted, #8492a6);
            padding: 5px 10px 4px;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-provider-tab:hover {
            background: var(--codex-usage-hud-header-surface, #202833);
            color: var(--codex-usage-hud-text, #e8eef7);
          }
          #${rootId} .codex-usage-hud-provider-tab[aria-selected="true"] {
            border-bottom-color: var(--codex-usage-hud-accent, #f3d27a);
            color: var(--codex-usage-hud-text, #e8eef7);
          }
          #${rootId} .codex-usage-hud-provider-tab-badge {
            border: 1px solid var(--codex-usage-hud-panel-border, #3a485a);
            border-radius: 4px;
            color: var(--codex-usage-hud-muted, #8492a6);
            padding: 1px 4px;
            font-size: 9px;
            line-height: 1.4;
          }
          #${rootId} .codex-usage-hud-provider-tab[aria-selected="true"] .codex-usage-hud-provider-tab-badge {
            border-color: color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 48%, transparent);
            color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-dirty-dot {
            width: 6px;
            height: 6px;
            flex: 0 0 6px;
            border-radius: 50%;
            background: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-pricing-apply-action {
            position: relative;
          }
          #${rootId} .codex-usage-hud-pricing-apply-dirty-dot {
            position: absolute;
            top: -3px;
            right: -3px;
            border: 1px solid var(--codex-usage-hud-panel-surface, #18202b);
          }
          #${rootId} .codex-usage-hud-provider-context {
            min-width: 0;
            min-height: 28px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            flex-wrap: nowrap;
          }
          #${rootId} .codex-usage-hud-provider-scope {
            min-width: 0;
            display: inline-flex;
            align-items: center;
            gap: 7px;
            color: var(--codex-usage-hud-text, #e8eef7);
            cursor: pointer;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-provider-scope-options {
            min-width: 0;
            display: inline-flex;
            align-items: center;
            flex-wrap: nowrap;
            gap: 8px 14px;
            flex: 1 1 auto;
          }
          #${rootId} .codex-usage-hud-provider-scope input {
            width: 15px;
            height: 15px;
            margin: 0;
            accent-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-scope input:disabled {
            opacity: .78;
            cursor: not-allowed;
          }
          #${rootId} .codex-usage-hud-provider-quick-launch {
            min-width: 0;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            color: var(--codex-usage-hud-muted, #8492a6);
            white-space: nowrap;
            cursor: pointer;
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-provider-quick-launch input {
            width: 14px;
            height: 14px;
            margin: 0;
            accent-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-quick-launch:has(input:checked) {
            color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-meta {
            min-width: 0;
            color: var(--codex-usage-hud-muted, #8492a6);
            text-align: right;
            overflow-wrap: anywhere;
          }
          #${rootId} .codex-usage-hud-provider-meta-row {
            min-width: 0;
            display: inline-flex;
            align-items: center;
            justify-content: flex-end;
            gap: 6px;
            flex: 0 1 auto;
          }
          #${rootId} .codex-usage-hud-settings-icon-action {
            width: 28px;
            height: 28px;
            flex: 0 0 28px;
            display: inline-grid;
            place-items: center;
            border: 1px solid transparent;
            border-radius: 5px;
            background: transparent;
            color: var(--codex-usage-hud-muted, #8492a6);
            cursor: pointer;
            font-size: 15px;
            line-height: 1;
          }
          #${rootId} .codex-usage-hud-settings-icon-action:hover,
          #${rootId} .codex-usage-hud-settings-icon-action:focus-visible {
            border-color: var(--codex-usage-hud-divider, #273241);
            background: var(--codex-usage-hud-header-surface, #202833);
            color: var(--codex-usage-hud-accent, #f3d27a);
            outline: none;
          }
          #${rootId} .codex-usage-hud-codex-cli-launch-action {
            color: var(--codex-usage-hud-accent, #f3d27a);
            font: 700 11px Consolas, "Cascadia Mono", ui-monospace, monospace;
          }
          #${rootId} .codex-usage-hud-codex-cli-layer {
            position: absolute;
            inset: 0;
            z-index: 4;
            display: grid;
            place-items: center;
            padding: 18px;
            background: rgba(7, 8, 9, .78);
            backdrop-filter: blur(5px);
          }
          #${rootId} .codex-usage-hud-codex-cli-dialog {
            width: min(680px, calc(100% - 12px));
            max-height: 100%;
            display: grid;
            gap: 12px;
            padding: 16px;
            border: 1px solid color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 36%, var(--codex-usage-hud-divider, #273241));
            border-radius: 8px;
            background: var(--codex-usage-hud-surface, #10161d);
            box-shadow: 0 24px 70px rgba(0, 0, 0, .56);
            overflow: auto;
          }
          #${rootId} .codex-usage-hud-codex-cli-head,
          #${rootId} .codex-usage-hud-codex-cli-actions,
          #${rootId} .codex-usage-hud-codex-cli-command-head,
          #${rootId} .codex-usage-hud-codex-cli-context {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
          }
          #${rootId} .codex-usage-hud-codex-cli-head > div {
            min-width: 0;
            display: grid;
            gap: 3px;
          }
          #${rootId} .codex-usage-hud-codex-cli-head strong {
            color: var(--codex-usage-hud-text, #e8eef7);
            font-size: 15px;
          }
          #${rootId} .codex-usage-hud-codex-cli-kicker {
            color: var(--codex-usage-hud-accent, #f3d27a);
            font: 800 9px Consolas, "Cascadia Mono", ui-monospace, monospace;
            letter-spacing: .14em;
          }
          #${rootId} .codex-usage-hud-codex-cli-loading,
          #${rootId} .codex-usage-hud-codex-cli-notice,
          #${rootId} .codex-usage-hud-codex-cli-danger {
            color: var(--codex-usage-hud-muted, #a9bcd2);
            font-size: 11px;
            line-height: 1.55;
          }
          #${rootId} .codex-usage-hud-codex-cli-context {
            justify-content: flex-start;
            min-height: 30px;
            padding: 0;
            border: 0;
            background: transparent;
          }
          #${rootId} .codex-usage-hud-codex-cli-context span {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-codex-cli-context strong {
            min-width: 0;
            overflow-wrap: anywhere;
            color: var(--codex-usage-hud-text, #e8eef7);
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-codex-cli-grid {
            min-width: 0;
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px 10px;
          }
          #${rootId} .codex-usage-hud-codex-cli-proxy {
            min-width: 0;
            min-height: 30px;
            display: flex;
            align-items: center;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-codex-cli-proxy-port {
            display: block;
            width: 76px;
            flex: 0 0 76px;
          }
          #${rootId} .codex-usage-hud-codex-cli-proxy-port input {
            min-height: 30px;
            padding: 5px 7px;
          }
          #${rootId} .codex-usage-hud-codex-cli-field,
          #${rootId} .codex-usage-hud-codex-cli-check {
            min-width: 0;
            display: grid;
            align-content: start;
            gap: 5px;
            color: var(--codex-usage-hud-muted, #a9bcd2);
            font-size: 10px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-codex-cli-wide {
            grid-column: 1 / -1;
          }
          #${rootId} .codex-usage-hud-codex-cli-field-head {
            min-width: 0;
            display: flex;
            align-items: baseline;
            justify-content: flex-start;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-codex-cli-check {
            min-height: 30px;
            display: flex;
            align-items: center;
            gap: 7px;
          }
          #${rootId} .codex-usage-hud-codex-cli-check-compact {
            min-height: 0;
            flex: 0 0 auto;
            gap: 5px;
            line-height: 1;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-codex-cli-field .codex-usage-hud-codex-cli-check-compact input {
            box-sizing: border-box;
            width: 13px;
            min-height: 13px;
            height: 13px;
            padding: 0;
            transform: translateY(1px);
          }
          #${rootId} .codex-usage-hud-codex-cli-check input {
            width: 15px;
            height: 15px;
            margin: 0;
            accent-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-codex-cli-field input,
          #${rootId} .codex-usage-hud-codex-cli-field select,
          #${rootId} .codex-usage-hud-codex-cli-command {
            box-sizing: border-box;
            width: 100%;
            min-width: 0;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 5px;
            background: var(--codex-usage-hud-panel-surface, #141b24);
            color: var(--codex-usage-hud-text, #e8eef7);
            outline: none;
          }
          #${rootId} .codex-usage-hud-codex-cli-field input,
          #${rootId} .codex-usage-hud-codex-cli-field select {
            min-height: 30px;
            padding: 5px 7px;
          }
          #${rootId} .codex-usage-hud-codex-cli-model-control {
            min-width: 0;
            display: grid;
            grid-template-columns: minmax(0, 1fr) 30px;
            gap: 6px;
            align-items: center;
          }
          #${rootId} .codex-usage-hud-codex-cli-model-refresh {
            width: 30px;
            height: 30px;
            border-color: var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-codex-cli-model-refresh:disabled {
            opacity: .45;
            cursor: wait;
          }
          #${rootId} .codex-usage-hud-codex-cli-field input:focus,
          #${rootId} .codex-usage-hud-codex-cli-field select:focus,
          #${rootId} .codex-usage-hud-codex-cli-command:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-codex-cli-command-head {
            color: var(--codex-usage-hud-text, #e8eef7);
            font-size: 11px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-codex-cli-command {
            min-height: 116px;
            resize: vertical;
            padding: 9px;
            font: 11px/1.55 Consolas, "Cascadia Mono", ui-monospace, monospace;
            white-space: pre;
          }
          #${rootId} .codex-usage-hud-codex-cli-notice {
            padding: 7px 9px;
            border-left: 2px solid var(--codex-usage-hud-warning, #ffb86b);
            background: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 8%, transparent);
          }
          #${rootId} .codex-usage-hud-codex-cli-notice a {
            color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-codex-cli-danger {
            padding: 7px 9px;
            border-left: 2px solid var(--codex-usage-hud-error, #ff7b86);
            background: color-mix(in srgb, var(--codex-usage-hud-error, #ff7b86) 8%, transparent);
            color: var(--codex-usage-hud-error, #ff7b86);
          }
          #${rootId} .codex-usage-hud-codex-cli-actions {
            flex-wrap: wrap;
            justify-content: flex-end;
          }
          #${rootId} .codex-usage-hud-codex-cli-status {
            min-width: 0;
            margin-right: auto;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-codex-cli-chat-test {
            min-width: 0;
            margin: 0;
            padding: 0 0 8px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            line-height: 1.4;
          }
          #${rootId} .codex-usage-hud-codex-cli-chat-test > summary {
            box-sizing: border-box;
            width: 100%;
            overflow: hidden;
            cursor: pointer;
            color: inherit;
            font-weight: 700;
            list-style-position: inside;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-codex-cli-chat-test > summary:hover,
          #${rootId} .codex-usage-hud-codex-cli-chat-test > summary:focus-visible {
            color: var(--codex-usage-hud-accent, #f3d27a);
            outline: none;
          }
          #${rootId} .codex-usage-hud-codex-cli-chat-test-body {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 8px;
            align-items: end;
            margin-top: 8px;
          }
          #${rootId} .codex-usage-hud-codex-cli-chat-test-body label {
            min-width: 0;
            display: grid;
            gap: 4px;
            color: inherit;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-codex-cli-chat-test-body input {
            box-sizing: border-box;
            width: 100%;
            min-height: 31px;
            padding: 5px 7px;
            border: 1px solid #47494f;
            border-radius: 5px;
            background: #14171b;
            color: #f1f3f5;
            outline: none;
            font: inherit;
            font-weight: 400;
          }
          #${rootId} .codex-usage-hud-codex-cli-chat-test-body input:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-codex-cli-chat-result {
            grid-column: 1 / -1;
            min-height: 14px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 400;
            line-height: 1.4;
            word-break: break-word;
          }
          #${rootId} .codex-usage-hud-codex-cli-chat-result-error {
            color: #f27878;
          }
          #${rootId} .codex-usage-hud-provider-delete-action:hover,
          #${rootId} .codex-usage-hud-provider-delete-action:focus-visible {
            border-color: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 56%, transparent);
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-provider-meta[data-tone="required"] {
            color: var(--codex-usage-hud-success, #8fe3a1);
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-provider-meta[data-tone="historical"] {
            color: var(--codex-usage-hud-warning, #ffb86b);
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-provider-context-adjustment {
            min-width: 140px;
            width: 160px;
            flex: 0 0 160px;
          }
          #${rootId} .codex-usage-hud-provider-context-adjustment input {
            min-width: 0;
            box-sizing: border-box;
            width: 100%;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 5px;
            background: var(--codex-usage-hud-panel-surface, #141b24);
            color: var(--codex-usage-hud-text, #e8eef7);
            min-height: 28px;
            padding: 4px 7px;
            outline: none;
          }
          #${rootId} .codex-usage-hud-provider-context-adjustment input:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-price-actions {
            min-width: 0;
            display: grid;
            grid-template-columns: auto minmax(0, 1fr) auto repeat(3, 30px);
            gap: 8px;
            align-items: center;
            margin-top: 6px;
          }
          #${rootId} .codex-usage-hud-price-actions > .codex-usage-hud-settings-action {
            justify-self: start;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-price-actions > .codex-usage-hud-pricing-icon-action {
            width: 30px;
            min-width: 30px;
            height: 30px;
            padding: 0;
            display: inline-grid;
            place-items: center;
            font-size: 18px;
            line-height: 1;
          }
          #${rootId} .codex-usage-hud-price-actions input[data-setting-key="pricing_url"] {
            min-width: 0;
            box-sizing: border-box;
            width: 100%;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 5px;
            background: var(--codex-usage-hud-panel-surface, #141b24);
            color: var(--codex-usage-hud-text, #e8eef7);
            min-height: 30px;
            padding: 5px 7px;
            outline: none;
          }
          #${rootId} .codex-usage-hud-price-actions input[data-setting-key="pricing_url"]:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-empty {
            color: var(--codex-usage-hud-muted, #8492a6);
            padding: 8px 0 4px;
          }
          #${rootId} .codex-usage-hud-price-unit {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-price-unit-wrap {
            min-width: 0;
            display: inline-flex;
            align-items: center;
            justify-content: flex-end;
            gap: 7px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-provider-add-action {
            min-height: 26px;
            padding: 3px 8px;
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-price-table {
            grid-column: 1 / -1;
            margin-top: 4px;
            overflow-x: auto;
            scrollbar-width: thin;
            scrollbar-color: var(--codex-usage-hud-divider, #273241) transparent;
          }
          #${rootId} .codex-usage-hud-price-row,
          #${rootId} .codex-usage-hud-price-header {
            display: grid;
            grid-template-columns: minmax(130px, 1.4fr) repeat(4, minmax(72px, 1fr));
            gap: 6px;
            align-items: center;
            min-width: 680px;
          }
          #${rootId} .codex-usage-hud-price-advanced {
            display: none;
          }
          #${rootId} .codex-usage-hud-price-header {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-price-detected {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            align-items: center;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-pricing-tools {
            min-width: 0;
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 6px;
            margin-top: 8px;
            padding-top: 8px;
            border-top: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 72%, transparent);
          }
          #${rootId} .codex-usage-hud-pricing-tools .codex-usage-hud-settings-action {
            min-width: 0;
            width: 100%;
            white-space: normal;
          }
          #${rootId} .codex-usage-hud-pricing-version-list {
            min-width: 0;
            display: grid;
            gap: 4px;
            margin-top: 7px;
          }
          #${rootId} .codex-usage-hud-pricing-version-item {
            min-width: 0;
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto minmax(0, auto);
            gap: 8px;
            align-items: center;
            padding: 5px 7px;
            border: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 82%, transparent);
            border-radius: 5px;
            background: color-mix(in srgb, var(--codex-usage-hud-panel-surface, #141b24) 78%, transparent);
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-pricing-version-item strong {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: var(--codex-usage-hud-text, #e8eef7);
          }
          #${rootId} .codex-usage-hud-pricing-version-item span {
            color: var(--codex-usage-hud-accent, #f3d27a);
            font: 10px Consolas, "Cascadia Mono", ui-monospace, monospace;
          }
          #${rootId} .codex-usage-hud-pricing-version-empty {
            margin-top: 7px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-pricing-field {
            min-width: 0;
            display: grid;
            gap: 5px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 11px;
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-pricing-field input,
          #${rootId} .codex-usage-hud-pricing-field textarea {
            min-width: 0;
            box-sizing: border-box;
            width: 100%;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 5px;
            background: var(--codex-usage-hud-panel-surface, #141b24);
            color: var(--codex-usage-hud-text, #e8eef7);
            padding: 7px;
            outline: none;
            font: inherit;
            font-weight: 400;
          }
          #${rootId} .codex-usage-hud-pricing-field textarea {
            min-height: 132px;
            resize: vertical;
            font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
            font-size: 10px;
            line-height: 1.45;
          }
          #${rootId} .codex-usage-hud-pricing-field input:focus,
          #${rootId} .codex-usage-hud-pricing-field textarea:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-pricing-field input[aria-invalid="true"] {
            border-color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-pricing-dialog {
            width: min(700px, calc(100% - 24px));
          }
          #${rootId} .codex-usage-hud-pricing-impact {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 11px;
            line-height: 1.55;
            overflow-wrap: anywhere;
          }
          #${rootId} .codex-usage-hud-pricing-preview-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 6px;
          }
          #${rootId} .codex-usage-hud-pricing-preview-grid > span {
            min-width: 0;
            display: grid;
            gap: 3px;
            padding: 8px;
            border: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 82%, transparent);
            border-radius: 5px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-pricing-preview-grid strong {
            color: var(--codex-usage-hud-text, #e8eef7);
            font-size: 12px;
            overflow-wrap: anywhere;
          }
          #${rootId} .codex-usage-hud-pricing-preview-list {
            max-height: 112px;
            overflow: auto;
            display: grid;
            gap: 4px;
            margin-inline: 18px;
            padding: 7px;
            border: 1px solid color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 36%, transparent);
            border-radius: 5px;
            color: var(--codex-usage-hud-warning, #ffb86b);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-settings-status {
            min-width: 0;
            color: var(--codex-usage-hud-request-muted, #a9bcd2);
            font-size: 11px;
            overflow-wrap: anywhere;
          }
          #${rootId} .codex-usage-hud-settings-footnote {
            grid-column: 1 / -1;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            margin-top: 2px;
          }
          #${rootId} .codex-usage-hud-settings-footnote .codex-usage-hud-settings-status {
            text-align: right;
          }
          #${rootId} .codex-usage-hud-settings-lock-toggle {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            min-width: 0;
            color: var(--codex-usage-hud-request-muted, #a9bcd2);
            font-size: 11px;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-settings-lock-toggle input {
            flex: 0 0 auto;
            width: 14px;
            height: 14px;
            margin: 0;
            accent-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-settings-status[data-kind="error"] {
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="storage"] {
            width: min(980px, calc(100vw - 48px));
            height: min(572px, calc(100vh - 48px));
            max-height: min(572px, calc(100vh - 48px));
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="storage"] .codex-usage-hud-settings-body {
            padding: 0;
            overflow: hidden;
            min-height: 0;
            height: 100%;
            display: flex;
            flex-direction: column;
          }
          #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="storage"] .codex-usage-hud-settings-actions {
            display: none;
          }
          #${rootId} .codex-usage-hud-cleanup-workspace {
            min-width: 0;
            min-height: 0;
            height: 100%;
            flex: 1 1 auto;
            display: grid;
            grid-template-rows: minmax(0, 1fr) auto;
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-cleanup-page-head {
            min-height: 52px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 9px 14px;
            border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 88%, transparent);
            background: color-mix(in srgb, var(--codex-usage-hud-surface, #10161d) 86%, #191a1c);
          }
          #${rootId} .codex-usage-hud-cleanup-segments {
            display: inline-grid;
            grid-template-columns: repeat(2, minmax(96px, 1fr));
            min-width: 0;
            border: 1px solid #414349;
            border-radius: 6px;
            overflow: hidden;
            background: #151618;
          }
          #${rootId} .codex-usage-hud-cleanup-segments button {
            min-width: 96px;
            min-height: 31px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 7px;
            border: 0;
            border-right: 1px solid #414349;
            border-radius: 0;
            background: transparent;
            color: var(--codex-usage-hud-muted, #9da1a8);
            padding: 0 12px;
            cursor: pointer;
            font: inherit;
            font-size: 12px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-cleanup-segments button:last-child { border-right: 0; }
          #${rootId} .codex-usage-hud-cleanup-segments button[data-active="true"] {
            background: #2b2d31;
            color: var(--codex-usage-hud-text, #f1f3f5);
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-cleanup-head-meta {
            color: #73777f;
            font-size: 11px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-cleanup-content {
            min-width: 0;
            min-height: 0;
            overflow-x: hidden;
            overflow-y: auto;
            position: relative;
            scrollbar-width: thin;
          }
          #${rootId} .codex-usage-hud-cleanup-footer {
            min-height: 50px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 8px 13px;
            border-top: 1px solid var(--codex-usage-hud-divider, #393b40);
            background: #2c2d30;
          }
          #${rootId} .codex-usage-hud-cleanup-footer-meta {
            min-width: 0;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 11px;
            overflow-wrap: anywhere;
          }
          #${rootId} .codex-usage-hud-cleanup-footer-actions {
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 0 0 auto;
          }
          #${rootId} .codex-usage-hud-cleanup-workspace .codex-usage-hud-settings-action {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            border: 1px solid #4a4d53;
            background: #2b2d31;
            color: var(--codex-usage-hud-text, #f1f3f5);
            font-size: 12px;
            font-weight: 600;
          }
          #${rootId} .codex-usage-hud-cleanup-workspace .codex-usage-hud-settings-action[data-primary="true"],
          #${rootId} .codex-usage-hud-settings-confirm-card .codex-usage-hud-settings-action[data-primary="true"] {
            background: #3b8eea;
            border-color: #3b8eea;
            color: #ffffff;
          }
          #${rootId} .codex-usage-hud-cleanup-workspace .codex-usage-hud-settings-action[data-danger="true"],
          #${rootId} .codex-usage-hud-settings-confirm-card[data-tone="danger"] .codex-usage-hud-settings-action[data-danger="true"] {
            background: #c43e45;
            border-color: #d84a51;
            color: #ffffff;
          }
          #${rootId} .codex-usage-hud-cleanup-icon {
            width: 16px;
            height: 16px;
            stroke: currentColor;
            stroke-width: 2;
            stroke-linecap: round;
            stroke-linejoin: round;
            fill: none;
            flex: 0 0 auto;
          }
          #${rootId} .codex-usage-hud-cleanup-icon-lg {
            width: 30px;
            height: 30px;
          }
          #${rootId} .codex-usage-hud-cleanup-empty-state {
            min-width: 0;
            height: 100%;
            min-height: 220px;
            display: grid;
            place-content: center;
            justify-items: center;
            gap: 12px;
            text-align: center;
            padding: 18px 16px 28px;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-mark {
            width: 62px;
            height: 62px;
            display: grid;
            place-items: center;
            border: 1px solid #3f5f82;
            border-radius: 50%;
            background: #17202a;
            color: #7db9f7;
          }
          #${rootId} .codex-usage-hud-cleanup-empty-title {
            margin: 0;
            color: var(--codex-usage-hud-text, #f1f3f5);
            font-size: 17px;
            font-weight: 700;
            line-height: 1.25;
          }
          #${rootId} .codex-usage-hud-cleanup-empty-meta {
            margin: 0;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 12px;
            line-height: 1.5;
          }
          #${rootId} .codex-usage-hud-cleanup-empty {
            display: grid;
            place-content: center;
            justify-items: center;
            gap: 10px;
            min-height: 200px;
            padding: 32px 20px;
            text-align: center;
          }
          #${rootId} .codex-usage-hud-cleanup-empty-mark {
            width: 56px;
            height: 56px;
            display: grid;
            place-items: center;
            border: 1px solid #3f5f82;
            border-radius: 50%;
            background: #17202a;
            color: #6b9fd9;
            opacity: .85;
          }
          #${rootId} .codex-usage-hud-cleanup-empty-title {
            margin: 0;
            color: var(--codex-usage-hud-text, #d8dde3);
            font-size: 14px;
            font-weight: 650;
            line-height: 1.3;
          }
          #${rootId} .codex-usage-hud-cleanup-empty-hint {
            margin: 0;
            color: var(--codex-usage-hud-muted, #7d848e);
            font-size: 11px;
            line-height: 1.5;
            max-width: 260px;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-mark[data-live="true"] {
            position: relative;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-mark[data-live="true"]::before {
            content: "";
            position: absolute;
            inset: -5px;
            border-radius: 50%;
            border: 2px solid transparent;
            border-top-color: var(--codex-usage-hud-accent-blue, #3b8eea);
            border-right-color: rgba(59, 142, 234, .35);
            animation: codex-usage-hud-cleanup-spin .9s linear infinite;
          }
          @keyframes codex-usage-hud-cleanup-spin { to { transform: rotate(360deg); } }
          #${rootId} .codex-usage-hud-cleanup-scan-strip {
            display: grid;
            gap: 8px;
            padding: 11px 15px 12px;
            border-bottom: 1px solid var(--codex-usage-hud-border, #393b40);
            background: linear-gradient(180deg, #1a2430 0%, #151a20 100%);
          }
          #${rootId} .codex-usage-hud-cleanup-scan-strip-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-strip-title {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            color: #7db9f7;
            font-size: 12px;
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-strip-meta {
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 11px;
            font-variant-numeric: tabular-nums;
            text-align: right;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-track {
            position: relative;
            height: 6px;
            border-radius: 999px;
            overflow: hidden;
            background: rgba(255, 255, 255, .08);
          }
          #${rootId} .codex-usage-hud-cleanup-scan-fill {
            height: 100%;
            border-radius: inherit;
            background: linear-gradient(90deg, #5ea7ff, #9ccbff);
            box-shadow: 0 0 12px rgba(94, 167, 255, .35);
            transition: width .28s ease;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-fill[data-indeterminate="true"] {
            width: 38% !important;
            animation: codex-usage-hud-cleanup-indet 1.35s ease-in-out infinite;
          }
          @keyframes codex-usage-hud-cleanup-indet {
            0% { transform: translateX(-120%); }
            100% { transform: translateX(320%); }
          }
          #${rootId} .codex-usage-hud-cleanup-scan-stage {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-stage[data-merged="session-index"] {
            justify-content: flex-start;
            gap: 8px;
            padding-top: 2px;
            border-top: 1px dashed rgba(94, 167, 255, .22);
            color: #a9c6e8;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-stage[data-merged="session-index"] > span:first-child {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            min-width: 0;
            color: #cfe4ff;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-stage[data-merged="session-index"] > span[data-secondary="true"] {
            margin-left: auto;
            font-variant-numeric: tabular-nums;
            text-align: right;
          }
          #${rootId} .codex-usage-hud-cleanup-scan-stage strong {
            color: #cfe4ff;
            font-weight: 600;
          }
          #${rootId} .codex-usage-hud-cleanup-mini-spinner {
            width: 12px;
            height: 12px;
            border: 1.5px solid rgba(125, 185, 247, .25);
            border-top-color: #7db9f7;
            border-radius: 50%;
            animation: codex-usage-hud-cleanup-spin .75s linear infinite;
            flex: 0 0 auto;
          }
          #${rootId} .codex-usage-hud-cleanup {
            display: flex;
            flex-direction: column;
            min-height: 0;
            height: 100%;
          }
          #${rootId} .codex-usage-hud-settings-action[data-size="large"] {
            min-height: 38px;
            padding: 0 17px;
            font-size: 13px;
          }
          #${rootId} .codex-usage-hud-session-cleanup {
            min-width: 0;
            min-height: 100%;
            height: 100%;
            display: grid;
            grid-template-rows: auto minmax(0, 1fr);
            align-content: start;
          }
          #${rootId} .codex-usage-hud-session-cleanup:has(> .codex-usage-hud-cleanup-empty-state):not(:has(> .codex-usage-hud-cleanup-scan-strip)) {
            grid-template-rows: minmax(0, 1fr);
          }
          #${rootId} .codex-usage-hud-session-index-drawer {
            min-width: 0;
            display: grid;
            grid-template-rows: 0fr;
            opacity: 0;
            visibility: hidden;
            transition: grid-template-rows .22s cubic-bezier(.32, .72, .3, 1), opacity .18s ease, visibility 0s linear .22s;
          }
          #${rootId} .codex-usage-hud-session-index-drawer[data-open="true"] {
            grid-template-rows: 1fr;
            opacity: 1;
            visibility: visible;
            transition: grid-template-rows .22s cubic-bezier(.32, .72, .3, 1), opacity .18s ease, visibility 0s linear 0s;
          }
          #${rootId} .codex-usage-hud-session-index-drawer-inner {
            min-width: 0;
            min-height: 0;
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-session-index-coverage {
            min-width: 0;
            display: grid;
            gap: 10px;
            padding: 11px 12px 12px;
            border: 1px solid #2b3644;
            border-radius: 6px;
            background: linear-gradient(180deg, #19212b 0%, #141a22 100%);
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-session-index-coverage[data-variant="hint"] {
            gap: 8px;
            border-color: #3d4d63;
            background: rgba(94, 167, 255, .06);
          }
          #${rootId} .codex-usage-hud-session-index-notice {
            display: flex;
            align-items: center;
            gap: 6px;
            margin: 0;
            padding: 6px 8px;
            border: 1px solid rgba(255, 184, 107, .28);
            border-radius: 4px;
            background: rgba(255, 184, 107, .08);
            color: #ffd9a8;
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-index-notice .codex-usage-hud-cleanup-icon {
            width: 13px;
            height: 13px;
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-session-index-head {
            min-width: 0;
            display: grid;
            grid-template-columns: auto minmax(0, 1fr) auto;
            align-items: center;
            gap: 10px;
          }
          #${rootId} .codex-usage-hud-session-index-badge {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            height: 20px;
            padding: 0 8px 0 7px;
            border: 1px solid transparent;
            border-radius: 999px;
            font-size: 10px;
            font-weight: 700;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-index-badge-dot {
            width: 6px;
            height: 6px;
            flex: 0 0 6px;
            border-radius: 50%;
            background: currentColor;
          }
          #${rootId} .codex-usage-hud-session-index-badge[data-state="empty"] {
            border-color: #3a4250;
            background: rgba(124, 133, 146, .12);
            color: #7c8592;
          }
          #${rootId} .codex-usage-hud-session-index-badge[data-state="off"] {
            border-color: #434b58;
            background: rgba(169, 180, 194, .10);
            color: #a9b4c2;
          }
          #${rootId} .codex-usage-hud-session-index-badge[data-state="active"] {
            border-color: rgba(94, 167, 255, .42);
            background: rgba(94, 167, 255, .14);
            color: #9ccbff;
          }
          #${rootId} .codex-usage-hud-session-index-badge[data-state="paused"] {
            border-color: rgba(255, 184, 107, .42);
            background: rgba(255, 184, 107, .12);
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-session-index-badge[data-state="ready"] {
            border-color: rgba(125, 227, 160, .38);
            background: rgba(125, 227, 160, .12);
            color: #7de3a0;
          }
          #${rootId} .codex-usage-hud-session-index-badge[data-state="error"] {
            border-color: rgba(255, 107, 107, .42);
            background: rgba(255, 107, 107, .12);
            color: #ffb3a8;
          }
          #${rootId} .codex-usage-hud-session-index-badge[data-state="active"] .codex-usage-hud-session-index-badge-dot {
            animation: codex-usage-hud-session-index-pulse 1.2s ease-in-out infinite;
          }
          @keyframes codex-usage-hud-session-index-pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: .25; }
          }
          #${rootId} .codex-usage-hud-session-index-text {
            min-width: 0;
            margin: 0;
            color: #b9cbe0;
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-session-index-metrics {
            display: flex;
            align-items: center;
            gap: 16px;
            margin: 0;
          }
          #${rootId} .codex-usage-hud-session-index-metric {
            display: grid;
            gap: 1px;
            text-align: right;
          }
          #${rootId} .codex-usage-hud-session-index-metric dt {
            color: #7c8592;
            font-size: 9px;
            letter-spacing: .04em;
          }
          #${rootId} .codex-usage-hud-session-index-metric dd {
            margin: 0;
            color: #dce6f2;
            font-size: 12px;
            font-weight: 700;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-index-progress {
            min-width: 0;
            display: grid;
            gap: 5px;
          }
          #${rootId} .codex-usage-hud-session-index-progress-meta {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 10px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-index-progress-meta > span:last-child {
            font-variant-numeric: tabular-nums;
            text-align: right;
          }
          #${rootId} .codex-usage-hud-session-index-track {
            position: relative;
            height: 6px;
            border-radius: 999px;
            overflow: hidden;
            background: rgba(255, 255, 255, .08);
          }
          #${rootId} .codex-usage-hud-session-index-fill {
            height: 100%;
            border-radius: inherit;
            background: linear-gradient(90deg, #5ea7ff, #9ccbff);
            box-shadow: 0 0 12px rgba(94, 167, 255, .35);
            transition: width .28s ease;
            position: relative;
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-session-index-progress[data-state="paused"] .codex-usage-hud-session-index-fill {
            background: linear-gradient(90deg, #d79a4e, #ffc98a);
            box-shadow: none;
          }
          #${rootId} .codex-usage-hud-session-index-fill[data-indeterminate="true"] {
            width: 38% !important;
            animation: codex-usage-hud-session-index-indet 1.35s ease-in-out infinite;
          }
          @keyframes codex-usage-hud-session-index-indet {
            0% { transform: translateX(-120%); }
            100% { transform: translateX(320%); }
          }
          #${rootId} .codex-usage-hud-session-index-fill[data-phase="indexing"]::after {
            content: "";
            position: absolute;
            top: 0;
            bottom: 0;
            left: 0;
            width: 42%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, .7), transparent);
            transform: translateX(-120%);
            animation: codex-usage-hud-session-index-shimmer 1.6s ease-in-out infinite;
          }
          @keyframes codex-usage-hud-session-index-shimmer {
            0% { transform: translateX(-120%); }
            100% { transform: translateX(320%); }
          }
          #${rootId} .codex-usage-hud-session-index-actions {
            min-width: 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding-top: 10px;
            border-top: 1px solid rgba(255, 255, 255, .07);
          }
          #${rootId} .codex-usage-hud-session-index-actions-primary {
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-session-index-actions-secondary {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-left: auto;
          }
          #${rootId} .codex-usage-hud-session-index-field {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: #9ccbff;
            font-size: 10px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-index-field select {
            max-width: 148px;
            min-height: 25px;
            box-sizing: border-box;
            border: 1px solid #44464c;
            border-radius: 5px;
            background: #17191c;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font: inherit;
            font-size: 10px;
            padding: 2px 5px;
          }
          #${rootId} .codex-usage-hud-session-index-action[data-primary="true"] {
            min-height: 26px;
            padding: 0 11px;
            border: 1px solid #3b8eea;
            border-radius: 5px;
            background: #3b8eea;
            color: #ffffff;
            font-size: 11px;
            font-weight: 600;
          }
          #${rootId} .codex-usage-hud-session-index-action[data-primary="true"]:hover:not(:disabled) {
            border-color: #4d9af0;
            background: #4d9af0;
          }
          #${rootId} .codex-usage-hud-session-index-switch {
            position: relative;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            min-height: 24px;
            color: #b9cbe0;
            font-size: 10px;
            white-space: nowrap;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-session-index-switch input {
            position: absolute;
            width: 1px;
            height: 1px;
            margin: -1px;
            opacity: 0;
            pointer-events: none;
          }
          #${rootId} .codex-usage-hud-session-index-switch-track {
            position: relative;
            display: inline-flex;
            align-items: center;
            width: 28px;
            height: 16px;
            flex: 0 0 28px;
            box-sizing: border-box;
            padding: 2px;
            border: 1px solid #596577;
            border-radius: 999px;
            background: #29313b;
            transition: background .16s ease, border-color .16s ease;
          }
          #${rootId} .codex-usage-hud-session-index-switch-track > span {
            display: block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #aeb9c8;
            box-shadow: 0 1px 3px rgba(0, 0, 0, .35);
            transition: transform .16s ease, background .16s ease;
          }
          #${rootId} .codex-usage-hud-session-index-switch input:checked + .codex-usage-hud-session-index-switch-track {
            border-color: #55c98a;
            background: rgba(85, 201, 138, .32);
          }
          #${rootId} .codex-usage-hud-session-index-switch input:checked + .codex-usage-hud-session-index-switch-track > span {
            transform: translateX(12px);
            background: #7de3a0;
          }
          #${rootId} .codex-usage-hud-session-index-switch input:focus-visible + .codex-usage-hud-session-index-switch-track {
            outline: 2px solid #5ea7ff;
            outline-offset: 2px;
          }
          #${rootId} .codex-usage-hud-session-index-switch strong {
            color: #7de3a0;
            font-weight: 600;
          }
          #${rootId} .codex-usage-hud-session-index-switch input:not(:checked) ~ strong {
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-session-index-clear {
            min-height: 25px;
            padding: 2px 8px;
            border: 1px solid rgba(255, 107, 107, .28);
            border-radius: 5px;
            background: rgba(255, 107, 107, .08);
            color: #ffb3a8;
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-index-clear:hover:not(:disabled) {
            border-color: rgba(255, 107, 107, .42);
            background: rgba(255, 107, 107, .18);
            color: #ffd2cb;
          }
          #${rootId} .codex-usage-hud-session-tools {
            min-width: 0;
            display: grid;
            gap: 8px;
            padding: 10px 13px;
            border-bottom: 1px solid var(--codex-usage-hud-divider, #393b40);
            position: relative;
            z-index: 3;
            overflow: visible;
          }
          #${rootId} .codex-usage-hud-session-tools-primary {
            min-width: 0;
            display: grid;
            grid-template-columns: minmax(0, 2fr) minmax(0, 1.5fr) minmax(0, 1.5fr);
            align-items: center;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-session-index-workdir {
            grid-column: 2;
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-session-search {
            grid-column: 1;
            min-width: 0;
            min-height: 32px;
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 0 10px;
            border: 1px solid #44464c;
            border-radius: 5px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            background: #111214;
          }
          #${rootId} .codex-usage-hud-session-search-meta {
            flex: 0 0 auto;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 9px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-search-meta[data-kind="warning"] {
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-session-workdir-filter {
            flex: 1 1 auto;
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-session-search input[type="search"] {
            min-width: 0;
            width: 100%;
            border: 0;
            outline: 0;
            background: transparent;
            color: var(--codex-usage-hud-text, #e8eef7);
            font: inherit;
            font-size: 11px;
            padding: 0;
          }
          #${rootId} .codex-usage-hud-session-search-submit {
            flex: 0 0 auto;
            min-height: 26px;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            border: 1px solid #44464c;
            border-radius: 4px;
            padding: 0 8px;
            background: #1b1e23;
            color: var(--codex-usage-hud-muted, #b7c0cc);
            font: inherit;
            font-size: 10px;
            cursor: pointer;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-search-submit:hover { background: #252a31; color: #eef4fb; }
          #${rootId} .codex-usage-hud-session-index-toggle {
            flex: 0 0 auto;
            height: 32px;
            box-sizing: border-box;
            min-height: 32px;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            border: 1px solid #44464c;
            border-radius: 4px;
            padding: 0 8px;
            background: #1b1e23;
            color: var(--codex-usage-hud-muted, #b7c0cc);
            font: inherit;
            font-size: 10px;
            cursor: pointer;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-index-toggle[aria-expanded="true"] {
            border-color: #5ea7ff;
            background: rgba(94, 167, 255, .12);
            color: #dcecff;
          }
          #${rootId} .codex-usage-hud-session-index-toggle:hover { background: #252a31; color: #eef4fb; }
          #${rootId} .codex-usage-hud-session-index-toggle-label {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          #${rootId} .codex-usage-hud-session-index-toggle-track {
            width: 34px;
            height: 4px;
            flex: 0 0 34px;
            overflow: hidden;
            border-radius: 999px;
            background: rgba(255, 255, 255, .12);
          }
          #${rootId} .codex-usage-hud-session-index-toggle-fill {
            display: block;
            height: 100%;
            border-radius: inherit;
            background: linear-gradient(90deg, #5ea7ff, #9ccbff);
            transition: width .28s ease;
            position: relative;
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-session-index-toggle-fill[data-indeterminate="true"] {
            width: 38% !important;
            animation: codex-usage-hud-session-index-indet 1.35s ease-in-out infinite;
          }
          #${rootId} .codex-usage-hud-session-date-filter {
            grid-column: 3;
            position: relative;
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-session-date-trigger,
          #${rootId} .codex-usage-hud-session-filter-control select {
            min-width: 0;
            min-height: 32px;
            border: 1px solid #44464c;
            border-radius: 5px;
            background: #17191c;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font: inherit;
            font-size: 10px;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-session-date-trigger {
            width: 100%;
            display: flex;
            align-items: center;
            gap: 7px;
            padding: 0 9px;
            text-align: left;
          }
          #${rootId} .codex-usage-hud-session-date-trigger > span {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-date-trigger .codex-usage-hud-cleanup-icon:last-child {
            margin-left: auto;
            transform: rotate(90deg);
          }
          #${rootId} .codex-usage-hud-session-date-filter[data-open="true"] .codex-usage-hud-session-date-trigger {
            border-color: #4c78a9;
            color: #c7e0ff;
            background: #192631;
          }
          #${rootId} .codex-usage-hud-session-date-popover {
            position: absolute;
            top: calc(100% + 7px);
            right: 0;
            z-index: 4;
            width: min(470px, calc(100vw - 44px));
            display: grid;
            gap: 10px;
            padding: 11px;
            border: 1px solid #46586d;
            border-radius: 6px;
            background: #171b20;
            box-shadow: 0 14px 28px rgba(0, 0, 0, .32);
          }
          #${rootId} .codex-usage-hud-session-date-fields {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
            gap: 7px;
            align-items: end;
          }
          #${rootId} .codex-usage-hud-session-date-fields label {
            min-width: 0;
            display: grid;
            gap: 5px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-date-fields input {
            min-width: 0;
            height: 31px;
            box-sizing: border-box;
            border: 1px solid #44464c;
            border-radius: 5px;
            background: #111214;
            color: var(--codex-usage-hud-text, #e8eef7);
            padding: 3px 6px;
            font: 10px Consolas, "Cascadia Mono", ui-monospace, monospace;
          }
          #${rootId} .codex-usage-hud-session-date-separator {
            padding-bottom: 8px;
            color: var(--codex-usage-hud-muted, #9da1a8);
          }
          #${rootId} .codex-usage-hud-session-date-presets {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 6px;
          }
          #${rootId} .codex-usage-hud-session-date-presets button,
          #${rootId} .codex-usage-hud-session-filter-summary-clear {
            min-height: 28px;
            border: 0;
            border-radius: 5px;
            background: #272a2e;
            color: #9ccbff;
            padding: 4px 8px;
            font: inherit;
            font-size: 10px;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-session-date-actions {
            display: flex;
            justify-content: flex-end;
            gap: 7px;
          }
          #${rootId} .codex-usage-hud-session-date-error {
            color: var(--codex-usage-hud-warning, #ffb86b);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-filter-controls {
            min-width: 0;
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 6px;
          }
          #${rootId} .codex-usage-hud-session-filter-control {
            min-width: 0;
            display: grid;
            gap: 3px;
          }
          #${rootId} .codex-usage-hud-session-filter-control label {
            min-width: 0;
            display: grid;
            gap: 3px;
          }
          #${rootId} .codex-usage-hud-session-filter-control label > span {
            overflow: hidden;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 9px;
            font-weight: 650;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-filter-control select {
            width: 100%;
            padding: 4px 7px;
          }
          #${rootId} .codex-usage-hud-session-filter-summary {
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 7px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-filter-summary-tags {
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 5px;
            overflow: auto hidden;
            scrollbar-width: none;
          }
          #${rootId} .codex-usage-hud-session-filter-summary-tags::-webkit-scrollbar { display: none; }
          #${rootId} .codex-usage-hud-session-filter-summary-tag {
            flex: 0 0 auto;
            max-width: 220px;
            overflow: hidden;
            border: 1px solid #405267;
            border-radius: 999px;
            color: #a9d1ff;
            padding: 3px 7px;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-filter-summary-clear {
            flex: 0 0 auto;
            min-height: 23px;
            padding: 2px 7px;
            color: var(--codex-usage-hud-muted, #9da1a8);
          }
          #${rootId} .codex-usage-hud-session-table {
            min-width: 0;
            position: relative;
            z-index: 1;
            min-height: 0;
            overflow: auto;
            scrollbar-width: thin;
          }
          #${rootId} .codex-usage-hud-session-head {
            position: sticky;
            top: 0;
            z-index: 2;
          }
          #${rootId} .codex-usage-hud-session-pagination {
            min-width: 0;
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 9px;
            padding: 7px 13px;
            border-top: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-pagination > span {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-pagination > div {
            display: flex;
            gap: 5px;
            flex: 0 0 auto;
          }
          #${rootId} .codex-usage-hud-session-pagination button {
            width: 27px;
            height: 27px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0;
            border: 1px solid #44464c;
            border-radius: 5px;
            background: #17191c;
            color: #b9c5d3;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-session-pagination button:hover:not(:disabled) {
            border-color: #5a7898;
            color: #e8eef7;
            background: #202a34;
          }
          #${rootId} .codex-usage-hud-session-pagination button:focus-visible {
            outline: 1px solid var(--codex-usage-hud-accent, #f3d27a);
            outline-offset: 2px;
          }
          #${rootId} .codex-usage-hud-session-pagination button:disabled {
            opacity: .35;
            cursor: default;
          }
          #${rootId} .codex-usage-hud-session-pagination .codex-usage-hud-cleanup-icon {
            width: 13px;
            height: 13px;
          }
          #${rootId} .codex-usage-hud-session-page-prev {
            transform: rotate(180deg);
          }
          #${rootId} .codex-usage-hud-session-head,
          #${rootId} .codex-usage-hud-session-row {
            display: grid;
            grid-template-columns: 28px minmax(0, 1.35fr) minmax(0, .9fr) 88px 72px 82px;
            grid-template-areas: "check title workdir time status size";
            gap: 9px;
            align-items: center;
            padding: 0 13px;
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-session-head {
            min-height: 31px;
            color: #73777f;
            font-size: 10px;
            border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
            background: #1c1d1f;
            position: sticky;
            top: 0;
            z-index: 1;
          }
          #${rootId} .codex-usage-hud-session-head > :nth-child(1),
          #${rootId} .codex-usage-hud-session-row > :nth-child(1) { grid-area: check; }
          #${rootId} .codex-usage-hud-session-head > :nth-child(2),
          #${rootId} .codex-usage-hud-session-row > :nth-child(2) { grid-area: title; }
          #${rootId} .codex-usage-hud-session-head > :nth-child(3),
          #${rootId} .codex-usage-hud-session-row > :nth-child(3) { grid-area: workdir; }
          #${rootId} .codex-usage-hud-session-head > :nth-child(4),
          #${rootId} .codex-usage-hud-session-row > :nth-child(4) { grid-area: time; }
          #${rootId} .codex-usage-hud-session-head > :nth-child(5),
          #${rootId} .codex-usage-hud-session-row > :nth-child(5) { grid-area: status; }
          #${rootId} .codex-usage-hud-session-head > :nth-child(6),
          #${rootId} .codex-usage-hud-session-row > :nth-child(6) { grid-area: size; }
          #${rootId} .codex-usage-hud-session-head span:last-child,
          #${rootId} .codex-usage-hud-session-size { text-align: right; }
          #${rootId} .codex-usage-hud-session-row {
            min-height: 54px;
            border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
          }
          #${rootId} .codex-usage-hud-session-row[data-selected="true"] { background: #172331; }
          #${rootId} .codex-usage-hud-session-row[data-selectable="false"] { opacity: .55; }
          #${rootId} .codex-usage-hud-session-row input[type="checkbox"] {
            width: 15px;
            height: 15px;
            margin: 0;
            accent-color: #3b8eea;
          }
          #${rootId} .codex-usage-hud-session-select {
            display: flex;
            align-items: center;
          }
          #${rootId} .codex-usage-hud-session-title {
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-session-title strong,
          #${rootId} .codex-usage-hud-session-workdir,
          #${rootId} .codex-usage-hud-session-title > span {
            display: block;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-title strong {
            font-size: 11px;
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-session-title span {
            margin-top: 3px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 9px;
          }
          #${rootId} .codex-usage-hud-session-title span[data-secondary="true"] {
            display: none;
          }
          #${rootId} .codex-usage-hud-session-title span[data-kind="warning"] {
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-session-title span[data-search-hit="true"] {
            color: #9ccbff;
            font-size: 9px;
          }
          #${rootId} .codex-usage-hud-session-workdir {
            color: #c4c7cc;
            font-size: 10px;
          }
          #${rootId} button.codex-usage-hud-session-workdir {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            min-width: 0;
            max-width: 100%;
            padding: 0;
            border: 0;
            background: transparent;
            color: #c4c7cc;
            cursor: pointer;
            font: inherit;
            text-align: left;
          }
          #${rootId} button.codex-usage-hud-session-workdir:hover {
            color: var(--codex-usage-hud-accent, #f3d27a);
            text-decoration: underline;
            text-underline-offset: 2px;
          }
          #${rootId} button.codex-usage-hud-session-workdir:focus-visible {
            outline: 1px solid var(--codex-usage-hud-accent, #f3d27a);
            outline-offset: 2px;
          }
          #${rootId} button.codex-usage-hud-session-workdir > .codex-usage-hud-cleanup-icon {
            flex: 0 0 auto;
            width: 12px;
            height: 12px;
          }
          #${rootId} button.codex-usage-hud-session-workdir > span {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} button.codex-usage-hud-session-jump {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 auto;
            width: 24px;
            height: 24px;
            margin-left: 6px;
            padding: 0;
            border: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
            border-radius: 999px;
            background: color-mix(in srgb, var(--codex-usage-hud-surface-soft, #2a2f3a) 80%, transparent);
            color: #b9bcc3;
            cursor: pointer;
            font: inherit;
            transition: color .12s ease, border-color .12s ease, background .12s ease, opacity .12s ease;
            vertical-align: middle;
          }
          #${rootId} button.codex-usage-hud-session-jump:hover {
            color: var(--codex-usage-hud-accent, #f3d27a);
            border-color: color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 55%, transparent);
          }
          #${rootId} button.codex-usage-hud-session-jump:focus-visible {
            outline: 1px solid var(--codex-usage-hud-accent, #f3d27a);
            outline-offset: 2px;
          }
          #${rootId} button.codex-usage-hud-session-jump[disabled] {
            cursor: progress;
            opacity: .55;
          }
          #${rootId} button.codex-usage-hud-session-jump[data-kind="fallback"] {
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} button.codex-usage-hud-session-jump > .codex-usage-hud-cleanup-icon {
            width: 12px;
            height: 12px;
          }
          @keyframes codex-usage-hud-session-jump-spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }
          #${rootId} button.codex-usage-hud-session-jump.codex-usage-hud-session-jump-inflight > .codex-usage-hud-cleanup-icon {
            animation: codex-usage-hud-session-jump-spin 0.9s linear infinite;
          }
          #${rootId} .codex-usage-hud-session-cell {
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-size {
            text-align: right;
            color: #c7cad0;
            font-size: 10px;
            font-variant-numeric: tabular-nums;
          }
          #${rootId} .codex-usage-hud-session-badge {
            min-height: 22px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0 7px;
            border: 1px solid #4a4d53;
            border-radius: 999px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 9px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-status-cell {
            min-width: 0;
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 4px;
          }
          #${rootId} .codex-usage-hud-session-badge[data-state="current"],
          #${rootId} .codex-usage-hud-session-badge[data-state="running"] {
            border-color: #356894;
            color: #8dc7ff;
            background: #172431;
          }
          #${rootId} .codex-usage-hud-session-badge[data-state="archived"] {
            border-color: #5b4b2b;
            color: #e2bd6c;
            background: #262116;
          }
          #${rootId} .codex-usage-hud-session-capability {
            margin: 8px 13px 0;
            padding: 8px 10px;
            border-left: 3px solid var(--codex-usage-hud-warning, #ffb86b);
            background: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 8%, transparent);
            color: var(--codex-usage-hud-warning, #ffb86b);
            overflow-wrap: anywhere;
          }
          #${rootId} .codex-usage-hud-session-results {
            min-width: 0;
            display: grid;
            gap: 5px;
            padding: 9px 13px;
            border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
          }
          #${rootId} .codex-usage-hud-session-results > div {
            min-width: 0;
            display: flex;
            justify-content: space-between;
            gap: 10px;
          }
          #${rootId} .codex-usage-hud-session-results > div span:first-child {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-results [data-kind="success"] { color: var(--codex-usage-hud-success, #8fe3a1); }
          #${rootId} .codex-usage-hud-session-results [data-kind="error"] { color: var(--codex-usage-hud-warning, #ffb86b); }
          #${rootId} .codex-usage-hud-settings-confirm-layer {
            position: absolute;
            inset: 0;
            z-index: 3;
            display: grid;
            place-items: center;
            padding: 24px;
            background: rgba(7, 8, 9, .72);
            backdrop-filter: blur(4px);
          }
          #${rootId} .codex-usage-hud-settings-confirm-card {
            position: relative;
            width: min(500px, calc(100% - 36px));
            display: grid;
            gap: 12px;
            padding: 18px 0 0;
            border: 1px solid color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 34%, transparent);
            border-radius: 8px;
            background: #1d1e20;
            box-shadow: 0 22px 58px rgba(0, 0, 0, .52);
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-settings-confirm-close {
            position: absolute;
            top: 8px;
            right: 8px;
            width: 28px;
            height: 28px;
            min-width: 28px;
            min-height: 28px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border: 0;
            border-radius: 6px;
            background: transparent;
            color: var(--codex-usage-hud-text-muted, #8a93a0);
            padding: 0;
            margin: 0;
            font-size: 18px;
            font-weight: 700;
            line-height: 1;
            cursor: pointer;
            z-index: 1;
          }
          #${rootId} .codex-usage-hud-settings-confirm-close:hover {
            background: color-mix(in srgb, var(--codex-usage-hud-panel-border, #2e3846) 72%, #ffffff 28%);
            color: #ffffff;
          }
          #${rootId} .codex-usage-hud-settings-confirm-card:has(.codex-usage-hud-settings-confirm-main),
          #${rootId} .codex-usage-hud-settings-confirm-card[data-tone="danger"] {
            gap: 0;
            padding: 0;
          }
          #${rootId} .codex-usage-hud-settings-confirm-card[data-tone="danger"] {
            border-color: #5e3a3d;
          }
          #${rootId} .codex-usage-hud-session-transfer-card {
            width: min(720px, calc(100% - 18px));
            max-height: calc(100% - 18px);
            grid-template-rows: auto minmax(0, 1fr) auto;
            gap: 0;
            padding: 0;
          }
          #${rootId} .codex-usage-hud-session-transfer-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
            padding: 16px 18px 12px;
            border-bottom: 1px solid var(--codex-usage-hud-divider, #393b40);
          }
          #${rootId} .codex-usage-hud-session-transfer-head .codex-usage-hud-settings-confirm-title {
            padding: 0;
          }
          #${rootId} .codex-usage-hud-session-transfer-body {
            min-height: 0;
            overflow: auto;
            display: grid;
            gap: 10px;
            padding: 14px 18px;
          }
          #${rootId} .codex-usage-hud-session-transfer-context {
            min-width: 0;
            display: grid;
            grid-template-columns: auto minmax(90px, 1fr) auto minmax(150px, 1fr);
            align-items: center;
            gap: 7px 10px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-transfer-context strong {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: var(--codex-usage-hud-text, #f1f3f5);
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-session-transfer-context select,
          #${rootId} .codex-usage-hud-session-transfer-search {
            min-width: 0;
            box-sizing: border-box;
            min-height: 31px;
            border: 1px solid #44464c;
            border-radius: 5px;
            background: #111214;
            color: var(--codex-usage-hud-text, #e8eef7);
            font: inherit;
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-session-transfer-context select {
            width: 100%;
            padding: 4px 7px;
          }
          #${rootId} .codex-usage-hud-session-transfer-mode {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-session-transfer-mode label {
            min-width: 0;
            display: grid;
            grid-template-columns: auto 1fr;
            grid-template-rows: auto auto;
            column-gap: 7px;
            align-items: center;
            padding: 8px 9px;
            border: 1px solid #3d424b;
            border-radius: 6px;
            color: var(--codex-usage-hud-text, #e8eef7);
            cursor: pointer;
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-session-transfer-mode label:has(input:checked) {
            border-color: #4c78a9;
            background: #192631;
          }
          #${rootId} .codex-usage-hud-session-transfer-mode input {
            grid-row: 1 / span 2;
            accent-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-session-transfer-mode small {
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 9px;
          }
          #${rootId} .codex-usage-hud-session-transfer-search {
            display: flex;
            align-items: center;
            gap: 7px;
            padding: 0 9px;
            color: var(--codex-usage-hud-muted, #9da1a8);
          }
          #${rootId} .codex-usage-hud-session-transfer-search input {
            min-width: 0;
            width: 100%;
            border: 0;
            outline: 0;
            background: transparent;
            color: inherit;
            font: inherit;
          }
          #${rootId} .codex-usage-hud-session-transfer-toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-transfer-toolbar label {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: var(--codex-usage-hud-text, #e8eef7);
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-session-transfer-toolbar input,
          #${rootId} .codex-usage-hud-session-transfer-row input {
            accent-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-session-transfer-list {
            min-height: 96px;
            max-height: 290px;
            overflow: auto;
            display: grid;
            align-content: start;
            gap: 5px;
            padding: 2px;
          }
          #${rootId} .codex-usage-hud-session-transfer-list[data-state="progress"] {
            min-height: 300px;
            max-height: none;
            overflow: hidden;
            display: grid;
            grid-template-rows: auto minmax(0, 1fr);
            gap: 0;
            padding: 0;
          }
          #${rootId} .codex-usage-hud-session-transfer-progress-state {
            min-width: 0;
            min-height: 0;
            height: 100%;
            display: grid;
            grid-template-rows: auto minmax(0, 1fr);
          }
          #${rootId} .codex-usage-hud-session-transfer-progress-state .codex-usage-hud-cleanup-empty-state {
            min-height: 220px;
          }
          #${rootId} .codex-usage-hud-session-transfer-row {
            min-width: 0;
            display: grid;
            grid-template-columns: auto minmax(0, 1fr) auto;
            align-items: center;
            gap: 8px;
            padding: 7px 8px;
            border: 1px solid #30353d;
            border-radius: 5px;
            background: #171a1e;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-session-transfer-row:hover {
            border-color: #4c78a9;
            background: #19232d;
          }
          #${rootId} .codex-usage-hud-session-transfer-main {
            min-width: 0;
            display: grid;
            gap: 2px;
          }
          #${rootId} .codex-usage-hud-session-transfer-main strong,
          #${rootId} .codex-usage-hud-session-transfer-main small {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-transfer-main strong {
            color: var(--codex-usage-hud-text, #e8eef7);
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-session-transfer-main small,
          #${rootId} .codex-usage-hud-session-transfer-time {
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 9px;
          }
          #${rootId} .codex-usage-hud-session-transfer-time {
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-session-transfer-empty {
            display: grid;
            place-items: center;
            min-height: 86px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 10px;
            text-align: center;
          }
          #${rootId} .codex-usage-hud-session-transfer-progress,
          #${rootId} .codex-usage-hud-session-transfer-result {
            display: grid;
            gap: 4px;
            padding: 8px 9px;
            border: 1px solid #35475b;
            border-radius: 5px;
            background: #17212b;
            color: #c7e0ff;
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-transfer-progress {
            display: flex;
            justify-content: space-between;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-session-transfer-pagination {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            min-height: 28px;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-session-transfer-pagination .codex-usage-hud-settings-icon-action {
            flex: 0 0 26px;
            width: 26px;
            min-width: 26px;
            height: 26px;
            padding: 0;
          }
          #${rootId} .codex-usage-hud-session-transfer-result[data-kind="success"] {
            border-color: #315b41;
            background: #18271e;
            color: #a7e4b3;
          }
          #${rootId} .codex-usage-hud-session-transfer-result[data-kind="error"] {
            border-color: #5e3a3d;
            background: #2a1d20;
            color: #ffb9bd;
          }
          #${rootId} .codex-usage-hud-session-transfer-error {
            overflow-wrap: anywhere;
            color: #ffcf9a;
          }
          #${rootId} .codex-usage-hud-session-transfer-error small[data-kind="hint"] {
            display: block;
            margin-top: 3px;
            color: #b8c0cc;
            font-size: 11px;
            line-height: 1.5;
          }
          #${rootId} .codex-usage-hud-session-transfer-target-notice {
            overflow-wrap: anywhere;
            color: #ffcf9a;
          }
          #${rootId} .codex-usage-hud-session-transfer-card > .codex-usage-hud-settings-confirm-actions {
            flex-wrap: wrap;
            padding: 10px 18px 12px;
            border-top: 1px solid var(--codex-usage-hud-divider, #393b40);
          }
          #${rootId} .codex-usage-hud-settings-confirm-main {
            padding: 18px 19px 16px;
          }
          #${rootId} .codex-usage-hud-settings-confirm-card > .codex-usage-hud-settings-confirm-kicker,
          #${rootId} .codex-usage-hud-settings-confirm-card > .codex-usage-hud-settings-confirm-title,
          #${rootId} .codex-usage-hud-settings-confirm-card > .codex-usage-hud-settings-confirm-body {
            padding-inline: 18px;
          }
          #${rootId} .codex-usage-hud-settings-confirm-kicker {
            color: var(--codex-usage-hud-accent, #f3d27a);
            font-size: 11px;
            font-weight: 800;
            letter-spacing: .08em;
            text-transform: uppercase;
          }
          #${rootId} .codex-usage-hud-settings-confirm-title {
            margin: 0;
            color: var(--codex-usage-hud-text, #f6f9fc);
            font-size: 17px;
            font-weight: 720;
            line-height: 1.25;
          }
          #${rootId} .codex-usage-hud-settings-confirm-body {
            margin: 0;
            color: #c9cbd0;
            font-size: 11px;
            line-height: 1.6;
            white-space: pre-wrap;
          }
          #${rootId} .codex-usage-hud-provider-config-card {
            width: min(680px, calc(100% - 28px));
          }
          #${rootId} .codex-usage-hud-provider-config-status {
            min-height: 16px;
            padding: 2px 18px 0;
            font-size: 11px;
            color: var(--codex-usage-hud-request-muted, #a9bcd2);
            overflow-wrap: anywhere;
          }
          #${rootId} .codex-usage-hud-provider-config-status[data-kind="error"] {
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-provider-config-preview {
            min-width: 0;
            display: grid;
            gap: 8px;
            padding: 0 18px;
          }
          #${rootId} .codex-usage-hud-provider-config-preview > summary {
            width: fit-content;
            color: var(--codex-usage-hud-muted, #8492a6);
            cursor: pointer;
            font-size: 10px;
            font-weight: 700;
            list-style-position: inside;
          }
          #${rootId} .codex-usage-hud-provider-config-preview > summary:hover,
          #${rootId} .codex-usage-hud-provider-config-preview > summary:focus-visible {
            color: var(--codex-usage-hud-accent, #f3d27a);
            outline: none;
          }
          #${rootId} .codex-usage-hud-provider-config-grid {
            min-width: 0;
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            padding: 0 18px;
          }
          #${rootId} .codex-usage-hud-provider-config-grid > label {
            min-width: 0;
            display: grid;
            gap: 4px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-provider-config-grid > label input,
          #${rootId} .codex-usage-hud-provider-config-grid > label select {
            min-width: 0;
            box-sizing: border-box;
            width: 100%;
            min-height: 31px;
            padding: 5px 7px;
            border: 1px solid #47494f;
            border-radius: 5px;
            background: #14171b;
            color: #f1f3f5;
            outline: none;
            font: inherit;
            font-weight: 400;
          }
          #${rootId} .codex-usage-hud-provider-config-grid > label input:focus,
          #${rootId} .codex-usage-hud-provider-config-grid > label select:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-config-grid > label input[readonly] {
            opacity: .7;
            cursor: not-allowed;
          }
          #${rootId} .codex-usage-hud-provider-config-grid-placeholder {
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-provider-config-auth-note {
            min-width: 0;
            box-sizing: border-box;
            display: flex;
            align-items: center;
            padding: 6px 10px;
            border: 1px solid #3a3d44;
            border-radius: 5px;
            background: rgba(243, 210, 122, 0.07);
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 400;
            line-height: 1.45;
          }
          #${rootId} .codex-usage-hud-provider-config-section {
            min-width: 0;
            display: grid;
            gap: 5px;
            padding: 0 18px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-provider-config-section textarea {
            box-sizing: border-box;
            width: 100%;
            min-height: 150px;
            resize: vertical;
            padding: 8px;
            border: 1px solid #47494f;
            border-radius: 5px;
            background: #14171b;
            color: #f1f3f5;
            outline: none;
            font: 12px/1.45 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
            font-weight: 400;
            white-space: pre;
          }
          #${rootId} .codex-usage-hud-provider-config-section textarea:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-config-section span {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 400;
            line-height: 1.4;
          }
          #${rootId} .codex-usage-hud-provider-config-scope {
            min-width: 0;
            grid-column: 1 / -1;
            display: flex;
            flex-wrap: wrap;
            gap: 10px 16px;
            margin: 0;
            padding: 8px 9px;
            border: 1px solid #47494f;
            border-radius: 5px;
            color: #c9cbd0;
            font-size: 10px;
            font-weight: 400;
          }
          #${rootId} .codex-usage-hud-provider-config-scope legend {
            padding: 0 4px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-provider-config-scope label {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-provider-config-scope input {
            width: 15px;
            height: 15px;
            margin: 0;
            accent-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-config-apikey {
            grid-column: 1 / -1;
            min-width: 0;
            display: grid;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-provider-config-apikey label {
            min-width: 0;
            display: grid;
            gap: 4px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-provider-config-apikey-field {
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 6px;
          }
          #${rootId} .codex-usage-hud-provider-config-apikey-field input {
            min-width: 0;
            box-sizing: border-box;
            flex: 1 1 auto;
            min-height: 31px;
            padding: 5px 7px;
            border: 1px solid #47494f;
            border-radius: 5px;
            background: #14171b;
            color: #f1f3f5;
            outline: none;
            font: inherit;
            font-weight: 400;
          }
          #${rootId} .codex-usage-hud-provider-config-apikey-field input:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-config-eye {
            flex: 0 0 auto;
            box-sizing: border-box;
            width: 34px;
            min-height: 31px;
            padding: 0;
            line-height: 1;
          }
          #${rootId} .codex-usage-hud-provider-config-fetch {
            flex: 0 0 auto;
          }
          #${rootId} .codex-usage-hud-provider-config-models {
            flex: 0 1 auto;
            min-width: 130px;
            max-width: 220px;
            box-sizing: border-box;
            min-height: 31px;
            padding: 4px 6px;
            border: 1px solid #47494f;
            border-radius: 5px;
            background: #14171b;
            color: #f1f3f5;
            outline: none;
            font: inherit;
            font-weight: 400;
          }
          #${rootId} .codex-usage-hud-provider-config-models:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-config-models[hidden] {
            display: none;
          }
          #${rootId} .codex-usage-hud-provider-config-fetch-status {
            min-height: 14px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 400;
            line-height: 1.4;
          }
          #${rootId} .codex-usage-hud-provider-config-fetch-status-error {
            color: #f27878;
          }
          #${rootId} .codex-usage-hud-provider-config-chat-test {
            min-width: 0;
            padding: 4px 0 0;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            line-height: 1.4;
          }
          #${rootId} .codex-usage-hud-provider-config-chat-test[hidden] {
            display: none;
          }
          #${rootId} .codex-usage-hud-provider-config-chat-test > summary {
            width: fit-content;
            cursor: pointer;
            color: inherit;
            font-weight: 700;
            list-style-position: inside;
          }
          #${rootId} .codex-usage-hud-provider-config-chat-test > summary:hover,
          #${rootId} .codex-usage-hud-provider-config-chat-test > summary:focus-visible {
            color: var(--codex-usage-hud-accent, #f3d27a);
            outline: none;
          }
          #${rootId} .codex-usage-hud-provider-config-chat-test-body {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 8px;
            align-items: end;
            margin-top: 8px;
          }
          #${rootId} .codex-usage-hud-provider-config-chat-test-body label {
            min-width: 0;
            display: grid;
            gap: 4px;
            color: inherit;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-provider-config-chat-test-body input {
            box-sizing: border-box;
            width: 100%;
            min-height: 31px;
            padding: 5px 7px;
            border: 1px solid #47494f;
            border-radius: 5px;
            background: #14171b;
            color: #f1f3f5;
            outline: none;
            font: inherit;
            font-weight: 400;
          }
          #${rootId} .codex-usage-hud-provider-config-chat-test-body input:focus {
            border-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-provider-config-chat-result {
            grid-column: 1 / -1;
            min-height: 14px;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 400;
            line-height: 1.4;
            word-break: break-word;
          }
          #${rootId} .codex-usage-hud-provider-config-chat-result-error {
            color: #f27878;
          }
          #${rootId} .codex-usage-hud-provider-delete-card {
            width: min(520px, calc(100% - 28px));
          }
          #${rootId} .codex-usage-hud-provider-delete-card > .codex-usage-hud-settings-confirm-title {
            padding: 14px 18px 12px;
          }
          #${rootId} .codex-usage-hud-provider-delete-options {
            display: grid;
            gap: 9px;
            padding: 10px 18px;
            color: #e3e6eb;
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-provider-delete-options label {
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-provider-delete-options input {
            width: 15px;
            height: 15px;
            margin: 0;
            accent-color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-settings-confirm-main .codex-usage-hud-settings-confirm-body {
            margin: 8px 0 0;
          }
          #${rootId} .codex-usage-hud-settings-confirm-danger-mark {
            width: 42px;
            height: 42px;
            display: grid;
            place-items: center;
            border: 1px solid #754247;
            border-radius: 50%;
            background: #2b191b;
            color: #ff858a;
            margin-bottom: 12px;
          }
          #${rootId} .codex-usage-hud-settings-confirm-summary {
            margin-top: 13px;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            border: 1px solid #47494f;
            border-radius: 6px;
            overflow: hidden;
          }
          #${rootId} .codex-usage-hud-settings-confirm-summary div {
            padding: 10px 11px;
            border-right: 1px solid #47494f;
          }
          #${rootId} .codex-usage-hud-settings-confirm-summary div:last-child { border-right: 0; }
          #${rootId} .codex-usage-hud-settings-confirm-summary span {
            display: block;
            color: var(--codex-usage-hud-muted, #9da1a8);
            font-size: 9px;
          }
          #${rootId} .codex-usage-hud-settings-confirm-summary strong {
            display: block;
            margin-top: 3px;
            font-size: 13px;
          }
          #${rootId} .codex-usage-hud-settings-confirm-note {
            margin-top: 12px;
            display: flex;
            align-items: flex-start;
            gap: 8px;
            color: #e6bd69;
            font-size: 10px;
            line-height: 1.5;
          }
          #${rootId} .codex-usage-hud-settings-confirm-actions {
            min-height: 52px;
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 8px;
            padding: 9px 12px;
            border-top: 1px solid var(--codex-usage-hud-divider, #393b40);
            background: #292a2d;
          }
          #${rootId} .codex-usage-hud-pricing-apply-all {
            min-width: 0;
            max-width: 55%;
            display: inline-flex;
            align-items: center;
            gap: 7px;
            margin-right: auto;
            color: #c9cbd0;
            font-size: 11px;
            line-height: 1.35;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-pricing-apply-all input {
            width: 15px;
            height: 15px;
            flex: 0 0 15px;
            margin: 0;
            accent-color: var(--codex-usage-hud-accent, #f3d27a);
          }
          #${rootId} .codex-usage-hud-settings-confirm-actions .codex-usage-hud-settings-action {
            min-height: 34px;
            padding-inline: 12px;
          }
          #${rootId} .codex-usage-hud-settings-confirm-actions .codex-usage-hud-settings-action[data-danger="true"] {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-settings-confirm-actions .codex-usage-hud-settings-action[data-danger="true"] svg {
            display: block;
            flex: 0 0 auto;
          }
          #${rootId} .codex-usage-hud-settings-loading-track {
            position: relative;
            overflow: hidden;
            height: 10px;
            border-radius: 999px;
            background: var(--codex-usage-hud-progress-track, #1A2430);
          }
          #${rootId} .codex-usage-hud-settings-loading-bar,
          #${rootId} .codex-usage-hud-settings-loading-glow {
            position: absolute;
            top: 0;
            bottom: 0;
            border-radius: 999px;
            animation: codex-usage-hud-loading-slide 1.2s ease-in-out infinite alternate;
          }
          #${rootId} .codex-usage-hud-settings-loading-bar {
            left: 0;
            width: 34%;
            background: var(--codex-usage-hud-accent, #F3D27A);
          }
          #${rootId} .codex-usage-hud-settings-loading-glow {
            left: 8%;
            width: 16%;
            background: color-mix(in srgb, var(--codex-usage-hud-accent, #FFE7A0) 55%, white);
            animation-duration: 1.2s;
          }
          @keyframes codex-usage-hud-loading-slide {
            from {
              transform: translateX(0);
            }
            to {
              transform: translateX(190%);
            }
          }
          #${rootId} .codex-usage-hud-settings-action[data-variant="subtle"] {
            background: var(--codex-usage-hud-header-surface, #202833);
            color: var(--codex-usage-hud-accent, #f3d27a);
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-settings-action[data-variant="ghost"] {
            background: transparent;
            border: 1px solid var(--codex-usage-hud-panel-border, #2e3846);
            color: var(--codex-usage-hud-request-muted, #a9bcd2);
          }
          #${rootId} .codex-usage-hud-support {
            display: grid;
            gap: 12px;
            color: var(--codex-usage-hud-text, #dde7f2);
            line-height: 1.55;
          }
          #${rootId} .codex-usage-hud-rest-reminder-card {
            display: grid;
            gap: 7px;
            padding: 8px 0 10px;
            border-top: 1px solid var(--codex-usage-hud-panel-border, #2e3846);
            border-bottom: 1px solid var(--codex-usage-hud-panel-border, #2e3846);
          }
          #${rootId} .codex-usage-hud-rest-reminder-top {
            display: flex;
            align-items: center;
            gap: 8px;
            min-height: 26px;
          }
          #${rootId} .codex-usage-hud-rest-reminder-title {
            color: var(--codex-usage-hud-text, #dde7f2);
            font-weight: 750;
            font-size: 12px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-rest-reminder-toggle {
            display: inline-flex;
            align-items: center;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-rest-reminder-toggle input {
            position: absolute;
            opacity: 0;
            pointer-events: none;
          }
          #${rootId} .codex-usage-hud-rest-reminder-track {
            position: relative;
            width: 28px;
            height: 16px;
            border-radius: 999px;
            background: #3a4652;
            transition: background .12s ease;
          }
          #${rootId} .codex-usage-hud-rest-reminder-track::after {
            content: "";
            position: absolute;
            top: 2px;
            left: 2px;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #f4f6f8;
            transition: transform .12s ease;
          }
          #${rootId} .codex-usage-hud-rest-reminder-toggle input:checked + .codex-usage-hud-rest-reminder-track {
            background: var(--codex-usage-hud-success, #8fe3a1);
          }
          #${rootId} .codex-usage-hud-rest-reminder-toggle input:checked + .codex-usage-hud-rest-reminder-track::after {
            transform: translateX(12px);
          }
          #${rootId} .codex-usage-hud-rest-reminder-status {
            margin-left: auto;
            display: inline-flex;
            align-items: baseline;
            gap: 6px;
            min-width: 0;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 11px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-rest-reminder-status[data-state="lunch"] {
            color: var(--codex-usage-hud-warning, #ffb86b);
          }
          #${rootId} .codex-usage-hud-rest-reminder-status[data-state="break"] {
            color: var(--codex-usage-hud-success, #8fe3a1);
          }
          #${rootId} .codex-usage-hud-rest-reminder-status[data-state="off"],
          #${rootId} .codex-usage-hud-rest-reminder-status[data-state="disabled"] {
            color: var(--codex-usage-hud-request-muted, #718095);
          }
          #${rootId} .codex-usage-hud-rest-reminder-status b {
            color: var(--codex-usage-hud-accent, #f3d27a);
            font-size: 12px;
            font-weight: 750;
            font-variant-numeric: tabular-nums;
          }
          #${rootId} .codex-usage-hud-rest-reminder-status[data-state="lunch"] b,
          #${rootId} .codex-usage-hud-rest-reminder-status[data-state="break"] b,
          #${rootId} .codex-usage-hud-rest-reminder-status[data-state="off"] b,
          #${rootId} .codex-usage-hud-rest-reminder-status[data-state="disabled"] b {
            color: inherit;
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-rest-reminder-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 5px 6px;
          }
          #${rootId} .codex-usage-hud-rest-reminder-field {
            display: grid;
            grid-template-columns: auto minmax(0, 1fr);
            align-items: center;
            gap: 5px;
            min-width: 0;
            min-height: 28px;
            padding: 0 6px 0 7px;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 6px;
            background: rgba(255, 255, 255, .015);
          }
          #${rootId} .codex-usage-hud-rest-reminder-field > span {
            color: var(--codex-usage-hud-request-muted, #718095);
            font-size: 10px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-rest-reminder-field input {
            min-width: 0;
            width: 100%;
            box-sizing: border-box;
            min-height: 24px;
            border: 0;
            background: transparent;
            color: var(--codex-usage-hud-text, #dde7f2);
            padding: 0;
            text-align: right;
            font-size: 12px;
            font-weight: 650;
            font-variant-numeric: tabular-nums;
          }
          #${rootId} .codex-usage-hud-rest-reminder-field input:focus {
            outline: none;
            box-shadow: none;
          }
          #${rootId} .codex-usage-hud-rest-reminder-field:focus-within {
            border-color: color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 55%, var(--codex-usage-hud-panel-border, #2e3846));
            box-shadow: 0 0 0 2px rgba(243, 210, 122, .08);
          }
          #${rootId} .codex-usage-hud-rest-reminder-schedule {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 6px;
          }
          #${rootId} .codex-usage-hud-rest-reminder-slot {
            display: grid;
            grid-template-columns: auto minmax(0, 1fr) auto;
            align-items: center;
            gap: 5px;
            min-height: 30px;
            padding: 0 7px;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 6px;
            background: rgba(255, 255, 255, .015);
          }
          #${rootId} .codex-usage-hud-rest-reminder-slot > span {
            color: var(--codex-usage-hud-request-muted, #718095);
            font-size: 10px;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-rest-reminder-range {
            display: inline-flex;
            align-items: center;
            justify-content: end;
            gap: 3px;
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-rest-reminder-slot input[type="time"] {
            width: 62px;
            box-sizing: border-box;
            min-height: 22px;
            border: 0;
            background: transparent;
            color: var(--codex-usage-hud-text, #dde7f2);
            padding: 0;
            text-align: center;
            font-size: 11px;
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-rest-reminder-slot input[type="time"]:focus {
            outline: none;
            box-shadow: none;
          }
          #${rootId} .codex-usage-hud-rest-reminder-slot:focus-within {
            border-color: color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 55%, var(--codex-usage-hud-panel-border, #2e3846));
          }
          #${rootId} .codex-usage-hud-rest-reminder-dash {
            color: var(--codex-usage-hud-request-muted, #718095);
            font-size: 10px;
          }
          #${rootId} .codex-usage-hud-rest-reminder-check {
            display: inline-flex;
            align-items: center;
            margin-left: 2px;
          }
          #${rootId} .codex-usage-hud-rest-reminder-check input {
            width: 13px;
            height: 13px;
            margin: 0;
            accent-color: var(--codex-usage-hud-success, #8fe3a1);
          }
          #${rootId} .codex-usage-hud-rest-reminder-foot {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto auto;
            gap: 6px;
            align-items: center;
          }
          #${rootId} .codex-usage-hud-rest-reminder-summary {
            min-width: 0;
            color: var(--codex-usage-hud-request-muted, #718095);
            font-size: 10px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-rest-reminder-foot .codex-usage-hud-settings-action {
            min-height: 26px;
            min-width: 52px;
            padding: 0 9px;
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-rest-mask {
            position: fixed;
            inset: 0;
            /* Above settings modal so the test preview can be seen while settings stay open. */
            z-index: 2147483600;
            display: none;
            overflow: hidden;
            /* Root is pointer-events:none; mask must capture clicks across Codex. */
            pointer-events: auto;
            cursor: default;
            background:
              radial-gradient(ellipse 70% 50% at 50% 38%, rgba(243, 210, 122, 0.10), transparent 58%),
              radial-gradient(ellipse 55% 40% at 18% 78%, rgba(115, 213, 160, 0.07), transparent 55%),
              radial-gradient(ellipse 50% 45% at 84% 18%, rgba(156, 203, 255, 0.06), transparent 50%),
              linear-gradient(165deg, rgba(7, 11, 18, 0.78) 0%, rgba(10, 14, 22, 0.86) 48%, rgba(6, 9, 14, 0.92) 100%);
            backdrop-filter: blur(8px) saturate(1.05);
            -webkit-backdrop-filter: blur(8px) saturate(1.05);
            opacity: 0;
            transition: opacity 220ms ease;
          }
          #${rootId} .codex-usage-hud-rest-mask::before,
          #${rootId} .codex-usage-hud-rest-mask::after {
            content: "";
            position: absolute;
            border-radius: 50%;
            pointer-events: none;
            filter: blur(2px);
          }
          #${rootId} .codex-usage-hud-rest-mask::before {
            width: min(42vw, 380px);
            height: min(42vw, 380px);
            left: 50%;
            top: 34%;
            transform: translate(-50%, -50%);
            background: radial-gradient(circle, rgba(243, 210, 122, 0.16) 0%, rgba(243, 210, 122, 0.04) 42%, transparent 70%);
            animation: codex-usage-hud-rest-glow 5.6s ease-in-out infinite;
          }
          #${rootId} .codex-usage-hud-rest-mask::after {
            inset: 0;
            border-radius: 0;
            background:
              linear-gradient(to bottom, rgba(255,255,255,0.035), transparent 18%, transparent 82%, rgba(0,0,0,0.18)),
              radial-gradient(ellipse at center, transparent 42%, rgba(0, 0, 0, 0.28) 100%);
          }
          #${rootId} .codex-usage-hud-rest-mask[data-visible="true"] {
            display: block;
            opacity: 1;
          }
          @keyframes codex-usage-hud-rest-glow {
            0%, 100% { opacity: 0.72; transform: translate(-50%, -50%) scale(1); }
            50% { opacity: 1; transform: translate(-50%, -50%) scale(1.08); }
          }
          @keyframes codex-usage-hud-rest-card-in {
            from {
              opacity: 0;
              transform: translate(-50%, calc(-50% + 14px)) scale(0.97);
            }
            to {
              opacity: 1;
              transform: translate(-50%, -50%) scale(1);
            }
          }
          #${rootId} .codex-usage-hud-rest-toast {
            position: fixed;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            z-index: 2147483610;
            width: min(440px, calc(100vw - 36px));
            padding: 0;
            border-radius: 20px;
            border: 1px solid rgba(243, 210, 122, 0.22);
            background:
              linear-gradient(160deg, rgba(36, 44, 56, 0.98) 0%, rgba(18, 24, 34, 0.98) 55%, rgba(14, 19, 28, 0.99) 100%);
            color: var(--codex-usage-hud-text, #dce7f2);
            box-shadow:
              0 0 0 1px rgba(255, 255, 255, 0.03) inset,
              0 1px 0 rgba(255, 255, 255, 0.06) inset,
              0 28px 80px rgba(0, 0, 0, 0.55),
              0 0 60px rgba(243, 210, 122, 0.08);
            display: none;
            gap: 0;
            overflow: hidden;
            /* Root is pointer-events:none; toast must re-enable hit testing. */
            pointer-events: auto;
          }
          #${rootId} .codex-usage-hud-rest-toast[data-visible="true"] {
            display: grid;
            animation: codex-usage-hud-rest-card-in 280ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
          }
          #${rootId} .codex-usage-hud-rest-toast-accent {
            height: 3px;
            background: linear-gradient(90deg, transparent 0%, #f3d27a 22%, #b5dd92 55%, #9ccbff 78%, transparent 100%);
            opacity: 0.95;
          }
          #${rootId} .codex-usage-hud-rest-toast-body {
            display: grid;
            gap: 14px;
            padding: 22px 22px 18px;
          }
          #${rootId} .codex-usage-hud-rest-toast-head {
            display: grid;
            grid-template-columns: auto minmax(0, 1fr);
            gap: 14px;
            align-items: start;
          }
          #${rootId} .codex-usage-hud-rest-toast-icon {
            display: grid;
            place-items: center;
            width: 48px;
            height: 48px;
            border-radius: 16px;
            border: 1px solid rgba(243, 210, 122, 0.28);
            background:
              radial-gradient(circle at 30% 25%, rgba(255, 245, 210, 0.22), transparent 55%),
              linear-gradient(145deg, rgba(243, 210, 122, 0.22), rgba(243, 210, 122, 0.06));
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.08);
            font-size: 22px;
            line-height: 1;
          }
          #${rootId} .codex-usage-hud-rest-toast-kicker {
            margin: 0 0 4px;
            color: rgba(243, 210, 122, 0.86);
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0;
            text-transform: uppercase;
          }
          #${rootId} .codex-usage-hud-rest-toast-title {
            margin: 0;
            color: #f6f0df;
            font-size: 18px;
            font-weight: 750;
            line-height: 1.3;
            letter-spacing: 0;
          }
          #${rootId} .codex-usage-hud-rest-toast-title-row {
            display: flex;
            align-items: baseline;
            flex-wrap: wrap;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-rest-prompt-elapsed {
            color: rgba(243, 210, 122, 0.82);
            font-size: 11px;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-rest-toast-message {
            margin: 0;
            color: rgba(220, 231, 242, 0.92);
            font-size: 14px;
            line-height: 1.65;
          }
          #${rootId} .codex-usage-hud-rest-toast-hint {
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 0;
            padding: 10px 12px;
            border-radius: 12px;
            border: 1px solid rgba(156, 203, 255, 0.12);
            background: rgba(156, 203, 255, 0.06);
            color: rgba(169, 188, 210, 0.95);
            font-size: 12px;
            line-height: 1.45;
            min-height: 18px;
            font-variant-numeric: tabular-nums;
          }
          #${rootId} .codex-usage-hud-rest-toast-hint-dot {
            flex: 0 0 auto;
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #73d5a0;
            box-shadow: 0 0 0 4px rgba(115, 213, 160, 0.14);
          }
          #${rootId} .codex-usage-hud-rest-toast-actions {
            display: flex;
            justify-content: flex-end;
            flex-wrap: wrap;
            gap: 10px;
            padding: 0 22px 20px;
          }
          #${rootId} .codex-usage-hud-rest-toast-early-actions,
          #${rootId} .codex-usage-hud-rest-bubble-early-actions {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 7px;
            color: rgba(169, 188, 210, 0.95);
            font-size: 12px;
          }
          #${rootId} .codex-usage-hud-rest-toast-early-actions {
            padding: 0 22px 10px;
          }
          #${rootId} .codex-usage-hud-rest-bubble-early-actions {
            padding-top: 2px;
          }
          #${rootId} .codex-usage-hud-rest-toast-early-actions button,
          #${rootId} .codex-usage-hud-rest-bubble-early-actions button {
            min-height: 28px;
            min-width: 48px;
            padding: 0 9px;
            border-radius: 8px;
            border: 1px solid rgba(156, 203, 255, 0.24);
            background: rgba(156, 203, 255, 0.08);
            color: var(--codex-usage-hud-text, #dce7f2);
            cursor: pointer;
            font-size: 11px;
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-rest-toast-early-actions button:hover,
          #${rootId} .codex-usage-hud-rest-bubble-early-actions button:hover {
            border-color: rgba(243, 210, 122, 0.5);
            background: rgba(243, 210, 122, 0.12);
          }
          #${rootId} .codex-usage-hud-rest-credit-custom {
            display: inline-flex;
            align-items: center;
            gap: 6px;
          }
          #${rootId} .codex-usage-hud-rest-credit-custom input[type="number"] {
            width: 64px;
            min-height: 28px;
            padding: 0 6px;
            border-radius: 8px;
            border: 1px solid rgba(156, 203, 255, 0.24);
            background: rgba(7, 11, 18, 0.55);
            color: var(--codex-usage-hud-text, #dce7f2);
            font-size: 12px;
          }
          #${rootId} .codex-usage-hud-rest-credit-custom button {
            min-width: 48px;
            min-height: 28px;
            padding: 0 9px;
            border-radius: 8px;
            border: 1px solid rgba(156, 203, 255, 0.24);
            background: rgba(156, 203, 255, 0.08);
            color: var(--codex-usage-hud-text, #dce7f2);
            cursor: pointer;
            font-size: 11px;
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-rest-credit-custom button:hover {
            border-color: rgba(243, 210, 122, 0.5);
            background: rgba(243, 210, 122, 0.12);
          }
          #${rootId} .codex-usage-hud-rest-toast button {
            pointer-events: auto;
            cursor: pointer;
            min-height: 38px;
            min-width: 108px;
            padding: 0 16px;
            border-radius: 11px;
            font-size: 13px;
            font-weight: 650;
            transition: border-color 140ms ease, background 140ms ease, transform 140ms ease, box-shadow 140ms ease;
          }
          #${rootId} .codex-usage-hud-rest-toast button:hover {
            transform: translateY(-1px);
          }
          #${rootId} .codex-usage-hud-rest-toast button:active {
            transform: translateY(0);
          }
          #${rootId} .codex-usage-hud-rest-toast button[data-primary="true"] {
            border-color: transparent;
            background: linear-gradient(180deg, #f8df95 0%, #f3d27a 100%);
            color: #1a1408;
            box-shadow: 0 8px 18px rgba(243, 210, 122, 0.22);
          }
          #${rootId} .codex-usage-hud-rest-toast button[data-primary="true"]:hover {
            box-shadow: 0 10px 22px rgba(243, 210, 122, 0.3);
          }
          #${rootId} .codex-usage-hud-rest-toast button:not([data-primary="true"]) {
            border-color: rgba(90, 106, 124, 0.85);
            background: rgba(30, 40, 52, 0.92);
            color: #dce7f2;
          }
          #${rootId} .codex-usage-hud-rest-toast button:not([data-primary="true"]):hover {
            border-color: rgba(243, 210, 122, 0.45);
            background: rgba(38, 50, 64, 0.96);
          }
          #${rootId} .codex-usage-hud-rest-bubble {
            position: fixed;
            z-index: 2147483550;
            display: none;
            box-sizing: border-box;
            width: min(430px, calc(100vw - 16px));
            padding: 12px 14px;
            border: 1px solid rgba(243, 210, 122, 0.28);
            border-radius: 12px;
            background: linear-gradient(155deg, rgba(28, 36, 47, 0.98), rgba(14, 20, 29, 0.99));
            color: var(--codex-usage-hud-text, #dce7f2);
            box-shadow: 0 16px 42px rgba(0, 0, 0, 0.38), inset 0 1px 0 rgba(255, 255, 255, 0.04);
            pointer-events: auto;
            -webkit-app-region: no-drag;
          }
          #${rootId} .codex-usage-hud-rest-bubble[data-visible="true"][data-positioned="true"] {
            display: grid;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-rest-bubble-head {
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--codex-usage-hud-warning, #ffb86b);
            font-size: 13px;
            font-weight: 750;
          }
          #${rootId} .codex-usage-hud-rest-bubble-head .codex-usage-hud-rest-prompt-elapsed {
            margin-left: auto;
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-rest-bubble-detail {
            color: var(--codex-usage-hud-request-text, #b8c6d8);
            font-size: 12px;
            line-height: 1.45;
          }
          #${rootId} .codex-usage-hud-rest-bubble-foot {
            display: flex;
            align-items: center;
            gap: 8px;
            min-width: 0;
          }
          #${rootId} .codex-usage-hud-rest-bubble-status {
            flex: 1 1 auto;
            min-width: 0;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 11px;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          #${rootId} .codex-usage-hud-rest-bubble button {
            flex: 0 0 auto;
            min-height: 28px;
            padding: 0 10px;
            border-radius: 8px;
            border: 1px solid rgba(243, 210, 122, 0.34);
            background: rgba(243, 210, 122, 0.10);
            color: var(--codex-usage-hud-text, #dce7f2);
            cursor: pointer;
            font-size: 11px;
            font-weight: 650;
          }
          #${rootId} .codex-usage-hud-rest-bubble button[data-primary="true"] {
            border-color: transparent;
            background: var(--codex-usage-hud-warning, #ffb86b);
            color: #1a1408;
          }
          #${rootId} .codex-usage-hud-support a {
            color: var(--codex-usage-hud-info, #9ccbff);
          }
          #${rootId} .codex-usage-hud-support-note {
            color: var(--codex-usage-hud-request-muted, #a9bcd2);
            font-size: 12px;
          }
          #${rootId} .codex-usage-hud-support-qr-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
            align-items: start;
          }
          #${rootId} .codex-usage-hud-support-qr {
            min-width: 0;
            display: grid;
            gap: 8px;
            justify-items: center;
            padding: 10px;
            border: 1px solid var(--codex-usage-hud-divider, #273241);
            border-radius: 8px;
            background: var(--codex-usage-hud-panel-surface, #141b24);
          }
          #${rootId} .codex-usage-hud-support-qr-title {
            width: 100%;
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 8px;
            color: var(--codex-usage-hud-text, #e8eef7);
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-support-qr-title span:last-child {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 10px;
            font-weight: 600;
          }
          #${rootId} .codex-usage-hud-support-qr img {
            display: block;
            width: 100%;
            max-width: 260px;
            max-height: 360px;
            object-fit: contain;
            border-radius: 6px;
            background: #ffffff;
          }
          #${rootId}[data-theme-variant="light"] {
            color-scheme: light;
          }
          #${rootId}[data-theme-variant="dark"] {
            color-scheme: dark;
          }
          #${rootId} .codex-usage-hud-panel {
            border-color: var(--codex-usage-hud-panel-border);
            color: var(--codex-usage-hud-text);
          }
          #${rootId} .${topClass} {
            background: var(--codex-usage-hud-surface);
          }
          #${rootId} .${requestClass} {
            background: var(--codex-usage-hud-request-surface);
          }
          #${rootId} .codex-usage-hud-handle,
          #${rootId} .codex-usage-hud-update-button,
          #${rootId} .codex-usage-hud-settings-button {
            background: var(--codex-usage-hud-header-surface);
          }
          #${rootId} .codex-usage-hud-handle {
            color: var(--codex-usage-hud-muted);
          }
          #${rootId} .codex-usage-hud-update-button {
            color: var(--codex-usage-hud-info);
          }
          #${rootId} .codex-usage-hud-update-button[data-state="paused"],
          #${rootId} .codex-usage-hud-update-button[data-state="error"],
          #${rootId} .codex-usage-hud-update-button[data-icon="install"] {
            color: var(--codex-usage-hud-warning);
          }
          #${rootId} .codex-usage-hud-settings-button {
            color: var(--codex-usage-hud-muted);
          }
          #${rootId} .codex-usage-hud-handle:hover,
          #${rootId} .codex-usage-hud-update-button:hover,
          #${rootId} .codex-usage-hud-settings-button:hover {
            background: var(--codex-usage-hud-panel-border);
            color: var(--codex-usage-hud-accent);
          }
          #${rootId} .codex-usage-hud-progress-rail {
            border-color: var(--codex-usage-hud-progress-track-border);
            background: linear-gradient(180deg, rgba(255,255,255,.032), rgba(255,255,255,0)), var(--codex-usage-hud-progress-track);
            isolation: isolate;
          }
          #${rootId} .codex-usage-hud-progress-rail[data-tone="session"] .codex-usage-hud-progress-fill,
          #${rootId} .codex-usage-hud-progress-rail[data-tone="cache"] .codex-usage-hud-progress-fill {
            background: linear-gradient(90deg, var(--codex-usage-hud-progress-cache), var(--codex-usage-hud-progress-cache-end));
          }
          #${rootId} .codex-usage-hud-progress-rail[data-tone="day"] .codex-usage-hud-progress-fill {
            background: linear-gradient(90deg, var(--codex-usage-hud-progress-day), var(--codex-usage-hud-progress-day-end));
          }
          #${rootId} .codex-usage-hud-progress-rail[data-tone="week"] .codex-usage-hud-progress-fill {
            background: linear-gradient(90deg, var(--codex-usage-hud-progress-week), var(--codex-usage-hud-progress-week-end));
          }
          #${rootId} .codex-usage-hud-progress-rail[data-tone="error"] .codex-usage-hud-progress-fill {
            background: linear-gradient(90deg, var(--codex-usage-hud-progress-overflow), var(--codex-usage-hud-error));
          }
          #${rootId} .codex-usage-hud-progress-rail[data-overflow="true"] {
            border-color: var(--codex-usage-hud-progress-overflow-badge-edge);
          }
          #${rootId} .codex-usage-hud-progress-overflow {
            background: linear-gradient(90deg, var(--codex-usage-hud-progress-overflow-highlight), var(--codex-usage-hud-progress-overflow) 60%, var(--codex-usage-hud-error));
          }
          #${rootId} .codex-usage-hud-progress-overflow-anchor {
            background: radial-gradient(circle at 35% 35%, var(--codex-usage-hud-progress-overflow-highlight) 0%, var(--codex-usage-hud-progress-overflow) 58%, var(--codex-usage-hud-error) 100%);
            box-shadow: 0 0 0 1px rgba(255,255,255,.06), 0 0 8px rgba(0,0,0,.18);
          }
          #${rootId} .codex-usage-hud-progress-badge {
            border-color: var(--codex-usage-hud-progress-overflow-badge-edge);
            background: var(--codex-usage-hud-progress-overflow-badge);
            color: var(--codex-usage-hud-progress-overflow-badge-text);
          }
          #${rootId} .codex-usage-hud-panel-header,
          #${rootId} .codex-usage-hud-settings-tab.is-active,
          #${rootId} .codex-usage-hud-settings-loading-kicker {
            background: var(--codex-usage-hud-header-surface);
          }
          #${rootId} .codex-usage-hud-top-body {
            scrollbar-color: var(--codex-usage-hud-divider) var(--codex-usage-hud-surface);
          }
          #${rootId} .codex-usage-hud-top-card,
          #${rootId} .codex-usage-hud-settings-modal-shell,
          #${rootId} .codex-usage-hud-settings-shell {
            background: var(--codex-usage-hud-panel-surface);
          }
          #${rootId} .codex-usage-hud-top-card,
          #${rootId} .codex-usage-hud-alert,
          #${rootId} .codex-usage-hud-activity-main,
          #${rootId} .codex-usage-hud-activity-step,
          #${rootId} .codex-usage-hud-activity-metric,
          #${rootId} .codex-usage-hud-session-insight,
          #${rootId} .codex-usage-hud-token-chip,
          #${rootId} .codex-usage-hud-heavy-round,
          #${rootId} .codex-usage-hud-chip,
          #${rootId} .codex-usage-hud-settings-modal-shell,
          #${rootId} .codex-usage-hud-settings-shell,
          #${rootId} .codex-usage-hud-input,
          #${rootId} .codex-usage-hud-select,
          #${rootId} .codex-usage-hud-textarea {
            border-color: var(--codex-usage-hud-divider);
          }
          #${rootId} .codex-usage-hud-activity-main,
          #${rootId} .codex-usage-hud-activity-step,
          #${rootId} .codex-usage-hud-activity-metric,
          #${rootId} .codex-usage-hud-session-insight,
          #${rootId} .codex-usage-hud-heavy-round,
          #${rootId} .codex-usage-hud-token-chip {
            background: var(--codex-usage-hud-request-panel-surface);
          }
          #${rootId} .codex-usage-hud-card-title,
          #${rootId} .codex-usage-hud-title,
          #${rootId} .codex-usage-hud-stat-value,
          #${rootId} .codex-usage-hud-activity-node-title,
          #${rootId} .codex-usage-hud-value,
          #${rootId} .codex-usage-hud-token-chip,
          #${rootId} .codex-usage-hud-support,
          #${rootId} .codex-usage-hud-support-qr-title {
            color: var(--codex-usage-hud-text);
          }
          #${rootId} .codex-usage-hud-card-title {
            color: var(--codex-usage-hud-request-text);
          }
          #${rootId} .codex-usage-hud-chip {
            background: var(--codex-usage-hud-header-surface);
            color: var(--codex-usage-hud-text);
          }
          #${rootId} .codex-usage-hud-chip[data-tone="warning"] {
            border-color: var(--codex-usage-hud-warning);
            background: color-mix(in srgb, var(--codex-usage-hud-warning) 14%, var(--codex-usage-hud-panel-surface));
            color: var(--codex-usage-hud-warning);
          }
          #${rootId} .codex-usage-hud-copy-chip[data-copyable="true"]:hover,
          #${rootId} .codex-usage-hud-heavy-round[data-copyable="true"]:hover,
          #${rootId} .codex-usage-hud-value[data-copyable="true"]:hover {
            border-color: var(--codex-usage-hud-accent);
            background: color-mix(in srgb, var(--codex-usage-hud-accent) 10%, var(--codex-usage-hud-panel-surface));
            color: var(--codex-usage-hud-accent);
          }
          #${rootId} .codex-usage-hud-stat-value.info,
          #${rootId} .codex-usage-hud-value.blue,
          #${rootId} .codex-usage-hud-activity-main strong,
          #${rootId} .codex-usage-hud-activity-load-more {
            color: var(--codex-usage-hud-info);
          }
          #${rootId} .codex-usage-hud-stat-value.cache,
          #${rootId} .codex-usage-hud-value[data-copied="true"] {
            color: var(--codex-usage-hud-success) !important;
          }
          #${rootId} .codex-usage-hud-stat-label,
          #${rootId} .codex-usage-hud-section-title,
          #${rootId} .codex-usage-hud-token-chip span:first-child,
          #${rootId} .codex-usage-hud-heavy-round-detail,
          #${rootId} .codex-usage-hud-activity-node-time,
          #${rootId} .codex-usage-hud-activity-node-detail,
          #${rootId} .codex-usage-hud-session-composition,
          #${rootId} .codex-usage-hud-value.muted {
            color: var(--codex-usage-hud-muted);
          }
          #${rootId} .codex-usage-hud-session-composition {
            color: var(--codex-usage-hud-request-muted);
          }
          #${rootId} .codex-usage-hud-heavy-round-title,
          #${rootId} .codex-usage-hud-activity-metric .codex-usage-hud-value,
          #${rootId} .codex-usage-hud-value.accent,
          #${rootId} .codex-usage-hud-value[data-copyable="true"][data-copy-field="slow"] {
            color: var(--codex-usage-hud-accent);
          }
          #${rootId} .codex-usage-hud-value[data-copyable="true"][data-copy-field="gap"] {
            color: var(--codex-usage-hud-info);
          }
          #${rootId} .codex-usage-hud-value.warn,
          #${rootId} .codex-usage-hud-alert-title,
          #${rootId} .codex-usage-hud-alert-close {
            color: var(--codex-usage-hud-warning);
          }
          #${rootId} .codex-usage-hud-alert {
            border-color: var(--codex-usage-hud-warning);
            background: color-mix(in srgb, var(--codex-usage-hud-warning) 13%, var(--codex-usage-hud-panel-surface));
          }
          #${rootId} .codex-usage-hud-alert-dot {
            background: var(--codex-usage-hud-warning);
          }
          #${rootId} .codex-usage-hud-alert-close:hover {
            background: color-mix(in srgb, var(--codex-usage-hud-warning) 12%, transparent);
          }
          #${rootId} .codex-usage-hud-activity-timeline {
            scrollbar-color: var(--codex-usage-hud-divider) var(--codex-usage-hud-request-panel-surface);
          }
          #${rootId} .codex-usage-hud-activity-load-more {
            border-color: var(--codex-usage-hud-divider);
            background: color-mix(in srgb, var(--codex-usage-hud-info) 8%, var(--codex-usage-hud-panel-surface));
          }
          #${rootId} .codex-usage-hud-activity-load-more:hover {
            border-color: var(--codex-usage-hud-info);
            background: color-mix(in srgb, var(--codex-usage-hud-info) 12%, var(--codex-usage-hud-panel-surface));
          }
          #${rootId} .codex-usage-hud-activity-load-more:disabled:hover {
            border-color: var(--codex-usage-hud-divider);
            background: color-mix(in srgb, var(--codex-usage-hud-info) 8%, var(--codex-usage-hud-panel-surface));
          }
          #${rootId} .codex-usage-hud-activity-node-dot {
            background: var(--codex-usage-hud-info);
            box-shadow: 0 0 0 3px color-mix(in srgb, var(--codex-usage-hud-info) 14%, transparent);
          }
          #${rootId} .codex-usage-hud-activity-node:not(:last-child)::after {
            background: var(--codex-usage-hud-divider);
          }
          #${rootId} .codex-usage-hud-activity-node[data-active="true"] .codex-usage-hud-activity-node-dot {
            background: var(--codex-usage-hud-accent);
            box-shadow: 0 0 0 3px color-mix(in srgb, var(--codex-usage-hud-accent) 16%, transparent);
          }
          #${rootId} .codex-usage-hud-activity-node[data-locate-highlight="true"] {
            border-radius: 5px;
            background: color-mix(in srgb, var(--codex-usage-hud-accent) 12%, transparent);
            box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--codex-usage-hud-accent) 48%, transparent);
          }
          #${rootId} .codex-usage-hud-activity-node[data-locate-highlight="true"] .codex-usage-hud-activity-node-dot {
            background: var(--codex-usage-hud-accent);
            box-shadow: 0 0 0 4px color-mix(in srgb, var(--codex-usage-hud-accent) 24%, transparent);
          }
          #${rootId} .codex-usage-hud-divider {
            background: var(--codex-usage-hud-divider);
          }
          #${rootId} .codex-usage-hud-request-list {
            background: var(--codex-usage-hud-request-panel-surface);
            scrollbar-color: var(--codex-usage-hud-divider) var(--codex-usage-hud-request-panel-surface);
          }
          #${rootId} .codex-usage-hud-row {
            color: var(--codex-usage-hud-request-text);
          }
          #${rootId} .codex-usage-hud-row[data-latest="true"] {
            color: var(--codex-usage-hud-accent);
          }
          #${rootId} .codex-usage-hud-row-time,
          #${rootId} .codex-usage-hud-session-meta,
          #${rootId} .codex-usage-hud-support-qr-title span:last-child {
            color: var(--codex-usage-hud-request-muted);
          }
          #${rootId} .codex-usage-hud-warning,
          #${rootId} .codex-usage-hud-line-warning,
          #${rootId} [data-field="topWarnings"] {
            color: var(--codex-usage-hud-warning) !important;
          }
          #${rootId} .codex-usage-hud-error {
            color: var(--codex-usage-hud-error) !important;
          }
          #${rootId} .codex-usage-hud-line-accent,
          #${rootId} .codex-usage-hud-support-qr-title span:first-child {
            color: var(--codex-usage-hud-accent);
          }
          #${rootId} .codex-usage-hud-line-info {
            color: var(--codex-usage-hud-info);
          }
          #${rootId} .codex-usage-hud-line-muted,
          #${rootId} .codex-usage-hud-label,
          #${rootId} .codex-usage-hud-field-caption {
            color: var(--codex-usage-hud-muted);
          }
          @container (max-width: 560px) {
            #${rootId} .codex-usage-hud-top-grid {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-top-column {
              height: auto;
            }
            #${rootId} .codex-usage-hud-top-column-left,
            #${rootId} .codex-usage-hud-top-column-right {
              grid-template-rows: auto;
            }
            #${rootId} .codex-usage-hud-activity-card {
              min-height: auto;
            }
          }
          @container (max-width: 440px) {
            #${rootId} .codex-usage-hud-session-stats {
              grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-session-stats .codex-usage-hud-chip {
              display: none;
            }
            #${rootId} .codex-usage-hud-token-breakdown {
              display: none;
            }
            #${rootId} .codex-usage-hud-session-insight {
              grid-template-columns: auto minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-session-insight .codex-usage-hud-value:last-child,
            #${rootId} .codex-usage-hud-card-actions .codex-usage-hud-copy-chip {
              display: none;
            }
            #${rootId} .codex-usage-hud-activity-metrics {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-activity-metrics .codex-usage-hud-activity-metric:last-child {
              display: none;
            }
            #${rootId} .codex-usage-hud-activity-task-nav {
              gap: 3px;
            }
            #${rootId} .codex-usage-hud-activity-task-button {
              flex-basis: 20px;
              width: 20px;
              height: 20px;
            }
            #${rootId} .codex-usage-hud-activity-task-index {
              min-width: 48px;
              padding-left: 5px;
              padding-right: 5px;
            }
          }
          @media (max-width: 760px) {
            #${rootId} .codex-usage-hud-session-meta {
              display: none;
            }
            #${rootId} .codex-usage-hud-cache-pill {
              display: none;
            }
            #${rootId} .${topClass} .codex-usage-hud-panel-header {
              grid-template-columns: auto minmax(0, 1fr) 22px;
            }
            #${rootId} .codex-usage-hud-settings-grid {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-codex-cli-grid {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-codex-cli-wide {
              grid-column: 1;
            }
            #${rootId} .codex-usage-hud-codex-cli-layer {
              padding: 8px;
            }
            #${rootId} .codex-usage-hud-codex-cli-dialog {
              padding: 12px;
            }
            #${rootId} .codex-usage-hud-settings-compact-row {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-settings-compact-inputs {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-rest-reminder-grid {
              grid-template-columns: repeat(3, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-rest-reminder-schedule {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-provider-editor {
              grid-column: 1;
            }
            #${rootId} .codex-usage-hud-provider-editor-head {
              grid-template-columns: auto minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-provider-navigation {
              grid-column: 1 / -1;
              grid-row: 2;
            }
            #${rootId} .codex-usage-hud-provider-editor-head .codex-usage-hud-price-unit {
              display: none;
            }
            #${rootId} .codex-usage-hud-price-actions {
              grid-template-columns: auto minmax(0, 1fr) auto;
            }
            #${rootId} .codex-usage-hud-pricing-tools {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-pricing-preview-grid {
              grid-template-columns: repeat(3, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-provider-context {
              align-items: center;
              flex-wrap: wrap;
            }
            #${rootId} .codex-usage-hud-provider-scope-options {
              flex-wrap: wrap;
            }
            #${rootId} .codex-usage-hud-provider-meta {
              max-width: 58%;
            }
            #${rootId} .codex-usage-hud-support-qr-grid {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="storage"] {
              width: min(980px, calc(100vw - 48px));
              height: min(572px, calc(100vh - 48px));
            }
            #${rootId} .codex-usage-hud-session-tools {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-session-tools-primary {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-session-search,
            #${rootId} .codex-usage-hud-session-index-workdir,
            #${rootId} .codex-usage-hud-session-date-filter {
              grid-column: auto;
              width: 100%;
            }
            #${rootId} .codex-usage-hud-session-index-workdir {
              flex-direction: column;
              align-items: stretch;
            }
            #${rootId} .codex-usage-hud-session-index-toggle,
            #${rootId} .codex-usage-hud-session-workdir-filter {
              width: 100%;
            }
            #${rootId} .codex-usage-hud-session-index-head {
              grid-template-columns: auto minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-session-index-metrics {
              grid-column: 1 / -1;
              justify-content: flex-start;
            }
            #${rootId} .codex-usage-hud-session-index-metric {
              text-align: left;
            }
            #${rootId} .codex-usage-hud-session-index-actions {
              flex-direction: column;
              align-items: stretch;
            }
            #${rootId} .codex-usage-hud-session-index-actions-secondary {
              margin-left: 0;
              justify-content: space-between;
            }
            #${rootId} .codex-usage-hud-session-filters { width: 100%; }
            #${rootId} .codex-usage-hud-session-head,
            #${rootId} .codex-usage-hud-session-row {
              grid-template-columns: 28px minmax(0, 1.2fr) minmax(0, .9fr) 70px 68px;
              grid-template-areas:
                "check title title status size"
                "check workdir time status size";
            }
            #${rootId} .codex-usage-hud-session-row {
              min-height: 54px;
              padding-top: 8px;
              padding-bottom: 8px;
            }
            #${rootId} .codex-usage-hud-session-head > :nth-child(3),
            #${rootId} .codex-usage-hud-session-head > :nth-child(4) {
              display: none;
            }
            #${rootId} .codex-usage-hud-insights-summary {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-insights-metric:nth-child(2) { border-right: 0; }
            #${rootId} .codex-usage-hud-insights-metric:nth-child(-n + 2) { border-bottom: 1px solid var(--codex-usage-hud-divider, #273241); }
            #${rootId} .codex-usage-hud-insights-rankings {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-insights-ranking {
              border-right: 0;
              border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
            }
            #${rootId} .codex-usage-hud-insights-ranking:last-child { border-bottom: 0; }
            #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="backgroundUsage"] {
              height: calc(100vh - 24px);
            }
            #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="backgroundUsage"] .codex-usage-hud-settings-body {
              overflow: auto;
            }
            #${rootId} .codex-usage-hud-background {
              display: block;
              height: auto;
              min-height: 100%;
            }
            #${rootId} .codex-usage-hud-background-metrics {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-background-toolbar {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-background-range {
              grid-column: 1 / -1;
              border-right: 0;
              padding-right: 0;
            }
            #${rootId} .codex-usage-hud-background-master-detail {
              display: block;
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-background-history,
            #${rootId} .codex-usage-hud-background-detail {
              overflow: visible;
            }
            #${rootId} .codex-usage-hud-background-history {
              border-right: 0;
              border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
            }
            #${rootId} .codex-usage-hud-background-request {
              grid-template-columns: 70px minmax(92px, 1fr) 56px 78px;
            }
            #${rootId} .codex-usage-hud-background-request > span:nth-child(3),
            #${rootId} .codex-usage-hud-background-request-index {
              display: none;
            }
          }
          @media (max-width: 520px) {
            #${rootId} .codex-usage-hud-settings-tabs {
              overflow-x: auto;
            }
            #${rootId} .codex-usage-hud-settings-tab {
              flex: 0 0 auto;
            }
            #${rootId} .codex-usage-hud-rest-reminder-grid {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-settings-actions {
              align-items: flex-start;
              flex-wrap: wrap;
            }
            #${rootId} .codex-usage-hud-settings-actions > div:last-child {
              display: flex;
              width: 100%;
              gap: 6px;
            }
            #${rootId} .codex-usage-hud-settings-actions > div:last-child .codex-usage-hud-settings-action {
              min-width: 0;
              flex: 1 1 0;
            }
            #${rootId} .codex-usage-hud-price-actions {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-pricing-tools,
            #${rootId} .codex-usage-hud-pricing-preview-grid {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-pricing-version-item {
              grid-template-columns: minmax(0, 1fr) auto;
            }
            #${rootId} .codex-usage-hud-pricing-version-item time {
              grid-column: 1 / -1;
            }
            #${rootId} .codex-usage-hud-provider-context {
              display: grid;
            }
            #${rootId} .codex-usage-hud-provider-context-adjustment {
              width: 100%;
              flex: 1 1 auto;
            }
            #${rootId} .codex-usage-hud-provider-meta {
              max-width: none;
              text-align: left;
            }
            #${rootId} .codex-usage-hud-insights-toolbar,
            #${rootId} .codex-usage-hud-insights-background,
            #${rootId} .codex-usage-hud-cleanup-preview-head {
              align-items: flex-start;
              flex-direction: column;
            }
            #${rootId} .codex-usage-hud-insights-range { width: 100%; }
            #${rootId} .codex-usage-hud-cleanup-page-head {
              align-items: flex-start;
              flex-direction: column;
            }
            #${rootId} .codex-usage-hud-cleanup-row {
              grid-template-columns: 24px minmax(0, 1fr) 64px 24px;
            }
            #${rootId} .codex-usage-hud-cleanup-row-impact { grid-column: 2 / -1; }
            #${rootId} .codex-usage-hud-cleanup-details {
              margin-left: 14px;
              padding-right: 14px;
            }
            #${rootId} .codex-usage-hud-cleanup-protected-note > button {
              align-items: flex-start;
              flex-direction: column;
              gap: 4px;
            }
            #${rootId} .codex-usage-hud-cleanup-backup {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-cleanup-footer {
              align-items: flex-start;
              flex-direction: column;
            }
            #${rootId} .codex-usage-hud-session-tools {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-session-tools-primary {
              grid-template-columns: minmax(0, 1fr);
              align-items: stretch;
            }
            #${rootId} .codex-usage-hud-session-search,
            #${rootId} .codex-usage-hud-session-index-workdir,
            #${rootId} .codex-usage-hud-session-date-filter {
              grid-column: auto;
              width: 100%;
            }
            #${rootId} .codex-usage-hud-session-index-workdir {
              flex-direction: column;
              align-items: stretch;
            }
            #${rootId} .codex-usage-hud-session-index-toggle,
            #${rootId} .codex-usage-hud-session-workdir-filter {
              width: 100%;
            }
            #${rootId} .codex-usage-hud-session-index-head {
              grid-template-columns: auto minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-session-index-metrics {
              grid-column: 1 / -1;
              justify-content: flex-start;
            }
            #${rootId} .codex-usage-hud-session-index-metric {
              text-align: left;
            }
            #${rootId} .codex-usage-hud-session-index-actions {
              flex-direction: column;
              align-items: stretch;
            }
            #${rootId} .codex-usage-hud-session-index-actions-secondary {
              margin-left: 0;
              justify-content: space-between;
            }
            #${rootId} .codex-usage-hud-session-date-popover {
              left: 0;
              right: auto;
              width: 100%;
              box-sizing: border-box;
            }
            #${rootId} .codex-usage-hud-session-filter-controls {
              width: 100%;
              display: grid;
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-session-filter-summary {
              align-items: flex-start;
              flex-direction: column;
            }
            #${rootId} .codex-usage-hud-session-filter-summary-tags {
              width: 100%;
            }
            #${rootId} .codex-usage-hud-session-head { display: none; }
            #${rootId} .codex-usage-hud-session-row {
              grid-template-columns: 28px minmax(0, 1fr) auto;
              grid-template-areas:
                "check title size"
                "check status size";
              min-height: 58px;
              padding-top: 8px;
              padding-bottom: 8px;
            }
            #${rootId} .codex-usage-hud-session-row > .codex-usage-hud-session-workdir,
            #${rootId} .codex-usage-hud-session-cell {
              display: none;
            }
            #${rootId} .codex-usage-hud-session-title span[data-secondary="true"] {
              display: block;
            }
            #${rootId} .codex-usage-hud-session-status-cell {
              justify-self: start;
            }
            #${rootId} .codex-usage-hud-session-results > div {
              align-items: flex-start;
              flex-direction: column;
              gap: 2px;
            }
            #${rootId} .codex-usage-hud-background-toolbar {
              grid-template-columns: minmax(0, 1fr);
            }
            #${rootId} .codex-usage-hud-background-range {
              grid-column: 1;
            }
            #${rootId} .codex-usage-hud-background-range button {
              min-width: 0;
              flex: 1 1 0;
              padding-inline: 6px;
            }
            #${rootId} .codex-usage-hud-background-detail-grid {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            #${rootId} .codex-usage-hud-background-detail-grid .codex-usage-hud-background-detail-wide {
              grid-column: 1 / -1;
            }
            #${rootId} .codex-usage-hud-background-request {
              grid-template-columns: minmax(92px, 1fr) 52px 74px;
            }
            #${rootId} .codex-usage-hud-background-request > span:first-child {
              display: none;
            }
          }
          #${rootId} .codex-usage-hud-search-float {
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            z-index: 2147482640;
            width: 320px;
            max-width: calc(100vw - 36px);
            max-height: 70vh;
            display: flex;
            flex-direction: column;
            box-sizing: border-box;
            gap: 8px;
            padding: 12px 12px 10px;
            border: 1px solid var(--codex-usage-hud-panel-border, #3a485a);
            border-radius: 12px;
            background: linear-gradient(150deg, rgba(28, 36, 47, .98), rgba(15, 21, 29, .98));
            box-shadow: 0 18px 44px rgba(0, 0, 0, .42), inset 0 1px 0 rgba(255, 255, 255, .06);
            color: var(--codex-usage-hud-text, #e8eef7);
            font: 12px/1.4 "Microsoft YaHei UI", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            pointer-events: auto;
            user-select: text;
            touch-action: none;
            cursor: move;
          }
          /* 自由拖拽中：JS 写入 left/top，取消居中变换 */
          #${rootId} .codex-usage-hud-search-float.is-dragging {
            transform: none !important;
            user-select: none;
          }
          /* 吸附到 Codex 正文区域左 / 右边缘 */
          #${rootId} .codex-usage-hud-search-float.side-left {
            left: 12px;
            right: auto;
            top: 50%;
            transform: translateY(-50%) !important;
          }
          #${rootId} .codex-usage-hud-search-float.side-right {
            right: 12px;
            left: auto;
            top: 50%;
            transform: translateY(-50%) !important;
          }
          /* 折叠态：仅保留侧边小药丸，点击展开 */
          #${rootId} .codex-usage-hud-search-float.is-collapsed {
            width: auto;
            max-width: none;
            max-height: none;
            flex-direction: row;
            gap: 0;
            padding: 7px 10px;
            transform: none !important;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-search-float.is-collapsed.side-left,
          #${rootId} .codex-usage-hud-search-float.is-collapsed.side-right {
            transform: translateY(-50%) !important;
          }
          #${rootId} .codex-usage-hud-search-float.is-collapsed > *:not(.codex-usage-hud-search-toggle) {
            display: none;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-toggle {
            flex: 0 0 auto;
            width: 22px;
            height: 22px;
            border: 0;
            border-radius: 6px;
            background: rgba(255, 255, 255, .08);
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 13px;
            line-height: 1;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-toggle:hover {
            background: rgba(156, 203, 255, .2);
            color: var(--codex-usage-hud-info, #9ccbff);
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-head {
            display: flex;
            align-items: flex-start;
            gap: 8px;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-query {
            flex: 1 1 auto;
            min-width: 0;
            font-weight: 700;
            color: var(--codex-usage-hud-accent, #f3d27a);
            word-break: break-word;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-close {
            flex: 0 0 auto;
            width: 22px;
            height: 22px;
            border: 0;
            border-radius: 6px;
            background: rgba(255, 255, 255, .08);
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 15px;
            line-height: 1;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-close:hover {
            background: rgba(255, 107, 107, .18);
            color: var(--codex-usage-hud-error, #ff6b6b);
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-chip {
            padding: 3px 9px;
            border: 1px solid var(--codex-usage-hud-panel-border, #3a485a);
            border-radius: 999px;
            background: rgba(156, 203, 255, .12);
            color: var(--codex-usage-hud-info, #9ccbff);
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-chip:hover {
            background: rgba(156, 203, 255, .22);
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-meta {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 11px;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-list {
            list-style: none;
            margin: 0;
            padding: 4px 0 0;
            overflow-y: auto;
            border-top: 1px solid var(--codex-usage-hud-divider, #273241);
            max-height: 38vh;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-row {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px;
            border-radius: 8px;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-row:hover {
            background: rgba(255, 255, 255, .07);
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-row.is-current {
            background: rgba(243, 210, 122, .16);
            box-shadow: inset 0 0 0 1px rgba(243, 210, 122, .4);
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-index {
            flex: 0 0 auto;
            width: 18px;
            text-align: right;
            color: var(--codex-usage-hud-muted, #8492a6);
            font-variant-numeric: tabular-nums;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-title {
            flex: 1 1 auto;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-badge {
            flex: 0 0 auto;
            padding: 1px 6px;
            border-radius: 999px;
            background: rgba(143, 227, 161, .18);
            color: var(--codex-usage-hud-success, #8fe3a1);
            font-size: 10px;
            font-weight: 700;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-nav {
            display: flex;
            gap: 6px;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-nav button {
            flex: 1 1 0;
            padding: 6px 8px;
            border: 1px solid var(--codex-usage-hud-panel-border, #3a485a);
            border-radius: 8px;
            background: rgba(255, 255, 255, .06);
            color: var(--codex-usage-hud-text, #e8eef7);
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-nav button:hover:not([disabled]) {
            background: rgba(255, 255, 255, .12);
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-nav button[disabled] {
            opacity: .4;
            cursor: default;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-return {
            color: var(--codex-usage-hud-info, #9ccbff) !important;
          }
          #${rootId} .codex-usage-hud-search-float .codex-usage-hud-search-empty {
            color: var(--codex-usage-hud-muted, #8492a6);
            font-size: 11px;
          }
        `;
        document.documentElement.appendChild(style);
      }
"""

__all__ = ["TEXT"]
