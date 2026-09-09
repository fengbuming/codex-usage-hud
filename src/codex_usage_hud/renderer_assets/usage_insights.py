"""Renderer usage insights domain asset."""

TEXT = r"""
  function createUsageInsightsDomain(ctx, shared) {
      function usageInsightsFromPayload() {
        if (usageInsightsState.data && typeof usageInsightsState.data === "object") {
          return usageInsightsState.data;
        }
        const value = currentPayload()?.usageInsights;
        return value && typeof value === "object" && Object.keys(value).length ? value : null;
      }

      function usageInsightsFormatTokens(value) {
        const amount = Math.max(0, Number(value) || 0);
        if (amount >= 1000000) return `${(amount / 1000000).toFixed(amount >= 10000000 ? 0 : 1)}M`;
        if (amount >= 1000) return `${(amount / 1000).toFixed(amount >= 100000 ? 0 : 1)}k`;
        return Math.round(amount).toLocaleString();
      }

      function usageInsightsFormatCost(value) {
        if (value === null || value === undefined || !Number.isFinite(Number(value))) return "费用待估";
        const amount = Math.max(0, Number(value));
        return `$${amount < 1 ? amount.toFixed(3) : amount.toFixed(2)}`;
      }

      function usageInsightsFormatRatio(value) {
        if (value === null || value === undefined || !Number.isFinite(Number(value))) return "--";
        const raw = Number(value);
        const ratio = raw > 1 ? raw / 100 : raw;
        return `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%`;
      }

      function usageInsightsRangeItem(item, range) {
        if (!item || typeof item !== "object") return {};
        const scoped = item[range];
        if (scoped && typeof scoped === "object") return { ...item, ...scoped };
        const prefix = range === "week" ? "week" : "today";
        return {
          ...item,
          tokens: item[`${prefix}Tokens`] ?? item.tokens,
          costUsd: item[`${prefix}CostUsd`] ?? item.costUsd,
          cacheRatio: item[`${prefix}CacheRatio`] ?? item.cacheRatio,
          requestCount: item[`${prefix}RequestCount`] ?? item.requestCount,
        };
      }

      function usageInsightsRangeData(data, range) {
        const scoped = data?.[range] && typeof data[range] === "object" ? data[range] : {};
        const totals = scoped?.totals && typeof scoped.totals === "object"
          ? scoped.totals
          : (data?.[`${range}Totals`] && typeof data[`${range}Totals`] === "object"
            ? data[`${range}Totals`]
            : (data?.totals?.[range] || data?.totals || {}));
        const list = (name) => {
          const values = Array.isArray(scoped?.[name]) ? scoped[name] : (Array.isArray(data?.[name]) ? data[name] : []);
          return values.map((item) => usageInsightsRangeItem(item, range));
        };
        return {
          totals,
          sessions: list("sessions"),
          topSessionsByUsage: list("topSessionsByUsage"),
          topSessionsByCost: list("topSessionsByCost"),
          models: list("models"),
          providers: list("providers"),
          background: scoped?.background || data?.background || {},
          costCoverage: scoped?.costCoverage || totals?.costCoverage || data?.costCoverage || {},
        };
      }

      function usageInsightsRankLabel(item, kind) {
        if (kind === "sessions") return String(item?.title || item?.name || item?.sessionTitle || item?.id || item?.sessionId || "未命名会话");
        if (kind === "models") return String(item?.model || item?.name || "未知模型");
        const rawProvider = String(item?.provider || item?.name || "").trim();
        if (!rawProvider) return "未知 Provider";
        return providerRegistryDisplayName(currentPayload()?.settings, rawProvider);
      }

      function usageInsightsSessionModelNames(session) {
        const values = Array.isArray(session?.models) ? session.models : [];
        const names = values.map((item) => String(
          item && typeof item === "object" ? (item.model || item.name || "") : item,
        ).trim()).filter(Boolean);
        return Array.from(new Set(names));
      }

      function usageInsightsSessionModelSummary(session, limit = 2) {
        const names = usageInsightsSessionModelNames(session);
        if (!names.length) return "未知模型";
        const visible = names.slice(0, Math.max(1, Number(limit) || 1));
        return `${visible.join(" + ")}${names.length > visible.length ? ` +${names.length - visible.length}` : ""}`;
      }

      function usageInsightsRankingRowsHtml(items, kind, {
        limit = 5,
        metric = "tokens",
        selectedSessionId = "",
        sessionAction = "usage-insights-session",
      } = {}) {
        return (Array.isArray(items) ? items : []).slice(0, limit).map((item, index) => {
          const sessionId = String(item?.id || item?.sessionId || "");
          const label = usageInsightsRankLabel(item, kind);
          const actionable = kind === "sessions"
            && (item?.actionable === true || item?.canActivate === true)
            && !!sessionId;
          const selectable = kind === "sessions"
            && !!sessionId
            && sessionAction !== "usage-insights-session";
          const opensSession = actionable && sessionAction === "usage-insights-session";
          const tag = opensSession ? "button" : "div";
          const action = selectable || opensSession ? sessionAction : "";
          const actionAttrs = action
            ? `${opensSession ? ' type="button"' : ' role="button" tabindex="0"'} data-action="${escapeHtml(action)}" data-usage-session-id="${escapeHtml(sessionId)}" data-selected="${String(sessionId === selectedSessionId)}" aria-label="${escapeHtml(selectable ? `查看会话 ${label}` : `打开会话 ${label}`)}"`
            : "";
          const workdir = String(item?.workdirName || "").trim();
          const workdirPath = String(item?.workdir || "").trim();
          const modelText = usageInsightsSessionModelSummary(item);
          const cache = usageInsightsFormatRatio(item?.cacheRatio);
          const coverage = item?.costCoverage && typeof item.costCoverage === "object"
            ? item.costCoverage
            : {};
          const incompleteCost = coverage?.hasCompleteCost === false;
          const hasEstimatedCost = item?.costUsd !== null && item?.costUsd !== undefined;
          const costCoverageNote = incompleteCost
            ? (hasEstimatedCost ? "费用部分可估" : "费用不可估")
            : "";
          const latestEventAt = String(item?.latestEventAt || "");
          const timestamp = latestEventAt ? backgroundUsageTime(latestEventAt, { compact: true }) : "--";
          const tokens = `${usageInsightsFormatTokens(item?.tokens ?? item?.totalTokens)} tokens`;
          const cost = usageInsightsFormatCost(item?.costUsd);
          const cacheText = cache === "--" ? "缓存 --" : `缓存 ${cache}`;
          const rankLabel = metric === "cost" ? "金额排名" : "用量排名";
          const workdirHtml = workdirPath
            ? `<button type="button" class="codex-usage-hud-session-ranking-workdir" data-action="usage-insights-open-workdir" data-usage-session-id="${escapeHtml(sessionId)}" aria-label="打开工作目录 ${escapeHtml(workdir)}" title="${escapeHtml(workdirPath)}">/${escapeHtml(workdir || "目录")}</button>`
            : `<span class="codex-usage-hud-session-ranking-workdir" title="未记录工作目录">/${escapeHtml(workdir || "--")}</span>`;
          return `
            <div class="codex-usage-hud-session-ranking-row" data-selected="${String(sessionId === selectedSessionId)}">
              <${tag}${actionAttrs} class="codex-usage-hud-background-event codex-usage-hud-session-ranking-select">
                <span class="codex-usage-hud-background-event-title" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
                <span class="codex-usage-hud-session-ranking-cost" title="${escapeHtml(`${rankLabel} #${index + 1} ${cost}`)}">#${index + 1} ${escapeHtml(cost)}</span>
                <span class="codex-usage-hud-session-ranking-meta">${workdirHtml}<span class="codex-usage-hud-session-ranking-time" title="${escapeHtml(timestamp)}">${escapeHtml(timestamp)}</span></span>
                <span class="codex-usage-hud-session-ranking-model" title="${escapeHtml(modelText)}">${escapeHtml(modelText)}${costCoverageNote ? ` · ${escapeHtml(costCoverageNote)}` : ""}</span>
                <span class="codex-usage-hud-background-event-totals"><strong title="${escapeHtml(tokens)}">${escapeHtml(tokens)}</strong><span title="${escapeHtml(cacheText)}">${escapeHtml(cacheText)}</span></span>
              </${tag}>
            </div>
          `;
        }).join("");
      }

      function requestUsageInsightsRefresh({ force = false } = {}) {
        if (usageInsightsState.refreshRequestId) return false;
        const requestId = typedSettingsRequestId("usage-insights");
        usageInsightsState.refreshRequestId = requestId;
        usageInsightsState.error = "";
        const submitted = submitSettingsCommand(
          { action: "usageInsightsRefresh", requestId },
          "正在刷新会话排行...",
          { preserveOverlay: true },
        );
        if (!submitted) {
          usageInsightsState.refreshRequestId = "";
          usageInsightsState.error = "无法提交会话排行刷新请求。";
        }
        return submitted;
      }

      function rerenderUsageInsightsIfVisible({ cleanupProgress = false } = {}) {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden) return;
        // Never force the storage tab from payload ticks — only rewrite the body
        // when the user is already on 会话管理. Other tabs stay usable during scans.
        if (settingsActiveTab === "storage") {
          if (cleanupProgress) {
            // Progress may arrive once per cache definition. Coalesce it to a
            // maximum of five body rebuilds per second so long scans keep their
            // animation, scroll position, and input focus stable.
            scheduleStoragePanelRefresh({ throttleMs: 200 });
            return;
          }
          refreshStoragePanelIfVisible();
          return;
        }
        if (
          settingsActiveTab === "backgroundUsage"
          && backgroundUsageSessionRankingMode()
        ) {
          syncBackgroundUsagePanel();
        }
      }

      function applyUsageInsightsPayload(_root, payload) {
        const data = payload?.usageInsights;
        if (!data || typeof data !== "object") return;
        usageInsightsState.data = data;
        const state = String(data.state || "").toLowerCase();
        const responseRequestId = String(data.requestId || "");
        if (
          state !== "loading"
          && (!usageInsightsState.refreshRequestId || responseRequestId === usageInsightsState.refreshRequestId)
        ) {
          usageInsightsState.refreshRequestId = "";
        }
        usageInsightsState.error = String(data.error || "");
        rerenderUsageInsightsIfVisible();
      }

    function install() {
      return true;
    }

    function apply(root, payload) {
      return applyUsageInsightsPayload(root, payload || {});
    }

    function dispose() {
      return true;
    }

    return {
      install,
      apply,
      dispose,
      usageInsightsFromPayload,
      usageInsightsFormatTokens,
      usageInsightsFormatCost,
      usageInsightsFormatRatio,
      usageInsightsRangeItem,
      usageInsightsRangeData,
      usageInsightsRankLabel,
      usageInsightsSessionModelNames,
      usageInsightsSessionModelSummary,
      usageInsightsRankingRowsHtml,
      requestUsageInsightsRefresh,
      rerenderUsageInsightsIfVisible,
      applyUsageInsightsPayload,
    };
  }

  const usageInsightsDomain = ctx.domains.register(
    "usage_insights",
    createUsageInsightsDomain(ctx, shared),
  );
  const {
    usageInsightsFromPayload,
    usageInsightsFormatTokens,
    usageInsightsFormatCost,
    usageInsightsFormatRatio,
    usageInsightsRangeItem,
    usageInsightsRangeData,
    usageInsightsRankLabel,
    usageInsightsSessionModelNames,
    usageInsightsSessionModelSummary,
    usageInsightsRankingRowsHtml,
    requestUsageInsightsRefresh,
    rerenderUsageInsightsIfVisible,
    applyUsageInsightsPayload,
  } = usageInsightsDomain;
"""

__all__ = ["TEXT"]
