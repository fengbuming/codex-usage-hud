"""Renderer model-picker JavaScript domain."""

TEXT = r"""
  function createModelPickerDomain(ctx, shared) {
    const { normalize, visible, cssEscape } = shared;
    const catalogModels = Array.isArray(codexModelPickerCatalog)
      ? codexModelPickerCatalog.filter((model) => model && typeof model === "object" && model.model)
      : [];
    let models = catalogModels.slice();
    let catalogSignature = "";
    let observedTriggerParent = null;
    const ownedLabels = new Map();
    const syntheticSelect = Symbol("codexUsageHudModelPickerSelect");
    let installed = false;
    let fastButton = null;
    let fastTrigger = null;
    let fastParent = null;
    let fastPending = null;
    let fastError = "";
    let fastSessionId = "";
    const confirmedFastRequests = new Map();

    // Resolve native state on each event. Missing native capabilities do not
    // prevent an explicit Fast request through the HUD gateway.
    function nativeFastContext() {
      let fallback = null;
      for (const trigger of document.querySelectorAll(
        '[data-codex-intelligence-trigger], button[aria-haspopup], button[data-state]'
      )) {
        if (!visible(trigger) || trigger === fastButton) continue;
        const fiber = findFiber(trigger, (item) => {
          const props = item?.memoizedProps;
          return Array.isArray(props?.serviceTierOptions)
            && Object.hasOwn(props, "selectedServiceTier");
        }, 120);
        if (!fiber) {
          if (trigger.hasAttribute("data-codex-intelligence-trigger")) fallback = { trigger };
          continue;
        }
        const props = fiber.memoizedProps;
        const options = props.serviceTierOptions;
        const fast = options.find((option) => option && typeof option.value === "string" && (
          option.iconKind === "fast" || ["fast", "priority"].includes(option.value)
        ));
        const standard = options.find((option) => option && Object.hasOwn(option, "value")
          && (option.value === null || option.value === "default"));
        return { trigger, props, fast, standard };
      }
      return fallback;
    }

    function removeNativeFastToggle() {
      fastButton?.removeEventListener("click", toggleNativeFast);
      fastButton?.remove();
      fastButton = null;
      fastTrigger = null;
      fastParent = null;
      fastPending = null;
      fastError = "";
      ctx.observers?.clear?.("native_fast");
    }

    function nativeFastAvailable(context) {
      return !!(context?.fast && context?.standard && !context.fast.disabled
        && typeof context.props.onSelectServiceTier === "function"
        && !context.standard.disabled && !context.props.disabled
        && !context.props.serviceTierOptionsLoading && !context.trigger.disabled
        && context.trigger.getAttribute("aria-disabled") !== "true");
    }

    function fastRequestSessionId() {
      const selection = selectionContext();
      // null identifies the new-chat composer; never substitute a stale
      // payload's previous conversation for that explicit native selection.
      if (selection) return String(selection.conversationId || "");
      return String(ctx.state?.payload?.()?.sessionId || "");
    }

    function fastRequestPreference(sessionId) {
      const preferences = ctx.state?.payload?.()?.settings?.fast_request_preferences || {};
      if (preferences[sessionId] === confirmedFastRequests.get(sessionId)) confirmedFastRequests.delete(sessionId);
      return confirmedFastRequests.get(sessionId) ?? preferences[sessionId];
    }

    async function toggleNativeFast(event) {
      event.preventDefault();
      event.stopPropagation();
      if (fastPending || ctx.state?.payload?.()?.settings?.show_native_fast_toggle !== true) return;
      const context = nativeFastContext();
      const sessionId = fastRequestSessionId();
      const trigger = context?.trigger || selectionContext()?.trigger;
      // A click queued before navigation must never select a tier in a new chat.
      if (trigger !== fastTrigger || sessionId !== fastSessionId) return;
      const operation = {};
      fastPending = operation;
      fastError = "";
      const preference = fastRequestPreference(sessionId);
      const selected = context?.props?.selectedServiceTier;
      const enabled = preference ? preference === "fast" : !!context?.fast && selected === context.fast.value;
      const useNative = nativeFastAvailable(context) && !preference;
      const gatewayActive = ctx.state?.payload?.()?.settings?.provider_gateway?.active === true;
      patchNativeFastToggle();
      try {
        if (useNative && !gatewayActive) {
          const target = enabled ? context.standard.value : context.fast.value;
          if (await context.props.onSelectServiceTier(target) === false) throw new Error("Codex 未接受 Fast 设置，请开启 HUD 请求代理后重试");
        } else {
          const result = await settingsShellDomain.codexGatewayCommand({
            action: "fastRequestSet", sessionId, enabled: !enabled,
          });
          if (result.sessionId !== sessionId || result.enabled !== !enabled) throw new Error("Fast 设置未得到确认，请重试");
          confirmedFastRequests.set(sessionId, result.serviceTier);
          if (fastPending === operation && result.gateway !== true) {
            fastError = "偏好已保存；开启 HUD 请求代理后附加速度参数";
          }
        }
      } catch (error) {
        if (fastPending === operation) fastError = String(error?.message || "切换失败，请重试");
      } finally {
        if (fastPending === operation) fastPending = null;
        if (installed) schedulePatch();
      }
    }

    function patchNativeFastToggle() {
      if (ctx.state?.payload?.()?.settings?.show_native_fast_toggle !== true) {
        removeNativeFastToggle();
        return;
      }
      const context = nativeFastContext();
      const trigger = context?.trigger || selectionContext()?.trigger;
      const parent = trigger?.parentElement;
      if (!parent) { removeNativeFastToggle(); return; }
      const sessionId = fastRequestSessionId();
      if (fastTrigger !== trigger || fastParent !== parent || fastSessionId !== sessionId || !fastButton?.isConnected) {
        removeNativeFastToggle();
        fastTrigger = trigger;
        fastParent = parent;
        fastSessionId = sessionId;
        fastButton = document.createElement("button");
        fastButton.type = "button";
        fastButton.setAttribute("data-codex-usage-hud-fast-toggle", "true");
        fastButton.setAttribute("role", "switch");
        fastButton.addEventListener("click", toggleNativeFast);
        parent.appendChild(fastButton);
        if (typeof MutationObserver === "function") {
          const observer = new MutationObserver((records) => {
            if (records.some((record) => record.target !== fastButton
              && !fastButton?.contains(record.target))) schedulePatch();
          });
          observer.observe(parent, { childList: true, subtree: true, characterData: true,
            attributes: true, attributeFilter: ["data-fast-mode-enabled", "disabled", "aria-disabled"] });
          ctx.observers?.set?.("native_fast", observer);
        }
      }
      const preference = fastRequestPreference(sessionId);
      const requestMode = !!preference || !nativeFastAvailable(context)
        || ctx.state?.payload?.()?.settings?.provider_gateway?.active === true;
      const selected = context?.props?.selectedServiceTier;
      const enabled = preference ? preference === "fast" : !!context?.fast && selected === context.fast.value;
      const ultrafast = selected === "ultrafast"
        || context?.props?.serviceTierOptions.some((option) => option.value === selected && option.iconKind === "ultrafast");
      const text = !preference && ultrafast ? "Fast · Ultrafast" : "ϟ Fast";
      if (fastButton.textContent !== text) fastButton.textContent = text;
      const checked = String(enabled);
      if (fastButton.getAttribute("aria-checked") !== checked) fastButton.setAttribute("aria-checked", checked);
      const disabled = !!fastPending;
      if (fastButton.disabled !== disabled) fastButton.disabled = disabled;
      fastButton.setAttribute("aria-label", enabled ? "关闭 Fast" : ultrafast && !preference ? "切换为 Fast" : "启用 Fast");
      fastButton.title = fastError || (requestMode
        ? `${sessionId ? "当前聊天" : "新聊天默认"}：${enabled ? "请求 Fast" : "标准速度"}。由 HUD 请求代理附加速度参数，上游决定实际档位和计费。`
        : ultrafast ? "当前为 Ultrafast；点击切换为 Fast，用量由 Codex 原生规则决定"
        : enabled ? "关闭 Fast，切回标准速度" : "启用原生 Fast，速度更快，用量更高");
    }

    function imageDraftModel(model) {
      const match = /^hud-image\/([0-9a-f]{32})\/(.+)$/.exec(String(model || ""));
      return match ? { token: match[1], model: match[2] } : null;
    }

    function imageModelLabel(model) {
      return imageDraftModel(model)?.model || String(model || "");
    }

    function reactFiberForNode(node) {
      if (!node) return null;
      const key = Object.getOwnPropertyNames(node).find((name) => name.startsWith("__reactFiber$"));
      return key ? node[key] : null;
    }

    function findFiber(node, predicate, limit = 36) {
      let fiber = reactFiberForNode(node);
      for (let depth = 0; fiber && depth < limit; depth += 1, fiber = fiber.return) {
        try {
          let root = fiber;
          for (let count = 0; root.return && count < 300; count += 1) root = root.return;
          const committed = root.stateNode?.current && root.stateNode.current !== root
            ? fiber.alternate || fiber : fiber;
          if (predicate(committed)) return committed;
        } catch (_) {}
      }
      return null;
    }

    function reasoningEffortLabel(value) {
      switch (String(value || "")) {
        case "minimal": return "极简";
        case "low": return "轻度";
        case "medium": return "中";
        case "high": return "高";
        case "xhigh": return "极高";
        case "max": return "最大";
        case "ultra": return "Ultra";
        default: return String(value || "");
      }
    }

    function normalizeReasoningEfforts(model) {
      const raw = Array.isArray(model?.supportedReasoningEfforts) ? model.supportedReasoningEfforts : [];
      return raw
        .map((item) => {
          const reasoningEffort = String(item?.reasoningEffort || "").trim();
          if (!reasoningEffort) return null;
          return {
            reasoningEffort,
            description: String(item?.description || reasoningEffortLabel(reasoningEffort)),
          };
        })
        .filter(Boolean);
    }

    function modelOptionFromCatalog(model, prototypeOption = null, forcedReasoningEffort = "") {
      const efforts = normalizeReasoningEfforts(model);
      const fallbackEffort = String(model.defaultReasoningEffort || efforts[0]?.reasoningEffort || "medium");
      const selectedEfforts = forcedReasoningEffort
        ? [{ reasoningEffort: forcedReasoningEffort, description: reasoningEffortLabel(forcedReasoningEffort) }]
        : efforts;
      return {
        id: String(model.model),
        model: String(model.model),
        upgrade: null,
        upgradeInfo: null,
        availabilityNux: null,
        displayName: String(model.displayName || model.model),
        description: String(model.description || ""),
        hidden: false,
        supportedReasoningEfforts: selectedEfforts.length ? selectedEfforts : [{ reasoningEffort: fallbackEffort, description: reasoningEffortLabel(fallbackEffort) }],
        defaultReasoningEffort: forcedReasoningEffort || fallbackEffort,
        inputModalities: Array.isArray(model.inputModalities) && model.inputModalities.length
          ? model.inputModalities.map(String)
          : (Array.isArray(prototypeOption?.inputModalities) ? prototypeOption.inputModalities : ["text"]),
        supportsPersonality: prototypeOption?.supportsPersonality ?? true,
        additionalSpeedTiers: Array.isArray(prototypeOption?.additionalSpeedTiers) ? prototypeOption.additionalSpeedTiers : [],
        serviceTiers: Array.isArray(prototypeOption?.serviceTiers) ? prototypeOption.serviceTiers : [],
        defaultServiceTier: prototypeOption?.defaultServiceTier ?? null,
        isDefault: false,
      };
    }

    function modelPickerLeafFiber(node) {
      return findFiber(node, (fiber) => !!fiber?.memoizedProps?.modelOption);
    }

    function modelPickerMenuItems() {
      return Array.from(document.querySelectorAll('[role="menuitem"], [role="menuitemradio"]'))
        .filter(visible);
    }

    function modelPickerModernProps(node) {
      return findFiber(node, (fiber) => {
        const props = fiber?.memoizedProps;
        return Array.isArray(props?.models)
          && Array.isArray(props?.modelOptions)
          && typeof props?.onSelectModel === "function";
      }, 120)?.memoizedProps || null;
    }

    function selectionContext() {
      for (const trigger of Array.from(document.querySelectorAll("button[aria-haspopup], button[data-state]"))) {
        if (!visible(trigger)) continue;
        const props = modelPickerModernProps(trigger);
        if (!props) continue;
        const owner = findFiber(trigger, (fiber) => Object.hasOwn(fiber?.memoizedProps || {}, "conversationId"), 120);
        return { trigger, props, conversationId: owner?.memoizedProps?.conversationId,
          selectedModel: String(props.model || ""), reasoningEffort: String(props.reasoningEffort || ""),
          nativeModels: props.models };
      }
      return null;
    }

    function refreshProviderModels(selection) {
      const payload = ctx.state?.payload?.() || {};
      const settings = payload.settings || {};
      const provider = String(payload.activeSessionProvider || settings.default_provider || settings.app_provider || "").toLowerCase();
      const entry = settings.provider_registry?.[provider] || {};
      const preferred = Array.isArray(entry.desktopModels)
        ? entry.desktopModels.map((model) => String(model || "").trim()).filter(Boolean)
        : [String(entry.desktopModel || "").trim()].filter(Boolean);
      const current = selection && !imageDraftModel(selection.selectedModel) && !selection.nativeModels.some((model) => model.model === selection.selectedModel)
        ? selection.selectedModel : "";
      const signature = JSON.stringify([provider, preferred, current, selection?.reasoningEffort || ""]);
      if (signature === catalogSignature) return;
      catalogSignature = signature;
      removeSyntheticItems();
      models = catalogModels.slice();
      for (const model of [...preferred, current]) {
        if (!model || models.some((item) => item.model === model)) continue;
        const effort = model === current ? selection.reasoningEffort || "low" : "low";
        models.push({ model, displayName: model, defaultReasoningEffort: effort,
          supportedReasoningEfforts: [{ reasoningEffort: effort }], inputModalities: ["text"] });
      }
    }

    function restoreModelLabel(node, state) {
      if (node.textContent === state.applied) node.textContent = state.original;
      if (node.getAttribute("title") === state.applied) {
        if (state.title === null) node.removeAttribute("title");
        else node.setAttribute("title", state.title);
      }
      ownedLabels.delete(node);
    }

    function patchModelLabel(selection) {
      const custom = selection?.selectedModel && !selection.nativeModels.some((model) => model.model === selection.selectedModel);
      const label = selection?.trigger?.querySelector?.("span.truncate.whitespace-nowrap");
      for (const [node, state] of ownedLabels) {
        if (node !== label || !custom) restoreModelLabel(node, state);
      }
      if (custom && label) {
        const state = ownedLabels.get(label) || { original: label.textContent, title: label.getAttribute("title") };
        state.applied = imageModelLabel(selection.selectedModel);
        ownedLabels.set(label, state);
        if (label.textContent !== state.applied) label.textContent = state.applied;
        if (label.getAttribute("title") !== state.applied) label.setAttribute("title", state.applied);
      }
      const parent = selection?.trigger?.parentElement || null;
      if (parent !== observedTriggerParent) {
        observedTriggerParent = parent;
        ctx.observers?.clear?.("model_picker_trigger");
        if (parent && typeof MutationObserver === "function") {
          const observer = new MutationObserver(schedulePatch);
          observer.observe(parent, { childList: true, subtree: true, characterData: true });
          ctx.observers?.set?.("model_picker_trigger", observer);
        }
      }
    }

    function modelPickerLabelKey(value) {
      return normalize(value)
        .toLowerCase()
        .replace(/^gpt[\s-]*/, "")
        .replace(/[^a-z0-9]+/g, "");
    }

    function modelPickerNodeMatchesModel(node, model) {
      const nodeKey = modelPickerLabelKey(node?.textContent);
      const modelKeys = [model?.displayName, model?.model, model?.id]
        .map(modelPickerLabelKey)
        .filter(Boolean);
      return !!nodeKey && modelKeys.some((key) => nodeKey === key || nodeKey.startsWith(key));
    }

    function modelPickerContext() {
      const menuItems = modelPickerMenuItems();
      const legacyItems = menuItems
        .filter(visible)
        .map((node) => ({ node, fiber: modelPickerLeafFiber(node) }))
        .filter((item) => !!item.fiber?.memoizedProps?.modelOption);
      if (legacyItems.length) {
        const props = legacyItems[0].fiber.memoizedProps || {};
        return {
          items: legacyItems.map((item) => item.node),
          nativeModels: legacyItems.map((item) => item.fiber.memoizedProps.modelOption),
          prototypeOption: props.modelOption || null,
          selectedModel: String(props.selectedModel || ""),
          selectModel: props.onSelect,
        };
      }

      const modernProps = menuItems
        .map(modelPickerModernProps)
        .find(Boolean);
      if (!modernProps) return null;
      const nativeModels = modernProps.models.filter((model) => model && typeof model === "object");
      const items = menuItems.filter((node) => (
        node.getAttribute("role") === "menuitemradio"
        && nativeModels.some((model) => modelPickerNodeMatchesModel(node, model))
      ));
      if (!items.length) return null;
      return {
        items,
        nativeModels,
        prototypeOption: nativeModels[0] || null,
        selectedModel: String(modernProps.model || ""),
        selectModel(option) {
          if (!option?.model) return;
          if (
            typeof modernProps.onBeforeSelectModel === "function"
            && modernProps.onBeforeSelectModel(option.model) === false
          ) return;
          modernProps.onSelectModel(option.model, option.defaultReasoningEffort || "medium");
          modernProps.onSelectComplete?.();
        },
      };
    }

    function selectedCatalogModelFromMenu() {
      const selection = window[modelPickerSelectionName];
      const selectedModel = String(selection?.model || "");
      return models.find((model) => model.model === selectedModel) || null;
    }

    function insertSyntheticModelItem(container, referenceNode, model, modelProps) {
      if (!container || !referenceNode || !modelProps?.onSelect) return;
      if (container.querySelector(`[data-codex-usage-hud-model-option="${cssEscape(model.model)}"]`)) return;
      const prototypeOption = modelProps.modelOption || null;
      const option = modelOptionFromCatalog(model, prototypeOption);
      const node = referenceNode.cloneNode(true);
      node.textContent = option.displayName;
      node.title = option.description || option.displayName;
      node.setAttribute("role", referenceNode.getAttribute("role") || "menuitem");
      node.setAttribute("tabindex", "-1");
      node.setAttribute("data-codex-usage-hud-model-option", option.model);
      node.removeAttribute("data-model-selected");
      node.removeAttribute("aria-describedby");
      if (node.getAttribute("role") === "menuitemradio") {
        node.setAttribute("aria-checked", String(modelProps.selectedModel === option.model));
      } else {
        node.removeAttribute("aria-checked");
      }
      if (modelProps.selectedModel === option.model) node.setAttribute("data-model-selected", "true");
      node[syntheticSelect] = (event) => {
        event.preventDefault();
        event.stopPropagation();
        window[modelPickerSelectionName] = {
          model: option.model,
          option,
          selectModel: modelProps.onSelect,
          serviceTier: option.defaultServiceTier ?? null,
        };
        modelProps.onSelect(option, option.defaultServiceTier ?? null);
        schedulePatch();
      };
      container.insertBefore(node, referenceNode);
    }

    function insertSyntheticReasoningItem(container, referenceNode, model, effort, { modern = false } = {}) {
      const selection = window[modelPickerSelectionName];
      if (!container || !referenceNode || typeof selection?.selectModel !== "function") return;
      if (container.querySelector(`[data-codex-usage-hud-reasoning-option="${cssEscape(effort.reasoningEffort)}"]`)) return;
      const node = referenceNode.cloneNode(true);
      const label = reasoningEffortLabel(effort.reasoningEffort);
      node.textContent = label;
      node.title = effort.description || label;
      node.setAttribute("role", modern ? "menuitemradio" : "menuitem");
      node.setAttribute("tabindex", "-1");
      node.setAttribute("data-codex-usage-hud-reasoning-option", effort.reasoningEffort);
      node.removeAttribute("data-reasoning-selected");
      node.removeAttribute("data-reasoning-slider");
      node.removeAttribute("aria-describedby");
      node.removeAttribute("aria-keyshortcuts");
      if (modern) {
        node.setAttribute("aria-checked", String(selection?.option?.defaultReasoningEffort === effort.reasoningEffort));
      } else {
        node.removeAttribute("aria-checked");
      }
      node[syntheticSelect] = (event) => {
        event.preventDefault();
        event.stopPropagation();
        const option = modelOptionFromCatalog(model, selection.option, effort.reasoningEffort);
        window[modelPickerSelectionName] = {
          model: option.model,
          option,
          selectModel: selection.selectModel,
          serviceTier: option.defaultServiceTier ?? null,
        };
        selection.selectModel(option, option.defaultServiceTier ?? null);
        schedulePatch();
      };
      container.appendChild(node);
    }

    function handleSyntheticSelection(event) {
      if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
      const node = event.target?.closest?.(
        "[data-codex-usage-hud-model-option], [data-codex-usage-hud-reasoning-option]"
      );
      const select = node?.[syntheticSelect];
      if (typeof select === "function") select(event);
    }

    function removeSyntheticItems() {
      document.querySelectorAll(
        "[data-codex-usage-hud-model-option], [data-codex-usage-hud-reasoning-option]"
      ).forEach((node) => node.remove());
    }

    function patchCodexModelPicker() {
      patchNativeFastToggle();
      const selection = selectionContext();
      refreshProviderModels(selection);
      patchModelLabel(selection);
      const context = modelPickerContext();
      if (!models.length) return;
      if (context?.items?.length && typeof context.selectModel === "function") {
        const first = context.items[0];
        const container = first.parentElement;
        const existing = new Set(context.nativeModels.map((item) => String(item?.model || item?.id || "")));
        const modelProps = {
          modelOption: context.prototypeOption,
          selectedModel: imageModelLabel(context.selectedModel),
          onSelect: context.selectModel,
        };
        for (const model of models) {
          if (!existing.has(String(model.model))) {
            insertSyntheticModelItem(container, first, model, modelProps);
          }
        }
      }
      const selectedModel = selectedCatalogModelFromMenu();
      if (!selectedModel) return;
      const reasoningItems = modelPickerMenuItems()
        .filter((node) => node.hasAttribute("data-reasoning-selected") || ["轻度", "中", "高", "极高"].includes(normalize(node.textContent)));
      const modernSlider = document.querySelector('[data-reasoning-slider="true"]');
      const referenceNode = reasoningItems[0] || (visible(modernSlider) ? modernSlider : null);
      if (!referenceNode) return;
      const existingLabels = new Set(reasoningItems.map((node) => normalize(node.textContent)));
      const container = referenceNode.parentElement;
      for (const effort of normalizeReasoningEfforts(selectedModel)) {
        if (!existingLabels.has(reasoningEffortLabel(effort.reasoningEffort))) {
          insertSyntheticReasoningItem(container, referenceNode, selectedModel, effort, {
            modern: !reasoningItems.length,
          });
        }
      }
    }

    function schedulePatch(event = null) {
      if (!installed) return;
      if (event?.type && !fastButton && !ctx.state?.payload?.()?.settings?.show_native_fast_toggle
        && !models.length && !ownedLabels.size && observedTriggerParent
        && !observedTriggerParent.contains?.(event.target)) return;
      ctx.frames.cancel("model_picker");
      for (const timer of (window[modelPickerPatchTimersName] || [])) {
        ctx.lifecycle.clearTimeout(timer);
      }
      window[modelPickerPatchRafName] = ctx.frames.schedule("model_picker", patchCodexModelPicker);
      window[modelPickerPatchTimersName] = [60, 180, 360].map((delay) => (
        ctx.lifecycle.timeout("model_picker", patchCodexModelPicker, delay)
      ));
    }

    function install() {
      if (installed) return false;
      installed = true;
      window[modelPickerPatchHandlerName] = schedulePatch;
      ctx.lifecycle.listen("model_picker", document, "pointerdown", schedulePatch, true);
      ctx.lifecycle.listen("model_picker", document, "pointerover", schedulePatch, true);
      ctx.lifecycle.listen("model_picker", document, "focusin", schedulePatch, true);
      ctx.lifecycle.listen("model_picker", document, "keydown", schedulePatch, true);
      ctx.lifecycle.listen("model_picker", document, "click", handleSyntheticSelection);
      ctx.lifecycle.listen("model_picker", document, "keydown", handleSyntheticSelection);
      return true;
    }

    function apply() {
      schedulePatch();
    }

    function dispose() {
      installed = false;
      removeNativeFastToggle();
      ctx.frames.cancel("model_picker");
      for (const timer of (window[modelPickerPatchTimersName] || [])) {
        ctx.lifecycle.clearTimeout(timer);
      }
      removeSyntheticItems();
      for (const [node, state] of ownedLabels) restoreModelLabel(node, state);
      ctx.observers?.clear?.("model_picker_trigger");
      observedTriggerParent = null;
      delete window[modelPickerPatchHandlerName];
      delete window[modelPickerPatchRafName];
      delete window[modelPickerPatchTimersName];
      delete window[modelPickerSelectionName];
    }

    return { install, apply, dispose, selectionContext };
  }

  const modelPickerDomain = ctx.domains.register(
    "model_picker",
    createModelPickerDomain(ctx, shared),
  );
"""

__all__ = ["TEXT"]
