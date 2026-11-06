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
    const previousTextModels = new Map();
    const imagePending = new Set();
    const imageErrors = new Map();
    const imagePanels = new Map();
    const imageProviders = new Map();
    const imageContextPending = new Set();
    const imageContextErrors = new Set();
    let installed = false;

    function isImageModel(model) {
      return /^(?:gpt-image-[\w.-]+|gemini-[\w.-]*image[\w.-]*|gemini-nano-banana[\w.-]*)$/i.test(String(model || ""));
    }

    function imageDraftModel(model) {
      const match = /^hud-image\/([0-9a-f]{32})\/(.+)$/.exec(String(model || ""));
      return match ? { token: match[1], model: match[2] } : null;
    }

    function imageModelLabel(model) {
      return imageDraftModel(model)?.model || String(model || "");
    }

    function imageContext(selection) {
      const payload = ctx.state?.payload?.() || {};
      const settings = payload.settings || {};
      const gateway = settings.provider_gateway || {};
      // Bind to the live composer, never a stale HUD snapshot from another tab.
      const raw = String(selection?.conversationId || "").replace(/^local:/, "");
      const sessionId = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(raw) ? raw.toLowerCase() : "";
      const draft = !!selection && !raw;
      const alias = imageDraftModel(selection?.selectedModel);
      const provider = String(draft ? gateway.default_provider || settings.default_provider || ""
        : sessionId && sessionId === payload.sessionId && !payload.cachedPreview
        ? payload.activeSessionProvider : imageProviders.get(sessionId) || "").toLowerCase();
      const entry = settings.provider_registry?.[provider] || {};
      const imageModels = (Array.isArray(entry.desktopModels) ? entry.desktopModels : [entry.desktopModel]).filter(isImageModel);
      const saved = gateway.image_sessions?.[sessionId];
      const enabled = !!alias || (!!saved && saved.provider === provider);
      const selected = imageModelLabel(selection?.selectedModel);
      const model = isImageModel(selected) && imageModels.includes(selected)
        ? selected : saved?.model || imageModels[0] || "";
      return { payload, gateway, sessionId, provider, imageModels, enabled, model, draft, alias,
        key: sessionId || (draft ? "draft" : ""), loading: !!sessionId && !provider };
    }

    async function resolveImageContext(state, retry = false) {
      if (!state.gateway.active || !state.loading || imageContextPending.has(state.sessionId)
        || (!retry && imageContextErrors.has(state.sessionId))) return;
      const command = ctx.domains?.get?.("settings_shell")?.codexGatewayCommand;
      if (typeof command !== "function") return;
      imageContextPending.add(state.sessionId);
      imageContextErrors.delete(state.sessionId);
      imageErrors.delete(state.sessionId);
      try {
        const result = await command({ action: "imageGenerationContext", sessionId: state.sessionId });
        if (installed && result.sessionId === state.sessionId && result.provider) imageProviders.set(state.sessionId, result.provider);
      } catch (error) {
        if (installed) {
          imageContextErrors.add(state.sessionId);
          imageErrors.set(state.sessionId, String(error?.message || "无法确认当前会话，请重试。"));
        }
      } finally {
        imageContextPending.delete(state.sessionId);
        schedulePatch();
      }
    }

    async function toggleImageGeneration(event) {
      event.preventDefault();
      event.stopPropagation();
      const selection = selectionContext();
      const state = imageContext(selection);
      if (state.loading) { resolveImageContext(state, true); return; }
      if (!state.gateway.active || !state.key || (!state.enabled && !state.model) || imagePending.has(state.key)) return;
      const command = ctx.domains?.get?.("settings_shell")?.codexGatewayCommand;
      if (typeof command !== "function") return;
      imagePending.add(state.key);
      imageErrors.delete(state.key);
      schedulePatch();
      try {
        const result = await command(state.draft
          ? { action: "imageGenerationDraft", enabled: !state.enabled, model: state.model, token: state.alias?.token || "" }
          : { action: "imageGenerationSet", sessionId: state.sessionId, enabled: !state.enabled, model: state.model });
        if (!installed) return;
        const payload = ctx.state?.payload?.() || {};
        const gateway = payload.settings?.provider_gateway;
        if (gateway && !result.draft) {
          gateway.image_sessions = { ...(gateway.image_sessions || {}) };
          if (result.enabled) gateway.image_sessions[result.sessionId] = { provider: result.provider, model: result.model };
          else delete gateway.image_sessions[result.sessionId];
        }
        // An acknowledgement for chat A must never change chat B's native model.
        const live = selectionContext();
        const liveState = imageContext(live);
        if (liveState.key === state.key && live?.trigger === selection?.trigger) {
          if (result.enabled) {
            if (!isImageModel(imageModelLabel(selection.selectedModel))) previousTextModels.set(state.key,
              { model: selection.selectedModel, effort: selection.reasoningEffort || "low" });
            live.props.onSelectModel(result.nativeModel || result.model, "low");
          } else if (isImageModel(imageModelLabel(live.selectedModel))) {
            const previous = previousTextModels.get(state.key);
            const fallback = previous?.model || live.nativeModels.find((item) => !isImageModel(item.model))?.model;
            if (fallback) live.props.onSelectModel(fallback, previous?.effort || "low");
          }
        }
      } catch (error) {
        if (installed) imageErrors.set(state.key, String(error?.message || "生图模式切换失败，请重试。"));
      } finally {
        imagePending.delete(state.key);
        schedulePatch();
      }
    }

    function patchImageToggle(selection) {
      for (const [panel] of imagePanels) {
        if (!panel.isConnected) imagePanels.delete(panel);
      }
      const slider = document.querySelector('[data-reasoning-slider="true"]');
      if (!visible(slider)) return;
      const container = slider.closest?.('[role="menu"], [role="dialog"]') || slider.parentElement;
      if (!container?.appendChild || !container.style || typeof document.createElement !== "function") return;
      const state = imageContext(selection);
      if (state.loading) resolveImageContext(state);
      // Once the first request has bound the actual UUID, drop the temporary
      // model alias so later new chats do not inherit a consumed draft token.
      if (state.sessionId && state.alias && state.gateway.image_sessions?.[state.sessionId]) {
        if (previousTextModels.has("draft")) previousTextModels.set(state.sessionId, previousTextModels.get("draft"));
        previousTextModels.delete("draft");
        selection.props.onSelectModel(state.alias.model, "low");
      }
      const busy = imagePending.has(state.key);
      const disabled = busy || state.loading || !state.gateway.active || !state.key || (!state.enabled && !state.model);
      let row = container.querySelector('[data-codex-usage-hud-image-toggle="true"]');
      if (!row) {
        row = document.createElement("button");
        row.type = "button";
        row.innerHTML = '<span>生图</span><span data-image-mode-track aria-hidden="true" style="display:inline-flex;align-items:center;width:18px;height:11px;border-radius:8px;background:color-mix(in srgb,currentColor 22%,transparent);padding:2px;box-sizing:border-box"><span data-image-mode-thumb style="width:7px;height:7px;border-radius:50%;background:currentColor;transition:transform .12s"></span></span>';
        row.setAttribute("data-codex-usage-hud-image-toggle", "true");
        row.setAttribute("role", "switch");
        row.setAttribute("aria-label", "生图，仅当前会话");
        row.style.cssText = "position:absolute;left:10px;top:7px;z-index:1;display:inline-flex;align-items:center;gap:4px;height:20px;padding:0 2px;border:0;border-radius:4px;background:transparent;color:inherit;font:inherit;font-size:11px;cursor:pointer";
        if (getComputedStyle(container).position === "static") {
          imagePanels.set(container, container.style.position);
          container.style.position = "relative";
        }
        row[syntheticSelect] = toggleImageGeneration;
        container.appendChild(row);
      }
      const error = imageErrors.get(state.key);
      const detail = busy ? "保存中…" : !state.gateway.active ? "请先开启请求代理" : !state.key
        ? "等待会话就绪" : error || (state.loading ? "正在确认当前会话供应商…"
          : !state.model ? "当前会话供应商未配置图片模型" : state.enabled ? state.model : "仅当前会话");
      const signature = JSON.stringify([state.enabled, disabled, detail]);
      if (row.getAttribute("data-image-signature") === signature) return;
      row.setAttribute("data-image-signature", signature);
      row.setAttribute("aria-checked", String(state.enabled));
      row.setAttribute("aria-disabled", String(disabled));
      row.title = detail + "。" + (state.enabled ? "关闭后恢复对话；生成的图片保存在本机。" : "开启后，将当前会话的下一条文字消息用于生图，每次生成一张图片。");
      row.style.opacity = disabled ? ".55" : "1";
      row.style.cursor = disabled ? "not-allowed" : "pointer";
      row.querySelector("[data-image-mode-thumb]").style.transform = state.enabled ? "translateX(7px)" : "translateX(0)";
      row.querySelector("[data-image-mode-track]").style.background = state.enabled
        ? "var(--color-token-button-primary-background, #008ddd)" : "color-mix(in srgb,currentColor 22%,transparent)";
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

    function handleImageToggleSelection(event) {
      if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
      const node = event.target?.closest?.('[data-codex-usage-hud-image-toggle="true"]');
      if (node?.[syntheticSelect]) toggleImageGeneration(event);
    }

    function removeSyntheticItems() {
      document.querySelectorAll(
        "[data-codex-usage-hud-model-option], [data-codex-usage-hud-reasoning-option]"
      ).forEach((node) => node.remove());
    }

    function patchCodexModelPicker() {
      const selection = selectionContext();
      refreshProviderModels(selection);
      patchModelLabel(selection);
      const context = modelPickerContext();
      patchImageToggle(selection);
      if (!models.length) return;
      if (context?.items?.length && typeof context.selectModel === "function") {
        const first = context.items[0];
        const container = first.parentElement;
        const existing = new Set(context.nativeModels.map((item) => String(item?.model || item?.id || "")));
        const modelProps = {
          modelOption: context.prototypeOption,
          selectedModel: imageModelLabel(context.selectedModel),
          onSelect(option, tier) {
            const alias = imageDraftModel(selection?.selectedModel);
            context.selectModel(alias && isImageModel(option.model)
              ? { ...option, model: `hud-image/${alias.token}/${option.model}` } : option, tier);
          },
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
      if (event?.type && !models.length && !ownedLabels.size && observedTriggerParent
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
      // Native popup portals stop bubbling at their React root. Observe the
      // owned switch in capture, before native menu handlers can swallow it.
      ctx.lifecycle.listen("model_picker", document, "click", handleImageToggleSelection, true);
      ctx.lifecycle.listen("model_picker", document, "keydown", handleImageToggleSelection, true);
      ctx.lifecycle.listen("model_picker", document, "click", handleSyntheticSelection);
      ctx.lifecycle.listen("model_picker", document, "keydown", handleSyntheticSelection);
      return true;
    }

    function apply() {
      schedulePatch();
    }

    function dispose() {
      installed = false;
      ctx.frames.cancel("model_picker");
      for (const timer of (window[modelPickerPatchTimersName] || [])) {
        ctx.lifecycle.clearTimeout(timer);
      }
      removeSyntheticItems();
      document.querySelectorAll('[data-codex-usage-hud-image-toggle="true"]').forEach((node) => node.remove());
      previousTextModels.clear();
      imageErrors.clear();
      imageProviders.clear();
      imageContextErrors.clear();
      for (const [panel, original] of imagePanels) {
        if (panel.style.position === "relative") panel.style.position = original;
      }
      imagePanels.clear();
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
