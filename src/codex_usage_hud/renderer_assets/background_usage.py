"""Renderer background usage domain asset."""

TEXT = r"""
  function createBackgroundUsageDomain(ctx, shared) {
      let backgroundUsagePolicySeq = 0;
      let backgroundUsagePolicyTimeoutId = 0;

      function clearBackgroundUsagePolicyTimeout() {
        ctx.lifecycle.clearTimeout(backgroundUsagePolicyTimeoutId);
        backgroundUsagePolicyTimeoutId = 0;
      }

      function scheduleBackgroundUsagePolicyTimeout(requestId) {
        clearBackgroundUsagePolicyTimeout();
        backgroundUsagePolicyTimeoutId = ctx.lifecycle.timeout(
          "background_usage_policy",
          () => {
            if (requestId !== backgroundUsageState.policyRequestId) return;
            backgroundUsagePolicyTimeoutId = 0;
            backgroundUsageState.policyRequestId = "";
            backgroundUsageState.policyPending = false;
            backgroundUsageState.policyLoading = false;
            backgroundUsageState.policyError = "后台任务控制请求超时，请重试。";
            syncBackgroundUsagePanel();
          },
          backgroundUsageRequestTimeoutMs,
        );
      }

      function backgroundUsagePolicyKey(detail) {
        return `${String(detail?.featureKey || "")}\u0000${String(detail?.eventId || "")}`;
      }

      function backgroundUsageBridgeUrl() {
        return String(currentPayload()?.backgroundUsageBridgeUrl || "").trim();
      }

      function backgroundUsageEndpoint(suffix = "") {
        const bridge = backgroundUsageBridgeUrl();
        if (!bridge) return null;
        try {
          const url = new URL(bridge, window.location.href);
          url.pathname = url.pathname.replace(
            /\/background-usage\/?$/,
            `/background-usage${suffix}`,
          );
          return url;
        } catch (_) {
          return null;
        }
      }

      function submitBackgroundUsageCommand(action, payload = {}) {
        if (!ctx.bindings.available(settingsCommandBindingName)) return "";
        const requestId = `background-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        try {
          ctx.bindings.send(settingsCommandBindingName, {
            id: requestId,
            createdAt: Date.now(),
            action,
            ...payload,
            requestId,
          });
          return requestId;
        } catch (error) {
          backgroundUsageState.error = `用量总览命令提交失败：${error?.message || error}`;
          return "";
        }
      }

      // Legacy contract marker: function readThemeStorage

      function backgroundUsageFormatCost(value) {
        if (value === null || value === undefined || !Number.isFinite(Number(value))) {
          return "估算不可用";
        }
        const amount = Number(value);
        const digits = amount >= 10 ? 2 : amount >= 1 ? 3 : 6;
        return `估算 $${amount.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "")}`;
      }

      function normalizeBackgroundUsageRange(value) {
        const normalized = String(value || "today").trim().toLowerCase();
        return new Set(["today", "7d", "30d", "all"]).has(normalized)
          ? normalized
          : "today";
      }

      function renderBackgroundUsageNotification(root, payload) {
        const notification = payload?.backgroundUsageNotification;
        const count = Math.max(0, Number(notification?.count || 0));
        const eventId = String(notification?.eventId || "").trim();
        const range = normalizeBackgroundUsageRange(notification?.range);
        const visible = count > 0 && !!eventId;
        root.querySelectorAll('[data-action="background-usage-open-notification"]').forEach((button) => {
          button.dataset.visible = String(visible);
          button.dataset.eventId = visible ? eventId : "";
          button.dataset.backgroundRange = visible ? range : "today";
          button.setAttribute("aria-hidden", String(!visible));
          button.tabIndex = visible ? 0 : -1;
          const label = visible
            ? `${count.toLocaleString()} 条未查看后台用量，打开用量总览`
            : "后台用量提醒";
          button.title = label;
          button.setAttribute("aria-label", label);
          const badge = button.querySelector('[data-field="backgroundUsageNotificationCount"]');
          if (badge) {
            badge.hidden = !visible;
            badge.textContent = count > 99 ? "99+" : String(count);
          }
        });
      }

      function markBackgroundUsageEventViewed(eventId) {
        const normalized = String(eventId || "").trim();
        if (!normalized) return;
        const data = backgroundUsageState.data;
        if (data && Array.isArray(data.events)) {
          backgroundUsageState.data = {
            ...data,
            events: data.events.map((event) => (
              String(event?.eventId || "") === normalized
                ? { ...event, unread: false }
                : event
            )),
          };
        }
        if (
          backgroundUsageState.detail
          && String(backgroundUsageState.detail?.eventId || "") === normalized
        ) {
          backgroundUsageState.detail = {
            ...backgroundUsageState.detail,
            unread: false,
          };
        }
      }

      function backgroundUsageTime(value, { compact = false } = {}) {
        const parsed = new Date(String(value || ""));
        if (Number.isNaN(parsed.getTime())) return "--";
        return parsed.toLocaleString([], compact
          ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
          : { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
      }

      function backgroundUsageRedactedPrompt(value) {
        return String(value || "")
          .replace(/([A-Za-z]:\\Users\\)[^\\/\r\n]+/gi, "$1[user]")
          .replace(/(\/Users\/)[^\/\r\n]+/g, "$1[user]")
          .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]");
      }

      function backgroundUsageWorkdirAssociationText(value) {
        const normalized = String(value || "").trim();
        return normalized === "verified_session"
          ? "已验证关联会话"
          : normalized === "log_observed"
          ? "仅日志观察"
          : "未记录";
      }

      function backgroundUsageEventHtml(event) {
        const eventId = String(event?.eventId || "").trim();
        const selected = String(event?.eventId || "") === backgroundUsageState.selectedEventId;
        const unread = event?.unread === true;
        const models = Array.isArray(event?.models) ? event.models.filter(Boolean) : [];
        const modelText = models.join(" + ") || "未知模型";
        const eventTime = backgroundUsageTime(event?.lastSeenAt, { compact: true });
        const eventTimeTitle = backgroundUsageTime(event?.lastSeenAt);
        return `
          <div class="codex-usage-hud-background-event-row" data-selected="${selected}">
            <button type="button" class="codex-usage-hud-background-event"
              data-action="background-usage-select" data-event-id="${escapeHtml(eventId)}" data-selected="${selected}" data-unread="${unread}">
              ${unread ? '<span class="codex-usage-hud-background-unread-dot" aria-label="未查看"></span>' : ""}
              <span class="codex-usage-hud-background-event-head">
                <span class="codex-usage-hud-background-event-title">${escapeHtml(event?.featureLabel || "未知后台任务")}</span>
                <span class="codex-usage-hud-background-status" title="${escapeHtml(eventTimeTitle)}">${escapeHtml(eventTime)}</span>
              </span>
              <span class="codex-usage-hud-background-event-meta">${escapeHtml(modelText)}</span>
              <span class="codex-usage-hud-background-event-totals">
                <strong>${escapeHtml(humanizeTokens(event?.totalTokens || 0))} tokens</strong>
                <span>${Number(event?.requestCount || 0).toLocaleString()} 次请求</span>
                <span>${escapeHtml(backgroundUsageFormatCost(event?.estimatedCostUsd))}</span>
              </span>
            </button>
          </div>
        `;
      }

      function backgroundUsageDetailHtml(detail) {
        if (backgroundUsageState.detailLoading && !detail) {
          return '<div class="codex-usage-hud-background-empty">正在读取请求明细...</div>';
        }
        if (!detail || typeof detail !== "object") {
          return '<div class="codex-usage-hud-background-empty">选择一项后台任务查看请求明细。</div>';
        }
        const models = Array.isArray(detail.models) ? detail.models.filter(Boolean) : [];
        const requests = Array.isArray(detail.requests) ? detail.requests : [];
        const detailTime = backgroundUsageTime(detail.lastSeenAt, { compact: true });
        const detailTimeTitle = backgroundUsageTime(detail.lastSeenAt);
        const rawPrompt = String(detail.prompt || "");
        const redactedPrompt = backgroundUsageRedactedPrompt(rawPrompt);
        const promptText = backgroundUsageState.promptExpanded
          ? rawPrompt
          : `${redactedPrompt.slice(0, 520)}${redactedPrompt.length > 520 ? "\n…" : ""}`;
        const requestRows = requests.map((request, index) => `
          <div class="codex-usage-hud-background-request">
            <span>${escapeHtml(backgroundUsageTime(request?.occurredAt, { compact: true }))}</span>
            <span class="codex-usage-hud-background-request-endpoint">POST ${escapeHtml(request?.endpoint || "/responses")}</span>
            <span title="${escapeHtml(request?.model || "")}">${escapeHtml(request?.model || "未知模型")}</span>
            <strong>${escapeHtml(humanizeTokens(request?.totalTokens || 0))}</strong>
            <span>${escapeHtml(backgroundUsageFormatCost(request?.estimatedCostUsd))}</span>
            <span class="codex-usage-hud-background-request-index">#${index + 1}</span>
          </div>
        `).join("");
        const processText = String(detail.processUuid || "");
        const threadText = String(detail.threadId || detail.eventId || "");
        const eventId = String(detail.eventId || "").trim();
        const workdir = String(detail.cwd || "").trim();
        const workdirAvailable = detail?.workdirAvailable === true;
        const workdirAssociation = backgroundUsageWorkdirAssociationText(detail?.workdirAssociation);
        const policy = backgroundUsageState.policy && typeof backgroundUsageState.policy === "object" ? backgroundUsageState.policy : null;
        const capability = String(policy?.capability || "");
        const verification = String(policy?.verificationState || "");
        const canDisable = policy?.canDisable === true;
        const canEnable = policy?.canEnable === true;
        const disabled = capability === "unsupported" || capability === "unknown";
        const desiredState = String(policy?.desiredState || "enabled");
        const effectiveState = backgroundUsagePolicyEffectiveState(policy);
        const isDisabled = effectiveState === "disabled";
        const actionState = backgroundUsagePolicyTargetState(policy);
        const displayDisabled = isDisabled;
        const label = backgroundUsageState.policyPending
          ? "正在提交..."
          : actionState === "enabled"
          ? "启用此类任务"
          : "禁用此类任务";
        const policyButton = policy && !disabled && (canDisable || canEnable)
          ? `<button type="button" class="codex-usage-hud-background-policy-switch" role="switch" aria-checked="${displayDisabled ? "false" : "true"}" aria-label="${escapeHtml(label)}" data-action="background-usage-policy" data-feature-key="${escapeHtml(String(detail.featureKey || "unknown"))}" data-event-id="${escapeHtml(eventId)}" title="${escapeHtml(String(policy?.message || ""))}" ${backgroundUsageState.policyPending ? "disabled" : ""}><span class="codex-usage-hud-background-policy-switch-track" aria-hidden="true"><span class="codex-usage-hud-background-policy-switch-thumb"></span></span><span class="codex-usage-hud-background-policy-switch-label">${escapeHtml(label)}</span></button>`
          : "";
        const policyMessage = backgroundUsageState.policyLoading
          ? "正在读取后台任务控制能力..."
          : backgroundUsageState.policyError || String(policy?.message || "");
        const workdirHtml = workdir && eventId && workdirAvailable
          ? `<button type="button" class="codex-usage-hud-background-workdir-link" data-action="background-usage-open-workdir" data-event-id="${escapeHtml(eventId)}" aria-label="打开记录时运行目录 ${escapeHtml(workdir)}" title="记录时运行目录 · ${escapeHtml(workdirAssociation)} · ${escapeHtml(workdir)}">${escapeHtml(workdir)}</button>`
          : `<strong title="${workdir ? `记录时运行目录 · ${escapeHtml(workdirAssociation)} · 当前目录不可用` : "未记录运行目录"}">${escapeHtml(workdir || "--")}</strong>`;
        const detailLabel = String(detail.featureLabel || "未知后台任务");
        const detailEnLabel = String(detail.featureEnLabel || "").trim();
        const detailPurpose = String(detail.featurePurpose || "").trim();
        const detailTitle = detailEnLabel
          ? `${detailLabel}（${detailEnLabel}）`
          : detailLabel;
        const detailSubText = detailPurpose
          ? `由 Codex App 官方 agent 工具在后台发起的请求 · 官方作用：${detailPurpose}`
          : "由 Codex App 官方 agent 工具在后台发起的请求";
        return `
          <div class="codex-usage-hud-background-detail-head">
            <div>
              <h3>${escapeHtml(detailTitle)}</h3>
              <span class="codex-usage-hud-background-detail-sub">${escapeHtml(detailSubText)}</span>
            </div>
            ${policyButton}
          </div>
          <div class="codex-usage-hud-background-detail-grid">
            <div><span>模型</span><strong>${escapeHtml(models.join(" + ") || "未知")}</strong></div>
            <div><span>请求</span><strong>${Number(detail.requestCount || 0).toLocaleString()} 次</strong></div>
            <div title="${escapeHtml(threadText)}"><span>线程</span><strong>${escapeHtml(threadText ? `…${threadText.slice(-12)}` : "--")}</strong></div>
            <div title="${escapeHtml(processText)}"><span>进程</span><strong>${escapeHtml(processText.split(":").slice(0, 2).join(":") || "--")}</strong></div>
            <div class="codex-usage-hud-background-detail-wide"><span>时段</span><strong>${escapeHtml(backgroundUsageTime(detail.firstSeenAt))} - ${escapeHtml(backgroundUsageTime(detail.lastSeenAt))}</strong></div>
            <div class="codex-usage-hud-background-detail-wide" title="${escapeHtml(workdir)}"><span>记录时运行目录 · ${escapeHtml(workdirAssociation)}</span>${workdirHtml}</div>
          </div>
          ${policyMessage ? `<div class="codex-usage-hud-background-policy-message" role="status">${escapeHtml(policyMessage)}</div>` : ""}
          <section class="codex-usage-hud-background-requests">
            <div class="codex-usage-hud-background-section-title">请求明细 <span>${requests.length}</span></div>
            <div class="codex-usage-hud-background-request-list">${requestRows || '<div class="codex-usage-hud-background-empty">没有可用请求明细。</div>'}</div>
          </section>
          ${rawPrompt ? `
            <section class="codex-usage-hud-background-prompt">
              <div class="codex-usage-hud-background-section-title">
                <span>请求内容</span>
                <span>
                  <button type="button" class="codex-usage-hud-settings-link" data-action="background-usage-toggle-prompt">${backgroundUsageState.promptExpanded ? "收起原文" : "展开原文"}</button>
                  <button type="button" class="codex-usage-hud-settings-link" data-action="background-usage-copy-prompt">复制原文</button>
                </span>
              </div>
              <pre data-expanded="${backgroundUsageState.promptExpanded}">${escapeHtml(promptText)}</pre>
            </section>
          ` : ""}
        `;
      }

      function backgroundUsageSessionRankingMode() {
        const key = String(backgroundUsageState.feature || "");
        if (key === "__session_top_usage__") return "usage";
        if (key === "__session_top_cost__") return "cost";
        return "";
      }

      function backgroundUsageSessionRankingRange() {
        if (backgroundUsageState.range === "today") return "today";
        if (backgroundUsageState.range === "30d") return "month";
        return "week";
      }

      function backgroundUsageSessionRankingDetailHtml(session, mode) {
        if (!session || typeof session !== "object") {
          return '<div class="codex-usage-hud-background-empty">选择一个会话查看用量汇总。</div>';
        }
        const sessionId = String(session?.id || session?.sessionId || "");
        const actionable = (session?.actionable === true || session?.canActivate === true) && !!sessionId;
        const coverage = session?.costCoverage && typeof session.costCoverage === "object"
          ? session.costCoverage
          : {};
        const totalEvents = Math.max(0, Number(coverage?.totalEventCount || 0));
        const pricedEvents = Math.max(0, Number(coverage?.pricedEventCount || 0));
        const completeCost = coverage?.hasCompleteCost !== false && (!totalEvents || pricedEvents >= totalEvents);
        const title = usageInsightsRankLabel(session, "sessions");
        const latestEventAt = String(session?.latestEventAt || "");
        const workdir = String(session?.workdirName || "").trim();
        const workdirPath = String(session?.workdir || "").trim();
        const workdirText = workdir || workdirPath || "--";
        const workdirHtml = workdirPath && sessionId
          ? `<button type="button" class="codex-usage-hud-background-workdir-link" data-action="usage-insights-open-workdir" data-usage-session-id="${escapeHtml(sessionId)}" aria-label="打开工作目录 ${escapeHtml(workdirPath)}" title="${escapeHtml(workdirPath)}">${escapeHtml(workdirText)}</button>`
          : `<strong>${escapeHtml(workdirText)}</strong>`;
        const modelNames = usageInsightsSessionModelNames(session);
        const modelText = modelNames.join("、") || "未知模型";
        const costText = usageInsightsFormatCost(session?.costUsd);
        const costNote = completeCost ? "HUD 本地估算" : (session?.costUsd == null ? "费用不可估" : "费用部分可估");
        return `
          <div class="codex-usage-hud-background-detail-head">
            <div>
              <h3>${escapeHtml(title)}</h3>
              <span class="codex-usage-hud-background-detail-sub">会话用量 · 本地聚合</span>
            </div>
            ${actionable ? '<button type="button" class="codex-usage-hud-settings-action" data-action="usage-insights-session" data-usage-session-id="' + escapeHtml(sessionId) + '" data-target-title="' + escapeHtml(title) + '" data-workdir="' + escapeHtml(workdir) + '">打开会话</button>' : '<span class="codex-usage-hud-background-status">仅统计</span>'}
          </div>
          <div class="codex-usage-hud-background-detail-grid">
            <div><span>Provider</span><strong>${escapeHtml(session?.provider ? providerRegistryDisplayName(currentPayload()?.settings, session.provider) : "未知")}</strong></div>
            <div><span>Tokens</span><strong>${escapeHtml(usageInsightsFormatTokens(session?.tokens ?? session?.totalTokens))}</strong></div>
            <div><span>输入</span><strong>${escapeHtml(usageInsightsFormatTokens(session?.inputTokens))}</strong></div>
            <div><span>缓存命中</span><strong>${escapeHtml(usageInsightsFormatRatio(session?.cacheRatio))}</strong></div>
            <div><span>金额</span><strong>${escapeHtml(costText)}</strong></div>
            <div><span>费用覆盖</span><strong>${escapeHtml(costNote)}</strong></div>
            <div><span>已计价请求</span><strong>${Math.min(pricedEvents, totalEvents).toLocaleString()} / ${totalEvents.toLocaleString()}</strong></div>
            <div><span>最近活动</span><strong>${escapeHtml(latestEventAt ? backgroundUsageTime(latestEventAt, { compact: true }) : "--")}</strong></div>
            <div class="codex-usage-hud-background-detail-full" title="${escapeHtml(modelText)}"><span>使用模型${modelNames.length > 1 ? `（${modelNames.length} 个）` : ""}</span><strong>${escapeHtml(modelText)}</strong></div>
            <div class="codex-usage-hud-background-detail-full" title="${escapeHtml(workdirPath || workdir)}"><span>工作目录</span>${workdirHtml}</div>
          </div>
          <section class="codex-usage-hud-background-requests">
            <div class="codex-usage-hud-background-section-title"><span>${escapeHtml(mode === "cost" ? "金额排名" : "用量排名")}</span><span>${escapeHtml(mode === "cost" ? costText : `${usageInsightsFormatTokens(session?.tokens ?? session?.totalTokens)} tokens`)}</span></div>
            <div class="codex-usage-hud-insights-meta">会话详情只展示本地聚合统计；打开会话后可继续查看原对话。</div>
          </section>
        `;
      }

      function backgroundUsageFeatureOptionsHtml(featureOptions) {
        const sessionRankingOptions = [
          ["__session_top_usage__", "Top10会话用量"],
          ["__session_top_cost__", "Top10会话金额"],
        ];
        const selected = String(backgroundUsageState.feature || "");
        const reserved = new Set(sessionRankingOptions.map(([key]) => key));
        const backgroundOptions = featureOptions
          .filter((item) => !reserved.has(String(item?.key || "")))
          .map((item) => `<option value="${escapeHtml(item?.key || "")}" ${selected === String(item?.key || "") ? "selected" : ""}>${escapeHtml(item?.label || item?.key || "")}</option>`)
          .join("");
        const sessionOptions = sessionRankingOptions
          .map(([key, label]) => `<option value="${key}" ${selected === key ? "selected" : ""}>${label}</option>`)
          .join("");
        return `<option value="">全部后台功能</option><optgroup label="后台任务">${backgroundOptions || '<option value="" disabled>暂无后台任务功能</option>'}</optgroup><optgroup label="用户会话排行">${sessionOptions}</optgroup>`;
      }

      function backgroundUsageSessionRankingPanelHtml(featureOptions, modelOptions) {
        const mode = backgroundUsageSessionRankingMode();
        const data = usageInsightsFromPayload();
        const state = String(data?.state || data?.status || "").toLowerCase();
        const range = backgroundUsageSessionRankingRange();
        const scoped = data ? usageInsightsRangeData(data, range) : {};
        const totals = scoped?.totals || {};
        const coverage = scoped?.costCoverage || {};
        const totalEvents = Math.max(0, Number(coverage?.totalEventCount || 0));
        const pricedEvents = Math.max(0, Number(coverage?.pricedEventCount || 0));
        const completeCost = coverage?.hasCompleteCost !== false && (!totalEvents || pricedEvents >= totalEvents);
        const rankingKey = mode === "cost" ? "topSessionsByCost" : "topSessionsByUsage";
        const sessions = Array.isArray(scoped?.[rankingKey]) ? scoped[rankingKey] : [];
        const selectedSessionId = sessions.some((item) => String(item?.id || item?.sessionId || "") === backgroundUsageState.selectedSessionId)
          ? backgroundUsageState.selectedSessionId
          : String(sessions[0]?.id || sessions[0]?.sessionId || "");
        const selectedSession = sessions.find((item) => String(item?.id || item?.sessionId || "") === selectedSessionId) || null;
        const title = mode === "cost" ? "Top10金额" : "Top10用量";
        const rankingRows = usageInsightsRankingRowsHtml(
          sessions,
          "sessions",
          {
            limit: 10,
            metric: mode === "cost" ? "cost" : "tokens",
            selectedSessionId,
            sessionAction: "background-usage-session-select",
          },
        );
        const error = String(data?.error || usageInsightsState.error || "");
        const loading = state === "loading" || (!data && !!usageInsightsState.refreshRequestId);
        const listHtml = loading
          ? '<div class="codex-usage-hud-background-empty" role="status">正在汇总会话排行...</div>'
          : error || state === "error" || state === "failed"
            ? `<div class="codex-usage-hud-background-empty" data-kind="error" role="alert">${escapeHtml(error || "会话排行生成失败，请重试。")}</div>`
            : `<div class="codex-usage-hud-background-event-list codex-usage-hud-session-ranking-list">${rankingRows || `<div class="codex-usage-hud-background-empty">${mode === "cost" ? "暂无可估金额的会话。" : "暂无会话用量数据。"}</div>`}</div>`;
        const sessionCount = Number(totals?.sessionCount || 0);
        const primaryValue = mode === "cost"
          ? usageInsightsFormatCost(totals?.costUsd)
          : usageInsightsFormatTokens(totals?.tokens ?? totals?.totalTokens);
        const primaryMeta = mode === "cost"
          ? (completeCost ? "HUD 本地估算" : "部分会话可估算")
          : "已确认会话 token";
        const rankingNote = mode === "cost"
          ? "仅按有本地估算费用的会话排序"
          : "按已确认的会话 token 排序";
        return `
          <div class="codex-usage-hud-background" data-background-usage-root="true"
            data-background-usage-filter-key="${escapeHtml(backgroundUsageFilterKey())}"
            aria-busy="${loading}">
            <div class="codex-usage-hud-background-metrics">
              <div><span>${escapeHtml(title)}</span><strong>${escapeHtml(primaryValue)}</strong><small>${escapeHtml(primaryMeta)}</small></div>
              <div><span>Tokens</span><strong>${escapeHtml(usageInsightsFormatTokens(totals?.tokens ?? totals?.totalTokens))}</strong><small>本地会话统计</small></div>
              <div><span>会话</span><strong>${sessionCount.toLocaleString()}</strong><small>最多展示 10 项</small></div>
              <div><span>缓存命中</span><strong>${escapeHtml(usageInsightsFormatRatio(totals?.cacheRatio))}</strong><small>${escapeHtml(data?.generatedAt ? `更新于 ${backgroundUsageTime(data.generatedAt, { compact: true })}` : "本地聚合")}</small></div>
            </div>
            <div class="codex-usage-hud-background-toolbar">
              <div class="codex-usage-hud-background-range" role="group" aria-label="会话排行范围">
                ${[["today", "今天"], ["7d", "近 7 天"], ["30d", "近 30 天"]].map(([key, label]) => `<button type="button" data-action="background-usage-range" data-background-range="${key}" data-active="${backgroundUsageState.range === key}">${label}</button>`).join("")}
              </div>
              <select data-background-usage-filter="feature" aria-label="功能筛选">
                ${backgroundUsageFeatureOptionsHtml(featureOptions)}
              </select>
              <select data-background-usage-filter="model" aria-label="模型筛选" disabled title="会话排行不按后台模型筛选">
                <option value="">全部模型</option>
                ${modelOptions.map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join("")}
              </select>
            </div>
            <div class="codex-usage-hud-background-master-detail" data-session-ranking="true">
              <section class="codex-usage-hud-background-history">
                <div class="codex-usage-hud-background-section-title"><span>${escapeHtml(title)}</span><span>${sessions.length} 项</span></div>
                <div class="codex-usage-hud-insights-meta codex-usage-hud-session-ranking-note">${escapeHtml(rankingNote)}</div>
                ${listHtml}
              </section>
              <section class="codex-usage-hud-background-detail" data-session-ranking-detail="true">
                ${loading || error || state === "error" || state === "failed"
                  ? '<div class="codex-usage-hud-background-empty">等待会话排行就绪。</div>'
                  : backgroundUsageSessionRankingDetailHtml(selectedSession, mode)}
              </section>
            </div>
          </div>
        `;
      }

      function backgroundUsagePanelHtml() {
        const data = backgroundUsageState.data;
        const summary = data?.summary && typeof data.summary === "object" ? data.summary : {};
        const events = Array.isArray(data?.events) ? data.events : [];
        const filters = data?.filters && typeof data.filters === "object" ? data.filters : {};
        const featureOptions = Array.isArray(filters.features) ? filters.features : [];
        const modelOptions = Array.isArray(filters.models) ? filters.models : [];
        if (backgroundUsageSessionRankingMode()) {
          return backgroundUsageSessionRankingPanelHtml(featureOptions, modelOptions);
        }
        if (!backgroundUsageBridgeUrl()) {
          return '<div class="codex-usage-hud-background" data-background-usage-root="true"><div class="codex-usage-hud-background-empty">用量总览当前不可用。</div></div>';
        }
        const modelSummary = Array.isArray(summary.models) && summary.models.length
          ? summary.models.join(" + ")
          : "--";
        const costNote = summary.costComplete === false ? "部分模型缺少价格" : "HUD 估算";
        return `
          <div class="codex-usage-hud-background" data-background-usage-root="true"
            data-background-usage-filter-key="${escapeHtml(backgroundUsageFilterKey())}"
            aria-busy="${backgroundUsageState.loading}">
            <div class="codex-usage-hud-background-metrics">
              <div><span>筛选费用</span><strong>${escapeHtml(backgroundUsageFormatCost(summary.estimatedCostUsd))}</strong><small>${escapeHtml(costNote)}</small></div>
              <div><span>Tokens</span><strong>${escapeHtml(humanizeTokens(summary.totalTokens || 0))}</strong><small>本机日志值</small></div>
              <div><span>后台任务</span><strong>${Number(summary.eventCount || 0).toLocaleString()}</strong><small>${Number(summary.requestCount || 0).toLocaleString()} 次请求</small></div>
              <div title="${escapeHtml(modelSummary)}"><span>使用模型</span><strong>${escapeHtml(modelSummary)}</strong><small>${Array.isArray(summary.models) ? summary.models.length : 0} 个模型</small></div>
            </div>
            <div class="codex-usage-hud-background-toolbar">
              <div class="codex-usage-hud-background-range" role="group" aria-label="日期范围">
                ${[["today", "今天"], ["7d", "近 7 天"], ["30d", "近 30 天"], ["all", "全部"]].map(([key, label]) => `<button type="button" data-action="background-usage-range" data-background-range="${key}" data-active="${backgroundUsageState.range === key}">${label}</button>`).join("")}
              </div>
              <select data-background-usage-filter="feature" aria-label="功能筛选">
                ${backgroundUsageFeatureOptionsHtml(featureOptions)}
              </select>
              <select data-background-usage-filter="model" aria-label="模型筛选">
                <option value="">全部模型</option>
                ${modelOptions.map((model) => `<option value="${escapeHtml(model)}" ${backgroundUsageState.model === String(model) ? "selected" : ""}>${escapeHtml(model)}</option>`).join("")}
              </select>
            </div>
            ${backgroundUsageState.error ? `<div class="codex-usage-hud-background-error">${escapeHtml(backgroundUsageState.error)}</div>` : ""}
            <div class="codex-usage-hud-background-master-detail">
              <section class="codex-usage-hud-background-history">
                <div class="codex-usage-hud-background-section-title"><span>后台任务历史</span><span>${events.length} 项</span></div>
                <div class="codex-usage-hud-background-event-list">
                  ${events.map(backgroundUsageEventHtml).join("") || `<div class="codex-usage-hud-background-empty">${backgroundUsageState.loading ? "正在读取用量总览..." : "当前筛选没有后台任务。"}</div>`}
                </div>
              </section>
              <section class="codex-usage-hud-background-detail"
                data-background-usage-detail-event-id="${escapeHtml(backgroundUsageState.selectedEventId)}"
                data-background-usage-detail-loaded="${!!backgroundUsageState.detail}">
                ${backgroundUsageDetailHtml(backgroundUsageState.detail)}
              </section>
            </div>
          </div>
        `;
      }

      function backgroundUsageFilterKey() {
        return JSON.stringify([
          backgroundUsageState.range,
          backgroundUsageState.feature,
          backgroundUsageState.model,
        ]);
      }

      function captureBackgroundUsageScrollPositions() {
        const modal = document.getElementById(settingsModalId);
        const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
        const panel = body?.querySelector?.('[data-background-usage-root="true"]');
        if (!body || !panel) return;
        const filterKey = String(panel.dataset.backgroundUsageFilterKey || "");
        const history = panel.querySelector(".codex-usage-hud-background-history");
        const detail = panel.querySelector(".codex-usage-hud-background-detail");
        const detailEventId = String(
          detail?.dataset?.backgroundUsageDetailEventId || "",
        );
        if (filterKey) {
          backgroundUsageBodyScrollTops.set(filterKey, Number(body.scrollTop || 0));
          backgroundUsageHistoryScrollTops.set(
            filterKey,
            Number(history?.scrollTop || 0),
          );
        }
        if (
          detailEventId
          && detail?.dataset?.backgroundUsageDetailLoaded === "true"
        ) {
          backgroundUsageDetailScrollTops.set(
            detailEventId,
            Number(detail?.scrollTop || 0),
          );
        }
      }

      function restoreBackgroundUsageScrollPositions() {
        const modal = document.getElementById(settingsModalId);
        const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
        const panel = body?.querySelector?.('[data-background-usage-root="true"]');
        if (!body || !panel) return;
        const filterKey = String(panel.dataset.backgroundUsageFilterKey || "");
        const history = panel.querySelector(".codex-usage-hud-background-history");
        const detail = panel.querySelector(".codex-usage-hud-background-detail");
        const detailEventId = String(
          detail?.dataset?.backgroundUsageDetailEventId || "",
        );
        const apply = () => {
          body.scrollTop = Number(backgroundUsageBodyScrollTops.get(filterKey) || 0);
          if (history) {
            history.scrollTop = Number(
              backgroundUsageHistoryScrollTops.get(filterKey) || 0,
            );
          }
          if (detail) {
            detail.scrollTop = Number(
              backgroundUsageDetailScrollTops.get(detailEventId) || 0,
            );
          }
        };
        apply();
        ctx.lifecycle.frame("background_usage", apply);
      }

      function clearBackgroundUsageRequestTimeout(kind) {
        if (kind === "query") {
          ctx.lifecycle.clearTimeout(backgroundUsageQueryTimeoutId);
          backgroundUsageQueryTimeoutId = 0;
          return;
        }
        ctx.lifecycle.clearTimeout(backgroundUsageDetailTimeoutId);
        backgroundUsageDetailTimeoutId = 0;
      }

      function scheduleBackgroundUsageRequestTimeout(kind, requestId, eventId = "") {
        clearBackgroundUsageRequestTimeout(kind);
        const onTimeout = () => {
          if (kind === "query") {
            if (requestId !== backgroundUsageState.queryRequestId) return;
            backgroundUsageQueryTimeoutId = 0;
            backgroundUsageState.loading = false;
            backgroundUsageState.error = "用量总览读取超时，请重试。";
          } else {
            if (
              requestId !== backgroundUsageState.detailRequestId
              || eventId !== backgroundUsageState.selectedEventId
            ) return;
            backgroundUsageDetailTimeoutId = 0;
            backgroundUsageState.detailLoading = false;
            backgroundUsageState.error = "请求明细读取超时，请重试。";
          }
          syncBackgroundUsagePanel();
        };
        const timeoutId = ctx.lifecycle.timeout(
          "background_usage_request",
          onTimeout,
          backgroundUsageRequestTimeoutMs,
        );
        if (kind === "query") backgroundUsageQueryTimeoutId = timeoutId;
        else backgroundUsageDetailTimeoutId = timeoutId;
      }

      async function fetchBackgroundUsageWithTimeout(url, options = {}) {
        const controller = new AbortController();
        const releaseAbort = ctx.teardown.add(
          "background_usage_fetch",
          () => controller.abort(),
        );
        const timeoutId = ctx.lifecycle.timeout(
          "background_usage_fetch",
          () => controller.abort(),
          backgroundUsageRequestTimeoutMs,
        );
        try {
          return await fetch(url, {
            cache: "no-store",
            ...options,
            signal: controller.signal,
          });
        } finally {
          ctx.lifecycle.clearTimeout(timeoutId);
          releaseAbort(false);
        }
      }

      function syncBackgroundUsagePanel() {
        if (!ctx.lifecycle.active()) return;
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "backgroundUsage") return;
        const body = modal.querySelector(".codex-usage-hud-settings-body");
        if (!body) return;
        captureBackgroundUsageScrollPositions();
        body.innerHTML = backgroundUsagePanelHtml();
        restoreBackgroundUsageScrollPositions();
      }

      async function loadBackgroundUsageDetail(eventId, { markViewed = false } = {}) {
        const normalized = String(eventId || "").trim();
        const url = backgroundUsageEndpoint("/detail");
        if (!normalized || backgroundUsageSessionRankingMode()) return;
        const requestSeq = ++backgroundUsageDetailSeq;
        backgroundUsageState.detailLoading = true;
        backgroundUsageState.promptExpanded = false;
        clearBackgroundUsageRequestTimeout("detail");
        backgroundUsageState.detailRequestId = "";
        syncBackgroundUsagePanel();
        const bindingRequestId = submitBackgroundUsageCommand(
          "backgroundUsageDetail",
          { eventId: normalized, markViewed: markViewed === true },
        );
        if (bindingRequestId) {
          backgroundUsageState.detailRequestId = bindingRequestId;
          scheduleBackgroundUsageRequestTimeout("detail", bindingRequestId, normalized);
          return;
        }
        if (!url) {
          backgroundUsageState.detailLoading = false;
          backgroundUsageState.error ||= "用量总览桥接未连接";
          syncBackgroundUsagePanel();
          return;
        }
        url.searchParams.set("eventId", normalized);
        try {
          if (markViewed) {
            const confirmUrl = backgroundUsageEndpoint("/confirm");
            if (!confirmUrl) throw new Error("用量总览确认桥接未连接");
            const confirmResponse = await fetchBackgroundUsageWithTimeout(
              confirmUrl.toString(),
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ eventId: normalized }),
              },
            );
            const confirmPayload = await confirmResponse.json();
            if (!confirmResponse.ok || confirmPayload?.status !== "ok") {
              throw new Error(confirmPayload?.message || `HTTP ${confirmResponse.status}`);
            }
          }
          const response = await fetchBackgroundUsageWithTimeout(url.toString());
          const payload = await response.json();
          if (!response.ok || payload?.status !== "ok") {
            throw new Error(payload?.message || `HTTP ${response.status}`);
          }
          if (requestSeq !== backgroundUsageDetailSeq || backgroundUsageState.selectedEventId !== normalized) return;
          backgroundUsageState.detail = payload.backgroundUsageDetail || null;
          void loadBackgroundUsagePolicy(backgroundUsageState.detail);
          if (markViewed && backgroundUsageState.detail?.unread === false) {
            markBackgroundUsageEventViewed(normalized);
          }
          backgroundUsageState.error = "";
        } catch (error) {
          if (requestSeq !== backgroundUsageDetailSeq) return;
          backgroundUsageState.error = error?.name === "AbortError"
            ? "请求明细读取超时，请重试。"
            : `请求明细读取失败：${error?.message || error}`;
        } finally {
          if (requestSeq === backgroundUsageDetailSeq) {
            backgroundUsageState.detailLoading = false;
            syncBackgroundUsagePanel();
          }
        }
      }

      function loadBackgroundUsagePolicy(detail) {
        const featureKey = String(detail?.featureKey || "").trim();
        const eventId = String(detail?.eventId || "").trim();
        if (!featureKey || !eventId) return;
        const requestSeq = ++backgroundUsagePolicySeq;
        const policyKey = backgroundUsagePolicyKey(detail);
        clearBackgroundUsagePolicyTimeout();
        backgroundUsageState.policyRequestId = "";
        backgroundUsageState.policy = null;
        backgroundUsageState.policyError = "";
        backgroundUsageState.policyLoading = true;
        syncBackgroundUsagePanel();
        const requestId = submitBackgroundUsageCommand("backgroundUsagePolicyQuery", { featureKey, eventId });
        if (requestId) {
          backgroundUsageState.policyRequestId = requestId;
          scheduleBackgroundUsagePolicyTimeout(requestId);
          return;
        }
        const url = backgroundUsageEndpoint("/policy");
        if (!url) {
          backgroundUsageState.policyLoading = false;
          backgroundUsageState.policyError = "后台任务控制能力当前不可用。";
          syncBackgroundUsagePanel();
          return;
        }
        url.searchParams.set("feature", featureKey); url.searchParams.set("eventId", eventId);
        void fetchBackgroundUsageWithTimeout(url.toString()).then((response) => response.json().then((payload) => ({ response, payload }))).then(({response, payload}) => {
          if (
            requestSeq !== backgroundUsagePolicySeq
            || backgroundUsagePolicyKey(backgroundUsageState.detail) !== policyKey
          ) return;
          backgroundUsageState.policyLoading = false;
          if (response.ok && payload?.status === "ok") backgroundUsageState.policy = payload.backgroundUsagePolicy || null;
          else backgroundUsageState.policyError = payload?.message || "后台任务控制能力读取失败。";
          syncBackgroundUsagePanel();
        }).catch((error) => {
          if (
            requestSeq !== backgroundUsagePolicySeq
            || backgroundUsagePolicyKey(backgroundUsageState.detail) !== policyKey
          ) return;
          backgroundUsageState.policyLoading = false;
          backgroundUsageState.policyError = `后台任务控制能力读取失败：${error?.message || error}`;
          syncBackgroundUsagePanel();
        });
      }

      function backgroundUsagePolicyTargetState(policy) {
        const desiredState = String(policy?.desiredState || "enabled");
        const verification = String(policy?.verificationState || "");
        if (verification === "requires_user_action") {
          return desiredState;
        }
        const effectiveState = backgroundUsagePolicyEffectiveState(policy);
        return effectiveState === "disabled" ? "enabled" : "disabled";
      }

      function backgroundUsagePolicyEffectiveState(policy) {
        const explicitState = String(policy?.effectiveState || "");
        if (explicitState === "enabled" || explicitState === "disabled") {
          return explicitState;
        }
        const desiredState = String(policy?.desiredState || "enabled");
        const verification = String(policy?.verificationState || "");
        if (verification === "verified") return desiredState;
        if (verification === "requires_user_action") {
          return desiredState === "disabled" ? "enabled" : "disabled";
        }
        return "enabled";
      }

      function backgroundUsagePolicyCopy(detail, desiredState) {
        const featureKey = String(detail?.featureKey || "");
        const featureLabel = String(detail?.featureLabel || "后台任务");
        const disabling = desiredState === "disabled";
        const title = `${disabling ? "禁用" : "启用"}“${featureLabel}”`;
        let body = "";
        if (featureKey === "suggestion_safety") {
          body = disabling
            ? "“建议安全检查”没有独立公开开关。继续操作会打开 Codex 设置；关闭上下文建议后，这条建议链路及其后续安全检查请求也会停止，前台会话不受影响。"
            : "“建议安全检查”没有独立公开开关。继续操作会打开 Codex 设置；启用上下文建议后，这条建议链路及其后续安全检查请求也可以恢复，前台会话不受影响。";
        } else if (featureKey === "memory_consolidation") {
          body = disabling
            ? "将关闭 Codex 的 Memories 总开关。以后新的记忆整理后台任务不会启动；已有后台用量和请求明细会保留，不会被删除或重算。"
            : "将开启 Codex 的 Memories 总开关。以后新的记忆整理后台任务可以恢复；已有后台用量和请求明细不会被删除或重算。";
        } else {
          body = `将打开 Codex 设置以${disabling ? "关闭" : "启用"} Suggested prompts。HUD 当前无法自动读取该开关状态，请以 Codex 设置页显示的状态为准。`;
        }
        return {
          title,
          body,
          confirmLabel: disabling ? "确认禁用" : "确认启用",
        };
      }

      function backgroundUsagePolicyConfirm(detail) {
        const policy = backgroundUsageState.policy || {};
        const featureKey = String(detail?.featureKey || "");
        const desiredState = backgroundUsagePolicyTargetState(policy);
        const copy = backgroundUsagePolicyCopy(detail, desiredState);
        const dialog = settingsDialogRoot(); if (!dialog) return;
        closeSettingsConfirm();
        const layer = document.createElement("div"); layer.className = "codex-usage-hud-settings-confirm-layer"; layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `<div class="codex-usage-hud-settings-confirm-card"><div class="codex-usage-hud-settings-confirm-title">${escapeHtml(copy.title)}</div><div class="codex-usage-hud-settings-confirm-body">${escapeHtml(copy.body)}</div><div class="codex-usage-hud-settings-confirm-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="background-usage-policy-cancel">取消</button><button type="button" class="codex-usage-hud-settings-action" data-primary="true" data-action="background-usage-policy-confirm" data-feature-key="${escapeHtml(featureKey)}" data-event-id="${escapeHtml(String(detail?.eventId || ""))}" data-desired-state="${desiredState}">${escapeHtml(copy.confirmLabel)}</button></div></div>`;
        dialog.appendChild(layer);
      }

      function backgroundUsagePolicyNotice(detail, policy) {
        const dialog = settingsDialogRoot(); if (!dialog) return;
        const disabling = String(policy?.desiredState || "disabled") === "disabled";
        const title = disabling ? "Memories 已禁用" : "Memories 已启用";
        const body = String(policy?.message || "设置已写入并立即更新显示状态。部分 Codex 版本可能需要重启后才会完全采用新配置。");
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `<div class="codex-usage-hud-settings-confirm-card"><div class="codex-usage-hud-settings-confirm-title">${escapeHtml(title)}</div><div class="codex-usage-hud-settings-confirm-body">${escapeHtml(body)}</div><div class="codex-usage-hud-settings-confirm-actions"><button type="button" class="codex-usage-hud-settings-action" data-primary="true" data-action="background-usage-policy-cancel">知道了</button></div></div>`;
        dialog.appendChild(layer);
      }

      function applyBackgroundUsagePolicy(featureKey, eventId, desiredState) {
        const requestSeq = ++backgroundUsagePolicySeq;
        const policyKey = `${String(featureKey || "")}\u0000${String(eventId || "")}`;
        clearBackgroundUsagePolicyTimeout();
        backgroundUsageState.policyRequestId = "";
        backgroundUsageState.policyPending = true;
        backgroundUsageState.policyError = desiredState === "disabled"
          ? "正在写入 Codex 禁用设置..."
          : "正在写入 Codex 启用设置...";
        syncBackgroundUsagePanel();
        const command = { featureKey, eventId, desiredState, expectedPolicyRevision: backgroundUsageState.policy?.policyRevision, source: "usage_detail" };
        const requestId = submitBackgroundUsageCommand("backgroundUsagePolicySet", command);
        if (requestId) {
          backgroundUsageState.policyRequestId = requestId;
          scheduleBackgroundUsagePolicyTimeout(requestId);
          return;
        }
        const url = backgroundUsageEndpoint("/policy"); if (!url) { backgroundUsageState.policyPending = false; backgroundUsageState.policyError = "后台任务控制桥接未连接。"; syncBackgroundUsagePanel(); return; }
        void fetchBackgroundUsageWithTimeout(url.toString(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(command) }).then((response) => response.json().then((payload) => ({response, payload}))).then(({response, payload}) => {
          if (
            requestSeq !== backgroundUsagePolicySeq
            || backgroundUsagePolicyKey(backgroundUsageState.detail) !== policyKey
          ) return;
          backgroundUsageState.policyPending = false;
          if (response.ok && payload?.status === "ok") {
            backgroundUsageState.policy = payload.backgroundUsagePolicy || null;
            backgroundUsageState.policyError = String(payload?.backgroundUsagePolicy?.message || "");
            backgroundUsagePolicyNotice(backgroundUsageState.detail, payload.backgroundUsagePolicy);
          } else {
            backgroundUsageState.policyError = payload?.message || "后台任务控制失败。";
          }
          syncBackgroundUsagePanel();
        }).catch((error) => {
          if (
            requestSeq !== backgroundUsagePolicySeq
            || backgroundUsagePolicyKey(backgroundUsageState.detail) !== policyKey
          ) return;
          backgroundUsageState.policyPending = false;
          backgroundUsageState.policyError = `后台任务控制失败：${error?.message || error}`;
          syncBackgroundUsagePanel();
        });
      }

      async function loadBackgroundUsage({ eventId = "", force = false } = {}) {
        if (backgroundUsageSessionRankingMode()) {
          clearBackgroundUsageRequestTimeout("query");
          clearBackgroundUsageRequestTimeout("detail");
          backgroundUsageState.loading = false;
          backgroundUsageState.detailLoading = false;
          const insights = usageInsightsFromPayload();
          const state = String(insights?.state || insights?.status || "").toLowerCase();
          if (force || !insights || state === "idle") {
            requestUsageInsightsRefresh({ force });
          }
          syncBackgroundUsagePanel();
          return;
        }
        const url = backgroundUsageEndpoint();
        const revision = Math.max(0, Number(currentPayload()?.backgroundUsageRevision || 0));
        const filterKey = backgroundUsageFilterKey();
        const requestedEventId = String(eventId || backgroundUsageState.selectedEventId || "").trim();
        if (
          !force
          && backgroundUsageState.data
          && backgroundUsageState.loadedRevision === revision
          && backgroundUsageState.loadedFilterKey === filterKey
        ) {
          if (requestedEventId && requestedEventId !== backgroundUsageState.selectedEventId) {
            backgroundUsageState.selectedEventId = requestedEventId;
            backgroundUsageState.detail = null;
            syncBackgroundUsagePanel();
            await loadBackgroundUsageDetail(requestedEventId);
          }
          return;
        }
        const requestSeq = ++backgroundUsageFetchSeq;
        backgroundUsageState.loading = true;
        backgroundUsageState.error = "";
        clearBackgroundUsageRequestTimeout("query");
        backgroundUsageState.queryRequestId = "";
        backgroundUsageState.queryFilterKey = filterKey;
        syncBackgroundUsagePanel();
        const bindingRequestId = submitBackgroundUsageCommand(
          "backgroundUsageQuery",
          {
            filters: {
              range: backgroundUsageState.range,
              feature: backgroundUsageState.feature,
              model: backgroundUsageState.model,
              eventId: requestedEventId,
            },
          },
        );
        if (bindingRequestId) {
          backgroundUsageState.queryRequestId = bindingRequestId;
          scheduleBackgroundUsageRequestTimeout("query", bindingRequestId);
          return;
        }
        if (!url) {
          backgroundUsageState.loading = false;
          backgroundUsageState.error ||= "用量总览桥接未连接";
          syncBackgroundUsagePanel();
          return;
        }
        url.searchParams.set("range", backgroundUsageState.range);
        if (backgroundUsageState.feature) url.searchParams.set("feature", backgroundUsageState.feature);
        if (backgroundUsageState.model) url.searchParams.set("model", backgroundUsageState.model);
        if (requestedEventId) url.searchParams.set("eventId", requestedEventId);
        try {
          const response = await fetchBackgroundUsageWithTimeout(url.toString());
          const payload = await response.json();
          if (!response.ok || payload?.status !== "ok") {
            throw new Error(payload?.message || `HTTP ${response.status}`);
          }
          if (requestSeq !== backgroundUsageFetchSeq) return;
          backgroundUsageState.data = payload.backgroundUsage || null;
          backgroundUsageState.loadedRevision = revision;
          backgroundUsageState.loadedFilterKey = filterKey;
          backgroundUsageState.selectedEventId = String(
            payload?.backgroundUsage?.selectedEventId || requestedEventId || ""
          );
          backgroundUsageState.detail = null;
          backgroundUsageState.error = "";
          syncBackgroundUsagePanel();
          if (backgroundUsageState.selectedEventId) {
            await loadBackgroundUsageDetail(backgroundUsageState.selectedEventId);
          }
        } catch (error) {
          if (requestSeq !== backgroundUsageFetchSeq) return;
          backgroundUsageState.error = error?.name === "AbortError"
            ? "用量总览读取超时，请重试。"
            : `用量总览读取失败：${error?.message || error}`;
          backgroundUsageState.data = null;
          backgroundUsageState.detail = null;
          syncBackgroundUsagePanel();
        } finally {
          if (requestSeq === backgroundUsageFetchSeq) {
            backgroundUsageState.loading = false;
            syncBackgroundUsagePanel();
          }
        }
      }

      function backgroundUsageSelectedDetail(responsePayload, eventId) {
        const detail = responsePayload?.selectedDetail;
        if (!detail || typeof detail !== "object") return null;
        const detailEventId = String(detail.eventId || "").trim();
        return detailEventId && detailEventId === eventId ? detail : null;
      }

      function applyBackgroundUsagePayload(root, payload) {
        renderBackgroundUsageNotification(root, payload || {});
        const response = payload?.settingsCommandStatus?.backgroundUsageResponse;
        const openEventId = String(
          payload?.settingsCommandStatus?.backgroundUsageOpenEventId || ""
        ).trim();
        if (response && typeof response === "object") {
          const kind = String(response.kind || "");
          const requestId = String(response.requestId || "");
          const responseError = String(response.error || "");
          if (kind === "query") {
            if (requestId !== backgroundUsageState.queryRequestId) return;
            clearBackgroundUsageRequestTimeout("query");
            backgroundUsageState.loading = false;
            backgroundUsageState.data = response.payload || null;
            backgroundUsageState.loadedRevision = Math.max(
              0,
              Number(response?.payload?.revision ?? payload?.backgroundUsageRevision ?? 0),
            );
            backgroundUsageState.loadedFilterKey = responseError
              ? ""
              : backgroundUsageState.queryFilterKey;
            backgroundUsageState.selectedEventId = String(
              response?.payload?.selectedEventId || backgroundUsageState.selectedEventId || "",
            );
            backgroundUsageState.detail = backgroundUsageSelectedDetail(
              response.payload,
              backgroundUsageState.selectedEventId,
            );
            backgroundUsageState.error = responseError;
            syncBackgroundUsagePanel();
            if (!responseError && backgroundUsageState.selectedEventId) {
              void loadBackgroundUsageDetail(backgroundUsageState.selectedEventId);
            }
            return;
          }
          if (kind === "detail") {
            if (requestId !== backgroundUsageState.detailRequestId) return;
            if (String(response.eventId || "") !== backgroundUsageState.selectedEventId) return;
            clearBackgroundUsageRequestTimeout("detail");
            backgroundUsageState.detailLoading = false;
            backgroundUsageState.detail = response.payload || null;
            void loadBackgroundUsagePolicy(backgroundUsageState.detail);
            if (backgroundUsageState.detail?.unread === false) {
              markBackgroundUsageEventViewed(response.eventId);
            }
            backgroundUsageState.error = responseError;
            syncBackgroundUsagePanel();
            return;
          }
          if (kind === "policyQuery" || kind === "policyApply") {
            if (requestId !== backgroundUsageState.policyRequestId) return;
            clearBackgroundUsagePolicyTimeout();
            backgroundUsageState.policyPending = false;
            backgroundUsageState.policyLoading = false;
            backgroundUsageState.policy = response.payload || null;
            backgroundUsageState.policyError = responseError;
            syncBackgroundUsagePanel();
            if (kind === "policyApply" && !responseError) {
              backgroundUsagePolicyNotice(backgroundUsageState.detail, response.payload);
            }
            return;
          }
          if (kind === "open") {
            clearBackgroundUsageRequestTimeout("query");
            clearBackgroundUsageRequestTimeout("detail");
            backgroundUsageFetchSeq += 1;
            backgroundUsageDetailSeq += 1;
            backgroundUsageState.queryRequestId = "";
            backgroundUsageState.detailRequestId = "";
            backgroundUsageState.range = normalizeBackgroundUsageRange(
              response?.payload?.range,
            );
            backgroundUsageState.feature = "";
            backgroundUsageState.model = "";
            backgroundUsageState.selectedSessionId = "";
            backgroundUsageState.loading = false;
            backgroundUsageState.detailLoading = false;
            backgroundUsageState.data = response.payload || null;
            backgroundUsageState.loadedRevision = Math.max(
              0,
              Number(response?.payload?.revision ?? payload?.backgroundUsageRevision ?? 0),
            );
            backgroundUsageState.loadedFilterKey = responseError
              ? ""
              : backgroundUsageFilterKey();
            backgroundUsageState.selectedEventId = String(
              response?.payload?.selectedEventId
              || response.eventId
              || openEventId
              || "",
            );
            backgroundUsageState.detail = backgroundUsageSelectedDetail(
              response.payload,
              backgroundUsageState.selectedEventId,
            );
            backgroundUsageState.promptExpanded = false;
            backgroundUsageState.error = responseError;
            const hasPreview = !!backgroundUsageState.detail;
            renderSettingsModal("backgroundUsage");
            // Auto-located open path: mark the jumped-to event as viewed.
            if (hasPreview && backgroundUsageState.selectedEventId) {
              void loadBackgroundUsageDetail(
                backgroundUsageState.selectedEventId,
                { markViewed: true },
              );
            } else if (backgroundUsageState.selectedEventId) {
              markBackgroundUsageEventViewed(backgroundUsageState.selectedEventId);
              syncBackgroundUsagePanel();
            }
            return;
          }
        }
        if (openEventId) {
          clearBackgroundUsageRequestTimeout("query");
          clearBackgroundUsageRequestTimeout("detail");
          const notification = payload?.backgroundUsageNotification;
          backgroundUsageState.range = normalizeBackgroundUsageRange(
            String(notification?.eventId || "") === openEventId
              ? notification?.range
              : "today",
          );
          backgroundUsageState.feature = "";
          backgroundUsageState.model = "";
          backgroundUsageState.selectedSessionId = "";
          backgroundUsageState.selectedEventId = openEventId;
          backgroundUsageState.data = null;
          backgroundUsageState.detail = null;
          backgroundUsageState.loadedRevision = -1;
          backgroundUsageState.promptExpanded = false;
          // Fallback open path (no correlated open response yet): still mark viewed.
          markBackgroundUsageEventViewed(openEventId);
          renderSettingsModal("backgroundUsage");
          return;
        }
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "backgroundUsage") return;
        if (backgroundUsageSessionRankingMode()) {
          const insights = usageInsightsFromPayload();
          const state = String(insights?.state || insights?.status || "").toLowerCase();
          if (!insights || state === "idle") {
            void loadBackgroundUsage();
          }
          return;
        }
        const revision = Math.max(0, Number(payload?.backgroundUsageRevision || 0));
        if (!backgroundUsageState.data || backgroundUsageState.loadedRevision !== revision) {
          void loadBackgroundUsage({ force: true });
        }
      }

    function install() {
      return true;
    }

    function apply(root, payload) {
      return applyBackgroundUsagePayload(root, payload || {});
    }

      function dispose() {
        clearBackgroundUsagePolicyTimeout();
        return true;
      }

    return {
      install,
      apply,
      dispose,
      backgroundUsageBridgeUrl,
      backgroundUsageEndpoint,
      submitBackgroundUsageCommand,
      backgroundUsageFormatCost,
      normalizeBackgroundUsageRange,
      renderBackgroundUsageNotification,
      markBackgroundUsageEventViewed,
      backgroundUsageTime,
      backgroundUsageRedactedPrompt,
      backgroundUsageEventHtml,
      backgroundUsageDetailHtml,
      backgroundUsageSessionRankingMode,
      backgroundUsageSessionRankingRange,
      backgroundUsageSessionRankingDetailHtml,
      backgroundUsageFeatureOptionsHtml,
      backgroundUsageSessionRankingPanelHtml,
      backgroundUsagePanelHtml,
      backgroundUsageFilterKey,
      captureBackgroundUsageScrollPositions,
      restoreBackgroundUsageScrollPositions,
      clearBackgroundUsageRequestTimeout,
      scheduleBackgroundUsageRequestTimeout,
      fetchBackgroundUsageWithTimeout,
      syncBackgroundUsagePanel,
      loadBackgroundUsageDetail,
      loadBackgroundUsagePolicy,
      backgroundUsagePolicyTargetState,
      backgroundUsagePolicyEffectiveState,
      backgroundUsagePolicyCopy,
      backgroundUsagePolicyConfirm,
      backgroundUsagePolicyNotice,
      applyBackgroundUsagePolicy,
      loadBackgroundUsage,
      backgroundUsageSelectedDetail,
      applyBackgroundUsagePayload,
    };
  }

  const backgroundUsageDomain = ctx.domains.register(
    "background_usage",
    createBackgroundUsageDomain(ctx, shared),
  );
  const {
    backgroundUsageBridgeUrl,
    backgroundUsageEndpoint,
    submitBackgroundUsageCommand,
    backgroundUsageFormatCost,
    normalizeBackgroundUsageRange,
    renderBackgroundUsageNotification,
    markBackgroundUsageEventViewed,
    backgroundUsageTime,
    backgroundUsageRedactedPrompt,
    backgroundUsageEventHtml,
    backgroundUsageDetailHtml,
    backgroundUsageSessionRankingMode,
    backgroundUsageSessionRankingRange,
    backgroundUsageSessionRankingDetailHtml,
    backgroundUsageFeatureOptionsHtml,
    backgroundUsageSessionRankingPanelHtml,
    backgroundUsagePanelHtml,
    backgroundUsageFilterKey,
    captureBackgroundUsageScrollPositions,
    restoreBackgroundUsageScrollPositions,
    clearBackgroundUsageRequestTimeout,
    scheduleBackgroundUsageRequestTimeout,
    fetchBackgroundUsageWithTimeout,
    syncBackgroundUsagePanel,
    loadBackgroundUsageDetail,
    loadBackgroundUsagePolicy,
    backgroundUsagePolicyTargetState,
    backgroundUsagePolicyEffectiveState,
    backgroundUsagePolicyCopy,
    backgroundUsagePolicyConfirm,
    backgroundUsagePolicyNotice,
    applyBackgroundUsagePolicy,
    loadBackgroundUsage,
    backgroundUsageSelectedDetail,
    applyBackgroundUsagePayload,
  } = backgroundUsageDomain;
"""

__all__ = ["TEXT"]
