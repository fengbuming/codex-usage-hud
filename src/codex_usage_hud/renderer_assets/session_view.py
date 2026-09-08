"""Renderer session-view JavaScript domain."""

TEXT = r"""
  function createSessionViewDomain(ctx, shared) {
  // ABI marker retained for existing renderer contract probes:
  // data-field="topTaskOrdinalSession"
  function lineInner(node) {
    let inner = node.querySelector(":scope > .codex-usage-hud-line-inner");
    if (!inner) {
      const text = node.textContent || "";
      node.textContent = "";
      inner = document.createElement("span");
      inner.className = "codex-usage-hud-line-inner";
      inner.textContent = text;
      node.appendChild(inner);
    }
    return inner;
  }


  function refreshMarquee(node) {
    if (!node?.classList?.contains("codex-usage-hud-line") || !node.isConnected) return;
    const inner = node.querySelector(":scope > .codex-usage-hud-line-inner");
    if (!inner) return;
    const available = Math.max(1, node.clientWidth || node.getBoundingClientRect().width || 0);
    const overflow = Math.ceil((inner.scrollWidth || inner.getBoundingClientRect().width || 0) - available);
    if (overflow > 1) {
      node.dataset.marquee = "true";
      node.style.setProperty("--codex-usage-hud-marquee-distance", `${overflow}px`);
      node.style.setProperty("--codex-usage-hud-marquee-duration", `${Math.max(4000, 3000 + (overflow * 60))}ms`);
      return;
    }
    delete node.dataset.marquee;
    node.style.removeProperty("--codex-usage-hud-marquee-distance");
    node.style.removeProperty("--codex-usage-hud-marquee-duration");
  }

  function refreshAllMarquees(root = document.getElementById(rootId)) {
    if (!root) return;
    root.querySelectorAll(".codex-usage-hud-progress-rail").forEach(budgetDomain.refreshProgressRailLabel);
    root.querySelectorAll(".codex-usage-hud-line").forEach(refreshMarquee);
    root.querySelectorAll(".codex-usage-hud-progress-strip").forEach(budgetDomain.refreshCollapsedProgressStrip);
    // 强制立即刷新一次，避免 HUD 打开很久才生效
    ctx.lifecycle.timeout(
      "session_view_progress",
      () => root.querySelectorAll(".codex-usage-hud-progress-rail").forEach(
        budgetDomain.refreshProgressRailLabel,
      ),
      0,
    );
  }

  function applyLineText(node, value, { refresh = true } = {}) {
    const text = String(value || "");
    const inner = lineInner(node);
    inner.textContent = text;
    node.dataset.currentText = text;
    if (refresh) ctx.lifecycle.frame("session_view", () => refreshMarquee(node));
  }

  function cancelNumericAnimation(node) {
    const animation = numericAnimations.get(node);
    if (!animation) return;
    ctx.lifecycle.clearFrame(animation.raf);
    numericAnimations.delete(node);
  }

  function extractNumericParts(text) {
    const source = String(text || "");
    const parts = [];
    const tokens = [];
    let cursor = 0;
    numericTokenRe.lastIndex = 0;
    for (const match of source.matchAll(numericTokenRe)) {
      parts.push(source.slice(cursor, match.index));
      tokens.push(match[0]);
      cursor = Number(match.index || 0) + match[0].length;
    }
    parts.push(source.slice(cursor));
    return { parts, tokens };
  }

  function parseNumericToken(token) {
    const match = String(token || "").match(/^(\$?)(\d+(?:,\d{3})*(?:\.\d+)?)([kM%]?)$/);
    if (!match) return null;
    const [, prefix, amount, suffix] = match;
    const decimals = amount.includes(".") ? amount.split(".", 2)[1].length : 0;
    const usesGrouping = amount.includes(",");
    const value = Number(amount.replace(/,/g, ""));
    if (!Number.isFinite(value)) return null;
    return { prefix, value, suffix, decimals, usesGrouping };
  }

  function formatNumericToken(value, template) {
    const decimals = Math.max(0, template.decimals || 0);
    let body = "";
    if (decimals <= 0) {
      const rounded = Math.round(value);
      body = template.usesGrouping ? rounded.toLocaleString("en-US") : String(rounded);
    } else if (template.usesGrouping) {
      body = value.toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });
    } else {
      body = value.toFixed(decimals);
    }
    return `${template.prefix}${body}${template.suffix}`;
  }

  function canAnimateNumericText(startText, endText) {
    const start = extractNumericParts(startText);
    const end = extractNumericParts(endText);
    if (!start.tokens.length || start.tokens.length !== end.tokens.length) return false;
    if (start.parts.length !== end.parts.length || start.parts.some((part, index) => part !== end.parts[index])) return false;
    return start.tokens.every((token, index) => {
      const startToken = parseNumericToken(token);
      const endToken = parseNumericToken(end.tokens[index]);
      return !!startToken && !!endToken && startToken.prefix === endToken.prefix && startToken.suffix === endToken.suffix;
    });
  }

  function interpolateNumericText(startText, endText, progress) {
    const start = extractNumericParts(startText);
    const end = extractNumericParts(endText);
    const clamped = clamp(progress, 0, 1);
    const pieces = [];
    for (let index = 0; index < end.parts.length - 1; index += 1) {
      pieces.push(end.parts[index]);
      const startToken = parseNumericToken(start.tokens[index]);
      const endToken = parseNumericToken(end.tokens[index]);
      if (!startToken || !endToken) {
        pieces.push(end.tokens[index]);
        continue;
      }
      const value = startToken.value + ((endToken.value - startToken.value) * clamped);
      pieces.push(formatNumericToken(value, endToken));
    }
    pieces.push(end.parts[end.parts.length - 1]);
    return pieces.join("");
  }

  function setAnimatedLineText(node, value) {
    const next = String(value || "");
    const current = node.dataset.currentText ?? node.textContent ?? "";
    cancelNumericAnimation(node);
    if (!current || current === next || !canAnimateNumericText(current, next)) {
      applyLineText(node, next);
      return;
    }
    const startedAt = performance.now();
    const step = (now) => {
      const progress = clamp((now - startedAt) / 360, 0, 1);
      applyLineText(node, interpolateNumericText(current, next, progress), { refresh: progress >= 1 });
      if (progress >= 1) {
        applyLineText(node, next);
        numericAnimations.delete(node);
        return;
      }
      numericAnimations.set(node, { raf: ctx.lifecycle.frame("session_view_numeric", step) });
    };
    numericAnimations.set(node, { raf: ctx.lifecycle.frame("session_view_numeric", step) });
  }

  function setText(root, field, value) {
    root.querySelectorAll(`[data-field="${field}"]`).forEach((node) => {
      const text = String(value || "");
      if (node.classList.contains("codex-usage-hud-line")) {
        node.title = text;
        if (field === "requestLine" || field === "requestLineExpanded") {
          setAnimatedLineText(node, text);
        } else {
          cancelNumericAnimation(node);
          applyLineText(node, text);
        }
        return;
      }
      node.textContent = text;
      if (text) node.title = text;
      else node.removeAttribute("title");
    });
  }

  function setFieldTitle(root, field, value) {
    const title = String(value || "");
    root.querySelectorAll(`[data-field="${field}"]`).forEach((node) => {
      if (title) node.title = title;
      else if (!node.dataset.copyable) node.removeAttribute("title");
    });
  }

  function fallbackCopyHudText(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.left = "-1000px";
    area.style.top = "0";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_) {
      ok = false;
    }
    area.remove();
    return ok;
  }

  async function copyHudText(text) {
    const value = String(text || "");
    if (!value) return false;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {}
    }
    return fallbackCopyHudText(value);
  }

  function flashCopyState(node, ok) {
    const previousTitle = node.dataset.copyTitle || node.title || "";
    node.dataset.copied = ok ? "true" : "false";
    node.title = ok ? "已复制" : "复制失败";
    ctx.lifecycle.timeout("copy_feedback", () => {
      if (!node.isConnected) return;
      delete node.dataset.copied;
      node.title = previousTitle;
    }, 900);
  }

  function configureCopy(root, field, text, title, copyField) {
    root.querySelectorAll(`[data-field="${field}"]`).forEach((node) => {
      const value = String(text || "");
      if (value) {
        node.dataset.copyable = "true";
        node.dataset.copyText = value;
        node.dataset.copyTitle = title;
        node.dataset.copyField = copyField;
        node.title = title;
        return;
      }
      delete node.dataset.copyable;
      delete node.dataset.copyText;
      delete node.dataset.copyTitle;
      delete node.dataset.copyField;
      delete node.dataset.copied;
      node.removeAttribute("title");
    });
  }

  function elapsedSecondsText(startedAtMs, nowMs = Date.now()) {
    const seconds = Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
    return `${seconds}s`.padStart(8, " ");
  }

  function refreshRunningRows() {
    const root = document.getElementById(rootId);
    if (!root) {
      ctx.lifecycle.clearInterval(window[runningTimerName] || 0);
      window[runningTimerName] = 0;
      return;
    }
    const nodes = Array.from(root.querySelectorAll(".codex-usage-hud-row-time[data-running='true']"));
    if (!nodes.length) {
      ctx.lifecycle.clearInterval(window[runningTimerName] || 0);
      window[runningTimerName] = 0;
      return;
    }
    const now = Date.now();
    for (const node of nodes) {
      const startedAtMs = Date.parse(node.dataset.startedAt || "");
      if (!Number.isFinite(startedAtMs)) continue;
      node.textContent = elapsedSecondsText(startedAtMs, now);
      node.dataset.tick = String(Math.floor(now / 1000) % 2);
    }
  }

  function syncRunningRowsTimer(root) {
    refreshRunningRows();
    const hasRunningRow = !!root.querySelector(".codex-usage-hud-row-time[data-running='true']");
    if (hasRunningRow && !window[runningTimerName]) {
      window[runningTimerName] = ctx.lifecycle.interval("session_view", refreshRunningRows, 1000);
    }
    if (!hasRunningRow && window[runningTimerName]) {
      ctx.lifecycle.clearInterval(window[runningTimerName]);
      window[runningTimerName] = 0;
    }
  }

  function appendStructuredRequestRow(list, item, index) {
    const row = document.createElement("div");
    row.className = "codex-usage-hud-row";
    row.dataset.latest = String(index === 0);
    const prefix = String(item?.prefix ?? "");
    const time = String(item?.time ?? "");
    const suffix = String(item?.suffix ?? "");
    if (!prefix && !suffix) {
      row.textContent = String(item?.text || "");
      list.appendChild(row);
      return;
    }
    const prefixNode = document.createElement("span");
    prefixNode.textContent = prefix;
    const timeNode = document.createElement("span");
    timeNode.className = "codex-usage-hud-row-time";
    timeNode.textContent = time;
    const running = index === 0 && item?.running && item?.startedAt;
    if (running) {
      timeNode.dataset.running = "true";
      timeNode.dataset.startedAt = String(item.startedAt);
    }
    const suffixNode = document.createElement("span");
    suffixNode.textContent = suffix;
    row.append(prefixNode, timeNode, suffixNode);
    list.appendChild(row);
  }

  function requestMoreRequestRows(list) {
    const total = Math.max(0, Number(list.dataset.requestRowsTotal || 0));
    const rendered = Math.max(0, Number(list.dataset.requestRowsRendered || 0));
    if (
      !total
      || rendered >= total
      || list.dataset.requestRowsLoading === "true"
      || !ctx.bindings.available(settingsCommandBindingName)
    ) return;
    list.dataset.requestRowsLoading = "true";
    try {
      ctx.bindings.send(settingsCommandBindingName, {
        id: `request-rows-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        createdAt: Date.now(),
        action: "loadMoreRequestRows",
        sessionId: String(list.dataset.requestRowsSessionId || ""),
      });
    } catch (_) {
      list.dataset.requestRowsLoading = "false";
    }
  }

  function bindRequestRowsPagination(list) {
    if (list.dataset.requestRowsPaginationBound === "true") return;
    list.dataset.requestRowsPaginationBound = "true";
    list.addEventListener("scroll", () => {
      if (list.scrollTop + list.clientHeight >= list.scrollHeight - 2) {
        requestMoreRequestRows(list);
      }
    }, { passive: true });
  }

  function renderRequestRows(root, rows, rowDetails, newSession = false, totalRows = 0, sessionId = "") {
    const list = root.querySelector('[data-field="requestRows"]');
    if (!list) return;
    const currentSessionId = String(sessionId || "");
    const previousScrollTop = list.dataset.requestRowsSessionId === currentSessionId
      ? list.scrollTop
      : 0;
    list.textContent = "";
    list.dataset.requestRowsSessionId = currentSessionId;
    list.dataset.requestRowsTotal = String(Math.max(0, Number(totalRows) || 0));
    list.dataset.requestRowsLoading = "false";
    const details = Array.isArray(rowDetails) && rowDetails.length ? rowDetails : [];
    if (details.length) {
      details.forEach((item, index) => appendStructuredRequestRow(list, item, index));
      list.dataset.requestRowsRendered = String(details.length);
      list.scrollTop = previousScrollTop;
      bindRequestRowsPagination(list);
      syncRunningRowsTimer(root);
      return;
    }
    if (newSession && (!Array.isArray(rows) || !rows.length)) {
      list.dataset.requestRowsRendered = "0";
      list.scrollTop = previousScrollTop;
      bindRequestRowsPagination(list);
      syncRunningRowsTimer(root);
      return;
    }
    const items = Array.isArray(rows) && rows.length ? rows : ["本次请求(等待) $0.0000 ↑- ◎- ↓- ◇- ↻- ∑-"];
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-row";
      row.textContent = String(item || "");
      list.appendChild(row);
    }
    list.dataset.requestRowsRendered = String(items.length);
    list.scrollTop = previousScrollTop;
    bindRequestRowsPagination(list);
    syncRunningRowsTimer(root);
  }

  function renderActiveSessionCandidates(root, payload) {
    const candidates = Array.isArray(payload?.matchCandidates)
      ? payload.matchCandidates.filter((item) => item && typeof item === "object")
      : [];
    const visible = payload?.followReason === "ambiguous-persisted-identity" && candidates.length > 1;
    root.querySelectorAll('[data-field="activeSessionCandidates"]').forEach((container) => {
      container.hidden = !visible;
      container.replaceChildren();
      if (!visible) return;

      const heading = document.createElement("div");
      heading.className = "codex-usage-hud-active-session-candidates-title";
      heading.textContent = "检测到多个未归档同名会话，请选择：";
      container.appendChild(heading);

      const detail = document.createElement("div");
      detail.className = "codex-usage-hud-active-session-candidates-detail";
      detail.textContent = "这些候选都来自当前会话列表；选择后将按精确会话 ID 绑定。";
      container.appendChild(detail);

      const list = document.createElement("div");
      list.className = "codex-usage-hud-active-session-candidates-list";
      candidates.forEach((candidate, index) => {
        const sessionId = String(candidate.sessionId || "").trim();
        if (!sessionId) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "codex-usage-hud-active-session-candidate";
        button.dataset.action = "active-session-candidate";
        button.dataset.sessionId = sessionId;
        button.dataset.selectionSeq = String(Number(payload?.selectionSeq || 0));
        const shortId = sessionId.length > 16
          ? `${sessionId.slice(0, 8)}…${sessionId.slice(-6)}`
          : sessionId;
        const name = String(candidate.rolloutName || "").trim();
        const updatedAtMs = Number(candidate.updatedAtMs || 0);
        const updated = updatedAtMs > 0
          ? new Date(updatedAtMs).toLocaleString()
          : "时间未知";
        button.title = `${shortId}${name ? `\n${name}` : ""}`;

        const label = document.createElement("span");
        label.className = "codex-usage-hud-active-session-candidate-label";
        label.textContent = `候选 ${index + 1} · ${shortId}`;
        const meta = document.createElement("span");
        meta.className = "codex-usage-hud-active-session-candidate-meta";
        meta.textContent = `${name || "本地会话文件未知"} · 更新于 ${updated}`;
        button.append(label, meta);
        list.append(button);
      });
      container.appendChild(list);
    });
  }

  function renderHeavyRounds(root, details) {
    const list = root.querySelector('[data-field="topHeavyRounds"]');
    if (!list) return;
    list.replaceChildren();
    const items = Array.isArray(details?.heavyRounds) ? details.heavyRounds.slice(0, 3) : [];
    if (!items.length) {
      list.dataset.empty = "true";
      const placeholders = [
        ["暂无会话高消耗轮次", "会话出现 token 确认后展示 Top 3"],
        ["等待统计", "不会因新需求开始而清空"],
        ["保持占位", "新轮次超过历史 Top 3 后刷新"],
      ];
      for (const [titleText, detailText] of placeholders) {
        const empty = document.createElement("div");
        empty.className = "codex-usage-hud-heavy-round";
        empty.dataset.placeholder = "true";
        empty.innerHTML = `
          <span class="codex-usage-hud-heavy-round-title">${titleText}</span>
          <span class="codex-usage-hud-heavy-round-detail">${detailText}</span>
        `;
        list.appendChild(empty);
      }
      return;
    }
    delete list.dataset.empty;
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-heavy-round";
      const rolledBack = item.rolledBack === true;
      const tooltip = String(item.tooltip || [item.title, item.detail].filter(Boolean).join("  "));
      if (tooltip) row.title = tooltip;
      const copyText = String(item.copyText || "");
      if (copyText) {
        row.dataset.copyable = "true";
        row.dataset.copyText = copyText;
        row.dataset.copyTitle = rolledBack
          ? `点击复制内容（该轮已回滚，不在聊天中）\n${tooltip}`
          : (tooltip
            ? `点击定位轮次并复制内容\n${tooltip}`
            : "点击定位轮次并复制内容");
        row.dataset.copyField = "heavy";
        row.title = row.dataset.copyTitle;
      }
      if (item.taskIndex) row.dataset.activityTaskIndex = String(item.taskIndex);
      if (item.roundIndex) row.dataset.activityRoundIndex = String(item.roundIndex);
      if (item.taskPrompt) row.dataset.activityTaskPrompt = String(item.taskPrompt);
      if (item.taskTurnId) row.dataset.activityTaskTurnId = String(item.taskTurnId);
      const locateTexts = Array.isArray(item.locateTexts)
        ? item.locateTexts.filter((text) => typeof text === "string" && text.trim())
        : [];
      if (locateTexts.length) row.dataset.locateTexts = JSON.stringify(locateTexts);
      if (rolledBack) row.dataset.rolledBack = "true";
      const title = document.createElement("span");
      title.className = "codex-usage-hud-heavy-round-title";
      title.textContent = String(item.title || "");
      const detail = document.createElement("span");
      detail.className = "codex-usage-hud-heavy-round-detail";
      detail.textContent = String(item.detail || "");
      if (tooltip) detail.title = tooltip;
      row.append(title, detail);
      list.appendChild(row);
    }
  }

  function activityTrailRoundIndexes(item) {
    const indexes = [];
    const primary = Number(item?.roundIndex || 0);
    if (primary > 0) indexes.push(Math.round(primary));
    if (Array.isArray(item?.roundIndexes)) {
      for (const value of item.roundIndexes) {
        const normalized = Number(value || 0);
        if (normalized > 0) indexes.push(Math.round(normalized));
      }
    }
    const titleMatch = String(item?.title || "").match(/轮次\s*#(\d+)/);
    if (titleMatch) indexes.push(Number(titleMatch[1]));
    return Array.from(new Set(indexes.filter((value) => value > 0)));
  }

  function activityTrailMatchesRound(item, roundIndex) {
    const expected = Math.round(Number(roundIndex || 0));
    return expected > 0 && activityTrailRoundIndexes(item).includes(expected);
  }

  function renderActivityTimeline(root, details) {
    const list = root.querySelector('[data-field="topActivityTrail"]');
    if (!list) return;
    const previousScrollTop = list.scrollTop || 0;
    const button = root.querySelector('[data-field="topActivityLoadMore"]');
    const items = Array.isArray(details?.activityTrail) ? details.activityTrail : [];
    const allItems = items.filter((item) => item && (item.title || item.detail || item.time));
    const signature = allItems.map((item) => [item.time, item.title, item.detail, item.tooltip].join("|")).join(";");
    const context = [
      details?.taskOrdinal || "",
      details?.currentTask || "",
      details?.title || "",
    ].join("|");
    const contextChanged = list.dataset.context !== context;
    if (contextChanged) {
      list.dataset.context = context;
      list.dataset.visibleCount = "4";
      list.scrollTop = 0;
    } else if (!list.dataset.visibleCount) {
      list.dataset.visibleCount = "4";
    }
    list.dataset.signature = signature;
    const visibleCount = Math.max(4, Number(list.dataset.visibleCount || 4));
    const visibleItems = allItems.slice(0, visibleCount);
    list.dataset.fill = visibleItems.length > 0 && visibleItems.length <= 4 ? "spread" : "dense";
    list.replaceChildren();
    if (button) {
      button.hidden = false;
      button.disabled = visibleItems.length >= allItems.length;
      button.textContent = "查看更多";
      button.title = visibleItems.length >= allItems.length ? "已显示全部活动轨迹" : "加载更早的活动轨迹";
    }
    if (!visibleItems.length) {
      const empty = document.createElement("div");
      empty.className = "codex-usage-hud-activity-node";
      empty.title = "等待会话产生新活动";
      empty.innerHTML = `
        <span class="codex-usage-hud-activity-node-time">--:--</span>
        <span class="codex-usage-hud-activity-node-dot"></span>
        <span>
          <span class="codex-usage-hud-activity-node-title">暂无时间节点</span>
          <span class="codex-usage-hud-activity-node-detail">等待会话产生新活动</span>
        </span>
      `;
      list.appendChild(empty);
      return;
    }
    for (const item of visibleItems) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-activity-node";
      row.dataset.active = String(!!item.active);
      const taskIndex = Math.round(Number(item?.taskIndex || details?.index || 0));
      const roundIndexes = activityTrailRoundIndexes(item);
      if (taskIndex > 0) row.dataset.activityTaskIndex = String(taskIndex);
      if (roundIndexes.length) {
        row.dataset.activityRoundIndex = String(roundIndexes[roundIndexes.length - 1]);
        row.dataset.activityRoundIndexes = roundIndexes.join(",");
      }
      const locateTaskIndex = Math.round(Number(root.dataset.activityLocateTaskIndex || 0));
      const locateRoundIndex = Math.round(Number(root.dataset.activityLocateRoundIndex || 0));
      if (
        locateRoundIndex > 0
        && (!locateTaskIndex || !taskIndex || locateTaskIndex === taskIndex)
        && roundIndexes.includes(locateRoundIndex)
      ) {
        row.dataset.locateHighlight = "true";
      }
      const tooltip = String(item.tooltip || [item.time, item.title, item.detail].filter(Boolean).join("  "));
      if (tooltip) row.title = tooltip;
      const time = document.createElement("span");
      time.className = "codex-usage-hud-activity-node-time";
      time.textContent = String(item.time || "--:--");
      const dot = document.createElement("span");
      dot.className = "codex-usage-hud-activity-node-dot";
      const body = document.createElement("span");
      const title = document.createElement("span");
      title.className = "codex-usage-hud-activity-node-title";
      title.textContent = String(item.title || "活动");
      title.title = tooltip || title.textContent;
      const detail = document.createElement("span");
      detail.className = "codex-usage-hud-activity-node-detail";
      detail.textContent = String(item.detail || "");
      detail.title = tooltip || detail.textContent;
      if (detail.textContent || tooltip) {
        detail.dataset.copyable = "true";
        detail.dataset.copyText = tooltip || detail.textContent;
        detail.dataset.copyTitle = "点击复制轨迹详情";
        detail.dataset.copyField = "trail";
        detail.title = tooltip ? `点击复制轨迹详情\n${tooltip}` : "点击复制轨迹详情";
      }
      body.append(title, detail);
      row.append(time, dot, body);
      list.appendChild(row);
    }
    if (!contextChanged) {
      list.scrollTop = previousScrollTop;
    }
  }

  function activityTaskItems(details) {
    return Array.isArray(details?.activityTasks)
      ? details.activityTasks.filter((item) => item && typeof item === "object")
      : [];
  }

  function activityTaskSelection(root, payload, details, tasks) {
    if (!tasks.length) {
      delete root.dataset.activityTaskSessionKey;
      delete root.dataset.activityTaskIndex;
      delete root.dataset.activityTaskCount;
      delete root.dataset.activityTaskPinned;
      return null;
    }
    const sessionKey = String(
      payload?.rendererSessionId || payload?.sessionId || details?.title || "",
    );
    const count = tasks.length;
    const payloadIndex = Number(details?.activityTaskIndex || 0);
    const previousSessionKey = String(root.dataset.activityTaskSessionKey || "");
    const previousCount = Number(root.dataset.activityTaskCount || 0);
    const sameSession = previousSessionKey === sessionKey;
    const pinned = sameSession && root.dataset.activityTaskPinned === "true";
    let selected = Number(root.dataset.activityTaskIndex || 0);
    if (
      !Number.isFinite(selected)
      || selected < 1
      || !sameSession
      || (!pinned && previousCount !== count)
    ) {
      selected = payloadIndex > 0 ? payloadIndex : count;
    }
    selected = clamp(Math.round(selected), 1, count);
    if (!tasks.some((item) => Number(item.index || 0) === selected)) {
      selected = payloadIndex > 0 ? payloadIndex : Number(tasks[count - 1]?.index || count);
      delete root.dataset.activityTaskPinned;
    }
    root.dataset.activityTaskSessionKey = sessionKey;
    root.dataset.activityTaskCount = String(count);
    root.dataset.activityTaskIndex = String(selected);
    return tasks.find((item) => Number(item.index || 0) === selected) || tasks[count - 1];
  }

  function renderActivityTaskNav(root, details, tasks, selected) {
    const nav = root.querySelector('[data-field="topActivityTaskNav"]');
    if (!nav) return;
    const navigable = details?.activityTaskNavigable === true && tasks.length > 1 && !!selected;
    nav.hidden = !navigable;
    if (!navigable) return;
    const index = Math.max(1, Number(selected.index || 1));
    const count = Math.max(1, Number(selected.count || tasks.length));
    const previous = nav.querySelector('[data-action="activity-task-prev"]');
    const next = nav.querySelector('[data-action="activity-task-next"]');
    if (previous) previous.disabled = index <= 1;
    if (next) next.disabled = index >= count;
    setText(root, "topActivityTaskOrdinal", `Req ${index}/${count}`);
  }

  function selectActivityTask(root, delta) {
    const payload = currentPayload() || {};
    const details = payload?.topDetails || {};
    const tasks = activityTaskItems(details);
    if (details?.activityTaskNavigable !== true || tasks.length < 2) return false;
    const current = Number(root.dataset.activityTaskIndex || details.activityTaskIndex || tasks.length);
    const next = clamp(Math.round(current) + Number(delta || 0), 1, tasks.length);
    root.dataset.activityTaskIndex = String(next);
    root.dataset.activityTaskPinned = "true";
    renderTopDetails(root, payload);
    return true;
  }

  function selectActivityTaskIndex(root, index) {
    const payload = currentPayload() || {};
    const details = payload?.topDetails || {};
    const tasks = activityTaskItems(details);
    if (!tasks.length) return false;
    const next = clamp(Math.round(Number(index || 0)), 1, tasks.length);
    root.dataset.activityTaskIndex = String(next);
    root.dataset.activityTaskPinned = "true";
    renderTopDetails(root, payload);
    return true;
  }

  function locateActivityTrailRound(root, taskIndex, roundIndex) {
    const normalizedTaskIndex = Math.round(Number(taskIndex || 0));
    const normalizedRoundIndex = Math.round(Number(roundIndex || 0));
    if (normalizedRoundIndex <= 0) return false;
    const payload = currentPayload() || {};
    const rawDetails = payload?.topDetails || {};
    const tasks = activityTaskItems(rawDetails);
    if (normalizedTaskIndex > 0 && tasks.length) {
      selectActivityTaskIndex(root, normalizedTaskIndex);
    }
    const selected = tasks.find(
      (item) => Number(item.index || 0) === Number(root.dataset.activityTaskIndex || normalizedTaskIndex),
    );
    const details = selected ? { ...rawDetails, ...selected } : rawDetails;
    const items = Array.isArray(details?.activityTrail)
      ? details.activityTrail.filter((item) => item && (item.title || item.detail || item.time))
      : [];
    const itemIndex = items.findIndex((item) => activityTrailMatchesRound(item, normalizedRoundIndex));
    if (itemIndex < 0) return false;

    const list = root.querySelector('[data-field="topActivityTrail"]');
    if (!list) return false;
    const button = root.querySelector('[data-field="topActivityLoadMore"]');
    const pageSize = Math.max(1, Number(button?.dataset.pageSize || 12));
    const currentVisible = Math.max(4, Number(list.dataset.visibleCount || 4));
    const requiredVisible = itemIndex + 1;
    if (requiredVisible > currentVisible) {
      const pages = Math.ceil((requiredVisible - currentVisible) / pageSize);
      list.dataset.visibleCount = String(currentVisible + (pages * pageSize));
    }

    const locateToken = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    root.dataset.activityLocateTaskIndex = String(normalizedTaskIndex || Number(selected?.index || 0));
    root.dataset.activityLocateRoundIndex = String(normalizedRoundIndex);
    root.dataset.activityLocateToken = locateToken;
    renderTopDetails(root, payload);

    const row = Array.from(
      root.querySelectorAll(".codex-usage-hud-activity-node[data-activity-round-index]"),
    ).find((node) => String(node.dataset.activityRoundIndexes || node.dataset.activityRoundIndex || "")
      .split(",")
      .map(Number)
      .includes(normalizedRoundIndex));
    if (!row) return false;
    const listRect = list.getBoundingClientRect();
    const rowRect = row.getBoundingClientRect();
    const targetScrollTop = Math.max(
      0,
      Number(list.scrollTop || 0)
        + (rowRect.top - listRect.top)
        - Math.max(0, (list.clientHeight - rowRect.height) / 2),
    );
    if (typeof list.scrollTo === "function") {
      list.scrollTo({ top: targetScrollTop, behavior: "smooth" });
    } else {
      list.scrollTop = targetScrollTop;
    }
    ctx.lifecycle.timeout("activity_trail_locate", () => {
      if (!root.isConnected || root.dataset.activityLocateToken !== locateToken) return;
      delete root.dataset.activityLocateTaskIndex;
      delete root.dataset.activityLocateRoundIndex;
      delete root.dataset.activityLocateToken;
      root.querySelectorAll('[data-locate-highlight="true"]').forEach((node) => {
        delete node.dataset.locateHighlight;
      });
    }, 1800);
    return true;
  }

  function normalizedActivityText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function activityConversationTurn(node) {
    return node?.closest?.("[data-content-search-turn-key], [data-turn-key]") || null;
  }

  function activityConversationUnits(scope = document) {
    const hudRoot = document.getElementById(rootId);
    return Array.from(scope.querySelectorAll?.("[data-content-search-unit-key]") || [])
      .filter((node) => !hudRoot?.contains(node));
  }

  function selectedActivityTaskContext(taskPrompt, taskTurnId = "") {
    const payload = currentPayload() || {};
    const details = payload?.topDetails || {};
    const tasks = activityTaskItems(details);
    const root = document.getElementById(rootId);
    const selectedIndex = Math.round(Number(
      root?.dataset.activityTaskIndex || details?.activityTaskIndex || tasks.length || 0,
    ));
    const selected = tasks.find((item) => Number(item?.index || 0) === selectedIndex) || null;
    const prompt = String(taskPrompt || selected?.currentTask || "").trim();
    const normalizedPrompt = normalizedActivityText(prompt);
    const duplicatePromptCount = tasks.filter(
      (item) => normalizedActivityText(item?.currentTask || item?.prompt || "") === normalizedPrompt,
    ).length;
    const turnId = String(taskTurnId || selected?.turnId || "").trim();
    return {
      prompt,
      turnId,
      taskIndex: Math.max(0, Number(selected?.index || selectedIndex || 0)),
      taskCount: Math.max(0, Number(selected?.count || tasks.length || 0)),
      // Without a stable turn key, permit text fallback only when the selected
      // task prompt is unique in the payload.  Duplicate prompts are
      // intentionally treated as unresolved rather than risking another Req.
      allowUnkeyedFallback: !!turnId || duplicatePromptCount <= 1,
    };
  }

  function activityRequestNeedles(taskPrompt) {
    const prompt = normalizedActivityText(taskPrompt);
    if (!prompt) return [];
    const lines = String(taskPrompt || "")
      .split(/\r?\n/)
      .map(normalizedActivityText)
      .filter((line) => line.length >= 12)
      .sort((left, right) => right.length - left.length);
    return Array.from(new Set([prompt, ...lines]))
      .map((value) => value.slice(0, 600))
      .filter(Boolean);
  }

  function activityUnitBelongsToTurn(unit, turnId) {
    const expected = String(turnId || "").trim();
    if (!expected) return true;
    const key = String(unit?.getAttribute?.("data-content-search-unit-key") || "");
    return !key || key === expected || key.startsWith(`${expected}:`);
  }

  function bestActivityRequestUnit(units, needles, allowUserFallback = false, turnId = "") {
    const isUserUnit = (unit) => (
      unit.matches?.('[data-local-conversation-user-anchor="true"]')
      || !!unit.querySelector?.('[data-local-conversation-user-anchor="true"]')
      || /^(?:你说[：:]|You said:)/i.test(normalizedActivityText(unit.innerText || unit.textContent))
    );
    const scopedUnits = units.filter((unit) => activityUnitBelongsToTurn(unit, turnId));
    for (const needle of needles) {
      const matches = scopedUnits
        .map((unit) => ({
          unit,
          user: isUserUnit(unit),
          text: normalizedActivityText(unit.innerText || unit.textContent),
        }))
        .filter(({ text }) => text.includes(needle))
        .sort((left, right) => (
          Number(right.user) - Number(left.user)
          || (left.text.length - needle.length) - (right.text.length - needle.length)
        ));
      if (matches.length) return matches[0].unit;
    }
    // A turn id is an authoritative scope.  If its content is mounted but the
    // prompt text is unavailable (for example while markdown is still loading),
    // only fall back to a user unit from that same turn.  Never pick an
    // arbitrary unit from the current viewport: doing so can jump to another
    // Req when the requested turn is still virtualized.
    return allowUserFallback ? (scopedUnits.find(isUserUnit) || null) : null;
  }

  function findActivityTurnAnchor(taskTurnId) {
    const turnId = String(taskTurnId || "").trim();
    if (!turnId) return null;
    const candidates = [
      ...Array.from(document.querySelectorAll("[data-content-search-turn-key]")),
      ...Array.from(document.querySelectorAll("[data-turn-key]")),
    ];
    const exact = candidates.find(
      (node) => (
        node.getAttribute("data-content-search-turn-key") === turnId
        || node.getAttribute("data-turn-key") === turnId
      ),
    );
    if (!exact) return null;
    return exact.closest?.("[data-turn-key]") || exact;
  }

  function findActivityRequestTarget(
    taskPrompt,
    taskTurnId = "",
    allowUnkeyedFallback = true,
  ) {
    const needles = activityRequestNeedles(taskPrompt);
    const turnId = String(taskTurnId || "").trim();
    if (!needles.length && !turnId) return null;
    // A legacy JSONL session can lack the stable task_started.turn_id.  When
    // the payload contains more than one task with the same prompt, a global
    // text search is unsafe: the target may be virtualized away while another
    // Req with identical text is mounted.  Refuse that ambiguous fallback
    // instead of jumping to the wrong conversation turn.
    if (!turnId && allowUnkeyedFallback === false) return null;
    if (turnId) {
      const exactTurn = Array.from(
        document.querySelectorAll("[data-content-search-turn-key]"),
      ).find((node) => node.getAttribute("data-content-search-turn-key") === turnId);
      if (exactTurn) {
        const units = activityConversationUnits(exactTurn);
        if (!units.length) return null;
        const unit = bestActivityRequestUnit(units, needles, true, turnId);
        return unit ? { unit, turn: exactTurn } : null;
      }
      const keyedUnits = activityConversationUnits().filter((unit) => {
        const unitKey = String(unit.getAttribute("data-content-search-unit-key") || "");
        return unitKey === turnId || unitKey.startsWith(`${turnId}:`);
      });
      if (keyedUnits.length) {
        const unit = bestActivityRequestUnit(keyedUnits, needles, true, turnId);
        return unit ? { unit, turn: activityConversationTurn(unit) } : null;
      }
      return null;
    }
    const units = activityConversationUnits();
    const unit = bestActivityRequestUnit(units, needles);
    if (unit) return { unit, turn: activityConversationTurn(unit) };
    const turns = Array.from(
      document.querySelectorAll("[data-content-search-turn-key], [data-turn-key]"),
    );
    for (const needle of needles) {
      const turn = turns.find((node) => normalizedActivityText(node.innerText || node.textContent).includes(needle));
      if (turn) return { unit: null, turn };
    }
    return null;
  }

  function activityScrollIsCurrent(operation) {
    return !!operation
      && operation.active !== false
      && activeActivityScroll === operation
      && ctx.lifecycle.active();
  }

  function cancelActivityScroll(operation = activeActivityScroll) {
    if (!operation) return false;
    operation.active = false;
    const cancelWait = operation.cancelWait;
    operation.cancelWait = null;
    if (typeof cancelWait === "function") cancelWait();
    if (activeActivityScroll === operation) activeActivityScroll = null;
    return true;
  }

  function beginActivityScroll() {
    cancelActivityScroll();
    const operation = {
      id: ++activityScrollSerial,
      active: true,
      cancelWait: null,
    };
    activeActivityScroll = operation;
    return operation;
  }

  function finishActivityScroll(operation) {
    if (!operation) return;
    operation.active = false;
    const cancelWait = operation.cancelWait;
    operation.cancelWait = null;
    if (typeof cancelWait === "function") cancelWait();
    if (activeActivityScroll === operation) activeActivityScroll = null;
  }

  async function waitForActivityVirtualization(frames = 2, operation = null) {
    for (let index = 0; index < Math.max(1, Number(frames || 0)); index += 1) {
      if (operation && !activityScrollIsCurrent(operation)) return false;
      const completed = await new Promise((resolve) => {
        let settled = false;
        let frame = 0;
        let fallbackTimer = 0;
        const finish = (result = true) => {
          if (settled) return;
          settled = true;
          if (operation?.cancelWait === cancel) operation.cancelWait = null;
          if (frame) ctx.lifecycle.clearFrame(frame);
          if (fallbackTimer) ctx.lifecycle.clearTimeout(fallbackTimer);
          resolve(result && (!operation || activityScrollIsCurrent(operation)));
        };
        const cancel = () => finish(false);
        if (operation) operation.cancelWait = cancel;
        // Electron can throttle requestAnimationFrame while the Desktop window
        // is occluded.  Keep the official frame wait, with a short tracked
        // fallback so a Top3 click cannot stall indefinitely in that state.
        fallbackTimer = ctx.lifecycle.timeout(
          "activity_virtualization_frame",
          finish,
          80,
        );
        if (typeof window.requestAnimationFrame === "function") {
          frame = ctx.lifecycle.frame(
            "activity_virtualization_frame",
            () => finish(true),
          );
        } else {
          finish();
        }
      });
      if (!completed) return false;
    }
    return true;
  }

  function activityTimelineOffset(node) {
    return Math.max(0, -Number(node?.scrollTop || 0));
  }

  function setActivityTimelineOffset(node, value) {
    const offset = Math.max(0, Number(value || 0));
    node.scrollTop = offset > 0 ? -offset : 0;
  }

  function waitForMaterializedActivityRequest(
    context,
    timeoutMs = 450,
    timeline = null,
    operation = null,
  ) {
    const existing = findActivityRequestTarget(
      context.prompt,
      context.turnId,
      context.allowUnkeyedFallback,
    );
    if (existing) return Promise.resolve(existing);
    if (operation && !activityScrollIsCurrent(operation)) return Promise.resolve(null);
    const timeout = Math.max(80, Math.round(Number(timeoutMs || 0)));
    const observeTarget = timeline || document.body || document.documentElement;
    return new Promise((resolve) => {
      let observer = null;
      let timer = 0;
      let settled = false;
      const finish = (target) => {
        if (settled) return;
        settled = true;
        if (observer) observer.disconnect();
        ctx.observers?.clear?.("activity_request_materialization");
        if (timer) ctx.lifecycle.clearTimeout(timer);
        for (const release of releases) release?.();
        if (operation?.cancelWait === cancel) operation.cancelWait = null;
        resolve(target && (!operation || activityScrollIsCurrent(operation)) ? target : null);
      };
      const check = () => {
        if (operation && !activityScrollIsCurrent(operation)) {
          finish(null);
          return;
        }
        const target = findActivityRequestTarget(
          context.prompt,
          context.turnId,
          context.allowUnkeyedFallback,
        );
        if (target) finish(target);
      };
      const cancel = () => finish(null);
      const releases = [];
      if (operation) operation.cancelWait = cancel;
      if (typeof MutationObserver === "function" && observeTarget) {
        observer = new MutationObserver(check);
        try {
          observer.observe(observeTarget, {
            subtree: true,
            childList: true,
            attributes: true,
            attributeFilter: [
              "data-content-search-turn-key",
              "data-content-search-unit-key",
              "data-local-conversation-user-anchor",
            ],
          });
          ctx.observers?.set?.("activity_request_materialization", observer);
        } catch (_) {
          observer = null;
        }
      }
      if (timeline) {
        for (const type of ["scroll", "scrollend"]) {
          const release = ctx.lifecycle.listen?.(
            "activity_request_materialization",
            timeline,
            type,
            check,
            { passive: true },
          );
          if (typeof release === "function") releases.push(release);
        }
      }
      timer = ctx.lifecycle.timeout(
        "activity_request_materialization",
        () => finish(findActivityRequestTarget(
          context.prompt,
          context.turnId,
          context.allowUnkeyedFallback,
        )),
        timeout,
      );
      if (typeof window.requestAnimationFrame === "function") {
        ctx.lifecycle.frame("activity_request_materialization", check);
      } else {
        check();
      }
    });
  }

  async function materializeActivityRequest(context, operation) {
    if (!activityScrollIsCurrent(operation)) return null;
    let target = findActivityRequestTarget(
      context.prompt,
      context.turnId,
      context.allowUnkeyedFallback,
    );
    if (target) return target;
    if (context.allowUnkeyedFallback === false) return null;
    const timeline = document.querySelector("[data-app-action-timeline-scroll]");
    if (!timeline) return null;
    const mountedAnchor = findActivityTurnAnchor(context.turnId);
    if (mountedAnchor) {
      mountedAnchor.scrollIntoView?.({ block: "center", inline: "nearest", behavior: "auto" });
      target = await waitForMaterializedActivityRequest(context, 1500, timeline, operation);
      if (!activityScrollIsCurrent(operation)) return null;
      if (target) return target;
    }
    let maxOffset = Math.max(0, Number(timeline.scrollHeight || 0) - Number(timeline.clientHeight || 0));
    if (maxOffset <= 0) return null;
    const originalOffset = activityTimelineOffset(timeline);
    const taskIndex = Math.max(1, Math.round(Number(context.taskIndex || 1)));
    const taskCount = Math.max(taskIndex, Math.round(Number(context.taskCount || taskIndex)));
    const anchor = taskCount > 1
      ? maxOffset * ((taskCount - taskIndex) / (taskCount - 1))
      : originalOffset;
    const positions = [];
    const seen = new Set();
    const addPosition = (value, insertAt = null) => {
      const normalized = Math.round(clamp(Number(value || 0), 0, maxOffset));
      if (!seen.has(normalized)) {
        seen.add(normalized);
        if (Number.isInteger(insertAt)) {
          positions.splice(clamp(insertAt, 0, positions.length), 0, normalized);
        } else {
          positions.push(normalized);
        }
      }
    };
    addPosition(anchor);
    // Reaching the oldest loaded boundary is what prompts the official list to
    // fetch another history page.  Try both boundaries early, then refine.
    addPosition(maxOffset);
    addPosition(0);
    const page = Math.max(120, Number(timeline.clientHeight || 0) * 0.82);
    for (let step = 1; step <= 8; step += 1) {
      addPosition(anchor - (page * step));
      addPosition(anchor + (page * step));
    }
    // Turn heights are not uniform, so also probe a coarse set of absolute
    // positions.  This remains bounded while covering conversations where a
    // single long turn makes the task-index estimate inaccurate.
    for (let segment = 1; segment < 12; segment += 1) {
      addPosition((maxOffset * segment) / 12);
    }
    const deadline = Date.now() + 6500;
    for (let index = 0; index < positions.length && Date.now() < deadline; index += 1) {
      if (!activityScrollIsCurrent(operation)) return null;
      maxOffset = Math.max(
        maxOffset,
        Math.max(0, Number(timeline.scrollHeight || 0) - Number(timeline.clientHeight || 0)),
      );
      const position = clamp(Number(positions[index] || 0), 0, maxOffset);
      setActivityTimelineOffset(timeline, position);
      const atBoundary = position <= 1 || position >= maxOffset - 1;
      const waitMs = atBoundary ? 1200 : (index === 0 ? 700 : 350);
      target = await waitForMaterializedActivityRequest(context, waitMs, timeline, operation);
      if (!activityScrollIsCurrent(operation)) return null;
      if (target) return target;
      const updatedMaxOffset = Math.max(
        0,
        Number(timeline.scrollHeight || 0) - Number(timeline.clientHeight || 0),
      );
      if (updatedMaxOffset > maxOffset + 1) {
        // Pagination can grow the spacer after reaching the old boundary.
        // Queue the new boundary and a fresh task-index estimate instead of
        // abandoning the scan with the stale scrollHeight.
        maxOffset = updatedMaxOffset;
        const insertAt = index + 1;
        addPosition(
          maxOffset * ((taskCount - taskIndex) / Math.max(1, taskCount - 1)),
          insertAt,
        );
        addPosition(maxOffset, insertAt);
      }
    }
    if (!activityScrollIsCurrent(operation)) return null;
    setActivityTimelineOffset(timeline, originalOffset);
    await waitForActivityVirtualization(1, operation);
    return null;
  }

  function activityComparableText(value) {
    // Strip markdown that rendering removes (**, `, headings, lists, links)
    // so JSONL needles can match the rendered DOM text on both sides.
    return String(value || "")
      .split(/\r?\n/)
      .map((line) => line
        .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/(\*\*|__|\*|~~|`)/g, "")
        .replace(/^\s*(?:(?:#{1,6}|[-*+]|\d+[.)]|>)\s+|(?:输入|输出|工具调用|工具返回|推理|活动)\s*[：:]\s*)/, ""))
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function activityRoundNeedles(copyText) {
    const source = activityComparableText(copyText);
    const lines = String(copyText || "")
      .split(/\r?\n/)
      .map(activityComparableText)
      .filter((line) => (
        line.length >= 10
        && !/^Req\d+-#\d+/.test(line)
        && !/^(?:金额|Tokens)\b/i.test(line)
        && !/^(?:↑|↻|↓|◇)/.test(line)
      ));
    if (source.length >= 10 && !/^Req\d+-#\d+/.test(source)) lines.push(source);
    return Array.from(new Set(lines))
      .sort((left, right) => right.length - left.length)
      .map((line) => line.slice(0, 600));
  }

  function activityNodeIsEffectivelyVisible(node) {
    const rect = node.getBoundingClientRect?.();
    return !!rect && rect.width > 0 && rect.height > 0;
  }

  function activityTextMatches(scope, needle) {
    if (!scope || !needle) return [];
    const hudRoot = document.getElementById(rootId);
    const selectors = [
      "[data-content-search-unit-key]",
      "[data-markdown-text-style='assistant-message']",
      "[data-message-author-role]",
      "[data-message-id]",
      "[role='listitem']",
      "p",
      "pre",
      "code",
      "button",
      "div",
      "span",
    ];
    return Array.from(scope.querySelectorAll?.(selectors.join(",")) || [])
      .filter((node) => !hudRoot?.contains(node))
      // Match against textContent: Codex keeps a height:0 search-index copy
      // of collapsed round output whose innerText is empty, so a
      // visibility-aware read would never match it even when mounted.
      .map((node) => ({
        node,
        visible: activityNodeIsEffectivelyVisible(node),
        text: activityComparableText(node.textContent),
      }))
      .filter(({ text }) => text.includes(needle))
      // Prefer visible renderings over the hidden search-index copy, then the
      // smallest exact container.
      .sort((left, right) => (
        Number(right.visible) - Number(left.visible)
        || (left.text.length - needle.length) - (right.text.length - needle.length)
      ));
  }

  // DOM_POSITION bits kept local so round-ordinal ordering never depends on
  // the global Node constructor being present.
  const DOM_POSITION_PRECEDING = 2;
  const DOM_POSITION_FOLLOWING = 4;

  function activityRoundOrdinal(node, scope, headers = null) {
    // 1-based ordinal of the round a node sits in within a request turn, or 0
    // when the boundary cannot be determined. Uses the same work-disclosure
    // headers the disclosure fallback relies on: a node belongs to round k
    // when it is contained in, or appears after, the k-th header.
    if (!node) return 0;
    const toggles = headers || activityWorkDisclosureToggles(scope);
    if (!toggles.length) return 0;
    let ordinal = 0;
    for (const header of toggles) {
      if (typeof header.contains === "function" && header.contains(node)) {
        ordinal += 1;
        break;
      }
      if (typeof node.compareDocumentPosition !== "function") break;
      const bits = node.compareDocumentPosition(header);
      if (bits & DOM_POSITION_FOLLOWING) {
        ordinal += 1;
        continue;
      }
      if (bits & DOM_POSITION_PRECEDING) break;
      ordinal += 1;
      break;
    }
    return ordinal;
  }

  function activityRoundNeedlePool(copyText, locateTexts) {
    const pool = [];
    const seen = new Set();
    const add = (source) => {
      for (const needle of activityRoundNeedles(source)) {
        if (needle && !seen.has(needle)) {
          seen.add(needle);
          pool.push(needle);
        }
      }
    };
    add(copyText);
    for (const text of Array.isArray(locateTexts) ? locateTexts : []) {
      add(String(text || ""));
    }
    return pool.slice(0, 24);
  }

  function findActivityRoundTarget(copyText, requestTarget, roundIndex = 0, locateTexts = []) {
    const needles = activityRoundNeedlePool(copyText, locateTexts);
    const requestScope = requestTarget?.turn || requestTarget?.unit || null;
    if (!requestScope) return null;
    const expectedRound = Math.round(Number(roundIndex || 0));
    const headers = activityWorkDisclosureToggles(requestScope);
    const visibleMatches = [];
    const hiddenMatches = [];
    const seen = new Set();
    for (const needle of needles) {
      // Prefer the first needle with a visible match: the longest copyText
      // needle often only exists in Codex's hidden search-index copy, while a
      // shorter line or another round entry (agent message, tool call) can
      // match the visible paragraph the user should actually see.
      for (const match of activityTextMatches(requestScope, needle)) {
        if (seen.has(match.node)) continue;
        seen.add(match.node);
        if (visibleActivityNode(match.node) === match.node) visibleMatches.push(match.node);
        else hiddenMatches.push(match.node);
      }
    }
    const selectRoundAware = (list) => {
      // The same tool output can appear in several rounds of a task, and the
      // tightest text match would then land on the first occurrence instead of
      // the requested one. Prefer a candidate whose round ordinal is closest
      // to the requested round index.
      if (expectedRound <= 0 || !list.length) return null;
      const ordinals = list
        .map((node) => ({ node, ordinal: activityRoundOrdinal(node, requestScope, headers) }))
        .filter((entry) => entry.ordinal > 0)
        .sort((left, right) => (
          Math.abs(left.ordinal - expectedRound) - Math.abs(right.ordinal - expectedRound)
          || left.ordinal - right.ordinal
        ));
      return ordinals[0]?.node || null;
    };
    return selectRoundAware(visibleMatches)
      || visibleMatches[0]
      || selectRoundAware(hiddenMatches)
      || hiddenMatches[0]
      || null;
  }

  function visibleActivityNode(node) {
    let current = node;
    for (let depth = 0; current && depth < 24; depth += 1) {
      // Collapsed subtrees hide either via display:none (no rects) or, for
      // Codex's 已处理 group, a height:0 container that still reports a
      // client rect — both must count as hidden.
      if (activityNodeIsEffectivelyVisible(current)) return current;
      current = current.parentElement;
    }
    return null;
  }

  function scrollActivityNodeIntoView(node, behavior = "smooth") {
    const visibleNode = visibleActivityNode(node) || node;
    visibleNode.scrollIntoView?.({ block: "center", inline: "nearest", behavior });
    return visibleNode;
  }

  function activityTargetInViewport(node, timeline) {
    if (!node || typeof node.getBoundingClientRect !== "function") return true;
    const rect = node.getBoundingClientRect();
    if (!rect || rect.height <= 0 || rect.width <= 0) return false;
    const viewport = typeof timeline?.getBoundingClientRect === "function"
      ? timeline.getBoundingClientRect()
      : null;
    if (!viewport || viewport.height <= 0) return true;
    return rect.top < viewport.bottom - 2 && rect.bottom > viewport.top + 2;
  }

  function activityLooksLikeWorkDisclosure(node) {
    // Only the work-summary header (已处理/Worked) is safe to click: generic
    // aria-expanded buttons include image previews, and clicking those pops
    // an overlay over the conversation.
    const label = normalizedActivityText(
      node.getAttribute?.("aria-label")
      || node.getAttribute?.("title")
      || node.innerText
      || node.textContent,
    );
    if (/^(?:已处理|处理了|用时|耗时|历时|worked(?:\s+for)?\s|duration\b|processed\b|took\b)/i.test(label)) return true;
    // Chevron-only buttons carry no text; accept them when the header row
    // they sit in is the work summary itself.
    const rowText = normalizedActivityText(
      node.parentElement?.innerText || node.parentElement?.textContent,
    );
    return /^(?:已处理|处理了|用时|耗时|历时|worked(?:\s+for)?\s|duration\b|processed\b|took\b)/i.test(rowText);
  }

  function activityWorkDisclosureToggles(scope) {
    const hudRoot = document.getElementById(rootId);
    return Array.from(scope?.querySelectorAll?.("button, [role='button']") || [])
      .filter((node) => !hudRoot?.contains?.(node) && activityLooksLikeWorkDisclosure(node));
  }

  function activityDisclosureIsCollapsed(node) {
    const expanded = node.getAttribute?.("aria-expanded");
    if (expanded === "true") return false;
    if (expanded === "false") return true;
    // Codex Desktop lacks aria-expanded on the work-summary toggle; its
    // chevron reads ">" while collapsed and "∨" while expanded.
    const text = normalizedActivityText(node.innerText || node.textContent);
    if (/[∨⌄▾▼]/.test(text)) return false;
    if (/[>›▸❯]/.test(text)) return true;
    // No state signal (chevron may be an SVG glyph): only trust toggles this
    // locator has not already opened as collapsed.
    return node.dataset?.codexHudLocateExpanded !== "true";
  }

  function activityExpandSettleDelay(ms, operation) {
    return new Promise((resolve) => {
      let timer = 0;
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        if (timer) ctx.lifecycle.clearTimeout(timer);
        if (operation?.cancelWait === cancel) operation.cancelWait = null;
        resolve(result);
      };
      const cancel = () => finish(false);
      if (operation) operation.cancelWait = cancel;
      timer = ctx.lifecycle.timeout(
        "activity_round_expand_settle",
        () => finish(true),
        Math.max(0, Number(ms || 0)),
      );
    });
  }

  function clickActivityDisclosureToggle(toggle) {
    if (!toggle || typeof toggle.click !== "function") return false;
    // Mirror the CDP probe's click sequence: some Codex controls only react
    // to the full pointer event chain, not to a bare .click().
    if (typeof MouseEvent === "function" && typeof toggle.dispatchEvent === "function") {
      for (const type of ["pointerdown", "mousedown", "mouseup"]) {
        toggle.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
      }
    }
    toggle.click();
    // Remember what this locator opened: with no aria-expanded and an SVG
    // chevron there is no other way to tell expanded from collapsed on the
    // next locate, and clicking again would fold the group shut.
    if (toggle.dataset) toggle.dataset.codexHudLocateExpanded = "true";
    return true;
  }

  async function ensureActivityDisclosuresExpanded(requestTarget, operation) {
    if (!activityScrollIsCurrent(operation)) return false;
    const scope = requestTarget?.turn || requestTarget?.unit || null;
    if (!scope) return false;
    let clicked = false;
    for (const toggle of activityWorkDisclosureToggles(scope)
      .filter(activityDisclosureIsCollapsed)
      .slice(0, 12)) {
      if (!activityScrollIsCurrent(operation)) return clicked;
      if (clickActivityDisclosureToggle(toggle)) clicked = true;
    }
    if (!clicked) return false;
    if (!(await waitForActivityVirtualization(2, operation))) return clicked;
    await activityExpandSettleDelay(320, operation);
    return clicked;
  }

  async function expandActivityRoundContent(
    copyText,
    requestTarget,
    roundIndex,
    operation,
    locateTexts = [],
  ) {
    if (!activityScrollIsCurrent(operation)) return null;
    const scope = requestTarget?.turn || requestTarget?.unit || null;
    if (!scope) return null;
    // Expansion is an on-demand side effect: stop at the first hit and never
    // re-collapse, mirroring the sidebar project reveal in the CDP probe.
    const deadline = Date.now() + 4000;
    const toggles = activityWorkDisclosureToggles(scope)
      .filter(activityDisclosureIsCollapsed)
      .slice(0, 12);
    for (const toggle of toggles) {
      if (!activityScrollIsCurrent(operation) || Date.now() > deadline) return null;
      if (!clickActivityDisclosureToggle(toggle)) continue;
      if (!(await waitForActivityVirtualization(2, operation))) return null;
      if (!await activityExpandSettleDelay(320, operation)) return null;
      const target = findActivityRoundTarget(copyText, requestTarget, roundIndex, locateTexts);
      if (target) return target;
    }
    return null;
  }

  function activityRoundDisclosureFallback(requestTarget, roundIndex) {
    // Reasoning-heavy or image-heavy rounds never re-render their JSONL text
    // in the DOM, so text needles cannot match.  Work summaries appear one
    // per round in DOM order; land on the round's own summary header by
    // ordinal instead of jumping back to the user request.
    const scope = requestTarget?.turn || requestTarget?.unit || null;
    if (!scope) return null;
    const headers = activityWorkDisclosureToggles(scope);
    if (!headers.length) return null;
    const ordinal = Math.round(Number(roundIndex || 0));
    const index = ordinal >= 1 ? Math.min(ordinal, headers.length) - 1 : headers.length - 1;
    const header = headers[index];
    return visibleActivityNode(header) || header;
  }

  function pulseActivityConversationTarget(target, roundIndex = 0) {
    if (!target) return;
    // Pulse the enclosing round-scoped unit as well as the leaf so the
    // highlight stays visible even when the matched text node is small.
    const hudRoot = document.getElementById(rootId);
    const container = target.closest?.("[data-content-search-unit-key]");
    const pulseNodes = [];
    if (container && container !== target && !hudRoot?.contains?.(container)) {
      pulseNodes.push(container, target);
    } else {
      pulseNodes.push(target);
    }
    for (const node of pulseNodes) {
      const token = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      node.dataset.codexHudLocatePulse = token;
      if (roundIndex) node.dataset.codexHudLocateRound = String(roundIndex);
      node.animate?.(
        [
          { boxShadow: "0 0 0 0 rgba(243, 210, 122, 0)" },
          { boxShadow: "0 0 0 3px rgba(243, 210, 122, .72)" },
          { boxShadow: "0 0 0 8px rgba(243, 210, 122, 0)" },
        ],
        { duration: 1200, easing: "ease-out" },
      );
      ctx.lifecycle.timeout("conversation_locate_pulse", () => {
        if (!node.isConnected || node.dataset.codexHudLocatePulse !== token) return;
        delete node.dataset.codexHudLocatePulse;
        delete node.dataset.codexHudLocateRound;
      }, 1300);
    }
  }

  function scrollToActivityRequest(taskPrompt, behavior = "smooth") {
    const context = selectedActivityTaskContext(taskPrompt);
    const target = findActivityRequestTarget(
      context.prompt,
      context.turnId,
      context.allowUnkeyedFallback,
    );
    const node = target?.unit || target?.turn;
    if (!node) return false;
    node.scrollIntoView?.({ block: "center", inline: "nearest", behavior });
    return true;
  }

  async function scrollToActivityRound(
    copyText,
    taskPrompt,
    roundIndex = 0,
    taskTurnId = "",
    locateTexts = [],
  ) {
    const operation = beginActivityScroll();
    const context = selectedActivityTaskContext(taskPrompt, taskTurnId);
    const timeline = document.querySelector("[data-app-action-timeline-scroll]");
    let requestTarget = await materializeActivityRequest(context, operation);
    if (!activityScrollIsCurrent(operation)) return false;
    let requestUnitTarget = requestTarget?.unit || requestTarget?.turn;
    if (requestUnitTarget) {
      requestUnitTarget.scrollIntoView?.({ block: "center", inline: "nearest", behavior: "auto" });
      if (!(await waitForActivityVirtualization(2, operation))) return false;
      // VirtualizedTurnList may replace the whole mounted turn after the first
      // official-anchor scroll.  Re-resolve by the stable turn id before doing
      // the precise input/output lookup; otherwise the pulse lands on a
      // detached node even though the viewport moved to the right Req.
      const refreshedRequest = findActivityRequestTarget(
        context.prompt,
        context.turnId,
        context.allowUnkeyedFallback,
      ) || await waitForMaterializedActivityRequest(context, 350, timeline, operation);
      if (!activityScrollIsCurrent(operation)) return false;
      if (refreshedRequest) {
        requestTarget = refreshedRequest;
        requestUnitTarget = refreshedRequest.unit || refreshedRequest.turn;
      } else if (!requestUnitTarget.isConnected) {
        requestUnitTarget = null;
      }
    }
    let preciseTarget = findActivityRoundTarget(copyText, requestTarget, roundIndex, locateTexts);
    let disclosureFallback = null;
    if (!preciseTarget) {
      // Codex Desktop collapses round output under disclosures like
      // "已处理 3m 45s".  Expand them one by one so the needle text becomes
      // matchable, then retry the precise lookup before falling back.
      preciseTarget = await expandActivityRoundContent(
        copyText,
        requestTarget,
        roundIndex,
        operation,
        locateTexts,
      );
      if (!activityScrollIsCurrent(operation)) return false;
      if (!preciseTarget) {
        disclosureFallback = activityRoundDisclosureFallback(requestTarget, roundIndex);
      }
    } else if (visibleActivityNode(preciseTarget) !== preciseTarget) {
      // The needle matched content that Codex keeps mounted but collapsed
      // (height:0).  Without expanding the group the pulse would land on an
      // invisible node; expand, then re-resolve the now-visible content.
      const expanded = await ensureActivityDisclosuresExpanded(requestTarget, operation);
      if (!activityScrollIsCurrent(operation)) return false;
      if (expanded) {
        preciseTarget = findActivityRoundTarget(copyText, requestTarget, roundIndex, locateTexts)
          || preciseTarget;
      }
    }
    let target = preciseTarget || disclosureFallback || requestUnitTarget;
    if (!target) {
      finishActivityScroll(operation);
      return false;
    }
    target = scrollActivityNodeIntoView(target, "smooth");
    if (!(await waitForActivityVirtualization(2, operation))) return false;
    const correctedRequest = findActivityRequestTarget(
      context.prompt,
      context.turnId,
      context.allowUnkeyedFallback,
    )
      || (!target.isConnected
        ? await waitForMaterializedActivityRequest(context, 350, timeline, operation)
        : null);
    if (!activityScrollIsCurrent(operation)) return false;
    if (correctedRequest) {
      const correctedPrecise = findActivityRoundTarget(
        copyText,
        correctedRequest,
        roundIndex,
        locateTexts,
      );
      const correctedTarget = correctedPrecise
        || disclosureFallback
        || correctedRequest.unit
        || correctedRequest.turn;
      if (correctedTarget) {
        requestTarget = correctedRequest;
        preciseTarget = correctedPrecise;
        const correctedVisible = visibleActivityNode(correctedTarget) || correctedTarget;
        if (correctedVisible !== target) {
          correctedVisible.scrollIntoView?.({ block: "center", inline: "nearest", behavior: "smooth" });
          target = correctedVisible;
        }
      }
    }
    if (!target.isConnected) {
      finishActivityScroll(operation);
      return false;
    }
    // Virtualization can re-mount the turn after the smooth scroll settles and
    // shift the target out of the viewport. Verify the final position and do
    // one corrective auto scroll before pulsing.
    if (!activityTargetInViewport(target, timeline)) {
      target.scrollIntoView?.({ block: "center", inline: "nearest", behavior: "auto" });
      if (!(await waitForActivityVirtualization(1, operation))) return false;
      if (!activityScrollIsCurrent(operation)) return false;
      if (!target.isConnected) {
        finishActivityScroll(operation);
        return false;
      }
    }
    pulseActivityConversationTarget(target, roundIndex);
    finishActivityScroll(operation);
    return true;
  }

  function renderTopDetails(root, payload) {
    const rawDetails = payload?.topDetails || {};
    const tasks = activityTaskItems(rawDetails);
    const selected = activityTaskSelection(root, payload, rawDetails, tasks);
    const details = selected ? { ...rawDetails, ...selected } : rawDetails;
    const copies = selected && selected.copies && typeof selected.copies === "object"
      ? selected.copies
      : (payload?.topCopies || {});
    const mapping = {
      topTitle: details.title || "Codex 会话 / 预算",
      topSession: details.session || "",
      topSessionCost: details.sessionCost || "",
      topSessionTokens: details.sessionTokens || "",
      topSessionRounds: details.sessionRounds || "",
      topTaskOrdinalSession: details.taskOrdinalSession || "",
      topTaskOrdinalActivity: details.taskOrdinalActivity || "",
      topCacheText: details.cacheText || "",
      topSessionMix: details.sessionMix || "",
      topSessionAverage: details.sessionAverage || "",
      topSessionComposition: details.sessionComposition || "",
      topHeavyRoundsSummary: details.heavyRoundsSummary || "",
      topSessionInputTokens: details.sessionInputTokens || "",
      topSessionCachedTokens: details.sessionCachedTokens || "",
      topSessionOutputTokens: details.sessionOutputTokens || "",
      topSessionReasoningTokens: details.sessionReasoningTokens || "",
      topWarnings: details.warnings || "",
      topExecutingLabel: details.executingLabel || "正在执行",
      topExecuting: details.executing || "",
      topCurrentTaskLabel: details.currentTaskLabel || "当前需求",
      topCurrentTask: details.currentTask || "",
      topActivityState: details.activityState || "",
      topActivityElapsedLabel: details.activityElapsedLabel || "已运行",
      topActivityElapsed: details.activityElapsed || "",
      topActivityGapLabel: details.activityGapLabel || "当前等待",
      topActivityGap: details.activityGap || "",
      topActivityLastLabel: details.activityLastLabel || "需求轮次",
      topActivityLast: details.activityLast || "",
      topSlow: details.slow || "",
      topGap: details.gap || "",
      topActivityTaskOrdinal: selected?.taskOrdinal || "",
    };
    for (const [field, value] of Object.entries(mapping)) setText(root, field, value);
    renderActivityTaskNav(root, rawDetails, tasks, selected);
    setFieldTitle(root, "topActivityLast", details.activityLastTooltip || details.activityLast || "");
    setFieldTitle(root, "topActivityTaskOrdinal", selected?.taskOrdinal || "");
    renderHeavyRounds(root, details);
    renderActivityTimeline(root, details);
    const hasWarnings = !!String(details.warnings || "").trim();
    root.querySelectorAll('[data-field-panel="topWarnings"]').forEach((node) => {
      node.hidden = !hasWarnings;
    });
    budgetDomain.apply(root, payload || {});
    configureCopy(root, "topSlow", copies.slow || "", "点击复制最慢工具命令", "slow");
    configureCopy(root, "topGap", copies.gap || "", "点击复制最长等待详情", "gap");
    configureCopy(root, "topCurrentTask", details.currentTask || "", `点击复制当前需求\n${details.currentTask || ""}`, "task");
    configureCopy(root, "topExecuting", details.executing || "", `点击复制${details.executingLabel || "当前活动"}\n${details.executing || ""}`, "executing");
  }
  function applyCurrentSessionPayload(root, payload) {
    setText(root, "topLine", payload?.topLine || "codex-usage-hud 等待数据");
    setText(root, "requestLine", payload?.requestLine || "本次请求 等待");
    setText(root, "requestLineExpanded", payload?.requestLine || "最近模型请求轮次");
    root.querySelectorAll('[data-field="topLine"], [data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.remove(warningClass);
    });
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.toggle(warningClass, !!payload?.warning);
    });
    root.querySelectorAll('[data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.toggle(errorClass, payload?.requestStatus === "error");
    });
    renderTopDetails(root, payload || {});
    renderRequestRows(
      root,
      payload?.requestRows || [],
      payload?.requestRowDetails || [],
      !!(payload?.newSession || payload?.pendingSession),
      payload?.requestRowsTotal || 0,
      payload?.rendererSessionId || payload?.sessionId || "",
    );
    renderActiveSessionCandidates(root, payload || {});
    renderBackgroundUsageNotification(root, payload || {});
    diagnosticsDomain.applyConnectionHealth(root, payload || {});
    applyActiveSessionSequence(payload);
  }
  function applyActiveSessionSequence(payload) {
    if (payload?.cachedPreview) return;
    const appliedSeq = Number(payload?.selectionSeq || 0);
    if (appliedSeq > Number(window[activeSessionAppliedSeqName] || 0)) {
      window[activeSessionAppliedSeqName] = appliedSeq;
    }
    if (appliedSeq > Number(window[activeSessionSelectionSeqName] || 0)) {
      // A HUD reinjection can receive an observation from the previous script
      // realm before installing the new one. Keep the next click monotonic
      // relative to the Python tracker that already accepted that sequence.
      window[activeSessionSelectionSeqName] = appliedSeq;
    }
  }

  function applySessionSwitchPayload(root, payload) {
    setText(root, "topLine", payload?.topLine || "codex-usage-hud 等待数据");
    setText(root, "requestLine", payload?.requestLine || "本次请求 等待");
    setText(root, "requestLineExpanded", payload?.requestLine || "最近模型请求轮次");
    root.querySelectorAll('[data-field="topLine"], [data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.remove(warningClass);
    });
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.toggle(warningClass, !!payload?.warning);
    });
    root.querySelectorAll('[data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.toggle(errorClass, payload?.requestStatus === "error");
    });
    renderActiveSessionCandidates(root, payload || {});
    renderBackgroundUsageNotification(root, payload || {});
    diagnosticsDomain.applyConnectionHealth(root, payload || {});
    applyActiveSessionSequence(payload);
  }

    // 索引检索跳转：高亮、滚动定位与上/下命中导航全部交给 Codex 原生的
    // 「在会话中查找」（Ctrl+F，内部命令 findInThread，消息 type
    // `find-in-thread`）；HUD 只负责在跳转落地后打开它并填入检索词。
    // 原生查找栏的输入框 id 为 content-search-input，虚拟列表与计数
    // （N / M results）均由它自己处理，HUD 不再做任何命中定位。
    let threadFindJump = null;
    let pendingSearchJump = "";

    function cancelThreadFindJump() {
      if (threadFindJump?.timer) ctx.lifecycle.clearTimeout(threadFindJump.timer);
      threadFindJump = null;
    }

    function prepareSearchJump(itemId) {
      cancelThreadFindJump();
      pendingSearchJump = itemId;
    }

    function fillThreadFindInput(input, query, { force = false } = {}) {
      // React 受控输入：必须走 native value setter + input 事件才会触发 onChange。
      const value = String(query || "");
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      if (!input || !setter || (!force && input.value === value)) return false;
      // React tracks the last value it observed. When a repeated jump uses the
      // same query, setting that same value again can be treated as a no-op;
      // clear once to produce a real change event before restoring the query.
      if (force && input.value === value) {
        setter.call(input, "");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      // 必须再触发回车，否则 Codex 原生查找栏只显示填充文本但不执行检索
      //（截图复现：搜索框有词但显示「0 个结果」，手动按回车后才高亮）。
      input.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Enter", code: "Enter", keyCode: 13, which: 13,
        bubbles: true, cancelable: true,
      }));
      return true;
    }

    function threadFindInputVisible(input) {
      if (!input?.isConnected) return false;
      const style = window.getComputedStyle?.(input);
      if (style && (style.display === "none" || style.visibility === "hidden")) return false;
      const rect = input.getBoundingClientRect?.();
      return !!rect && rect.width > 0 && rect.height > 0;
    }

    function openThreadFind(query) {
      const value = String(query || "").trim();
      if (!value) return;
      cancelThreadFindJump();
      const existingInput = document.getElementById("content-search-input");
      const state = { query: value, timer: 0 };
      threadFindJump = state;
      // Sending find-in-thread while the native bar is already open toggles it
      // closed. Reuse the mounted input and force a fresh search instead, so a
      // repeated click on the same result keeps the match highlighted.
      if (threadFindInputVisible(existingInput)) {
        fillThreadFindInput(existingInput, value, { force: true });
        state.timer = ctx.lifecycle.timeout("thread_find_refill", () => {
          if (threadFindJump !== state) return;
          const current = document.getElementById("content-search-input");
          if (current) fillThreadFindInput(current, value, { force: true });
        }, 120);
        return;
      }
      // Codex 的消息中枢监听 window message（信封只要求同源 + string type），
      // 因此与官方 Ctrl+F 完全同通道：postMessage 即可打开查找栏。
      window.postMessage({ type: "find-in-thread" }, "*");
      let elapsed = 0;
      const step = () => {
        if (threadFindJump !== state) return;
        const input = document.getElementById("content-search-input");
        if (input) {
          fillThreadFindInput(input, value, { force: true });
          // 会话可能在查找栏打开后才挂载完；若输入框仍是我们填的词
          // （用户没有开始手动输入），补触发一次让原生检索重扫已挂载内容。
          state.timer = ctx.lifecycle.timeout("thread_find_refill", () => {
            if (threadFindJump !== state) return;
            const current = document.getElementById("content-search-input");
            if (current && current.value === value) {
              fillThreadFindInput(current, value, { force: true });
            }
          }, 800);
          return;
        }
        elapsed += 80;
        if (elapsed < 2400) state.timer = ctx.lifecycle.timeout("thread_find_open", step, 80);
      };
      step();
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
      }[ch]));
    }
    function escapeAttr(value) {
      return escapeHtml(value).replace(/"/g, "&quot;");
    }

    // 会话内检索浮窗：仅在跳转到 Codex 会话后出现。浮窗列出排序后的命中会话、
    // 查询分词（点击即用该分词驱动原生查找），并支持上一个 / 下一个在命中会话间
    // 穿梭；高亮、滚动与计数完全交给 Codex 原生 find-in-thread。
    let searchFloat = null;

    // 把会话 ISO 时间戳拆成年/月/日标签，供浮窗按月分组与逐行展示日期。
    function sessionMonthKeyFromIso(iso) {
      if (!iso || typeof iso !== "string") return "";
      const m = /^(\d{4})-(\d{2})/.exec(iso);
      if (!m) return "";
      return m[1] + "-" + m[2];
    }
    function sessionMonthLabel(key) {
      if (!key) return "未知日期";
      const parts = key.split("-");
      return parts[0] + "年" + parseInt(parts[1], 10) + "月";
    }
    function sessionDateLabel(iso) {
      if (!iso || typeof iso !== "string") return "";
      const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
      if (!m) return "";
      return parseInt(m[2], 10) + "月" + parseInt(m[3], 10) + "日";
    }

    function renderSearchFloat() {
      if (!searchFloat) return;
      let panel = document.getElementById("codex-usage-hud-search-float");
      if (!panel) {
        panel = document.createElement("div");
        panel.id = "codex-usage-hud-search-float";
        const host = document.getElementById(rootId) || document.body;
        host.appendChild(panel);
        // 拖拽吸附只在面板创建时绑定一次，避免 applySearchJump 重复 addEventListener。
        bindSearchFloatDrag(panel);
      }
      const { query, revision, currentId } = searchFloat;
      // 「只看可高亮」把仅命中工具输出、跳转后无法被 Codex 原生查找高亮的
      // 会话整条滤掉，避免用户反复点进没有结果的会话。
      const onlyExact = Boolean(searchFloat.onlyExact);
      const matches = onlyExact
        ? (searchFloat.matches || []).filter((entry) => entry.exactPhrase)
        : (searchFloat.matches || []);
      const total = matches.length;
      const idx = matches.findIndex((entry) => entry.id === currentId);
      const collapsed = searchFloat.collapsed ? " is-collapsed" : "";
      const dragging = Boolean(searchFloat.dragging || panel.classList.contains("is-dragging"));
      const side = !dragging && searchFloat.side ? ` ${searchFloat.side}` : "";
      panel.className = `codex-usage-hud-search-float${collapsed}${side}${dragging ? " is-dragging" : ""}`;
      // 按月分组（月份降序：最新在前），组内沿用检索排序；每个分组标题
      // 显示月份与命中数量，逐行额外展示该会话的日期（月-日）。
      const buckets = new Map();
      matches.forEach((entry) => {
        const key = sessionMonthKeyFromIso(entry.updatedAt);
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(entry);
      });
      const monthOrder = [...buckets.keys()]
        .filter((k) => k)
        .sort((a, b) => (a < b ? 1 : -1));
      if (buckets.has("")) monthOrder.push("");
      let rowsHtml = "";
      let displayIndex = 0;
      monthOrder.forEach((key) => {
        const groupItems = buckets.get(key);
        rowsHtml += `<li class="codex-usage-hud-search-group"><span>${escapeHtml(sessionMonthLabel(key))}</span><span class="codex-usage-hud-search-group-count">${groupItems.length}</span></li>`;
        groupItems.forEach((entry) => {
          displayIndex += 1;
          const rowChips = (entry.matchedTokens || [])
            .map((token) => `<button type="button" class="codex-usage-hud-search-chip" data-token="${escapeAttr(token)}">${escapeHtml(token)}</button>`)
            .join("");
          const dateLabel = sessionDateLabel(entry.updatedAt);
          const kinds = Array.isArray(entry.kinds) ? entry.kinds : [];
          const indexOnly = !entry.exactPhrase
            && kinds.length > 0
            && kinds.every((kind) => kind !== "user" && kind !== "assistant");
          const rowMeta = [
            entry.exactPhrase
              ? '<span class="codex-usage-hud-search-badge">正文精确命中</span>'
              : (indexOnly ? '<span class="codex-usage-hud-search-badge is-index-only" title="命中索引内容，打开后可能无法被 Codex 原生查找定位">索引命中</span>' : ""),
            dateLabel ? `<span class="codex-usage-hud-search-date">${dateLabel}</span>` : "",
          ].filter(Boolean).join("");
          rowsHtml += `
          <li class="codex-usage-hud-search-row${entry.id === currentId ? " is-current" : ""}" data-item-id="${escapeAttr(entry.id)}" data-revision="${escapeAttr(revision)}">
            <span class="codex-usage-hud-search-index">${displayIndex}</span>
            <span class="codex-usage-hud-search-row-main">
              <span class="codex-usage-hud-search-title">${escapeHtml(entry.title || entry.id)}</span>
              ${rowMeta ? `<span class="codex-usage-hud-search-row-meta">${rowMeta}</span>` : ""}
              ${rowChips ? `<span class="codex-usage-hud-search-row-chips">${rowChips}</span>` : ""}
            </span>
          </li>`;
        });
      });
      panel.innerHTML = `
        <div class="codex-usage-hud-search-head">
          <button type="button" class="codex-usage-hud-search-toggle" data-action="toggle" title="${searchFloat.collapsed ? "展开" : "折叠"}" aria-label="${searchFloat.collapsed ? "展开检索浮窗" : "折叠检索浮窗"}"><span aria-hidden="true">${searchFloat.collapsed ? "»" : "«"}</span></button>
          <div class="codex-usage-hud-search-heading">
            <span class="codex-usage-hud-search-kicker">会话命中</span>
            <span class="codex-usage-hud-search-query" title="${escapeAttr(query)}">${escapeHtml(query)}</span>
          </div>
          <button type="button" class="codex-usage-hud-search-close" data-action="close" title="关闭" aria-label="关闭检索浮窗"><span aria-hidden="true">×</span></button>
        </div>
        <div class="codex-usage-hud-search-meta">
          <span class="codex-usage-hud-search-meta-count">${total} 个命中</span>
          <span aria-hidden="true">·</span>
          <span>当前第 ${idx >= 0 ? idx + 1 : "-"} / ${total}</span>
          <button type="button" data-action="only-exact" class="codex-usage-hud-search-filter${onlyExact ? " is-on" : ""}" aria-pressed="${onlyExact ? "true" : "false"}" title="只列出正文连续命中、跳转后可被 Codex 查找高亮的会话">只看可高亮</button>
        </div>
        ${searchFloat.notice ? `<div class="codex-usage-hud-search-notice">${escapeHtml(searchFloat.notice)}</div>` : ""}
        ${total ? `<ul class="codex-usage-hud-search-list">${rowsHtml}</ul>` : '<div class="codex-usage-hud-search-empty">没有正文连续命中的会话，可关闭「只看可高亮」查看索引命中。</div>'}
        <div class="codex-usage-hud-search-nav">
          <button type="button" data-action="prev" aria-label="上一个命中" ${idx <= 0 ? "disabled" : ""}><span class="codex-usage-hud-search-nav-icon" aria-hidden="true">←</span><span>上一个</span></button>
          <button type="button" data-action="next" aria-label="下一个命中" ${(idx < 0 || idx >= total - 1) ? "disabled" : ""}><span>下一个</span><span class="codex-usage-hud-search-nav-icon" aria-hidden="true">→</span></button>
          <button type="button" data-action="return" class="codex-usage-hud-search-return" aria-label="返回会话管理"><span class="codex-usage-hud-search-nav-icon" aria-hidden="true">↩</span><span>会话管理</span></button>
        </div>`;
    }

    function toggleSearchFloat() {
      if (!searchFloat) return;
      searchFloat.collapsed = !searchFloat.collapsed;
      renderSearchFloat();
    }

    function bindSearchFloat(panel) {
      panel.onclick = (event) => {
        const chip = event.target?.closest?.(".codex-usage-hud-search-chip");
        const row = event.target?.closest?.(".codex-usage-hud-search-row");
        const action = event.target?.closest?.("[data-action]");
        if (chip) {
          event.preventDefault();
          openThreadFind(String(chip.dataset.token || ""));
          return;
        }
        if (row) {
          event.preventDefault();
          jumpToSearchResult(String(row.dataset.itemId || ""), String(row.dataset.revision || ""));
          return;
        }
        if (!action) return;
        const name = action.dataset.action;
        if (name === "toggle") { toggleSearchFloat(); return; }
        if (name === "close") { closeSearchFloat(); return; }
        if (name === "only-exact") {
          searchFloat.onlyExact = !searchFloat.onlyExact;
          searchFloat.notice = "";
          renderSearchFloat();
          return;
        }
        if (name === "return") {
          closeSearchFloat();
          if (typeof window.__codexUsageHudOpenSessionCleanup === "function") {
            window.__codexUsageHudOpenSessionCleanup("storage");
          }
          return;
        }
        if (name === "prev" || name === "next") {
          const ctxFloat = searchFloat;
          if (!ctxFloat || !ctxFloat.matches) return;
          const at = ctxFloat.matches.findIndex((entry) => entry.id === ctxFloat.currentId);
          const next = name === "prev" ? at - 1 : at + 1;
          if (next >= 0 && next < ctxFloat.matches.length) {
            jumpToSearchResult(ctxFloat.matches[next].id, ctxFloat.revision);
          }
        }
      };
    }

    // 查找 Codex 聊天正文内容区域的左边缘，用于浮窗左侧吸附定位。
    // 依次尝试多个已知选择器（Codex 版本间可能变化），找不到则回退到
    // 视口左侧（带默认侧栏宽度偏移）。
    function chatContentLeftEdge() {
      const candidates = [
        // 主滚动容器（虚拟列表宿主）
        "[class*='conversation']",
        "[class*='chat-content']",
        "[class*='thread-view']",
        "[class*='message-list']",
        "main",
        "[role='main']",
      ];
      for (const sel of candidates) {
        const el = document.querySelector(sel);
        if (el) {
          const r = el.getBoundingClientRect();
          if (r.width > 200) return r.left;
        }
      }
      return 280; // 回退值：典型侧栏宽度
    }

    function bindSearchFloatDrag(panel) {
      let startX = 0, startY = 0, originLeft = 0, originTop = 0, dragging = false;
      panel.addEventListener("pointerdown", (event) => {
        if (searchFloat?.collapsed) return;
        // 排除交互元素与可滚动列表（含其滚动条），避免按住滚动条时
        // 面板被当拖拽起点而飘到 (0,0)。
        if (event.target?.closest?.("button, .codex-usage-hud-search-row, .codex-usage-hud-search-chip, .codex-usage-hud-search-list")) return;
        const rect = panel.getBoundingClientRect();
        dragging = true;
        if (searchFloat) searchFloat.dragging = true;
        panel.classList.add("is-dragging");
        // Read the visual rect before removing the centered side class. With
        // all fixed insets set to auto, the HUD root's static position is
        // (0, 0), which used to make the panel jump as soon as it was held.
        panel.style.left = `${rect.left}px`;
        panel.style.top = `${rect.top}px`;
        panel.style.right = "auto";
        panel.style.bottom = "auto";
        panel.classList.remove("side-left", "side-right");
        originLeft = rect.left;
        originTop = rect.top;
        startX = event.clientX;
        startY = event.clientY;
        try { panel.setPointerCapture(event.pointerId); } catch (_) {}
      });
      panel.addEventListener("pointermove", (event) => {
        if (!dragging) return;
        const dx = event.clientX - startX;
        const dy = event.clientY - startY;
        const nx = Math.max(4, Math.min(window.innerWidth - panel.offsetWidth - 4, originLeft + dx));
        const ny = Math.max(4, Math.min(window.innerHeight - panel.offsetHeight - 4, originTop + dy));
        panel.style.left = `${nx}px`;
        panel.style.top = `${ny}px`;
      });
      const end = (event) => {
        if (!dragging) return;
        dragging = false;
        panel.classList.remove("is-dragging");
        try { panel.releasePointerCapture(event.pointerId); } catch (_) {}
        if (!searchFloat) return;
        searchFloat.dragging = false;
        const rect = panel.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        if (cx < window.innerWidth * 0.33) {
          searchFloat.side = "side-left";
          panel.classList.add("side-left");
        } else if (cx > window.innerWidth * 0.67) {
          searchFloat.side = "side-right";
          panel.classList.add("side-right");
        } else {
          searchFloat.side = "";
        }
        if (searchFloat.side === "side-left") {
          // 吸附到 Codex 聊天正文区域的左边缘（而非整个窗口左缘）。
          panel.style.left = `${chatContentLeftEdge()}px`;
          panel.style.top = "";
          panel.style.right = "";
        } else if (searchFloat.side === "side-right") {
          panel.style.left = "";
          panel.style.top = "";
          panel.style.right = "";
        }
      };
      panel.addEventListener("pointerup", end);
      panel.addEventListener("pointercancel", end);
    }

    function jumpToSearchResult(itemId, revision) {
      if (!itemId) return;
      const query = String(searchFloat?.query || "");
      // 点击浮窗某会话：无论是否已在当前会话，都把命中关键词立即注入 Codex
      // 自带搜索框定位；同时始终发起会话跳转命令，确保即使用户在 Codex 里手动
      // 切到了其它会话，也能点此行跳回该命中会话（落地回执由 applySearchJump
      // 再次打开原生查找栏）。
      // 仅工具输出命中的会话，正文里并没有这段连续关键词，跳转后 Codex
      // 原生查找多半无高亮。先就地说明，避免用户误以为跳转或高亮坏了。
      const entry = (searchFloat?.matches || []).find((item) => item.id === itemId);
      if (searchFloat) {
        searchFloat.notice = entry && !entry.exactPhrase
          ? "该会话正文没有这段完整关键词（命中来自工具输出），跳转后 Codex 查找可能无高亮"
          : "";
        renderSearchFloat();
      }
      if (query) openThreadFind(query);
      prepareSearchJump(itemId);
      const sent = ctx.bindings.send(settingsCommandBindingName, {
        id: `search-jump-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        createdAt: Date.now(),
        action: "openSessionCleanupSession",
        itemId,
        inventoryRevision: revision,
        searchQuery: query,
      });
      if (sent === false) {
        console.warn("search jump command channel unavailable");
      }
    }

    function closeSearchFloat() {
      const panel = document.getElementById("codex-usage-hud-search-float");
      if (panel) panel.remove();
      searchFloat = null;
    }

    async function applySearchJump(response) {
      if (!response || !response.search) return;
      const query = String(response.search?.query || "").trim();
      // 刷新浮窗上下文：维护排序后的命中列表、分词与当前所在会话。
      // 保留用户已设置的折叠 / 吸附侧边状态，不每次覆盖。
      if (
        response.itemId
        && Array.isArray(response.search.matches)
        && response.search.matches.length
      ) {
        const previous = searchFloat || {};
        searchFloat = {
          query,
          tokens: Array.isArray(response.search.tokens) ? response.search.tokens : [],
          matches: response.search.matches,
          revision: String(response.search.revision || ""),
          currentId: String(response.itemId || ""),
          collapsed: Boolean(previous.collapsed),
          side: previous.side || "side-right",
          dragging: Boolean(previous.dragging),
        };
        renderSearchFloat();
        bindSearchFloat(document.getElementById("codex-usage-hud-search-float"));
      }
      if (!pendingSearchJump || response?.itemId !== pendingSearchJump) return;
      if (!query || response.search?.error || !response.verified) { pendingSearchJump = ""; return; }
      pendingSearchJump = "";
      const targetKey = String(response.search?.targetKey || "");
      // 跳转回执可能早于 Codex 的 active-session 事件到达；轮询等待落地会话与
      // 跳转目标一致后再打开原生查找栏，避免因为时序差静默放弃检索高亮。
      let waited = 0;
      const state = { timer: 0 };
      threadFindJump = state;
      const probe = async () => {
        if (threadFindJump !== state) return;
        const identity = String(readActiveSessionRef()?.sessionId || "");
        if (!identity || waited >= 2600) { cancelThreadFindJump(); return; }
        const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(identity));
        const key = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
        if (key !== targetKey) {
          waited += 90;
          state.timer = ctx.lifecycle.timeout("thread_find_identity", () => { void probe(); }, 90);
          return;
        }
        cancelThreadFindJump();
        openThreadFind(query);
      };
      void probe();
    }

    let installed = false;
    let activeActivityScroll = null;
    let activityScrollSerial = 0;
    const numericTokenRe = /\$?\d+(?:,\d{3})*(?:\.\d+)?(?:[kM%])?/g;
    const numericAnimations = new WeakMap();

    function install() {
      if (installed) return false;
      installed = true;
      return true;
    }

    function apply(root, payload, kind = "currentSession") {
      if (!installed) install();
      if (kind === "sessionSwitch") applySessionSwitchPayload(root, payload || {});
      else applyCurrentSessionPayload(root, payload || {});
    }

    function dispose() {
      const wasInstalled = installed;
      installed = false;
      cancelThreadFindJump();
      cancelActivityScroll();
      ctx.observers?.clear?.("activity_request_materialization");
      const root = document.getElementById(rootId);
      root?.querySelectorAll(".codex-usage-hud-line").forEach(cancelNumericAnimation);
      ctx.lifecycle.clearInterval(window[runningTimerName] || 0);
      window[runningTimerName] = 0;
      return wasInstalled;
    }

    return {
      install,
      apply,
      dispose,
      cancelNumericAnimation,
      configureCopy,
      copyHudText,
      flashCopyState,
      refreshAllMarquees,
      refreshMarquee,
      renderActivityTimeline,
      renderTopDetails,
      locateActivityTrailRound,
      selectActivityTask,
      selectActivityTaskIndex,
      scrollToActivityRequest,
      scrollToActivityRound,
      prepareSearchJump,
      applySearchJump,
      setFieldTitle,
      setText,
    };
  }

  const sessionViewDomain = ctx.domains.register(
    "session_view",
    createSessionViewDomain(ctx, shared),
  );
  const {
    cancelNumericAnimation,
    configureCopy,
    copyHudText,
    flashCopyState,
    refreshAllMarquees,
    refreshMarquee,
    renderActivityTimeline,
    setFieldTitle,
    setText,
  } = sessionViewDomain;
"""

__all__ = ["TEXT"]
