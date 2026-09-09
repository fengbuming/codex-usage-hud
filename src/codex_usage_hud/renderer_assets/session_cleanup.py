"""Renderer session cleanup domain asset."""

TEXT = r"""
  function createSessionCleanupDomain(ctx, shared) {
      function storageFormatBytes(value) {
        let bytes = Math.max(0, Number(value) || 0);
        const units = ["B", "KB", "MB", "GB", "TB"];
        let index = 0;
        while (bytes >= 1024 && index < units.length - 1) { bytes /= 1024; index += 1; }
        return `${bytes >= 10 || index === 0 ? bytes.toFixed(0) : bytes.toFixed(1)} ${units[index]}`;
      }

      function sessionCleanupFromPayload() {
        if (sessionCleanupState.data && typeof sessionCleanupState.data === "object") {
          return sessionCleanupState.data;
        }
        const value = currentPayload()?.sessionCleanup;
        return value && typeof value === "object" && Object.keys(value).length ? value : null;
      }

      function sessionCleanupPayloadWithInventory(data) {
        const incoming = data && typeof data === "object" ? data : {};
        const previous = sessionCleanupState.data && typeof sessionCleanupState.data === "object"
          ? sessionCleanupState.data
          : {};
        if (Array.isArray(incoming.sessions) || !Array.isArray(previous.sessions)) {
          return incoming;
        }
        return { ...previous, ...incoming, sessions: previous.sessions };
      }

      function cleanupIconSvg(name, extraClass = "") {
        const paths = {
          scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="11" cy="11" r="4"/><path d="m16 16 3 3"/>',
          trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"/>',
          refresh: '<path d="M20 11a8 8 0 0 0-14.9-4M4 4v6h6M4 13a8 8 0 0 0 14.9 4M20 20v-6h-6"/>',
          shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
          search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
          calendar: '<rect width="18" height="18" x="3" y="4" rx="2" ry="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
          check: '<path d="m5 12 4 4L19 6"/>',
          alert: '<path d="m21 19-9-16-9 16h18Z"/><path d="M12 9v4M12 17h.01"/>',
          chevron: '<path d="m9 18 6-6-6-6"/>',
          database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/>',
          copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
          folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M2 10h20"/>',
          external: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14 21 3"/>',
          terminal: '<path d="m7 11 3 3-3 3"/><path d="M13 17h4"/><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>',
        };
        const body = paths[String(name || "")] || paths.scan;
        const klass = ["codex-usage-hud-cleanup-icon", extraClass].filter(Boolean).join(" ");
        return `<svg class="${klass}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
      }

      function storagePanelHtml() {
        const sessionData = sessionCleanupFromPayload();
        const operation = sessionData?.operation && typeof sessionData.operation === "object"
          ? sessionData.operation
          : {};
        const action = String(operation?.action || "");
        const state = String(operation?.state || "");
        const scanned = !!String(sessionData?.revision || "");
        const executing = new Set(["execute", "sessionCleanupExecute"]).has(action)
          && new Set(["accepted", "running"]).has(state);
        const busy = new Set(["scanning", "accepted", "running"]).has(state)
          || !!sessionCleanupState.pendingRequestId;
        const selectedCount = sessionCleanupState.selectedIds.size;
        const selectedRows = sessionCleanupRows(sessionData)
          .filter((item) => sessionCleanupState.selectedIds.has(String(item?.id || "")));
        const descendants = selectedRows.reduce(
          (sum, item) => sum + Math.max(0, Number(item?.descendantCount || 0)),
          0,
        );
        const bytes = selectedRows.reduce(
          (sum, item) => sum + Math.max(0, Number(item?.bytes || 0)),
          0,
        );
        const footerMeta = executing
          ? "正在永久删除所选会话，完成后将直接刷新当前列表"
          : (selectedCount
            ? `已选 ${selectedCount} 个会话 · 含 ${descendants} 个关联子任务 · ${storageFormatBytes(bytes)}`
            : (scanned ? "当前/运行中会话不可选；子任务随主会话汇总" : "上次扫描：--"));
        const footerActions = `<div class="codex-usage-hud-cleanup-footer-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-scan" ${busy ? "disabled" : ""}>${busy ? (executing ? "正在删除..." : "正在扫描...") : (scanned ? "重新扫描" : "扫描会话")}</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-preview" data-danger="true" data-size="large" ${busy || !selectedCount || sessionData?.capability?.available !== true ? "disabled" : ""}>${cleanupIconSvg("trash")}永久删除</button></div>`;
        return `<div class="codex-usage-hud-cleanup-workspace"><div class="codex-usage-hud-cleanup-content">${sessionCleanupPanelHtml()}</div><div class="codex-usage-hud-cleanup-footer"><span class="codex-usage-hud-cleanup-footer-meta">${footerMeta}</span>${footerActions}</div></div>`;
      }

      function sessionCleanupRows(data = sessionCleanupFromPayload(), filters = sessionCleanupState) {
        const filterState = filters && typeof filters === "object"
          ? filters
          : sessionCleanupState;
        const search = String(filterState.search || "").trim().toLowerCase();
        const workdirId = String(filterState.workdirId || "").trim();
        const searchRevision = String(filterState.searchResultRevision || "");
        const searchQuery = String(filterState.searchResultQuery || "").trim().toLowerCase();
        const serverSearchActive = new Set(["completed", "indexing"]).has(filterState.searchResultState)
          && !!search
          && searchQuery === search
          && searchRevision === String(data?.revision || "");
        const serverMatches = filterState.searchResultMatches instanceof Set
          ? filterState.searchResultMatches
          : new Set(Array.isArray(filterState.searchResultMatches) ? filterState.searchResultMatches : []);
        const archive = String(filterState.archive || "all");
        const availability = String(filterState.availability || "all");
        const clientKind = String(filterState.clientKind || "all");
        const modelProvider = String(filterState.modelProvider || "all");
        const startAt = sessionCleanupDateValue(filterState.dateStart);
        const endAt = sessionCleanupDateValue(filterState.dateEnd);
        const rows = (Array.isArray(data?.sessions) ? data.sessions : []).filter((item) => {
          const archived = item?.archived === true;
          const status = String(item?.status || "idle");
          const selectable = item?.selectable === true;
          const updatedAt = sessionCleanupDateValue(item?.updatedAt);
          if (archive === "archived" && !archived) return false;
          if (archive === "unarchived" && archived) return false;
          if (availability === "selectable" && !selectable) return false;
          if (availability === "protected" && selectable) return false;
          if (["current", "running", "unresolved", "unavailable"].includes(availability) && status !== availability) return false;
          if (clientKind !== "all" && String(item?.clientKind || "unknown") !== clientKind) return false;
          if (modelProvider !== "all" && String(item?.modelProvider || "unknown") !== modelProvider) return false;
          if (workdirId && String(item?.workdirId || "") !== workdirId) return false;
          if (startAt !== null && (updatedAt === null || updatedAt < startAt)) return false;
          if (endAt !== null && (updatedAt === null || updatedAt > endAt)) return false;
          if (!search) return true;
          if (serverSearchActive) {
            if (serverMatches.size || filterState.searchResultState === "completed") {
              return serverMatches.has(String(item?.id || ""));
            }
          }
          const haystack = `${item?.title || ""} ${item?.workdirName || ""} ${item?.modelProvider || ""} ${item?.clientKind || ""}`.toLowerCase();
          return search.split(/\s+/).filter(Boolean).every((term) => haystack.includes(term));
        });
        const sort = String(filterState.sort || "recent");
        return rows.sort((left, right) => {
          if (serverSearchActive && search && filterState.searchResultMatches instanceof Set) {
            const details = filterState.searchResultDetails instanceof Map
              ? filterState.searchResultDetails
              : new Map();
            const leftExact = details.get(String(left?.id || ""))?.exactPhrase ? 1 : 0;
            const rightExact = details.get(String(right?.id || ""))?.exactPhrase ? 1 : 0;
            if (leftExact !== rightExact) return rightExact - leftExact;
            const leftScore = Number(details.get(String(left?.id || ""))?.score || 0);
            const rightScore = Number(details.get(String(right?.id || ""))?.score || 0);
            if (leftScore !== rightScore) return rightScore - leftScore;
          }
          const leftUpdated = sessionCleanupDateValue(left?.updatedAt) || 0;
          const rightUpdated = sessionCleanupDateValue(right?.updatedAt) || 0;
          const leftBytes = Math.max(0, Number(left?.bytes || 0));
          const rightBytes = Math.max(0, Number(right?.bytes || 0));
          if (sort === "oldest") return leftUpdated - rightUpdated || rightBytes - leftBytes;
          if (sort === "largest") return rightBytes - leftBytes || leftUpdated - rightUpdated;
          if (sort === "recent") return rightUpdated - leftUpdated || rightBytes - leftBytes;
          // Keep the legacy value deterministic for callers that explicitly
          // supplied it; new state and UI defaults use "recent" below.
          if (sort === "recommended") {
            const leftProtection = left?.selectable === true ? 0 : 1;
            const rightProtection = right?.selectable === true ? 0 : 1;
            if (leftProtection !== rightProtection) return leftProtection - rightProtection;
            const leftArchive = left?.archived === true ? 0 : 1;
            const rightArchive = right?.archived === true ? 0 : 1;
            if (leftArchive !== rightArchive) return leftArchive - rightArchive;
            return leftUpdated - rightUpdated || rightBytes - leftBytes;
          }
          return rightUpdated - leftUpdated || rightBytes - leftBytes;
        });
      }

      const SESSION_CLEANUP_PAGE_SIZE = 30;

      function sessionCleanupPageRows(
        data = sessionCleanupFromPayload(),
        filters = sessionCleanupState,
      ) {
        const rows = sessionCleanupRows(data, filters);
        const pageCount = Math.max(1, Math.ceil(rows.length / SESSION_CLEANUP_PAGE_SIZE));
        const requestedPage = Math.max(0, Math.floor(Number(sessionCleanupState.page || 0)));
        const page = Math.min(requestedPage, pageCount - 1);
        if (filters === sessionCleanupState && page !== requestedPage) {
          sessionCleanupState.page = page;
        }
        const start = page * SESSION_CLEANUP_PAGE_SIZE;
        return rows.slice(start, start + SESSION_CLEANUP_PAGE_SIZE);
      }

      function sessionCleanupPageCount(rowCount) {
        return Math.max(1, Math.ceil(Math.max(0, Number(rowCount || 0)) / SESSION_CLEANUP_PAGE_SIZE));
      }

      function moveSessionCleanupPage(direction) {
        const data = sessionCleanupFromPayload();
        const rows = sessionCleanupRows(data);
        const pageCount = sessionCleanupPageCount(rows.length);
        const current = Math.min(
          Math.max(0, Math.floor(Number(sessionCleanupState.page || 0))),
          pageCount - 1,
        );
        const next = Math.min(
          pageCount - 1,
          Math.max(0, current + (Number(direction) < 0 ? -1 : 1)),
        );
        if (next === current) return false;
        sessionCleanupState.page = next;
        renderSettingsModal("storage");
        return true;
      }

      function sessionCleanupDateValue(value) {
        const parsed = Date.parse(String(value || ""));
        return Number.isFinite(parsed) ? parsed : null;
      }

      function sessionCleanupDateTimeInputValue(value) {
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-")
          + `T${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
      }

      function sessionCleanupDateLabel(value) {
        const timestamp = sessionCleanupDateValue(value);
        if (timestamp === null) return "";
        return new Date(timestamp).toLocaleString("zh-CN", {
          month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
        });
      }

      function sessionCleanupDateRangeLabel(start = sessionCleanupState.dateStart, end = sessionCleanupState.dateEnd) {
        const startLabel = sessionCleanupDateLabel(start);
        const endLabel = sessionCleanupDateLabel(end);
        if (startLabel && endLabel) return `${startLabel} 至 ${endLabel}`;
        if (startLabel) return `${startLabel} 起`;
        if (endLabel) return `截至 ${endLabel}`;
        return "全部时间";
      }

      function sessionCleanupDateRangeError(start, end) {
        const startAt = sessionCleanupDateValue(start);
        const endAt = sessionCleanupDateValue(end);
        return startAt !== null && endAt !== null && startAt > endAt
          ? "开始时间不能晚于结束时间"
          : "";
      }

      function sessionCleanupDatePresetValues(preset) {
        const now = new Date();
        const start = new Date(now);
        const end = new Date(now);
        if (preset === "today") {
          start.setHours(0, 0, 0, 0);
        } else if (preset === "7d") {
          start.setDate(start.getDate() - 6);
          start.setHours(0, 0, 0, 0);
        } else if (preset === "week") {
          start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
          start.setHours(0, 0, 0, 0);
        } else if (preset === "30d") {
          start.setDate(start.getDate() - 29);
          start.setHours(0, 0, 0, 0);
        } else if (preset === "month") {
          start.setDate(1);
          start.setHours(0, 0, 0, 0);
        } else if (preset === "older") {
          end.setTime(end.getTime() - 30 * 86400000);
          return { start: "", end: sessionCleanupDateTimeInputValue(end) };
        }
        return {
          start: sessionCleanupDateTimeInputValue(start),
          end: sessionCleanupDateTimeInputValue(end),
        };
      }

      function sessionCleanupClientLabel(value) {
        return ({ app: "Codex App", cli: "CLI", unknown: "来源未知" })[String(value || "unknown")] || "来源未知";
      }

      function sessionCleanupAvailabilityLabel(value) {
        return ({
          all: "全部删除状态",
          selectable: "可永久删除",
          protected: "受保护",
          current: "当前会话",
          running: "运行中",
          unresolved: "映射无法确认",
          unavailable: "暂不可删除",
        })[String(value || "all")] || "全部删除状态";
      }

      function sessionCleanupFilterOptionHtml(options, selectedValue) {
        return options.map(([value, label]) => `<option value="${escapeHtml(value)}" ${String(value) === String(selectedValue) ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
      }

      function sessionCleanupWorkdirOptionHtml(data = sessionCleanupFromPayload()) {
        const detailed = Array.isArray(sessionCleanupState.workdirOptions)
          && sessionCleanupState.workdirOptions.some((item) => item?.path);
        const options = detailed
          ? sessionCleanupState.workdirOptions
          : (Array.isArray(data?.workdirs) ? data.workdirs : []);
        return [
          ["", "全部工作目录"],
          ...options.map((item) => {
            const id = String(item?.id || "").trim();
            const label = String(item?.label || "未命名目录").trim() || "未命名目录";
            const path = String(item?.path || "").trim();
            const count = Math.max(0, Number(item?.sessionCount || 0));
            const unavailable = item?.available === false ? " · 历史目录" : "";
            return [id, `${label}${path ? ` · ${path}` : ""}${unavailable}${count ? ` · ${count}` : ""}`];
          }).filter(([value]) => value),
        ].map(([value, label]) => `<option value="${escapeHtml(value)}" ${String(value) === String(sessionCleanupState.workdirId || "") ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
      }

      function sessionCleanupMatchKindLabel(value) {
        return ({
          user: "用户输入",
          assistant: "模型输出",
          tool: "工具记录",
          file: "修改文件",
          metadata: "会话信息",
        })[String(value || "")] || "命中";
      }

      function sessionCleanupFilterSummary(data, rows) {
        const labels = [];
        const dateRange = sessionCleanupDateRangeLabel();
        if (dateRange !== "全部时间") labels.push(`最后活动：${dateRange}`);
        if (sessionCleanupState.archive === "archived") labels.push("已在 Codex 中归档");
        if (sessionCleanupState.archive === "unarchived") labels.push("未归档");
        if (sessionCleanupState.availability !== "all") labels.push(sessionCleanupAvailabilityLabel(sessionCleanupState.availability));
        if (sessionCleanupState.clientKind !== "all") labels.push(sessionCleanupClientLabel(sessionCleanupState.clientKind));
        if (sessionCleanupState.modelProvider !== "all") labels.push(`提供方：${providerRegistryDisplayName(currentPayload()?.settings, sessionCleanupState.modelProvider)}`);
        if (String(sessionCleanupState.search || "").trim()) labels.push("搜索");
        if (String(sessionCleanupState.workdirId || "").trim()) labels.push("工作目录");
        const total = Array.isArray(data?.sessions) ? data.sessions.length : 0;
        const tags = labels.map((label) => `<span class="codex-usage-hud-session-filter-summary-tag" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`).join("");
        return `<div class="codex-usage-hud-session-filter-summary"><span>${rows.length} / ${total} 个会话</span><div class="codex-usage-hud-session-filter-summary-tags">${tags || '<span>未设置筛选</span>'}</div>${labels.length ? '<button type="button" class="codex-usage-hud-session-filter-summary-clear" data-action="session-cleanup-filters-clear">清除</button>' : ""}</div>`;
      }

      function sessionCleanupStatusLabel(item) {
        const status = String(item?.status || "idle");
        return ({ idle: "普通", archived: "已归档", current: "当前", running: "运行中", unresolved: "映射异常", unavailable: "不可用" })[status] || status;
      }

      function sessionCleanupReasonLabel(value) {
        const reason = String(value || "").trim();
        const exact = {
          "Not scanned yet.": "尚未扫描。",
          "Codex local session store is unavailable.": "本机会话存储不可用。",
          "Codex local session store schema is not recognized.": "本机会话存储结构无法识别。",
          "The current session cannot be permanently deleted.": "当前会话不可永久删除。",
          "This session tree still has active work.": "该会话或关联子任务仍在运行。",
          "The session spawn relation could not be verified.": "无法完整验证主会话与子任务关系。",
          "The session rollout mapping could not be verified.": "无法完整验证会话本地记录映射。",
        };
        if (exact[reason]) return exact[reason];
        if (reason.startsWith("Codex local session store could not be opened")) {
          return "无法以写入方式打开本机会话存储。";
        }
        return reason;
      }

      function sessionCleanupPhaseLabel(operation = {}) {
        const raw = String(operation?.phaseLabel || "").trim();
        const labels = {
          "Reading session index": "读取会话索引",
          "Resolving session families": "归并主会话与关联子任务",
          "Checking deletion protection": "检查永久删除保护状态",
        };
        return labels[raw] || raw || "读取会话索引";
      }

      function sessionIndexScanMergeHtml() {
        // PRD §6.2: while a scan is running, fold the warm-index progress into
        // the scan phase instead of showing a second, unrelated progress strip.
        // Hidden entirely when there is no live index state.
        const sessionIndex = sessionIndexDomainState();
        if (!sessionIndex) return "";
        const coverage = String(sessionIndex.coverage || "empty");
        const jobState = String(sessionIndex.jobState || "idle");
        const built = Math.max(0, Number(sessionIndex.builtCount || 0));
        const total = Math.max(0, Number(sessionIndex.totalCount || 0));
        const range = String(sessionIndex.selectedRange || "1m");
        const rangeLabel = {
          "1m": "最近 1 个月", "3m": "最近 3 个月", "6m": "最近 6 个月",
          "1y": "最近 1 年", "all": "全部",
        }[range] || "最近 1 个月";
        const running = new Set(["running", "attached"]).has(jobState);
        const phase = String(sessionIndex.phase || "");
        if (running && total > 0) {
          const percent = Math.min(100, Math.round((built / total) * 100));
          const remaining = Math.max(0, Number(sessionIndex.estimatedRemainingSec || 0));
          const remainingLabel = remaining > 0 ? ` · 预计剩余 ${sessionIndexEtaLabel(remaining)}` : "";
          if (phase === "scanning") {
            // The warm job is re-enumerating candidate sessions (a separate,
            // potentially slow scan from the cleanup scan). Show an explicit
            // status so the silent window never looks frozen at a stale 100%.
            return `<div class="codex-usage-hud-cleanup-scan-stage" data-merged="session-index"><span><span class="codex-usage-hud-cleanup-mini-spinner"></span><strong>正在扫描会话文件</strong> · ${rangeLabel}</span><span data-secondary="true">准备建立搜索索引，请稍候</span></div>`;
          }
          return `<div class="codex-usage-hud-cleanup-scan-stage" data-merged="session-index"><span><span class="codex-usage-hud-cleanup-mini-spinner"></span><strong>建立搜索索引</strong> · ${rangeLabel}</span><span data-secondary="true">${built}/${total} 个会话 · ${percent}%${remainingLabel}</span></div>`;
        }
        if (coverage === "full") {
          return `<div class="codex-usage-hud-cleanup-scan-stage" data-merged="session-index"><span><strong>搜索索引已就绪</strong></span><span>可搜索全部会话</span></div>`;
        }
        if (total > 0) {
          return `<div class="codex-usage-hud-cleanup-scan-stage" data-merged="session-index"><span><strong>搜索索引已就绪</strong> · ${rangeLabel}</span><span>${total} 个会话可搜索</span></div>`;
        }
        return null;
      }

      function formatSessionCleanupElapsed(startedAt) {
        const start = Number(startedAt || 0);
        if (!start) return "0:00";
        const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
        const minutes = Math.floor(seconds / 60);
        return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
      }

      function sessionCleanupScanActive() {
        const data = sessionCleanupFromPayload();
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        return new Set(["scan", "sessionCleanupScan"]).has(String(operation?.action || ""))
          && new Set(["scanning", "accepted", "running"]).has(String(operation?.state || ""));
      }

      function stopSessionCleanupScanWatchdog() {
        if (sessionCleanupScanWatchdogTimer) {
          ctx.lifecycle.clearTimeout(sessionCleanupScanWatchdogTimer);
          sessionCleanupScanWatchdogTimer = 0;
        }
      }

      function scheduleSessionCleanupScanWatchdog(requestId) {
        stopSessionCleanupScanWatchdog();
        const expectedRequestId = String(requestId || "").trim();
        if (!expectedRequestId) return false;
        sessionCleanupScanWatchdogTimer = ctx.lifecycle.timeout(
          "session_cleanup_scan_watchdog",
          () => {
            sessionCleanupScanWatchdogTimer = 0;
            const pending = String(sessionCleanupState.pendingRequestId || "");
            const transferRequest = String(sessionTransferState.scanRequestId || "");
            if (pending !== expectedRequestId && transferRequest !== expectedRequestId) return;
            const data = sessionCleanupFromPayload();
            const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
            const operationRequestId = String(operation?.requestId || "");
            const operationAction = String(operation?.action || "").toLowerCase();
            const operationState = String(operation?.state || "").toLowerCase();
            const active = new Set(["scan", "sessioncleanupscan"]).has(operationAction)
              && new Set(["scanning", "accepted", "running"]).has(operationState)
              && operationRequestId === expectedRequestId;
            const terminal = new Set(["completed", "partial", "failed", "cancelled"]).has(operationState)
              && operationRequestId === expectedRequestId;
            if (active) {
              scheduleSessionCleanupScanWatchdog(expectedRequestId);
              return;
            }
            if (terminal) {
              sessionCleanupState.pendingRequestId = "";
              sessionCleanupState.scanStartedAt = 0;
              stopSessionCleanupElapsedTicker();
              stopSessionCleanupScanWatchdog();
              if (sessionTransferState.scanRequestId === expectedRequestId) {
                sessionTransferState.scanRequestId = "";
                sessionTransferState.operation = operation;
                if (sessionTransferState.open) renderSessionTransferDialog();
              } else {
                refreshStoragePanelIfVisible();
              }
              return;
            }
            sessionCleanupState.pendingRequestId = "";
            sessionCleanupState.scanStartedAt = 0;
            stopSessionCleanupElapsedTicker();
            if (sessionTransferState.scanRequestId === expectedRequestId) {
              sessionTransferState.scanRequestId = "";
              sessionTransferState.operation = null;
              if (sessionTransferState.open) renderSessionTransferDialog();
            } else {
              refreshStoragePanelIfVisible();
            }
            setSettingsStatus("扫描命令未收到响应，请重新扫描。", "error");
          },
          15000,
        );
        return true;
      }

      function activeSessionCleanupScanRequestId(data = sessionCleanupFromPayload()) {
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const action = String(operation?.action || "").toLowerCase();
        const state = String(operation?.state || "").toLowerCase();
        if (
          !new Set(["scan", "sessioncleanupscan"]).has(action)
          || !new Set(["scanning", "accepted", "running"]).has(state)
        ) return "";
        return String(
          operation?.requestId || sessionCleanupState.pendingRequestId || "",
        ).trim();
      }

      function stopSessionCleanupElapsedTicker() {
        if (sessionCleanupElapsedTimer) {
          ctx.lifecycle.clearInterval(sessionCleanupElapsedTimer);
          sessionCleanupElapsedTimer = 0;
        }
      }

      function syncSessionCleanupElapsed() {
        const modal = document.getElementById(settingsModalId);
        if (
          !modal
          || modal.hidden
          || settingsActiveTab !== "storage"
          || !sessionCleanupState.scanStartedAt
          || !sessionCleanupScanActive()
        ) {
          stopSessionCleanupElapsedTicker();
          return false;
        }
        const node = modal.querySelector('[data-session-cleanup-elapsed="true"]');
        if (node) node.textContent = formatSessionCleanupElapsed(sessionCleanupState.scanStartedAt);
        return true;
      }

      function ensureSessionCleanupElapsedTicker() {
        if (!syncSessionCleanupElapsed()) return false;
        if (!sessionCleanupElapsedTimer) {
          sessionCleanupElapsedTimer = ctx.lifecycle.interval(
            "session_cleanup_elapsed",
            syncSessionCleanupElapsed,
            1000,
          );
        }
        return true;
      }

      // --- Session-index progress elapsed timer -------------------------------
      // Mirrors the cleanup-scan elapsed ticker: while the warm index job is
      // running, tick a "已用时" readout every second so the UI never looks
      // frozen during the silent enumeration/indexing windows.
      function stopSessionIndexElapsedTicker() {
        if (sessionCleanupState.sessionIndexElapsedTimer) {
          ctx.lifecycle.clearInterval(sessionCleanupState.sessionIndexElapsedTimer);
          sessionCleanupState.sessionIndexElapsedTimer = 0;
        }
      }

      function syncSessionIndexElapsed() {
        const modal = document.getElementById(settingsModalId);
        const sessionIndex = sessionIndexDomainState();
        const running = sessionIndex
          && new Set(["running", "attached"]).has(String(sessionIndex.jobState || ""))
          && Number(sessionIndex.startedAt || 0) > 0;
        if (
          !modal
          || modal.hidden
          || settingsActiveTab !== "storage"
          || !running
        ) {
          stopSessionIndexElapsedTicker();
          return false;
        }
        const node = modal.querySelector('[data-session-index-elapsed="true"]');
        if (node) node.textContent = formatSessionCleanupElapsed(Number(sessionIndex.startedAt));
        return true;
      }

      function ensureSessionIndexElapsedTicker() {
        if (!syncSessionIndexElapsed()) return false;
        if (!sessionCleanupState.sessionIndexElapsedTimer) {
          sessionCleanupState.sessionIndexElapsedTimer = ctx.lifecycle.interval(
            "session_index_elapsed",
            syncSessionIndexElapsed,
            1000,
          );
        }
        return true;
      }

      function formatSessionIndexEta(seconds) {
        const value = Math.max(0, Math.round(Number(seconds) || 0));
        if (value <= 0) return "";
        if (value < 60) return `${value}秒`;
        const minutes = Math.floor(value / 60);
        if (minutes < 60) return `${minutes}分钟`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}小时${minutes % 60 ? ` ${minutes % 60}分` : ""}`;
        const days = Math.floor(hours / 24);
        return `${days}天`;
      }

      function sessionIndexEtaLabel(seconds) {
        return formatSessionIndexEta(seconds) || "稍后";
      }

      function sessionIndexRangeLabel(range) {
        return {
          "1m": "最近 1 个月", "3m": "最近 3 个月", "6m": "最近 6 个月",
          "1y": "最近 1 年", "all": "全部",
        }[String(range || "1m")] || String(range || "当前范围");
      }

      function sessionIndexProgressPercent(sessionIndex) {
        const built = Math.max(0, Number(sessionIndex?.builtCount || 0));
        const total = Math.max(0, Number(sessionIndex?.totalCount || 0));
        return total > 0 ? Math.min(100, Math.round((built / total) * 100)) : 0;
      }

      function sessionIndexToggleHtml(sessionIndex, expanded = false) {
        const jobState = String(sessionIndex?.jobState || "idle");
        const coverage = String(sessionIndex?.coverage || "empty");
        const built = Math.max(0, Number(sessionIndex?.builtCount || 0));
        const total = Math.max(0, Number(sessionIndex?.totalCount || 0));
        const percent = sessionIndexProgressPercent(sessionIndex);
        const active = new Set(["running", "attached", "paused"]).has(jobState);
        const rangeLabel = sessionIndexRangeLabel(sessionIndex?.selectedRange);
        const phase = String(sessionIndex?.phase || "");
        let label = "索引";
        let title = "显示或隐藏搜索索引状态";
        if (active) {
          if (phase === "scanning") {
            label = "索引中";
            title = "正在扫描会话文件，准备建立搜索索引";
          } else {
            label = total > 0 ? `索引 ${percent}%` : "索引中";
            title = total > 0
              ? `正在建立搜索索引：${built}/${total} 个会话 · ${percent}%`
              : "正在建立搜索索引";
          }
        } else if (coverage === "full") {
          label = "索引 · 全部";
          title = "当前已索引范围：全部会话";
        } else if (coverage.indexOf("partial(") === 0 || coverage.indexOf("range_done(") === 0) {
          label = `索引 · ${rangeLabel}`;
          title = `当前已索引范围：${rangeLabel}`;
        } else if (coverage === "empty") {
          label = "索引 · 未建立";
          title = "尚未建立搜索索引";
        }
        const progress = active
          ? `<span class="codex-usage-hud-session-index-toggle-track" role="progressbar" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100" aria-label="搜索索引建立进度"><span class="codex-usage-hud-session-index-toggle-fill" data-phase="${phase === 'indexing' ? 'indexing' : ''}" data-indeterminate="${total <= 0 || phase === 'scanning'}" style="width:${phase === 'scanning' ? 8 : Math.max(percent, total > 0 ? 8 : 38)}%"></span></span>`
          : "";
        return `<button type="button" class="codex-usage-hud-session-index-toggle" data-action="session-index-toggle" aria-expanded="${expanded ? "true" : "false"}" aria-label="${escapeHtml(title)}" title="${escapeHtml(title)}">${cleanupIconSvg("search")}<span class="codex-usage-hud-session-index-toggle-label">${escapeHtml(label)}</span>${progress}</button>`;
      }

      function sessionIndexExtendOptions(sessionIndex) {
        // The extend dropdown intentionally avoids a synthetic duration. A
        // current-range count cannot predict a larger range's density, so a
        // precise-looking estimate is more misleading than useful. Live
        // progress below remains the source of truth once the job starts.
        const ranges = [
          ["3m", "最近 3 个月"], ["6m", "最近 6 个月"], ["1y", "最近 1 年"], ["all", "全部"],
        ];
        const current = String(sessionIndex?.selectedRange || "1m");
        const currentIndex = ["1m", ...ranges.map(([key]) => key)].indexOf(current);
        return ranges.filter(([key]) => currentIndex < 0 || ["1m", ...ranges.map(([value]) => value)].indexOf(key) > currentIndex).map(([key, label]) => {
          return { key, label };
        });
      }

      function sessionIndexBuildOptions(sessionIndex) {
        const current = String(sessionIndex?.selectedRange || "1m").trim().toLowerCase();
        return [
          ["1m", "最近 1 个月"],
          ["3m", "最近 3 个月"],
          ["6m", "最近 6 个月"],
          ["1y", "最近 1 年"],
          ["all", "全部"],
        ].map(([key, label]) => ({ key, label, selected: key === current }));
      }

      function sessionIndexPanelVisible(forceVisible = false) {
        // Single source of truth for drawer visibility: the toggle's
        // aria-expanded and the rendered content read the same answer, so they
        // can never drift apart across a re-render.
        return forceVisible
          ? !sessionCleanupState.indexPanelHidden
          : !!sessionCleanupState.indexPanelOpen;
      }

      function sessionIndexBadge(sessionIndex) {
        // Badge state drives color + wording so the panel is readable at a
        // glance even before the sentence is parsed.
        const jobState = String(sessionIndex?.jobState || "idle");
        const coverage = String(sessionIndex?.coverage || "empty");
        if (sessionIndex?.enabled === false) return { state: "off", label: "已关闭" };
        if (jobState === "error") return { state: "error", label: "建立失败" };
        if (jobState === "paused") return { state: "paused", label: "已暂停" };
        if (new Set(["running", "attached"]).has(jobState)) {
          return {
            state: "active",
            label: String(sessionIndex?.phase || "") === "scanning" ? "扫描中" : "索引中",
          };
        }
        if (coverage === "full" || coverage.indexOf("range_done") === 0) {
          return { state: "ready", label: "已就绪" };
        }
        return { state: "empty", label: "未建立" };
      }

      function sessionIndexMetricsHtml(sessionIndex) {
        // Numbers the headline drops live here. 已索引 only appears once the
        // range has an enumerated total, so an unbuilt index shows no 0 / 0.
        const built = Math.max(0, Number(sessionIndex?.builtCount || 0));
        const total = Math.max(0, Number(sessionIndex?.totalCount || 0));
        const metrics = [];
        if (total > 0) metrics.push(["已索引", `${built} / ${total}`]);
        metrics.push(["范围", sessionIndexRangeLabel(sessionIndex?.selectedRange)]);
        metrics.push(["占用", storageFormatBytes(Math.max(0, Number(sessionIndex?.diskBytes || 0)))]);
        return `<dl class="codex-usage-hud-session-index-metrics">${metrics.map(([label, value]) => `<div class="codex-usage-hud-session-index-metric"><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
      }

      function sessionIndexPanelHtml({ notice = "" } = {}) {
        const sessionIndex = sessionIndexDomainState();
        if (!sessionIndex) {
          return `<div class="codex-usage-hud-session-index-coverage" data-variant="panel" data-state="empty" data-job-state="unavailable" data-coverage="empty"><p class="codex-usage-hud-session-index-text">搜索索引状态当前不可用</p></div>`;
        }
        const coverage = String(sessionIndex.coverage || "empty");
        const jobState = String(sessionIndex.jobState || "idle");
        const phase = String(sessionIndex.phase || "");
        const range = String(sessionIndex.selectedRange || "1m");
        const rangeLabel = sessionIndexRangeLabel(range);
        const built = Math.max(0, Number(sessionIndex.builtCount || 0));
        const total = Math.max(0, Number(sessionIndex.totalCount || 0));
        const enabled = sessionIndex.enabled !== false;
        const running = new Set(["running", "attached"]).has(jobState);
        const paused = jobState === "paused";
        const indexing = running || paused;
        const percent = sessionIndexProgressPercent(sessionIndex);
        const remaining = Math.max(0, Number(sessionIndex.estimatedRemainingSec || 0));
        // Live "已用时" readout: a per-second ticker refreshes the span so the
        // UI never looks frozen during the silent enumeration/indexing windows.
        const elapsedSpan = Number(sessionIndex.startedAt || 0) > 0
          ? ` · 已用时 <span data-session-index-elapsed="true">${formatSessionCleanupElapsed(Number(sessionIndex.startedAt))}</span>`
          : "";
        const controlPending = Boolean(sessionCleanupState.sessionIndexControlRequestId);
        const disabled = controlPending || !enabled ? " disabled aria-disabled=\"true\"" : "";
        const controlDisabled = controlPending ? " disabled aria-disabled=\"true\"" : "";
        // Layout contract: the headline carries prose only. Counts live in the
        // metric row and timers live on the progress meta line, so the panel
        // reads as three scannable bands instead of one dense sentence.
        const badge = sessionIndexBadge(sessionIndex);
        let message = "";
        let progressPhase = "";
        let progressMeta = "";
        if (!enabled) {
          message = built > 0
            ? `索引功能已关闭 · 已保留 ${built} 个会话索引，重新开启后可继续扩展`
            : "索引功能已关闭，不会自动建立或更新索引";
        } else if (controlPending && !running) {
          // Command just sent but the live domain hasn't flipped to running
          // yet: surface the pending label immediately so the click feels
          // responsive even before the first backend frame arrives.
          message = sessionCleanupState.sessionIndexControlLabel || "正在更新搜索索引...";
        } else if (paused) {
          message = `索引已暂停，已完成的 ${built} 个会话仍可搜索`;
          progressPhase = "已暂停 · 写入索引";
          progressMeta = `${percent}%${elapsedSpan}`;
        } else if (running && phase === "scanning") {
          message = "正在扫描本地会话文件，扫描完成后开始写入索引";
          progressPhase = "扫描会话文件 · 第 1/2 步";
          progressMeta = elapsedSpan ? elapsedSpan.slice(3) : "";
        } else if (running) {
          message = `正在为「${rangeLabel}」建立搜索索引，可收起此面板，任务在后台继续`;
          progressPhase = "写入索引 · 第 2/2 步";
          progressMeta = `${percent}%${remaining > 0 ? ` · 预计剩余 ${sessionIndexEtaLabel(remaining)}` : ""}${elapsedSpan}`;
        } else if (coverage === "full") {
          message = "已建立全部会话索引，可搜索全部历史会话";
          progressPhase = "已完成";
          progressMeta = "100%";
        } else if (coverage.indexOf("range_done") === 0) {
          message = `当前可搜索 ${rangeLabel}，更早的会话需要先扩展索引范围`;
          progressPhase = "已完成";
          progressMeta = `${percent}%`;
        } else if (jobState === "error") {
          message = "搜索索引建立失败，可重新选择范围再次开始，已有索引不受影响";
        } else {
          message = "尚未建立搜索索引，建立后可按标题、内容与文件名检索历史会话";
        }
        const extendOptions = sessionIndexExtendOptions(sessionIndex);
        const actions = [];
        // The empty-result codex-usage-hud-session-coverage-hint reuses this
        // same inline block and owns the range selector for extension.
        // Primary band: the action that moves the index forward. Range
        // selectors stay attached to their button so the pair reads as one
        // control instead of two unrelated widgets.
        if (paused) {
          actions.push(`<button type="button" class="codex-usage-hud-settings-action codex-usage-hud-session-index-action" data-action="session-index-resume" data-size="small" data-primary="true"${disabled}>继续</button>`);
        } else if (coverage === "empty" || jobState === "error") {
          const options = sessionIndexBuildOptions(sessionIndex)
            .map((item) => `<option value="${item.key}"${item.selected ? " selected" : ""}>${item.label}</option>`)
            .join("");
          actions.push(`<label class="codex-usage-hud-session-index-field">建立范围<select data-session-index-start="true"${controlDisabled}>${options}</select></label>`);
          actions.push(`<button type="button" class="codex-usage-hud-settings-action codex-usage-hud-session-index-action" data-action="session-index-start" data-size="small" data-primary="true"${disabled}>开始索引</button>`);
        }
        if (
          sessionIndex.canExtend === true
          && extendOptions.length
          && coverage !== "empty"
          && jobState !== "error"
        ) {
          const options = extendOptions.map((item) => `<option value="${item.key}">${item.label}</option>`).join("");
          actions.push(`<label class="codex-usage-hud-session-index-field">扩展到<select data-session-index-extend="true"${disabled}>${options}</select></label>`);
          actions.push(`<button type="button" class="codex-usage-hud-settings-action codex-usage-hud-session-index-action" data-action="session-index-extend" data-size="small" data-primary="true"${disabled}>${controlPending ? "处理中..." : "扩展索引"}</button>`);
        }
        // Progress band: the bar owns its stage label and timing meta, so the
        // headline never has to repeat the same numbers.
        const progressHtml = indexing || total > 0
          ? `<div class="codex-usage-hud-session-index-progress" data-state="${badge.state}"><div class="codex-usage-hud-session-index-progress-meta"><span>${escapeHtml(progressPhase)}</span><span>${progressMeta}</span></div><div class="codex-usage-hud-session-index-track" role="progressbar" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100" aria-label="搜索索引建立进度"><div class="codex-usage-hud-session-index-fill" data-phase="${phase === 'indexing' ? 'indexing' : ''}" data-indeterminate="${indexing && (total <= 0 || phase === 'scanning')}" style="width:${phase === 'scanning' ? 8 : Math.max(percent, indexing ? 6 : 0)}%"></div></div></div>`
          : "";
        // The empty-result hint reuses this structure in a tighter context;
        // data-variant lets each skin target its own layout instead of the two
        // fighting each other through class overrides.
        const variant = notice ? "hint" : "panel";
        const panelClass = notice
          ? "codex-usage-hud-session-index-coverage codex-usage-hud-session-coverage-hint"
          : "codex-usage-hud-session-index-coverage";
        const noticeHtml = notice
          ? `<p class="codex-usage-hud-session-index-notice">${cleanupIconSvg("alert")}<span>${escapeHtml(notice)}</span></p>`
          : "";
        const clearDisabled = controlPending ? " disabled aria-disabled=\"true\"" : "";
        // Secondary band sits opposite the primary action: configuration and
        // the destructive clear must not share a visual weight with 开始索引.
        const primaryHtml = actions.length
          ? `<div class="codex-usage-hud-session-index-actions-primary">${actions.join("")}</div>`
          : "";
        const secondaryHtml = `<div class="codex-usage-hud-session-index-actions-secondary"><label class="codex-usage-hud-session-index-switch"><input type="checkbox" data-session-index-enabled="true" ${enabled ? "checked" : ""}${controlPending ? " disabled" : ""} aria-label="启用搜索索引"><span class="codex-usage-hud-session-index-switch-track" aria-hidden="true"><span></span></span><span>索引功能</span><strong>${enabled ? "已开启" : "已关闭"}</strong></label><button type="button" class="codex-usage-hud-settings-action codex-usage-hud-session-index-clear" data-action="session-index-clear" data-size="small"${clearDisabled}>清除索引</button></div>`;
        return `<section class="${panelClass}" data-variant="${variant}" data-state="${badge.state}" data-job-state="${escapeHtml(jobState)}" data-coverage="${escapeHtml(coverage)}" aria-label="搜索索引">${noticeHtml}<header class="codex-usage-hud-session-index-head"><span class="codex-usage-hud-session-index-badge" data-state="${badge.state}"><span class="codex-usage-hud-session-index-badge-dot" aria-hidden="true"></span>${escapeHtml(badge.label)}</span><p class="codex-usage-hud-session-index-text">${message}</p>${sessionIndexMetricsHtml(sessionIndex)}</header>${progressHtml}<div class="codex-usage-hud-session-index-actions">${primaryHtml}${secondaryHtml}</div></section>`;
      }

      function sessionIndexDomainState() {
        // The warm job's status arrives on command responses and on the
        // event-driven ``sessionIndex`` settings domain. Cache the freshest
        // snapshot when it is present, then fall back to the payload shape.
        if (sessionCleanupState.sessionIndex && typeof sessionCleanupState.sessionIndex === "object") {
          return sessionCleanupState.sessionIndex.available === false
            ? null
            : sessionCleanupState.sessionIndex;
        }
        const payload = currentPayload();
        const payloadIndex = payload?.sessionIndex;
        const statusIndex = payload?.settingsCommandStatus?.sessionIndex;
        const candidate = (statusIndex && typeof statusIndex === "object" && Object.keys(statusIndex).length)
          ? statusIndex
          : (payloadIndex && typeof payloadIndex === "object" && Object.keys(payloadIndex).length
            ? payloadIndex
            : null);
        if (!candidate) return null;
        if (candidate.available === false) return null;
        return candidate;
      }

      function sessionCleanupPanelHtml() {
        const data = sessionCleanupFromPayload();
        const sessionIndex = sessionIndexDomainState();
        const scanned = !!String(data?.revision || "");
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const state = String(operation?.state || "idle");
        const operationAction = String(operation?.action || "");
        const busy = new Set(["scanning", "accepted", "running"]).has(state) || !!sessionCleanupState.pendingRequestId;
        const scanInProgress = new Set(["scan", "sessionCleanupScan"]).has(operationAction)
          && new Set(["scanning", "accepted"]).has(state);
        if (scanInProgress) {
          const phaseLabel = sessionCleanupPhaseLabel(operation);
          const progress = Math.max(0, Math.min(99, Number(operation?.progress || 0)));
          const phaseIndex = Math.max(1, Number(operation?.phaseIndex || 1));
          const phaseCount = Math.max(phaseIndex, Number(operation?.phaseCount || 3));
          const elapsed = formatSessionCleanupElapsed(sessionCleanupState.scanStartedAt);
          const mergedIndexHtml = sessionIndexScanMergeHtml() || "";
          return `<section class="codex-usage-hud-session-cleanup" aria-label="会话管理" aria-busy="true"><div class="codex-usage-hud-cleanup-scan-strip" aria-live="polite"><div class="codex-usage-hud-cleanup-scan-strip-top"><div class="codex-usage-hud-cleanup-scan-strip-title"><span class="codex-usage-hud-cleanup-mini-spinner"></span>扫描本地会话</div><div class="codex-usage-hud-cleanup-scan-strip-meta">第 ${phaseIndex}/${phaseCount} 步 · 约 ${progress || 1}% · 已用时 <span data-session-cleanup-elapsed="true">${escapeHtml(elapsed)}</span></div></div><div class="codex-usage-hud-cleanup-scan-track"><div class="codex-usage-hud-cleanup-scan-fill" data-indeterminate="${progress <= 0}" style="width:${Math.max(progress, 8)}%"></div></div><div class="codex-usage-hud-cleanup-scan-stage"><span>当前：<strong>${escapeHtml(phaseLabel || "读取会话索引")}</strong></span><span>筛选与删除在完成后解锁</span></div>${mergedIndexHtml}</div><div class="codex-usage-hud-cleanup-empty-state" style="min-height:180px"><div class="codex-usage-hud-cleanup-scan-mark" data-live="true">${cleanupIconSvg("trash", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">正在扫描会话</h2><p class="codex-usage-hud-cleanup-empty-meta">按主会话归并本地记录与关联子任务</p><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-cancel">取消扫描</button></div></section>`;
        }
        if (!data || !scanned) {
          return `<section class="codex-usage-hud-session-cleanup" aria-label="会话管理"><div class="codex-usage-hud-cleanup-empty-state"><div class="codex-usage-hud-cleanup-scan-mark">${cleanupIconSvg("trash", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">尚未扫描会话</h2><p class="codex-usage-hud-cleanup-empty-meta">按主会话整理本地记录，关联子任务会随主会话一起永久删除。</p><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-scan" data-primary="true" data-size="large" ${busy ? "disabled" : ""}>${busy ? "正在扫描..." : `${cleanupIconSvg("search")}扫描会话`}</button></div></section>`;
        }
        const capability = data?.capability && typeof data.capability === "object" ? data.capability : {};
        const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
        const rows = sessionCleanupRows(data);
        const pageRows = sessionCleanupPageRows(data);
        const pageCount = sessionCleanupPageCount(rows.length);
        const pageIndex = Math.min(
          Math.max(0, Math.floor(Number(sessionCleanupState.page || 0))),
          pageCount - 1,
        );
        const inventoryRevision = String(data?.revision || "");
        const searchResultActive = new Set(["completed", "indexing"]).has(sessionCleanupState.searchResultState)
          && !!String(sessionCleanupState.search || "").trim()
          && String(sessionCleanupState.searchResultQuery || "").trim().toLowerCase()
            === String(sessionCleanupState.search || "").trim().toLowerCase()
          && String(sessionCleanupState.searchResultRevision || "") === inventoryRevision;
        const searchKindsById = new Map(
          (Array.isArray(data?.search?.matchKinds) ? data.search.matchKinds : [])
            .filter((item) => item && typeof item === "object")
            .map((item) => [
              String(item?.id || ""),
              {
                kinds: Array.isArray(item?.kinds) ? item.kinds : [],
                score: Number(item?.score || 0),
              },
            ]),
        );
        const visibleSelectable = pageRows.filter((item) => item?.selectable === true && String(item?.id || ""));
        const allVisibleSelected = visibleSelectable.length > 0
          && visibleSelectable.every((item) => sessionCleanupState.selectedIds.has(String(item.id)));
        const rowHtml = pageRows.map((item) => {
          const id = String(item?.id || "");
          const selectable = item?.selectable === true && !!id;
          const checked = selectable && sessionCleanupState.selectedIds.has(id);
          const descendants = Math.max(0, Number(item?.descendantCount || 0));
          const updatedAt = item?.updatedAt ? backgroundUsageTime(item.updatedAt, { compact: true }) : "--";
          const client = sessionCleanupClientLabel(item?.clientKind);
          const provider = providerRegistryDisplayName(currentPayload()?.settings, item?.modelProvider || "unknown");
          const workdirName = String(item?.workdirName || "").trim();
          const hitDetail = searchResultActive ? (searchKindsById.get(id) || {}) : {};
          const hitKinds = Array.isArray(hitDetail) ? hitDetail : (hitDetail.kinds || []);
          const hitLabel = hitKinds.length
            ? `<span data-search-hit="true">${escapeHtml(hitKinds.map(sessionCleanupMatchKindLabel).join(" · "))}</span>`
            : "";
          const workdirButton = (position) => workdirName && id && inventoryRevision
            ? `<button type="button" class="codex-usage-hud-session-workdir" data-action="session-cleanup-open-workdir" data-session-cleanup-workdir-id="${escapeHtml(id)}" data-session-cleanup-inventory-revision="${escapeHtml(inventoryRevision)}" data-session-cleanup-workdir-position="${escapeHtml(position)}" aria-label="打开工作目录 ${escapeHtml(workdirName)}" title="打开工作目录：${escapeHtml(workdirName)}">${cleanupIconSvg("folder")}<span>${escapeHtml(workdirName)}</span></button>`
            : "";
          // Once Desktop reports it no longer holds the thread the same slot
          // becomes a terminal `codex resume` fallback instead of a dead jump.
          const jumpUnavailable = sessionCleanupState.sessionJumpUnavailableIds.has(id);
          const jumpInflight = sessionCleanupState.sessionJumpInflight.has(id);
          const jumpLabel = jumpUnavailable
            ? "Codex Desktop 未保留此会话，在终端用 codex resume 恢复"
            : "在 Codex Desktop 中打开此会话";
          const jumpKind = jumpUnavailable ? "fallback" : "jump";
          const jumpClass = jumpInflight
            ? `codex-usage-hud-session-jump codex-usage-hud-session-jump-inflight`
            : `codex-usage-hud-session-jump`;
          const jumpButton = (position) => id && inventoryRevision
            ? `<button type="button" class="${jumpClass}" data-action="${jumpUnavailable ? "session-cleanup-resume-session" : "session-cleanup-open-session"}" data-session-cleanup-jump-id="${escapeHtml(id)}" data-session-cleanup-inventory-revision="${escapeHtml(inventoryRevision)}" data-session-cleanup-jump-position="${escapeHtml(position)}" data-kind="${jumpKind}" aria-label="${escapeHtml(jumpLabel)}" title="${escapeHtml(jumpLabel)}" ${jumpInflight ? "disabled" : ""}>${cleanupIconSvg(jumpUnavailable ? "terminal" : "external")}</button>`
            : "";
          const secondaryMeta = [
            updatedAt,
            `${client} · ${provider}`,
            descendants ? `含 ${descendants} 个关联子任务` : "无关联子任务",
          ].filter(Boolean).join(" · ");
          const secondaryWorkdir = workdirButton("secondary");
          const secondaryJump = jumpButton("secondary");
          const secondary = `${secondaryWorkdir}${secondaryWorkdir && secondaryMeta ? " · " : ""}${escapeHtml(secondaryMeta)}${secondaryJump ? ` ${secondaryJump}` : ""}`;
          const related = [client, provider, descendants ? `含 ${descendants} 个关联子任务` : "无关联子任务"].join(" · ");
          const status = String(item?.status || "idle");
          const archivedBadge = item?.archived === true && status !== "archived"
            ? `<span class="codex-usage-hud-session-badge" data-state="archived">已归档</span>`
            : "";
          const workdirColumn = `<span class="codex-usage-hud-session-workdir-cell">${workdirButton("column")
            || `<span class="codex-usage-hud-session-workdir" title="未记录工作目录">--</span>`}${jumpButton("column")}</span>`;
          return `<div class="codex-usage-hud-session-row" data-selectable="${selectable}" data-selected="${checked}"><label class="codex-usage-hud-session-select"><input type="checkbox" data-session-cleanup-id="${escapeHtml(id)}" aria-label="选择会话 ${escapeHtml(item?.title || "未命名会话")}" ${checked ? "checked" : ""} ${selectable ? "" : "disabled"}></label><div class="codex-usage-hud-session-title"><strong title="${escapeHtml(item?.title || "未命名会话")}">${escapeHtml(item?.title || "未命名会话")}</strong>${hitLabel}<span>${escapeHtml(related)}</span><span data-secondary="true">${secondary}</span>${item?.blockedReason ? `<span data-kind="warning">${escapeHtml(sessionCleanupReasonLabel(item.blockedReason))}</span>` : ""}</div>${workdirColumn}<span class="codex-usage-hud-session-cell">${escapeHtml(updatedAt)}</span><span class="codex-usage-hud-session-status-cell"><span class="codex-usage-hud-session-badge" data-state="${escapeHtml(status)}">${escapeHtml(sessionCleanupStatusLabel(item))}</span>${archivedBadge}</span><span class="codex-usage-hud-session-size">${storageFormatBytes(item?.bytes)}</span></div>`;
        }).join("");
        const results = Array.isArray(operation?.results) ? operation.results : [];
        const showResultDetails = results.length > 0 && state !== "completed";
        const resultHtml = showResultDetails ? `<div class="codex-usage-hud-session-results"><strong>${state === "partial" ? "部分完成" : "删除失败"}</strong>${results.map((item) => `<div><span>${escapeHtml(item?.title || "会话")}</span><span data-kind="${item?.state === "deleted" ? "success" : "error"}">${escapeHtml(item?.state === "deleted" ? "已永久删除" : item?.error || "删除失败")}</span></div>`).join("")}</div>` : "";
        const unavailable = capability?.available === false ? `<div class="codex-usage-hud-session-capability" data-kind="warning">${escapeHtml(sessionCleanupReasonLabel(capability?.reason) || "本机会话存储不可用，会话清单保持只读。")}</div>` : "";
        const pagination = rows.length > 0
          ? `<div class="codex-usage-hud-session-pagination" data-session-cleanup-pagination="true"><span>第 ${pageIndex + 1} / ${pageCount} 页 · 共 ${rows.length} 项</span><div><button type="button" data-action="session-cleanup-page" data-direction="prev" data-session-cleanup-page-direction="prev" aria-label="上一页" title="上一页" ${pageIndex <= 0 ? "disabled" : ""}>${cleanupIconSvg("chevron", "codex-usage-hud-session-page-prev")}</button><button type="button" data-action="session-cleanup-page" data-direction="next" data-session-cleanup-page-direction="next" aria-label="下一页" title="下一页" ${pageIndex >= pageCount - 1 ? "disabled" : ""}>${cleanupIconSvg("chevron")}</button></div></div>`
          : "";
        const providers = Array.from(new Set(sessions.map((item) => String(item?.modelProvider || "unknown")))).sort();
        const control = (key, label, options) => `<div class="codex-usage-hud-session-filter-control"><label><span>${label}</span><select data-session-cleanup-filter="${key}" aria-label="${label}">${sessionCleanupFilterOptionHtml(options, sessionCleanupState[key])}</select></label></div>`;
        const dateError = sessionCleanupDateRangeError(sessionCleanupState.dateDraftStart, sessionCleanupState.dateDraftEnd);
        const datePopover = sessionCleanupState.datePickerOpen ? `<div class="codex-usage-hud-session-date-popover" role="dialog" aria-label="最后活动时间"><div class="codex-usage-hud-session-date-fields"><label>开始时间<input type="datetime-local" data-session-cleanup-date-start="true" value="${escapeHtml(sessionCleanupState.dateDraftStart)}"></label><span class="codex-usage-hud-session-date-separator">至</span><label>结束时间<input type="datetime-local" data-session-cleanup-date-end="true" value="${escapeHtml(sessionCleanupState.dateDraftEnd)}"></label></div><div class="codex-usage-hud-session-date-presets">${[["today", "今天"], ["7d", "近 7 天"], ["week", "本周"], ["30d", "近 30 天"], ["month", "本月"], ["older", "30 天前"]].map(([value, label]) => `<button type="button" data-action="session-cleanup-date-preset" data-session-cleanup-date-preset="${value}">${label}</button>`).join("")}</div>${dateError ? `<div class="codex-usage-hud-session-date-error">${dateError}</div>` : ""}<div class="codex-usage-hud-session-date-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-date-reset">清除</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-date-confirm" data-primary="true" ${dateError ? "disabled" : ""}>确认</button></div></div>` : "";
        const controls = [
          control("archive", "归档状态", [["all", "全部"], ["archived", "已归档"], ["unarchived", "未归档"]]),
          control("availability", "删除状态", [["all", "全部"], ["selectable", "可永久删除"], ["protected", "受保护"], ["current", "当前会话"], ["running", "运行中"], ["unresolved", "映射无法确认"], ["unavailable", "暂不可删除"]]),
          control("clientKind", "客户端", [["all", "全部"], ["app", "Codex App"], ["cli", "CLI"], ["unknown", "来源未知"]]),
          control("modelProvider", "模型提供方", [["all", "全部"], ...providers.map((value) => [value, providerRegistryDisplayName(currentPayload()?.settings, value)])]),
          control("sort", "排序", [["recent", "最后活动最近"], ["oldest", "最后活动最早"], ["largest", "占用最大"]]),
        ].join("");
        const workdirControl = `<div class="codex-usage-hud-session-filter-control codex-usage-hud-session-workdir-filter"><select data-session-cleanup-filter="workdirId" aria-label="工作目录">${sessionCleanupWorkdirOptionHtml(data)}</select></div>`;
        const searchButtonLabel = sessionCleanupState.searchRequestId ? "搜索中" : "搜索";
        // PRD §6.4 / §D1: honest coverage hint. When the index does not cover
        // all history, a search that returns "no rows" must be distinguishable
        // from "no matching sessions". Show the hint on an empty result set
        // whenever coverage is partial; the hint itself carries a direct
        // extend entry (select + button) so the user can widen the window
        // without scrolling back to the banner.
        const coverageHint = ((() => {
          const sessionIndex = sessionIndexDomainState();
          if (!sessionIndex) return "";
          const coverage = String(sessionIndex.coverage || "empty");
          const searching = Boolean(String(sessionCleanupState.search || "").trim());
          if (coverage === "full" || !searching || rowHtml) return "";
          const range = String(sessionIndex.selectedRange || "1m");
          const rangeLabel = {
            "1m": "最近 1 个月", "3m": "最近 3 个月", "6m": "最近 6 个月",
            "1y": "最近 1 年", "all": "全部",
          }[range] || range;
          if (range === "all") return "";
          return `没搜到结果，可能因为索引只覆盖了 ${rangeLabel}。更早的会话需要先扩展索引范围。`;
        })());
        // The drawer element stays mounted so the open/close transition has
        // something to animate; only its data-open flips.
        const indexBody = sessionIndexPanelHtml({ notice: coverageHint });
        const indexVisible = Boolean(indexBody) && sessionIndexPanelVisible(Boolean(coverageHint));
        const indexPanel = indexBody
          ? `<div class="codex-usage-hud-session-index-drawer" data-open="${indexVisible ? "true" : "false"}"${indexVisible ? "" : " inert"}><div class="codex-usage-hud-session-index-drawer-inner">${indexBody}</div></div>`
          : "";
        const searchPreparing = String(sessionCleanupState.search || "").trim()
          && new Set(["pending", "indexing"]).has(sessionCleanupState.searchResultState);
        const emptyState = rowHtml ? "" : `<div class="codex-usage-hud-cleanup-empty"><div class="codex-usage-hud-cleanup-empty-mark">${cleanupIconSvg("search", "codex-usage-hud-cleanup-icon-lg")}</div><p class="codex-usage-hud-cleanup-empty-title">${searchPreparing ? "搜索索引准备中" : "当前筛选没有会话"}</p><p class="codex-usage-hud-cleanup-empty-hint">${searchPreparing ? "准备完成后将自动更新搜索结果，无需重新打开界面" : "试试调整筛选条件，或清除筛选后重新查看"}</p></div>`;
        const indexToggle = sessionIndexToggleHtml(sessionIndex, indexVisible);
        return `<section class="codex-usage-hud-session-cleanup" aria-label="会话管理">${unavailable}<div class="codex-usage-hud-session-tools"><div class="codex-usage-hud-session-tools-primary"><div class="codex-usage-hud-session-search">${cleanupIconSvg("search")}<input type="search" data-session-cleanup-search="true" value="${escapeHtml(sessionCleanupState.searchDraft)}" placeholder="搜索会话、内容或文件" aria-label="搜索会话"><button type="button" class="codex-usage-hud-session-search-submit" data-action="session-cleanup-search-submit" aria-label="开始搜索">${cleanupIconSvg("search")}<span>${searchButtonLabel}</span></button></div><div class="codex-usage-hud-session-index-workdir">${indexToggle}${workdirControl}</div><div class="codex-usage-hud-session-date-filter" data-open="${sessionCleanupState.datePickerOpen}"><button type="button" class="codex-usage-hud-session-date-trigger" data-action="session-cleanup-date-toggle" aria-expanded="${sessionCleanupState.datePickerOpen ? "true" : "false"}" aria-haspopup="dialog">${cleanupIconSvg("calendar")}<span>最后活动：${escapeHtml(sessionCleanupDateRangeLabel())}</span>${cleanupIconSvg("chevron")}</button>${datePopover}</div></div>${indexPanel}<div class="codex-usage-hud-session-filter-controls">${controls}</div>${sessionCleanupFilterSummary(data, rows)}</div><div class="codex-usage-hud-session-table"><div class="codex-usage-hud-session-head"><span><input type="checkbox" data-session-cleanup-select-all="true" ${allVisibleSelected ? "checked" : ""} ${visibleSelectable.length ? "" : "disabled"} aria-label="全选当前页"></span><span>会话</span><span aria-hidden="true"></span><span>最后活动</span><span>状态</span><span>占用</span></div>${rowHtml || emptyState}</div>${pagination}${resultHtml}</section>`;
      }

      function captureStorageUiState() {
        const modal = document.getElementById(settingsModalId);
        const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
        if (body) storageBodyScrollTop = body.scrollTop;
        const cleanupContent = modal?.querySelector?.(".codex-usage-hud-cleanup-content");
        if (cleanupContent) cleanupContentScrollTop = cleanupContent.scrollTop;
        const sessionTable = modal?.querySelector?.(".codex-usage-hud-session-table");
        if (sessionTable) sessionTableScrollTop = sessionTable.scrollTop;
      }

      function restoreStorageUiState() {
        const modal = document.getElementById(settingsModalId);
        const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
        if (body) body.scrollTop = storageBodyScrollTop;
        const cleanupContent = modal?.querySelector?.(".codex-usage-hud-cleanup-content");
        if (cleanupContent) cleanupContent.scrollTop = cleanupContentScrollTop;
        const sessionTable = modal?.querySelector?.(".codex-usage-hud-session-table");
        if (sessionTable) sessionTable.scrollTop = sessionTableScrollTop;
      }

      function captureStorageFocus(body) {
        const active = document.activeElement;
        if (!body || !(active instanceof HTMLElement) || !body.contains(active)) return null;
        const identityAttributes = [
          "data-action",
          "data-cleanup-group-id",
          "data-item-id",
          "data-session-cleanup-id",
          "data-setting-key",
          "data-cleanup-backup-directory",
          "data-cleanup-consent",
          "data-cleanup-auto-close",
          "data-session-cleanup-search",
          "data-session-cleanup-select-all",
          "data-session-index-enabled",
        ];
        const identity = identityAttributes
          .filter((name) => active.hasAttribute(name))
          .map((name) => [name, active.getAttribute(name) || ""]);
        if (!identity.length) return null;
        return {
          tagName: active.tagName.toLowerCase(),
          identity,
          selectionStart: typeof active.selectionStart === "number" ? active.selectionStart : null,
          selectionEnd: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
        };
      }

      function restoreStorageFocus(body, descriptor) {
        if (!body || !descriptor || !Array.isArray(descriptor.identity)) return;
        const candidates = body.querySelectorAll(descriptor.tagName || "*");
        for (const candidate of candidates) {
          if (!descriptor.identity.every(([name, value]) => candidate.getAttribute(name) === value)) continue;
          if (candidate.matches?.(":disabled") || candidate.disabled === true) return;
          try {
            candidate.focus({ preventScroll: true });
          } catch (_) {
            candidate.focus?.();
          }
          if (
            typeof descriptor.selectionStart === "number"
            && typeof candidate.setSelectionRange === "function"
          ) {
            try {
              candidate.setSelectionRange(descriptor.selectionStart, descriptor.selectionEnd ?? descriptor.selectionStart);
            } catch (_) {}
          }
          return;
        }
      }

      function scheduleStoragePanelRefresh({ throttleMs = 0 } = {}) {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "storage") return false;
        const now = performance.now();
        const delay = Math.max(0, Number(throttleMs || 0) - (now - storageRefreshLastAt));
        const queueFrame = () => {
          if (storageRefreshRaf) return;
          storageRefreshRaf = ctx.frames.schedule("storage", () => {
            storageRefreshRaf = 0;
            storageRefreshLastAt = performance.now();
            refreshStoragePanelIfVisible();
          });
        };
        if (delay > 0) {
          if (!storageRefreshTimer) {
            storageRefreshTimer = ctx.lifecycle.timeout("storage", () => {
              storageRefreshTimer = 0;
              queueFrame();
            }, delay);
          }
          return true;
        }
        queueFrame();
        return true;
      }

      function refreshStoragePanelIfVisible() {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "storage") return false;
        const body = modal.querySelector(".codex-usage-hud-settings-body");
        if (!body) return false;
        const focus = captureStorageFocus(body);
        captureStorageUiState();
        body.innerHTML = storagePanelHtml();
        restoreStorageUiState();
        restoreStorageFocus(body, focus);
        // Keep the index "已用时" readout ticking while the job runs; the
        // function stops its own timer once the job leaves the running state.
        ensureSessionIndexElapsedTicker();
        return true;
      }

      function requestSessionCleanupCancel() {
        const transferRequestId = sessionTransferState.open
          ? String(sessionTransferState.scanRequestId || "").trim()
          : "";
        const requestId = typedSettingsRequestId("session-cleanup-cancel");
        const submitted = submitSettingsCommand(
          { action: "sessionCleanupCancel", requestId },
          "正在取消会话扫描...",
          { preserveOverlay: true },
        );
        if (submitted) {
          sessionCleanupState.pendingRequestId = "";
          sessionCleanupState.scanStartedAt = 0;
          stopSessionCleanupScanWatchdog();
          stopSessionCleanupElapsedTicker();
          if (transferRequestId) {
            sessionTransferState.scanRequestId = "";
            sessionTransferState.cancelledRequestId = transferRequestId;
            sessionTransferState.startedAt = 0;
            sessionTransferState.operation = {
              ...(sessionTransferState.operation && typeof sessionTransferState.operation === "object"
                ? sessionTransferState.operation
                : {}),
              action: "cancel",
              state: "cancelled",
              requestId: transferRequestId,
              progress: 100,
            };
            if (sessionTransferState.open) renderSessionTransferDialog();
          }
        }
        return submitted;
      }

      function requestSessionCleanupScan({ preserveTransfer = false } = {}) {
        if (sessionCleanupState.pendingRequestId || sessionCleanupScanActive()) return false;
        const requestId = typedSettingsRequestId("session-cleanup-scan");
        const transferOpen = preserveTransfer && sessionTransferState.open;
        const submitted = submitSettingsCommand(
          { action: "sessionCleanupScan", requestId },
          "正在扫描本地会话清单...",
          { preserveOverlay: true },
        );
        if (!submitted) {
          return false;
        }
        // The bridge call itself is the delivery acknowledgement available to
        // the renderer.  Only now do we enter the pending state that disables
        // controls and starts elapsed-time tracking.
        sessionCleanupState.pendingRequestId = requestId;
        sessionCleanupState.scanStartedAt = Date.now();
        sessionCleanupState.selectedIds.clear();
        sessionCleanupState.previewTokenShown = "";
        if (transferOpen) {
          sessionTransferState.scanRequestId = requestId;
          sessionTransferState.startedAt = sessionCleanupState.scanStartedAt;
          sessionTransferState.cancelledRequestId = "";
          sessionTransferState.operation = {
            action: "sessionCleanupScan",
            state: "scanning",
            requestId,
            progress: 0,
          };
          sessionTransferState.selectedIds.clear();
          renderSessionTransferDialog();
        } else {
          renderSettingsModal("storage", "正在扫描本地会话清单...");
        }
        scheduleSessionCleanupScanWatchdog(requestId);
        return true;
      }

      function requestSessionCleanupPreview() {
        const data = sessionCleanupFromPayload();
        const itemIds = Array.from(sessionCleanupState.selectedIds);
        const revision = String(data?.revision || "");
        if (!revision || !itemIds.length) {
          setSettingsStatus("请先扫描并选择可删除会话。", "error");
          return false;
        }
        const requestId = typedSettingsRequestId("session-cleanup-preview");
        sessionCleanupState.pendingRequestId = requestId;
        sessionCleanupState.previewTokenShown = "";
        const submitted = submitSettingsCommand({
          action: "sessionCleanupPreview",
          requestId,
          itemIds,
          inventoryRevision: revision,
        }, "正在生成永久删除确认...", { preserveOverlay: true });
        if (!submitted) sessionCleanupState.pendingRequestId = "";
        return submitted;
      }

      function openSessionCleanupExecuteConfirm() {
        const dialog = settingsDialogRoot();
        const data = sessionCleanupFromPayload();
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const token = String(operation?.confirmationToken || "");
        if (!dialog || String(operation?.state || "") !== "preview" || !token) return;
        const selectedIds = Array.isArray(operation?.selectedIds) ? operation.selectedIds : [];
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.dataset.sessionCleanupConfirm = "true";
        layer.dataset.sessionCleanupConfirmToken = token;
        const descendants = Math.max(0, Number(operation?.descendantCount || 0));
        const estimatedBytes = storageFormatBytes(operation?.estimatedBytes);
        layer.innerHTML = `<div class="codex-usage-hud-settings-confirm-card" data-tone="danger" role="alertdialog" aria-modal="true" aria-label="确认永久删除会话"><div class="codex-usage-hud-settings-confirm-main"><div class="codex-usage-hud-settings-confirm-danger-mark">${cleanupIconSvg("trash", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-settings-confirm-title">永久删除 ${selectedIds.length} 个会话？</h2><p class="codex-usage-hud-settings-confirm-body">会话内容、索引和关联子任务将从本机移除。此操作不会进入回收站，也无法恢复。Codex App 的归档入口无法恢复这些会话。</p><div class="codex-usage-hud-settings-confirm-summary"><div><span>主会话</span><strong>${selectedIds.length}</strong></div><div><span>关联子任务</span><strong>${descendants}</strong></div><div><span>本地数据</span><strong>${escapeHtml(estimatedBytes)}</strong></div></div><div class="codex-usage-hud-settings-confirm-note">${cleanupIconSvg("alert")}<span>执行前会再次核验会话身份与运行状态；任一异常都会取消整批删除。</span></div></div><div class="codex-usage-hud-settings-confirm-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-confirm-cancel">取消</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-execute" data-danger="true">${cleanupIconSvg("trash")}永久删除</button></div></div>`;
        dialog.appendChild(layer);
        layer.querySelector('[data-action="session-cleanup-confirm-cancel"]')?.focus?.();
      }

      function restoreSessionCleanupConfirm(expectedToken) {
        const token = String(expectedToken || "");
        if (!token) return;
        queueMicrotask(() => {
          const modal = document.getElementById(settingsModalId);
          const data = sessionCleanupFromPayload();
          const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
          const currentToken = String(operation?.confirmationToken || "");
          if (
            !modal
            || modal.hidden
            || settingsActiveTab !== "storage"
            || String(operation?.state || "") !== "preview"
            || currentToken !== token
          ) return;
          const existing = modal.querySelector('[data-session-cleanup-confirm="true"]');
          if (String(existing?.dataset.sessionCleanupConfirmToken || "") === token) return;
          openSessionCleanupExecuteConfirm();
        });
      }

      function openSessionCleanupDeleteLoading(requestId) {
        openSettingsLoading({
          kicker: "正在删除",
          title: "正在永久删除会话",
          body: "正在更新本机索引和会话列表，请勿关闭此窗口。",
          mode: "session-cleanup-delete",
        });
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        if (!layer) return;
        layer.dataset.sessionCleanupDeleteLoading = "true";
        layer.dataset.sessionCleanupDeleteRequestId = String(requestId || "");
      }

      function executeSessionCleanup() {
        const data = sessionCleanupFromPayload();
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const itemIds = Array.isArray(operation?.selectedIds) ? operation.selectedIds : [];
        const requestId = typedSettingsRequestId("session-cleanup-execute");
        sessionCleanupState.pendingRequestId = requestId;
        openSessionCleanupDeleteLoading(requestId);
        const submitted = submitSettingsCommand({
          action: "sessionCleanupExecute",
          requestId,
          itemIds,
          inventoryRevision: String(data?.revision || operation?.inventoryRevision || ""),
          confirmationToken: String(operation?.confirmationToken || ""),
        }, "正在以本地事务永久删除会话...", { preserveOverlay: true });
        if (!submitted) {
          sessionCleanupState.pendingRequestId = "";
          closeSettingsConfirm();
          restoreSessionCleanupConfirm(String(operation?.confirmationToken || ""));
        }
        return submitted;
      }

      function applySessionCleanupSearchState(data) {
        const incoming = data?.search;
        if (!incoming || typeof incoming !== "object") return false;
        const query = String(incoming.query || "").trim();
        const responseRequestId = String(incoming.requestId || "");
        const expectedRequestId = String(sessionCleanupState.searchRequestId || "");
        if (query.toLowerCase() !== String(sessionCleanupState.search || "").trim().toLowerCase()) return false;
        if (String(incoming.workdirId || "") !== String(sessionCleanupState.workdirId || "")) return false;
        // Non-empty request ids are direct command responses. Empty ids are
        // unsolicited resident-index updates and are accepted only when no
        // newer query request is waiting; an empty/mismatched request id is a
        // stale response while that newer request is pending.
        if (responseRequestId) {
          if (!expectedRequestId || responseRequestId !== expectedRequestId) return false;
        } else if (expectedRequestId) {
          return false;
        }
        const incomingGeneration = Number(incoming.generation || 0);
        const currentGeneration = Number(sessionCleanupState.searchResultGeneration || 0);
        if (!responseRequestId && incomingGeneration < currentGeneration) return false;
        sessionCleanupState.searchResultQuery = query;
        sessionCleanupState.searchResultRevision = String(incoming.revision || data?.revision || "");
        sessionCleanupState.searchResultState = String(incoming.state || "idle");
        sessionCleanupState.searchResultGeneration = incomingGeneration;
        sessionCleanupState.searchResultMatches = new Set(
          Array.isArray(incoming.matches)
            ? incoming.matches.map((item) => String(item || "")).filter(Boolean)
            : [],
        );
        sessionCleanupState.searchResultDetails = new Map(
          Array.isArray(incoming.matchKinds)
            ? incoming.matchKinds
              .filter((item) => item && typeof item === "object" && String(item?.id || ""))
              .map((item) => [String(item.id), item])
            : [],
        );
        sessionCleanupState.searchResultTokens = Array.isArray(incoming.searchTokens)
          ? incoming.searchTokens.filter((token) => String(token || "").trim())
          : [];
        sessionCleanupState.searchRequestId = "";
        return true;
      }

      function stopSessionCleanupSearchTimer() {
        if (sessionCleanupSearchTimer) {
          ctx.lifecycle.clearTimeout(sessionCleanupSearchTimer);
          sessionCleanupSearchTimer = 0;
        }
      }

      function requestSessionCleanupSearch(query = sessionCleanupState.search) {
        const text = String(query || "");
        const requestId = typedSettingsRequestId("session-cleanup-search");
        sessionCleanupState.searchRequestId = requestId;
        sessionCleanupState.searchResultQuery = text;
        sessionCleanupState.searchResultRevision = "";
        sessionCleanupState.searchResultState = "pending";
        sessionCleanupState.searchResultMatches = new Set();
        sessionCleanupState.searchResultDetails = new Map();
        const submitted = submitSettingsCommand(
          {
            action: "sessionCleanupSearch",
            requestId,
            query: text,
            workdirId: String(sessionCleanupState.workdirId || ""),
          },
          text.trim() ? "正在搜索会话内容..." : "正在清除搜索...",
          { preserveOverlay: true, quiet: true },
        );
        if (!submitted) sessionCleanupState.searchRequestId = "";
        return submitted;
      }

      function requestSessionCleanupWorkdirOptions({ force = false } = {}) {
        if (force) sessionCleanupState.workdirOptionsRetryBlocked = false;
        if (sessionCleanupState.workdirOptionsRetryBlocked) return false;
        if (sessionCleanupState.workdirOptionsRequestId) return false;
        const requestId = typedSettingsRequestId("session-cleanup-workdirs");
        sessionCleanupState.workdirOptionsRequestId = requestId;
        const submitted = submitSettingsCommand(
          { action: "sessionCleanupWorkdirOptions", requestId },
          "正在读取工作目录选项...",
          { preserveOverlay: true },
        );
        if (!submitted) sessionCleanupState.workdirOptionsRequestId = "";
        return submitted;
      }

      function requestSessionIndexControl(command = {}, pendingLabel = "正在更新搜索索引...") {
        const wasAttached = sessionCleanupState.sessionIndexUiAttached === true;
        const requestId = String(command?.requestId || typedSettingsRequestId("session-index"));
        const request = {
          ...command,
          action: "sessionIndexControl",
          requestId,
        };
        sessionCleanupState.sessionIndexUiAttached = true;
        sessionCleanupState.sessionIndexControlRequestId = requestId;
        sessionCleanupState.sessionIndexControlLabel = pendingLabel;
        const submitted = submitSettingsCommand(request, pendingLabel, { preserveOverlay: true });
        if (submitted) {
          refreshStoragePanelIfVisible();
          ctx.lifecycle.timeout("session_index_control_watchdog", () => {
            if (sessionCleanupState.sessionIndexControlRequestId !== requestId) return;
            sessionCleanupState.sessionIndexControlRequestId = "";
            sessionCleanupState.sessionIndexControlLabel = "";
            refreshStoragePanelIfVisible();
            setSettingsStatus("索引控制命令未收到响应，请重试。", "error");
          }, 15000);
        } else {
          sessionCleanupState.sessionIndexControlRequestId = "";
          sessionCleanupState.sessionIndexControlLabel = "";
          sessionCleanupState.sessionIndexUiAttached = wasAttached;
        }
        return submitted;
      }

      function requestSessionIndexEnabled(enabled) {
        const desired = enabled === true;
        const command = { control: desired ? "enable" : "disable" };
        if (desired) {
          const startSelect = document
            .getElementById(settingsModalId)
            ?.querySelector?.('select[data-session-index-start="true"]');
          const range = String(startSelect?.value || "").trim().toLowerCase();
          if (range) command.range = range;
        }
        return requestSessionIndexControl(
          command,
          desired ? "正在启用搜索索引..." : "正在关闭搜索索引...",
        );
      }

      function requestSessionIndexClear() {
        return requestSessionIndexControl(
          { control: "clear" },
          "正在清除搜索索引...",
        );
      }

      function openSessionIndexClearConfirm() {
        const dialog = settingsDialogRoot();
        if (!dialog) return false;
        const sessionIndex = sessionIndexDomainState() || {};
        const diskBytes = Math.max(0, Number(sessionIndex.diskBytes || 0));
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.dataset.sessionIndexClearConfirm = "true";
        layer.innerHTML = `<div class="codex-usage-hud-settings-confirm-card" data-tone="danger" role="alertdialog" aria-modal="true" aria-label="确认清除搜索索引"><div class="codex-usage-hud-settings-confirm-kicker">本地搜索索引</div><div class="codex-usage-hud-settings-confirm-title">清除搜索索引？</div><div class="codex-usage-hud-settings-confirm-body">将关闭自动索引，并从磁盘删除当前索引及其本地快照（约 ${storageFormatBytes(diskBytes)}），但不会删除任何会话内容。清除后可重新开启索引并选择最近 1 个月、3 个月、6 个月、1 年或全部范围建立索引。</div><div class="codex-usage-hud-settings-confirm-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-index-clear-cancel" data-variant="ghost">取消</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-index-clear-confirm" data-danger="true" data-primary="true">${cleanupIconSvg("trash")}清除索引</button></div></div>`;
        dialog.appendChild(layer);
        layer.querySelector('[data-action="session-index-clear-cancel"]')?.focus?.();
        return true;
      }

      function resetSessionCleanupPendingRequests() {
        stopSessionCleanupSearchTimer();
        sessionCleanupState.searchRequestId = "";
        sessionCleanupState.workdirOptionsRequestId = "";
        sessionCleanupState.searchResultQuery = "";
        sessionCleanupState.searchResultRevision = "";
        sessionCleanupState.searchResultState = "idle";
        sessionCleanupState.searchResultGeneration = 0;
        sessionCleanupState.searchResultMatches = new Set();
        sessionCleanupState.searchResultDetails = new Map();
      }

      function applySessionCleanupPayload(_root, payload) {
        const incoming = payload?.sessionCleanup;
        let data = sessionCleanupState.data;
        const previousRevision = String(sessionCleanupState.data?.revision || "");
        if (incoming && typeof incoming === "object") {
          data = sessionCleanupPayloadWithInventory(incoming);
          // A full inventory payload is the freshest version. A partial
          // operation-only delta keeps the previous session list, so only a
          // payload carrying ``sessions`` (or a revision bump) replaces it.
          if (Array.isArray(incoming.sessions) || previousRevision !== String(data?.revision || "")) {
            sessionCleanupState.data = data;
          } else {
            sessionCleanupState.data = { ...sessionCleanupState.data, ...data };
          }
        }
        const statusIndex = payload?.settingsCommandStatus?.sessionIndex;
        if (statusIndex && typeof statusIndex === "object" && Object.keys(statusIndex).length) {
          sessionCleanupState.sessionIndex = { ...statusIndex };
          sessionCleanupState.sessionIndexRefreshing = false;
        }
        if (
          Array.isArray(incoming.sessions)
          && previousRevision
          && previousRevision !== String(data?.revision || "")
        ) {
          sessionCleanupState.page = 0;
        }
        const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
        let requestDetailedWorkdirs = false;
        if (
          Array.isArray(data?.workdirs)
        ) {
          const existingOptions = Array.isArray(sessionCleanupState.workdirOptions)
            ? sessionCleanupState.workdirOptions
            : [];
          const byId = new Map(
            existingOptions
              .filter((item) => item && typeof item === "object" && String(item?.id || ""))
              .map((item) => [String(item.id), item]),
          );
          const hadDetailed = existingOptions.some((item) => item?.path);
          for (const item of data.workdirs) {
            const id = String(item?.id || "");
            if (!id) continue;
            if (byId.has(id)) byId.set(id, { ...byId.get(id), ...item });
            else {
              byId.set(id, item);
              if (hadDetailed && !item?.path) requestDetailedWorkdirs = true;
            }
          }
          sessionCleanupState.workdirOptions = Array.from(byId.values());
        }
        applySessionCleanupSearchState(data);
        if (
          sessionCleanupState.workdirId
          && !sessionCleanupState.workdirOptions.some(
            (item) => String(item?.id || "") === String(sessionCleanupState.workdirId),
          )
        ) {
          sessionCleanupState.workdirId = "";
          persistSessionCleanupFilters();
        }
        if (requestDetailedWorkdirs) requestSessionCleanupWorkdirOptions();
        const validIds = new Set(sessions.filter((item) => item?.selectable === true).map((item) => String(item?.id || "")));
        sessionCleanupState.selectedIds = new Set(
          Array.from(sessionCleanupState.selectedIds).filter((id) => validIds.has(id)),
        );
        const providers = new Set(sessions.map((item) => String(item?.modelProvider || "unknown")));
        if (sessionCleanupState.modelProvider !== "all" && !providers.has(sessionCleanupState.modelProvider)) {
          sessionCleanupState.modelProvider = "all";
          persistSessionCleanupFilters();
        }
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const state = String(operation?.state || "").toLowerCase();
        const responseRequestId = String(operation?.requestId || "");
        const pendingRequestId = String(sessionCleanupState.pendingRequestId || "");
        const scanTerminal = new Set(["completed", "partial", "failed", "cancelled"]).has(state)
          && new Set(["scan", "sessioncleanupscan"]).has(String(operation?.action || "").toLowerCase());
        const inventoryMutationTerminal = new Set(["completed", "partial", "failed", "cancelled"]).has(state)
          && new Set(["execute", "providerdelete", "sessiontransfer"]).has(
            String(operation?.action || "").toLowerCase(),
          );
        if (
          !new Set(["scanning", "accepted", "running"]).has(state)
          && (!pendingRequestId || responseRequestId === pendingRequestId)
        ) {
          sessionCleanupState.pendingRequestId = "";
          if (new Set(["scan", "sessionCleanupScan"]).has(String(operation?.action || ""))) {
            sessionCleanupState.scanStartedAt = 0;
            if (!pendingRequestId || responseRequestId === pendingRequestId) {
              stopSessionCleanupScanWatchdog();
            }
          }
        }
        rerenderUsageInsightsIfVisible();
        const loadingLayer = document.querySelector(
          `#${settingsModalId} [data-session-cleanup-delete-loading="true"]`,
        );
        const loadingRequestId = String(
          loadingLayer?.dataset.sessionCleanupDeleteRequestId || "",
        );
        const completedExecute = new Set(["completed", "partial", "failed"]).has(state)
          && new Set(["execute", "sessioncleanupexecute"]).has(
            String(operation?.action || "").toLowerCase(),
          );
        if (completedExecute && loadingRequestId && responseRequestId === loadingRequestId) {
          sessionCleanupState.previewTokenShown = "";
          closeSettingsConfirm();
        }
        const token = String(operation?.confirmationToken || "");
        if (state === "preview" && token && token !== sessionCleanupState.previewTokenShown) {
          sessionCleanupState.previewTokenShown = token;
          restoreSessionCleanupConfirm(token);
        }
        ensureSessionCleanupElapsedTicker();
        if (
          inventoryMutationTerminal
          && String(sessionCleanupState.search || "").trim()
          && !sessionCleanupState.searchRequestId
          && String(data?.search?.indexState || "") !== "indexing"
        ) {
          // Inventory mutations invalidate opaque match ids. Deletion/transfer
          // mutations re-run the query; scans refresh it from the resident
          // index worker without another renderer request.
          requestSessionCleanupSearch(sessionCleanupState.search);
        }
        if (settingsActiveTab === "storage") {
          scheduleStoragePanelRefresh({ throttleMs: 0 });
        }
        if (sessionTransferState.open) applySessionTransferPayload(payload);
      }

    function install() {
      return true;
    }

    function apply(root, payload) {
      return applySessionCleanupPayload(root, payload || {});
    }

    function dispose() {
      stopSessionCleanupElapsedTicker();
      stopSessionCleanupScanWatchdog();
      stopSessionCleanupSearchTimer();
      return true;
    }

    return {
      install,
      apply,
      dispose,
      storageFormatBytes,
      sessionCleanupFromPayload,
      cleanupIconSvg,
      sessionIndexDomainState,
      sessionIndexRangeLabel,
      sessionIndexProgressPercent,
      sessionIndexToggleHtml,
      sessionIndexScanMergeHtml,
      sessionIndexExtendOptions,
      sessionIndexBuildOptions,
      formatSessionIndexEta,
      storagePanelHtml,
      sessionCleanupRows,
      sessionCleanupPageRows,
      sessionCleanupPageCount,
      moveSessionCleanupPage,
      sessionCleanupDateValue,
      sessionCleanupDateTimeInputValue,
      sessionCleanupDateLabel,
      sessionCleanupDateRangeLabel,
      sessionCleanupDateRangeError,
      sessionCleanupDatePresetValues,
      sessionCleanupClientLabel,
      sessionCleanupAvailabilityLabel,
      sessionCleanupFilterOptionHtml,
      sessionCleanupFilterSummary,
      sessionCleanupStatusLabel,
      sessionCleanupReasonLabel,
      sessionCleanupPhaseLabel,
      formatSessionCleanupElapsed,
      sessionCleanupScanActive,
      activeSessionCleanupScanRequestId,
      stopSessionCleanupElapsedTicker,
      stopSessionCleanupSearchTimer,
      requestSessionCleanupSearch,
      requestSessionCleanupWorkdirOptions,
      requestSessionIndexControl,
      requestSessionIndexEnabled,
      requestSessionIndexClear,
      openSessionIndexClearConfirm,
      resetSessionCleanupPendingRequests,
      syncSessionCleanupElapsed,
      ensureSessionCleanupElapsedTicker,
      sessionCleanupPanelHtml,
      captureStorageUiState,
      restoreStorageUiState,
      captureStorageFocus,
      restoreStorageFocus,
      scheduleStoragePanelRefresh,
      refreshStoragePanelIfVisible,
      requestSessionCleanupCancel,
      requestSessionCleanupScan,
      requestSessionCleanupPreview,
      openSessionCleanupExecuteConfirm,
      restoreSessionCleanupConfirm,
      openSessionCleanupDeleteLoading,
      executeSessionCleanup,
      applySessionCleanupPayload,
    };
  }

  const sessionCleanupDomain = ctx.domains.register(
    "session_cleanup",
    createSessionCleanupDomain(ctx, shared),
  );
  const {
    storageFormatBytes,
    sessionCleanupFromPayload,
    cleanupIconSvg,
    sessionIndexDomainState,
    sessionIndexRangeLabel,
    sessionIndexProgressPercent,
    sessionIndexToggleHtml,
    sessionIndexScanMergeHtml,
    sessionIndexExtendOptions,
    sessionIndexBuildOptions,
    formatSessionIndexEta,
    storagePanelHtml,
    sessionCleanupRows,
    sessionCleanupPageRows,
    sessionCleanupPageCount,
    moveSessionCleanupPage,
    sessionCleanupDateValue,
    sessionCleanupDateTimeInputValue,
    sessionCleanupDateLabel,
    sessionCleanupDateRangeLabel,
    sessionCleanupDateRangeError,
    sessionCleanupDatePresetValues,
    sessionCleanupClientLabel,
    sessionCleanupAvailabilityLabel,
    sessionCleanupFilterOptionHtml,
    sessionCleanupFilterSummary,
    sessionCleanupStatusLabel,
    sessionCleanupReasonLabel,
    sessionCleanupPhaseLabel,
    formatSessionCleanupElapsed,
    sessionCleanupScanActive,
    activeSessionCleanupScanRequestId,
    stopSessionCleanupElapsedTicker,
    stopSessionCleanupSearchTimer,
    requestSessionCleanupSearch,
    requestSessionCleanupWorkdirOptions,
    requestSessionIndexControl,
    requestSessionIndexEnabled,
    requestSessionIndexClear,
    openSessionIndexClearConfirm,
    resetSessionCleanupPendingRequests,
    syncSessionCleanupElapsed,
    ensureSessionCleanupElapsedTicker,
    sessionCleanupPanelHtml,
    captureStorageUiState,
    restoreStorageUiState,
    captureStorageFocus,
    restoreStorageFocus,
    scheduleStoragePanelRefresh,
    refreshStoragePanelIfVisible,
    requestSessionCleanupCancel,
    requestSessionCleanupScan,
    requestSessionCleanupPreview,
    openSessionCleanupExecuteConfirm,
    restoreSessionCleanupConfirm,
    openSessionCleanupDeleteLoading,
    executeSessionCleanup,
    applySessionCleanupPayload,
  } = sessionCleanupDomain;
"""

__all__ = ["TEXT"]
