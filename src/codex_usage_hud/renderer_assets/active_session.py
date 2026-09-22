"""Renderer active-session bridge and sequence domain asset."""

TEXT = r"""
  function createActiveSessionDomain(ctx, shared) {
      function normalizeThreadId(value) {
        const text = normalize(value);
        const match = text.match(/^(?:[a-z0-9_.-]+:)(.+)$/i);
        return match ? normalize(match[1]) : text;
      }

      function activeSessionIdIsProvisional(value) {
        const text = normalize(value);
        const match = text.match(/^(?:[a-z0-9_.-]+:)(.+)$/i);
        const normalized = (match ? match[1] : text).toLowerCase();
        return normalized.startsWith("client-new-thread:");
      }

      const activeSessionIdentitySelector = [
        "[data-app-action-sidebar-thread-id]",
        "[data-session-id]",
        "a[href*='thread']",
        "a[href*='conversation']",
        "a[href*='session']",
      ].join(",");
      const activeSessionTitleSelector = [
        "[data-thread-title]",
        ".truncate.select-none",
        ".truncate.text-base",
      ].join(",");
      const activeSessionRowSelector = [
        activeSessionIdentitySelector,
        "[role='link']",
        "[role='button']",
      ].join(",");

      function activeSessionNodeOwnedByHud(node) {
        return !!node?.closest?.(`#${rootId}`);
      }

      function activeSessionFirstOutsideHud(selector) {
        return Array.from(document.querySelectorAll(selector))
          .find((node) => !activeSessionNodeOwnedByHud(node)) || null;
      }

      function activeSessionLocationId() {
        const source = `${location.pathname}${location.search}${location.hash}`;
        const match = source.match(/(?:session|conversation|thread)(?:\/|=|:|-)([A-Za-z0-9_.-]+)/i)
          || source.match(/\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:[/?#]|$)/)
          || source.match(/\/([A-Za-z0-9_-]{24,})(?:[/?#]|$)/);
        return match ? normalizeThreadId(decodeURIComponent(match[1])) : "";
      }

      function activeSessionRowHref(row) {
        return row?.getAttribute?.("href") || row?.querySelector?.("a")?.getAttribute?.("href") || "";
      }

      function activeSessionRowUrl(row) {
        const href = activeSessionRowHref(row);
        if (!href) return location.href;
        try {
          return new URL(href, location.href).href;
        } catch (_) {
          return location.href;
        }
      }

      function activeSessionIdentityRow(row) {
        if (!row) return row;
        if (row.matches?.(activeSessionIdentitySelector)) return row;
        return row.closest?.(activeSessionIdentitySelector) || row;
      }

      function activeSessionRowLooksThread(row) {
        const sourceRow = activeSessionIdentityRow(row);
        if (!sourceRow) return false;
        if (sourceRow.matches?.(activeSessionIdentitySelector)) return true;
        if (sourceRow?.querySelector?.(activeSessionTitleSelector)) return true;
        const href = activeSessionRowHref(sourceRow);
        return !!href && /(?:session|conversation|thread)/i.test(href);
      }

      function cleanActiveSessionTitle(value) {
        return normalize(String(value || "").replace(
          /\s*\d+\s*(秒|分|分钟|小时|天|周|个月|月|年|sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s*$/i,
          ""
        ));
      }

      function activeSessionTitleIsNewSession(value) {
        return /^(新对话|新会话|新聊天|new chat|new conversation|new session)$/i.test(cleanActiveSessionTitle(value));
      }

      function activeSessionHeaderElement() {
        try {
          const header = conversationHeaderElement();
          if (visible(header)) return header;
        } catch (_) {}
        const surface = document.querySelector('[data-testid="app-shell-header-context-menu-surface"]');
        const surfaceHeader = surface?.closest?.("header.app-header-tint, header, .app-header-tint");
        if (visible(surfaceHeader)) return surfaceHeader;
        return Array.from(document.querySelectorAll([
          "header.app-header-tint",
          "[data-testid='app-shell-header']",
          "[data-testid='app-shell-header-context-menu-surface']",
          ".app-header-tint",
        ].join(","))).filter(visible)[0] || null;
      }

      function activeSessionHeaderTitleIgnored(value) {
        const text = cleanActiveSessionTitle(value);
        if (!text) return true;
        return /^(File\s*Edit\s*View\s*Window\s*Help|FileEditViewWindowHelp|文件|编辑|视图|帮助|切换底部面板显示|显示\/隐藏侧边栏|chat actions|open in)$/i.test(text);
      }

      function activeSessionHeaderTitleText() {
        const header = activeSessionHeaderElement();
        if (!visible(header)) return "";
        const candidates = Array.from(header.querySelectorAll([
          "[data-thread-title]",
          "[data-testid*='thread-title' i]",
          "[data-testid*='conversation-title' i]",
          "h1",
          "h2",
          ".truncate",
          "[class*='truncate']",
          "button",
          "[role='button']",
          "span",
          "div",
        ].join(","))).filter((node) => (
          visible(node)
          && !node.closest?.(`#${rootId}`)
        ));
        for (const node of candidates) {
          // Prefer leaf-ish text; skip pure icon/menu chrome.
          if (node.querySelector?.("svg") && !(node.textContent || "").trim()) continue;
          const text = cleanActiveSessionTitle(node.textContent || "").slice(0, 160);
          if (!text || text.length < 2) continue;
          if (activeSessionHeaderTitleIgnored(text)) continue;
          // Ignore giant blobs that include the whole chrome.
          if (text.length > 120 && /\s/.test(text) && text.split(/\s+/).length > 12) continue;
          return text;
        }
        const clone = header.cloneNode(true);
        clone.querySelectorAll([
          "svg",
          `#${rootId}`,
          ".codex-usage-hud-panel",
        ].join(",")).forEach((node) => node.remove());
        const fallback = cleanActiveSessionTitle(clone.textContent || "").slice(0, 160);
        if (!fallback || activeSessionHeaderTitleIgnored(fallback)) return "";
        // If clone still contains menu words, strip leading chrome tokens.
        const stripped = fallback
          .replace(/^(File\s*Edit\s*View\s*Window\s*Help|文件\s*编辑\s*视图\s*帮助)\s*/i, "")
          .trim();
        return activeSessionHeaderTitleIgnored(stripped) ? "" : stripped;
      }

      function activeSessionComposerVisible() {
        try {
          const composer = composerElement();
          if (visible(composer)) {
            const rect = composer.getBoundingClientRect();
            if (
              rect.width >= 240
              && rect.height >= 20
              && rect.left > 200
              && rect.top > 80
              && rect.bottom <= innerHeight + 4
            ) return true;
          }
        } catch (_) {}
        return Array.from(document.querySelectorAll([
          "textarea",
          "[contenteditable='true']",
          "[role='textbox']",
          ".ProseMirror",
        ].join(","))).some((node) => {
          if (!visible(node)) return false;
          const rect = node.getBoundingClientRect();
          return (
            rect.width >= 120
            && rect.height >= 18
            && rect.left > 200
            && rect.top > 80
            && rect.bottom <= innerHeight + 4
          );
        });
      }

      function activeSessionComposerDraftText() {
        try {
          const composer = composerElement();
          const input = composer?.querySelector?.(
            "textarea, [contenteditable='true'], [role='textbox'], .ProseMirror",
          );
          if (!input || !visible(input)) return "";
          if (input.tagName === "TEXTAREA" || typeof input.value === "string") {
            return String(input.value || "");
          }
          return String(input.innerText || input.textContent || "");
        } catch (_) {
          return "";
        }
      }

      function activeSessionConversationScope() {
        // Stable container for the disclosure lookup and the mutation observer.
        // Per-turn nodes use `display: contents` (zero-size rect) and therefore
        // fail a plain visibility check; they must never be used as the scope
        // boundary because the newest (running) turn has no collapsed-work
        // disclosure yet.
        const hudRoot = document.getElementById(rootId);
        try {
          const header = conversationHeaderElement();
          const scope = header?.closest?.("main, [role='main']");
          if (scope && !hudRoot?.contains(scope)) return scope;
        } catch (_) {}
        const main = Array.from(document.querySelectorAll("main, [role='main']"))
          .find((node) => visible(node) && !hudRoot?.contains(node));
        if (main) return main;
        const turns = Array.from(document.querySelectorAll(
          "[data-content-search-turn-key], [data-turn-key]",
        )).filter((node) => node.isConnected && !hudRoot?.contains(node));
        return turns[turns.length - 1] || null;
      }

      function activeSessionTurnKeyOf(node) {
        return normalize(
          node?.getAttribute?.("data-content-search-turn-key")
          || node?.getAttribute?.("data-turn-key")
          || "",
        );
      }

      function activeSessionCollapsedDisclosureGroups(scope) {
        // Group native collapsed-work toggles by their innermost turn node.
        // querySelectorAll walks document order, so the resulting groups follow
        // turn chronology; a still-running turn simply contributes no group.
        const root = scope || activeSessionConversationScope();
        if (!root || !root.querySelectorAll) return [];
        const groups = new Map();
        const buttons = Array.from(root.querySelectorAll(
          "button[aria-expanded], [role='button'][aria-expanded]",
        ));
        for (const node of buttons) {
          if (activeSessionNodeOwnedByHud(node)) continue;
          const title = activeSessionWorkDisclosureTitle(node);
          if (!title) continue;
          // closest() returns the innermost turn boundary, which is the
          // display:contents per-turn node keyed by the turn id. Virtualized
          // "history-content:*" chunks wrap turns but never merge distinct
          // groups together.
          const turnNode = node.closest?.("[data-content-search-turn-key], [data-turn-key]") || root;
          const key = turnNode === root ? "__conversation__" : activeSessionTurnKeyOf(turnNode);
          if (!groups.has(key)) groups.set(key, { key, turnNode, entries: [] });
          groups.get(key).entries.push({ node, title });
        }
        return Array.from(groups.values());
      }

      function activeSessionDisclosureTitleLine(value) {
        for (const rawLine of String(value || "").split(/\r?\n/)) {
          const line = normalize(rawLine);
          // The duration button belongs to the user message, not to the
          // following tool-execution disclosure. Never forward it as the
          // bubble's execution-state title.
          if (/^(?:已处理|处理了|用时|耗时|历时|worked(?:\s+for)?\s|duration\b|processed\b|took\b)/i.test(line)) continue;
          if (/^(?:正在(?:编辑|运行|读取|执行|调用|处理|查看|操作)|等待(?:确认|用户)|(?:编辑了|运行了|读取了|执行了|调用了|处理了|已(?:编辑|运行|读取|执行|调用|处理|使用|创建|写入|修改))|(?:working|editing|running|reading|executing|calling|waiting|edited|ran|read|executed|used|created)\b)/i.test(line)) {
            return line.slice(0, 512);
          }
        }
        return "";
      }

      function activeSessionDisclosureTitleIsActive(value) {
        const line = normalize(value);
        return /^(?:正在(?:编辑|运行|读取|执行|调用|处理|查看|操作)|等待(?:确认|用户)|(?:working|editing|running|reading|executing|calling|waiting)\b)/i.test(line);
      }

      function activeSessionWorkDisclosureTitle(node) {
        if (!node || activeSessionNodeOwnedByHud(node) || !visible(node)) return "";
        // A time/disclosure button is the only native control that carries
        // the collapsed-work heading. Generic buttons may contain the same
        // text in an ancestor and are unsafe to associate with the turn.
        if (node.tagName?.toLowerCase() !== "button" && node.getAttribute?.("role") !== "button") return "";
        if (!node.hasAttribute?.("aria-expanded")) return "";
        const candidates = [
          node.getAttribute?.("aria-label"),
          node.getAttribute?.("title"),
          node.innerText,
          node.textContent,
          node.parentElement?.getAttribute?.("aria-label"),
          node.parentElement?.getAttribute?.("title"),
          node.parentElement?.innerText,
          node.parentElement?.textContent,
        ];
        for (const candidate of candidates) {
          const title = activeSessionDisclosureTitleLine(candidate);
          if (title) return title;
        }
        return "";
      }

      function activeSessionCollapsedWorkState() {
        let groups;
        try {
          groups = activeSessionCollapsedDisclosureGroups();
        } catch (_) {
          return { title: "", ambiguous: false };
        }
        if (!groups.length) return { title: "", ambiguous: false };
        // The last group is the most recently materialized turn that already
        // has a collapsed-work disclosure. Selecting it (instead of the newest
        // running turn) keeps the bubble title on the latest finished block and
        // follows Codex when the next turn collapses.
        const activeEntries = groups.flatMap((group) => (
          group.entries.filter(({ title }) => activeSessionDisclosureTitleIsActive(title))
        ));
        if (activeEntries.length === 1) {
          return { title: activeEntries[0].title, ambiguous: false };
        }
        if (activeEntries.length > 1) {
          return { title: "", ambiguous: true };
        }
        const latest = groups[groups.length - 1];
        if (latest.entries.length !== 1) {
          return { title: "", ambiguous: latest.entries.length > 1 };
        }
        return { title: latest.entries[0].title, ambiguous: false };
      }

      function activeSessionCollapsedWorkTitle() {
        return activeSessionCollapsedWorkState().title;
      }

      function activeSessionHeaderLooksNewSession(rows) {
        const header = activeSessionHeaderElement();
        if (!visible(header)) return false;
        const headerTitle = activeSessionHeaderTitleText();
        // A real header title means this is not a blank new-chat page.
        if (headerTitle && !activeSessionTitleIsNewSession(headerTitle)) return false;
        if (activeSessionLocationId()) return false;
        const activeRows = Array.isArray(rows) ? rows : activeSessionRows();
        if (activeRows.some(activeSessionRowSelected)) return false;
        // Any sidebar row with a real thread id that looks active/current also
        // disqualifies the blank new-session latch.
        if (activeRows.some((row) => {
          const ref = activeSessionRefFromRow(row);
          return !!(ref.sessionId || ref.rawSessionId) && !ref.pendingSession && !activeSessionTitleIsNewSession(ref.title);
        }) && activeRows.some(activeSessionRowSelected)) {
          return false;
        }
        if (headerTitle && activeSessionTitleIsNewSession(headerTitle)) {
          return activeSessionComposerVisible();
        }
        if (headerTitle) return false;
        return activeSessionComposerVisible();
      }

      function activeSessionRefFromRow(row) {
        const sourceRow = activeSessionIdentityRow(row);
        const href = activeSessionRowHref(sourceRow);
        const idMatch = href.match(/(?:session|conversation|thread)[=/:-]([A-Za-z0-9_.-]+)/i)
          || href.match(/([A-Za-z0-9_-]{8,})$/);
        const rawSessionId = sourceRow?.getAttribute?.("data-app-action-sidebar-thread-id")
          || (idMatch && idMatch[1])
          || sourceRow?.getAttribute?.("data-session-id")
          || sourceRow?.getAttribute?.("data-testid")
          || "";
        const sessionId = normalizeThreadId(rawSessionId);
        const titleNode = sourceRow?.querySelector?.(activeSessionTitleSelector);
        const rawTitle = titleNode?.textContent
          || (titleNode ? "" : (activeSessionRowLooksThread(sourceRow) ? (sourceRow?.textContent || row?.textContent || "") : ""));
        const title = cleanActiveSessionTitle(titleNode ? rawTitle : rawTitle.replace(/\s*(Export|Delete|Move|Remove from project|导出|删除|移动|移出项目)+$/g, "")).slice(0, 160);
        if (activeSessionTitleIsNewSession(title)) {
          return { rawSessionId: "", sessionId: "", title };
        }
        if (activeSessionIdIsProvisional(rawSessionId)) {
          return {
            rawSessionId: normalize(rawSessionId),
            rendererSessionId: normalize(rawSessionId),
            sessionId: "",
            title,
            pendingSession: true,
          };
        }
        return { rawSessionId: normalize(rawSessionId), sessionId, title };
      }

      function activeSessionRowSelected(row) {
        if (row?.getAttribute?.("data-app-action-sidebar-thread-active") === "true") return true;
        if (row?.getAttribute?.("aria-current") === "page" || row?.getAttribute?.("aria-current") === "true") return true;
        if (row?.getAttribute?.("aria-selected") === "true") return true;
        if (row?.getAttribute?.("data-active") === "true" || row?.getAttribute?.("data-selected") === "true") return true;
        if (row?.matches?.("[data-state='active'], [data-state='selected'], .active, .selected")) return true;
        // Soft visual/ARIA fallbacks used by some Codex builds.
        if (row?.getAttribute?.("data-highlighted") === "true") return true;
        if (row?.classList?.contains?.("bg-token-sidebar-item-active")) return true;
        try {
          const style = getComputedStyle(row);
          if (style && Number(style.fontWeight || 0) >= 600) {
            const bg = style.backgroundColor || "";
            if (bg && bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent") return true;
          }
        } catch (_) {}
        return false;
      }

      function activeSessionRowMatchesLocation(row) {
        const href = activeSessionRowHref(row);
        if (href) {
          try {
            const url = new URL(href, location.href);
            if (url.href === location.href) return true;
          } catch (_) {
            if (location.href.includes(href)) return true;
          }
        }
        const ref = activeSessionRefFromRow(row);
        return (
          (!!ref.rawSessionId && location.href.includes(ref.rawSessionId))
          || (!!ref.sessionId && location.href.includes(ref.sessionId))
        );
      }

      function activeSessionRows() {
        const container = activeSessionContainer();
        const root = container || document;
        return Array.from(root.querySelectorAll(activeSessionRowSelector))
          .filter((row) => !activeSessionNodeOwnedByHud(row))
          .filter((row) => activeSessionRowLooksThread(row))
          .filter((row) => {
            const ref = activeSessionRefFromRow(row);
            return !!(ref.sessionId || ref.title);
          });
      }

      function readActiveSessionRef() {
        const rows = activeSessionRows();
        const headerTitle = activeSessionHeaderTitleText();
        if (activeSessionHeaderLooksNewSession(rows)) {
          return {
            sessionId: "",
            title: "",
            url: location.href,
            newSession: true,
            matchedBy: "header-empty",
            draftText: activeSessionComposerDraftText(),
          };
        }
        let row = rows.find(activeSessionRowSelected) || rows.find(activeSessionRowMatchesLocation) || null;
        // If header already shows a real title, prefer the sidebar row with the same title.
        if (!row && headerTitle && !activeSessionTitleIsNewSession(headerTitle)) {
          row = rows.find((candidate) => {
            const ref = activeSessionRefFromRow(candidate);
            const title = cleanActiveSessionTitle(ref.title || "");
            return title && (title === headerTitle || headerTitle.startsWith(title) || title.startsWith(headerTitle));
          }) || null;
        }
        const ref = row ? activeSessionRefFromRow(row) : { sessionId: activeSessionLocationId(), title: "" };
        const pendingSession = !!ref.pendingSession;
        let title = ref.title || "";
        if (!title && headerTitle && !activeSessionTitleIsNewSession(headerTitle)) {
          title = headerTitle;
        }
        const newSession = !pendingSession && !ref.sessionId && activeSessionTitleIsNewSession(title);
        // Never claim new-session when the chrome already shows a real conversation title.
        if (!pendingSession && !ref.sessionId && !newSession && !title && headerTitle && !activeSessionTitleIsNewSession(headerTitle)) {
          title = headerTitle;
        }
        return {
          sessionId: (newSession || pendingSession) ? "" : (ref.sessionId || ""),
          rendererSessionId: ref.rendererSessionId || ref.rawSessionId || "",
          title: newSession ? "" : (title || ""),
          url: location.href,
          newSession,
          pendingSession,
          matchedBy: row ? "sidebar-row" : (title ? "header-title" : ""),
          draftText: activeSessionComposerDraftText(),
        };
      }

      function activeSessionContainer() {
        const titleNode = activeSessionFirstOutsideHud(activeSessionTitleSelector);
        const identityRows = Array.from(document.querySelectorAll(activeSessionIdentitySelector))
          .filter((node) => !activeSessionNodeOwnedByHud(node));
        const selectedIdentityRow = identityRows.find(activeSessionRowSelected) || null;
        const row = selectedIdentityRow
          || identityRows[0]
          || titleNode?.closest?.(activeSessionRowSelector)
          || activeSessionFirstOutsideHud(activeSessionRowSelector);
        // Prefer the role=list that owns the selected identity row. Codex
        // Desktop can expose separate lists for pinned and recent threads;
        // using the first row's list would hide the selected row in another
        // group. Without an explicit selected row, use the broader nav/aside
        // so all groups remain searchable.
        // Note: [class*='sidebar'] is intentionally last because row items
        // themselves may carry "sidebar" in their class (data-app-action-sidebar-*).
        if (selectedIdentityRow) {
          return row?.closest?.("[role='list']")
            || row?.closest?.("aside, nav, [role='navigation']")
            || row?.closest?.("[data-testid*='sidebar' i], [class*='sidebar' i]")
            || row?.parentElement
            || document.querySelector("[role='list']")
            || document.querySelector("aside, nav, [role='navigation']")
            || document.querySelector("[data-testid*='sidebar' i], [class*='sidebar' i]")
            || null;
        }
        return row?.closest?.("aside, nav, [role='navigation']")
          || row?.closest?.("[role='list']")
          || row?.closest?.("[data-testid*='sidebar' i], [class*='sidebar' i]")
          || row?.parentElement
          || document.querySelector("[role='list']")
          || document.querySelector("aside, nav, [role='navigation']")
          || document.querySelector("[data-testid*='sidebar' i], [class*='sidebar' i]")
          || null;
      }

      function postActiveSession(reason = "event", overrideRef = null) {
        const bridge = settingsBridgeUrl();
        const ref = overrideRef || readActiveSessionRef();
        const newSession = !!ref.newSession || reason === "new-session";
        const pendingSession = !!ref.pendingSession;
        const draftText = Object.prototype.hasOwnProperty.call(ref, "draftText")
          ? String(ref.draftText || "")
          : activeSessionComposerDraftText();
        const collapsedWork = (newSession || pendingSession)
          ? { title: "", ambiguous: false }
          : activeSessionCollapsedWorkState();
        const collapsedTitle = collapsedWork.title;
        const collapsedDisclosureAmbiguous = !!collapsedWork.ambiguous;
        const sendRequested = /^(composer-send|composer-send-click|composer-enter|composer-submit)$/i.test(
          String(reason || ""),
        );
        if (!ref.sessionId && !ref.title && !newSession && !pendingSession) return;
        const rawRendererSessionId = normalize(
          ref.rendererSessionId || ref.rawSessionId || ref.sessionId || "",
        );
        const rendererSessionId = normalizeThreadId(rawRendererSessionId);
        const canonicalSessionId = activeSessionIdIsProvisional(rawRendererSessionId)
          ? ""
          : rendererSessionId;
        const lastCanonicalSessionId = normalizeThreadId(
          window[activeSessionCanonicalIdName] || "",
        );
        const lastCanonicalAt = Number(window[activeSessionCanonicalAtName] || 0);
        const headerTitleTransition = (
          !canonicalSessionId
          && !newSession
          && !pendingSession
          && ref.matchedBy === "header-title"
        );
        const transientWithoutCanonicalId = !canonicalSessionId && (
          newSession
          || pendingSession
          || headerTitleTransition
        );
        if (
          transientWithoutCanonicalId
          && reason !== "click"
          && lastCanonicalSessionId
          && Date.now() - lastCanonicalAt < 2500
        ) {
          // Codex can briefly expose only the header title while replacing the
          // selected row; do not let that transition overwrite a recent id.
          ctx.lifecycle.clearTimeout(window[activeSessionSettledTimerName] || 0);
          window[activeSessionSettledTimerName] = ctx.lifecycle.timeout("active_session", () => {
            postActiveSession("settled");
          }, 320);
          return;
        }
        // The canonical thread UUID is the selection identity. Renderer
        // rebuilds may rewrite the raw prefix or title while the same thread
        // stays selected; those fields still belong in the payload, but must
        // not create a new authoritative sequence.
        const selectionKey = canonicalSessionId
          ? JSON.stringify([canonicalSessionId, newSession, pendingSession])
          : JSON.stringify([
            rawRendererSessionId,
            ref.title,
            newSession,
            pendingSession,
          ]);
        if (window[activeSessionSelectionKeyName] !== selectionKey) {
          window[activeSessionSelectionKeyName] = selectionKey;
          window[activeSessionSelectionSeqName] = Number(window[activeSessionSelectionSeqName] || 0) + 1;
        }
        const selectionSeq = Math.max(1, Number(window[activeSessionSelectionSeqName] || 1));
        const appliedSeq = Number(window[activeSessionAppliedSeqName] || 0);
        const signature = JSON.stringify([
          selectionSeq,
          rawRendererSessionId,
          ref.title,
          ref.url || location.href,
          newSession,
          pendingSession,
          draftText,
          sendRequested,
          collapsedTitle,
          collapsedDisclosureAmbiguous,
        ]);
        if (window[activeSessionLastSignatureName] === signature && appliedSeq >= selectionSeq) return;
        const payload = {
          sessionId: canonicalSessionId,
          rendererSessionId: rawRendererSessionId,
          selectionSeq,
          title: ref.title,
          url: ref.url || location.href,
          reason: newSession ? "new-session" : (pendingSession ? "pending-session" : reason),
          newSession,
          pendingSession,
          draftText,
          sendRequested,
          collapsedTitle,
          collapsedDisclosureAmbiguous,
          matchedBy: ref.matchedBy || "",
          observedAt: Number(ref.observedAt || 0) || Date.now(),
        };
        if (ctx.bindings.available(activeSessionBindingName)) {
          try {
            ctx.bindings.send(activeSessionBindingName, payload);
            // Mark an observation as delivered only after the transport accepts
            // it. The first sidebar click can race binding setup on a cold start;
            // recording the signature before delivery suppressed every follow-up
            // until the user clicked a second conversation.
            window[activeSessionLastSignatureName] = signature;
            if (canonicalSessionId) {
              window[activeSessionCanonicalIdName] = canonicalSessionId;
              window[activeSessionCanonicalAtName] = Date.now();
            } else {
              window[activeSessionCanonicalIdName] = "";
              window[activeSessionCanonicalAtName] = 0;
            }
            return;
          } catch (_) {}
        }
        if (!bridge) return;
        fetch(`${bridge}/active-session`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          keepalive: true,
        }).then(() => {
          if (!ctx.lifecycle.active()) return;
          window[activeSessionLastSignatureName] = signature;
          if (canonicalSessionId) {
            window[activeSessionCanonicalIdName] = canonicalSessionId;
            window[activeSessionCanonicalAtName] = Date.now();
          } else {
            window[activeSessionCanonicalIdName] = "";
            window[activeSessionCanonicalAtName] = 0;
          }
        }).catch(() => {});
      }

      function scheduleActiveSessionReport(reason = "event") {
        ctx.lifecycle.clearTimeout(window[activeSessionTimerName] || 0);
        window[activeSessionTimerName] = ctx.lifecycle.timeout("active_session", () => {
          postActiveSession(reason);
          refreshActiveSessionObserver();
        }, 0);
      }

      function scheduleActiveSessionDraftReport() {
        ctx.lifecycle.clearTimeout(window[activeSessionDraftTimerName] || 0);
        window[activeSessionDraftTimerName] = ctx.lifecycle.timeout(
          "active_session",
          () => {
            window[activeSessionDraftTimerName] = 0;
            postActiveSession("composer-draft");
          },
          80,
        );
      }

      function clearActiveSessionSendFollowup() {
        for (const timer of (window[activeSessionSendFollowupTimersName] || [])) {
          ctx.lifecycle.clearTimeout(timer);
        }
        window[activeSessionSendFollowupTimersName] = [];
        ctx.lifecycle.clearTimeout(window[activeSessionSettledTimerName] || 0);
        window[activeSessionSettledTimerName] = 0;
      }

      function showActiveSessionFollowFeedback(reason = "reading-session-data") {
        const root = document.getElementById(rootId);
        if (!root) return;
        const message = reason === "renderer-channel-unavailable"
          ? "会话切换暂停：renderer 事件通道不可用"
          : "会话切换中：正在读取会话数据";
        setText(root, "requestLine", message);
        setText(root, "requestLineExpanded", message);
      }

      function activeSessionPayloadCache() {
        const existing = window[activeSessionPayloadCacheName];
        if (existing instanceof Map) return existing;
        const cache = new Map();
        window[activeSessionPayloadCacheName] = cache;
        return cache;
      }

      function activeSessionPayloadKeys(value) {
        const raw = normalize(value?.rendererSessionId || value?.rawSessionId || "");
        const canonical = normalize(value?.sessionId || "");
        return [...new Set([raw, canonical].filter(Boolean))];
      }

      function cacheActiveSessionPayload(payload) {
        if (
          !payload
          || payload.cachedPreview
          || payload.newSession
          || payload.pendingSession
          || payload.requestStatus !== "confirmed"
        ) return;
        const keys = activeSessionPayloadKeys(payload);
        if (!keys.length) return;
        const cache = activeSessionPayloadCache();
        const cached = { ...payload, cachedPreview: false };
        delete cached.payloadDomains;
        for (const key of keys) cache.set(key, cached);
        while (cache.size > 48) cache.delete(cache.keys().next().value);
      }

      function applyCachedActiveSessionPayload(ref, observedAt = 0) {
        const cache = activeSessionPayloadCache();
        const cached = activeSessionPayloadKeys(ref).map((key) => cache.get(key)).find(Boolean);
        if (!cached || typeof window.__codexUsageHudUpdate !== "function") return false;
        const domain = {
          ...cached,
          selectionSeq: Math.max(1, Number(window[activeSessionSelectionSeqName] || 1)),
          selectionObservedAt: Number(observedAt || 0) || Date.now(),
          followState: "cached",
          followReason: "renderer-cached-preview",
          cachedPreview: true,
        };
        window.__codexUsageHudUpdate({
          ...domain,
          payloadDomains: { sessionSwitch: domain },
        });
        window.__codexUsageHudCachedPreview = {
          selectionSeq: domain.selectionSeq,
          observedAt: domain.selectionObservedAt,
          appliedAt: Date.now(),
          rendererSessionId: normalize(ref?.rendererSessionId || ref?.rawSessionId || ""),
        };
        return true;
      }

      function activeSessionComposerTarget(target) {
        if (!(target instanceof Node)) return false;
        const composer = composerElement();
        if (composer?.contains?.(target)) return true;
        return !!target.closest?.("textarea, [contenteditable='true'], [role='textbox'], .ProseMirror");
      }

      function scheduleActiveSessionSendFollowup(reason = "composer-send", expectedSessionId = "") {
        clearActiveSessionSendFollowup();
        const expected = normalize(expectedSessionId);
        const report = () => {
          const ref = readActiveSessionRef();
          // Codex clears/unmounts the composer and transient header before the
          // first post-send DOM mutation. Preserve the provisional handoff for
          // the test-visible invariant: preserve the provisional handoff.
          // that gap so the existing draft bubble flips to "sending" now;
          // later follow-ups replace it with the canonical thread id.
          if (
            /^(composer-send|composer-send-click|composer-enter|composer-submit)$/i.test(
              String(reason || ""),
            )
            && !ref.sessionId
            && !ref.title
            && !ref.newSession
            && !ref.pendingSession
          ) {
            ref.newSession = true;
          }
          const current = normalize(ref.rendererSessionId || ref.rawSessionId || ref.sessionId || "");
          if (expected && current !== expected) return;
          postActiveSession(reason, ref);
          refreshActiveSessionObserver();
          // Keep reconciling while still on a blank/new latch so late title/id
          // assignment (beyond the initial 1.6s burst) can clear sticky state.
          if (ref.newSession || ref.pendingSession || (!ref.sessionId && !ref.title)) {
            return true;
          }
          return false;
        };
        // Publish before Codex clears the composer or waits for its first API
        // response. A short follow-up burst is retained only for a new/pending
        // row whose canonical id is still being materialized.
        let keepFollowup = false;
        try {
          keepFollowup = report();
        } catch (_) {}
        const delays = keepFollowup
          ? [32, 120, 320, 800, 1600, 3200, 5600, 9000]
          : [32, 120, 320];
        window[activeSessionSendFollowupTimersName] = delays.map((ms) => ctx.lifecycle.timeout("active_session", () => {
          try {
            if (!report()) clearActiveSessionSendFollowup();
          } catch (_) {}
        }, ms));
      }

      function activeSessionComposerSubmitButton(button) {
        if (
          !(button instanceof HTMLElement)
          || button.hasAttribute("disabled")
          || button.getAttribute("aria-disabled") === "true"
        ) return false;
        const label = cleanActiveSessionTitle(
          button.getAttribute("aria-label")
          || button.getAttribute("title")
          || button.getAttribute("data-testid")
          || button.getAttribute("data-action")
          || button.textContent
          || ""
        );
        if (button.getAttribute("type")?.toLowerCase() === "submit") return true;
        return /(?:send|submit|发送|提交)/i.test(label);
      }

      function refreshActiveSessionObserver() {
        const container = activeSessionContainer();
        const header = activeSessionHeaderElement();
        const conversation = activeSessionConversationScope();
        ctx.observers.clear("active_session");
        if (!container && !header && !conversation) return false;
        window[activeSessionObserverName] = ctx.observers.set("active_session", new MutationObserver(() => {
          scheduleActiveSessionReport("active-session-dom");
        }));
        if (container) window[activeSessionObserverName].observe(container, {
          subtree: true,
          childList: true,
          attributes: true,
          attributeFilter: [
            "aria-selected",
            "aria-current",
            "data-active",
            "data-selected",
            "data-state",
            "data-app-action-sidebar-thread-active",
            "data-app-action-sidebar-thread-id",
            "href",
          ],
        });
        if (header && header !== container && !container?.contains?.(header)) {
          window[activeSessionObserverName].observe(header, {
            subtree: true,
            childList: true,
            characterData: true,
            attributes: true,
            attributeFilter: [
              "aria-label",
              "class",
              "data-thread-title",
              "data-testid",
              "title",
            ],
          });
        }
        if (conversation && conversation !== container && conversation !== header) {
          window[activeSessionObserverName].observe(conversation, {
            subtree: true,
            childList: true,
            characterData: true,
            attributes: true,
            attributeFilter: ["aria-expanded", "aria-label", "title", "data-state"],
          });
        }
        return true;
      }

      function startActiveSessionBootstrapObserver() {
        if (window[activeSessionBootstrapObserverName] || refreshActiveSessionObserver()) return;
        if (!document.body) return;
        window[activeSessionBootstrapObserverName] = ctx.observers.set("active_session_bootstrap", new MutationObserver(() => {
          if (refreshActiveSessionObserver()) {
            ctx.observers.clear("active_session_bootstrap");
            delete window[activeSessionBootstrapObserverName];
            scheduleActiveSessionReport("sidebar-ready");
          }
        }));
        window[activeSessionBootstrapObserverName].observe(document.body, {
          subtree: true,
          childList: true,
        });
        window[activeSessionBootstrapTimerName] = ctx.lifecycle.timeout("active_session_bootstrap", () => {
          ctx.observers.clear("active_session_bootstrap");
          delete window[activeSessionBootstrapObserverName];
          delete window[activeSessionBootstrapTimerName];
        }, 5000);
      }

      function installActiveSessionHistoryPatch() {
        if (window[activeSessionHistoryPatchName]) return;
        const originalPushState = history.pushState;
        const originalReplaceState = history.replaceState;
        const patch = {
          originalPushState,
          originalReplaceState,
          pushState: function(...args) {
            const result = originalPushState.apply(this, args);
            scheduleActiveSessionSendFollowup("history");
            return result;
          },
          replaceState: function(...args) {
            const result = originalReplaceState.apply(this, args);
            scheduleActiveSessionSendFollowup("history");
            return result;
          },
          popstate: () => scheduleActiveSessionSendFollowup("popstate"),
        };
        try {
          history.pushState = patch.pushState;
          history.replaceState = patch.replaceState;
          ctx.lifecycle.listen("active_session", window, "popstate", patch.popstate);
          window[activeSessionHistoryPatchName] = patch;
        } catch (_) {
          try {
            history.pushState = originalPushState;
            history.replaceState = originalReplaceState;
          } catch (_) {}
        }
      }

      function removeActiveSessionWatchers() {
        ctx.lifecycle.disposeScope("active_session_listeners");
        ctx.lifecycle.clearTimeout(window[activeSessionTimerName] || 0);
        clearActiveSessionSendFollowup();
        ctx.observers.clear("active_session");
        ctx.observers.clear("active_session_bootstrap");
        const patch = window[activeSessionHistoryPatchName];
        if (patch) {
          if (history.pushState === patch.pushState) history.pushState = patch.originalPushState;
          if (history.replaceState === patch.replaceState) history.replaceState = patch.originalReplaceState;
        }
        delete window[activeSessionObserverName];
        delete window[activeSessionBootstrapObserverName];
        delete window[activeSessionTimerName];
        delete window[activeSessionDraftTimerName];
        delete window[activeSessionSendFollowupTimersName];
        delete window[activeSessionComposerHandlerName];
        delete window[activeSessionClickHandlerName];
        delete window[activeSessionHistoryPatchName];
        delete window[activeSessionLastSignatureName];
        delete window[activeSessionCanonicalIdName];
        delete window[activeSessionCanonicalAtName];
        delete window[activeSessionSettledTimerName];
        delete window[activeSessionSelectionKeyName];
        delete window[activeSessionSelectionSeqName];
        delete window[activeSessionAppliedSeqName];
      }

      function ensureActiveSessionWatchers() {
        let activeSessionScope = ctx.lifecycle.getScope("active_session_listeners");
        if (!window[activeSessionClickHandlerName]) {
          activeSessionScope = ctx.lifecycle.scope("active_session_listeners");
          window[activeSessionClickHandlerName] = (event) => {
            if (activeSessionNodeOwnedByHud(event.target)) return;
            const submitButton = event.target?.closest?.("button, [role='button']");
            if (
              !composerBadgeEnabled
              && submitButton
              && activeSessionComposerTarget(submitButton)
              && activeSessionComposerSubmitButton(submitButton)
            ) {
              scheduleActiveSessionSendFollowup("composer-send");
              return;
            }
            const container = event.target?.closest?.("aside, nav, [role='navigation'], [data-testid*='sidebar' i], [class*='sidebar' i]");
            const identityRow = event.target?.closest?.(activeSessionIdentitySelector);
            const row = identityRow || event.target?.closest?.(activeSessionRowSelector);
            const explicitRow = !!identityRow || row?.matches?.("[role='link']");
            if (row && !container && !explicitRow) return;
            if (row && (!container || container.contains(row))) {
              const ref = activeSessionRefFromRow(row);
              const pendingSession = !!ref.pendingSession;
              const newSession = !pendingSession && !ref.sessionId && activeSessionTitleIsNewSession(ref.title);
              const observedAt = Date.now();
              showActiveSessionFollowFeedback(
                ctx.bindings.available(activeSessionBindingName)
                  ? "reading-session-data"
                  : "renderer-channel-unavailable"
              );
              postActiveSession("click", {
                sessionId: (newSession || pendingSession) ? "" : (ref.sessionId || ""),
                rendererSessionId: ref.rendererSessionId || ref.rawSessionId || "",
                title: newSession ? "" : (ref.title || ""),
                url: activeSessionRowUrl(row),
                newSession,
                pendingSession,
                observedAt,
              });
              applyCachedActiveSessionPayload({
                sessionId: ref.sessionId || "",
                rendererSessionId: ref.rendererSessionId || ref.rawSessionId || "",
              }, observedAt);
              scheduleActiveSessionSendFollowup(
                "click",
                ref.rendererSessionId || ref.rawSessionId || ref.sessionId || "",
              );
            }
          };
          activeSessionScope.listen(document, "click", window[activeSessionClickHandlerName], true);
        }
        if (!window[activeSessionComposerHandlerName]) {
          activeSessionScope ||= ctx.lifecycle.scope("active_session_listeners");
          const submit = (event) => {
            if (activeSessionComposerTarget(event.target)) {
              scheduleActiveSessionSendFollowup("composer-submit");
            }
          };
          const input = (event) => {
            if (activeSessionComposerTarget(event.target)) {
              scheduleActiveSessionDraftReport();
            }
          };
          const keydown = (event) => {
            if (
              event.key === "Enter"
              && !event.shiftKey
              && !event.isComposing
              && activeSessionComposerTarget(event.target)
            ) {
              scheduleActiveSessionSendFollowup("composer-enter");
            }
          };
          window[activeSessionComposerHandlerName] = { submit, input, keydown };
          activeSessionScope.listen(document, "submit", submit, true);
          activeSessionScope.listen(document, "input", input, true);
          if (!composerBadgeEnabled) {
            activeSessionScope.listen(document, "keydown", keydown, true);
          }
        }
        installActiveSessionHistoryPatch();
        startActiveSessionBootstrapObserver();
        scheduleActiveSessionReport("payload");
      }

      window.__codexUsageHudReportActiveSession = (reason = "manual") => {
        ensureActiveSessionWatchers();
        const reportReason = String(reason || "manual");
        const ref = readActiveSessionRef();
        const collapsedWork = (ref.newSession || ref.pendingSession)
          ? { title: "", ambiguous: false }
          : activeSessionCollapsedWorkState();
        postActiveSession(reportReason, ref);
        // Return the same renderer-observed reference through Runtime.evaluate.
        // CDP bindings still keep it live afterwards, but this synchronous result
        // guarantees that the initial HUD snapshot follows the page already open
        // when the HUD starts (including the blank-title new-session page).
        return {
          sessionId: ref.sessionId || ref.rendererSessionId || "",
          rendererSessionId: ref.rendererSessionId || "",
          title: ref.title || "",
          draftText: ref.draftText || "",
          collapsedTitle: collapsedWork.title,
          collapsedDisclosureAmbiguous: !!collapsedWork.ambiguous,
          sendRequested: /^(composer-send|composer-send-click|composer-enter|composer-submit)$/i.test(reportReason),
          newSession: !!ref.newSession,
          pendingSession: !!ref.pendingSession,
          matchedBy: ref.matchedBy || "",
          reason: reportReason,
          observedAt: Date.now(),
        };
      };

    function install() {
      return true;
    }

    function apply(_root, _payload) {
      return true;
    }

    function dispose() {
      removeActiveSessionWatchers();
      return true;
    }

    return {
      install,
      apply,
      dispose,
      normalizeThreadId,
      activeSessionIdIsProvisional,
      activeSessionNodeOwnedByHud,
      activeSessionFirstOutsideHud,
      activeSessionLocationId,
      activeSessionRowHref,
      activeSessionRowUrl,
      activeSessionIdentityRow,
      activeSessionRowLooksThread,
      cleanActiveSessionTitle,
      activeSessionTitleIsNewSession,
      activeSessionHeaderElement,
      activeSessionHeaderTitleIgnored,
      activeSessionHeaderTitleText,
      activeSessionComposerVisible,
      activeSessionHeaderLooksNewSession,
      activeSessionRefFromRow,
      activeSessionRowSelected,
      activeSessionRowMatchesLocation,
      activeSessionRows,
      readActiveSessionRef,
      activeSessionContainer,
      postActiveSession,
      scheduleActiveSessionReport,
      clearActiveSessionSendFollowup,
      showActiveSessionFollowFeedback,
      activeSessionPayloadCache,
      activeSessionPayloadKeys,
      cacheActiveSessionPayload,
      applyCachedActiveSessionPayload,
      activeSessionComposerTarget,
      scheduleActiveSessionSendFollowup,
      activeSessionComposerSubmitButton,
      refreshActiveSessionObserver,
      startActiveSessionBootstrapObserver,
      installActiveSessionHistoryPatch,
      removeActiveSessionWatchers,
      ensureActiveSessionWatchers,
    };
  }

  const activeSessionDomain = ctx.domains.register(
    "active_session",
    createActiveSessionDomain(ctx, shared),
  );
  const {
    normalizeThreadId,
    activeSessionIdIsProvisional,
    activeSessionNodeOwnedByHud,
    activeSessionFirstOutsideHud,
    activeSessionLocationId,
    activeSessionRowHref,
    activeSessionRowUrl,
    activeSessionIdentityRow,
    activeSessionRowLooksThread,
    cleanActiveSessionTitle,
    activeSessionTitleIsNewSession,
    activeSessionHeaderElement,
    activeSessionHeaderTitleIgnored,
    activeSessionHeaderTitleText,
    activeSessionComposerVisible,
    activeSessionHeaderLooksNewSession,
    activeSessionRefFromRow,
    activeSessionRowSelected,
    activeSessionRowMatchesLocation,
    activeSessionRows,
    readActiveSessionRef,
    activeSessionContainer,
    postActiveSession,
    scheduleActiveSessionReport,
    clearActiveSessionSendFollowup,
    showActiveSessionFollowFeedback,
    activeSessionPayloadCache,
    activeSessionPayloadKeys,
    cacheActiveSessionPayload,
    applyCachedActiveSessionPayload,
    activeSessionComposerTarget,
    scheduleActiveSessionSendFollowup,
    activeSessionComposerSubmitButton,
    refreshActiveSessionObserver,
    startActiveSessionBootstrapObserver,
    installActiveSessionHistoryPatch,
    removeActiveSessionWatchers,
    ensureActiveSessionWatchers,
  } = activeSessionDomain;
"""

__all__ = ["TEXT"]
