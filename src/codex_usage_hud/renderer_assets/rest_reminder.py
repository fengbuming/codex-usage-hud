"""Renderer rest-reminder JavaScript domain."""

TEXT = r"""
  function createRestReminderDomain(ctx, shared) {
  let dismissedReminderKey = "";

  function reminderKey(reminder) {
    const phase = String(reminder?.phase || "");
    if (phase !== "prompt" && phase !== "preview") return "";
    return `${phase}:${Number(reminder?.promptStartedAtMs) || 0}:${Number(reminder?.promptEndsAtMs) || 0}`;
  }

  function dismiss() {
    // 后台确认前仍可能推送旧状态；只压住本轮提醒，下一轮使用新的时间戳。
    dismissedReminderKey = reminderKey(currentPayload()?.restReminder);
    renderRestReminderToast(null, currentPayload());
  }

  function restore() {
    dismissedReminderKey = "";
    renderRestReminderToast(null, currentPayload());
  }

  function restReminderToastMarkup() {
    return `
      <div class="codex-usage-hud-rest-mask" data-rest-reminder-mask="true" data-visible="false" aria-hidden="true"></div>
      <div class="codex-usage-hud-rest-toast" data-rest-reminder-toast="true" data-visible="false" role="dialog" aria-live="assertive" aria-modal="true" aria-labelledby="codex-usage-hud-rest-title">
        <div class="codex-usage-hud-rest-toast-accent" aria-hidden="true"></div>
        <div class="codex-usage-hud-rest-toast-body">
          <div class="codex-usage-hud-rest-toast-head">
            <div class="codex-usage-hud-rest-toast-icon" aria-hidden="true">☕</div>
            <div>
              <p class="codex-usage-hud-rest-toast-kicker">专注休息</p>
              <div class="codex-usage-hud-rest-toast-title-row">
                <h2 class="codex-usage-hud-rest-toast-title" id="codex-usage-hud-rest-title">该休息一下了</h2>
                <span class="codex-usage-hud-rest-prompt-elapsed" data-rest-reminder-prompt-elapsed="true">已等待 00:00</span>
              </div>
            </div>
          </div>
          <p class="codex-usage-hud-rest-toast-message" data-rest-reminder-message="true">站起来走走，让眼睛放松片刻。</p>
          <p class="codex-usage-hud-rest-toast-hint">
            <span class="codex-usage-hud-rest-toast-hint-dot" aria-hidden="true"></span>
            <span data-rest-reminder-break-countdown="true">休息结束后会自动开始下一轮</span>
          </p>
        </div>
        <div class="codex-usage-hud-rest-toast-early-actions" data-rest-reminder-early-actions="true" hidden>
          <span>我已提前休息过了：</span>
          <button type="button" data-action="rest-reminder-credit" data-minutes="3">3分钟</button>
          <button type="button" data-action="rest-reminder-credit" data-minutes="5">5分钟</button>
          <button type="button" data-action="rest-reminder-credit" data-minutes="10">10分钟</button>
          <button type="button" data-action="rest-reminder-credit-more">更多</button>
          <span class="codex-usage-hud-rest-credit-custom" data-rest-reminder-credit-custom="true" hidden>
            <input type="number" min="1" max="1440" step="1" value="15" aria-label="我已提前休息了多少分钟" data-rest-reminder-credit-custom-input="true">
            <button type="button" data-action="rest-reminder-credit-custom-confirm">确认</button>
            <button type="button" data-action="rest-reminder-credit-custom-cancel">取消</button>
          </span>
        </div>
        <div class="codex-usage-hud-rest-toast-actions">
          <button type="button" class="codex-usage-hud-settings-action" data-action="rest-reminder-postpone" hidden>稍后提醒</button>
          <button type="button" class="codex-usage-hud-settings-action" data-action="rest-reminder-start" data-primary="true">开始休息</button>
        </div>
      </div>
      <div class="codex-usage-hud-rest-bubble" data-rest-reminder-bubble="true" data-visible="false" data-positioned="false" role="status" aria-live="polite">
        <div class="codex-usage-hud-rest-bubble-head">
          <span aria-hidden="true">☕</span>
          <span data-rest-reminder-bubble-title="true">休息提醒</span>
          <span class="codex-usage-hud-rest-prompt-elapsed" data-rest-reminder-prompt-elapsed="true" hidden>已等待 00:00</span>
        </div>
        <div class="codex-usage-hud-rest-bubble-detail" data-rest-reminder-bubble-detail="true"></div>
        <div class="codex-usage-hud-rest-bubble-early-actions" data-rest-reminder-bubble-early-actions="true" hidden>
          <span>我已提前休息过了：</span>
          <button type="button" data-action="rest-reminder-credit" data-minutes="3">3分钟</button>
          <button type="button" data-action="rest-reminder-credit" data-minutes="5">5分钟</button>
          <button type="button" data-action="rest-reminder-credit" data-minutes="10">10分钟</button>
          <button type="button" data-action="rest-reminder-credit-more">更多</button>
          <span class="codex-usage-hud-rest-credit-custom" data-rest-reminder-credit-custom="true" hidden>
            <input type="number" min="1" max="1440" step="1" value="15" aria-label="我已提前休息了多少分钟" data-rest-reminder-credit-custom-input="true">
            <button type="button" data-action="rest-reminder-credit-custom-confirm">确认</button>
            <button type="button" data-action="rest-reminder-credit-custom-cancel">取消</button>
          </span>
        </div>
        <div class="codex-usage-hud-rest-bubble-foot">
          <span class="codex-usage-hud-rest-bubble-status" data-rest-reminder-bubble-status="true"></span>
          <button type="button" data-action="rest-reminder-postpone" data-rest-reminder-bubble-secondary="true" hidden></button>
          <button type="button" data-action="rest-reminder-start" data-rest-reminder-bubble-primary="true" data-primary="true" hidden></button>
        </div>
      </div>
    `;
  }


  function stopRestReminderOverlayTicker() {
    if (!restReminderOverlayTimer) return;
    ctx.lifecycle.clearInterval(restReminderOverlayTimer);
    restReminderOverlayTimer = 0;
  }

  function formatRestReminderBubbleDuration(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainder = total % 60;
    const parts = [minutes, remainder].map((value) => String(value).padStart(2, "0"));
    if (hours > 0) parts.unshift(String(hours).padStart(2, "0"));
    return parts.join(":");
  }

  function restReminderPromptElapsedText(reminder, now = Date.now()) {
    const startedAt = Number(reminder?.promptStartedAtMs) || 0;
    if (startedAt <= 0) return "已等待 00:00";
    return `已等待 ${formatRestReminderBubbleDuration(Math.max(0, (now - startedAt) / 1000))}`;
  }

  function restReminderBubbleCopy(reminder, now = Date.now()) {
    const phase = String(reminder?.phase || "");
    const completedToday = Math.max(0, Number(reminder?.completedTodaySeconds) || 0);
    const message = String(reminder?.message || "该休息一下了。");
    const actions = [];
    let title = "休息提醒";
    let detail = message;
    let status = "";
    if (phase === "prompt") {
      const remaining = Math.max(0, (Number(reminder?.promptEndsAtMs) - now) / 1000);
      title = "该休息一下了";
      status = `等待你的选择 · 不会自动跳过 · 今日已休息 ${formatRestReminderBubbleDuration(completedToday)}`;
      if (reminder?.canPostpone) {
        const minutes = Math.max(1, Math.round(Number(reminder?.postponeMinutes) || 10));
        actions.push({ action: "rest-reminder-postpone", label: `延迟 ${minutes} 分钟`, primary: false });
      }
      actions.push({ action: "rest-reminder-start", label: "开始休息", primary: true });
    } else if (phase === "postponed") {
      const remaining = Math.max(0, (Number(reminder?.postponeEndsAtMs) - now) / 1000);
      title = "休息已延迟";
      detail = `${formatRestReminderBubbleDuration(remaining)} 后再次提醒`;
      status = `延迟不计入休息 · 今日已休息 ${formatRestReminderBubbleDuration(completedToday)}`;
      actions.push({ action: "rest-reminder-start", label: "开始休息", primary: true });
    } else if (phase === "resting") {
      const startedAt = Number(reminder?.restStartedAtMs) || now;
      const endsAt = Number(reminder?.restEndsAtMs) || now;
      const elapsed = Math.max(0, (Math.min(now, endsAt) - startedAt) / 1000);
      const target = Math.max(1, Math.round(Number(reminder?.breakMinutes) || 2)) * 60;
      title = "正在休息";
      detail = `本次已休息 ${formatRestReminderBubbleDuration(elapsed)}`;
      const today = Math.max(completedToday + elapsed, Number(reminder?.todayRestedSeconds) || 0);
      status = `目标 ${formatRestReminderBubbleDuration(target)} · 今日累计 ${formatRestReminderBubbleDuration(today)}`;
      actions.push({ action: "rest-reminder-finish", label: "提前结束", primary: true });
    } else if (phase === "completed") {
      const duration = Math.max(0, Number(reminder?.lastRestDurationSeconds) || 0);
      title = "休息完成";
      detail = `本次 ${formatRestReminderBubbleDuration(duration)} · 今日累计 ${formatRestReminderBubbleDuration(completedToday)}`;
      status = "新一轮专注已开始";
    } else if (phase === "preview") {
      title = "测试预览";
      status = "不会改变当前计时，也不会计入今日休息";
      actions.push({ action: "rest-reminder-ack", label: "关闭预览", primary: true });
    }
    return { title, detail, status, actions };
  }

  function applyRestReminderBubbleContent(bubble, reminder) {
    if (!bubble) return;
    const copy = restReminderBubbleCopy(reminder);
    const title = bubble.querySelector('[data-rest-reminder-bubble-title="true"]');
    const detail = bubble.querySelector('[data-rest-reminder-bubble-detail="true"]');
    const status = bubble.querySelector('[data-rest-reminder-bubble-status="true"]');
    const promptElapsed = bubble.querySelector('[data-rest-reminder-prompt-elapsed="true"]');
    if (title) title.textContent = copy.title;
    if (detail) detail.textContent = copy.detail;
    if (status) status.textContent = copy.status;
    if (promptElapsed) {
      promptElapsed.textContent = restReminderPromptElapsedText(reminder);
      promptElapsed.hidden = String(reminder?.phase || "") !== "prompt" || reminder?.preview === true;
    }
    const earlyActions = bubble.querySelector('[data-rest-reminder-bubble-early-actions="true"]');
    if (earlyActions) earlyActions.hidden = String(reminder?.phase || "") !== "prompt";
    const secondary = bubble.querySelector('[data-rest-reminder-bubble-secondary="true"]');
    const primary = bubble.querySelector('[data-rest-reminder-bubble-primary="true"]');
    const secondaryAction = copy.actions.find((item) => !item.primary);
    const primaryAction = copy.actions.find((item) => item.primary);
    for (const [button, item] of [[secondary, secondaryAction], [primary, primaryAction]]) {
      if (!button) continue;
      button.hidden = !item;
      if (!item) continue;
      button.textContent = item.label;
      button.dataset.action = item.action;
    }
  }

  function positionRestReminderBubble(root = document.getElementById(rootId)) {
    const bubble = root?.querySelector?.('[data-rest-reminder-bubble="true"]');
    if (!bubble || bubble.dataset.visible !== "true") return;
    const composer = composerRect();
    if (!composer) {
      bubble.dataset.positioned = "false";
      return;
    }
    const width = Math.min(430, Math.max(300, Math.min(composer.width, innerWidth - 16)));
    bubble.style.width = `${Math.round(width)}px`;
    bubble.dataset.positioned = "true";
    const height = Math.max(1, bubble.getBoundingClientRect().height || 110);
    const left = clamp(composer.right - width, 8, Math.max(8, innerWidth - width - 8));
    const top = clamp(composer.top - height - 8, 8, Math.max(8, innerHeight - height - 8));
    bubble.style.left = `${Math.round(left)}px`;
    bubble.style.top = `${Math.round(top)}px`;
  }

  function renderRestReminderBubble(root, reminder) {
    const bubble = root?.querySelector?.('[data-rest-reminder-bubble="true"]');
    if (!bubble) return false;
    const fallback = desktopOverlayDependency().installed === false;
    const visible = fallback && reminder?.bubbleVisible === true;
    bubble.dataset.visible = visible ? "true" : "false";
    if (!visible) {
      bubble.dataset.positioned = "false";
      return false;
    }
    applyRestReminderBubbleContent(bubble, reminder);
    ctx.lifecycle.frame("rest_reminder", () => positionRestReminderBubble(root));
    return true;
  }

  function syncRestReminderOverlayCountdown() {
    const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
    const bubble = document.querySelector(`#${rootId} [data-rest-reminder-bubble="true"]`);
    const toastVisible = !!toast && toast.dataset.visible === "true";
    const bubbleVisible = !!bubble && bubble.dataset.visible === "true";
    if (!toastVisible && !bubbleVisible) {
      stopRestReminderOverlayTicker();
      return;
    }
    const reminder = currentPayload()?.restReminder;
    const phase = String(reminder?.phase || "");
    const needsTicker = (
      (toastVisible && phase === "preview")
      || (bubbleVisible && (phase === "postponed" || phase === "resting"))
      || (toastVisible && phase === "prompt")
      || (bubbleVisible && phase === "prompt")
    );
    if (!needsTicker) {
      stopRestReminderOverlayTicker();
    }
    if (bubbleVisible) applyRestReminderBubbleContent(bubble, reminder);
    if (toastVisible) {
      const promptElapsed = toast.querySelector('[data-rest-reminder-prompt-elapsed="true"]');
      if (promptElapsed) {
        promptElapsed.textContent = restReminderPromptElapsedText(reminder);
        promptElapsed.hidden = phase !== "prompt" || reminder?.preview === true;
      }
      const countdown = toast.querySelector('[data-rest-reminder-break-countdown="true"]');
      const infinite = reminder?.promptWaitInfinite === true && !reminder?.preview;
      const seconds = (Number(reminder?.promptEndsAtMs) - Date.now()) / 1000;
      const remaining = formatRestReminderRemaining(Math.max(0, seconds));
      if (infinite) {
        if (countdown) countdown.textContent = "等待你的选择 · 不会自动跳过本次休息";
      } else if (!Number.isFinite(seconds) || seconds <= 0) {
        if (countdown) {
          countdown.textContent = reminder?.preview ? "正在关闭预览..." : "正在跳过本次休息...";
        }
        toast.dataset.visible = "false";
        const mask = document.querySelector(`#${rootId} [data-rest-reminder-mask="true"]`);
        if (mask) {
          mask.dataset.visible = "false";
          mask.setAttribute("aria-hidden", "true");
        }
      } else if (countdown) {
        countdown.textContent = reminder?.preview
          ? `测试预览 · ${remaining} 后自动关闭，不改动当前计时`
          : "等待你的选择 · 不会自动跳过本次休息";
      }
    }
  }

  function ensureRestReminderOverlayTicker() {
    syncRestReminderOverlayCountdown();
    const reminder = currentPayload()?.restReminder;
    const phase = String(reminder?.phase || "");
    const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
    const bubble = document.querySelector(`#${rootId} [data-rest-reminder-bubble="true"]`);
    const needsTicker = (
      (toast?.dataset.visible === "true" && phase === "preview")
      || (bubble?.dataset.visible === "true" && (phase === "postponed" || phase === "resting"))
      || (toast?.dataset.visible === "true" && phase === "prompt")
      || (bubble?.dataset.visible === "true" && phase === "prompt")
    );
    if (!needsTicker) return;
    if (!restReminderOverlayTimer) {
      restReminderOverlayTimer = ctx.lifecycle.interval(
        "rest_reminder_overlay",
        syncRestReminderOverlayCountdown,
        1000,
      );
    }
  }

  function renderRestReminderToast(root, payload) {
    const host = root || document.getElementById(rootId);
    if (!host) return;
    let toast = host.querySelector('[data-rest-reminder-toast="true"]');
    let mask = host.querySelector('[data-rest-reminder-mask="true"]');
    if (!toast || !mask) {
      host.insertAdjacentHTML("beforeend", restReminderToastMarkup());
      toast = host.querySelector('[data-rest-reminder-toast="true"]');
      mask = host.querySelector('[data-rest-reminder-mask="true"]');
    }
    if (!host.querySelector('[data-rest-reminder-bubble="true"]')) {
      const wrapper = document.createElement("div");
      wrapper.innerHTML = restReminderToastMarkup();
      const bubble = wrapper.querySelector('[data-rest-reminder-bubble="true"]');
      if (bubble) host.appendChild(bubble);
    }
    if (!toast) return;
    const reminder = payload?.restReminder && typeof payload.restReminder === "object"
      ? payload.restReminder
      : {};
    const phase = String(reminder.phase || "");
    const promptEndsAtMs = Number(reminder.promptEndsAtMs);
    const visible = !!reminder.visible
      && (!dismissedReminderKey || reminderKey(reminder) !== dismissedReminderKey)
      && (
        phase === "prompt"
          ? reminder.promptWaitInfinite === true
          : phase === "preview"
            && Number.isFinite(promptEndsAtMs)
            && promptEndsAtMs > Date.now()
      );
    const wasVisible = toast.dataset.visible === "true";
    toast.dataset.visible = visible ? "true" : "false";
    if (mask) {
      mask.dataset.visible = visible ? "true" : "false";
      mask.setAttribute("aria-hidden", visible ? "false" : "true");
    }
    const messageNode = toast.querySelector('[data-rest-reminder-message="true"]');
    if (messageNode) {
      messageNode.textContent = String(reminder.message || "站起来走走，让眼睛放松片刻。");
    }
    const kicker = toast.querySelector(".codex-usage-hud-rest-toast-kicker");
    if (kicker) {
      kicker.textContent = reminder.preview ? "预览提醒" : "专注休息";
    }
    const postponeBtn = toast.querySelector('[data-action="rest-reminder-postpone"]');
    if (postponeBtn) {
      const canPostpone = !!reminder.canPostpone;
      postponeBtn.hidden = !canPostpone;
      const minutes = Math.max(1, Math.round(Number(reminder.postponeMinutes) || 10));
      postponeBtn.textContent = `延迟 ${minutes} 分钟`;
    }
    const ackBtn = toast.querySelector('[data-action="rest-reminder-ack"], [data-action="rest-reminder-start"]');
    if (ackBtn) {
      ackBtn.dataset.action = reminder.preview ? "rest-reminder-ack" : "rest-reminder-start";
      ackBtn.textContent = reminder.preview ? "关闭预览" : "开始休息";
    }
    const earlyActions = toast.querySelector('[data-rest-reminder-early-actions="true"]');
    if (earlyActions) earlyActions.hidden = phase !== "prompt";
    const bubbleVisible = renderRestReminderBubble(host, reminder);
    if (visible || bubbleVisible) {
      ensureRestReminderOverlayTicker();
      if (!wasVisible && ackBtn) {
        ctx.lifecycle.frame("rest_reminder", () => {
          try { ackBtn.focus({ preventScroll: true }); } catch (_) { ackBtn.focus(); }
        });
      }
    } else {
      stopRestReminderOverlayTicker();
    }
  }

    let installed = false;
    let restReminderOverlayTimer = 0;

    function install() {
      if (installed) return false;
      installed = true;
      return true;
    }

    function apply(root, payload) {
      if (!installed) install();
      renderRestReminderToast(root, payload || {});
    }

    function dispose() {
      const wasInstalled = installed;
      installed = false;
      stopRestReminderOverlayTicker();
      return wasInstalled;
    }

    return {
      install,
      apply,
      dispose,
      markup: restReminderToastMarkup,
      position: positionRestReminderBubble,
      dismiss,
      restore,
    };
  }

  const restReminderDomain = ctx.domains.register(
    "rest_reminder",
    createRestReminderDomain(ctx, shared),
  );
  const {
    markup: restReminderToastMarkup,
    position: positionRestReminderBubble,
  } = restReminderDomain;
"""

__all__ = ["TEXT"]
