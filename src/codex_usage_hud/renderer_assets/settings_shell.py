"""Renderer settings shell domain asset."""

from .settings_support_panels import TEXT as SETTINGS_SUPPORT_PANELS

_TEXT_PREFIX = r"""
  function createSettingsShellDomain(ctx, shared) {
      const pricingWorkflowState = {
        pendingSettings: null,
        pendingSettingsBase: null,
        pendingApplyAll: false,
        importPayload: null,
        importSourcePayload: null,
        importPreview: null,
        handledArtifacts: new Map(),
        providerDeleteRequestId: "",
        providerDeleteProvider: "",
        providerDeleteHasSessionHistory: false,
        providerModelsFetching: false,
      };
      const codexProviderDrafts = new Map();
      const codexProviderDirty = new Set();
      let autoSavedSettingsSignature = "";
      const codexCliState = {
        open: false,
        provider: "",
        requestId: "",
        models: [],
        modelsFetching: false,
        modelsFetched: false,
        modelsRequestId: "",
        reopenModelPickerAfterFetch: false,
        modelsError: "",
        chatTestOk: false,
        options: null,
        terminalId: "",
        useProxy: false,
        proxyPort: "7897",
        permission: "full",
        resume: false,
        workdir: "",
        workdirCustom: false,
        noProject: false,
        migratedWorkdirs: false,
        model: "",
        commandEdited: false,
        commandText: "",
        origin: "",
        quickLaunchPhase: "",
        quickLaunchMessage: "",
        quickLaunchDismissed: false,
        launchRequestId: "",
        launchCancelRequestId: "",
        quickLaunchFrameId: 0,
        launchSubmitFrameId: 0,
        launchTimeoutTimerId: 0,
        launchMinVisibleTimerId: 0,
        launchStartedAt: 0,
        pendingLaunchState: null,
        requestedWorkdir: null,
      };
      const codexCliLaunchMinVisibleMs = 240;
      const codexCliLaunchTimeoutMs = 15000;
      const codexCliQuickLaunchMenuState = {
        open: false,
        toggle: null,
        surface: null,
        providers: [],
        workdirProvider: "",
        workdirToggle: null,
        workdirSurface: null,
        workdirExpanded: false,
      };

      function codexCliLaunchStateKey(optionsValue = codexCliState.options || {}, providerValue = codexCliState.provider) {
        const options = optionsValue || {};
        const profile = String(options.profile || "").trim();
        const provider = String(options.provider || providerValue || "").trim().toLowerCase();
        return "profile:" + (profile || "default") + "|provider:" + (provider || "default");
      }

      function codexCliStoredLaunchStates() {
        try {
          const states = JSON.parse(ctx.storage.read(localStorage, codexCliLaunchStorageKey, "{}"));
          return states && typeof states === "object" && !Array.isArray(states) ? states : {};
        } catch (_) {
          return {};
        }
      }

      function codexCliPersistedLaunchState() {
        const saved = codexCliStoredLaunchStates()[codexCliLaunchStateKey()];
        return saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {};
      }

      function codexCliLaunchState(command) {
        const options = codexCliState.options || {};
        return {
          version: 2,
          provider: String(options.provider || codexCliState.provider || "").trim().toLowerCase(),
          profile: String(options.profile || "").trim(),
          savedAt: Date.now(),
          terminalId: codexCliState.terminalId,
          useProxy: codexCliState.useProxy === true,
          proxyPort: codexCliState.proxyPort,
          permission: codexCliState.permission,
          resume: codexCliState.resume === true,
          workdir: codexCliState.workdir,
          workdirCustom: codexCliState.workdirCustom === true,
          noProject: codexCliState.noProject === true,
          migratedWorkdirs: codexCliState.migratedWorkdirs === true,
          model: String(codexCliState.model || "").trim(),
          command: String(command || ""),
          commandEdited: codexCliState.commandEdited === true
            || String(command || "") !== codexCliCommandText(),
        };
      }

      function codexCliPersistLaunchState(savedState = codexCliState.pendingLaunchState) {
        if (!savedState || typeof savedState !== "object" || Array.isArray(savedState)) return false;
        try {
          const states = codexCliStoredLaunchStates();
          states[codexCliLaunchStateKey(savedState, savedState.provider)] = { ...savedState, savedAt: Date.now() };
          ctx.storage.write(localStorage, codexCliLaunchStorageKey, JSON.stringify(states));
          codexCliPersistActiveProfile(savedState.provider, savedState.profile);
          return true;
        } catch (_) {
          return false;
        }
      }

      function codexCliWorkdirRecentsStorageKey() {
        return `${codexCliLaunchStorageKey}:workdirs`;
      }

      function codexCliStoredWorkdirRecents() {
        try {
          const values = JSON.parse(ctx.storage.read(localStorage, codexCliWorkdirRecentsStorageKey(), "{}"));
          return values && typeof values === "object" && !Array.isArray(values) ? values : {};
        } catch (_) {
          return {};
        }
      }

      function codexCliWorkdirScopeKey(provider, profile) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        const normalizedProfile = String(profile || "").trim();
        return `profile:${normalizedProfile || "default"}|provider:${normalizedProvider || "default"}`;
      }

      function codexCliActiveProfileStorageKey() {
        return `${codexCliLaunchStorageKey}:profiles`;
      }

      function codexCliActiveProfile(provider) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (!normalizedProvider) return "";
        try {
          const values = JSON.parse(ctx.storage.read(localStorage, codexCliActiveProfileStorageKey(), "{}"));
          const stored = values && typeof values === "object" && !Array.isArray(values)
            ? String(values[normalizedProvider] || "").trim()
            : "";
          if (stored) return stored;
        } catch (_) {
          // Fall through to the latest structured launch state.
        }
        const saved = Object.values(codexCliStoredLaunchStates())
          .filter((item) => String(item?.provider || "").trim().toLowerCase() === normalizedProvider)
          .sort((left, right) => Number(right?.savedAt || 0) - Number(left?.savedAt || 0))[0];
        return String(saved?.profile || "").trim();
      }

      function codexCliPersistActiveProfile(provider, profile) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (!normalizedProvider) return false;
        try {
          const values = JSON.parse(ctx.storage.read(localStorage, codexCliActiveProfileStorageKey(), "{}"));
          const stored = values && typeof values === "object" && !Array.isArray(values) ? values : {};
          stored[normalizedProvider] = String(profile || "").trim();
          ctx.storage.write(localStorage, codexCliActiveProfileStorageKey(), JSON.stringify(stored));
          return true;
        } catch (_) {
          return false;
        }
      }

      function codexCliWorkdirRecentLabel(path) {
        const normalized = codexCliNormaliseWorkdirPath(path).replace(/[\\/]+$/, "");
        const parts = normalized.split(/[\\/]/).filter(Boolean);
        return parts[parts.length - 1] || normalized;
      }

      function codexCliRecentWorkdirs(provider, profile = codexCliActiveProfile(provider)) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (!normalizedProvider) return [];
        const stored = codexCliStoredWorkdirRecents();
        const scopeKey = codexCliWorkdirScopeKey(normalizedProvider, profile);
        const current = Array.isArray(stored[scopeKey]?.workdirs)
          ? stored[scopeKey].workdirs
          : [];
        const legacy = Object.values(codexCliStoredLaunchStates())
          .filter((item) => String(item?.provider || "").trim().toLowerCase() === normalizedProvider)
          .filter((item) => String(item?.profile || "").trim() === String(profile || "").trim())
          .filter((item) => item?.noProject !== true)
          .map((item) => ({ path: item?.workdir, label: "", usedAt: item?.savedAt }));
        const seen = new Set();
        return [...current, ...legacy]
          .map((item) => ({
            path: codexCliNormaliseWorkdirPath(item?.path),
            label: String(item?.label || "").trim(),
            usedAt: Number(item?.usedAt || 0),
          }))
          .sort((left, right) => right.usedAt - left.usedAt)
          .filter((item) => {
            const identity = codexCliTransferWorkdirIdentity(item.path);
            if (!item.path || !identity || seen.has(identity)) return false;
            seen.add(identity);
            return true;
          })
          .slice(0, 10)
          .map((item) => ({ ...item, label: item.label || codexCliWorkdirRecentLabel(item.path) }));
      }

      function codexCliDefaultWorkdir(provider) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (!normalizedProvider) return { path: "", noProject: false };
        const profile = codexCliActiveProfile(normalizedProvider);
        const saved = Object.values(codexCliStoredLaunchStates())
          .filter((item) => String(item?.provider || "").trim().toLowerCase() === normalizedProvider)
          .filter((item) => String(item?.profile || "").trim() === String(profile || "").trim())
          .filter((item) => item?.commandEdited !== true)
          .sort((left, right) => Number(right?.savedAt || 0) - Number(left?.savedAt || 0))[0];
        return {
          path: saved?.noProject === true ? "" : codexCliNormaliseWorkdirPath(saved?.workdir),
          noProject: saved?.noProject === true,
        };
      }

      function codexCliPersistRecentWorkdir(savedState = codexCliState.pendingLaunchState) {
        if (!savedState || typeof savedState !== "object" || savedState.noProject === true) return false;
        const provider = String(savedState.provider || "").trim().toLowerCase();
        const profile = String(savedState.profile || "").trim();
        const path = codexCliNormaliseWorkdirPath(savedState.workdir);
        const identity = codexCliTransferWorkdirIdentity(path);
        if (!provider || !path || !identity) return false;
        try {
          const stored = codexCliStoredWorkdirRecents();
          const scopeKey = codexCliWorkdirScopeKey(provider, profile);
          const current = Array.isArray(stored[scopeKey]?.workdirs) ? stored[scopeKey].workdirs : [];
          const now = Date.now();
          const workdirs = [{ path, label: codexCliWorkdirRecentLabel(path), usedAt: now }, ...current]
            .map((item) => ({
              path: codexCliNormaliseWorkdirPath(item?.path),
              label: String(item?.label || "").trim(),
              usedAt: Number(item?.usedAt || 0),
            }))
            .filter((item, index, values) => {
              const itemIdentity = codexCliTransferWorkdirIdentity(item.path);
              return !!itemIdentity && values.findIndex((candidate) => (
                codexCliTransferWorkdirIdentity(candidate.path) === itemIdentity
              )) === index;
            })
            .slice(0, 10);
          stored[scopeKey] = { workdirs, savedAt: now };
          ctx.storage.write(localStorage, codexCliWorkdirRecentsStorageKey(), JSON.stringify(stored));
          codexCliPersistActiveProfile(provider, profile);
          return true;
        } catch (_) {
          return false;
        }
      }

      function codexCliTransferWorkdirIdentity(path) {
        const value = codexCliNormaliseWorkdirPath(path).replace(/\\/g, "/");
        return /^[a-z]:\//i.test(value) ? value.toLowerCase() : value;
      }

      function codexCliNormaliseWorkdirPath(path) {
        const value = String(path || "").trim();
        const longPathPrefix = "\\\\?\\";
        if (!value.startsWith(longPathPrefix)) return value;
        const remainder = value.slice(longPathPrefix.length);
        return remainder.toUpperCase().startsWith("UNC\\")
          ? `\\\\${remainder.slice(4)}`
          : remainder;
      }

      function codexCliStoredTransferWorkdirs() {
        try {
          const values = JSON.parse(ctx.storage.read(localStorage, codexCliTransferWorkdirStorageKey, "{}"));
          return values && typeof values === "object" && !Array.isArray(values) ? values : {};
        } catch (_) {
          return {};
        }
      }

      function codexCliTransferWorkdirStorageKey(providerValue = codexCliState.options?.provider || codexCliState.provider) {
        const provider = String(providerValue || "").trim().toLowerCase();
        return provider ? `provider:${provider}` : "";
      }

      function codexCliTransferWorkdirs() {
        const key = codexCliTransferWorkdirStorageKey();
        const stored = key ? codexCliStoredTransferWorkdirs()[key] : null;
        const values = Array.isArray(stored?.workdirs) ? stored.workdirs : [];
        const seen = new Set();
        return values.reduce((result, item) => {
          const path = codexCliNormaliseWorkdirPath(item?.path);
          const identity = codexCliTransferWorkdirIdentity(path);
          if (!path || !identity || seen.has(identity)) return result;
          seen.add(identity);
          result.push({ path, label: codexCliNormaliseWorkdirPath(item?.label) || path });
          return result;
        }, []);
      }

      function codexCliPersistTransferWorkdirs(operation) {
        if (String(operation?.action || "").toLowerCase() !== "sessiontransfer") return false;
        const targetProvider = String(operation?.targetProvider || "").trim().toLowerCase();
        if (!targetProvider) return false;
        const ready = (Array.isArray(operation?.results) ? operation.results : [])
          .filter((item) => item?.targetVisible === true && item?.targetResumable === true)
          .map((item) => ({
            path: codexCliNormaliseWorkdirPath(item?.workdir),
            label: codexCliNormaliseWorkdirPath(item?.workdirName),
          }));
        if (!ready.length) return false;
        try {
          const stored = codexCliStoredTransferWorkdirs();
          const key = codexCliTransferWorkdirStorageKey(targetProvider);
          const current = Array.isArray(stored[key]?.workdirs) ? stored[key].workdirs : [];
          const seen = new Set();
          const workdirs = [...current, ...ready].reduce((result, item) => {
            const path = codexCliNormaliseWorkdirPath(item?.path);
            const identity = codexCliTransferWorkdirIdentity(path);
            if (!path || !identity || seen.has(identity)) return result;
            seen.add(identity);
            result.push({ path, label: codexCliNormaliseWorkdirPath(item?.label) || path });
            return result;
          }, []);
          if (!workdirs.length) return false;
          stored[key] = { workdirs, savedAt: Date.now() };
          ctx.storage.write(localStorage, codexCliTransferWorkdirStorageKey, JSON.stringify(stored));
          return true;
        } catch (_) {
          return false;
        }
      }

      function codexCliVisibleWorkdirs() {
        const discovered = Array.isArray(codexCliState.options?.workdirs)
          ? codexCliState.options.workdirs
          : [];
        const values = codexCliState.migratedWorkdirs
          ? codexCliTransferWorkdirs()
          : discovered;
        return values.reduce((result, item) => {
          const path = codexCliNormaliseWorkdirPath(item?.path);
          if (!path) return result;
          result.push({
            ...item,
            path,
            label: codexCliNormaliseWorkdirPath(item?.label) || path,
          });
          return result;
        }, []);
      }

      function codexCliNoProjectWorkdir() {
        return codexCliNormaliseWorkdirPath(codexCliState.options?.noProjectWorkdir);
      }

      function codexCliApplyRequestedWorkdir() {
        const requested = codexCliState.requestedWorkdir;
        if (!requested || typeof requested !== "object") return false;
        const noProject = requested.noProject === true;
        const workdir = noProject
          ? codexCliNoProjectWorkdir()
          : codexCliNormaliseWorkdirPath(requested.workdir);
        if (!workdir) return false;
        codexCliState.noProject = noProject;
        codexCliState.workdir = workdir;
        codexCliState.migratedWorkdirs = false;
        codexCliState.workdirCustom = !noProject && !codexCliVisibleWorkdirs()
          .some((item) => String(item?.path || "") === workdir);
        return true;
      }

      function codexCliWorkdirOptionsHtml() {
        return codexCliVisibleWorkdirs().map((item) => `
          <option value="${escapeHtml(item.path)}" ${!codexCliState.workdirCustom && item.path === codexCliState.workdir ? "selected" : ""}>
            ${escapeHtml(item.label)} · ${escapeHtml(item.path)}
          </option>
        `).join("");
      }

      function codexCliSyncWorkdirOptions(layer) {
        const select = layer?.querySelector('[data-codex-cli-field="workdirSelect"]');
        if (!select) return;
        const paths = codexCliVisibleWorkdirs().map((item) => String(item?.path || ""));
        const noProjectWorkdir = codexCliNoProjectWorkdir();
        if (codexCliState.noProject && codexCliState.workdir !== noProjectWorkdir) {
          codexCliState.noProject = false;
        }
        if (!codexCliState.noProject && !paths.includes(codexCliState.workdir)) {
          codexCliState.workdir = "";
          codexCliState.workdirCustom = false;
        }
        const custom = codexCliState.migratedWorkdirs ? "" : '<option value="__custom__">自定义路径…</option>';
        const noProject = noProjectWorkdir
          ? `<option value="__no_project__">不在项目中工作</option>`
          : "";
        select.innerHTML = `<option value="" disabled ${!codexCliState.workdir ? "selected" : ""}>请选择工作目录</option>${noProject}${custom}${codexCliWorkdirOptionsHtml()}`;
        select.value = codexCliState.noProject ? "__no_project__" : (codexCliState.workdir || "");
      }

      function codexCliValidatedQuickLaunchState(saved, options = codexCliState.options || {}) {
        if (!saved || typeof saved !== "object" || Array.isArray(saved)) return null;
        const provider = String(options.provider || codexCliState.provider || "").trim().toLowerCase();
        const profile = String(options.profile || "").trim();
        const hasSavedProvider = Object.prototype.hasOwnProperty.call(saved, "provider");
        const hasSavedProfile = Object.prototype.hasOwnProperty.call(saved, "profile");
        if (!hasSavedProvider || !hasSavedProfile) return null;
        const savedProvider = String(saved.provider ?? "").trim().toLowerCase();
        const savedProfile = String(saved.profile ?? "").trim();
        if (!provider || savedProvider !== provider || savedProfile !== profile) return null;
        // A manually edited raw command must be reviewed in the full dialog. The
        // quick path only replays structured fields and regenerates the command.
        if (saved.commandEdited === true) return null;
        const terminalId = String(saved.terminalId || "").trim();
        const permission = String(saved.permission || "").trim();
        const workdir = codexCliNormaliseWorkdirPath(saved.workdir);
        const noProject = saved.noProject === true;
        const terminals = Array.isArray(options.terminals) ? options.terminals : [];
        const permissions = Array.isArray(options.permissions) ? options.permissions : [];
        if (!terminalId || !terminals.some((item) => String(item?.id || "") === terminalId)) return null;
        if (!permission || !permissions.some((item) => String(item?.id || "") === permission)) return null;
        if (!workdir) return null;
        if (
          noProject
          && codexCliTransferWorkdirIdentity(workdir)
            !== codexCliTransferWorkdirIdentity(options.noProjectWorkdir)
        ) return null;
        const proxyPort = Number.parseInt(String(saved.proxyPort || ""), 10);
        if (saved.useProxy === true && (!Number.isInteger(proxyPort) || proxyPort < 1 || proxyPort > 65535)) return null;
        return {
          ...saved,
          version: 2,
          provider,
          profile,
          terminalId,
          permission,
          workdir,
          noProject,
          proxyPort: String(Number.isInteger(proxyPort) ? proxyPort : 7897),
          commandEdited: false,
        };
      }

      function codexCliQuickLaunchConfigurationInvalid(status) {
        if (status?.codexCliLaunchConfigurationInvalid === true) return true;
        const message = String(status?.message || "");
        return [
          "Codex CLI 命令不能为空",
          "工作目录不存在或不可访问",
          "所选终端当前不可用",
          "所选终端没有可执行文件",
        ].some((fragment) => message.includes(fragment));
      }

      function applyCodexCliPersistedLaunchState(saved) {
        if (!saved || typeof saved !== "object") return;
        const terminals = Array.isArray(codexCliState.options?.terminals)
          ? codexCliState.options.terminals
          : [];
        const terminalId = String(saved.terminalId || "").trim();
        if (terminals.some((item) => String(item?.id || "") === terminalId)) {
          codexCliState.terminalId = terminalId;
        }
        const permissions = Array.isArray(codexCliState.options?.permissions)
          ? codexCliState.options.permissions
          : [];
        const permission = String(saved.permission || "").trim();
        if (permissions.some((item) => String(item?.id || "") === permission)) {
          codexCliState.permission = permission;
        }
        const proxyPort = Number.parseInt(String(saved.proxyPort || ""), 10);
        if (Number.isInteger(proxyPort) && proxyPort >= 1 && proxyPort <= 65535) {
          codexCliState.proxyPort = String(proxyPort);
        }
        codexCliState.useProxy = saved.useProxy === true;
        codexCliState.resume = saved.resume === true;
        const workdir = String(saved.workdir || "").trim();
        codexCliState.noProject = saved.noProject === true;
        codexCliState.migratedWorkdirs = saved.migratedWorkdirs === true;
        if (workdir) {
          codexCliState.workdir = workdir;
          const knownWorkdir = codexCliVisibleWorkdirs()
            .some((item) => String(item?.path || "") === workdir);
          codexCliState.workdirCustom = !codexCliState.noProject && (
            saved.workdirCustom === true || !knownWorkdir
          );
        }
        codexCliState.model = String(saved.model || "").trim();
        const command = String(saved.command || "");
        codexCliState.commandText = command;
        codexCliState.commandEdited = saved.commandEdited === true && command.trim() !== "";
      }

      function codexCliDialogLayer() {
        return document.querySelector(`#${settingsModalId} [data-codex-cli-dialog="true"]`);
      }

      function codexCliQuickLaunchLayer() {
        return document.querySelector('[data-codex-cli-quick-launch="true"]');
      }

      function codexCliIsQuickLaunch() {
        return codexCliState.origin === "application-menu";
      }

      function codexCliQuickLaunchCopy(phase = codexCliState.quickLaunchPhase) {
        const provider = codexCliState.provider
          ? providerDisplayName(hudSettingsFromPayload(), codexCliState.provider)
          : "Provider";
        if (phase === "validating") {
          return {
            title: `正在校验 ${provider} 的上次配置`,
            body: "正在确认终端、工作目录和 Provider/Profile 是否仍然可用。",
          };
        }
        if (phase === "launching") {
          return {
            title: `正在启动 ${provider}`,
            body: "正在打开终端并交接 Codex CLI。停止仅在终端创建前有效。",
          };
        }
        if (phase === "stopping") {
          return {
            title: "正在停止启动",
            body: "正在等待后端确认；确认前不会重复提交启动请求。",
          };
        }
        if (phase === "committed") {
          return {
            title: "终端创建已经开始",
            body: "现在无法安全撤销；可以关闭此窗口，启动结果仍会在后台完成。",
          };
        }
        if (phase === "slow") {
          return {
            title: "启动时间比预期更长",
            body: "请求仍由后台处理，当前不会允许重复启动。可以停止或关闭此窗口。",
          };
        }
        if (phase === "error") {
          return {
            title: "无法使用上次配置启动",
            body: codexCliState.quickLaunchMessage || "请打开配置界面检查终端、工作目录和启动选项。",
          };
        }
        return {
          title: `正在读取 ${provider} 的启动配置`,
          body: "找到完整且匹配的上次配置后会直接启动；否则打开配置界面供你确认。",
        };
      }

      function renderCodexCliQuickLaunch(phase = codexCliState.quickLaunchPhase || "discovering", message = "") {
        if (!codexCliIsQuickLaunch() || !codexCliState.open || codexCliState.quickLaunchDismissed) return;
        codexCliState.quickLaunchPhase = phase;
        if (message) codexCliState.quickLaunchMessage = String(message);
        const copy = codexCliQuickLaunchCopy(phase);
        let layer = codexCliQuickLaunchLayer();
        if (!layer) {
          layer = document.createElement("div");
          layer.dataset.codexCliQuickLaunch = "true";
          document.body?.appendChild(layer);
        }
        if (!layer) return;
        const canStop = !new Set(["committed", "error"]).has(phase);
        const showConfigure = phase === "error";
        layer.innerHTML = `
          <div class="codex-usage-hud-cli-quick-surface" role="dialog" aria-modal="true" aria-label="${escapeHtml(copy.title)}">
            <div class="codex-usage-hud-cli-quick-head">
              <div><span>CODEX CLI</span><strong>${escapeHtml(codexCliState.provider ? providerDisplayName(hudSettingsFromPayload(), codexCliState.provider) : "Provider")}</strong></div>
              <button type="button" data-codex-cli-quick-action="close" aria-label="关闭 Loading" title="关闭 Loading">×</button>
            </div>
            <div class="codex-usage-hud-cli-quick-title">${escapeHtml(copy.title)}</div>
            <div class="codex-usage-hud-cli-quick-body">${escapeHtml(copy.body)}</div>
            ${phase === "error" ? "" : '<div class="codex-usage-hud-cli-quick-track" aria-hidden="true"><span></span></div>'}
            <div class="codex-usage-hud-cli-quick-actions">
              ${showConfigure ? '<button type="button" data-codex-cli-quick-action="configure" data-primary="true">调整配置</button>' : ""}
              ${canStop ? '<button type="button" data-codex-cli-quick-action="stop" data-primary="true">停止</button>' : ""}
              <button type="button" data-codex-cli-quick-action="close">关闭</button>
            </div>
          </div>
        `;
      }

      function codexCliTerminal() {
        const terminals = Array.isArray(codexCliState.options?.terminals)
          ? codexCliState.options.terminals
          : [];
        return terminals.find((item) => String(item?.id || "") === codexCliState.terminalId)
          || terminals[0]
          || {};
      }

      function codexCliShell() {
        const shell = String(codexCliTerminal()?.shell || "powershell").toLowerCase();
        return ["powershell", "cmd", "bash", "zsh"].includes(shell) ? shell : "powershell";
      }

      function codexCliLaunchTitle(options = codexCliState.options || {}) {
        const provider = String(options.provider || codexCliState.provider || "").trim().toLowerCase();
        const profile = String(options.profile || "").trim();
        const defaultProvider = String(options.defaultProvider || "").trim().toLowerCase();
        const args = [];
        if (profile) {
          args.push("--profile", profile);
        } else if (provider && provider !== defaultProvider && /^[A-Za-z0-9_-]+$/.test(provider)) {
          args.push("--config", `model_provider=${provider}`);
        }
        return ["启动 Codex", ...args].join(" ");
      }

      function codexCliQuote(value, shell) {
        const text = String(value ?? "");
        if (!text.includes("://") && /^[A-Za-z0-9_./:@%+=,-]+$/.test(text)) return text;
        if (shell === "powershell") return `'${text.replace(/'/g, "''")}'`;
        if (shell === "cmd") return `"${text.replace(/"/g, '\\"')}"`;
        return `'${text.replace(/'/g, "'\\''")}'`;
      }

      function codexCliPermissionArgs(permission) {
        if (String(permission || "") === "read-only") {
          return ["--sandbox", "read-only", "--ask-for-approval", "on-request"];
        }
        if (String(permission || "") === "workspace-write") {
          return ["--sandbox", "workspace-write", "--ask-for-approval", "on-request"];
        }
        return ["--dangerously-bypass-approvals-and-sandbox"];
      }

      function codexCliCommandText() {
        const options = codexCliState.options || {};
        const shell = codexCliShell();
        const provider = String(options.provider || codexCliState.provider || "").trim().toLowerCase();
        const profile = String(options.profile || "").trim();
        const defaultProvider = String(options.defaultProvider || "").trim().toLowerCase();
        const args = [];
        const model = String(codexCliState.model || "").trim();
        if (profile) {
          args.push("--profile", profile);
        } else if (provider && provider !== defaultProvider && /^[A-Za-z0-9_-]+$/.test(provider)) {
          args.push("--config", `model_provider=${provider}`);
        }
        if (model) {
          // 用 -c model= 覆盖 profile/顶层 model，不改任何文件，仅本次启动生效。
          args.push("--config", `model=${model}`);
        }
        args.push(...codexCliPermissionArgs(codexCliState.permission));
        if (codexCliState.resume) args.push("resume");
        const executable = String(options?.codex?.command || "codex");
        const command = [executable, ...args]
          .map((value, index) => index === 0 ? value : codexCliQuote(value, shell))
          .join(" ");
        const lines = [];
        const port = Math.max(1, Math.min(65535, Number.parseInt(codexCliState.proxyPort, 10) || 7897));
        if (codexCliState.useProxy) {
          const proxy = `http://127.0.0.1:${port}`;
          if (shell === "powershell") {
            lines.push(`$env:HTTP_PROXY = ${codexCliQuote(proxy, shell)}`);
            lines.push(`$env:HTTPS_PROXY = ${codexCliQuote(proxy, shell)}`);
          } else if (shell === "cmd") {
            lines.push(`set "HTTP_PROXY=${proxy}"`);
            lines.push(`set "HTTPS_PROXY=${proxy}"`);
          } else {
            const quoted = codexCliQuote(proxy, shell);
            lines.push(`export HTTP_PROXY=${quoted} HTTPS_PROXY=${quoted}`);
          }
        }
        const workdir = String(codexCliState.workdir || "").trim();
        if (workdir && codexCliState.noProject !== true) {
          const quoted = codexCliQuote(workdir, shell);
          lines.push(
            shell === "powershell"
              ? `Set-Location -LiteralPath ${quoted}`
              : shell === "cmd"
                ? `cd /d ${quoted}`
                : `cd -- ${quoted}`,
          );
        }
        lines.push(command);
        return lines.join("\n");
      }

      function codexCliDisplayedCommandText() {
        return codexCliState.commandEdited === true && codexCliState.commandText
          ? codexCliState.commandText
          : codexCliCommandText();
      }

      function codexCliSyncControls({ syncCommand = codexCliState.commandEdited !== true } = {}) {
        const layer = codexCliDialogLayer();
        if (!layer) return;
        const setValue = (selector, value) => {
          const node = layer.querySelector(selector);
          if (node) node.value = String(value ?? "");
        };
        const proxy = layer.querySelector('[data-codex-cli-field="useProxy"]');
        if (proxy) proxy.checked = codexCliState.useProxy === true;
        setValue('[data-codex-cli-field="proxyPort"]', codexCliState.proxyPort);
        const proxyPort = layer.querySelector('[data-codex-cli-proxy-port="true"]');
        if (proxyPort) proxyPort.hidden = codexCliState.useProxy !== true;
        setValue('[data-codex-cli-field="terminal"]', codexCliState.terminalId);
        setValue('[data-codex-cli-field="permission"]', codexCliState.permission);
        setValue('[data-codex-cli-field="model"]', codexCliState.model);
        const resume = layer.querySelector('[data-codex-cli-field="resume"]');
        if (resume) resume.checked = codexCliState.resume === true;
        const migratedWorkdirs = layer.querySelector('[data-codex-cli-field="migratedWorkdirs"]');
        if (migratedWorkdirs) migratedWorkdirs.checked = codexCliState.migratedWorkdirs === true;
        const workdirSelect = layer.querySelector('[data-codex-cli-field="workdirSelect"]');
        const workdirInput = layer.querySelector('[data-codex-cli-field="workdirInput"]');
        if (workdirSelect) {
          const known = !codexCliState.workdirCustom && Array.from(workdirSelect.options).some(
            (option) => option.value === codexCliState.workdir,
          );
          workdirSelect.value = codexCliState.noProject
            ? "__no_project__"
            : (codexCliState.workdirCustom
              ? "__custom__"
              : (known ? codexCliState.workdir : "__custom__"));
        }
        if (workdirInput) {
          workdirInput.value = codexCliState.workdir;
          workdirInput.hidden = codexCliState.migratedWorkdirs || codexCliState.workdirCustom !== true;
        }
        const command = layer.querySelector('[data-codex-cli-field="command"]');
        if (syncCommand && command && command.value !== codexCliDisplayedCommandText()) {
          command.value = codexCliDisplayedCommandText();
        }
        const launch = layer.querySelector('[data-action="codex-cli-launch"]');
        if (launch) launch.disabled = !codexCliTerminal().id;
      }

      function codexCliReadControls() {
        const layer = codexCliDialogLayer();
        if (!layer) return;
        const value = (selector) => String(layer.querySelector(selector)?.value || "").trim();
        const proxy = layer.querySelector('[data-codex-cli-field="useProxy"]');
        codexCliState.useProxy = proxy?.checked === true;
        codexCliState.proxyPort = value('[data-codex-cli-field="proxyPort"]') || "7897";
        codexCliState.terminalId = value('[data-codex-cli-field="terminal"]');
        codexCliState.permission = value('[data-codex-cli-field="permission"]') || "full";
        codexCliState.model = value('[data-codex-cli-field="model"]');
        codexCliState.resume = layer.querySelector('[data-codex-cli-field="resume"]')?.checked === true;
        codexCliState.migratedWorkdirs = layer.querySelector('[data-codex-cli-field="migratedWorkdirs"]')?.checked === true;
        const workdirSelect = value('[data-codex-cli-field="workdirSelect"]');
        codexCliState.noProject = workdirSelect === "__no_project__";
        codexCliState.workdirCustom = workdirSelect === "__custom__";
        codexCliState.workdir = codexCliNormaliseWorkdirPath(
          codexCliState.noProject
            ? codexCliNoProjectWorkdir()
            : (codexCliState.workdirCustom
              ? value('[data-codex-cli-field="workdirInput"]')
              : workdirSelect),
        );
      }

      function codexCliSyncOptionsFromCommand(text) {
        const command = String(text || "");
        const port = command.match(/127\.0\.0\.1:(\d{1,5})/);
        codexCliState.useProxy = /HTTP_PROXY|HTTPS_PROXY|http_proxy|https_proxy/.test(command);
        if (port) codexCliState.proxyPort = port[1];
        codexCliState.permission = command.includes("--dangerously-bypass-approvals-and-sandbox")
          ? "full"
          : command.includes("--sandbox read-only")
            ? "read-only"
            : command.includes("--sandbox workspace-write")
              ? "workspace-write"
              : codexCliState.permission;
        codexCliState.resume = /(^|\s)resume(\s|$)/.test(command);
        const location = command.match(/Set-Location\s+-LiteralPath\s+'((?:''|[^'])*)'/i)
          || command.match(/cd\s+\/d\s+"([^"]+)"/i)
          || command.match(/cd\s+--\s+'((?:'\\''|[^'])*)'/i);
        if (location) {
          codexCliState.workdir = codexCliNormaliseWorkdirPath(
            String(location[1] || "").replace(/''/g, "'"),
          );
          codexCliState.noProject = false;
        }
        const workdirKnown = (Array.isArray(codexCliState.options?.workdirs) ? codexCliState.options.workdirs : [])
          .some((item) => String(item?.path || "") === codexCliState.workdir);
        codexCliState.workdirCustom = !!codexCliState.workdir && !workdirKnown;
        codexCliState.commandText = command;
        codexCliState.commandEdited = true;
        codexCliSyncControls({ syncCommand: false });
      }

      function codexCliRenderCommand() {
        const command = codexCliDialogLayer()?.querySelector('[data-codex-cli-field="command"]');
        if (command) {
          command.value = codexCliCommandText();
          codexCliState.commandText = "";
          codexCliState.commandEdited = false;
        }
        codexCliSyncControls();
      }

      function codexCliModelOptions() {
        const models = Array.isArray(codexCliState.models) ? codexCliState.models : [];
        const current = String(codexCliState.model || "").trim();
        const unique = [...new Set(models)];
        const hasCurrent = !!current && unique.some((model) => String(model) === current);
        const options = ['<option value="">留空用默认</option>'];
        if (unique.length) {
          unique.forEach((model) => {
            const value = String(model);
            options.push(`<option value="${escapeHtml(value)}" ${value === current ? "selected" : ""}>${escapeHtml(value)}</option>`);
          });
        } else if (codexCliState.modelsFetching) {
          options.push('<option value="" disabled data-codex-cli-model-placeholder="true">正在获取模型列表…</option>');
        } else {
          options.push('<option value="" disabled data-codex-cli-model-placeholder="true">点击获取模型列表…</option>');
        }
        if (current && !hasCurrent) {
          options.push(`<option value="${escapeHtml(current)}" selected>${escapeHtml(current)}（自定义）</option>`);
        }
        return options.join("");
      }

      function codexCliModelNote() {
        if (codexCliState.modelsFetching) return "正在获取当前 Provider 的模型列表…";
        if (codexCliState.modelsError) return "模型列表获取失败，点击下拉框或刷新按钮重试。";
        const count = Array.isArray(codexCliState.models) ? codexCliState.models.length : 0;
        if (count) return `已从当前 Provider 获取 ${count} 个模型。`;
        if (codexCliState.modelsFetched) return "当前 Provider 未返回可用模型，可再次点击下拉框重试。";
        return "点击模型下拉框获取当前 Provider 的模型列表。";
      }

      function codexCliChatTestSummarySuffix() {
        if (!codexCliState.modelsError) return "";
        const error = String(codexCliState.modelsError).trim();
        return `（${escapeHtml(error)}）`;
      }

      function codexCliChatTestState() {
        if (codexCliState.chatTestOk) return "hidden";
        if (codexCliState.modelsError) return "open";
        return "";
      }

      function reopenCodexCliModelPicker(select) {
        if (!select || document.activeElement !== select) return false;
        try {
          if (select.matches(":open")) return true;
        } catch (_) {}
        try {
          if (typeof select.showPicker !== "function") return false;
          select.showPicker();
          return true;
        } catch (_) {
          return false;
        }
      }

      function syncCodexCliModelDiscoveryState({ syncOptions = false, reopenPicker = false } = {}) {
        const layer = codexCliDialogLayer();
        if (!layer) return;
        const select = layer.querySelector('[data-codex-cli-field="model"]');
        if (select && syncOptions) {
          select.innerHTML = codexCliModelOptions();
          const current = String(codexCliState.model || "").trim();
          if ([...select.options].some((option) => option.value === current)) {
            select.value = current;
          }
        }
        const note = layer.querySelector('[data-codex-cli-model-note="true"]');
        if (note) note.textContent = codexCliModelNote();
        const error = layer.querySelector('[data-codex-cli-model-error="true"]');
        if (error) {
          const message = String(codexCliState.modelsError || "").trim();
          error.textContent = message ? `（${message}）` : "";
        }
        const placeholder = layer.querySelector('[data-codex-cli-model-placeholder="true"]');
        if (placeholder) {
          placeholder.textContent = codexCliState.modelsFetching
            ? "正在获取模型列表…"
            : "点击获取模型列表…";
        }
        const refresh = layer.querySelector('[data-action="codex-cli-model-refresh"]');
        if (refresh) {
          refresh.disabled = codexCliState.modelsFetching;
          refresh.dataset.loading = String(codexCliState.modelsFetching);
        }
        const chatTest = layer.querySelector('[data-codex-cli-chat-test="true"]');
        if (chatTest) {
          chatTest.hidden = codexCliState.chatTestOk;
          if (codexCliState.modelsError) chatTest.open = true;
        }
        if (select && reopenPicker) reopenCodexCliModelPicker(select);
      }

      function codexCliFormHtml() {
        const options = codexCliState.options || {};
        const terminals = Array.isArray(options.terminals) ? options.terminals : [];
        const permissions = Array.isArray(options.permissions) ? options.permissions : [];
        const terminalOptions = terminals.map((item) => `
          <option value="${escapeHtml(item.id)}" ${item.id === codexCliState.terminalId ? "selected" : ""}>
            ${escapeHtml(item.label)}${item.recommended ? " · 推荐" : ""}
          </option>
        `).join("");
        const permissionOptions = permissions.map((item) => `
          <option value="${escapeHtml(item.id)}" ${item.id === codexCliState.permission ? "selected" : ""}>
            ${escapeHtml(item.label)}
          </option>
        `).join("");
        const workdirOptions = codexCliWorkdirOptionsHtml();
        const noProjectWorkdirOption = codexCliNoProjectWorkdir()
          ? `<option value="__no_project__" ${codexCliState.noProject ? "selected" : ""}>不在项目中工作</option>`
          : "";
        const chatTestState = codexCliChatTestState();
        const powershell7 = options.powershell7 || {};
        const powershellNotice = options.platform === "windows" && powershell7.available !== true
          ? `<div class="codex-usage-hud-codex-cli-notice" data-tone="warning">
              未检测到 PowerShell 7。Windows PowerShell 仍可使用；建议从
              <a href="${escapeHtml(powershell7.installUrl || "https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows")}" target="_blank" rel="noreferrer">微软官方安装说明</a>
              安装后重新打开此列表。
            </div>`
          : "";
        return `
          <div class="codex-usage-hud-codex-cli-grid">
            <div class="codex-usage-hud-codex-cli-context">
              <label class="codex-usage-hud-codex-cli-check">
                <input type="checkbox" data-codex-cli-field="resume" ${codexCliState.resume ? "checked" : ""}>
                <span>启动时打开历史会话选择器（resume）</span>
              </label>
            </div>
            <div class="codex-usage-hud-codex-cli-proxy">
              <label class="codex-usage-hud-codex-cli-check">
                <input type="checkbox" data-codex-cli-field="useProxy" ${codexCliState.useProxy ? "checked" : ""}>
                <span>使用本机代理</span>
              </label>
              <label class="codex-usage-hud-codex-cli-field codex-usage-hud-codex-cli-proxy-port" data-codex-cli-proxy-port="true" ${codexCliState.useProxy ? "" : "hidden"}>
                <input data-codex-cli-field="proxyPort" inputmode="numeric" pattern="[0-9]*" value="${escapeHtml(codexCliState.proxyPort)}" aria-label="代理端口">
              </label>
            </div>
            <label class="codex-usage-hud-codex-cli-field">
              <span>启动终端</span>
              <select data-codex-cli-field="terminal">${terminalOptions || '<option value="">未发现可用终端</option>'}</select>
            </label>
            <label class="codex-usage-hud-codex-cli-field">
              <span>Agent 访问权限</span>
              <select data-codex-cli-field="permission">${permissionOptions}</select>
            </label>
            <div class="codex-usage-hud-codex-cli-field" title="启动参数覆盖为 -c model=；留空则用 profile 或默认模型">
              <span id="codex-usage-hud-codex-cli-model-label">模型（可选）</span>
              <div class="codex-usage-hud-codex-cli-model-control">
                <select data-codex-cli-field="model" aria-labelledby="codex-usage-hud-codex-cli-model-label" aria-describedby="codex-usage-hud-codex-cli-model-help">${codexCliModelOptions()}</select>
                <button type="button" class="codex-usage-hud-settings-icon-action codex-usage-hud-codex-cli-model-refresh" data-action="codex-cli-model-refresh" aria-label="重新获取当前 Provider 的模型列表" title="重新获取模型列表" ${codexCliState.modelsFetching ? "disabled" : ""}><span aria-hidden="true">↻</span></button>
              </div>
            </div>
            <div class="codex-usage-hud-codex-cli-field codex-usage-hud-codex-cli-workdir-field">
              <div class="codex-usage-hud-codex-cli-field-head"><span>工作目录</span><label class="codex-usage-hud-codex-cli-check codex-usage-hud-codex-cli-check-compact"><input type="checkbox" data-codex-cli-field="migratedWorkdirs" ${codexCliState.migratedWorkdirs ? "checked" : ""}><span>来自迁移</span></label></div>
              <select data-codex-cli-field="workdirSelect">
                <option value="" disabled ${!codexCliState.workdir && codexCliState.workdirCustom !== true ? "selected" : ""}>请选择工作目录</option>
                ${noProjectWorkdirOption}
                ${codexCliState.migratedWorkdirs ? "" : '<option value="__custom__">自定义路径…</option>'}
                ${workdirOptions}
              </select>
              <input data-codex-cli-field="workdirInput" value="${escapeHtml(codexCliState.workdir)}" placeholder="输入目录绝对路径" aria-label="工作目录路径">
            </div>
            <details class="codex-usage-hud-codex-cli-chat-test codex-usage-hud-codex-cli-wide" data-codex-cli-chat-test="true" ${chatTestState === "open" ? "open" : ""} ${chatTestState === "hidden" ? "hidden" : ""}>
              <summary id="codex-usage-hud-codex-cli-model-help"><span data-codex-cli-model-note="true">${codexCliModelNote()}</span> · 没有模型列表？改用自定义模型名发送简短聊天测试<span data-codex-cli-model-error="true">${codexCliChatTestSummarySuffix()}</span></summary>
              <div class="codex-usage-hud-codex-cli-chat-test-body">
                <label>
                  <span>自定义模型名称</span>
                  <input data-codex-cli-field="chatModel" value="${escapeHtml(codexCliState.model || "")}" placeholder="例如 Deepseek-v4-flash" autocomplete="off">
                </label>
                <button type="button" class="codex-usage-hud-settings-action" data-action="codex-cli-chat-test">发送聊天测试 (hi)</button>
                <div class="codex-usage-hud-codex-cli-chat-result" data-codex-cli-chat-result="true" aria-live="polite"></div>
              </div>
            </details>
          </div>
          ${powershellNotice}
          ${options.codex?.available === false ? '<div class="codex-usage-hud-codex-cli-notice" data-tone="warning">当前 PATH 中未检测到 codex 命令，但仍可先编辑并启动命令。</div>' : ""}
          <div class="codex-usage-hud-codex-cli-command-head">
            <span>最终命令</span>
            <button type="button" class="codex-usage-hud-settings-icon-action" data-action="codex-cli-copy" aria-label="复制最终命令" title="复制最终命令">⧉</button>
          </div>
          <textarea class="codex-usage-hud-codex-cli-command" data-codex-cli-field="command" spellcheck="false" rows="6" aria-label="最终 Codex CLI 命令">${escapeHtml(codexCliDisplayedCommandText())}</textarea>
          ${codexCliState.permission === "full" ? '<div class="codex-usage-hud-codex-cli-danger">完全访问会跳过 Codex CLI 的审批与沙箱限制，请确认命令和工作目录后再启动。</div>' : ""}
          <div class="codex-usage-hud-codex-cli-actions">
            <button type="button" class="codex-usage-hud-settings-action" data-action="codex-cli-refresh">刷新终端和工作目录</button>
            <span class="codex-usage-hud-codex-cli-status" data-codex-cli-status="true"></span>
            <button type="button" class="codex-usage-hud-settings-action" data-action="codex-cli-close">取消</button>
            <button type="button" class="codex-usage-hud-settings-action" data-action="codex-cli-launch" data-primary="true">启动 Codex CLI</button>
          </div>
        `;
      }

      function renderCodexCliDialog() {
        const layer = codexCliDialogLayer();
        if (!layer) return;
        if (!codexCliState.options) {
          layer.innerHTML = `
            <div class="codex-usage-hud-codex-cli-dialog" role="dialog" aria-modal="true" aria-label="启动 Codex CLI">
              <div class="codex-usage-hud-codex-cli-head"><div><span class="codex-usage-hud-codex-cli-kicker">CODEX CLI</span><strong>正在读取本机终端</strong></div><button type="button" class="codex-usage-hud-settings-close" data-action="codex-cli-close" aria-label="关闭">×</button></div>
              <div class="codex-usage-hud-codex-cli-loading">正在检测终端、PowerShell 7 和 Codex Desktop 工作目录…</div>
            </div>
          `;
          return;
        }
        layer.innerHTML = `
          <div class="codex-usage-hud-codex-cli-dialog" role="dialog" aria-modal="true" aria-label="启动 Codex CLI">
            <div class="codex-usage-hud-codex-cli-head"><div><span class="codex-usage-hud-codex-cli-kicker">CODEX CLI</span><strong>${escapeHtml(codexCliLaunchTitle())}</strong></div><button type="button" class="codex-usage-hud-settings-close" data-action="codex-cli-close" aria-label="关闭">×</button></div>
            ${codexCliFormHtml()}
          </div>
        `;
        codexCliSyncControls();
      }

      function requestCodexCliDiscovery() {
        const requestId = typedSettingsRequestId("codex-cli");
        codexCliState.requestId = requestId;
        return submitSettingsCommand(
          { action: "codexCliDiscover", provider: codexCliState.provider, requestId },
          "正在读取本机终端和 Codex Desktop 工作目录...",
          { preserveOverlay: true },
        );
      }

      function requestCodexCliModels({ force = false, reopenPicker = false } = {}) {
        const count = Array.isArray(codexCliState.models) ? codexCliState.models.length : 0;
        if (!codexCliState.open || !codexCliState.provider || codexCliState.modelsFetching || (!force && count)) return false;
        const requestId = typedSettingsRequestId("codex-cli-models");
        codexCliState.modelsFetching = true;
        codexCliState.modelsFetched = false;
        codexCliState.modelsRequestId = requestId;
        codexCliState.reopenModelPickerAfterFetch = reopenPicker;
        codexCliState.modelsError = "";
        syncCodexCliModelDiscoveryState();
        const submitted = submitSettingsCommand(
          {
            action: "codexCliFetchModels",
            provider: codexCliState.provider,
            requestId,
          },
          "正在获取模型列表...",
          { preserveOverlay: true },
        );
        if (!submitted) {
          codexCliState.modelsFetching = false;
          codexCliState.modelsRequestId = "";
          codexCliState.reopenModelPickerAfterFetch = false;
          codexCliState.modelsError = "模型列表获取请求未能提交。";
          syncCodexCliModelDiscoveryState();
        }
        return submitted;
      }

      function openCodexCliDialog(provider = "") {
        const dialog = settingsDialogRoot();
        if (!dialog || codexCliState.launchRequestId) return false;
        const requestedWorkdir = codexCliState.requestedWorkdir;
        closeCodexCliDialog();
        codexCliState.open = true;
        codexCliState.origin = "dialog";
        codexCliState.provider = String(provider || settingsProviderDraft?.appProvider || "").trim().toLowerCase();
        codexCliState.options = null;
        codexCliState.models = [];
        codexCliState.modelsFetching = false;
        codexCliState.modelsFetched = false;
        codexCliState.modelsRequestId = "";
        codexCliState.reopenModelPickerAfterFetch = false;
        codexCliState.modelsError = "";
        codexCliState.chatTestOk = false;
        codexCliState.commandEdited = false;
        codexCliState.commandText = "";
        codexCliState.quickLaunchPhase = "";
        codexCliState.quickLaunchMessage = "";
        codexCliState.quickLaunchDismissed = false;
        codexCliState.launchRequestId = "";
        codexCliState.launchCancelRequestId = "";
        codexCliState.pendingLaunchState = null;
        codexCliState.requestedWorkdir = requestedWorkdir && typeof requestedWorkdir === "object"
          ? { ...requestedWorkdir }
          : null;
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-codex-cli-layer";
        layer.dataset.codexCliDialog = "true";
        dialog.appendChild(layer);
        renderCodexCliDialog();
        requestCodexCliDiscovery();
        return true;
      }

      function openCodexCliQuickLaunch(provider = "", requestedWorkdir = null) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (!normalizedProvider) return false;
        if (codexCliState.launchRequestId) {
          codexCliState.quickLaunchDismissed = false;
          if (codexCliIsQuickLaunch()) {
            renderCodexCliQuickLaunch(codexCliState.quickLaunchPhase || "launching");
          }
          return false;
        }
        closeCodexCliDialog();
        codexCliQuickLaunchLayer()?.remove();
        codexCliState.open = true;
        codexCliState.origin = "application-menu";
        codexCliState.provider = normalizedProvider;
        codexCliState.requestId = "";
        codexCliState.models = [];
        codexCliState.modelsFetching = false;
        codexCliState.modelsFetched = false;
        codexCliState.modelsRequestId = "";
        codexCliState.reopenModelPickerAfterFetch = false;
        codexCliState.modelsError = "";
        codexCliState.chatTestOk = false;
        codexCliState.options = null;
        codexCliState.commandEdited = false;
        codexCliState.commandText = "";
        codexCliState.quickLaunchPhase = "discovering";
        codexCliState.quickLaunchMessage = "";
        codexCliState.quickLaunchDismissed = false;
        codexCliState.launchRequestId = "";
        codexCliState.launchCancelRequestId = "";
        codexCliState.pendingLaunchState = null;
        codexCliState.requestedWorkdir = requestedWorkdir && typeof requestedWorkdir === "object"
          ? { ...requestedWorkdir }
          : null;
        renderCodexCliQuickLaunch("discovering");
        if (!requestCodexCliDiscovery()) {
          renderCodexCliQuickLaunch("error", "无法连接 HUD 启动服务，请稍后重试或打开配置界面。");
          return false;
        }
        return true;
      }

      function closeCodexCliDialog() {
        if (codexCliState.launchRequestId) {
          closeSettingsConfirm();
          return false;
        }
        clearCodexCliLaunchLifecycle();
        codexCliDialogLayer()?.remove();
        codexCliQuickLaunchLayer()?.remove();
        codexCliState.open = false;
        codexCliState.origin = "";
        codexCliState.requestId = "";
        codexCliState.options = null;
        codexCliState.models = [];
        codexCliState.modelsFetching = false;
        codexCliState.modelsFetched = false;
        codexCliState.modelsRequestId = "";
        codexCliState.reopenModelPickerAfterFetch = false;
        codexCliState.modelsError = "";
        codexCliState.chatTestOk = false;
        codexCliState.commandEdited = false;
        codexCliState.commandText = "";
        codexCliState.quickLaunchPhase = "";
        codexCliState.quickLaunchMessage = "";
        codexCliState.quickLaunchDismissed = false;
        codexCliState.launchRequestId = "";
        codexCliState.launchCancelRequestId = "";
        codexCliState.launchStartedAt = 0;
        codexCliState.pendingLaunchState = null;
        codexCliState.requestedWorkdir = null;
        codexCliState.noProject = false;
        return true;
      }

      function clearCodexCliLaunchLifecycle() {
        if (codexCliState.quickLaunchFrameId) {
          ctx.lifecycle.clearFrame(codexCliState.quickLaunchFrameId);
          codexCliState.quickLaunchFrameId = 0;
        }
        if (codexCliState.launchSubmitFrameId) {
          ctx.lifecycle.clearFrame(codexCliState.launchSubmitFrameId);
          codexCliState.launchSubmitFrameId = 0;
        }
        if (codexCliState.launchTimeoutTimerId) {
          ctx.lifecycle.clearTimeout(codexCliState.launchTimeoutTimerId);
          codexCliState.launchTimeoutTimerId = 0;
        }
        if (codexCliState.launchMinVisibleTimerId) {
          ctx.lifecycle.clearTimeout(codexCliState.launchMinVisibleTimerId);
          codexCliState.launchMinVisibleTimerId = 0;
        }
      }

      function dismissCodexCliQuickLaunch() {
        if (!codexCliIsQuickLaunch()) return false;
        if (!codexCliState.launchRequestId) {
          closeCodexCliDialog();
          return true;
        }
        codexCliState.quickLaunchDismissed = true;
        codexCliQuickLaunchLayer()?.remove();
        return true;
      }

      function openCodexCliQuickLaunchConfiguration(requestedWorkdir = codexCliState.requestedWorkdir) {
        if (!codexCliIsQuickLaunch() || codexCliState.launchRequestId) return false;
        const provider = String(codexCliState.provider || "").trim().toLowerCase();
        closeCodexCliDialog();
        const modal = document.getElementById(settingsModalId);
        const resetProviderDraft = !modal || modal.hidden;
        if (!modal || modal.hidden || settingsActiveTab !== "settings") {
          renderSettingsModal("settings", "", { resetProviderDraft });
        }
        codexCliState.requestedWorkdir = requestedWorkdir && typeof requestedWorkdir === "object"
          ? { ...requestedWorkdir }
          : null;
        openCodexCliDialog(provider);
        return true;
      }

      function stopCodexCliQuickLaunch() {
        if (!codexCliIsQuickLaunch() || !codexCliState.open) return false;
        if (!codexCliState.launchRequestId) {
          closeCodexCliDialog();
          return true;
        }
        if (codexCliState.launchCancelRequestId) return false;
        const cancelRequestId = typedSettingsRequestId("codex-cli-launch-cancel");
        codexCliState.launchCancelRequestId = cancelRequestId;
        renderCodexCliQuickLaunch("stopping");
        const submitted = submitSettingsCommand(
          {
            action: "codexCliLaunchCancel",
            requestId: cancelRequestId,
            launchRequestId: codexCliState.launchRequestId,
          },
          "正在停止 Codex CLI 启动...",
          { preserveOverlay: true },
        );
        if (!submitted) {
          codexCliState.launchCancelRequestId = "";
          renderCodexCliQuickLaunch("error", "停止请求未能提交；启动状态仍由后台处理。可以关闭窗口后稍候。");
        }
        return submitted;
      }

      function refreshCodexCliDialog() {
        if (!codexCliState.open) return;
        codexCliState.options = null;
        codexCliState.models = [];
        codexCliState.modelsFetching = false;
        codexCliState.modelsFetched = false;
        codexCliState.modelsRequestId = "";
        codexCliState.reopenModelPickerAfterFetch = false;
        codexCliState.modelsError = "";
        codexCliState.chatTestOk = false;
        renderCodexCliDialog();
        requestCodexCliDiscovery();
      }

      function codexCliCopyCommand() {
        const command = String(codexCliDialogLayer()?.querySelector('[data-codex-cli-field="command"]')?.value || "");
        void copyHudText(command).then((ok) => {
          const node = codexCliDialogLayer()?.querySelector('[data-codex-cli-status="true"]');
          if (node) node.textContent = ok ? "已复制命令" : "复制失败";
        });
      }

      function codexCliFieldInput(field) {
        if (!codexCliDialogLayer()) return;
        if (field === "command") {
          const command = codexCliDialogLayer().querySelector('[data-codex-cli-field="command"]');
          codexCliSyncOptionsFromCommand(command?.value || "");
          return;
        }
        if (field === "migratedWorkdirs") {
          codexCliReadControls();
          codexCliSyncWorkdirOptions(codexCliDialogLayer());
          codexCliSyncControls({ syncCommand: false });
          codexCliRenderCommand();
          return;
        }
        codexCliReadControls();
        codexCliRenderCommand();
      }

      function codexCliFieldChange(field) {
        codexCliFieldInput(field);
      }

      function launchCodexCliWithState(command) {
        if (codexCliState.launchRequestId) return false;
        const layer = codexCliDialogLayer();
        const status = layer?.querySelector('[data-codex-cli-status="true"]');
        if (!command.trim()) {
          if (codexCliIsQuickLaunch()) renderCodexCliQuickLaunch("error", "上次配置无法生成有效命令，请重新确认启动选项。");
          else if (status) status.textContent = "请先填写命令";
          return false;
        }
        if (!String(codexCliState.workdir || "").trim() && codexCliState.noProject !== true) {
          if (codexCliIsQuickLaunch()) renderCodexCliQuickLaunch("error", "上次配置没有工作目录，请重新选择后启动。");
          else if (status) status.textContent = "请先选择工作目录后再启动 Codex CLI";
          return false;
        }
        if (!codexCliState.terminalId) {
          if (codexCliIsQuickLaunch()) renderCodexCliQuickLaunch("error", "上次选择的终端当前不可用，请重新选择。");
          else if (status) status.textContent = "未发现可用终端，请先刷新终端列表";
          return false;
        }
        codexCliState.pendingLaunchState = codexCliLaunchState(command);
        const launch = layer?.querySelector('[data-action="codex-cli-launch"]');
        if (launch) launch.disabled = true;
        const requestId = typedSettingsRequestId("codex-cli-launch");
        codexCliState.launchRequestId = requestId;
        codexCliState.launchCancelRequestId = "";
        codexCliState.launchStartedAt = Date.now();
        if (codexCliIsQuickLaunch()) {
          renderCodexCliQuickLaunch("launching");
        } else {
          openSettingsLoading({
            kicker: "正在启动",
            title: "正在打开终端",
            body: "正在检查终端并启动 Codex CLI，请稍候。",
            mode: "codex-cli-launch",
          });
        }
        const launchCommand = {
          action: "codexCliLaunch",
          requestId,
          provider: codexCliState.provider,
          profile: String(codexCliState.options?.profile || ""),
          terminalId: codexCliState.terminalId,
          useProxy: codexCliState.useProxy === true,
          proxyPort: codexCliState.proxyPort,
          permission: codexCliState.permission,
          resume: codexCliState.resume === true,
          command,
          workdir: codexCliState.workdir,
          workdirCustom: codexCliState.workdirCustom === true,
          noProject: codexCliState.noProject === true,
          migratedWorkdirs: codexCliState.migratedWorkdirs === true,
        };
        codexCliState.launchTimeoutTimerId = ctx.lifecycle.timeout(
          "codex_cli_launch_timeout",
          () => {
            if (codexCliState.launchRequestId !== requestId) return;
            codexCliState.launchTimeoutTimerId = 0;
            if (codexCliIsQuickLaunch()) {
              renderCodexCliQuickLaunch("slow");
            } else {
              setSettingsLoadingText({
                kicker: "仍在启动",
                title: "启动时间比预期更长",
                body: "请求仍在后台处理，为避免重复打开终端，收到最终结果前不会允许再次启动。",
              });
            }
          },
          codexCliLaunchTimeoutMs,
        );
        codexCliState.launchSubmitFrameId = ctx.lifecycle.frame("codex_cli_launch_submit", () => {
          codexCliState.launchSubmitFrameId = 0;
          if (!codexCliState.open || codexCliState.launchRequestId !== requestId) return;
          const submitted = submitSettingsCommand(
            launchCommand,
            "正在打开终端并启动 Codex CLI...",
            { preserveOverlay: true },
          );
          if (!submitted) {
            clearCodexCliLaunchLifecycle();
            codexCliState.launchRequestId = "";
            codexCliState.launchCancelRequestId = "";
            codexCliState.launchStartedAt = 0;
            codexCliState.pendingLaunchState = null;
            if (codexCliIsQuickLaunch()) {
              renderCodexCliQuickLaunch("error", "启动请求未能提交，请检查 HUD 连接后重试。");
            } else {
              closeSettingsConfirm();
            }
            if (launch) launch.disabled = false;
          }
        });
        return true;
      }

      function launchCodexCliFromDialog() {
        const layer = codexCliDialogLayer();
        if (!layer) return false;
        codexCliReadControls();
        const command = String(layer.querySelector('[data-codex-cli-field="command"]')?.value || "");
        return launchCodexCliWithState(command);
      }

      function launchCodexCliFromQuickState() {
        if (!codexCliIsQuickLaunch() || !codexCliState.options) return false;
        codexCliState.commandEdited = false;
        codexCliState.commandText = "";
        return launchCodexCliWithState(codexCliCommandText());
      }

      function chatTestCodexCliFromDialog() {
        const layer = codexCliDialogLayer();
        if (!layer) return false;
        const model = String(
          layer.querySelector('[data-codex-cli-field="chatModel"]')?.value || "",
        ).trim();
        const resultNode = layer.querySelector('[data-codex-cli-chat-result="true"]');
        const chatButton = layer.querySelector('[data-action="codex-cli-chat-test"]');
        if (!model) {
          if (resultNode) resultNode.textContent = "请输入自定义模型名称。";
          return false;
        }
        if (!codexCliState.provider) {
          if (resultNode) resultNode.textContent = "当前 Provider 不可用。";
          return false;
        }
        if (resultNode) resultNode.textContent = "正在发送聊天测试...";
        if (chatButton) chatButton.disabled = true;
        submitSettingsCommand(
          {
            action: "codexCliChatTest",
            provider: codexCliState.provider,
            model,
            message: "hi",
            requestId: typedSettingsRequestId("codex-cli-chat-test"),
          },
          "正在发送聊天测试...",
          { preserveOverlay: true },
        );
        return true;
      }

      function applyCodexCliCommandStatus(status) {
        if (!status || typeof status !== "object") return;
        const action = String(status.action || "");
        if (action === "codexCliLaunchPending" && codexCliState.open) {
          const expected = String(codexCliState.launchRequestId || "");
          const received = String(status.requestId || "");
          if (!expected || !received || expected !== received) return;
          if (codexCliIsQuickLaunch()) {
            renderCodexCliQuickLaunch("launching");
          } else {
            setSettingsLoadingText({
              kicker: "正在启动",
              title: "正在打开终端",
              body: "启动命令已提交，正在等待终端响应。",
            });
          }
          return;
        }
        if (action === "codexCliLaunchCancel" && codexCliState.open) {
          const expectedCancel = String(codexCliState.launchCancelRequestId || "");
          const receivedCancel = String(status.requestId || "");
          const expectedLaunch = String(codexCliState.launchRequestId || "");
          const receivedLaunch = String(status.launchRequestId || "");
          if (!expectedCancel || !receivedCancel || expectedCancel !== receivedCancel) return;
          if (!expectedLaunch || !receivedLaunch || expectedLaunch !== receivedLaunch) return;
          if (status.cancelAccepted === true) {
            renderCodexCliQuickLaunch("stopping");
          } else if (status.spawnCommitted === true) {
            codexCliState.launchCancelRequestId = "";
            renderCodexCliQuickLaunch("committed");
          } else {
            codexCliState.launchCancelRequestId = "";
            renderCodexCliQuickLaunch("error", String(status.message || "停止请求未能匹配当前启动。"));
          }
          return;
        }
        if (action === "codexCliFetchModels" && codexCliState.open && !codexCliIsQuickLaunch()) {
          const expected = String(codexCliState.modelsRequestId || "");
          const received = String(status.requestId || "");
          if (!expected || !received || expected !== received) return;
          const reopenPicker = codexCliState.reopenModelPickerAfterFetch;
          codexCliState.modelsFetching = false;
          codexCliState.modelsRequestId = "";
          codexCliState.reopenModelPickerAfterFetch = false;
          const payload = status.codexCliModels;
          if (String(status.kind || "") === "error" || !payload) {
            codexCliState.modelsFetched = false;
            codexCliState.modelsError = status?.message || "模型列表获取失败。";
          } else {
            codexCliState.modelsFetched = true;
            codexCliState.modelsError = "";
            codexCliState.models = Array.isArray(payload.models)
              ? payload.models.filter(Boolean).map(String)
              : [];
          }
          syncCodexCliModelDiscoveryState({ syncOptions: true, reopenPicker });
          return;
        }
        if (action === "codexCliChatTest" && codexCliState.open && !codexCliIsQuickLaunch()) {
          const result = status?.codexCliChatTest;
          const resultNode = codexCliDialogLayer()?.querySelector('[data-codex-cli-chat-result="true"]');
          const chatButton = codexCliDialogLayer()?.querySelector('[data-action="codex-cli-chat-test"]');
          if (chatButton) chatButton.disabled = false;
          const ok = result?.ok === true;
          const reply = String(result?.reply || "").trim();
          const error = String(result?.error || "").trim();
          if (resultNode) {
            if (ok) {
              resultNode.textContent = `模型 ${result?.model || "?"} 可用：${reply}`;
              resultNode.classList.remove("codex-usage-hud-codex-cli-chat-result-error");
            } else {
              resultNode.textContent = error || "聊天测试失败。";
              resultNode.classList.add("codex-usage-hud-codex-cli-chat-result-error");
            }
          }
          if (ok && result?.model) {
            // 测试成功的模型直接作为启动模型选择，便于直接用该模型启动。
            codexCliState.model = String(result.model).trim();
            codexCliState.chatTestOk = true;
            renderCodexCliDialog();
          }
          return;
        }
        if (action === "codexCliDiscover" && codexCliState.open) {
          const expected = String(codexCliState.requestId || "");
          const received = String(status.requestId || "");
          if (!expected || !received || expected !== received) return;
          codexCliState.requestId = "";
          if (String(status.kind || "") === "error" || !status.codexCli) {
            if (codexCliIsQuickLaunch()) {
              renderCodexCliQuickLaunch("error", String(status.message || "无法读取本机终端和工作目录。"));
            }
            return;
          }
          codexCliState.options = status.codexCli;
          codexCliPersistActiveProfile(
            status.codexCli.provider || codexCliState.provider,
            status.codexCli.profile,
          );
          const proxy = status.codexCli.proxy || {};
          codexCliState.terminalId = String(status.codexCli.defaultTerminal || "");
          codexCliState.useProxy = false;
          codexCliState.proxyPort = String(proxy.port || "7897");
          codexCliState.permission = String(status.codexCli.defaultPermission || "full");
          codexCliState.resume = false;
          codexCliState.workdir = "";
          codexCliState.workdirCustom = false;
          codexCliState.noProject = false;
          codexCliState.migratedWorkdirs = false;
          codexCliState.commandText = "";
          codexCliState.commandEdited = false;
          const persistedLaunchState = codexCliPersistedLaunchState();
          if (codexCliIsQuickLaunch()) {
            const validatedLaunchState = codexCliValidatedQuickLaunchState(
              persistedLaunchState,
              status.codexCli,
            );
            if (!validatedLaunchState) {
              openCodexCliQuickLaunchConfiguration();
              return;
            }
            applyCodexCliPersistedLaunchState(validatedLaunchState);
            if (codexCliState.requestedWorkdir && !codexCliApplyRequestedWorkdir()) {
              openCodexCliQuickLaunchConfiguration();
              return;
            }
            renderCodexCliQuickLaunch("validating");
            codexCliState.quickLaunchFrameId = ctx.lifecycle.frame("codex_cli_quick_launch", () => {
              codexCliState.quickLaunchFrameId = 0;
              if (codexCliState.open && codexCliIsQuickLaunch()) launchCodexCliFromQuickState();
            });
          } else {
            applyCodexCliPersistedLaunchState(persistedLaunchState);
            codexCliApplyRequestedWorkdir();
            renderCodexCliDialog();
          }
          return;
        }
        if (action === "codexCliLaunch" && codexCliState.open) {
          const expected = String(codexCliState.launchRequestId || "");
          const received = String(status.requestId || "");
          if (!expected || !received || expected !== received) return;
          const finish = () => {
            if (codexCliState.launchRequestId !== received) return;
            const quickLaunch = codexCliIsQuickLaunch();
            const quickDismissed = codexCliState.quickLaunchDismissed;
            const pendingLaunchState = codexCliState.pendingLaunchState;
            clearCodexCliLaunchLifecycle();
            codexCliState.launchRequestId = "";
            codexCliState.launchCancelRequestId = "";
            codexCliState.launchStartedAt = 0;
            codexCliState.pendingLaunchState = null;
            const launch = codexCliDialogLayer()?.querySelector('[data-action="codex-cli-launch"]');
            closeSettingsConfirm();
            if (status.codexCliLaunchCancelled === true) {
              closeCodexCliDialog();
            } else if (String(status.kind || "") !== "error" && status.codexCliLaunch) {
              codexCliPersistLaunchState(pendingLaunchState);
              codexCliPersistRecentWorkdir(pendingLaunchState);
              closeCodexCliDialog();
            } else if (quickLaunch && !quickDismissed && codexCliQuickLaunchConfigurationInvalid(status)) {
              openCodexCliQuickLaunchConfiguration();
            } else if (quickLaunch && !quickDismissed) {
              renderCodexCliQuickLaunch(
                "error",
                String(status.message || "Codex CLI 启动失败，请检查命令、终端和工作目录。"),
              );
            } else if (quickLaunch) {
              closeCodexCliDialog();
            } else if (launch) {
              launch.disabled = false;
              const statusNode = codexCliDialogLayer()?.querySelector('[data-codex-cli-status="true"]');
              if (statusNode) statusNode.textContent = String(status.message || "Codex CLI 启动失败，请检查命令和终端设置。");
            }
          };
          const elapsed = Math.max(0, Date.now() - Number(codexCliState.launchStartedAt || Date.now()));
          if (codexCliState.launchTimeoutTimerId) {
            ctx.lifecycle.clearTimeout(codexCliState.launchTimeoutTimerId);
            codexCliState.launchTimeoutTimerId = 0;
          }
          const remaining = codexCliLaunchMinVisibleMs - elapsed;
          if (remaining > 0) {
            if (codexCliState.launchMinVisibleTimerId) return;
            codexCliState.launchMinVisibleTimerId = ctx.lifecycle.timeout(
              "codex_cli_launch_min_visible",
              finish,
              remaining,
            );
            return;
          }
          finish();
        }
      }

      function settingsChromeMarkup() {
        return `
          <div id="${settingsModalId}" class="codex-usage-hud-settings-modal" hidden></div>
          ${composerBadgeEnabled
            ? `<div class="codex-usage-hud-token-breakdown" data-field="requestComposerBreakdown" role="tooltip" hidden></div>`
            : ""}
          <div class="codex-usage-hud-runtime-errors" data-field="runtimeErrorsPanel" hidden></div>
          ${restReminderToastMarkup()}
        `;
      }

      function loadPersistedSupportImages() {
        try {
          const raw = JSON.parse(ctx.storage.read(localStorage, supportImagesStorageKey, "[]"));
          if (!Array.isArray(raw)) return [];
          return raw.filter((item) => (
            item
            && typeof item === "object"
            && typeof item.src === "string"
            && item.src.startsWith("data:image/")
          ));
        } catch (_) {
          return [];
        }
      }

      function persistSupportImages(images) {
        try {
          const items = Array.isArray(images) ? images.filter(Boolean) : [];
          if (!items.length) return;
          ctx.storage.write(localStorage, supportImagesStorageKey, JSON.stringify(items));
        } catch (_) {}
      }

      function defaultHudSettings() {
        return {
          daily_budget_usd: 100,
          weekly_budget_usd: 400,
          daily_reset_time: "10:00",
          weekly_reset_weekday: 3,
          weekly_reset_time: "10:00",
          display_mode: "renderer",
          work_overlay_max_items: 6,
          work_overlay_side: "right",
          pricing_url: "",
          budget_thresholds: [0.5, 0.8, 0.9, 1.0],
          weekly_adjustment_usd: 0,
          provider_settings: {},
          provider_order: [],
          provider_scope_mode: "all",
          selected_providers: [],
          notification_only_providers: [],
          quick_launch_providers: [],
          provider_registry: {},
          app_provider: "",
          support_url: "https://github.com/fengbuming/codex-usage-hud",
          stop_hud_on_lock_screen: false,
          rest_reminder_enabled: false,
          rest_reminder_interval_minutes: 45,
          rest_reminder_break_minutes: 2,
          rest_reminder_postpone_minutes: 10,
          rest_reminder_idle_reset_minutes: 0,
          rest_reminder_work_start_time: "09:00",
          rest_reminder_work_end_time: "18:00",
          rest_reminder_lunch_enabled: true,
          rest_reminder_lunch_start_time: "12:00",
          rest_reminder_lunch_end_time: "13:30",
          model_prices: {},
          default_model_prices: {},
          pricing_versions: [],
          pricing_audit: [],
        };
      }

      function hudSettingsFromPayload() {
        const raw = currentPayload()?.settings || {};
        return { ...defaultHudSettings(), ...(raw && typeof raw === "object" ? raw : {}) };
      }

      function normalizePriceModel(value) {
        return String(value || "").trim().toLowerCase().replace(/-\\d{4}-\\d{2}-\\d{2}$/, "");
      }

      function priceModelPatternMatches(pattern, model) {
        const normalizedPattern = normalizePriceModel(pattern);
        const normalizedModel = normalizePriceModel(model);
        if (!normalizedPattern || !normalizedModel) return false;
        if (normalizedPattern.includes("*") || normalizedPattern.includes("?")) {
          const regexText = Array.from(normalizedPattern).map((char) => {
            if (char === "*") return ".*";
            if (char === "?") return ".";
            return char.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&");
          }).join("");
          const regex = new RegExp(`^${regexText}$`);
          return regex.test(normalizedModel);
        }
        return normalizedModel === normalizedPattern || normalizedModel.startsWith(`${normalizedPattern}-`);
      }

      function configuredPriceModels(settings) {
        const prices = settings?.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {};
        return Object.entries(prices).map(([key, price]) => String(price?.model || key || "").trim()).filter(Boolean);
      }

      function observedPriceModels() {
        const payload = currentPayload() || {};
        const values = [];
        if (payload.model) values.push(payload.model);
        if (Array.isArray(payload.observedModels)) values.push(...payload.observedModels);
        const seen = new Set();
        return values.map((item) => String(item || "").trim()).filter((item) => {
          if (!item || item === "n/a") return false;
          const key = normalizePriceModel(item);
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      }

      function unknownPriceModels(settings) {
        const configured = configuredPriceModels(settings);
        return observedPriceModels().filter(
          (model) => !configured.some((pattern) => priceModelPatternMatches(pattern, model))
        );
      }

      function settingsBridgeUrl() {
        return String(currentPayload()?.settingsBridgeUrl || "").replace(/\/+$/, "");
      }

      function settingsPathLabel() {
        return String(currentPayload()?.settingsPath || "");
      }

      function appVersion() {
        return String(currentPayload()?.appVersion || "unknown");
      }

      function currentUpdateState() {
        const raw = currentPayload()?.updateState || {};
        return raw && typeof raw === "object" ? raw : {};
      }

      function workOverlaySelectableMax() {
        const value = Number(currentPayload()?.workOverlaySelectableMax ?? 6);
        return Number.isFinite(value) && value >= 1 ? Math.round(value) : 6;
      }

      function desktopOverlayDependency() {
        const raw = currentPayload()?.desktopOverlayDependency || {};
        return raw && typeof raw === "object" ? raw : {};
      }

      function desktopOverlayDependencyHtml() {
        const dependency = desktopOverlayDependency();
        const installed = !!dependency.installed;
        const installing = !!dependency.installing;
        const requiresRestart = !!dependency.requiresRestart;
        const canInstall = !!dependency.canInstall;
        const version = String(dependency.version || "").trim();
        if (installed) {
          return `
            <div class="codex-usage-hud-overlay-dependency" data-installed="true" title="保存后显示方形进度气泡；会话完成后收起为圆形总结。">
              <span class="codex-usage-hud-overlay-dependency-state">已安装</span>
              <span class="codex-usage-hud-overlay-dependency-version">${escapeHtml(version ? `PySide6 ${version}` : "PySide6 可用")}</span>
            </div>
          `;
        }
        const actions = [];
        if (canInstall && !installing) {
          actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-install-desktop-overlay">立即安装</button>');
        }
        if (!requiresRestart) {
          actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-enable-desktop-overlay">启用气泡</button>');
        } else {
          actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-restart">立即重启</button>');
        }
        const stateText = installing ? "正在安装" : (requiresRestart ? "需要重启" : "未安装");
        const noteText = installing
          ? "后台安装中"
          : (requiresRestart ? "重启后生效" : "需 PySide6");
        return `
          <div class="codex-usage-hud-overlay-dependency" data-installed="false" title="${escapeHtml(
            installing
              ? "气泡组件正在后台安装；完成后可启用。"
              : (requiresRestart
                ? "安装完成后重启 HUD，才能显示会话进度气泡。"
                : "会话进度气泡需要 PySide6 桌面组件。")
          )}">
            <span class="codex-usage-hud-overlay-dependency-state">${stateText}</span>
            <span class="codex-usage-hud-overlay-dependency-note">${escapeHtml(noteText)}</span>
            <div class="codex-usage-hud-overlay-dependency-actions">${actions.join("")}</div>
          </div>
        `;
      }

      function syncDesktopOverlayDependency() {
        const node = document.querySelector(`#${settingsModalId} [data-desktop-overlay-dependency="true"]`);
        if (node) node.innerHTML = desktopOverlayDependencyHtml();
      }

      function updateStateFromPayload(payload) {
        const raw = payload?.updateState || {};
        return raw && typeof raw === "object" ? raw : {};
      }

      function updateActionGlyph(state) {
        return String(state?.icon || "download") === "install" ? "⇪" : "↓";
      }

      function renderUpdateButtons(root, payload) {
        const state = updateStateFromPayload(payload);
        const visible = !!state?.visible;
        root.querySelectorAll('[data-action="update-action"]').forEach((node) => {
          if (!(node instanceof HTMLButtonElement)) return;
          node.hidden = !visible;
          if (!visible) {
            node.removeAttribute("title");
            node.removeAttribute("aria-label");
            node.dataset.state = "";
            node.dataset.icon = "";
            return;
          }
          const title = String(state?.title || state?.message || "发现新版本");
          node.textContent = updateActionGlyph(state);
          node.title = title;
          node.setAttribute("aria-label", title);
          node.dataset.state = String(state?.phase || "");
          node.dataset.icon = String(state?.icon || "download");
        });
      }

      function thresholdText(settings) {
        const items = Array.isArray(settings.budget_thresholds) ? settings.budget_thresholds : [];
        return items.map((value) => Number(value || 0)).filter((value) => value > 0).join(",");
      }

      function priceRowsHtml(settings) {
        const prices = settings.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {};
        const entries = Object.entries(prices);
        if (!entries.length) entries.push(["gpt-5.6-sol", { input: 5, output: 30, cached_input: 0.5, cache_write: 6.25 }]);
        return entries.map(([key, price]) => {
          const model = String(price?.model || key || "");
          const provider = String(price?.provider || "");
          const baseUrl = String(price?.base_url || price?.baseUrl || "");
          return `
          <div class="codex-usage-hud-price-row" data-price-row="true" data-price-key="${escapeHtml(key)}" data-price-model="${escapeHtml(model)}" data-price-provider="${escapeHtml(provider)}" data-price-base-url="${escapeHtml(baseUrl)}">
            <input data-price-field="model" value="${escapeHtml(model)}" aria-label="模型">
            <input data-price-field="input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.input ?? 0)}" aria-label="输入单价">
            <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.cached_input ?? 0)}" aria-label="缓存读取单价">
            <input data-price-field="cache_write" type="number" min="0" step="0.000001" value="${escapeHtml(price?.cache_write ?? 0)}" aria-label="缓存写入单价">
            <input data-price-field="output" type="number" min="0" step="0.000001" value="${escapeHtml(price?.output ?? 0)}" aria-label="输出单价">
            <input class="codex-usage-hud-price-advanced" data-price-field="provider" value="${escapeHtml(provider)}" aria-label="渠道">
            <input class="codex-usage-hud-price-advanced" data-price-field="base_url" value="${escapeHtml(baseUrl)}" aria-label="Base URL">
          </div>
        `;
        }).join("");
      }

      function detectedPriceModelsHtml(settings) {
        const models = unknownPriceModels(settings);
        if (!models.length) return "";
        return `
          <div class="codex-usage-hud-price-detected">
            <span>检测到未计价模型</span>
            ${models.slice(0, 4).map((model) => `<button type="button" class="codex-usage-hud-settings-action" data-action="settings-add-detected-model" data-model="${escapeHtml(model)}">${escapeHtml(model)}</button>`).join("")}
          </div>
        `;
      }

      function settingsProviderNames(settings) {
        const registry = settings.provider_registry && typeof settings.provider_registry === "object" ? settings.provider_registry : {};
        const providerSettings = settings.provider_settings && typeof settings.provider_settings === "object" ? settings.provider_settings : {};
        const appProvider = String(settings.app_provider || "").trim().toLowerCase();
        const providerOrder = Array.isArray(settings.provider_order) ? settings.provider_order : [];
        const available = new Set(
          [...Object.keys(providerSettings), ...Object.keys(registry), appProvider]
            .map((provider) => String(provider || "").trim().toLowerCase())
            .filter(Boolean),
        );
        const names = [];
        const seen = new Set();
        const append = (values) => values.forEach((value) => {
          const provider = String(value || "").trim().toLowerCase();
          if (!provider || seen.has(provider)) return;
          seen.add(provider);
          names.push(provider);
        });
        // provider_order is the persisted append-only order. Older settings
        // may not have it, so provider_settings remains the first fallback;
        // registry-only providers are appended after both sources.
        append(providerOrder.filter((provider) => available.has(String(provider || "").trim().toLowerCase())));
        append(Object.keys(providerSettings));
        append(Object.keys(registry));
        append(appProvider ? [appProvider] : []);
        return appProvider
          ? [appProvider, ...names.filter((provider) => provider !== appProvider)]
          : names;
      }

      function cloneSettingsPriceTable(value) {
        const prices = value && typeof value === "object" ? value : {};
        return Object.fromEntries(Object.entries(prices).map(([key, price]) => [
          key,
          price && typeof price === "object" ? { ...price } : {},
        ]));
      }

      // 供应商显示名称：优先取未保存草稿中的名称，其次 config.toml 的 name 键，
      // 缺省时回退 Provider ID（默认名称与 Provider ID 一致）。
      function providerDisplayName(settings, provider) {
        const normalized = String(provider || "").trim().toLowerCase();
        if (!normalized) return "";
        const draftName = String(codexProviderDrafts.get(normalized)?.name || "").trim();
        if (draftName) return draftName;
        return providerRegistryDisplayName(settings, normalized);
      }

      function suggestedProviderEnvironmentKey(provider) {
        const normalized = String(provider || "")
          .trim()
          .replace(/[^A-Za-z0-9]+/g, "_")
          .replace(/^_+|_+$/g, "")
          .toUpperCase();
        // 环境变量名必须以字母或下划线开头；数字开头的 Provider ID（如 123abc）
        // 自动生成的键名如果保持数字开头，会同时被前端和 codex_provider_config 的
        // ENVIRONMENT_KEY_PATTERN 拒绝且提示不可见，表现为「添加」无反应。
        const prefix = /^[0-9]/.test(normalized) ? "_" : "";
        return `${prefix}${normalized || "PROVIDER"}_API_KEY`;
      }

      function suggestedProviderIdFromBaseUrl(baseUrl) {
        const raw = String(baseUrl || "").trim();
        if (!raw) return "";
        let hostname = "";
        try {
          const candidate = /^[A-Za-z][A-Za-z\d+.-]*:\/\//.test(raw)
            ? raw
            : `https://${raw}`;
          hostname = new URL(candidate).hostname;
        } catch {
          hostname = raw
            .replace(/^[A-Za-z][A-Za-z\d+.-]*:\/\//, "")
            .split(/[/?#]/, 1)[0]
            .replace(/:\d+$/, "");
        }
        const parts = hostname
          .replace(/^\[|\]$/g, "")
          .split(".")
          .map((part) => part.trim().toLowerCase())
          .filter(Boolean);
        if (parts.length < 2) return "";
        const provider = parts[parts.length - 2];
        return /^[a-z0-9_-]+$/.test(provider) ? provider : "";
      }

      // Base URL 输入规范化（纯 UI 动作）：去掉首尾空白与尾部斜杠，
      // 未以 /v1 结尾（忽略大小写）时自动补上 /v1。
      function normalizeProviderBaseUrlInput(value) {
        const trimmed = String(value || "").trim().replace(/\/+$/, "");
        if (!trimmed) return "";
        return /\/v1$/i.test(trimmed) ? trimmed : `${trimmed}/v1`;
      }

      function tomlBasicStringEscape(value) {
        return String(value || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      }

      function defaultProviderSectionText(provider, baseUrl, envKey, { isDefault = false } = {}) {
        const normalizedProvider = String(provider || "").trim().toLowerCase() || "provider-id";
        const lines = [
          "[model_providers." + normalizedProvider + "]",
          "name = \"" + tomlBasicStringEscape(normalizedProvider) + "\"",
          "base_url = \"" + tomlBasicStringEscape(baseUrl) + "\"",
          "wire_api = \"responses\"",
        ];
        if (!isDefault) lines.splice(3, 0, "env_key = \"" + tomlBasicStringEscape(envKey) + "\"");
        return lines.join("\n");
      }

      function providerSectionBasicString(text, key) {
        const escapedKey = String(key || "").replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&");
        const match = String(text || "").match(new RegExp("^[\t ]*" + escapedKey + "[\t ]*=[\t ]*\"((?:\\.|[^\"\\\r\n])*)\"", "m"));
        if (!match) return "";
        return match[1].replace(/\\(["\\])/g, "$1");
      }

      function setProviderSectionBasicString(text, key, value) {
        const escapedKey = String(key || "").replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&");
        const escapedValue = tomlBasicStringEscape(value);
        const pattern = new RegExp("^([\t ]*" + escapedKey + "[\t ]*=[\t ]*)\"(?:\\.|[^\"\\\r\n])*\"([\t ]*(?:#.*)?)$", "m");
        if (pattern.test(String(text || ""))) {
          return String(text || "").replace(pattern, "$1\"" + escapedValue + "\"$2");
        }
        const newline = String(text || "").includes("\r\n") ? "\r\n" : "\n";
        const suffix = String(text || "").endsWith("\n") || String(text || "").endsWith("\r") ? "" : newline;
        return String(text || "") + suffix + key + " = \"" + escapedValue + "\"";
      }

      function setProviderSectionHeader(text, provider) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        const header = "[model_providers." + normalizedProvider + "]";
        const pattern = /^[\t ]*\[model_providers\.[A-Za-z0-9_-]+\][\t ]*(?:#.*)?/;
        if (pattern.test(String(text || ""))) return String(text || "").replace(pattern, header);
        return defaultProviderSectionText(normalizedProvider, "", suggestedProviderEnvironmentKey(normalizedProvider));
      }

      function providerSectionHeaderId(text) {
        const match = String(text || "").match(/^[\t ]*\[model_providers\.([A-Za-z0-9_-]+)\][\t ]*(?:#.*)?/m) || "";
        return (match && match[1]) ? match[1].toLowerCase() : "";
      }

      function syncProviderSectionFromFields(layer) {
        const idNode = layer?.querySelector('[data-provider-config-field="provider_id"]');
        const nameNode = layer?.querySelector('[data-provider-config-field="name"]');
        const baseUrlNode = layer?.querySelector('[data-provider-config-field="base_url"]');
        const envNode = layer?.querySelector('[data-provider-config-field="env_key"]');
        const sectionNode = layer?.querySelector('[data-provider-config-field="section_text"]');
        if (!sectionNode) return;
        const id = String(idNode?.value || "provider-id").trim().toLowerCase();
        const previous = String(sectionNode.value);
        const previousHeaderId = providerSectionHeaderId(previous);
        const previousName = providerSectionBasicString(previous, "name");
        let text = setProviderSectionHeader(previous, id);
        if (nameNode) {
          // 名称字段为唯一事实来源；留空时回退为 Provider ID（默认名称与 ID 一致）。
          text = setProviderSectionBasicString(text, "name", String(nameNode.value || "").trim() || id);
        } else if (previousName === previousHeaderId && previousName !== id) {
          // name 仅在仍等于原 Provider ID（即未手动自定义过）时跟随联动同步，
          // 避免覆盖用户自行填写的供应商显示名称。
          text = setProviderSectionBasicString(text, "name", id);
        }
        text = setProviderSectionBasicString(text, "base_url", baseUrlNode?.value || "");
        text = setProviderSectionBasicString(text, "env_key", envNode?.value || "");
        sectionNode.value = text;
      }

      function syncProviderFieldsFromSection(layer) {
        const nameNode = layer?.querySelector('[data-provider-config-field="name"]');
        const baseUrlNode = layer?.querySelector('[data-provider-config-field="base_url"]');
        const envNode = layer?.querySelector('[data-provider-config-field="env_key"]');
        const sectionNode = layer?.querySelector('[data-provider-config-field="section_text"]');
        if (!sectionNode) return;
        const name = providerSectionBasicString(sectionNode.value, "name");
        const baseUrl = providerSectionBasicString(sectionNode.value, "base_url");
        const envKey = providerSectionBasicString(sectionNode.value, "env_key");
        if (nameNode && name) nameNode.value = name;
        if (baseUrlNode && baseUrl) baseUrlNode.value = baseUrl;
        if (envNode && envKey) envNode.value = envKey;
      }

      function codexProviderDraftFromSettings(settings, provider) {
        const detail = settings.provider_registry?.[provider] || {};
        const defined = detail.defined === true;
        return {
          providerId: provider,
          name: String(detail.name || "").trim(),
          baseUrl: String(detail.baseUrl || ""),
          envKey: String(detail.envKey || (defined ? "" : suggestedProviderEnvironmentKey(provider))),
          configText: String(detail.configText || ""),
          apiKey: "",
          currentApiKey: String(detail.apiKey || ""),
          isNew: !defined,
          hasApiKey: detail.hasApiKey === true,
          originalEnvKey: String(detail.envKey || ""),
        };
      }

      function ensureCodexProviderDraft(settings, provider) {
        const normalized = String(provider || "").trim().toLowerCase();
        if (!normalized) return null;
        if (!codexProviderDrafts.has(normalized)) {
          codexProviderDrafts.set(
            normalized,
            codexProviderDraftFromSettings(settings, normalized),
          );
        }
        return codexProviderDrafts.get(normalized);
      }

      function canonicalSettingsPriceTable(value, provider = "") {
        const prices = value && typeof value === "object" ? value : {};
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        const result = {};
        const identities = new Map();
        const priorities = new Map();
        Object.entries(prices).forEach(([key, rawPrice]) => {
          const price = rawPrice && typeof rawPrice === "object" ? { ...rawPrice } : {};
          const model = String(price.model || key || "").trim();
          if (!model) return;
          const explicitProvider = String(price.provider || "").trim().toLowerCase();
          const baseUrl = String(price.base_url || price.baseUrl || "").trim().replace(/\/+$/, "");
          const scopedKey = `${normalizedProvider}/${model}`;
          const isCurrentProviderRow = !!normalizedProvider && (
            explicitProvider === normalizedProvider
            || (!explicitProvider && String(key || "").toLowerCase() === scopedKey.toLowerCase())
          );
          const identity = `${normalizePriceModel(model)}\u0000${baseUrl.toLowerCase()}`;
          // Preserve the key for Base URL-specific rows; only unscoped rows
          // collapse to their model identity.
          let canonicalKey = baseUrl ? key : model;
          const existingIdentity = identities.get(canonicalKey);
          if (existingIdentity && existingIdentity !== identity) {
            canonicalKey = `${baseUrl ? `${baseUrl}/` : ""}${model}`;
          }
          const priority = explicitProvider === normalizedProvider
            ? 3
            : isCurrentProviderRow
              ? 2
              : !explicitProvider
                ? 1
                : 0;
          const previousKey = identities.get(identity);
          const previousPriority = priorities.get(identity) ?? -1;
          if (previousKey && priority < previousPriority) return;
          if (previousKey && previousKey !== canonicalKey) delete result[previousKey];
          result[canonicalKey] = price;
          identities.set(identity, canonicalKey);
          priorities.set(identity, priority);
        });
        return result;
      }

      function providerDraftFromSettings(settings, provider, enabled, notificationOnly) {
        const source = settings.provider_settings?.[provider] || {};
        const modelPrices = source.model_prices && typeof source.model_prices === "object"
          ? source.model_prices
          : settings.model_prices;
        return {
          enabled: !!enabled,
          notificationOnly: !!notificationOnly && !enabled,
          settings: {
            ...source,
            model_prices: canonicalSettingsPriceTable(modelPrices, provider),
            pricing_url: String(source.pricing_url ?? settings.pricing_url ?? ""),
            weekly_adjustment_usd: Number(source.weekly_adjustment_usd ?? settings.weekly_adjustment_usd ?? 0),
          },
        };
      }

      function ensureSettingsProviderDraft(settings, reset = false) {
        if (reset) {
          codexProviderDrafts.clear();
          codexProviderDirty.clear();
        }
        if (settingsProviderDraft && !reset) return settingsProviderDraft;
        const order = settingsProviderNames(settings);
        const appProvider = String(settings.app_provider || "").trim().toLowerCase();
        const providerSettings = settings.provider_settings && typeof settings.provider_settings === "object"
          ? settings.provider_settings
          : {};
        const selected = settings.provider_scope_mode === "custom"
          ? new Set((settings.selected_providers || []).map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean))
          : new Set(order);
        const notificationOnly = new Set(
          (settings.notification_only_providers || []).map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean)
        );
        const quickLaunchProviders = new Set(
          (settings.quick_launch_providers || []).map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean)
        );
        if (appProvider) selected.add(appProvider);
        const requestedProvider = String(window[settingsProviderName] || "").trim().toLowerCase();
        const activeProvider = order.includes(requestedProvider)
          ? requestedProvider
          : (order.includes(appProvider) ? appProvider : (order[0] || ""));
        settingsProviderDraft = {
          activeProvider,
          appProvider,
          order,
          quickLaunchProviders,
          providers: Object.fromEntries(order.map((provider) => {
            ensureCodexProviderDraft(settings, provider);
            // A payload received before runtime migration can still expose a
            // registry-only provider. Give it the same safe notification-only
            // default while its clean default price table is materialized.
            const isNewProvider = !Object.prototype.hasOwnProperty.call(providerSettings, provider);
            return [provider, providerDraftFromSettings(
              settings,
              provider,
              selected.has(provider) || provider === appProvider,
              (notificationOnly.has(provider) || (
                isNewProvider
                && settings.provider_scope_mode === "custom"
                && !selected.has(provider)
              )) && !selected.has(provider) && provider !== appProvider,
            )];
          })),
        };
        settingsDirtyProviders.clear();
        window[settingsProviderName] = activeProvider;
        return settingsProviderDraft;
      }

      function settingsProviderTabBadge(settings, provider) {
        const detail = settings.provider_registry?.[provider] || {};
        if (provider === settingsProviderDraft?.appProvider) return "App";
        if (detail.historicalOnly) return "历史";
        if (provider === "unknown") return "未知";
        return "";
      }

      function settingsProviderMeta(settings, provider) {
        const detail = settings.provider_registry?.[provider] || {};
        const profiles = Array.isArray(detail.profiles) ? detail.profiles.map((profile) => String(profile || "").trim()).filter(Boolean) : [];
        const parts = [];
        let tone = "";
        if (provider === settingsProviderDraft?.appProvider) {
          tone = "required";
        } else if (detail.historicalOnly) {
          parts.push("历史通道");
          tone = "historical";
        } else if (provider === "unknown") {
          parts.push("未知通道");
        }
        if (profiles.length) {
          parts.push(`${profiles.length > 1 ? "Profiles" : "Profile"}: ${profiles.join(", ")}`);
        }
        return { text: parts.join(" · "), tone };
      }

      function settingsProviderTabHtml(settings, provider) {
        const draft = ensureSettingsProviderDraft(settings);
        const badge = settingsProviderTabBadge(settings, provider);
        const dirty = settingsDirtyProviders.has(provider);
        const displayName = providerDisplayName(settings, provider);
        return `
          <button type="button" class="codex-usage-hud-provider-tab" role="tab"
            data-action="settings-provider-tab" data-provider-tab="true" data-provider="${escapeHtml(provider)}"
            aria-selected="${provider === draft.activeProvider}"
            aria-label="切换到 Provider ${escapeHtml(displayName)}">
            <span>${escapeHtml(displayName)}</span>
            ${badge ? `<span class="codex-usage-hud-provider-tab-badge">${escapeHtml(badge)}</span>` : ""}
            ${dirty ? '<span class="codex-usage-hud-provider-dirty-dot" aria-hidden="true"></span><span class="codex-usage-hud-settings-visually-hidden">有未保存修改</span>' : ""}
          </button>
        `;
      }

      function settingsProviderTabsHtml(settings) {
        const draft = ensureSettingsProviderDraft(settings);
        const appProvider = draft.appProvider && draft.order.includes(draft.appProvider)
          ? draft.appProvider
          : "";
        const railProviders = draft.order.filter((provider) => provider !== appProvider);
        const appTab = appProvider
          ? `<div class="codex-usage-hud-provider-tab-fixed" data-provider-tab-fixed="true">${settingsProviderTabHtml(settings, appProvider)}</div>`
          : "";
        const railTabs = railProviders.map((provider) => settingsProviderTabHtml(settings, provider)).join("");
        return `
          <div class="codex-usage-hud-provider-navigation" data-provider-navigation="true" role="tablist" aria-label="Provider">
            ${appTab}
            <div class="codex-usage-hud-provider-tab-viewport" data-provider-tab-viewport="true">
              <button type="button" class="codex-usage-hud-provider-nav-button" data-action="settings-provider-nav" data-direction="prev" aria-label="显示前一个 Provider" title="显示前一个 Provider" hidden>‹</button>
              <div class="codex-usage-hud-provider-tabs" data-provider-tabs="true">${railTabs}</div>
              <button type="button" class="codex-usage-hud-provider-nav-button" data-action="settings-provider-nav" data-direction="next" aria-label="显示后一个 Provider" title="显示后一个 Provider" hidden>›</button>
            </div>
          </div>
        `;
      }

      function settingsProviderEditorHtml(settings) {
        const draft = ensureSettingsProviderDraft(settings);
        const activeProvider = draft.activeProvider;
        const head = `
          <div class="codex-usage-hud-provider-editor-head">
            <div class="codex-usage-hud-price-title">模型单价${Number(settings?.pricing_sync?.unread_change_count || 0) > 0 ? ` <button type="button" class="codex-usage-hud-provider-dirty-dot" data-action="pricing-sync-open" aria-label="有 ${Number(settings.pricing_sync.unread_change_count)} 项价格更新" title="查看价格更新"></button>` : ""}</div>
            ${settingsProviderTabsHtml(settings)}
            <div class="codex-usage-hud-price-unit-wrap">
              <div class="codex-usage-hud-price-unit">USD / 1M tokens</div>
              <button type="button" class="codex-usage-hud-settings-action codex-usage-hud-provider-add-action" data-action="settings-add-provider">新增供应商</button>
            </div>
          </div>
        `;
        const entry = draft.providers[activeProvider];
        if (!activeProvider || !entry) {
          return `${head}<div class="codex-usage-hud-provider-empty">尚未发现 Provider</div>`;
        }
        const providerSettings = entry.settings;
        const required = activeProvider === draft.appProvider;
        const providerRegistryEntry = settings.provider_registry?.[activeProvider] || {};
        const officialAccount = required && providerRegistryEntry.officialAccount === true;
        // 仅 custom / requires_openai_auth 的默认供应商走 auth.json 编辑链路；
        // 其它默认供应商（如内置 openai）不开放编辑，与后端 is_default_app_provider 判定一致。
        const defaultProviderEditable = required && (
          activeProvider === "custom" || providerRegistryEntry.requiresOpenaiAuth === true
        );
        const quickLaunchEnabled = draft.quickLaunchProviders?.has(activeProvider) === true;
        const pricingDraftPending = settingsDirtyProviders.has(activeProvider);
        const meta = settingsProviderMeta(settings, activeProvider);
        const activeDisplayName = providerDisplayName(settings, activeProvider);
        const weeklyAdjustment = Number(providerSettings.weekly_adjustment_usd);
        const weeklyAdjustmentValue = Number.isFinite(weeklyAdjustment) && weeklyAdjustment > 0
          ? String(weeklyAdjustment)
          : "";
        const pricingUrlPlaceholder = "计费单价获取地址 · https://example.com/model-prices.json";
        return `
          ${head}
          <div class="codex-usage-hud-provider-context">
            <div class="codex-usage-hud-provider-scope-options">
              <label class="codex-usage-hud-provider-scope" ${required ? 'title="Codex App Provider 必须纳入统计"' : ""}>
                <input type="checkbox" data-provider-enabled="true" ${entry.enabled || required ? "checked" : ""} ${required ? "disabled" : ""}>
                <span>纳入统计</span>
              </label>
              <label class="codex-usage-hud-provider-scope" ${required ? 'title="Codex App Provider 必须纳入统计"' : ""}>
                <input type="checkbox" data-provider-notification-only="true" ${entry.notificationOnly && !required ? "checked" : ""} ${required ? "disabled" : ""}>
                <span>仅气泡通知不统计</span>
              </label>
              <div class="codex-usage-hud-provider-context-adjustment">
                <input data-setting-key="weekly_adjustment_usd" type="number" min="0" step="0.01" value="${escapeHtml(weeklyAdjustmentValue)}" placeholder="本周补充额度 USD" aria-label="本周补充额度 USD" title="本周补充额度 USD">
              </div>
            </div>
            <div class="codex-usage-hud-provider-meta-row">
              <div class="codex-usage-hud-provider-meta" data-tone="${escapeHtml(meta.tone)}">${escapeHtml(meta.text)}</div>
              <label class="codex-usage-hud-provider-quick-launch" title="将当前 Provider 加入 Codex Desktop 的启动CLI菜单">
                <input type="checkbox" data-provider-quick-launch="true" ${quickLaunchEnabled ? "checked" : ""}>
                <span>快捷启动</span>
              </label>
              <button type="button" class="codex-usage-hud-settings-icon-action codex-usage-hud-codex-cli-launch-action" data-action="settings-codex-cli-open" data-provider="${escapeHtml(activeProvider)}" aria-label="以当前 Provider 启动 Codex CLI" title="以当前 Provider 启动 Codex CLI"><span aria-hidden="true">&gt;_</span></button>
              <button type="button" class="codex-usage-hud-settings-icon-action" data-action="settings-transfer-provider" data-provider="${escapeHtml(activeProvider)}" aria-label="复制或迁移 ${escapeHtml(activeDisplayName)} 的会话" title="复制或迁移会话"><span aria-hidden="true">⇆</span></button>
              ${officialAccount
                ? '<span class="codex-usage-hud-provider-config-locked" role="img" aria-label="官方账号登录，默认 Provider 不可编辑" title="官方账号登录时由 Codex Desktop 管理，默认 Provider 不可编辑">🔒</span>'
                : defaultProviderEditable || !required
                  ? '<button type="button" class="codex-usage-hud-settings-icon-action" data-action="settings-edit-provider" data-provider="' + escapeHtml(activeProvider) + '" aria-label="编辑 ' + escapeHtml(activeDisplayName) + ' 供应商配置" title="编辑供应商配置">✎</button>'
                  : ''}
              ${required ? "" : '<button type="button" class="codex-usage-hud-settings-icon-action codex-usage-hud-provider-delete-action" data-action="settings-delete-provider" data-provider="' + escapeHtml(activeProvider) + '" aria-label="删除 ' + escapeHtml(activeDisplayName) + ' 供应商" title="删除供应商"><span aria-hidden="true">⌫</span></button>'}
              <button type="button" class="codex-usage-hud-settings-action codex-usage-hud-pricing-apply-action" data-action="settings-pricing-apply" data-primary="true" data-price-draft-pending="${pricingDraftPending}" aria-label="${pricingDraftPending ? "应用模型单价修改，有待应用修改" : "应用模型单价修改"}" title="${pricingDraftPending ? "应用待提交的模型单价修改" : "应用模型单价修改"}">应用<span class="codex-usage-hud-provider-dirty-dot codex-usage-hud-pricing-apply-dirty-dot" data-pricing-apply-dirty-dot="true" aria-hidden="true" ${pricingDraftPending ? "" : "hidden"}></span></button>
            </div>
          </div>
          <div class="codex-usage-hud-price-table">
            <div class="codex-usage-hud-price-header">
              <div>模型</div><div>输入</div><div>缓存读取</div><div>缓存写入</div><div>输出</div><div class="codex-usage-hud-price-advanced">渠道</div><div class="codex-usage-hud-price-advanced">Base URL</div>
            </div>
            <div data-price-rows="true">${priceRowsHtml(providerSettings)}</div>
            ${detectedPriceModelsHtml(providerSettings)}
            <div class="codex-usage-hud-price-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-add-model">添加模型</button>
              <input data-setting-key="pricing_url" value="${escapeHtml(providerSettings.pricing_url)}" placeholder="${escapeHtml(pricingUrlPlaceholder)}" aria-label="计费单价获取地址" title="${escapeHtml(pricingUrlPlaceholder)}">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-fetch-prices">检查价格更新</button>
              <button type="button" class="codex-usage-hud-settings-action codex-usage-hud-pricing-icon-action" data-action="settings-sync-provider-prices" aria-label="同步当前 Provider 单价到其它 Provider" title="同步当前 Provider 的模型单价到其它 Provider"><span aria-hidden="true">⇄</span></button>
              <button type="button" class="codex-usage-hud-settings-action codex-usage-hud-pricing-icon-action" data-action="pricing-export" aria-label="导出价格 JSON" title="导出价格 JSON"><span aria-hidden="true">⇩</span></button>
              <button type="button" class="codex-usage-hud-settings-action codex-usage-hud-pricing-icon-action" data-action="pricing-import-open" aria-label="导入价格 JSON" title="导入价格 JSON"><span aria-hidden="true">⇧</span></button>
            </div>
          </div>
        `;
      }

      function revealSettingsProviderTab(tab) {
        const tabs = tab?.closest?.('[data-provider-tabs="true"]');
        if (!tab || !tabs) return;
        const tabRect = tab.getBoundingClientRect();
        const railRect = tabs.getBoundingClientRect();
        const tabCenter = tabs.scrollLeft + (tabRect.left - railRect.left) + (tabRect.width / 2);
        const nextLeft = tabCenter - (tabs.clientWidth / 2);
        tabs.scrollLeft = Math.max(0, Math.min(nextLeft, tabs.scrollWidth - tabs.clientWidth));
      }

      function settingsProviderNavigationRoot(node = null) {
        if (node?.matches?.('[data-provider-navigation="true"]')) return node;
        return node?.closest?.('[data-provider-navigation="true"]')
          || document.querySelector(`#${settingsModalId} [data-provider-navigation="true"]`);
      }

      function syncSettingsProviderTabNavigation(node = null) {
        const navigation = settingsProviderNavigationRoot(node);
        const viewport = navigation?.querySelector?.('[data-provider-tab-viewport="true"]');
        const tabs = viewport?.querySelector?.('[data-provider-tabs="true"]');
        if (!navigation || !viewport || !tabs) return;
        const hasTabs = !!tabs.querySelector('[data-provider-tab="true"]');
        viewport.hidden = !hasTabs;
        if (!hasTabs) return;
        const hasOverflow = tabs.scrollWidth > tabs.clientWidth + 1;
        const canPrev = tabs.scrollLeft > 1;
        const canNext = tabs.scrollLeft + tabs.clientWidth < tabs.scrollWidth - 1;
        navigation.dataset.providerOverflow = String(hasOverflow);
        const previous = viewport.querySelector('[data-direction="prev"]');
        const next = viewport.querySelector('[data-direction="next"]');
        if (previous) {
          previous.hidden = !hasOverflow;
          previous.disabled = !canPrev;
        }
        if (next) {
          next.hidden = !hasOverflow;
          next.disabled = !canNext;
        }
      }

      function settingsProviderRailDirectionForTab(tab) {
        const tabs = tab?.closest?.('[data-provider-tabs="true"]');
        if (!tabs || tabs.clientWidth <= 0 || tabs.scrollWidth <= tabs.clientWidth + 1) return 0;
        const tabRect = tab.getBoundingClientRect();
        const railRect = tabs.getBoundingClientRect();
        if (tabs.scrollLeft > 1 && tabRect.left <= railRect.left + 1) return -1;
        if (tabs.scrollLeft + tabs.clientWidth < tabs.scrollWidth - 1 && tabRect.right >= railRect.right - 1) return 1;
        return 0;
      }

      function scrollSettingsProviderRail(direction = 0, node = null) {
        const navigation = settingsProviderNavigationRoot(node);
        const tabs = navigation?.querySelector?.('[data-provider-tabs="true"]');
        if (!tabs || !direction) return false;
        const items = Array.from(tabs.querySelectorAll('[data-provider-tab="true"]'));
        if (!items.length) return false;
        const railRect = tabs.getBoundingClientRect();
        let target = null;
        if (direction < 0) {
          target = items.filter((item) => item.getBoundingClientRect().right <= railRect.left + 1).pop() || items[0];
        } else {
          target = items.find((item) => item.getBoundingClientRect().left >= railRect.right - 1) || items[items.length - 1];
        }
        // offsetLeft may be relative to the outer navigation wrapper rather
        // than this scroll container. Convert the target's visible position
        // back into the rail's content coordinate so the edge button can
        // actually move away from the current scroll boundary.
        const targetLeft = tabs.scrollLeft + (target.getBoundingClientRect().left - railRect.left);
        const nextLeft = Math.max(0, Math.min(targetLeft, tabs.scrollWidth - tabs.clientWidth));
        tabs.scrollTo({ left: nextLeft, behavior: "smooth" });
        return true;
      }

      function renderSettingsProviderTabs() {
        const navigation = document.querySelector(`#${settingsModalId} [data-provider-navigation="true"]`);
        if (!navigation || !settingsProviderDraft) return;
        navigation.outerHTML = settingsProviderTabsHtml(hudSettingsFromPayload());
        const nextNavigation = document.querySelector(`#${settingsModalId} [data-provider-navigation="true"]`);
        syncSettingsProviderTabNavigation(nextNavigation);
        revealSettingsProviderTab(nextNavigation?.querySelector('[aria-selected="true"]'));
        syncSettingsProviderTabNavigation(nextNavigation);
      }

      function captureSettingsProviderForm() {
        const modal = document.getElementById(settingsModalId);
        const editor = modal?.querySelector('[data-provider-editor="true"]');
        const activeProvider = String(editor?.dataset.activeProvider || "").trim().toLowerCase();
        const entry = settingsProviderDraft?.providers?.[activeProvider];
        if (!editor || !activeProvider || !entry) return "";
        const modelPrices = {};
        editor.querySelectorAll("[data-price-row='true']").forEach((row) => {
          const model = String(row.querySelector("[data-price-field='model']")?.value || "").trim();
          if (!model) return;
          const provider = String(row.querySelector("[data-price-field='provider']")?.value || "").trim().toLowerCase();
          const baseUrl = String(row.querySelector("[data-price-field='base_url']")?.value || "").trim().replace(/\/+$/, "");
          const originalKey = String(row.dataset.priceKey || "").trim();
          const originalModel = String(row.dataset.priceModel || "").trim();
          const originalProvider = String(row.dataset.priceProvider || "").trim().toLowerCase();
          const originalBaseUrl = String(row.dataset.priceBaseUrl || "").trim().replace(/\/+$/, "");
          const field = (name) => {
            const value = Number(row.querySelector(`[data-price-field="${name}"]`)?.value);
            return Number.isFinite(value) && value >= 0 ? value : 0;
          };
          // Keep the persisted row key stable while editing numeric fields. The
          // provider/model form fields are optional scope overrides, so only
          // rebuild the key when one of those identity fields actually changes.
          const sameScope = originalKey
            && originalModel === model
            && originalProvider === provider
            && originalBaseUrl === baseUrl;
          const key = sameScope
            ? originalKey
            : (provider ? `${provider}/${model}` : (baseUrl ? `${baseUrl}/${model}` : model));
          const output = field("output");
          modelPrices[key] = {
            model,
            input: field("input"),
            cached_input: field("cached_input"),
            cache_write: field("cache_write"),
            output,
            reasoning: output,
          };
          if (provider) modelPrices[key].provider = provider;
          if (baseUrl) modelPrices[key].base_url = baseUrl;
        });
        const enabledNode = editor.querySelector('[data-provider-enabled="true"]');
        const notificationOnlyNode = editor.querySelector('[data-provider-notification-only="true"]');
        const quickLaunchNode = editor.querySelector('[data-provider-quick-launch="true"]');
        const pricingNode = editor.querySelector('[data-setting-key="pricing_url"]');
        const adjustmentNode = editor.querySelector('[data-setting-key="weekly_adjustment_usd"]');
        const adjustment = Number(adjustmentNode?.value);
        entry.enabled = activeProvider === settingsProviderDraft.appProvider || !!enabledNode?.checked;
        entry.notificationOnly = !entry.enabled && !!notificationOnlyNode?.checked;
        entry.settings = {
          ...entry.settings,
          model_prices: modelPrices,
          pricing_url: String(pricingNode?.value || "").trim(),
          weekly_adjustment_usd: Number.isFinite(adjustment) && adjustment >= 0 ? adjustment : 0,
        };
        if (quickLaunchNode?.checked) settingsProviderDraft.quickLaunchProviders.add(activeProvider);
        else settingsProviderDraft.quickLaunchProviders.delete(activeProvider);
        return activeProvider;
      }

      function updateSettingsProviderDraftStatus() {
        const count = settingsDirtyProviders.size;
        if (count) setSettingsStatus(`${count} 个 Provider 有未保存修改`);
      }

      function syncPricingApplyDirtyState() {
        const button = document.querySelector(`#${settingsModalId} [data-action="settings-pricing-apply"]`);
        if (!button) return;
        const activeProvider = String(settingsProviderDraft?.activeProvider || "").trim().toLowerCase();
        const pending = !!activeProvider && settingsDirtyProviders.has(activeProvider);
        button.dataset.priceDraftPending = String(pending);
        button.setAttribute(
          "aria-label",
          pending ? "应用模型单价修改，有待应用修改" : "应用模型单价修改",
        );
        button.title = pending ? "应用待提交的模型单价修改" : "应用模型单价修改";
        const dot = button.querySelector('[data-pricing-apply-dirty-dot="true"]');
        if (dot) dot.hidden = !pending;
      }

      function markSettingsProviderDirty() {
        const activeProvider = captureSettingsProviderForm();
        if (!activeProvider) return;
        settingsDirtyProviders.add(activeProvider);
        syncCodexCliQuickLaunchMenu();
        renderSettingsProviderTabs();
        syncPricingApplyDirtyState();
        updateSettingsProviderDraftStatus();
      }

      function renderSettingsProviderEditor({ focusTab = false } = {}) {
        const editor = document.querySelector(`#${settingsModalId} [data-provider-editor="true"]`);
        if (!editor || !settingsProviderDraft) return;
        editor.dataset.activeProvider = settingsProviderDraft.activeProvider;
        editor.innerHTML = settingsProviderEditorHtml(hudSettingsFromPayload());
        const activeTab = editor.querySelector('[data-provider-tab="true"][aria-selected="true"]');
        syncSettingsProviderTabNavigation(editor);
        revealSettingsProviderTab(activeTab);
        syncSettingsProviderTabNavigation(editor);
        if (focusTab) activeTab?.focus?.();
        updateSettingsProviderDraftStatus();
      }

      function switchSettingsProvider(provider, { focusTab = false, railDirection = 0 } = {}) {
        const nextProvider = String(provider || "").trim().toLowerCase();
        if (!settingsProviderDraft?.order.includes(nextProvider) || nextProvider === settingsProviderDraft.activeProvider) return;
        captureSettingsProviderForm();
        settingsProviderDraft.activeProvider = nextProvider;
        window[settingsProviderName] = nextProvider;
        renderSettingsProviderEditor({ focusTab });
        if (railDirection) {
          const activeTab = document.querySelector(
            `#${settingsModalId} [data-provider-tab="true"][aria-selected="true"]`,
          );
          revealSettingsProviderTab(activeTab);
          syncSettingsProviderTabNavigation(activeTab);
        }
      }

      function activateSettingsProviderTab(tab) {
        const provider = String(tab?.dataset?.provider || "").trim().toLowerCase();
        if (!provider) return;
        const railDirection = settingsProviderRailDirectionForTab(tab);
        if (provider === settingsProviderDraft?.activeProvider) {
          if (railDirection) {
            revealSettingsProviderTab(tab);
            syncSettingsProviderTabNavigation(tab);
          }
          return;
        }
        switchSettingsProvider(provider, { railDirection });
      }

      function cloneProviderModelPrices(value, provider) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        const prices = cloneSettingsPriceTable(value);
        return Object.fromEntries(Object.entries(prices).map(([key, price]) => [
          key,
          {
            ...price,
            model: String(price?.model || key || ""),
            provider: normalizedProvider,
          },
        ]));
      }

      function providerConfigDialogLayer() {
        return document.querySelector(`#${settingsModalId} [data-provider-config-dialog="true"]`);
      }

      function toggleProviderApiKeyVisibility() {
        const layer = providerConfigDialogLayer();
        const input = layer?.querySelector('[data-provider-config-field="api_key"]');
        const button = layer?.querySelector('[data-action="settings-provider-toggle-api-key"]');
        if (!input || !button) return;
        const reveal = input.type === "password";
        input.type = reveal ? "text" : "password";
        button.setAttribute("aria-label", reveal ? "隐藏明文" : "显示明文");
        button.setAttribute("title", reveal ? "隐藏明文" : "显示明文");
        if (button.firstElementChild) {
          button.firstElementChild.textContent = reveal ? "🙈" : "👁";
        }
      }

      function testProviderConnectivityFromDialog() {
        const layer = providerConfigDialogLayer();
        if (!layer) return false;
        const baseUrl = String(
          layer.querySelector('[data-provider-config-field="base_url"]')?.value || "",
        ).trim();
        const apiKey = String(
          layer.querySelector('[data-provider-config-field="api_key"]')?.value || "",
        ).trim();
        const statusNode = layer.querySelector('[data-provider-config-connectivity-status=""]');
        if (statusNode) {
          statusNode.textContent = "";
          statusNode.classList.remove("codex-usage-hud-provider-config-fetch-status-error");
        }
        if (!baseUrl) {
          const message = "请先填写 Base URL。";
          setSettingsStatus(message, "error");
          if (statusNode) {
            statusNode.textContent = message;
            statusNode.classList.add("codex-usage-hud-provider-config-fetch-status-error");
          }
          return false;
        }
        const activeApiKey = apiKey || "";
        if (!activeApiKey) {
          const message = "请先填写 API key（或输入已保存的密钥）。";
          setSettingsStatus(message, "error");
          if (statusNode) {
            statusNode.textContent = message;
            statusNode.classList.add("codex-usage-hud-provider-config-fetch-status-error");
          }
          return false;
        }
        pricingWorkflowState.providerModelsFetching = true;
        if (statusNode) {
          statusNode.textContent = "正在测试连通性...";
        }
        const testButton = layer.querySelector('[data-action="settings-provider-test-connectivity"]');
        if (testButton) testButton.disabled = true;
        return submitSettingsCommand(
          {
            action: "fetchProviderModels",
            requestId: typedSettingsRequestId("provider-models"),
            baseUrl,
            apiKey: activeApiKey,
          },
          "正在测试连通性...",
          { preserveOverlay: true },
        );
      }

      function applyProviderConnectivityStatus(status) {
        const isError = String(status.kind || "") === "error";
        if (!pricingWorkflowState.providerModelsFetching) return;
        const connected = status?.providerConnected;
        if (connected !== true && !isError) return;
        const layer = providerConfigDialogLayer();
        const statusNode = layer?.querySelector('[data-provider-config-connectivity-status=""]');
        pricingWorkflowState.providerModelsFetching = false;
        const testButton = layer?.querySelector('[data-action="settings-provider-test-connectivity"]');
        if (testButton) testButton.disabled = false;
        if (isError || connected !== true) {
          if (statusNode) {
            statusNode.textContent = status?.message && isError ? status.message : "连通性测试失败。";
            statusNode.classList.add("codex-usage-hud-provider-config-fetch-status-error");
          }
          const modelsNode = layer?.querySelector('[data-provider-config-models=""]');
          if (modelsNode) {
            modelsNode.hidden = true;
            modelsNode.innerHTML = "";
          }
          showProviderChatTest(layer, { force: true });
          return;
        }
        if (statusNode) {
          statusNode.textContent = status?.message || "连通性测试成功。";
          statusNode.classList.remove("codex-usage-hud-provider-config-fetch-status-error");
        }
        showProviderChatTest(layer, { force: false });
        const modelList = Array.isArray(status?.models) ? status.models.filter(Boolean).map(String) : [];
        const modelsNode = layer?.querySelector('[data-provider-config-models=""]');
        if (modelsNode) {
          if (modelList.length) {
            modelsNode.hidden = false;
            const unique = [...new Set(modelList)];
            modelsNode.innerHTML = `
              <option value="" selected disabled>可用模型（${unique.length}）</option>
              ${unique.map((model) =>
                `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join("")}
            `;
          } else {
            modelsNode.hidden = true;
            modelsNode.innerHTML = "";
          }
        }
      }

      function showProviderChatTest(layer, { force = false } = {}) {
        if (!layer) return;
        const chatTest = layer.querySelector('[data-provider-config-chat-test=""]');
        if (!chatTest) return;
        chatTest.hidden = false;
        if (force) chatTest.open = true;
      }

      function chatTestProviderFromDialog() {
        const layer = providerConfigDialogLayer();
        if (!layer) return false;
        const baseUrl = String(
          layer.querySelector('[data-provider-config-field="base_url"]')?.value || "",
        ).trim();
        const apiKey = String(
          layer.querySelector('[data-provider-config-field="api_key"]')?.value || "",
        ).trim();
        const model = String(
          layer.querySelector('[data-provider-config-field="chat_model"]')?.value || "",
        ).trim();
        const resultNode = layer.querySelector('[data-provider-config-chat-result=""]');
        const chatButton = layer.querySelector('[data-action="settings-provider-chat-test"]');
        if (!baseUrl) {
          setSettingsStatus("请先填写 Base URL。", "error");
          return false;
        }
        if (!apiKey) {
          setSettingsStatus("请先填写 API key（或输入已保存的密钥）。", "error");
          return false;
        }
        if (!model) {
          setSettingsStatus("请输入自定义模型名称。", "error");
          if (resultNode) resultNode.textContent = "请输入自定义模型名称。";
          return false;
        }
        if (resultNode) resultNode.textContent = "正在发送聊天测试...";
        if (chatButton) chatButton.disabled = true;
        return submitSettingsCommand(
          {
            action: "providerChatTest",
            requestId: typedSettingsRequestId("provider-chat-test"),
            baseUrl,
            apiKey,
            model,
            message: "hi",
          },
          "正在发送聊天测试...",
          { preserveOverlay: true },
        );
      }

      function applyProviderChatTestStatus(status) {
        const layer = providerConfigDialogLayer();
        if (!layer || !status || typeof status !== "object") return;
        if (String(status.action || "") !== "providerChatTest") return;
        const result = status?.providerChatTest;
        const resultNode = layer.querySelector('[data-provider-config-chat-result=""]');
        const chatButton = layer.querySelector('[data-action="settings-provider-chat-test"]');
        if (chatButton) chatButton.disabled = false;
        if (!resultNode) return;
        const ok = result?.ok === true;
        const reply = String(result?.reply || "").trim();
        const error = String(result?.error || "").trim();
        if (ok) {
          resultNode.textContent = `模型 ${result?.model || "?"} 可用：${reply}`;
          resultNode.classList.remove("codex-usage-hud-provider-config-chat-result-error");
        } else {
          resultNode.textContent = error || "聊天测试失败。";
          resultNode.classList.add("codex-usage-hud-provider-config-chat-result-error");
        }
      }

      function openProviderConfigDialog(provider = "", { isNew = false } = {}) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        const settings = hudSettingsFromPayload();
        ensureSettingsProviderDraft(settings);
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        const isAppProvider = !isNew && normalizedProvider === String(settingsProviderDraft.appProvider || "").trim().toLowerCase();
        const registryEntry = settings.provider_registry?.[normalizedProvider] || {};
        // 仅 custom / requires_openai_auth 的默认供应商走 auth.json 编辑链路，与后端 is_default_app_provider 判定一致。
        const isDefaultProvider = isAppProvider && (
          normalizedProvider === "custom" || registryEntry.requiresOpenaiAuth === true
        );
        // 官方账号登录时，任意默认供应商都不可编辑（不限于 custom）。
        if (isAppProvider && registryEntry.officialAccount === true) {
          setSettingsStatus("官方账号登录时，默认 Codex App Provider 由 Codex Desktop 管理，不支持编辑。", "error");
          return;
        }
        const target = isNew ? null : ensureCodexProviderDraft(settings, normalizedProvider);
        // 每次打开编辑对话框都从 provider registry 刷新 API key 回显值，
        // 避免使用会话内缓存的旧 draft 值。
        if (target) {
          target.currentApiKey = String(registryEntry.apiKey || "");
          if (typeof registryEntry.hasApiKey === "boolean") {
            target.hasApiKey = registryEntry.hasApiKey;
          }
          target.name = String(registryEntry.name || target.name || "");
          target.envKey = String(registryEntry.envKey || target.originalEnvKey || target.envKey || "");
          target.baseUrl = String(registryEntry.baseUrl || target.baseUrl || "");
          target.configText = String(registryEntry.configText || target.configText || "");
        }
        // 供应商显示名称：编辑时回显 config.toml 的 name 键，缺省与 Provider ID 一致；
        // 新增时留空并跟随 Provider ID 输入联动，保存时缺省同样回退为 Provider ID。
        const initialProviderName = isNew
          ? ""
          : String(target?.name || registryEntry.name || normalizedProvider).trim() || normalizedProvider;
        const providerDisplayTitle = initialProviderName || normalizedProvider;
        // 自动补 /v1 开关：新增时默认开启；编辑时若现有 Base URL 明确未以 /v1
        // 结尾则默认关闭，避免失焦时改写用户既有的非 /v1 配置。
        const initialV1Enabled = isNew
          || /\/v1\/?$/i.test(String(target?.baseUrl || registryEntry.baseUrl || "").trim());
        const targetEnvKey = target?.envKey || (isNew ? suggestedProviderEnvironmentKey(normalizedProvider) : "");
        const initialConfigText = String(
          target?.configText
            || defaultProviderSectionText(normalizedProvider, target?.baseUrl || "", targetEnvKey, {
              isDefault: isDefaultProvider,
            }),
        );
        const sourceProvider = isNew
          ? String(settingsProviderDraft.appProvider || "").trim().toLowerCase()
          : "";
        const sourceOptions = settingsProviderDraft.order.map((item) => `
          <option value="${escapeHtml(item)}" ${item === sourceProvider ? "selected" : ""}>${escapeHtml(providerDisplayName(settings, item))}</option>
        `).join("");
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.dataset.providerConfigDialog = "true";
        layer.dataset.providerConfigMode = isNew ? "new" : "edit";
        layer.dataset.providerConfigProvider = normalizedProvider;
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card codex-usage-hud-provider-config-card" role="dialog" aria-modal="true" aria-label="${isNew ? "新增供应商" : "编辑供应商配置"}">
            <div class="codex-usage-hud-settings-confirm-kicker">Codex model provider</div>
            <div class="codex-usage-hud-settings-confirm-title">${isNew ? "新增供应商" : `编辑 ${escapeHtml(providerDisplayTitle)} 供应商`}</div>
            <div class="codex-usage-hud-provider-config-grid">
              <div class="codex-usage-hud-provider-config-split">
                <label>Provider ID
                  <input data-provider-config-field="provider_id" value="${escapeHtml(normalizedProvider)}" ${isNew ? "" : "readonly"} autocomplete="off">
                </label>
                <label>供应商名称
                  <input data-provider-config-field="name" value="${escapeHtml(initialProviderName)}" ${isDefaultProvider ? "readonly" : ""} placeholder="默认与 Provider ID 相同" autocomplete="off">
                </label>
              </div>
              ${isNew ? `<label>复制模型列表 / 单价配置
                <select data-provider-config-field="source_provider">
                  <option value="">不复制，使用当前默认价格</option>
                  ${sourceOptions}
                </select>
              </label>` : `<div class="codex-usage-hud-provider-config-grid-placeholder" aria-hidden="true"></div>`}
              <div class="codex-usage-hud-provider-config-field">
                <div class="codex-usage-hud-provider-config-headrow">
                  <span>Base URL</span>
                  <label class="codex-usage-hud-provider-config-v1-toggle" title="开启后，Base URL 失焦时若未以 /v1 结尾将自动补上">
                    <input type="checkbox" data-provider-config-field="base_url_v1" ${initialV1Enabled ? "checked" : ""}>
                    <span>自动补 /v1</span>
                  </label>
                </div>
                <input data-provider-config-field="base_url" value="${escapeHtml(target?.baseUrl || "")}" placeholder="https://api.example.com/v1" autocomplete="url">
              </div>
              ${isDefaultProvider
                ? `<div class="codex-usage-hud-provider-config-auth-note">Codex App 使用 auth.json 中的 OPENAI_API_KEY，不使用用户环境变量。</div>`
                : `<label>用户环境变量名称
                  <input data-provider-config-field="env_key" value="${escapeHtml(target?.envKey || (isNew ? suggestedProviderEnvironmentKey(normalizedProvider) : ""))}" autocomplete="off">
                </label>`}
              <div class="codex-usage-hud-provider-config-apikey">
                <label>API key
                  <span class="codex-usage-hud-provider-config-apikey-field">
                    <input data-provider-config-field="api_key" type="password" value="${escapeHtml(isNew ? "" : (target?.currentApiKey || ""))}" placeholder="${isNew ? "请输入 API key" : (target?.hasApiKey ? "已填充当前密钥，可修改" : "请输入 API key")}" autocomplete="new-password">
                    <button type="button" class="codex-usage-hud-settings-icon-action codex-usage-hud-provider-config-eye" data-action="settings-provider-toggle-api-key" aria-label="显示明文" title="显示明文"><span aria-hidden="true">👁</span></button>
                    <select class="codex-usage-hud-provider-config-models" data-provider-config-models="" hidden aria-label="可用模型"></select>
                    <button type="button" class="codex-usage-hud-settings-action codex-usage-hud-provider-config-fetch" data-action="settings-provider-test-connectivity">测试连通性</button>
                  </span>
                </label>
                <div class="codex-usage-hud-provider-config-fetch-status" data-provider-config-connectivity-status="" aria-live="polite"></div>
                <details class="codex-usage-hud-provider-config-chat-test" data-provider-config-chat-test="" hidden>
                  <summary>没有可用的模型列表？改用自定义模型名发送简短聊天测试</summary>
                  <div class="codex-usage-hud-provider-config-chat-test-body">
                    <label>自定义模型名称
                      <input data-provider-config-field="chat_model" value="${escapeHtml(target?.model || "")}" placeholder="例如 Deepseek-v4-flash" autocomplete="off">
                    </label>
                    <button type="button" class="codex-usage-hud-settings-action" data-action="settings-provider-chat-test">发送聊天测试 (hi)</button>
                    <div class="codex-usage-hud-provider-config-chat-result" data-provider-config-chat-result="" aria-live="polite"></div>
                  </div>
                </details>
              </div>
              ${isNew ? `<fieldset class="codex-usage-hud-provider-config-scope">
                <legend>统计范围</legend>
                <label><input type="radio" name="codex-provider-scope" value="notification" checked> 仅气泡通知不统计</label>
                <label><input type="radio" name="codex-provider-scope" value="included"> 纳入统计</label>
              </fieldset>` : ""}
            </div>
            <details class="codex-usage-hud-provider-config-preview">
              <summary>展开 config.toml 多行配置预览</summary>
              <label class="codex-usage-hud-provider-config-section">config.toml 配置（[model_providers.${escapeHtml(normalizedProvider || "xxxx")}]）
                <textarea data-provider-config-field="section_text" rows="8" spellcheck="false">${escapeHtml(initialConfigText)}</textarea>
                <span>此处内容会写回用户 config.toml；Base URL 和环境变量名会与上面的字段同步。</span>
              </label>
            </details>
            <div class="codex-usage-hud-settings-confirm-body">${isDefaultProvider
              ? "保存设置后会更新主 config.toml 的默认 Provider 段；API key 写入 Codex auth.json，不会创建 custom.config.toml。编辑时已填充当前密钥，点击 👁 可查看明文。修改 Base URL / API key 后，需要重启 Codex Desktop 才能生效，保存后会提示你选择立即重启或稍后重启。"
              : "保存设置后会更新用户的 config.toml；API key 只写入用户环境变量，不会保存到 HUD 配置。编辑时已填充当前密钥，点击 👁 可查看明文。"}</div>
            <div class="codex-usage-hud-provider-config-status" data-provider-config-status="true" role="alert" aria-live="polite"></div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-provider-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-provider-apply" data-primary="true">${isNew ? "添加" : "应用"}</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
        const idNode = layer.querySelector('[data-provider-config-field="provider_id"]');
        const nameNode = layer.querySelector('[data-provider-config-field="name"]');
        const envNode = layer.querySelector('[data-provider-config-field="env_key"]');
        const sectionNode = layer.querySelector('[data-provider-config-field="section_text"]');
        const baseUrlNode = layer.querySelector('[data-provider-config-field="base_url"]');
        const v1Node = layer.querySelector('[data-provider-config-field="base_url_v1"]');
        let generatedEnvKey = isNew
          ? suggestedProviderEnvironmentKey(idNode?.value)
          : "";
        // 名称跟随 Provider ID 联动：仅当名称仍等于上次程序化同步值（或为空）时跟随，
        // 用户手动填写过名称后不再覆盖。
        let generatedName = isNew ? String(idNode?.value || "").trim() : "";
        const syncNameFromProviderId = (nextId) => {
          if (!isNew || !nameNode) return;
          const trimmed = String(nextId || "").trim();
          if (nameNode.value === generatedName || !nameNode.value) {
            nameNode.value = trimmed;
          }
          generatedName = trimmed;
        };
        if (isNew && idNode && envNode) {
          idNode.addEventListener("input", () => {
            if (envNode.value === generatedEnvKey || !envNode.value) {
              generatedEnvKey = suggestedProviderEnvironmentKey(idNode.value);
              envNode.value = generatedEnvKey;
            } else {
              generatedEnvKey = suggestedProviderEnvironmentKey(idNode.value);
            }
            syncNameFromProviderId(idNode.value);
            syncProviderSectionFromFields(layer);
          });
        }
        nameNode?.addEventListener("input", () => syncProviderSectionFromFields(layer));
        [baseUrlNode, envNode].forEach((node) => {
          node?.addEventListener("input", () => {
            if (isNew && node === baseUrlNode && idNode) {
              const suggestedProvider = suggestedProviderIdFromBaseUrl(baseUrlNode.value);
              if (suggestedProvider) {
                const shouldUpdateEnvKey = !envNode || envNode.value === generatedEnvKey || !envNode.value;
                idNode.value = suggestedProvider;
                generatedEnvKey = suggestedProviderEnvironmentKey(suggestedProvider);
                if (shouldUpdateEnvKey && envNode) envNode.value = generatedEnvKey;
                syncNameFromProviderId(suggestedProvider);
              }
            }
            syncProviderSectionFromFields(layer);
          });
        });
        // Base URL 失焦时若未以 /v1 结尾且「自动补 /v1」开关开启，则自动补上
        // （仅 UI 规范化，保存值随之更新；关闭开关后不再自动追加）。
        baseUrlNode?.addEventListener("change", () => {
          if (v1Node?.checked) {
            const next = normalizeProviderBaseUrlInput(baseUrlNode.value);
            if (next !== baseUrlNode.value) baseUrlNode.value = next;
          }
          syncProviderSectionFromFields(layer);
        });
        // 开关切换：打开时立即对当前值补 /v1；关闭时保留当前值，仅停止后续自动补全。
        v1Node?.addEventListener("change", () => {
          if (v1Node.checked && baseUrlNode) {
            const next = normalizeProviderBaseUrlInput(baseUrlNode.value);
            if (next !== baseUrlNode.value) baseUrlNode.value = next;
          }
          syncProviderSectionFromFields(layer);
        });
        sectionNode?.addEventListener("input", () => syncProviderFieldsFromSection(layer));
        // 编辑时自动选中 API key 输入框，便于直接替换密钥；
        // 新增时聚焦 Base URL 方便输入地址。
        const apiKeyNode = layer.querySelector('[data-provider-config-field="api_key"]');
        const initialFocusNode = isNew ? baseUrlNode : apiKeyNode;
        initialFocusNode?.focus?.();
        initialFocusNode?.select?.();
      }

      function setProviderConfigDialogError(message) {
        setSettingsStatus(message, "error");
        const layer = document.querySelector(`#${settingsModalId} [data-provider-config-dialog="true"]`);
        const target = layer?.querySelector('[data-provider-config-status="true"]');
        if (target) {
          target.textContent = String(message || "");
          target.dataset.kind = "error";
        }
      }

      function applyProviderConfigDialog() {
        const layer = document.querySelector(`#${settingsModalId} [data-provider-config-dialog="true"]`);
        if (!layer || !settingsProviderDraft) return false;
        const isNew = layer.dataset.providerConfigMode === "new";
        const idNode = layer.querySelector('[data-provider-config-field="provider_id"]');
        const nameNode = layer.querySelector('[data-provider-config-field="name"]');
        const sourceNode = layer.querySelector('[data-provider-config-field="source_provider"]');
        const baseUrlNode = layer.querySelector('[data-provider-config-field="base_url"]');
        const envNode = layer.querySelector('[data-provider-config-field="env_key"]');
        const apiKeyNode = layer.querySelector('[data-provider-config-field="api_key"]');
        const sectionNode = layer.querySelector('[data-provider-config-field="section_text"]');
        const provider = String(idNode?.value || "").trim().toLowerCase();
        // 显示名称缺省时回退 Provider ID（默认名称与 Provider ID 一致）。
        const displayName = String(nameNode?.value || "").trim() || provider;
        const isAppProvider = !isNew
          && provider === String(settingsProviderDraft.appProvider || "").trim().toLowerCase();
        const settings = hudSettingsFromPayload();
        const providerRegistryEntry = settings.provider_registry?.[provider] || {};
        // 与后端 is_default_app_provider 判定一致：仅 custom / requires_openai_auth 默认供应商走 auth.json 链路。
        const isDefaultProvider = isAppProvider && (
          provider === "custom" || providerRegistryEntry.requiresOpenaiAuth === true
        );
        if (isAppProvider && providerRegistryEntry.officialAccount === true) {
          setProviderConfigDialogError("官方账号登录时，默认 Codex App Provider 由 Codex Desktop 管理，不支持编辑。");
          return false;
        }
        const sectionText = String(sectionNode?.value || "");
        const baseUrl = sectionNode
          ? providerSectionBasicString(sectionText, "base_url").trim().replace(/\/+$/, "")
          : String(baseUrlNode?.value || "").trim().replace(/\/+$/, "");
        const envKey = isDefaultProvider
          ? ""
          : sectionNode
          ? providerSectionBasicString(sectionText, "env_key").trim()
          : String(envNode?.value || "").trim();
        const apiKey = String(apiKeyNode?.value || "");
        if (!/^[A-Za-z0-9_-]+$/.test(provider) || (isNew && provider === "custom")) {
          setProviderConfigDialogError("Provider ID 只能使用字母、数字、连字符或下划线，且不能是 custom。");
          return false;
        }
        if (["openai", "ollama", "lmstudio"].includes(String(provider).toLowerCase())) {
          setProviderConfigDialogError("Provider ID 与 Codex 内置 Provider 重名（openai / ollama / lmstudio），内置 Provider 无法覆盖，请改用其它 ID（例如 ollama-local）。");
          return false;
        }
        if (!baseUrl || /[\r\n]/.test(baseUrl)) {
          setProviderConfigDialogError("Base URL 不能为空且必须是单行文本。");
          return false;
        }
        const existingCodex = codexProviderDrafts.get(provider);
        if (envKey && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(envKey)) {
          setProviderConfigDialogError("请输入有效的用户环境变量名称。");
          return false;
        }
        if (!envKey && (isNew || (!isDefaultProvider && existingCodex?.originalEnvKey))) {
          setProviderConfigDialogError("请输入有效的用户环境变量名称。");
          return false;
        }
        const existing = settingsProviderDraft.providers[provider];
        if (isNew && existing) {
          setProviderConfigDialogError(`Provider ${provider} 已存在。`);
          return false;
        }
        if (
          !apiKey
          && (isNew || (isDefaultProvider && !existingCodex?.hasApiKey))
          && !existingCodex?.hasApiKey
        ) {
          setProviderConfigDialogError(
            isNew
              ? "新增供应商时请输入 API key。"
              : "默认 Codex App Provider 尚未配置 API key，请填写后再保存。",
          );
          return false;
        }
        // 默认供应商编辑时，先弹出重启确认框，由用户选择取消 / 稍后重启 / 立即重启。
        // 弹窗按钮会设置 providerRestartDecision 并再次调用本函数完成提交。
        if (isDefaultProvider && providerRestartDecision === null) {
          openProviderRestartConfirmDialog(provider);
          return;
        }
        const restartDecision = providerRestartDecision;
        providerRestartDecision = null;
        if (isNew) {
          const sourceProvider = String(sourceNode?.value || "").trim().toLowerCase();
          const sourceEntry = settingsProviderDraft.providers[sourceProvider];
          const sourceTable = sourceEntry?.settings?.model_prices
            || settings.default_model_prices
            || settings.model_prices;
          const includeInStats = layer.querySelector('input[name="codex-provider-scope"]:checked')?.value === "included";
          settingsProviderDraft.order.push(provider);
          settingsProviderDraft.providers[provider] = {
            enabled: includeInStats,
            notificationOnly: !includeInStats,
            settings: {
              model_prices: cloneProviderModelPrices(sourceTable, provider),
              pricing_url: "",
              weekly_adjustment_usd: 0,
            },
          };
          settingsProviderDraft.activeProvider = provider;
          window[settingsProviderName] = provider;
        } else if (!existing) {
          setProviderConfigDialogError(`Provider ${provider} 当前没有可编辑的价格草稿。`);
          return false;
        }
        codexProviderDrafts.set(provider, {
          providerId: provider,
          name: displayName,
          baseUrl,
          envKey,
          configText: sectionText,
          apiKey,
          isNew: isNew || existingCodex?.isNew === true,
          hasApiKey: !!apiKey || existingCodex?.hasApiKey === true,
          originalEnvKey: existingCodex?.originalEnvKey || "",
        });
        codexProviderDirty.add(provider);
        // 输入了自定义模型名时，把它一并写入该供应商的模型单价列表（单价默认 0）。
        const addedCustomModelPrice = addCustomModelPriceToDraft(
          provider,
          String(
            layer.querySelector('[data-provider-config-field="chat_model"]')?.value || "",
          ).trim(),
        );
        if (addedCustomModelPrice) settingsDirtyProviders.add(provider);
        closeSettingsConfirm();
        renderSettingsProviderEditor();
        renderSettingsProviderTabs();
        // 新增/编辑供应商后直接自动保存生效，无需再点设置页「保存」。
        if (isNew) {
          // 新增供应商时直接保存新价格，不再弹出「保存新价格」确认对话框。
          commitSettingsFromDraft(`正在保存供应商 ${displayName} 配置...`, {
            skipPricingDialog: true,
          });
          return true;
        }
        // 编辑供应商只保存 provider 配置（Base URL / 环境变量 / API key / config.toml 段），
        // 不提交 model 单价，避免触发价格变更校验或「保存新价格」确认流程。
        if (isDefaultProvider && restartDecision) {
          pendingProviderRestartAfterSave = restartDecision;
        }
        const submitted = submitSettingsCommand(
          { action: "save", codexProviders: collectCodexProviderUpdates() },
          `正在保存供应商 ${displayName} 配置...`,
        );
        if (submitted) {
          codexProviderDirty.clear();
          renderSettingsProviderTabs();
        }
        return true;
      }

      function addCustomModelPriceToDraft(provider, model) {
        // 把对话框中输入的「自定义模型名称」也写入该供应商的模型单价列表，
        // 单价默认全为 0，便于用户后续直接在此列表填写真实价格。
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        const normalizedModel = String(model || "").trim();
        if (!normalizedProvider || !normalizedModel) return false;
        const entry = settingsProviderDraft?.providers?.[normalizedProvider];
        if (!entry) return false;
        const table = entry.settings?.model_prices && typeof entry.settings.model_prices === "object"
          ? entry.settings.model_prices
          : {};
        const exists = Object.entries(table).some(([key, row]) => {
          const rowModel = String(row?.model || key || "").trim().toLowerCase();
          return rowModel === normalizedModel.toLowerCase();
        });
        if (exists) return false;
        entry.settings = {
          ...entry.settings,
          model_prices: {
            ...table,
            [normalizedModel]: {
              model: normalizedModel,
              input: 0,
              cached_input: 0,
              cache_write: 0,
              output: 0,
              reasoning: 0,
              provider: normalizedProvider,
            },
          },
        };
        return true;
      }

      function openProviderDeleteDialog(provider = "") {
        const dialog = settingsDialogRoot();
        if (!dialog || !settingsProviderDraft) return;
        const settings = hudSettingsFromPayload();
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (!normalizedProvider || normalizedProvider === settingsProviderDraft.appProvider) {
          setSettingsStatus("默认 Codex App Provider 不支持删除。", "error");
          return;
        }
        if (!settingsProviderDraft.providers?.[normalizedProvider]) {
          setSettingsStatus("找不到要删除的供应商。", "error");
          return;
        }
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.dataset.providerDeleteDialog = "true";
        layer.dataset.providerDeleteProvider = normalizedProvider;
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card codex-usage-hud-provider-delete-card" data-tone="danger" role="alertdialog" aria-modal="true" aria-label="删除供应商">
            <div class="codex-usage-hud-settings-confirm-title">删除供应商：${escapeHtml(providerDisplayName(settings, normalizedProvider))}？</div>
            <div class="codex-usage-hud-settings-confirm-body">默认会删除 config.toml 中该供应商的相关配置，引用它的 Provider profile，以及响应的 API key 用户环境变量。下面两项为可选删除内容，默认不勾选。</div>
            <div class="codex-usage-hud-provider-delete-options">
              <label><input type="checkbox" data-provider-delete-model-prices="true"><span>同时删除模型单价配置</span></label>
              <label><input type="checkbox" data-provider-delete-session-history="true"><span>同时删除会话历史记录</span></label>
            </div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-provider-delete-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-provider-delete-confirm" data-primary="true" data-danger="true">删除供应商</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
        layer.querySelector('[data-action="settings-provider-delete-confirm"]')?.focus?.();
      }

      function confirmProviderDeleteDialog() {
        const layer = document.querySelector(`#${settingsModalId} [data-provider-delete-dialog="true"]`);
        if (!layer) return false;
        const provider = String(layer.dataset.providerDeleteProvider || "").trim().toLowerCase();
        if (!provider || provider === settingsProviderDraft?.appProvider) {
          setSettingsStatus("默认 Codex App Provider 不支持删除。", "error");
          return false;
        }
        const deleteModelPrices = !!layer.querySelector('[data-provider-delete-model-prices="true"]')?.checked;
        const deleteSessionHistory = !!layer.querySelector('[data-provider-delete-session-history="true"]')?.checked;
        const requestId = typedSettingsRequestId("provider-delete");
        pricingWorkflowState.providerDeleteRequestId = requestId;
        pricingWorkflowState.providerDeleteProvider = provider;
        pricingWorkflowState.providerDeleteHasSessionHistory = deleteSessionHistory;
        openProviderDeleteLoading(requestId, provider, deleteSessionHistory);
        const submitted = submitSettingsCommand(
          {
            action: "deleteProvider",
            requestId,
            provider,
            deleteModelPrices,
            deleteSessionHistory,
          },
          deleteSessionHistory
            ? "正在删除供应商并安全清理会话历史..."
            : "正在删除供应商 config.toml 配置...",
          { preserveOverlay: true },
        );
        if (submitted) {
          settingsDirtyProviders.delete(provider);
          codexProviderDirty.delete(provider);
          renderSettingsProviderTabs();
        } else {
          clearProviderDeleteWorkflow();
          closeSettingsConfirm();
        }
        return submitted;
      }

      function typedSettingsRequestId(prefix) {
        return `${String(prefix || "request")}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
      }

      function refreshSessionIndexIfStale() {
        // Warm-index status is delivered via settingsCommandStatus responses
        // (sessionIndexStatus / sessionIndexControl). When the storage tab has
        // never seen a snapshot (fresh renderer or daemon restart), pull one
        // on open so the coverage banner and hints are not blank.
        const current = currentPayload();
        const cached = sessionCleanupState?.sessionIndex;
        const hasSnapshot = cached && typeof cached === "object" && Object.keys(cached).length > 0;
        const payloadSnapshot = current?.settingsCommandStatus?.sessionIndex
          || current?.sessionIndex;
        const hasPayloadSnapshot = payloadSnapshot
          && typeof payloadSnapshot === "object"
          && Object.keys(payloadSnapshot).length > 0;
        if (sessionCleanupState?.sessionIndexUiAttached === true && (hasSnapshot || hasPayloadSnapshot)) return;
        if (sessionCleanupState?.sessionIndexRefreshing) return;
        sessionCleanupState.sessionIndexUiAttached = true;
        sessionCleanupState.sessionIndexRefreshing = true;
        const requestId = typedSettingsRequestId("session-index-status");
        const submitted = submitSettingsCommand(
          { action: "sessionIndexStatus", requestId },
          "正在读取搜索索引状态...",
          { preserveOverlay: true, quiet: true },
        );
        if (!submitted) {
          sessionCleanupState.sessionIndexRefreshing = false;
          sessionCleanupState.sessionIndexUiAttached = false;
        }
        ctx.lifecycle.timeout("session_index_status_watchdog", () => {
          sessionCleanupState.sessionIndexRefreshing = false;
          if (sessionCleanupState.sessionIndexUiAttached === true && !hasSnapshot && !hasPayloadSnapshot) {
            sessionCleanupState.sessionIndexUiAttached = false;
          }
        }, 6000);
      }

      function detachSessionIndexUi() {
        if (sessionCleanupState?.sessionIndexUiAttached !== true) return false;
        sessionCleanupState.sessionIndexUiAttached = false;
        sessionCleanupState.sessionIndexRefreshing = false;
        const requestId = typedSettingsRequestId("session-index-detach");
        return submitSettingsCommand(
          { action: "sessionIndexControl", requestId, control: "cancel_ui" },
          "",
          { preserveOverlay: true, quiet: true },
        );
      }

        function renderSettingsModal(tab = "settings", status = "", { resetProviderDraft = false } = {}) {
        const root = document.getElementById(rootId);
        const modal = document.getElementById(settingsModalId);
        if (!root || !modal) return;
        const activeTab = ["storage", "backgroundUsage", "support", "about"].includes(tab) ? tab : "settings";
        if (settingsActiveTab === "storage" && activeTab !== "storage") {
          detachSessionIndexUi();
        }
        const preserveSecondaryLayers = !modal.hidden
          && settingsActiveTab === activeTab
          && !resetProviderDraft;
        if (!preserveSecondaryLayers && sessionTransferState.open) {
          closeSessionTransferDialog();
        }
        const preservedSecondaryLayers = preserveSecondaryLayers
          ? Array.from(modal.querySelectorAll(
            ".codex-usage-hud-settings-dialog > .codex-usage-hud-settings-confirm-layer, "
            + ".codex-usage-hud-settings-dialog > .codex-usage-hud-codex-cli-layer"
          ))
          : [];
        const sessionCleanupConfirmToken = String(
          modal.querySelector('[data-session-cleanup-confirm="true"]')?.dataset.sessionCleanupConfirmToken || "",
        );
        if (!modal.hidden) {
          captureSettingsProviderForm();
          if (settingsActiveTab === "backgroundUsage") {
            captureBackgroundUsageScrollPositions();
          }
          if (settingsActiveTab === "storage") captureStorageUiState();
        }
        const settings = hudSettingsFromPayload();
        settingsActiveTab = activeTab;
        autoSavedSettingsSignature = JSON.stringify(settingsWithPersistedPriceTables(settings, settings));
        // 重新打开设置界面时重置粘性错误，避免上一轮的旧错误残留到新一轮。
        settingsStatusErrorSticky = false;
        writeSettingsUiState(true, activeTab);
        if (activeTab === "settings") ensureSettingsProviderDraft(settings, resetProviderDraft);
        const path = settingsPathLabel();
        const bridge = settingsBridgeUrl();
        const defaultStatus = activeTab === "about"
          ? "可检查 GitHub Release 并启动 Windows 安装器。"
          : activeTab === "storage"
            ? "扫描和永久删除仅在用户明确操作时执行。"
            : activeTab === "backgroundUsage"
            ? "Tokens 来自本机日志；费用均为 HUD 估算。"
            : (bridge ? "设置将保存到本地配置文件" : "设置桥接未连接，无法保存本地配置文件");
        modal.innerHTML = `
          <div class="codex-usage-hud-settings-dialog" data-active-tab="${escapeHtml(activeTab)}" role="dialog" aria-modal="true" aria-label="codex-usage-hud 设置">
            <div class="codex-usage-hud-settings-head">
              <div class="codex-usage-hud-settings-title">codex-usage-hud v${escapeHtml(appVersion())}</div>
              <button type="button" class="codex-usage-hud-settings-close" data-action="settings-close" aria-label="关闭">×</button>
            </div>
            <div class="codex-usage-hud-settings-tabs" role="tablist">
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="settings" data-active="${activeTab === "settings"}">设置</button>
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="storage" data-active="${activeTab === "storage"}">会话管理</button>
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="backgroundUsage" data-active="${activeTab === "backgroundUsage"}">用量总览</button>
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="support" data-active="${activeTab === "support"}">请作者喝咖啡</button>
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="about" data-active="${activeTab === "about"}">版本更新</button>
            </div>
            <div class="codex-usage-hud-settings-body">
              ${activeTab === "support" ? supportPanelHtml(settings, path) : activeTab === "about" ? aboutPanelHtml(path) : activeTab === "storage" ? storagePanelHtml() : activeTab === "backgroundUsage" ? backgroundUsagePanelHtml() : settingsPanelHtml(settings, bridge, path)}
            </div>
            <div class="codex-usage-hud-settings-actions">
              <div class="codex-usage-hud-settings-status" data-settings-status="true">${escapeHtml(status || defaultStatus)}</div>
              <div>
                ${activeTab === "settings" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-restart" hidden>立即重启 HUD</button>' : activeTab === "backgroundUsage" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="background-usage-refresh">刷新</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-close" data-primary="true">关闭</button>' : activeTab === "about" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-check-update">检查更新</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-install-update" data-primary="true">安装更新</button>' : '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-close" data-primary="true">关闭</button>'}
              </div>
            </div>
          </div>
        `;
        const nextDialog = modal.querySelector(".codex-usage-hud-settings-dialog");
        if (nextDialog) {
          for (const layer of preservedSecondaryLayers) nextDialog.appendChild(layer);
        }
        modal.hidden = false;
        if (activeTab === "settings") {
          syncSettingsProviderTabNavigation(nextDialog);
          revealSettingsProviderTab(nextDialog?.querySelector('[data-provider-tab="true"][aria-selected="true"]'));
          syncSettingsProviderTabNavigation(nextDialog);
        }
        ensureRestReminderCountdownTicker();
        ensureSessionCleanupElapsedTicker();
        updateAboutActionButtons(currentUpdateState());
        if (activeTab === "storage") {
          restoreStorageUiState();
          restoreSessionCleanupConfirm(sessionCleanupConfirmToken);
          requestSessionCleanupWorkdirOptions({ force: true });
          refreshSessionIndexIfStale();
          if (
            String(sessionCleanupState.search || "").trim()
            && !sessionCleanupState.searchRequestId
            && sessionCleanupState.searchResultState !== "completed"
          ) {
            requestSessionCleanupSearch(sessionCleanupState.search);
          }
        }
        if (activeTab === "backgroundUsage") {
          restoreBackgroundUsageScrollPositions();
          if (backgroundUsageSessionRankingMode()) {
            void loadBackgroundUsage();
          } else {
            const revision = Math.max(0, Number(currentPayload()?.backgroundUsageRevision || 0));
            if (!backgroundUsageState.data || backgroundUsageState.loadedRevision !== revision) {
              void loadBackgroundUsage({ force: true });
            } else if (backgroundUsageState.selectedEventId && !backgroundUsageState.detail) {
              // Keep default markViewed=false here: explicit list click and the open
              // notification path pass markViewed:true themselves.
              void loadBackgroundUsageDetail(backgroundUsageState.selectedEventId);
            }
          }
        }
      }

      function restoreOpenSettingsModal() {
        const modal = document.getElementById(settingsModalId);
        const settingsUiState = readSettingsUiState();
        if (!modal || !modal.hidden || settingsUiState?.open !== true) return false;
        renderSettingsModal(String(settingsUiState.tab || settingsActiveTab));
        return true;
      }
      // 供会话内检索浮窗的「返回会话管理」按钮调用：直接打开设置页到会话管理标签。
      window.__codexUsageHudOpenSessionCleanup = (tab = "storage") => renderSettingsModal(tab);

      function settingsPanelHtml(settings, bridge, path) {
        ensureSettingsProviderDraft(settings);
        const overlaySelectableMax = workOverlaySelectableMax();
        const overlayValue = Math.min(
          overlaySelectableMax,
          Math.max(0, Math.round(Number(settings.work_overlay_max_items) || 0)),
        );
        const overlayOptions = Array.from({ length: overlaySelectableMax + 1 }, (_, index) => `
          <option value="${index}" ${overlayValue === index ? "selected" : ""}>${index}${index === 0 ? " - 不启用" : ""}</option>
        `).join("");
        const overlaySide = String(settings.work_overlay_side || "right").toLowerCase() === "left"
          ? "left"
          : "right";
        const overlaySideOptions = `
          <option value="right" ${overlaySide === "right" ? "selected" : ""}>右侧</option>
          <option value="left" ${overlaySide === "left" ? "selected" : ""}>左侧</option>
        `;
        const weekdayOptions = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
          .map((label, index) => `<option value="${index}" ${Number(settings.weekly_reset_weekday) === index ? "selected" : ""}>${label}</option>`)
          .join("");
        return `
          <div class="codex-usage-hud-settings-grid">
            <div class="codex-usage-hud-settings-field">
              <label>日额度 USD</label>
              <input data-setting-key="daily_budget_usd" type="number" min="0" step="0.01" value="${escapeHtml(settings.daily_budget_usd)}">
            </div>
            <div class="codex-usage-hud-settings-field">
              <label>周额度 USD</label>
              <input data-setting-key="weekly_budget_usd" type="number" min="0" step="0.01" value="${escapeHtml(settings.weekly_budget_usd)}">
            </div>
            <div class="codex-usage-hud-settings-field">
              <label>日额度重置时间</label>
              <input data-setting-key="daily_reset_time" type="time" value="${escapeHtml(settings.daily_reset_time)}">
            </div>
            <div class="codex-usage-hud-settings-field">
              <label>周额度重置</label>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
                <select data-setting-key="weekly_reset_weekday">${weekdayOptions}</select>
                <input data-setting-key="weekly_reset_time" type="time" value="${escapeHtml(settings.weekly_reset_time)}">
              </div>
            </div>
            <div class="codex-usage-hud-settings-compact-row">
              <div class="codex-usage-hud-settings-compact-inputs">
                <div class="codex-usage-hud-settings-field">
                  <label>超额提醒阈值</label>
                  <input data-setting-key="budget_thresholds" value="${escapeHtml(thresholdText(settings))}" placeholder="50,80,100">
                </div>
                <div class="codex-usage-hud-settings-field">
                  <label>会话进度气泡数量（0 为关闭）</label>
                  <select data-setting-key="work_overlay_max_items">${overlayOptions}</select>
                </div>
              </div>
              <div class="codex-usage-hud-settings-field">
                <label>气泡运行环境</label>
                <div class="codex-usage-hud-overlay-runtime-row">
                  <div data-desktop-overlay-dependency="true">${desktopOverlayDependencyHtml()}</div>
                  <label class="codex-usage-hud-overlay-side-control" title="选择会话进度气泡显示在屏幕哪一侧">
                    <select data-setting-key="work_overlay_side" aria-label="气泡位置">${overlaySideOptions}</select>
                  </label>
                </div>
              </div>
            </div>
            <div class="codex-usage-hud-provider-editor" data-provider-editor="true" data-active-provider="${escapeHtml(settingsProviderDraft?.activeProvider || "")}">
              ${settingsProviderEditorHtml(settings)}
            </div>
            <div class="codex-usage-hud-settings-footnote">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-exit" data-variant="ghost">退出 HUD</button>
              <label class="codex-usage-hud-settings-lock-toggle" title="默认锁屏/睡眠时已自动暂停对 Codex 的一切活动（HUD 保留，解锁恢复）。勾选此项则完全退出 HUD 进程，解锁后由守护重启">
                <input type="checkbox" data-setting-key="stop_hud_on_lock_screen" ${settings.stop_hud_on_lock_screen ? "checked" : ""} aria-label="锁屏后完全退出HUD进程">
                <span>锁屏后完全退出HUD进程</span>
              </label>
              <div class="codex-usage-hud-settings-status">配置文件：${escapeHtml(path || "未提供")} ${bridge ? "" : "（桥接未连接）"}</div>
            </div>
          </div>
        `;
      }

      function restReminderStatusTitle(state, configEnabled) {
        if (!configEnabled) return "未开启";
        switch (String(state || "")) {
          case "work": return "专注中";
          case "prompt": return "等待选择";
          case "postponed": return "已延迟";
          case "resting":
          case "break": return "休息中";
          case "lunch": return "午休";
          case "away": return "离开中";
          case "off": return "非工作时段";
          case "disabled": return "未开启";
          default: return "专注中";
        }
      }

"""

_TEXT_SUFFIX = r"""      // 状态栏是否正在展示一条「粘性错误」。为 true 时，兜底刷新（后台每轮
      // 渲染都会清空 settings_command_status，下一轮只带来 updateState 文案）
      // 不得覆盖当前错误文本，只有真实的新结果或用户操作才会清除，从而
      // 让错误信息显示久一点，不被后来的信息刷掉。
      let settingsStatusErrorSticky = false;
      let settingsRestartPending = false;
      let settingsRestartCodex = false;
      // 默认供应商编辑时，用户在重启确认弹窗中做出的决策：
      // null = 尚未决策（点击「应用」时弹出确认框）；
      // "later" = 稍后重启（保存但不重启）；
      // "now" = 立即重启（保存后自动重启 Codex Desktop）。
      let providerRestartDecision = null;
      // 保存提交后待执行的重启动作，由 applySettingsCommandStatus 在保存成功后消费。
      let pendingProviderRestartAfterSave = null;

      function setSettingsStatus(text, kind = "") {
        const node = document.querySelector(`#${settingsModalId} [data-settings-status="true"]`);
        if (!node) return;
        text = String(text || "");
        kind = String(kind || "");
        if (text && kind === "error") {
          settingsStatusErrorSticky = true;
        } else {
          settingsStatusErrorSticky = false;
        }
        node.textContent = text;
        node.dataset.kind = kind;
      }

      function setSettingsRestartVisible(visible, { codex = false } = {}) {
        const node = document.querySelector(`#${settingsModalId} [data-action="settings-restart"]`);
        if (!node) return;
        node.hidden = !visible;
        node.dataset.restartTarget = codex ? "codex" : "hud";
        node.textContent = codex ? "立即重启 Codex Desktop" : "立即重启 HUD";
      }

      function clearSettingsRestartPrompt() {
        settingsRestartPending = false;
        settingsRestartCodex = false;
        setSettingsRestartVisible(false);
      }

      function setSettingsActionState(actionName, { label = "", disabled = false } = {}) {
        const node = document.querySelector(`#${settingsModalId} [data-action="${actionName}"]`);
        if (!(node instanceof HTMLButtonElement)) return;
        if (label) node.textContent = label;
        node.disabled = !!disabled;
      }

      function formatRestReminderClock(milliseconds) {
        if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "--:--:--";
        return new Date(milliseconds).toLocaleTimeString("zh-CN", {
          hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
        });
      }

      function formatRestReminderInputTime(milliseconds) {
        const value = Number.isFinite(milliseconds) && milliseconds > 0
          ? new Date(milliseconds)
          : new Date();
        return [value.getHours(), value.getMinutes()]
          .map((item) => String(item).padStart(2, "0"))
          .join(":");
      }

      function formatRestReminderRemaining(seconds) {
        if (!Number.isFinite(seconds) || seconds < 0) return "--:--:--";
        const total = Math.max(0, Math.ceil(seconds));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const remainder = total % 60;
        return [hours, minutes, remainder].map((value) => String(value).padStart(2, "0")).join(":");
      }

      function restReminderSummarySeconds(timing, now = Date.now()) {
        const completed = Math.max(0, Number(timing?.completedTodaySeconds) || 0);
        const saved = Math.max(0, Number(timing?.todayRestedSeconds) || 0);
        const state = String(timing?.state || "");
        if (state !== "resting" && state !== "break") return Math.max(saved, completed);
        const started = Number(timing?.restStartedAtMs) || 0;
        const ends = Number(timing?.restEndsAtMs || timing?.breakEndsAtMs) || 0;
        const elapsed = started > 0
          ? Math.max(0, (Math.min(now, ends || now) - started) / 1000)
          : Math.max(0, Number(timing?.currentRestElapsedSeconds) || 0);
        return Math.max(saved, completed + elapsed);
      }

      function restReminderSummaryCount(timing) {
        const saved = Math.max(0, Math.round(Number(timing?.todayRestedCount) || 0));
        const completed = Math.max(0, Math.round(Number(timing?.completedTodayCount) || 0));
        const state = String(timing?.state || "");
        const active = (state === "resting" || state === "break")
          && Number(timing?.restStartedAtMs) > 0;
        return Math.max(saved, completed + (active ? 1 : 0));
      }

      function restReminderSummaryText(timing, now = Date.now()) {
        return `今日已休息 ${formatRestReminderRemaining(restReminderSummarySeconds(timing, now))} 共${restReminderSummaryCount(timing)}次`;
      }

      function syncRestReminderCountdown() {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "support") return;
        const timing = currentPayload()?.restReminder;
        const configEnabled = !!timing?.enabled;
        const running = configEnabled && timing?.running !== false;
        const state = String(timing?.state || (configEnabled ? "work" : "disabled"));
        const statusBox = modal.querySelector('.codex-usage-hud-rest-reminder-status');
        const statusTitle = modal.querySelector('[data-rest-reminder-status-title="true"]');
        const remaining = modal.querySelector('[data-rest-reminder-remaining="true"]');
        const summary = modal.querySelector('[data-rest-reminder-summary="true"]');
        const startInput = modal.querySelector('[data-rest-reminder-start-time="true"]');
        if (statusBox) statusBox.dataset.state = state;
        if (statusTitle) statusTitle.textContent = restReminderStatusTitle(state, configEnabled);
        if (summary) {
          const summaryText = restReminderSummaryText(timing);
          summary.textContent = summaryText;
          summary.title = summaryText;
        }
        if (startInput && startInput.dataset.userEdited !== "true") {
          const nextStart = formatRestReminderInputTime(Number(timing?.timerStartedAtMs));
          if (startInput.value !== nextStart) startInput.value = nextStart;
        }
        if (remaining) {
          if (state === "resting" || state === "break") {
            remaining.textContent = formatRestReminderRemaining(
              (Number(timing?.restEndsAtMs || timing?.breakEndsAtMs) - Date.now()) / 1000,
            );
          } else if (state === "prompt") {
            remaining.textContent = timing?.promptWaitInfinite === true
              ? "等待你的选择"
              : formatRestReminderRemaining(
                (Number(timing?.promptEndsAtMs) - Date.now()) / 1000,
              );
          } else if (state === "postponed") {
            remaining.textContent = formatRestReminderRemaining(
              (Number(timing?.postponeEndsAtMs) - Date.now()) / 1000,
            );
          } else {
            remaining.textContent = running
              ? formatRestReminderRemaining((Number(timing?.nextReminderAtMs) - Date.now()) / 1000)
              : "--:--:--";
          }
        }
      }

      function ensureRestReminderCountdownTicker() {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "support") {
          if (restReminderCountdownTimer) {
            ctx.lifecycle.clearInterval(restReminderCountdownTimer);
            restReminderCountdownTimer = 0;
          }
          return;
        }
        syncRestReminderCountdown();
        if (!restReminderCountdownTimer) {
          restReminderCountdownTimer = ctx.lifecycle.interval(
            "rest_reminder_settings",
            syncRestReminderCountdown,
            1000,
          );
        }
      }

      function updateAboutActionButtons(state) {
        const phase = String(state?.phase || "");
        const progressText = String(state?.progressText || "").trim();
        let checkLabel = "检查更新";
        let installLabel = "安装更新";
        let disableCheck = false;
        let disableInstall = false;
        if (phase === "checking") {
          checkLabel = "检查中...";
          installLabel = "请稍候";
          disableCheck = true;
          disableInstall = true;
        } else if (phase === "downloading") {
          installLabel = progressText ? `下载中 ${progressText}` : "下载中...";
          disableCheck = true;
          disableInstall = true;
        } else if (phase === "ready") {
          installLabel = "打开安装器";
        }
        setSettingsActionState("settings-check-update", {
          label: checkLabel,
          disabled: disableCheck,
        });
        setSettingsActionState("settings-install-update", {
          label: installLabel,
          disabled: disableInstall,
        });
      }

      function showSettingsRestartPrompt(message, kind = "error", { codex = false } = {}) {
        settingsRestartPending = true;
        settingsRestartCodex = codex;
        setSettingsStatus(
          `${message} 是否立即重启${codex ? " Codex Desktop" : " HUD"}？关闭窗口可稍后重启。`,
          kind,
        );
        setSettingsRestartVisible(true, { codex });
      }

      // 默认供应商编辑时，点击「应用」后弹出的重启确认框。
      // 三个选项：取消（不保存）、稍后重启（保存不重启）、立即重启（保存并重启）。
      function openProviderRestartConfirmDialog(provider = "") {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.dataset.providerRestartDialog = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="重启 Codex Desktop 确认">
            <div class="codex-usage-hud-settings-confirm-kicker">Codex Desktop</div>
            <div class="codex-usage-hud-settings-confirm-title">重启 Codex Desktop 以生效？</div>
            <div class="codex-usage-hud-settings-confirm-body">修改默认 Codex App Provider${provider ? `（${escapeHtml(providerDisplayName(hudSettingsFromPayload(), provider))}）` : ""}的 Base URL / API key 后，需要重启 Codex Desktop 才能加载新配置。\n\n选择操作方式：</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-provider-restart-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-provider-restart-later">稍后重启</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-provider-restart-now" data-primary="true">立即重启</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
        layer.querySelector('[data-action="settings-provider-restart-now"]')?.focus?.();
      }

      function closeProviderRestartConfirmDialog() {
        const layer = document.querySelector(`#${settingsModalId} [data-provider-restart-dialog="true"]`);
        if (layer) layer.remove();
      }

      // 弹窗按钮回调：设置重启决策后重新执行 applyProviderConfigDialog 完成保存。
      function applyProviderConfigWithRestartDecision(decision) {
        providerRestartDecision = decision;
        closeProviderRestartConfirmDialog();
        applyProviderConfigDialog();
      }

      function setSettingsLoadingText({ kicker = "", title = "", body = "" } = {}) {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"][data-loading-mode]`);
        if (!layer) return;
        const kickerNode = layer.querySelector(".codex-usage-hud-settings-confirm-kicker");
        const titleNode = layer.querySelector(".codex-usage-hud-settings-confirm-title");
        const bodyNode = layer.querySelector(".codex-usage-hud-settings-confirm-body");
        if (kickerNode) kickerNode.textContent = String(kicker || "");
        if (titleNode) titleNode.textContent = String(title || "");
        if (bodyNode) bodyNode.textContent = String(body || "");
      }

      function syncSettingsUpdateLoading(payload) {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"][data-loading-mode]`);
        if (!layer) return;
        const mode = String(layer.dataset.loadingMode || "");
        const state = updateStateFromPayload(payload);
        const phase = String(state?.phase || "");
        const progressText = String(state?.progressText || "").trim();
        if (mode === "check-update") {
          if (phase === "checking") {
            setSettingsLoadingText({
              kicker: "正在检查",
              title: "正在检查更新",
              body: "HUD daemon 正在查询 GitHub Release。通常只需 1 到 3 秒。",
            });
            return;
          }
          closeSettingsConfirm();
          return;
        }
        if (mode === "install-update") {
          if (phase === "checking") {
            setSettingsLoadingText({
              kicker: "正在准备",
              title: "正在检查并准备安装更新",
              body: "HUD daemon 正在查询 GitHub Release，并准备下载安装包。",
            });
            return;
          }
          if (phase === "downloading") {
            setSettingsLoadingText({
              kicker: "正在下载",
              title: "正在下载安装更新",
              body: progressText
                ? `当前进度：${progressText}\n\n下载完成后会自动启动安装器。`
                : "正在下载 Windows 安装包。\n\n下载完成后会自动启动安装器。",
            });
            return;
          }
          closeSettingsConfirm();
        }
      }

      function pricingArtifactSeen(status, suffix) {
        const requestId = String(status?.requestId || "");
        const action = String(status?.action || "pricing");
        const key = `${requestId || action}:${suffix}`;
        if (pricingWorkflowState.handledArtifacts.has(key)) return true;
        pricingWorkflowState.handledArtifacts.set(key, true);
        while (pricingWorkflowState.handledArtifacts.size > 64) {
          const oldest = pricingWorkflowState.handledArtifacts.keys().next().value;
          pricingWorkflowState.handledArtifacts.delete(oldest);
        }
        return false;
      }

      function applyPricingCommandStatus(status) {
        if (!status || typeof status !== "object") return;
        const action = String(status.action || "");
        if (status.pricingSource && settingsProviderDraft) {
          const source = status.pricingSource;
          const provider = String(settingsProviderDraft.activeProvider || "").trim().toLowerCase();
          const sync = { ...(hudSettingsFromPayload().pricing_sync || {}) };
          sync.last_checked_at = String(source.checked_at || "");
          sync.last_success_at = sync.last_checked_at;
          sync.last_result = "success";
          sync.source_url = String(source.source_url || sync.source_url || "");
          sync.unread_change_count = Number(status.pricingPreview?.updatedCount || status.pricingPreview?.addedCount || 0);
          const settings = hudSettingsFromPayload();
          settings.pricing_sync = sync;
          if (!provider) settings.pricing_sync = sync;
        }
        if (String(status.kind || "") === "error") return;
        if (
          status.pricingPreview
          && ["pricingImportPreview", "fetchPricesPreview"].includes(action)
          && !pricingArtifactSeen(status, "preview")
        ) {
          openPricingImportPreview(status.pricingPreview, status.pricingPayload);
        }
        if (
          status.pricingPath
          && action === "pricingExport"
          && !pricingArtifactSeen(status, "export")
        ) {
          openPricingExportPrompt(status);
        }
        if (action === "savePricing") {
          settingsDirtyProviders.clear();
          codexProviderDirty.clear();
          renderSettingsProviderTabs();
          syncPricingApplyDirtyState();
        }
      }

      const sessionTransferPageSize = 50;
      let sessionTransferElapsedTimer = 0;

      function sessionTransferProviderTargets(settings, sourceProvider) {
        const draft = ensureSettingsProviderDraft(settings);
        const source = String(sourceProvider || "").trim().toLowerCase();
        const registry = settings?.provider_registry && typeof settings.provider_registry === "object"
          ? settings.provider_registry
          : {};
        const providerSettings = settings?.provider_settings && typeof settings.provider_settings === "object"
          ? settings.provider_settings
          : {};
        return (draft?.order || []).filter((provider) => {
          const normalized = String(provider || "").trim().toLowerCase();
          if (!normalized || normalized === source || normalized === "unknown") return false;
          const detail = registry[normalized] && typeof registry[normalized] === "object"
            ? registry[normalized]
            : {};
          if (detail.historicalOnly === true) return false;
          const profiles = Array.isArray(detail.profiles) && detail.profiles.length > 0;
          return normalized === String(draft?.appProvider || "").trim().toLowerCase()
            || detail.defined === true
            || profiles
            || Object.prototype.hasOwnProperty.call(providerSettings, normalized);
        });
      }

      function normaliseSessionTransferTargetProvider(settings, sourceProvider) {
        const targets = sessionTransferProviderTargets(settings, sourceProvider);
        const current = String(sessionTransferState.targetProvider || "").trim().toLowerCase();
        const next = targets.find((provider) => (
          String(provider || "").trim().toLowerCase() === current
        )) || targets[0] || "";
        sessionTransferState.targetProvider = String(next || "").trim();
        return targets;
      }

      function sessionTransferDataFromPayload() {
        return sessionCleanupFromPayload();
      }

      function sessionTransferCompletedSourceIds(operation = sessionTransferOperation()) {
        const action = String(operation?.action || "").trim().toLowerCase();
        const mode = String(operation?.mode || "").trim().toLowerCase();
        const state = String(operation?.state || "").trim().toLowerCase();
        if (
          action !== "sessiontransfer"
          || mode !== "migrate"
          || !new Set(["completed", "partial", "failed"]).has(state)
          || !Array.isArray(operation?.results)
        ) return new Set();
        return new Set(
          operation.results
            .filter((item) => item?.targetVisible === true && item?.targetResumable === true)
            .map((item) => String(item?.id || "").trim())
            .filter(Boolean),
        );
      }

      function sessionTransferRows(data = sessionCleanupFromPayload()) {
        const source = String(sessionTransferState.sourceProvider || "").trim().toLowerCase();
        if (!source) return [];
        const completedSourceIds = sessionTransferCompletedSourceIds();
        return sessionCleanupRows(data, {
          search: String(sessionTransferState.search || ""),
          dateStart: "",
          dateEnd: "",
          archive: "all",
          availability: "all",
          clientKind: "all",
          // Provider values are normalized by the scan core, but keep this
          // surface case-insensitive so old inventories remain selectable.
          modelProvider: "all",
          sort: "recent",
        }).filter((item) => (
          (item?.transferable !== false || item?.archived === true)
          && !completedSourceIds.has(String(item?.id || "").trim())
          // A verified target copy already exists for this source; its
          // deletion is still pending, so do not offer a duplicate copy.
          // (The source stays listed in storage management for deletion.)
          && item?.pendingSourceCleanup !== true
          && String(item?.modelProvider || "").trim().toLowerCase() === source
        )).map((item) => item?.archived === true
          ? {
            ...item,
            selectable: false,
            transferBlockedReason: "会话已归档，请先解除归档后再复制或迁移。",
          }
          : item);
      }

      function sessionTransferView(data = sessionCleanupFromPayload()) {
        const allRows = sessionTransferRows(data);
        const totalPages = Math.max(1, Math.ceil(allRows.length / sessionTransferPageSize));
        const page = Math.min(
          Math.max(0, Number(sessionTransferState.page || 0)),
          totalPages - 1,
        );
        sessionTransferState.page = page;
        const rows = allRows.slice(
          page * sessionTransferPageSize,
          (page + 1) * sessionTransferPageSize,
        );
        const selectableIds = allRows
          .filter((item) => item?.selectable === true)
          .map((item) => String(item?.id || "").trim())
          .filter(Boolean);
        const selectableSet = new Set(selectableIds);
        const selected = new Set(
          Array.from(sessionTransferState.selectedIds).filter((id) => selectableSet.has(id)),
        );
        if (selected.size !== sessionTransferState.selectedIds.size) {
          sessionTransferState.selectedIds = selected;
        }
        return {
          rows,
          allRows,
          selectableIds,
          selectableSet,
          selected,
          page,
          totalPages,
        };
      }

      function sessionTransferSelectableIds(data = sessionCleanupFromPayload()) {
        return new Set(sessionTransferView(data).selectableIds);
      }

      function syncSessionTransferSelection(input) {
        const id = String(input?.dataset?.sessionTransferId || "").trim();
        if (!id) return false;
        if (input.checked) sessionTransferState.selectedIds.add(id);
        else sessionTransferState.selectedIds.delete(id);
        syncSessionTransferDialogControls();
        return true;
      }

      function syncSessionTransferSelectAll(input) {
        const selectedIds = sessionTransferSelectableIds();
        for (const id of selectedIds) {
          if (input?.checked) sessionTransferState.selectedIds.add(id);
          else sessionTransferState.selectedIds.delete(id);
        }
        syncSessionTransferDialogControls();
        return true;
      }

      function moveSessionTransferPage(direction) {
        const view = sessionTransferView();
        const delta = Number(direction || 0) < 0 ? -1 : 1;
        const nextPage = Math.min(
          Math.max(0, view.page + delta),
          view.totalPages - 1,
        );
        if (nextPage === view.page) return false;
        sessionTransferState.page = nextPage;
        renderSessionTransferDialog();
        return true;
      }

      function sessionTransferOperation() {
        const operation = sessionTransferState.operation && typeof sessionTransferState.operation === "object"
          ? sessionTransferState.operation
          : sessionTransferState.data?.operation;
        if (!operation || typeof operation !== "object") return {};
        if (String(operation?.action || "").toLowerCase() !== "sessiontransfer") return operation;
        if (!sessionTransferOperationMatchesCurrentPair(operation)) return {};
        return operation;
      }

      function sessionTransferOperationMatchesCurrentPair(operation) {
        const source = String(sessionTransferState.sourceProvider || "").trim().toLowerCase();
        const target = String(sessionTransferState.targetProvider || "").trim().toLowerCase();
        const operationSource = String(operation?.sourceProvider || "").trim().toLowerCase();
        const operationTarget = String(operation?.targetProvider || "").trim().toLowerCase();
        return !!source && !!target && !!operationSource && !!operationTarget
          && operationSource === source
          && operationTarget === target;
      }

      function sessionTransferBusy(operation = sessionTransferOperation()) {
        const state = String(operation?.state || "").toLowerCase();
        const sharedScanRequestId = String(activeSessionCleanupScanRequestId() || "").trim();
        const cancelledRequestId = String(sessionTransferState.cancelledRequestId || "").trim();
        return !!sessionTransferState.scanRequestId
          || !!sessionTransferState.requestId
          || (!!sharedScanRequestId && sharedScanRequestId !== cancelledRequestId)
          || new Set(["scanning", "accepted", "running"]).has(state);
      }

      function sessionTransferProgressModel(
        data = sessionCleanupFromPayload(),
        operation = sessionTransferOperation(),
      ) {
        const dataValue = data && typeof data === "object" ? data : {};
        const operationValue = operation && typeof operation === "object" ? operation : {};
        const action = String(operationValue?.action || "").toLowerCase();
        const state = String(operationValue?.state || "").toLowerCase();
        const activeStates = new Set(["scanning", "accepted", "running"]);
        const cancelledRequestId = String(sessionTransferState.cancelledRequestId || "").trim();
        const sharedScanRequestId = String(activeSessionCleanupScanRequestId(dataValue) || "").trim();
        const transferOperationActive = !!sessionTransferState.requestId
          || (action === "sessiontransfer" && activeStates.has(state));
        const scanning = !!sessionTransferState.scanRequestId
          || (!!sharedScanRequestId && sharedScanRequestId !== cancelledRequestId)
          || (new Set(["scan", "sessioncleanupscan"]).has(action) && activeStates.has(state));
        const scanOnly = scanning && !transferOperationActive;
        const transferring = transferOperationActive;
        const active = scanning || transferring;
        const mode = String(
          operationValue?.mode || sessionTransferState.mode || "copy",
        ).toLowerCase() === "migrate" ? "migrate" : "copy";
        const operationStartedAt = Math.max(0, Number(operationValue?.startedAt || 0));
        let startedAt = scanning
          ? Number(sessionCleanupState.scanStartedAt || 0)
          : Math.max(
            Number(sessionTransferState.startedAt || 0),
            operationStartedAt,
          );
        if (active && !startedAt) {
          startedAt = Date.now();
          if (scanning) sessionCleanupState.scanStartedAt = startedAt;
          else sessionTransferState.startedAt = startedAt;
        }
        const rawProgress = Math.max(0, Math.min(100, Number(operationValue?.progress || 0)));
        const progress = active ? Math.min(99, rawProgress) : rawProgress;
        const selectedIds = Array.isArray(operationValue?.selectedIds)
          ? operationValue.selectedIds
          : [];
        const total = Math.max(
          0,
          Number(operationValue?.sessionCount || selectedIds.length || sessionTransferState.selectedIds.size || 0),
        );
        const completed = Math.max(0, Number(operationValue?.completedCount || 0));
        const targetReady = Math.max(
          0,
          Number(operationValue?.targetReadyCount ?? operationValue?.copiedCount ?? 0),
        );
        const migrated = Math.max(0, Number(operationValue?.migratedCount || 0));
        const retained = Math.max(0, Number(operationValue?.sourceRetainedCount || 0));
        const sourceCleanupCompleted = Math.max(
          0,
          Number(operationValue?.sourceCleanupCompletedCount || migrated),
        );
        const sourceCleanupTotal = Math.max(
          0,
          Number(operationValue?.sourceCleanupTotalCount || total),
        );
        const elapsed = formatSessionCleanupElapsed(startedAt);
        const fallbackPhaseCount = mode === "migrate" ? 2 : 1;
        const phaseLabel = scanOnly
          ? sessionCleanupPhaseLabel(operationValue)
          : String(
            operationValue?.phaseLabel
              || (mode === "migrate" ? "复制目标并安全清理源会话" : "复制目标会话"),
          );
        const phaseCount = scanOnly
          ? Math.max(1, Number(operationValue?.phaseCount || 3))
          : Math.max(1, Number(operationValue?.phaseCount || fallbackPhaseCount));
        const phaseIndex = scanOnly
          ? Math.max(1, Number(operationValue?.phaseIndex || 1))
          : Math.min(
            phaseCount,
            Math.max(
              1,
              Number(
                operationValue?.phaseIndex
                  || (mode === "migrate" && completed >= total && total > 0 ? 2 : 1),
              ),
            ),
          );
        const progressMetaPrefix = scanOnly
          ? `第 ${phaseIndex}/${phaseCount} 步 · 约 ${Math.max(progress, 1)}% · 已用时 `
          : `${completed}/${total || "--"} 个会话 · 约 ${Math.max(progress, 1)}% · 已用时 `;
        const title = scanOnly
          ? "正在扫描会话"
          : (mode === "migrate" ? "正在迁移会话" : "正在复制会话");
        const subtitle = scanOnly
          ? "按主会话归并本地记录与关联子任务"
          : `${completed}/${total || "--"} 个会话 · 目标可见可续聊 ${targetReady} 个`;
        const stage = scanOnly
          ? "列表与提交将在完成后解锁"
          : `目标就绪 ${targetReady} · 源已删除 ${migrated} · 源保留 ${retained}`
            + (mode === "migrate" && sourceCleanupTotal > 0
              ? ` · 源清理 ${sourceCleanupCompleted}/${sourceCleanupTotal}`
              : "");
        return {
          active,
          scanning,
          scanOnly,
          transferring,
          mode,
          progress,
          progressMetaPrefix,
          phaseLabel,
          phaseIndex,
          phaseCount,
          title,
          subtitle,
          stage,
          elapsed,
          cancelAction: scanOnly ? "session-cleanup-cancel" : "session-transfer-cancel",
          cancelLabel: scanOnly ? "取消扫描" : (mode === "migrate" ? "取消迁移" : "取消复制"),
        };
      }

      function sessionTransferProgressHtml(model) {
        const view = model && typeof model === "object" ? model : {};
        const scanOnly = view.scanOnly === true;
        const icon = scanOnly ? "scan" : "copy";
        const progress = Math.max(0, Math.min(99, Number(view.progress || 0)));
        return `<div class="codex-usage-hud-session-transfer-progress-state" data-session-transfer-progress-state="true" aria-live="polite"><div class="codex-usage-hud-cleanup-scan-strip"><div class="codex-usage-hud-cleanup-scan-strip-top"><div class="codex-usage-hud-cleanup-scan-strip-title"><span class="codex-usage-hud-cleanup-mini-spinner"></span>${escapeHtml(scanOnly ? "扫描本地会话" : "处理 Provider 会话")}</div><div class="codex-usage-hud-cleanup-scan-strip-meta">${escapeHtml(view.progressMetaPrefix || "处理中 · 已用时 ")}<span data-session-transfer-elapsed="true">${escapeHtml(view.elapsed || "0:00")}</span></div></div><div class="codex-usage-hud-cleanup-scan-track"><div class="codex-usage-hud-cleanup-scan-fill" data-indeterminate="${progress <= 0}" style="width:${Math.max(progress, 8)}%"></div></div><div class="codex-usage-hud-cleanup-scan-stage"><span>当前：<strong>${escapeHtml(view.phaseLabel || "处理中")}</strong></span><span>${escapeHtml(view.stage || "完成后恢复列表")}</span></div></div><div class="codex-usage-hud-cleanup-empty-state" style="min-height:180px"><div class="codex-usage-hud-cleanup-scan-mark" data-live="true">${cleanupIconSvg(icon, "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">${escapeHtml(view.title || "正在处理会话")}</h2><p class="codex-usage-hud-cleanup-empty-meta">${escapeHtml(view.subtitle || "请稍候")}</p><button type="button" class="codex-usage-hud-settings-action" data-action="${escapeHtml(view.cancelAction || "session-transfer-cancel")}">${escapeHtml(view.cancelLabel || "取消")}</button></div></div>`;
      }

      function stopSessionTransferElapsedTicker() {
        if (sessionTransferElapsedTimer) {
          ctx.lifecycle.clearInterval(sessionTransferElapsedTimer);
          sessionTransferElapsedTimer = 0;
        }
      }

      function syncSessionTransferElapsed() {
        const layer = sessionTransferDialogLayer();
        const model = sessionTransferProgressModel();
        if (!layer || !sessionTransferState.open || !model.active) {
          stopSessionTransferElapsedTicker();
          return false;
        }
        layer.querySelectorAll('[data-session-transfer-elapsed="true"]').forEach((node) => {
          node.textContent = model.elapsed;
        });
        return true;
      }

      function ensureSessionTransferElapsedTicker() {
        if (!syncSessionTransferElapsed()) return false;
        if (!sessionTransferElapsedTimer) {
          sessionTransferElapsedTimer = ctx.lifecycle.interval(
            "session_transfer_elapsed",
            syncSessionTransferElapsed,
            1000,
          );
        }
        return true;
      }

      function sessionTransferModeValue(input) {
        return String(input?.value || input?.dataset?.sessionTransferMode || "copy").toLowerCase() === "migrate"
          ? "migrate"
          : "copy";
      }

      function syncSessionTransferModeFromDialog() {
        const checked = sessionTransferDialogLayer()?.querySelector?.('[data-session-transfer-mode]:checked');
        if (checked) sessionTransferState.mode = sessionTransferModeValue(checked);
        return sessionTransferState.mode;
      }

      function syncSessionTransferSubmitButton(mode = sessionTransferState.mode) {
        const submit = sessionTransferDialogLayer()?.querySelector?.('[data-session-transfer-submit-button="true"]');
        if (!submit) return false;
        submit.textContent = String(mode || "copy").toLowerCase() === "migrate" ? "开始迁移" : "开始复制";
        return true;
      }

      function bindSessionTransferModeControls(layer) {
        if (!layer) return false;
        layer.querySelectorAll?.('[data-session-transfer-mode]').forEach((input) => {
          const apply = () => {
            sessionTransferState.mode = sessionTransferModeValue(input);
            syncSessionTransferSubmitButton(sessionTransferState.mode);
          };
          input.addEventListener("input", apply);
          input.addEventListener("change", apply);
          input.addEventListener("click", apply);
        });
        return true;
      }

      function sessionTransferErrorHint(error) {
        const text = String(error || "");
        if (
          /thread[- ]store internal error/i.test(text)
          || /thread history projection/i.test(text)
          || /expected ordinal/i.test(text)
          || /failed to prepare paginated fork/i.test(text)
        ) {
          return "该会话的本地历史记录不一致（Codex 已知问题，暂未修复）：会话仍可在原 Provider 打开继续使用，但目前无法被复制或迁移。可先升级 Codex CLI 后重试。";
        }
        return "";
      }

      function sessionTransferErrorHtml(title, error) {
        const errorText = String(error || "未知错误");
        const hint = sessionTransferErrorHint(errorText);
        return `<div class="codex-usage-hud-session-transfer-error">${escapeHtml(title)}：${escapeHtml(errorText)}${hint ? `<small data-kind="hint">${escapeHtml(hint)}</small>` : ""}</div>`;
      }

      function sessionTransferResultHtml(operation) {
        const action = String(operation?.action || "");
        const state = String(operation?.state || "").toLowerCase();
        if (action !== "sessionTransfer" || !new Set(["completed", "partial", "failed"]).has(state)) {
          return "";
        }
        const mode = String(operation?.mode || "copy").toLowerCase() === "migrate" ? "迁移" : "复制";
        const copied = Math.max(0, Number(operation?.copiedCount || 0));
        const targetReady = Math.max(0, Number(operation?.targetReadyCount ?? copied));
        const migrated = Math.max(0, Number(operation?.migratedCount || 0));
        const targetFailed = Math.max(0, Number(operation?.targetFailedCount ?? operation?.failedCount ?? 0));
        const retained = mode === "迁移"
          ? Math.max(0, Number(operation?.sourceRetainedCount || 0))
          : 0;
        const cleanupPending = mode === "迁移"
          ? Math.max(0, Number(operation?.sourceCleanupPendingCount || 0))
          : 0;
        const headline = state === "completed"
          ? `${mode}完成：${targetReady} 个会话已可在目标 Provider 继续工作${cleanupPending ? `，${cleanupPending} 个源会话待清理` : ""}`
          : `${mode}${state === "partial" ? "部分完成" : "失败"}`;
        const details = mode === "迁移" && state !== "completed"
          ? `目标可见可续聊 ${targetReady} · 源已删除 ${migrated} · 源保留 ${retained} · 目标未就绪 ${targetFailed}`
          : mode === "迁移"
            ? `目标可见可续聊 ${targetReady} · 源已删除 ${migrated} · 源保留 ${retained}`
          : `目标可见可续聊 ${targetReady} · 目标未就绪 ${targetFailed}`;
        const operationError = String(operation?.error || "").trim();
        const targetProvider = String(operation?.targetProvider || "").trim().toLowerCase();
        const targetNotice = targetReady
          ? '<span class="codex-usage-hud-session-transfer-target-notice">已按目标 Provider 的会话列表确认落盘。启动 Codex CLI 时勾选“来自迁移”可选择对应工作目录。</span>'
          : "";
        const errors = [
          operationError
            ? sessionTransferErrorHtml("会话迁移", operationError)
            : "",
          ...(Array.isArray(operation?.results) && state !== "completed" ? operation.results : [])
          .filter((item) => String(item?.error || "").trim())
          .slice(0, 4)
          .map((item) => sessionTransferErrorHtml(item?.title || "会话", item?.error)),
        ].join("");
        return `<div class="codex-usage-hud-session-transfer-result" data-kind="${state === "completed" ? "success" : "error"}"><strong>${escapeHtml(headline)}</strong><span>${escapeHtml(details)}</span>${targetNotice}${errors}</div>`;
      }

      function syncSessionTransferDialogControls(data = sessionCleanupFromPayload(), viewOverride = null) {
        const layer = sessionTransferDialogLayer();
        if (!layer || !sessionTransferState.open) return false;
        syncSessionTransferModeFromDialog();
        const dataValue = data && typeof data === "object" ? data : {};
        const view = viewOverride || sessionTransferView(dataValue);
        const source = String(sessionTransferState.sourceProvider || "").trim().toLowerCase();
        const settings = hudSettingsFromPayload();
        const targets = normaliseSessionTransferTargetProvider(settings, source);
        const operation = sessionTransferOperation();
        const busy = sessionTransferBusy(operation);
        const progressModel = sessionTransferProgressModel(dataValue, operation);
        const selected = view.selected;
        const selectableIds = view.selectableIds;
        const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selected.has(id));
        const submitDisabled = busy
          || !String(dataValue?.revision || "").trim()
          || !selected.size
          || !source
          || !sessionTransferState.targetProvider;
        layer.querySelectorAll('[data-session-transfer-id]').forEach((checkbox) => {
          const id = String(checkbox.dataset.sessionTransferId || "");
          checkbox.checked = selected.has(id);
        });
        const selectAll = layer.querySelector('[data-session-transfer-select-all="true"]');
        if (selectAll) {
          selectAll.checked = allSelected;
          selectAll.indeterminate = selected.size > 0 && !allSelected;
          selectAll.disabled = busy || !selectableIds.length;
        }
        const count = layer.querySelector('[data-session-transfer-selection-count="true"]');
        if (count) count.textContent = `${selected.size} / ${selectableIds.length} 个可选会话`;
        layer.querySelectorAll('[data-session-transfer-mode]').forEach((radio) => {
          radio.checked = String(radio.value || radio.dataset.sessionTransferMode || "copy").toLowerCase() === sessionTransferState.mode;
          radio.disabled = busy;
        });
        const target = layer.querySelector('[data-session-transfer-target="true"]');
        if (target) {
          if (sessionTransferState.targetProvider) target.value = sessionTransferState.targetProvider;
          target.disabled = busy || !targets.length;
        }
        const search = layer.querySelector('[data-session-transfer-search="true"]');
        if (search) search.disabled = busy;
        const submit = layer.querySelector('[data-session-transfer-submit-button="true"]');
        if (submit) {
          submit.disabled = submitDisabled;
          syncSessionTransferSubmitButton(sessionTransferState.mode);
        }
        const scanButton = layer.querySelector('[data-action="session-transfer-scan"]');
        if (scanButton) {
          scanButton.disabled = busy;
          scanButton.textContent = progressModel.scanning ? "正在扫描..." : "重新扫描";
        }
        const list = layer.querySelector('[data-session-transfer-list="true"]');
        if (list) {
          const nextState = progressModel.active ? "progress" : "list";
          if (list.dataset.state !== nextState) {
            renderSessionTransferDialog(dataValue);
            return true;
          }
          if (progressModel.active) {
            list.innerHTML = sessionTransferProgressHtml(progressModel);
          }
        }
        const resultLayer = layer.querySelector('[data-session-transfer-result="true"]');
        if (resultLayer) {
          const result = sessionTransferResultHtml(operation);
          resultLayer.innerHTML = result;
          resultLayer.hidden = !result;
        }
        const card = layer.querySelector('.codex-usage-hud-session-transfer-card');
        if (card) card.setAttribute("aria-busy", String(busy));
        if (progressModel.active) ensureSessionTransferElapsedTicker();
        else stopSessionTransferElapsedTicker();
        return true;
      }

      function sessionTransferDialogHtml(dataOverride = null) {
        const data = dataOverride && typeof dataOverride === "object"
          ? dataOverride
          : (sessionCleanupFromPayload() || sessionTransferState.data || {});
        const source = String(sessionTransferState.sourceProvider || "").trim().toLowerCase();
        const settings = hudSettingsFromPayload();
        const targets = normaliseSessionTransferTargetProvider(settings, source);
        const operation = sessionTransferOperation();
        const view = sessionTransferView(data);
        const rows = view.rows;
        const selectableIds = view.selectableIds;
        const selected = view.selected;
        const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selected.has(id));
        const busy = sessionTransferBusy(operation);
        const progressModel = sessionTransferProgressModel(data, operation);
        const revision = String(data?.revision || "").trim();
        const rowHtml = rows.length
          ? rows.map((item) => {
            const id = String(item?.id || "");
            const selectable = item?.selectable === true;
            const checked = selected.has(id);
            const updated = String(item?.updatedAt || "").replace("T", " ").replace(/([+-]\d\d:\d\d|Z)$/, "");
            const archivedNotice = item?.archived === true
              ? '<small data-kind="warning">已归档，请先解除归档后再复制或迁移</small>'
              : "";
            return `<label class="codex-usage-hud-session-transfer-row" data-session-transfer-row="true"><input type="checkbox" data-session-transfer-id="${escapeHtml(id)}" ${checked ? "checked" : ""} ${selectable ? "" : "disabled"}><span class="codex-usage-hud-session-transfer-main"><strong>${escapeHtml(item?.title || "未命名会话")}</strong><small>${escapeHtml(item?.workdirName || "未记录工作目录")} · ${escapeHtml(providerDisplayName(settings, item?.modelProvider || "unknown"))}</small>${archivedNotice}</span><span class="codex-usage-hud-session-transfer-time">${escapeHtml(updated || "--")}</span></label>`;
          }).join("")
          : '<div class="codex-usage-hud-session-transfer-empty">没有找到可选的源 Provider 会话。请先扫描，或调整搜索条件。</div>';
        const targetOptions = targets.length
          ? targets.map((provider) => `<option value="${escapeHtml(provider)}" ${provider === sessionTransferState.targetProvider ? "selected" : ""}>${escapeHtml(providerDisplayName(settings, provider))}</option>`).join("")
          : '<option value="">没有可用目标 Provider</option>';
        const totalRows = view.allRows.length;
        const pageStart = totalRows ? view.page * sessionTransferPageSize + 1 : 0;
        const pageEnd = Math.min(totalRows, (view.page + 1) * sessionTransferPageSize);
        const pagination = !progressModel.active && view.totalPages > 1
          ? `<div class="codex-usage-hud-session-transfer-pagination"><button type="button" class="codex-usage-hud-settings-icon-action" data-action="session-transfer-page" data-direction="prev" aria-label="上一页" title="上一页" ${busy || view.page <= 0 ? "disabled" : ""}>‹</button><span>第 ${view.page + 1}/${view.totalPages} 页 · ${pageStart}-${pageEnd}/${totalRows}</span><button type="button" class="codex-usage-hud-settings-icon-action" data-action="session-transfer-page" data-direction="next" aria-label="下一页" title="下一页" ${busy || view.page >= view.totalPages - 1 ? "disabled" : ""}>›</button></div>`
          : "";
        const result = sessionTransferResultHtml(operation);
        const transferList = progressModel.active
          ? `<div class="codex-usage-hud-session-transfer-list" data-session-transfer-list="true" data-state="progress">${sessionTransferProgressHtml(progressModel)}</div>`
          : `<div class="codex-usage-hud-session-transfer-list" data-session-transfer-list="true" data-state="list">${rowHtml}</div>`;
        return `<div class="codex-usage-hud-settings-confirm-card codex-usage-hud-session-transfer-card" role="dialog" aria-modal="true" aria-label="复制或迁移 Provider 会话">
          <div class="codex-usage-hud-session-transfer-head"><div><div class="codex-usage-hud-settings-confirm-kicker">Provider 会话</div><div class="codex-usage-hud-settings-confirm-title">复制或迁移会话</div></div><button type="button" class="codex-usage-hud-settings-icon-action" data-action="session-transfer-close" aria-label="关闭" title="关闭">×</button></div>
          <div class="codex-usage-hud-session-transfer-body">
            <div class="codex-usage-hud-session-transfer-context"><span>源 Provider</span><strong>${escapeHtml(source ? providerDisplayName(settings, source) : "未选择")}</strong><span>目标 Provider</span><select data-session-transfer-target="true" ${busy || !targets.length ? "disabled" : ""}>${targetOptions}</select></div>
            <div class="codex-usage-hud-session-transfer-mode"><label><input type="radio" name="codex-session-transfer-mode" data-session-transfer-mode="copy" value="copy" ${sessionTransferState.mode === "copy" ? "checked" : ""} ${busy ? "disabled" : ""}><span>复制</span><small>保留源会话</small></label><label><input type="radio" name="codex-session-transfer-mode" data-session-transfer-mode="migrate" value="migrate" ${sessionTransferState.mode === "migrate" ? "checked" : ""} ${busy ? "disabled" : ""}><span>迁移</span><small>复制成功后删除源会话</small></label></div>
            <div class="codex-usage-hud-session-transfer-search"><span aria-hidden="true">⌕</span><input type="search" data-session-transfer-search="true" value="${escapeHtml(sessionTransferState.search)}" placeholder="搜索标题或工作目录" aria-label="搜索会话" ${busy ? "disabled" : ""}></div>
             <div class="codex-usage-hud-session-transfer-toolbar"><label><input type="checkbox" data-session-transfer-select-all="true" ${allSelected ? "checked" : ""} ${busy || !selectableIds.length ? "disabled" : ""}>全选当前筛选</label><span data-session-transfer-selection-count="true">${selected.size} / ${selectableIds.length} 个可选会话</span></div>
             ${transferList}
             ${pagination}
             <div data-session-transfer-result="true" ${result ? "" : "hidden"}>${result}</div>
           </div>
           <div class="codex-usage-hud-settings-confirm-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-transfer-scan" ${busy ? "disabled" : ""}>${progressModel.scanning ? "正在扫描..." : "重新扫描"}</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-transfer-submit" data-session-transfer-submit-button="true" data-primary="true" ${busy || !revision || !selected.size || !source || !sessionTransferState.targetProvider ? "disabled" : ""}>${sessionTransferState.mode === "migrate" ? "开始迁移" : "开始复制"}</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-transfer-close" data-variant="ghost">关闭</button></div>
         </div>`;
      }

      function sessionTransferDialogLayer() {
        return document.querySelector(`#${settingsModalId} [data-session-transfer-dialog="true"]`);
      }

      function renderSessionTransferDialog(dataOverride = null) {
        const layer = sessionTransferDialogLayer();
        if (!layer || !sessionTransferState.open) return;
        // Preserve the user's last mode when a list/scan update replaces the
        // dialog DOM. The native radio state is authoritative between renders.
        syncSessionTransferModeFromDialog();
        layer.innerHTML = sessionTransferDialogHtml(dataOverride);
        bindSessionTransferModeControls(layer);
        syncSessionTransferDialogControls(dataOverride || sessionCleanupFromPayload());
      }

      function closeSessionTransferDialog() {
        stopSessionTransferElapsedTicker();
        sessionTransferDialogLayer()?.remove();
        delete window[sessionTransferStateName];
        sessionTransferState.open = false;
        sessionTransferState.sourceProvider = "";
        sessionTransferState.targetProvider = "";
        sessionTransferState.mode = "copy";
        sessionTransferState.search = "";
        sessionTransferState.page = 0;
        sessionTransferState.selectedIds.clear();
        sessionTransferState.scanRequestId = "";
        sessionTransferState.requestId = "";
        sessionTransferState.startedAt = 0;
        sessionTransferState.cancelledRequestId = "";
        sessionTransferState.resumeRequestId = "";
        sessionTransferState.resumeSessionId = "";
        sessionTransferState.resumeState = "";
        sessionTransferState.resumeMessage = "";
        sessionTransferState.data = null;
        sessionTransferState.operation = null;
      }

      function requestSessionTransferScan() {
        if (!sessionTransferState.open || sessionTransferState.requestId) return false;
        sessionTransferState.cancelledRequestId = "";
        return requestSessionCleanupScan({ preserveTransfer: true });
      }

      function openSessionTransferDialog(provider = "") {
        const dialog = settingsDialogRoot();
        if (!dialog) return false;
        const settings = hudSettingsFromPayload();
        ensureSettingsProviderDraft(settings);
        const source = String(provider || settingsProviderDraft?.activeProvider || "").trim().toLowerCase();
        const targets = sessionTransferProviderTargets(settings, source);
        if (!source) {
          setSettingsStatus("未找到源 Provider，无法打开会话迁移。", "error");
          return false;
        }
        if (!targets.length) {
          setSettingsStatus("当前没有已配置的目标 Provider。请先添加或配置另一个 Provider。", "error");
          return false;
        }
        closeCodexCliDialog();
        closeSettingsConfirm();
        closeSessionTransferDialog();
        sessionTransferState.open = true;
        window[sessionTransferStateName] = sessionTransferState;
        sessionTransferState.sourceProvider = source;
        sessionTransferState.targetProvider = targets[0];
        sessionTransferState.mode = "copy";
        sessionTransferState.search = "";
        sessionTransferState.page = 0;
        const data = sessionTransferDataFromPayload();
        sessionTransferState.data = data && typeof data === "object" ? data : null;
        const operation = sessionTransferState.data?.operation;
        sessionTransferState.operation = operation && typeof operation === "object"
          && String(operation?.action || "").toLowerCase() === "sessiontransfer"
          && String(operation?.sourceProvider || "").trim().toLowerCase() === source
          && String(operation?.targetProvider || "").trim().toLowerCase() === String(targets[0] || "").trim().toLowerCase()
          ? operation
          : null;
        if (sessionTransferState.operation && sessionTransferProgressModel(sessionTransferState.data, sessionTransferState.operation).active) {
          const persistedStartedAt = Math.max(
            Number(sessionTransferState.startedAt || 0),
            Number(sessionTransferState.operation?.startedAt || 0),
          );
          sessionTransferState.startedAt = persistedStartedAt || Date.now();
        }
        const sharedScanRequestId = activeSessionCleanupScanRequestId(sessionTransferState.data);
        if (sharedScanRequestId) sessionTransferState.scanRequestId = sharedScanRequestId;
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.sessionTransferDialog = "true";
        dialog.appendChild(layer);
        renderSessionTransferDialog();
        if (
          !String(sessionTransferState.data?.revision || "").trim()
          && !sharedScanRequestId
        ) requestSessionTransferScan();
        return true;
      }

      function requestSessionTransferCancel() {
        if (!sessionTransferState.open || !sessionTransferBusy()) return false;
        const operation = sessionTransferOperation();
        const transferOperationActive = !!sessionTransferState.requestId
          || String(operation?.action || "").toLowerCase() === "sessiontransfer";
        if (
          (sessionTransferState.scanRequestId || activeSessionCleanupScanRequestId())
          && !transferOperationActive
        ) {
          return requestSessionCleanupCancel();
        }
        const targetRequestId = String(
          sessionTransferState.requestId || sessionTransferState.operation?.requestId || "",
        ).trim();
        const requestId = typedSettingsRequestId("session-transfer-cancel");
        const submitted = submitSettingsCommand(
          {
            action: "sessionCleanupCancel",
            requestId,
            targetRequestId,
          },
          "正在取消会话复制/迁移...",
          { preserveOverlay: true },
        );
        if (!submitted) return false;
        sessionTransferState.cancelledRequestId = targetRequestId;
        sessionTransferState.requestId = "";
        sessionTransferState.startedAt = 0;
        sessionTransferState.operation = {
          ...(sessionTransferState.operation && typeof sessionTransferState.operation === "object"
            ? sessionTransferState.operation
            : {}),
          action: "cancel",
          state: "cancelled",
          requestId: targetRequestId || requestId,
          progress: 100,
        };
        renderSessionTransferDialog();
        return true;
      }

      function submitSessionTransfer() {
        if (!sessionTransferState.open || sessionTransferBusy()) return false;
        const data = sessionCleanupFromPayload() || sessionTransferState.data || {};
        const view = sessionTransferView(data);
        const itemIds = Array.from(sessionTransferState.selectedIds).filter((id) => view.selectableSet.has(id));
        const sourceProvider = String(sessionTransferState.sourceProvider || "").trim().toLowerCase();
        const targetProvider = String(sessionTransferState.targetProvider || "").trim().toLowerCase();
        const inventoryRevision = String(data?.revision || "").trim();
        if (!itemIds.length || !sourceProvider || !targetProvider || !inventoryRevision) {
          setSettingsStatus("请选择至少一个会话，并确认已完成扫描和目标 Provider 选择。", "error");
          renderSessionTransferDialog();
          return false;
        }
        const requestId = typedSettingsRequestId("session-transfer");
        sessionTransferState.requestId = requestId;
        sessionTransferState.startedAt = Date.now();
        sessionTransferState.cancelledRequestId = "";
        sessionTransferState.operation = {
          action: "sessionTransfer",
          state: "accepted",
          requestId,
          sourceProvider,
          targetProvider,
          mode: sessionTransferState.mode,
          selectedIds: itemIds,
          progress: 0,
          startedAt: sessionTransferState.startedAt,
        };
        renderSessionTransferDialog();
        const submitted = submitSettingsCommand(
          {
            action: "sessionTransfer",
            requestId,
            itemIds,
            inventoryRevision,
            sourceProvider,
            targetProvider,
            mode: sessionTransferState.mode,
          },
          sessionTransferState.mode === "migrate" ? "正在复制并安全删除源会话..." : "正在复制会话...",
          { preserveOverlay: true },
        );
        if (!submitted) {
          sessionTransferState.requestId = "";
          sessionTransferState.startedAt = 0;
          sessionTransferState.operation = null;
          renderSessionTransferDialog();
        }
        return submitted;
      }

      function applySessionTransferPayload(payload) {
        const incoming = payload?.sessionCleanup;
        if (!incoming || typeof incoming !== "object") return;
        const previous = sessionTransferState.data && typeof sessionTransferState.data === "object"
          ? sessionTransferState.data
          : {};
        const data = Array.isArray(incoming.sessions) || !Array.isArray(previous.sessions)
          ? incoming
          : { ...previous, ...incoming, sessions: previous.sessions };
        sessionTransferState.data = data;
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const action = String(operation?.action || "").toLowerCase();
        const state = String(operation?.state || "").toLowerCase();
        const responseRequestId = String(operation?.requestId || "");
        const terminal = new Set(["completed", "partial", "failed", "cancelled"]).has(state);
        const cancelledRequestId = String(sessionTransferState.cancelledRequestId || "");
        const cancellationMatches = !!cancelledRequestId && responseRequestId === cancelledRequestId;
        if (new Set(["scan", "sessioncleanupscan"]).has(action)) {
          const requestMatches = (
            (sessionTransferState.scanRequestId && responseRequestId === sessionTransferState.scanRequestId)
            || (!sessionTransferState.scanRequestId && !sessionTransferState.requestId)
          );
          if ((requestMatches || cancellationMatches) && (!cancellationMatches || terminal)) {
            sessionTransferState.operation = operation;
            if (terminal) {
              sessionTransferState.scanRequestId = "";
              sessionTransferState.startedAt = 0;
              if (cancellationMatches) sessionTransferState.cancelledRequestId = "";
            }
          }
        } else if (action === "sessiontransfer") {
          if (sessionTransferState.open) {
            const source = String(sessionTransferState.sourceProvider || "").trim().toLowerCase();
            normaliseSessionTransferTargetProvider(hudSettingsFromPayload(), source);
          }
          const pairMatches = !sessionTransferState.open
            || sessionTransferOperationMatchesCurrentPair(operation);
          const requestMatches = !sessionTransferState.requestId
            || responseRequestId === sessionTransferState.requestId;
          if (pairMatches && requestMatches && (!cancellationMatches || terminal)) {
            sessionTransferState.operation = operation;
            const operationStartedAt = Math.max(0, Number(operation?.startedAt || 0));
            if (!terminal && operationStartedAt > 0) {
              sessionTransferState.startedAt = Math.max(
                Number(sessionTransferState.startedAt || 0),
                operationStartedAt,
              );
            }
            if (terminal) {
              codexCliPersistTransferWorkdirs(operation);
              sessionTransferState.requestId = "";
              sessionTransferState.startedAt = 0;
              if (cancellationMatches) sessionTransferState.cancelledRequestId = "";
              sessionTransferState.selectedIds.clear();
            }
          }
        } else if (action === "cancel") {
          sessionTransferState.operation = operation;
          sessionTransferState.scanRequestId = "";
          sessionTransferState.requestId = "";
          sessionTransferState.startedAt = 0;
        }
        if (!sessionTransferState.open) return;
        const layer = sessionTransferDialogLayer();
        // Progress notifications update only the progress surface. Rebuild the
        // list when the operation enters or leaves its active state.
        if (layer && !terminal) {
          syncSessionTransferDialogControls(data);
        } else {
          renderSessionTransferDialog(data);
        }
      }

      function applySettingsCommandStatus(payload) {
        const status = payload?.settingsCommandStatus;
        sessionViewDomain.applySearchJump(status?.sessionCleanupSessionJump);
        const sessionIndex = status?.sessionIndex || payload?.sessionIndex;
        if (sessionIndex && typeof sessionIndex === "object") {
          // Capture the prior job state BEFORE the optimistic overwrite so we
          // can detect a build finishing on this exact frame (running/attached
          // -> idle). The overwrite below would otherwise hide the transition.
          const prevIndexJobState = String(sessionCleanupState.sessionIndex?.jobState || "");
          // Terminal range_done/idle snapshots must replace the optimistic
          // progress state immediately, including when the modal is open.
          sessionCleanupState.sessionIndex = { ...sessionIndex };
          sessionCleanupState.sessionIndexRefreshing = false;
          const responseRequestId = String(sessionIndex.requestId || status?.requestId || "");
          const controlRequestId = String(sessionCleanupState.sessionIndexControlRequestId || "");
          const isControlResponse = Object.prototype.hasOwnProperty.call(sessionIndex, "accepted");
          if (controlRequestId && isControlResponse && (!responseRequestId || responseRequestId === controlRequestId)) {
            sessionCleanupState.sessionIndexControlRequestId = "";
          }
          if (settingsActiveTab === "storage") {
            refreshStoragePanelIfVisible();
            const modal = document.getElementById(settingsModalId);
            if (
              String(responseRequestId).startsWith("session-index-status-")
              && modal
              && !modal.hidden
              && settingsActiveTab === "storage"
            ) {
              sessionCleanupState.sessionIndexUiAttached = new Set(["running", "attached", "paused"]).has(
                String(sessionIndex.jobState || ""),
              );
            }
          }
          // When a warm-index build (initial build OR range extension) reaches
          // its terminal idle state, immediately re-run any active session
          // search so the newly indexed sessions surface without a manual
          // re-query. Only the running/attached -> idle transition fires; the
          // follow-up idle snapshot emitted by the off-thread finalizer is
          // suppressed by this guard because prevIndexJobState is already idle.
          const newIndexJobState = String(sessionIndex.jobState || "idle");
          if (
            new Set(["running", "attached"]).has(prevIndexJobState)
            && newIndexJobState === "idle"
          ) {
            const indexRoot = document.getElementById(rootId);
            const indexSearchInput = indexRoot
              ? indexRoot.querySelector('[data-session-cleanup-search="true"]')
              : null;
            const indexSearchText = indexSearchInput
              ? String(indexSearchInput.value || "").trim()
              : "";
            // Prefer the live input box; fall back to the committed query when
            // the storage panel is not mounted (modal closed but a prior search
            // was active). Empty content means nothing to refresh.
            const indexSearchQuery = indexSearchText || String(sessionCleanupState.search || "").trim();
            if (indexSearchQuery) {
              requestSessionCleanupSearch(indexSearchQuery);
            }
          }
        }
        const workdirOptionsResponse = status?.sessionCleanupWorkdirOptions;
        if (workdirOptionsResponse && typeof workdirOptionsResponse === "object") {
          const responseRequestId = String(workdirOptionsResponse.requestId || "");
          const expectedRequestId = String(sessionCleanupState.workdirOptionsRequestId || "");
          if (!expectedRequestId || !responseRequestId || responseRequestId === expectedRequestId) {
            const responseError = String(workdirOptionsResponse.error || "");
            sessionCleanupState.workdirOptionsRetryBlocked = !!responseError;
            if (Array.isArray(workdirOptionsResponse.options)) {
              const current = Array.isArray(sessionCleanupState.workdirOptions)
                ? sessionCleanupState.workdirOptions
                : [];
              const byId = new Map(
                current
                  .filter((item) => item && typeof item === "object" && String(item?.id || ""))
                  .map((item) => [String(item.id), item]),
              );
              for (const item of workdirOptionsResponse.options) {
                const id = String(item?.id || "");
                if (!id) continue;
                // Preserve a path-bearing entry already received from the
                // latest scan when an older request completes afterward.
                byId.set(id, { ...byId.get(id), ...item });
              }
              sessionCleanupState.workdirOptions = Array.from(byId.values());
            }
            sessionCleanupState.workdirOptionsRequestId = "";
            const safeOptions = Array.isArray(sessionCleanupState.data?.workdirs)
              ? sessionCleanupState.data.workdirs
              : [];
            const detailedIds = new Set(
              sessionCleanupState.workdirOptions
                .filter((item) => item?.path)
                .map((item) => String(item?.id || "")),
            );
            const responseReady = !responseError
              && workdirOptionsResponse.detailed === true
              && Array.isArray(workdirOptionsResponse.options)
              && workdirOptionsResponse.options.length > 0;
            if (responseReady && safeOptions.some((item) => String(item?.id || "") && !detailedIds.has(String(item.id)))) {
              requestSessionCleanupWorkdirOptions();
            }
            if (settingsActiveTab === "storage") refreshStoragePanelIfVisible();
          }
        }
        if (status && typeof status === "object" && String(status.action || "")) {
          // Quick launch is intentionally independent from the Settings modal.
          // Consume its request-scoped statuses even while Settings stays hidden.
          applyCodexCliCommandStatus(status);
        }
        if (
          sessionTransferState.open
          && sessionTransferState.resumeRequestId
          && status
          && typeof status === "object"
        ) {
          const statusAction = String(status.action || "");
          const statusRequestId = String(status.requestId || "");
          if (
            statusRequestId === String(sessionTransferState.resumeRequestId)
            && new Set(["codexCliLaunchPending", "codexCliLaunch"]).has(statusAction)
          ) {
            if (statusAction === "codexCliLaunchPending") {
              sessionTransferState.resumeState = "starting";
              sessionTransferState.resumeMessage = "正在打开目标 Provider 会话...";
            } else {
              const failed = String(status.kind || "").toLowerCase() === "error"
                || !status.codexCliLaunch;
              sessionTransferState.resumeState = failed ? "error" : "completed";
              sessionTransferState.resumeMessage = String(
                status.message
                || (failed ? "目标 Provider 会话启动失败。" : "已打开目标 Provider 会话，可继续聊天。"),
              );
              sessionTransferState.resumeRequestId = "";
            }
            renderSessionTransferDialog();
          }
        }
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden) return;
        const state = updateStateFromPayload(payload);
        updateAboutActionButtons(state);
        syncDesktopOverlayDependency();
        syncSettingsUpdateLoading(payload);
        applySessionTransferPayload(payload);
        let providerDeleteTerminalHandled = false;
        const cleanupOperation = payload?.sessionCleanup?.operation;
        if (cleanupOperation && typeof cleanupOperation === "object"
          && String(cleanupOperation.action || "") === "providerDelete") {
          const operationRequestId = String(cleanupOperation.requestId || "");
          const operationProvider = String(
            cleanupOperation.provider
              || cleanupOperation.providerResult?.providerId
              || "",
          ).trim().toLowerCase();
          const expectedRequestId = String(pricingWorkflowState.providerDeleteRequestId || "");
          const expectedProvider = String(pricingWorkflowState.providerDeleteProvider || "").trim().toLowerCase();
          const requestMatches = !expectedRequestId || operationRequestId === expectedRequestId;
          const providerMatches = !expectedProvider || operationProvider === expectedProvider;
          const terminal = ["completed", "partial", "failed"].includes(String(cleanupOperation.state || ""));
          if (expectedProvider && requestMatches && providerMatches && terminal) {
            const loadingLayer = modal.querySelector('[data-provider-delete-loading="true"]');
            const loadingRequestId = String(loadingLayer?.dataset.providerDeleteRequestId || "");
            const loadingProvider = String(loadingLayer?.dataset.providerDeleteProvider || "").trim().toLowerCase();
            if (
              loadingLayer
              && (!loadingRequestId || loadingRequestId === operationRequestId)
              && (!loadingProvider || loadingProvider === operationProvider)
            ) {
              closeSettingsConfirm();
            }
            if (String(cleanupOperation.state || "") === "failed") {
              setSettingsStatus(
                `供应商 ${providerDisplayName(hudSettingsFromPayload(), expectedProvider)} 删除失败：${String(cleanupOperation.error || "未知错误")}`,
                "error",
              );
              providerDeleteTerminalHandled = true;
            } else {
              const providerResult = cleanupOperation.providerResult;
              const message = String(
                providerResult?.message || `供应商 ${providerDisplayName(hudSettingsFromPayload(), expectedProvider)} 已删除。`,
              );
              setSettingsStatus(message, "");
              // config 删除已在同步 dispatch 阶段完成，此处幂等地确保供应商从界面移除
              // （后台历史清理的 terminal 事件也能覆盖到这里）。
              removeProviderFromSettingsUi(expectedProvider);
              providerDeleteTerminalHandled = true;
            }
            clearProviderDeleteWorkflow();
          }
        }
        if (status && typeof status === "object" && String(status.message || "")) {
          if (String(status.action || "") === "deleteProvider" && !providerDeleteTerminalHandled) {
            const expectedRequestId = String(pricingWorkflowState.providerDeleteRequestId || "");
            const statusRequestId = String(
              status.requestId || status.providerDeleteRequestId || "",
            );
            if (!expectedRequestId || !statusRequestId || statusRequestId === expectedRequestId) {
              pricingWorkflowState.providerDeleteRequestId = String(
                status.providerDeleteRequestId || pricingWorkflowState.providerDeleteRequestId || "",
              );
              pricingWorkflowState.providerDeleteProvider = String(
                status.provider || pricingWorkflowState.providerDeleteProvider || "",
              ).trim().toLowerCase();
              const terminalError = String(status.kind || "") === "error";
              // config.toml / 单价删除已在 daemon 同步请求阶段完成，status 即是
              // "供应商已删除" 的确认信号。这里统一关闭全屏 loading 遮罩、从设置
              // 界面移除该供应商并释放 workflow；历史会话清理若勾选则已在后台线程
              // 执行，不影响删除速度，也不等到历史清理完成才反映删除结果。
              const providerToRemove = String(
                status.provider || status.providerId || pricingWorkflowState.providerDeleteProvider || "",
              ).trim().toLowerCase();
              const loadingLayer = modal.querySelector('[data-provider-delete-loading="true"]');
              const loadingRequestId = String(loadingLayer?.dataset.providerDeleteRequestId || "");
              if (
                loadingLayer
                && (!loadingRequestId || !statusRequestId || loadingRequestId === statusRequestId)
              ) {
                closeSettingsConfirm();
              }
              if (!terminalError) {
                removeProviderFromSettingsUi(providerToRemove);
              }
              clearProviderDeleteWorkflow();
            }
          }
          if (!providerDeleteTerminalHandled) {
            if (status.restartVisible) {
              // 默认供应商保存后，若用户已在弹窗中做出重启决策，则按决策执行，
              // 不再走旧的「状态栏 + 底部立即重启按钮」流程。
              if (pendingProviderRestartAfterSave === "now") {
                pendingProviderRestartAfterSave = null;
                setSettingsStatus(status.message || "", status.kind || "");
                restartCodexFromModal();
              } else if (pendingProviderRestartAfterSave === "later") {
                pendingProviderRestartAfterSave = null;
                setSettingsStatus("默认 Codex App Provider 配置已保存，稍后重启 Codex Desktop 即可生效。", "");
              } else {
                showSettingsRestartPrompt(status.message || "配置已保存。", status.kind || "", {
                  codex: status.restartCodex === true,
                });
              }
            } else {
              // 保存未返回 restartVisible（或出错）时清除待执行的重启决策，
              // 避免残留状态影响后续操作。
              pendingProviderRestartAfterSave = null;
              setSettingsStatus(status.message || "", status.kind || "");
            }
          }
          // 重启提示是粘性的：一旦因默认供应商等配置显示，就只能由用户
          // 「立即重启」/关闭窗口「稍后重启」来关闭，不能被后续轮次的普通
          // 状态消息（如其它后台命令的 message）或空状态自动清掉。这里仅在
          // 尚未有待处理的重启提示时才按本轮状态同步可见性（幂等无操作）。
          if (!status.restartVisible && !settingsRestartPending) {
            clearSettingsRestartPrompt();
          }
          applyPricingCommandStatus(status);
          applyProviderConnectivityStatus(status);
          applyProviderChatTestStatus(status);
          const restSaveRequestId = String(status.restReminderSaveRequestId || "");
          if (
            status.restReminderSaved === true
            && !String(status.kind || "")
            && restSaveRequestId
            && restSaveRequestId !== restReminderSavedRequestId
          ) {
            const startNode = modal.querySelector('[data-rest-reminder-start-time="true"]');
            if (startNode) delete startNode.dataset.userEdited;
            restReminderSavedRequestId = restSaveRequestId;
          }
          return;
        }
        // 兜底：仅在确实有非空文案、且当前没有粘性错误在展示时才覆盖状态栏。
        // 后台的 settings_command_status 每轮渲染后会清空，若这里无条件写空串，
        // 会把当轮刚展示的错误信息在下一轮刷新时立刻刷掉（一闪而过）。
        const fallbackText = String(state?.message || state?.title || "").trim();
        if (!settingsRestartPending && fallbackText && !settingsStatusErrorSticky) {
          setSettingsStatus(state.message || state.title || "", state.error ? "error" : "");
        }
        if (settingsRestartPending) {
          setSettingsRestartVisible(true, { codex: settingsRestartCodex });
        } else {
          clearSettingsRestartPrompt();
        }
      }

      function submitSettingsCommand(command, pendingMessage, { preserveOverlay = false, quiet = false } = {}) {
        const payload = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          createdAt: Date.now(),
          ...command,
        };
        const bridge = settingsBridgeUrl();
        const bindingAvailable = !!ctx.bindings?.available?.(settingsCommandBindingName);
        if (!bindingAvailable && !bridge) {
          setSettingsStatus("无法提交设置命令：settings bridge 未连接", "error");
          return false;
        }
        try {
          if (bindingAvailable) {
            try {
              const sent = ctx.bindings.send(settingsCommandBindingName, payload);
              if (sent === false) throw new Error("settings command binding unavailable");
            } catch (error) {
              handleSettingsCommandSubmissionError(error, command);
              return false;
            }
          } else {
          fetch(`${bridge}/command`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            keepalive: true,
          }).catch((error) => {
            handleSettingsCommandSubmissionError(error, command);
          });
          }
        } catch (error) {
          handleSettingsCommandSubmissionError(error, command);
          return false;
        }
        const providerDeleteRequestId = String(command?.requestId || command?.id || "");
        const providerDeleteHandledSynchronously = String(command?.action || "") === "deleteProvider"
          && Boolean(providerDeleteRequestId)
          && !String(pricingWorkflowState.providerDeleteRequestId || "");
        if (!providerDeleteHandledSynchronously && !quiet) {
          setSettingsStatus(pendingMessage || "设置命令已提交，等待 HUD daemon 写入本地配置...");
        }
        clearSettingsRestartPrompt();
        if (!preserveOverlay) closeSettingsConfirm();
        return true;
      }

      function settingsDialogRoot() {
        return document.querySelector(`#${settingsModalId} .codex-usage-hud-settings-dialog`);
      }

      function closeSettingsConfirm() {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        if (layer) layer.remove();
      }

      function clearProviderDeleteWorkflow() {
        pricingWorkflowState.providerDeleteRequestId = "";
        pricingWorkflowState.providerDeleteProvider = "";
        pricingWorkflowState.providerDeleteHasSessionHistory = false;
      }

      // 从设置界面的 supplier draft 中移除已删除的供应商并刷新供应商 tab/编辑器。
      // config.toml 删除已在 daemon 同步完成，status 或 terminal 事件到达时调用一次
      // 即可让遮罩消失时供应商从设置界面移除；对重复事件保持幂等。
      function removeProviderFromSettingsUi(provider) {
        if (!settingsProviderDraft) return;
        provider = String(provider || "").trim().toLowerCase();
        if (!provider || !settingsProviderDraft.providers?.[provider]) return;
        delete settingsProviderDraft.providers?.[provider];
        settingsProviderDraft.order = settingsProviderDraft.order.filter(
          (item) => item !== provider,
        );
        codexProviderDrafts.delete(provider);
        codexProviderDirty.delete(provider);
        settingsDirtyProviders.delete(provider);
        settingsProviderDraft.quickLaunchProviders?.delete(provider);
        if (settingsProviderDraft.activeProvider === provider) {
          settingsProviderDraft.activeProvider = settingsProviderDraft.order.includes(
            settingsProviderDraft.appProvider,
          )
            ? settingsProviderDraft.appProvider
            : (settingsProviderDraft.order[0] || "");
          window[settingsProviderName] = settingsProviderDraft.activeProvider;
        }
        renderSettingsProviderEditor();
        renderSettingsProviderTabs();
      }

      function handleSettingsCommandSubmissionError(error, command = {}) {
        setSettingsStatus(`设置命令提交失败：${error?.message || error}`, "error");
        if (String(command?.action || "") !== "deleteProvider") return;
        const requestId = String(command?.requestId || command?.id || "");
        const expectedRequestId = String(pricingWorkflowState.providerDeleteRequestId || "");
        if (expectedRequestId && requestId && expectedRequestId !== requestId) return;
        const loadingLayer = document.querySelector(
          `#${settingsModalId} [data-provider-delete-loading="true"]`,
        );
        const loadingRequestId = String(loadingLayer?.dataset.providerDeleteRequestId || "");
        if (loadingLayer && (!loadingRequestId || !requestId || loadingRequestId === requestId)) {
          closeSettingsConfirm();
        }
        clearProviderDeleteWorkflow();
      }

      function openSettingsLoading({ kicker = "正在处理", title = "", body = "", mode = "" } = {}) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        if (mode) layer.dataset.loadingMode = mode;
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="status" aria-live="polite" aria-label="${escapeHtml(title || "正在处理设置变更")}">
            <button type="button" class="codex-usage-hud-settings-confirm-close" data-action="settings-confirm-close" aria-label="关闭">×</button>
            <div class="codex-usage-hud-settings-confirm-kicker">${escapeHtml(kicker)}</div>
            <div class="codex-usage-hud-settings-confirm-title">${escapeHtml(title)}</div>
            <div class="codex-usage-hud-settings-confirm-body">${escapeHtml(body)}</div>
            <div class="codex-usage-hud-settings-loading-track" aria-hidden="true">
              <div class="codex-usage-hud-settings-loading-bar"></div>
              <div class="codex-usage-hud-settings-loading-glow"></div>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
        const closeBtn = layer.querySelector('[data-action="settings-confirm-close"]');
        if (closeBtn) {
          closeBtn.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            closeSettingsConfirm();
          });
        }
      }

      function openProviderDeleteLoading(requestId, provider, deleteSessionHistory = false) {
        openSettingsLoading({
          kicker: "正在删除",
          title: "正在删除供应商",
          body: deleteSessionHistory
            ? "正在更新 config.toml 和模型单价配置；会话历史较多时会在后台清理，可关闭此窗口继续操作。"
            : "正在更新 config.toml 和模型单价配置，请勿关闭此窗口。",
          mode: "provider-delete",
        });
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        if (!layer) return false;
        layer.dataset.providerDeleteLoading = "true";
        layer.dataset.providerDeleteRequestId = String(requestId || "");
        layer.dataset.providerDeleteProvider = String(provider || "").trim().toLowerCase();
        return true;
      }

      function collectSettingsForm() {
        const modal = document.getElementById(settingsModalId);
        const settings = hudSettingsFromPayload();
        const settingNode = (key) => modal?.querySelector(`[data-setting-key="${key}"]`);
        const read = (key) => settingNode(key)?.value;
        const numberValue = (key, fallback) => {
          const value = Number(read(key));
          return Number.isFinite(value) && value >= 0 ? value : fallback;
        };
        const integerValue = (key, fallback, min, max) => {
          const value = Number(read(key));
          if (!Number.isFinite(value)) return fallback;
          return Math.min(max, Math.max(min, Math.round(value)));
        };
        captureSettingsProviderForm();
        const draft = ensureSettingsProviderDraft(settings);
        const providerSettings = { ...(settings.provider_settings || {}) };
        draft.order.forEach((provider) => {
          providerSettings[provider] = {
            ...(providerSettings[provider] || {}),
            ...(draft.providers[provider]?.settings || {}),
          };
        });
        const selectedProviders = draft.order.filter((provider) => (
          provider === draft.appProvider || !!draft.providers[provider]?.enabled
        ));
        const notificationOnlyProviders = draft.order.filter((provider) => (
          provider !== draft.appProvider
          && !draft.providers[provider]?.enabled
          && !!draft.providers[provider]?.notificationOnly
        ));
        const allProvidersSelected = draft.order.every((provider) => selectedProviders.includes(provider));
        const displayMode = "renderer";
        const next = {
          ...settings,
          daily_budget_usd: numberValue("daily_budget_usd", settings.daily_budget_usd),
          weekly_budget_usd: numberValue("weekly_budget_usd", settings.weekly_budget_usd),
          daily_reset_time: String(read("daily_reset_time") || settings.daily_reset_time),
          weekly_reset_weekday: Number(read("weekly_reset_weekday") ?? settings.weekly_reset_weekday),
          weekly_reset_time: String(read("weekly_reset_time") || settings.weekly_reset_time),
          display_mode: displayMode,
          work_overlay_max_items: integerValue(
            "work_overlay_max_items",
            Number(settings.work_overlay_max_items) || 0,
            0,
            workOverlaySelectableMax(),
          ),
          work_overlay_side: String(read("work_overlay_side") || settings.work_overlay_side || "right"),
          pricing_url: String(settings.pricing_url || "").trim(),
          provider_settings: providerSettings,
          provider_order: draft.order,
          provider_scope_mode: allProvidersSelected ? "all" : "custom",
          selected_providers: selectedProviders,
          notification_only_providers: notificationOnlyProviders,
          quick_launch_providers: Array.from(draft.quickLaunchProviders || [])
            .filter((provider) => draft.order.includes(provider)),
          budget_thresholds: String(read("budget_thresholds") || "")
            .split(",")
            .map((item) => Number(item.trim()))
            .filter((item) => Number.isFinite(item) && item > 0),
          weekly_adjustment_usd: settings.weekly_adjustment_usd,
          support_url: String(settings.support_url || "https://github.com/fengbuming/codex-usage-hud").trim(),
          model_prices: settings.model_prices,
        };
        if (settingNode("stop_hud_on_lock_screen")) {
          next.stop_hud_on_lock_screen = !!settingNode("stop_hud_on_lock_screen").checked;
        }
        // Rest-reminder controls live on the support tab; keep previous values when absent.
        if (settingNode("rest_reminder_enabled")) {
          next.rest_reminder_enabled = !!settingNode("rest_reminder_enabled").checked;
        }
        if (settingNode("rest_reminder_interval_minutes")) {
          next.rest_reminder_interval_minutes = integerValue(
            "rest_reminder_interval_minutes",
            Number(settings.rest_reminder_interval_minutes) || 45,
            1,
            180,
          );
        }
        if (settingNode("rest_reminder_break_minutes")) {
          next.rest_reminder_break_minutes = integerValue(
            "rest_reminder_break_minutes",
            Number(settings.rest_reminder_break_minutes) || 2,
            1,
            10,
          );
        }
        if (settingNode("rest_reminder_postpone_minutes")) {
          next.rest_reminder_postpone_minutes = integerValue(
            "rest_reminder_postpone_minutes",
            Number(settings.rest_reminder_postpone_minutes) || 10,
            5,
            30,
          );
        }
        [
          "rest_reminder_work_start_time",
          "rest_reminder_work_end_time",
          "rest_reminder_lunch_start_time",
          "rest_reminder_lunch_end_time",
        ].forEach((key) => {
          if (settingNode(key)) next[key] = String(read(key) || settings[key] || "");
        });
        if (settingNode("rest_reminder_lunch_enabled")) {
          next.rest_reminder_lunch_enabled = !!settingNode("rest_reminder_lunch_enabled").checked;
        }
        const startNode = modal?.querySelector('[data-rest-reminder-start-time="true"]');
        if (startNode?.value && startNode.dataset.userEdited === "true") {
          const [hour, minute] = String(startNode.value).split(":").map(Number);
          if (Number.isFinite(hour) && Number.isFinite(minute)) {
            const started = new Date();
            started.setHours(hour, minute, 0, 0);
            next.rest_reminder_timer_started_at_ms = started.getTime();
          }
        }
        return next;
      }

      function collectCodexProviderUpdates() {
        return Array.from(codexProviderDirty).map((provider) => {
          const draft = codexProviderDrafts.get(provider) || {};
          return {
            provider_id: String(draft.providerId || provider).trim().toLowerCase(),
            base_url: String(draft.baseUrl || "").trim(),
            env_key: String(draft.envKey || "").trim(),
            api_key: String(draft.apiKey || ""),
            section_text: String(draft.configText || ""),
            is_new: draft.isNew === true,
          };
        });
      }

      function pricingTableFingerprint(settings) {
        const providers = settings?.provider_settings && typeof settings.provider_settings === "object"
          ? settings.provider_settings
          : {};
        const normalizedProviders = Object.fromEntries(Object.entries(providers).sort(([left], [right]) => left.localeCompare(right)).map(([provider, value]) => [
          provider,
          value?.model_prices && typeof value.model_prices === "object" ? value.model_prices : {},
        ]));
        return JSON.stringify({
          model_prices: settings?.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {},
          provider_settings: normalizedProviders,
        });
      }

      function pricingChanged(settings) {
        return pricingTableFingerprint(settings) !== pricingTableFingerprint(hudSettingsFromPayload());
      }

      function settingsWithPersistedPriceTables(candidate, base = hudSettingsFromPayload()) {
        const baseProviders = base?.provider_settings && typeof base.provider_settings === "object"
          ? base.provider_settings
          : {};
        const candidateProviders = candidate?.provider_settings && typeof candidate.provider_settings === "object"
          ? candidate.provider_settings
          : {};
        const providerSettings = Object.fromEntries(Object.entries(candidateProviders).map(([provider, value]) => [
          provider,
          {
            ...(value && typeof value === "object" ? value : {}),
            model_prices: baseProviders[provider]?.model_prices && typeof baseProviders[provider].model_prices === "object"
              ? baseProviders[provider].model_prices
              : {},
          },
        ]));
        return {
          ...candidate,
          model_prices: base?.model_prices && typeof base.model_prices === "object" ? base.model_prices : {},
          provider_settings: providerSettings,
        };
      }

      function autoSaveNonPricingSettingsFromModal({ section = "" } = {}) {
        const settings = settingsWithPersistedPriceTables(collectSettingsForm());
        const signature = JSON.stringify(settings);
        if (signature === autoSavedSettingsSignature) return false;
        const submitted = submitSettingsCommand(
          { action: "save", settings, ...(section ? { section } : {}) },
          section === "restReminder" ? "正在自动保存提醒设置..." : "正在自动保存设置...",
        );
        if (submitted) autoSavedSettingsSignature = signature;
        return submitted;
      }

      function pricingTablesByScope(settings) {
        const tables = new Map();
        tables.set("", settings?.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {});
        const providers = settings?.provider_settings && typeof settings.provider_settings === "object"
          ? settings.provider_settings
          : {};
        Object.entries(providers).forEach(([provider, value]) => {
          tables.set(
            String(provider || "").trim().toLowerCase(),
            value?.model_prices && typeof value.model_prices === "object" ? value.model_prices : {},
          );
        });
        return tables;
      }

      function pricingRowFingerprint(row, fallbackModel = "") {
        const number = (value) => {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : 0;
        };
        return JSON.stringify({
          model: String(row?.model || fallbackModel || "").trim(),
          provider: String(row?.provider || "").trim().toLowerCase(),
          baseUrl: String(row?.base_url || row?.baseUrl || "").trim(),
          input: number(row?.input),
          cachedInput: number(row?.cached_input),
          cacheWrite: number(row?.cache_write),
          output: number(row?.output),
          reasoning: number(row?.reasoning ?? row?.output),
        });
      }

      function pricingShortPrice(row) {
        if (!row || typeof row !== "object") return "未设置";
        const number = (value) => {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : 0;
        };
        return `输入 ${number(row.input)} / 输出 ${number(row.output)}`;
      }

      function pricingModelName(row, fallback = "") {
        return String(row?.model || fallback || "").trim();
      }

      function pricingModelsMatch(left, right) {
        const leftModel = pricingModelName(null, left);
        const rightModel = pricingModelName(null, right);
        if (!leftModel || !rightModel) return false;
        const normalizedLeft = normalizePriceModel(leftModel);
        const normalizedRight = normalizePriceModel(rightModel);
        const leftIsPattern = normalizedLeft.includes("*") || normalizedLeft.includes("?");
        const rightIsPattern = normalizedRight.includes("*") || normalizedRight.includes("?");
        return normalizedLeft === normalizedRight
          || (leftIsPattern && priceModelPatternMatches(normalizedLeft, normalizedRight))
          || (rightIsPattern && priceModelPatternMatches(normalizedRight, normalizedLeft));
      }

      function pricingNumber(value) {
        const parsed = Number(value);
        return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
      }

      function pricingPriceFields(row, fallbackModel = "") {
        const output = pricingNumber(row?.output);
        return {
          model: pricingModelName(row, fallbackModel),
          input: pricingNumber(row?.input),
          cached_input: pricingNumber(row?.cached_input),
          cache_write: pricingNumber(row?.cache_write),
          output,
          reasoning: pricingNumber(row?.reasoning ?? output),
        };
      }

      function clonePricingSettings(settings) {
        const source = settings && typeof settings === "object" ? settings : {};
        const providerSettings = source.provider_settings && typeof source.provider_settings === "object"
          ? Object.fromEntries(Object.entries(source.provider_settings).map(([provider, value]) => [
            provider,
            {
              ...(value && typeof value === "object" ? value : {}),
              model_prices: cloneSettingsPriceTable(value?.model_prices),
            },
          ]))
          : {};
        return {
          ...source,
          model_prices: cloneSettingsPriceTable(source.model_prices),
          provider_settings: providerSettings,
        };
      }

      function pricingTableForProvider(settings, provider) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (!normalizedProvider) {
          return settings?.model_prices && typeof settings.model_prices === "object"
            ? settings.model_prices
            : {};
        }
        const entry = settings?.provider_settings?.[normalizedProvider];
        return entry?.model_prices && typeof entry.model_prices === "object"
          ? entry.model_prices
          : {};
      }

      function pricingChangedRows(previous, candidate, provider) {
        const before = pricingTableForProvider(previous, provider);
        const after = pricingTableForProvider(candidate, provider);
        return Object.entries(after).filter(([key, row]) => (
          pricingRowFingerprint(before[key], key) !== pricingRowFingerprint(row, key)
        ));
      }

      function applyPricingToAllProviders(previous, candidate, provider = "") {
        const changedRows = pricingChangedRows(previous, candidate, provider);
        if (!changedRows.length) return candidate;
        const result = clonePricingSettings(candidate);
        const providers = settingsProviderNames(result);
        if (!providers.length) {
          result.model_prices = cloneSettingsPriceTable(result.model_prices);
          changedRows.forEach(([key, sourceRow]) => {
            result.model_prices[key] = pricingPriceFields(sourceRow, key);
          });
          return result;
        }
        providers.forEach((targetProvider) => {
          const currentSettings = result.provider_settings?.[targetProvider] || {};
          const table = cloneSettingsPriceTable(currentSettings.model_prices);
          changedRows.forEach(([sourceKey, sourceRow]) => {
            const model = pricingModelName(sourceRow, sourceKey);
            const matchingKeys = Object.keys(table).filter((key) => (
              pricingModelsMatch(pricingModelName(table[key], key), model)
            ));
            if (matchingKeys.length) {
              matchingKeys.forEach((key) => {
                const targetRow = table[key] && typeof table[key] === "object" ? table[key] : {};
                table[key] = {
                  ...targetRow,
                  ...pricingPriceFields(sourceRow, model),
                  model: pricingModelName(targetRow, key) || model,
                };
              });
              return;
            }
            table[model] = {
              ...pricingPriceFields(sourceRow, model),
              provider: targetProvider,
            };
          });
          result.provider_settings[targetProvider] = {
            ...currentSettings,
            model_prices: table,
          };
        });
        return result;
      }

      function syncCurrentProviderPricesToOthers() {
        const sourceProvider = captureSettingsProviderForm();
        const draft = settingsProviderDraft;
        const sourceEntry = draft?.providers?.[sourceProvider];
        const sourceTable = sourceEntry?.settings?.model_prices && typeof sourceEntry.settings.model_prices === "object"
          ? sourceEntry.settings.model_prices
          : {};
        const sourceRows = Object.entries(sourceTable).filter(([key, row]) => pricingModelName(row, key));
        if (!draft || !sourceProvider || !sourceRows.length) {
          setSettingsStatus("当前 Provider 没有可同步的模型单价。", "error");
          return;
        }
        let providerCount = 0;
        let addedCount = 0;
        let updatedCount = 0;
        draft.order.forEach((targetProvider) => {
          if (targetProvider === sourceProvider) return;
          const targetEntry = draft.providers?.[targetProvider];
          if (!targetEntry) return;
          const currentSettings = targetEntry.settings || {};
          const table = cloneSettingsPriceTable(currentSettings.model_prices);
          let providerTouched = false;
          sourceRows.forEach(([sourceKey, sourceRow]) => {
            const model = pricingModelName(sourceRow, sourceKey);
            if (!model) return;
            const matchingKeys = Object.keys(table).filter((key) => (
              pricingModelsMatch(pricingModelName(table[key], key), model)
            ));
            if (matchingKeys.length) {
              matchingKeys.forEach((key) => {
                const targetRow = table[key] && typeof table[key] === "object" ? table[key] : {};
                const nextRow = {
                  ...targetRow,
                  ...pricingPriceFields(sourceRow, model),
                  model: pricingModelName(targetRow, key) || model,
                };
                if (pricingRowFingerprint(targetRow, key) !== pricingRowFingerprint(nextRow, key)) {
                  updatedCount += 1;
                  providerTouched = true;
                }
                table[key] = nextRow;
              });
              return;
            }
            table[model] = {
              ...pricingPriceFields(sourceRow, model),
              provider: targetProvider,
            };
            addedCount += 1;
            providerTouched = true;
          });
          if (!providerTouched) return;
          targetEntry.settings = {
            ...currentSettings,
            model_prices: canonicalSettingsPriceTable(table, targetProvider),
          };
          settingsDirtyProviders.add(targetProvider);
          providerCount += 1;
        });
        renderSettingsProviderTabs();
        if (!providerCount) {
          setSettingsStatus("其它 Provider 的模型单价已是最新。");
          return;
        }
        setSettingsStatus(
          "已同步 "
          + sourceRows.length
          + " 个模型到 "
          + providerCount
          + " 个 Provider：新增 "
          + addedCount
          + "，更新 "
          + updatedCount
          + "。保存后生效。"
        );
      }

      function pricingChangeSummaryHtml(previous, candidate) {
        const beforeTables = pricingTablesByScope(previous);
        const afterTables = pricingTablesByScope(candidate);
        const scopes = Array.from(new Set([...beforeTables.keys(), ...afterTables.keys()])).sort();
        const changes = [];
        scopes.forEach((scope) => {
          const before = beforeTables.get(scope) || {};
          const after = afterTables.get(scope) || {};
          const models = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
          models.forEach((model) => {
            const oldRow = before[model];
            const newRow = after[model];
            if (pricingRowFingerprint(oldRow, model) === pricingRowFingerprint(newRow, model)) return;
            changes.push({
              provider: scope || "全局",
              model: String(newRow?.model || oldRow?.model || model || "未命名模型"),
              oldRow,
              newRow,
            });
          });
        });
        if (!changes.length) return "";
        const rows = changes.slice(0, 6).map((change) => `
          <div><strong>${escapeHtml(change.provider)}</strong> · ${escapeHtml(change.model)}<br><span>${escapeHtml(pricingShortPrice(change.oldRow))} -> ${escapeHtml(pricingShortPrice(change.newRow))}</span></div>
        `).join("");
        const more = changes.length > 6 ? `<div>另有 ${changes.length - 6} 个模型价格变更</div>` : "";
        return `<div class="codex-usage-hud-pricing-preview-list" data-pricing-change-summary="true" aria-label="价格变更摘要">${rows}${more}</div>`;
      }

      function openPricingEffectiveDialog({ mode, settings = null, provider = "", url = "" }) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        pricingWorkflowState.pendingSettings = settings;
        pricingWorkflowState.pendingSettingsBase = settings;
        pricingWorkflowState.pendingApplyAll = false;
        pricingWorkflowState.pendingMode = String(mode || "save");
        pricingWorkflowState.pendingProvider = String(provider || "").trim().toLowerCase();
        pricingWorkflowState.pendingUrl = String(url || "").trim();
        const changeSummary = mode === "save"
          ? pricingChangeSummaryHtml(hudSettingsFromPayload(), settings)
          : "";
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="设置新价格的生效时间">
            <div class="codex-usage-hud-settings-confirm-kicker">价格版本</div>
            <div class="codex-usage-hud-settings-confirm-title">保存新价格</div>
            <div class="codex-usage-hud-settings-confirm-body">新价格从确认保存时起对后续请求生效；已有记录保持原来的统计结果，不进行历史重算。</div>
            ${changeSummary}
            ${mode === "fetch" ? '<div class="codex-usage-hud-pricing-impact">拉取结果会先进入导入预览，不会立即写入。</div>' : ""}
            <div class="codex-usage-hud-settings-confirm-actions">
              ${mode === "save" ? '<label class="codex-usage-hud-pricing-apply-all"><input type="checkbox" data-pricing-apply-all="true"><span>应用于所有 providers</span></label>' : ""}
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-effective-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-effective-confirm" data-primary="true">${mode === "save" ? "确认并保存" : "拉取并预览"}</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
        layer.querySelector('[data-action="pricing-effective-confirm"]')?.focus?.();
      }

      function updatePricingApplyAllPreview(enabled) {
        if (String(pricingWorkflowState.pendingMode || "") !== "save") return;
        const base = pricingWorkflowState.pendingSettingsBase || pricingWorkflowState.pendingSettings;
        if (!base) return;
        pricingWorkflowState.pendingApplyAll = !!enabled;
        pricingWorkflowState.pendingSettings = enabled
          ? applyPricingToAllProviders(
            hudSettingsFromPayload(),
            base,
            pricingWorkflowState.pendingProvider,
          )
          : base;
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        if (!layer) return;
        const current = layer.querySelector('[data-pricing-change-summary="true"]');
        const next = pricingChangeSummaryHtml(
          hudSettingsFromPayload(),
          pricingWorkflowState.pendingSettings,
        );
        if (current) {
          if (next) current.outerHTML = next;
          else current.remove();
        } else if (next) {
          layer.querySelector(".codex-usage-hud-settings-confirm-body")?.insertAdjacentHTML("afterend", next);
        }
      }

      function confirmPricingEffectiveAt() {
        const mode = String(pricingWorkflowState.pendingMode || "save");
        if (mode === "fetch") {
          submitSettingsCommand({
            action: "fetchPricesPreview",
            provider: pricingWorkflowState.pendingProvider,
            url: pricingWorkflowState.pendingUrl,
            reason: "official-sync",
          }, "正在拉取并校验价格 JSON...");
          return;
        }
        const applyAllNode = document.querySelector(`#${settingsModalId} [data-pricing-apply-all="true"]`);
        if (applyAllNode) updatePricingApplyAllPreview(!!applyAllNode.checked);
        submitSettingsCommand({
          action: "savePricing",
          settings: pricingWorkflowState.pendingSettings || collectSettingsForm(),
          codexProviders: collectCodexProviderUpdates(),
        }, "正在保存新的价格版本...");
      }

      function openPricingExportPrompt(status) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        const path = String(status?.pricingPath || "").trim();
        const filename = String(
          status?.filename || path.split(/[\\/]/).pop() || "codex-usage-hud-pricing.json",
        ).trim();
        if (!path || !filename) return;
        closeSettingsConfirm();
        const usedTemplate = status?.pricingUsedTemplate === true;
        const body = usedTemplate
          ? `当前模型价格表为空，已使用 gpt-5.6-sol 内置价格作为模板。\n\n文件已生成到：\n${path}\n\n是否打开这个文件？`
          : `已按当前用户模型单价配置生成。\n\n文件已生成到：\n${path}\n\n是否打开这个文件？`;
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.dataset.pricingExportPrompt = "true";
        layer.dataset.pricingFilename = filename;
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="价格 JSON 已生成">
            <div class="codex-usage-hud-settings-confirm-kicker">价格 JSON</div>
            <div class="codex-usage-hud-settings-confirm-title">价格文件已生成</div>
            <div class="codex-usage-hud-settings-confirm-body">${escapeHtml(body)}</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-open-cancel" data-variant="ghost">暂不打开</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-open" data-primary="true">打开文件</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
        layer.querySelector('[data-action="pricing-open"]')?.focus?.();
      }

      function openPricingImportDialog() {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        pricingWorkflowState.importPayload = null;
        pricingWorkflowState.importSourcePayload = null;
        pricingWorkflowState.importPreview = null;
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card codex-usage-hud-pricing-dialog" role="dialog" aria-modal="true" aria-label="导入价格 JSON">
            <div class="codex-usage-hud-settings-confirm-kicker">价格 JSON</div>
            <div class="codex-usage-hud-settings-confirm-title">导入并预览</div>
            <label class="codex-usage-hud-pricing-field">选择 JSON 文件
              <input type="file" accept="application/json,.json" data-pricing-import-file="true">
            </label>
            <label class="codex-usage-hud-pricing-field">或粘贴 JSON
              <textarea data-pricing-import-text="true" spellcheck="false" placeholder="{ &quot;schema_version&quot;: 1, &quot;unit&quot;: &quot;USD_per_1M_tokens&quot;, &quot;prices&quot;: [] }"></textarea>
            </label>
            <div class="codex-usage-hud-pricing-impact">导入的新增价格从确认导入时起对后续请求生效；已有记录不变。</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-import-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-import-preview" data-primary="true">校验并预览</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      async function readPricingImportFile(input) {
        const file = input?.files?.[0];
        if (!file) return;
        try {
          if (Number(file.size || 0) > 2 * 1024 * 1024) {
            throw new Error("价格 JSON 文件不能超过 2 MiB。");
          }
          const text = await file.text();
          const textarea = document.querySelector(`#${settingsModalId} [data-pricing-import-text="true"]`);
          if (textarea) textarea.value = text;
          setSettingsStatus(`已读取 ${file.name}，等待校验。`);
        } catch (error) {
          setSettingsStatus(`价格文件读取失败：${error?.message || error}`, "error");
        }
      }

      function previewPricingImport() {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        const textarea = layer?.querySelector('[data-pricing-import-text="true"]');
        try {
          const payload = JSON.parse(String(textarea?.value || ""));
          pricingWorkflowState.importSourcePayload = payload;
          pricingWorkflowState.importPayload = payload;
          submitSettingsCommand({
            action: "pricingImportPreview",
            payload,
          }, "正在校验价格 JSON 并生成导入预览...");
        } catch (error) {
          setSettingsStatus(`价格 JSON 无法预览：${error?.message || error}`, "error");
        }
      }

      function openPricingImportPreview(preview, payload) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        pricingWorkflowState.pendingMode = "import";
        pricingWorkflowState.importPreview = preview;
        pricingWorkflowState.importSourcePayload = pricingWorkflowState.importSourcePayload || payload || null;
        pricingWorkflowState.importPayload = payload || pricingWorkflowState.importPayload;
        const added = Number(preview?.addedCount ?? preview?.added ?? 0);
        const updated = Number(preview?.updatedCount ?? preview?.updated ?? 0);
        const skipped = Number(preview?.skippedCount ?? preview?.skipped ?? 0);
        const conflicts = Array.isArray(preview?.conflicts) ? preview.conflicts : [];
        const warnings = Array.isArray(preview?.warnings) ? preview.warnings : [];
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card codex-usage-hud-pricing-dialog" role="alertdialog" aria-modal="true" aria-label="确认价格导入">
            <div class="codex-usage-hud-settings-confirm-kicker">导入预览</div>
            <div class="codex-usage-hud-settings-confirm-title">新增 ${added} · 更新 ${updated} · 跳过 ${skipped}</div>
            <div class="codex-usage-hud-pricing-preview-grid"><span>冲突<strong>${conflicts.length}</strong></span><span>兼容提示<strong>${warnings.length}</strong></span></div>
            ${conflicts.length ? `<div class="codex-usage-hud-pricing-preview-list">${conflicts.slice(0, 8).map((item) => `<div>${escapeHtml(String(item.provider || "全局"))} · ${escapeHtml(String(item.model_pattern || item.model || "模型"))} · ${escapeHtml(String(item.effective_at || ""))}</div>`).join("")}</div>` : ""}
            ${warnings.length ? `<div class="codex-usage-hud-pricing-impact">${warnings.map((item) => escapeHtml(String(item))).join("<br>")}</div>` : ""}
            <div class="codex-usage-hud-pricing-impact">确认导入后，新增价格立即用于后续请求；已有记录不进行历史重算。</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-import-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-import-commit" data-primary="true" disabled>${conflicts.length ? "覆盖冲突并导入" : "确认导入"}</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      function commitPricingImport() {
        submitSettingsCommand({
          action: "pricingImportCommit",
          payload: pricingWorkflowState.importSourcePayload || pricingWorkflowState.importPayload,
          conflictPolicy: "overwrite",
        }, "正在原子写入价格版本...");
      }

      function copyPricingExample() {
        const example = {
          model: "your-model",
          provider: "your-provider",
          base_url: "https://api.example.com/v1",
          input: 1,
          cached_input: 0.1,
          cache_write: 1.25,
          output: 6,
          reasoning: 6,
          effective_at: new Date().toISOString(),
        };
        copyHudText(JSON.stringify(example, null, 2)).then(
          () => setSettingsStatus("已复制最小合法价格示例。"),
          (error) => setSettingsStatus(`复制示例失败：${error?.message || error}`, "error"),
        );
      }

      function commitSettingsFromDraft(pendingMessage, { skipPricingDialog = false } = {}) {
        const settings = collectSettingsForm();
        if (pricingChanged(settings)) {
          // 新增供应商时直接保存新价格，跳过「保存新价格」确认对话框。
          if (skipPricingDialog) {
            const codexProviders = collectCodexProviderUpdates();
            const submitted = submitSettingsCommand(
              {
                action: "savePricing",
                settings,
                ...(codexProviders.length ? { codexProviders } : {}),
              },
              pendingMessage || "正在保存新的价格版本...",
            );
            if (submitted) {
              settingsDirtyProviders.clear();
              codexProviderDirty.clear();
              renderSettingsProviderTabs();
            }
            return;
          }
          openPricingEffectiveDialog({
            mode: "save",
            settings,
            provider: String(settingsProviderDraft?.activeProvider || "").trim().toLowerCase(),
          });
          return;
        }
        const codexProviders = collectCodexProviderUpdates();
        const submitted = submitSettingsCommand(
          { action: "save", settings, ...(codexProviders.length ? { codexProviders } : {}) },
          pendingMessage || "正在保存设置..."
        );
        if (submitted) {
          settingsDirtyProviders.clear();
          codexProviderDirty.clear();
          renderSettingsProviderTabs();
        }
      }

      function applyModelPricesFromModal() {
        const settings = collectSettingsForm();
        if (!pricingChanged(settings)) {
          setSettingsStatus("模型单价没有待应用的修改。", "");
          return false;
        }
        openPricingEffectiveDialog({
          mode: "save",
          settings,
          provider: String(settingsProviderDraft?.activeProvider || "").trim().toLowerCase(),
        });
        return true;
      }

      function saveSettingsFromModal({ section = "" } = {}) {
        // The rest-reminder panel keeps its explicit Save action. It must not
        // submit a pending model-price draft through the generic save path.
        const settings = section === "restReminder"
          ? settingsWithPersistedPriceTables(collectSettingsForm())
          : collectSettingsForm();
        const codexProviders = section ? [] : collectCodexProviderUpdates();
        const submitted = submitSettingsCommand(
          { action: "save", settings, ...(section ? { section } : {}), ...(codexProviders.length ? { codexProviders } : {}) },
          section === "restReminder" ? "正在保存提醒设置..." : "正在保存设置...",
        );
        if (submitted && !section) {
          settingsDirtyProviders.clear();
          codexProviderDirty.clear();
          renderSettingsProviderTabs();
        }
      }

      function fetchPricesFromModal() {
        const settings = collectSettingsForm();
        const provider = String(settingsProviderDraft?.activeProvider || "").trim().toLowerCase();
        const url = String(settings?.provider_settings?.[provider]?.pricing_url || settings.pricing_url || "").trim();
        openPricingEffectiveDialog({ mode: "fetch", provider, url });
      }

      function restartHudFromModal() {
        submitSettingsCommand(
          { action: "restart", reason: "settings" },
          "重启请求已提交，等待 HUD daemon 处理..."
        );
      }

      function restartCodexFromModal() {
        submitSettingsCommand(
          { action: "restartCodex", reason: "provider-config" },
          "重启请求已提交，等待 HUD daemon 重启 Codex Desktop..."
        );
      }

      function installDesktopOverlayFromModal() {
        submitSettingsCommand(
          { action: "installDesktopOverlay" },
          "正在准备安装气泡组件..."
        );
      }

      function enableDesktopOverlayFromModal() {
        submitSettingsCommand(
          { action: "enableDesktopOverlay" },
          "正在重新检测气泡组件..."
        );
      }

      function exitHudFromModal() {
        openSettingsLoading({
          kicker: "正在退出",
          title: "正在停止 HUD",
          body: "HUD 正在退出当前界面，并停止后台守护进程（如果正在运行）。",
        });
        const submitted = submitSettingsCommand(
          { action: "exit", reason: "settings", expiresAt: Date.now() + 10000 },
          "退出请求已提交，正在停止 HUD...",
          { preserveOverlay: true }
        );
        if (submitted) {
          ctx.lifecycle.timeout("settings_restart", () => {
            try {
              if (typeof window.__codexUsageHudRemove === "function") {
                window.__codexUsageHudRemove();
                return;
              }
            } catch (_) {}
            document.getElementById(rootId)?.remove();
            document.getElementById(styleId)?.remove();
          }, 120);
        }
      }

      function checkUpdateFromModal() {
        openSettingsLoading({
          kicker: "正在检查",
          title: "正在检查更新",
          body: "HUD daemon 正在查询 GitHub Release。通常只需 1 到 3 秒。",
          mode: "check-update",
        });
        submitSettingsCommand(
          { action: "checkUpdate" },
          "检查更新请求已提交，等待 HUD daemon 查询 GitHub Release...",
          { preserveOverlay: true }
        );
      }

      function installUpdateFromModal() {
        openSettingsLoading({
          kicker: "正在准备",
          title: "正在检查并准备安装更新",
          body: "HUD daemon 会先检查 GitHub Release，再后台下载 Windows 安装包。",
          mode: "install-update",
        });
        submitSettingsCommand(
          { action: "installUpdate" },
          "安装更新请求已提交，等待 HUD daemon 下载并启动安装器...",
          { preserveOverlay: true }
        );
      }

      function openSettingsExitConfirm() {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="确认退出 HUD">
            <div class="codex-usage-hud-settings-confirm-kicker">退出 HUD</div>
            <div class="codex-usage-hud-settings-confirm-title">完全退出并停止守护进程？</div>
            <div class="codex-usage-hud-settings-confirm-body">这会完全退出 HUD，并停止后台守护进程（如果当前正在运行）。\n\n是否继续？</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-exit-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-exit-confirm" data-primary="true">退出 HUD</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      function runUpdateAction() {
        const state = currentUpdateState();
        let pending = "更新操作请求已提交，等待 HUD daemon 处理...";
        if (String(state?.phase || "") === "downloading") {
          pending = "暂停下载请求已提交...";
        } else if (String(state?.phase || "") === "paused") {
          pending = "继续下载请求已提交...";
        } else if (String(state?.phase || "") === "ready") {
          pending = "正在打开已下载的安装程序...";
        }
        submitSettingsCommand(
          { action: "updateAction" },
          pending
        );
      }

      function dismissWarningsToday() {
        const root = document.getElementById(rootId);
        if (root) {
          setText(root, "topWarnings", "");
          root.querySelectorAll('[data-field-panel="topWarnings"]').forEach((node) => {
            node.hidden = true;
          });
        }
        submitSettingsCommand(
          { action: "dismissWarningsToday" },
          "今天不再显示预算预警。"
        );
      }

      function priceClipboardValues(text) {
        const values = String(text ?? "").trim().split(/\s+/).filter(Boolean);
        if (values.length !== 5 || !values[0]) return null;
        const prices = values.slice(1).map((line) => {
          const value = line.replace(/^\$\s*/, "").trim();
          return /^(?:\d+(?:\.\d*)?|\.\d+)$/.test(value) ? value : "";
        });
        if (prices.some((value) => !value)) return null;
        return {
          model: values[0],
          input: prices[0],
          cached_input: prices[1],
          cache_write: prices[2],
          output: prices[3],
        };
      }

      function fillPriceRowFromClipboard(row, text) {
        if (!row?.matches?.('[data-price-row="true"]')) return false;
        const values = priceClipboardValues(text);
        if (!values) return false;
        const fields = [
          "model",
          "input",
          "cached_input",
          "cache_write",
          "output",
        ].map((name) => row.querySelector(`[data-price-field="${name}"]`));
        if (fields.some((field) => !field)) return false;
        const names = ["model", "input", "cached_input", "cache_write", "output"];
        fields.forEach((field, index) => {
          field.value = values[names[index]];
        });
        return true;
      }

      function addModelPriceRow(initialModel = "") {
        const rows = document.querySelector(`#${settingsModalId} [data-price-rows="true"]`);
        if (!rows) return;
        const row = document.createElement("div");
        row.className = "codex-usage-hud-price-row";
        row.dataset.priceRow = "true";
        row.innerHTML = `
          <input data-price-field="model" value="${escapeHtml(initialModel)}" aria-label="模型">
          <input data-price-field="input" type="number" min="0" step="0.000001" value="0" aria-label="输入单价">
          <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="0" aria-label="缓存读取单价">
          <input data-price-field="cache_write" type="number" min="0" step="0.000001" value="0" aria-label="缓存写入单价">
          <input data-price-field="output" type="number" min="0" step="0.000001" value="0" aria-label="输出单价">
          <input class="codex-usage-hud-price-advanced" data-price-field="provider" value="" aria-label="渠道">
          <input class="codex-usage-hud-price-advanced" data-price-field="base_url" value="" aria-label="Base URL">
        `;
        rows.appendChild(row);
        markSettingsProviderDirty();
        const target = initialModel ? row.querySelector('[data-price-field="input"]') : row.querySelector("input");
        target?.focus?.();
      }

      function openSettingsDiscardConfirm() {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="放弃待应用的模型单价修改">
            <div class="codex-usage-hud-settings-confirm-kicker">未保存修改</div>
            <div class="codex-usage-hud-settings-confirm-title">关闭设置并放弃修改？</div>
            <div class="codex-usage-hud-settings-confirm-body">${settingsDirtyProviders.size} 个 Provider 仍有待应用的模型单价修改。</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-discard-cancel" data-variant="ghost">继续编辑</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-discard-confirm" data-primary="true">放弃修改</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      function closeSettingsModal({ force = false } = {}) {
        const modal = document.getElementById(settingsModalId);
        if (!modal) return;
        captureSettingsProviderForm();
        if (!force && settingsDirtyProviders.size) {
          openSettingsDiscardConfirm();
          return;
        }
        closeCodexCliDialog();
        closeSessionTransferDialog();
        closeSettingsConfirm();
        clearSettingsRestartPrompt();
        if (settingsActiveTab === "backgroundUsage") {
          captureBackgroundUsageScrollPositions();
          clearBackgroundUsageRequestTimeout("query");
          clearBackgroundUsageRequestTimeout("detail");
        }
        modal.hidden = true;
        writeSettingsUiState(false, settingsActiveTab);
        ensureRestReminderCountdownTicker();
        stopSessionCleanupElapsedTicker();
        stopSessionCleanupSearchTimer();
        sessionCleanupState.sessionIndexRefreshing = false;
        detachSessionIndexUi();
        sessionCleanupState.sessionIndexControlRequestId = "";
        resetSessionCleanupPendingRequests();
        settingsProviderDraft = null;
        settingsDirtyProviders.clear();
        codexProviderDrafts.clear();
        codexProviderDirty.clear();
        syncCodexCliQuickLaunchMenu();
      }

      function applySettingsPayload(root, payload) {
        themeDomain.apply(root, payload || {});
        renderUpdateButtons(root, payload || {});
        applySettingsCommandStatus(payload || {});
        restReminderDomain.apply(root, payload || {});
        refreshComposerBadgeState(root);
        syncCodexCliQuickLaunchMenu();
      }

      function codexCliQuickLaunchMenuProviders(settings = hudSettingsFromPayload()) {
        const modal = document.getElementById(settingsModalId);
        const useDraft = !!settingsProviderDraft && !!modal && !modal.hidden;
        const available = useDraft
          ? settingsProviderDraft.order
          : settingsProviderNames(settings);
        const configured = useDraft
          ? Array.from(settingsProviderDraft.quickLaunchProviders || [])
          : (Array.isArray(settings.quick_launch_providers) ? settings.quick_launch_providers : []);
        const allowed = new Set(available.map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean));
        const seen = new Set();
        return configured
          .map((provider) => String(provider || "").trim().toLowerCase())
          .filter((provider) => provider && allowed.has(provider) && !seen.has(provider) && seen.add(provider));
      }

      function codexDesktopApplicationMenu() {
        return Array.from(document.querySelectorAll('[role="menubar"]')).find((node) => (
          !node.closest?.(`#${rootId}`)
          && node.querySelector?.('button[id^="application-menu-trigger-"], [role="menuitem"][aria-haspopup="menu"]')
        )) || null;
      }

      function ensureCodexCliQuickLaunchMenuStyle() {
        const styleId = "codex-usage-hud-codex-cli-menu-style";
        if (document.getElementById(styleId) || !document.head) return;
        const style = document.createElement("style");
        style.id = styleId;
        style.textContent = `
          [data-codex-usage-hud-cli-menu-surface="true"] {
            position: fixed;
            z-index: 2147483000;
            min-width: 190px;
            max-width: min(300px, calc(100vw - 12px));
            padding: 4px;
            border: 1px solid color-mix(in srgb, var(--border-subtle, #47505c) 80%, transparent);
            border-radius: 8px;
            background: var(--surface-elevated, #20252d);
            color: var(--text-primary, #f2f4f7);
            box-shadow: 0 14px 34px rgba(0, 0, 0, .36);
            pointer-events: auto;
            -webkit-app-region: no-drag;
          }
          [data-codex-usage-hud-cli-menu-surface="true"] [role="menuitem"] {
            width: 100%;
            min-height: 30px;
            display: flex;
            align-items: center;
            gap: 10px;
            box-sizing: border-box;
            padding: 5px 10px;
            border: 0;
            border-radius: 5px;
            background: transparent;
            color: inherit;
            font: inherit;
            text-align: left;
            cursor: pointer;
            pointer-events: auto;
            -webkit-app-region: no-drag;
          }
          [data-codex-usage-hud-cli-provider-row="true"] {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            gap: 2px;
          }
          [data-codex-usage-hud-cli-provider-row="true"] [data-codex-usage-hud-cli-provider="true"] {
            min-width: 0;
          }
          [data-codex-usage-hud-cli-provider-workdirs="true"] {
            min-width: 0;
            min-height: 30px;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 5px 8px;
            border: 0;
            border-radius: 5px;
            background: transparent;
            color: var(--text-tertiary, #a9b2bf);
            font: 600 11px/1 system-ui, sans-serif;
            cursor: pointer;
            pointer-events: auto;
            -webkit-app-region: no-drag;
          }
          [data-codex-usage-hud-cli-provider-workdirs="true"]:hover,
          [data-codex-usage-hud-cli-provider-workdirs="true"]:focus-visible {
            outline: none;
            background: var(--surface-hover, rgba(255, 255, 255, .1));
            color: inherit;
          }
          [data-codex-usage-hud-cli-workdir-surface="true"] {
            min-width: 250px;
            max-width: min(360px, calc(100vw - 12px));
          }
          [data-codex-usage-hud-cli-workdir="true"] {
            align-items: center;
          }
          [data-codex-usage-hud-cli-workdir="true"] > span:first-child {
            min-width: 0;
            display: grid;
            gap: 2px;
            flex: 1 1 auto;
          }
          [data-codex-usage-hud-cli-workdir="true"] strong,
          [data-codex-usage-hud-cli-workdir="true"] small {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          [data-codex-usage-hud-cli-workdir="true"] strong {
            font: 600 12px/1.25 system-ui, sans-serif;
          }
          [data-codex-usage-hud-cli-workdir="true"] small {
            color: var(--text-tertiary, #a9b2bf);
            font: 400 10px/1.25 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          }
          [data-codex-usage-hud-cli-workdir="true"][data-default="true"] {
            background: color-mix(in srgb, var(--accent, #7aa2ff) 16%, transparent);
            box-shadow: inset 2px 0 0 var(--accent, #7aa2ff);
          }
          [data-codex-usage-hud-cli-workdir-default="true"] {
            flex: 0 0 auto;
            padding: 2px 5px;
            border-radius: 4px;
            color: var(--accent, #7aa2ff);
            background: color-mix(in srgb, var(--accent, #7aa2ff) 13%, transparent);
            font: 700 10px/1 system-ui, sans-serif;
          }
          [data-codex-usage-hud-cli-menu-toggle="true"] {
            pointer-events: auto;
            -webkit-app-region: no-drag;
          }
          [data-codex-usage-hud-cli-menu-surface="true"] [role="menuitem"]:hover,
          [data-codex-usage-hud-cli-menu-surface="true"] [role="menuitem"]:focus-visible {
            outline: none;
            background: var(--surface-hover, rgba(255, 255, 255, .1));
          }
          [data-codex-usage-hud-cli-menu-surface="true"] [data-codex-usage-hud-cli-provider="true"]::before {
            content: ">_";
            color: var(--text-tertiary, #a9b2bf);
            font: 700 11px Consolas, "Cascadia Mono", ui-monospace, monospace;
          }
          [data-codex-cli-quick-launch="true"] {
            position: fixed;
            inset: 0;
            z-index: 2147483100;
            display: grid;
            place-items: center;
            padding: 16px;
            background: rgba(8, 11, 16, .42);
            backdrop-filter: blur(3px);
            animation: codexUsageHudCliQuickFade 140ms ease-out both;
          }
          .codex-usage-hud-cli-quick-surface {
            width: min(390px, calc(100vw - 32px));
            box-sizing: border-box;
            padding: 16px;
            border: 1px solid color-mix(in srgb, var(--border-subtle, #47505c) 72%, transparent);
            border-radius: 12px;
            background: color-mix(in srgb, var(--surface-elevated, #20252d) 96%, #111722);
            color: var(--text-primary, #f2f4f7);
            box-shadow: 0 22px 60px rgba(0, 0, 0, .42);
            animation: codexUsageHudCliQuickRise 180ms cubic-bezier(.2, .8, .2, 1) both;
          }
          .codex-usage-hud-cli-quick-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
          }
          .codex-usage-hud-cli-quick-head > div {
            display: flex;
            align-items: baseline;
            gap: 9px;
            min-width: 0;
          }
          .codex-usage-hud-cli-quick-head span {
            color: var(--text-tertiary, #a9b2bf);
            font: 700 10px/1 Consolas, "Cascadia Mono", ui-monospace, monospace;
            letter-spacing: .14em;
          }
          .codex-usage-hud-cli-quick-head strong {
            overflow: hidden;
            font: 650 12px/1.2 system-ui, sans-serif;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .codex-usage-hud-cli-quick-head button {
            width: 26px;
            height: 26px;
            padding: 0;
            border: 0;
            border-radius: 6px;
            background: transparent;
            color: var(--text-tertiary, #a9b2bf);
            font: 400 19px/1 system-ui, sans-serif;
            cursor: pointer;
          }
          .codex-usage-hud-cli-quick-head button:hover { background: rgba(255, 255, 255, .08); }
          .codex-usage-hud-cli-quick-title {
            margin-top: 16px;
            font: 650 16px/1.35 system-ui, sans-serif;
          }
          .codex-usage-hud-cli-quick-body {
            margin-top: 7px;
            color: var(--text-secondary, #c7ced8);
            font: 400 12px/1.55 system-ui, sans-serif;
          }
          .codex-usage-hud-cli-quick-track {
            height: 3px;
            margin-top: 16px;
            overflow: hidden;
            border-radius: 999px;
            background: rgba(255, 255, 255, .09);
          }
          .codex-usage-hud-cli-quick-track span {
            display: block;
            width: 42%;
            height: 100%;
            border-radius: inherit;
            background: var(--accent, #7aa2ff);
            animation: codexUsageHudCliQuickProgress 1.15s ease-in-out infinite;
          }
          .codex-usage-hud-cli-quick-actions {
            display: flex;
            justify-content: flex-end;
            gap: 8px;
            margin-top: 16px;
          }
          .codex-usage-hud-cli-quick-actions button {
            min-height: 30px;
            padding: 5px 12px;
            border: 1px solid rgba(255, 255, 255, .12);
            border-radius: 7px;
            background: transparent;
            color: inherit;
            font: 600 12px/1 system-ui, sans-serif;
            cursor: pointer;
          }
          .codex-usage-hud-cli-quick-actions button:hover { background: rgba(255, 255, 255, .08); }
          .codex-usage-hud-cli-quick-actions button[data-primary="true"] {
            border-color: color-mix(in srgb, var(--accent, #7aa2ff) 65%, transparent);
            background: color-mix(in srgb, var(--accent, #7aa2ff) 18%, transparent);
          }
          @keyframes codexUsageHudCliQuickFade { from { opacity: 0; } to { opacity: 1; } }
          @keyframes codexUsageHudCliQuickRise {
            from { opacity: 0; transform: translateY(7px) scale(.985); }
            to { opacity: 1; transform: translateY(0) scale(1); }
          }
          @keyframes codexUsageHudCliQuickProgress {
            0% { transform: translateX(-115%); }
            55%, 100% { transform: translateX(260%); }
          }
          .codex-usage-hud-cli-provider-label {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            cursor: default;
            user-select: none;
            opacity: .78;
          }
          .codex-usage-hud-cli-provider-label:empty { display: none; }
          .codex-usage-hud-cli-provider-label::before {
            content: "";
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: color-mix(in srgb, currentColor 55%, transparent);
          }
          .codex-usage-hud-cli-provider-label[data-mismatch="true"] {
            color: var(--status-warning, #e2a23a);
            opacity: 1;
          }
          .codex-usage-hud-cli-provider-label[data-mismatch="true"]::before {
            background: var(--status-warning, #e2a23a);
          }
          @media (prefers-reduced-motion: reduce) {
            [data-codex-cli-quick-launch="true"],
            .codex-usage-hud-cli-quick-surface,
            .codex-usage-hud-cli-quick-track span { animation: none; }
          }
        `;
        document.head.appendChild(style);
      }

      function closeCodexCliQuickLaunchMenu() {
        codexCliQuickLaunchMenuState.open = false;
        if (codexCliQuickLaunchMenuState.toggle) {
          codexCliQuickLaunchMenuState.toggle.setAttribute("aria-expanded", "false");
          codexCliQuickLaunchMenuState.toggle.dataset.state = "closed";
        }
        codexCliQuickLaunchMenuState.surface?.remove();
        codexCliQuickLaunchMenuState.surface = null;
        codexCliQuickLaunchMenuState.workdirSurface?.remove();
        codexCliQuickLaunchMenuState.workdirSurface = null;
        codexCliQuickLaunchMenuState.workdirProvider = "";
        codexCliQuickLaunchMenuState.workdirToggle = null;
        codexCliQuickLaunchMenuState.workdirExpanded = false;
      }

      function closeCodexCliQuickLaunchWorkdirMenu() {
        codexCliQuickLaunchMenuState.workdirSurface?.remove();
        codexCliQuickLaunchMenuState.workdirSurface = null;
        codexCliQuickLaunchMenuState.workdirProvider = "";
        codexCliQuickLaunchMenuState.workdirToggle = null;
        codexCliQuickLaunchMenuState.workdirExpanded = false;
      }

      function codexCliWorkdirMenuItemHtml(provider, item, index) {
        const path = String(item?.path || "");
        const label = String(item?.label || codexCliWorkdirRecentLabel(path));
        const defaultState = codexCliDefaultWorkdir(provider);
        const defaultWorkdir = !defaultState.noProject && !!defaultState.path
          && codexCliTransferWorkdirIdentity(defaultState.path) === codexCliTransferWorkdirIdentity(path);
        return `
          <button type="button" role="menuitem" tabindex="-1" data-codex-usage-hud-cli-workdir="true" data-provider="${escapeHtml(provider)}" data-workdir="${escapeHtml(path)}" ${defaultWorkdir ? 'data-default="true"' : ""} title="${escapeHtml(path)}">
            <span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(path)}</small></span>
            ${defaultWorkdir ? '<span data-codex-usage-hud-cli-workdir-default="true">默认</span>' : ""}
          </button>
        `;
      }

      function renderCodexCliQuickLaunchWorkdirMenu(provider, toggle, expanded = false) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (!normalizedProvider || !toggle?.isConnected) {
          closeCodexCliQuickLaunchWorkdirMenu();
          return;
        }
        let surface = codexCliQuickLaunchMenuState.workdirSurface;
        if (!surface?.isConnected) {
          surface = document.createElement("div");
          surface.dataset.codexUsageHudCliMenuSurface = "true";
          surface.dataset.codexUsageHudCliWorkdirSurface = "true";
          surface.setAttribute("role", "menu");
          document.body.appendChild(surface);
          codexCliQuickLaunchMenuState.workdirSurface = surface;
        }
        const recents = codexCliRecentWorkdirs(normalizedProvider);
        const visible = recents.slice(0, expanded ? 10 : 3);
        const defaultState = codexCliDefaultWorkdir(normalizedProvider);
        surface.setAttribute("aria-label", `${normalizedProvider} 工作目录`);
        surface.innerHTML = `
          <button type="button" role="menuitem" tabindex="-1" data-codex-usage-hud-cli-workdir="true" data-provider="${escapeHtml(normalizedProvider)}" data-no-project="true" ${defaultState.noProject ? 'data-default="true"' : ""}>
            <span><strong>不在项目中工作</strong><small>清除 Codex 项目上下文</small></span>
            ${defaultState.noProject ? '<span data-codex-usage-hud-cli-workdir-default="true">默认</span>' : ""}
          </button>
          ${visible.map((item, index) => codexCliWorkdirMenuItemHtml(normalizedProvider, item, index)).join("")}
          ${!expanded && recents.length > 3 ? `<button type="button" role="menuitem" tabindex="-1" data-codex-usage-hud-cli-workdir-more="true" data-provider="${escapeHtml(normalizedProvider)}">更多...</button>` : ""}
        `;
        const rect = toggle.getBoundingClientRect();
        const width = Math.min(360, Math.max(250, surface.scrollWidth || 250));
        const rightAligned = rect.right + 4 + width > innerWidth - 6;
        const left = rightAligned ? rect.left - width - 4 : rect.right + 4;
        surface.style.left = `${Math.round(Math.max(6, Math.min(left, innerWidth - width - 6)))}px`;
        surface.style.top = `${Math.round(Math.max(6, Math.min(rect.top, innerHeight - 8)))}px`;
        surface.style.maxHeight = `${Math.max(120, innerHeight - 16)}px`;
        surface.style.overflowY = "auto";
        surface.hidden = false;
        codexCliQuickLaunchMenuState.workdirProvider = normalizedProvider;
        codexCliQuickLaunchMenuState.workdirToggle = toggle;
        codexCliQuickLaunchMenuState.workdirExpanded = expanded;
      }

      function toggleCodexCliQuickLaunchWorkdirMenu(provider, toggle) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (
          codexCliQuickLaunchMenuState.workdirSurface?.isConnected
          && codexCliQuickLaunchMenuState.workdirProvider === normalizedProvider
          && codexCliQuickLaunchMenuState.workdirToggle === toggle
        ) {
          closeCodexCliQuickLaunchWorkdirMenu();
          return;
        }
        closeCodexCliQuickLaunchWorkdirMenu();
        renderCodexCliQuickLaunchWorkdirMenu(normalizedProvider, toggle, false);
      }

      function renderCodexCliQuickLaunchMenu() {
        const toggle = codexCliQuickLaunchMenuState.toggle;
        const providers = codexCliQuickLaunchMenuState.providers;
        if (!toggle?.isConnected || !providers.length) {
          closeCodexCliQuickLaunchMenu();
          return;
        }
        let surface = codexCliQuickLaunchMenuState.surface;
        if (!surface?.isConnected) {
          surface = document.createElement("div");
          surface.dataset.codexUsageHudCliMenuSurface = "true";
          surface.setAttribute("role", "menu");
          surface.setAttribute("aria-label", "启动CLI Provider");
          document.body.appendChild(surface);
          codexCliQuickLaunchMenuState.surface = surface;
        }
        surface.innerHTML = providers.map((provider) => {
          const displayName = providerDisplayName(hudSettingsFromPayload(), provider);
          return `
          <div data-codex-usage-hud-cli-provider-row="true">
            <button type="button" role="menuitem" tabindex="-1" data-codex-usage-hud-cli-provider="true" data-provider="${escapeHtml(provider)}">${escapeHtml(displayName)}</button>
            <button type="button" tabindex="-1" data-codex-usage-hud-cli-provider-workdirs="true" data-provider="${escapeHtml(provider)}" aria-label="选择 ${escapeHtml(displayName)} 的工作目录">工作目录 <span aria-hidden="true">›</span></button>
          </div>
        `;
        }).join("");
        const rect = toggle.getBoundingClientRect();
        const width = Math.min(300, Math.max(190, surface.scrollWidth || 190));
        const left = Math.max(6, Math.min(rect.left, innerWidth - width - 6));
        surface.style.left = `${Math.round(left)}px`;
        surface.style.top = `${Math.round(Math.min(innerHeight - 8, rect.bottom + 4))}px`;
        surface.style.maxHeight = `${Math.max(120, innerHeight - rect.bottom - 16)}px`;
        surface.style.overflowY = "auto";
        surface.hidden = false;
        codexCliQuickLaunchMenuState.open = true;
        toggle.setAttribute("aria-expanded", "true");
        toggle.dataset.state = "open";
      }

      function toggleCodexCliQuickLaunchMenu() {
        if (codexCliQuickLaunchMenuState.open) {
          closeCodexCliQuickLaunchMenu();
          return;
        }
        renderCodexCliQuickLaunchMenu();
      }

      function openCodexCliFromApplicationMenu(provider) {
        const normalizedProvider = String(provider || "").trim().toLowerCase();
        if (!normalizedProvider) return;
        const requestedWorkdir = codexCliState.requestedWorkdir;
        closeCodexCliQuickLaunchMenu();
        if (
          settingsDirtyProviders.has(normalizedProvider)
          || codexProviderDirty.has(normalizedProvider)
        ) {
          const modal = document.getElementById(settingsModalId);
          const resetProviderDraft = !modal || modal.hidden;
          if (!modal || modal.hidden || settingsActiveTab !== "settings") {
            renderSettingsModal("settings", "", { resetProviderDraft });
          }
          openCodexCliDialog(normalizedProvider);
          return;
        }
        if (requestedWorkdir) openCodexCliQuickLaunch(normalizedProvider, requestedWorkdir);
        else openCodexCliQuickLaunch(normalizedProvider);
      }

      function handleCodexCliQuickLaunchMenuClick(event) {
        const quickAction = event.target?.closest?.('[data-codex-cli-quick-action]');
        if (quickAction && codexCliQuickLaunchLayer()?.contains(quickAction)) {
          event.preventDefault();
          event.stopPropagation();
          const action = String(quickAction.dataset.codexCliQuickAction || "");
          if (action === "stop") stopCodexCliQuickLaunch();
          else if (action === "configure") openCodexCliQuickLaunchConfiguration();
          else dismissCodexCliQuickLaunch();
          return;
        }
        const workdirMore = event.target?.closest?.('[data-codex-usage-hud-cli-workdir-more="true"]');
        if (workdirMore && codexCliQuickLaunchMenuState.workdirSurface?.contains(workdirMore)) {
          event.preventDefault();
          event.stopPropagation();
          renderCodexCliQuickLaunchWorkdirMenu(
            workdirMore.dataset.provider || "",
            codexCliQuickLaunchMenuState.workdirToggle,
            true,
          );
          return;
        }
        const workdirItem = event.target?.closest?.('[data-codex-usage-hud-cli-workdir="true"]');
        if (workdirItem && codexCliQuickLaunchMenuState.workdirSurface?.contains(workdirItem)) {
          event.preventDefault();
          event.stopPropagation();
          const requestedWorkdir = workdirItem.dataset.noProject === "true"
            ? { noProject: true }
            : { workdir: workdirItem.dataset.workdir || "" };
          codexCliState.requestedWorkdir = requestedWorkdir;
          openCodexCliFromApplicationMenu(workdirItem.dataset.provider || "");
          return;
        }
        const workdirToggle = event.target?.closest?.('[data-codex-usage-hud-cli-provider-workdirs="true"]');
        if (workdirToggle && codexCliQuickLaunchMenuState.surface?.contains(workdirToggle)) {
          event.preventDefault();
          event.stopPropagation();
          toggleCodexCliQuickLaunchWorkdirMenu(workdirToggle.dataset.provider || "", workdirToggle);
          return;
        }
        const providerItem = event.target?.closest?.('[data-codex-usage-hud-cli-provider="true"]');
        if (providerItem && codexCliQuickLaunchMenuState.surface?.contains(providerItem)) {
          event.preventDefault();
          event.stopPropagation();
          openCodexCliFromApplicationMenu(providerItem.dataset.provider || "");
          return;
        }
        const toggle = event.target?.closest?.('[data-codex-usage-hud-cli-menu-toggle="true"]');
        if (toggle && codexCliQuickLaunchMenuState.toggle === toggle) {
          event.preventDefault();
          event.stopPropagation();
          toggleCodexCliQuickLaunchMenu();
        }
      }

      function codexCliQuickLaunchProviderLabelState() {
        const active = String(currentPayload()?.activeSessionProvider || "").trim().toLowerCase();
        const fallback = String(hudSettingsFromPayload().app_provider || "").trim().toLowerCase();
        if (active) {
          const mismatch = !!fallback && fallback !== active;
          const settings = hudSettingsFromPayload();
          const activeName = providerDisplayName(settings, active);
          const fallbackName = providerDisplayName(settings, fallback);
          return {
            provider: active,
            mismatch,
            text: `供应商 ${activeName}`,
            title: mismatch
              ? `当前会话供应商：${activeName}（config.toml 默认：${fallbackName}）`
              : `当前会话供应商：${activeName}`,
          };
        }
        if (fallback) {
          const fallbackName = providerDisplayName(hudSettingsFromPayload(), fallback);
          return {
            provider: fallback,
            mismatch: false,
            text: `供应商 ${fallbackName}（默认）`,
            title: `默认供应商：${fallbackName}（当前无活跃会话）`,
          };
        }
        return null;
      }

      function syncCodexCliQuickLaunchProviderLabel(menubar, toggle) {
        let label = menubar.querySelector('[data-codex-usage-hud-cli-provider-label="true"]');
        const state = codexCliQuickLaunchProviderLabelState();
        if (!state) {
          label?.remove();
          return;
        }
        if (!label) {
          label = document.createElement("span");
          label.dataset.codexUsageHudCliProviderLabel = "true";
          const template = menubar.querySelector('button[role="menuitem"][aria-haspopup="menu"]');
          label.className = `${template?.className || ""} codex-usage-hud-cli-provider-label`.trim();
          label.setAttribute("aria-label", "当前会话供应商");
        }
        // The body-wide MutationObserver re-runs this sync on every DOM write,
        // so steady state must write nothing: touch the DOM only when the
        // placement or content actually differs, otherwise the renderer ends
        // in a mutation/observer feedback loop and freezes the app.
        if (label.parentElement !== menubar || label.previousElementSibling !== toggle) {
          toggle.insertAdjacentElement("afterend", label);
        }
        if (
          label.dataset.provider !== state.provider
          || label.dataset.mismatch !== String(state.mismatch)
          || label.textContent !== state.text
          || label.title !== state.title
        ) {
          label.dataset.provider = state.provider;
          label.dataset.mismatch = String(state.mismatch);
          label.textContent = state.text;
          label.title = state.title;
        }
      }

      function syncCodexCliQuickLaunchMenu() {
        const providers = codexCliQuickLaunchMenuProviders();
        const menubar = codexDesktopApplicationMenu();
        const existing = document.querySelector('[data-codex-usage-hud-cli-menu-toggle="true"]');
        if (!providers.length || !menubar) {
          closeCodexCliQuickLaunchMenu();
          existing?.remove();
          menubar?.querySelector('[data-codex-usage-hud-cli-provider-label="true"]')?.remove();
          codexCliQuickLaunchMenuState.toggle = null;
          codexCliQuickLaunchMenuState.providers = [];
          return;
        }
        let toggle = menubar.querySelector('[data-codex-usage-hud-cli-menu-toggle="true"]');
        if (!toggle) {
          toggle = document.createElement("button");
          toggle.type = "button";
          toggle.id = "codex-usage-hud-cli-menu-trigger";
          toggle.setAttribute("role", "menuitem");
          toggle.setAttribute("aria-haspopup", "menu");
          toggle.setAttribute("aria-expanded", "false");
          toggle.dataset.state = "closed";
          toggle.dataset.codexUsageHudCliMenuToggle = "true";
          const template = menubar.querySelector('button[role="menuitem"][aria-haspopup="menu"]');
          toggle.className = template?.className || "no-drag rounded-md border border-transparent px-2.5 py-1 text-base font-normal leading-none outline-none text-tertiary";
          toggle.textContent = "启动CLI";
          const anchor = menubar.querySelector("#application-menu-content-anchor");
          if (anchor) menubar.insertBefore(toggle, anchor);
          else menubar.appendChild(toggle);
        }
        codexCliQuickLaunchMenuState.toggle = toggle;
        const previousProviders = codexCliQuickLaunchMenuState.providers.join("\u0000");
        codexCliQuickLaunchMenuState.providers = providers;
        syncCodexCliQuickLaunchProviderLabel(menubar, toggle);
        if (codexCliQuickLaunchMenuState.open && previousProviders !== providers.join("\u0000")) {
          renderCodexCliQuickLaunchMenu();
        }
      }

      function installCodexCliQuickLaunchMenu() {
        ensureCodexCliQuickLaunchMenuStyle();
        ctx.lifecycle.listen("codex_cli_quick_launch_menu", document, "click", handleCodexCliQuickLaunchMenuClick, true);
        ctx.lifecycle.listen("codex_cli_quick_launch_menu", document, "pointerdown", (event) => {
          if (!codexCliQuickLaunchMenuState.open) return;
          const surface = codexCliQuickLaunchMenuState.surface;
          const workdirSurface = codexCliQuickLaunchMenuState.workdirSurface;
          const toggle = codexCliQuickLaunchMenuState.toggle;
          if (
            !surface?.contains(event.target)
            && !workdirSurface?.contains(event.target)
            && event.target !== toggle
            && !toggle?.contains(event.target)
          ) {
            closeCodexCliQuickLaunchMenu();
          }
        }, true);
        ctx.lifecycle.listen("codex_cli_quick_launch_menu", document, "keydown", (event) => {
          if (event.key !== "Escape") return;
          if (codexCliQuickLaunchLayer()) {
            event.preventDefault();
            event.stopPropagation();
            dismissCodexCliQuickLaunch();
            return;
          }
          if (codexCliQuickLaunchMenuState.workdirSurface) {
            event.preventDefault();
            closeCodexCliQuickLaunchWorkdirMenu();
            return;
          }
          if (codexCliQuickLaunchMenuState.open) {
            event.preventDefault();
            closeCodexCliQuickLaunchMenu();
          }
        }, true);
        ctx.lifecycle.listen("codex_cli_quick_launch_menu", window, "resize", () => {
          if (codexCliQuickLaunchMenuState.open) renderCodexCliQuickLaunchMenu();
          if (codexCliQuickLaunchMenuState.workdirSurface) {
            renderCodexCliQuickLaunchWorkdirMenu(
              codexCliQuickLaunchMenuState.workdirProvider,
              codexCliQuickLaunchMenuState.workdirToggle,
              codexCliQuickLaunchMenuState.workdirExpanded,
            );
          }
        }, { passive: true });
        if (document.body) {
          const observer = ctx.observers.set("codex_cli_quick_launch_menu", new MutationObserver(() => {
            if (!document.getElementById(rootId)) return;
            syncCodexCliQuickLaunchMenu();
          }));
          observer?.observe(document.body, {
            childList: true,
            subtree: true,
          });
        }
        syncCodexCliQuickLaunchMenu();
      }

      function disposeCodexCliQuickLaunchMenu() {
        ctx.observers.clear("codex_cli_quick_launch_menu");
        closeCodexCliQuickLaunchMenu();
        document.querySelector('[data-codex-usage-hud-cli-menu-toggle="true"]')?.remove();
        document.querySelector('[data-codex-usage-hud-cli-provider-label="true"]')?.remove();
        document.getElementById("codex-usage-hud-codex-cli-menu-style")?.remove();
        codexCliQuickLaunchLayer()?.remove();
        codexCliQuickLaunchMenuState.toggle = null;
        codexCliQuickLaunchMenuState.providers = [];
      }

    function install() {
      installCodexCliQuickLaunchMenu();
      return true;
    }

    function apply(root, payload) {
      return applySettingsPayload(root, payload || {});
    }

    function dispose() {
      disposeCodexCliQuickLaunchMenu();
      stopSessionTransferElapsedTicker();
      return true;
    }

    return {
      install,
      apply,
      dispose,
      settingsChromeMarkup,
      loadPersistedSupportImages,
      persistSupportImages,
      defaultHudSettings,
      hudSettingsFromPayload,
      normalizePriceModel,
      priceModelPatternMatches,
      configuredPriceModels,
      observedPriceModels,
      unknownPriceModels,
      settingsBridgeUrl,
      settingsPathLabel,
      appVersion,
      currentUpdateState,
      workOverlaySelectableMax,
      desktopOverlayDependency,
      desktopOverlayDependencyHtml,
      syncDesktopOverlayDependency,
      updateStateFromPayload,
      updateActionGlyph,
      renderUpdateButtons,
      thresholdText,
      priceRowsHtml,
      detectedPriceModelsHtml,
      settingsProviderNames,
      cloneSettingsPriceTable,
      suggestedProviderEnvironmentKey,
      ensureCodexProviderDraft,
      providerDraftFromSettings,
      ensureSettingsProviderDraft,
      openCodexCliDialog,
      closeCodexCliDialog,
      refreshCodexCliDialog,
      requestCodexCliModels,
      codexCliCopyCommand,
      codexCliFieldInput,
      codexCliFieldChange,
      launchCodexCliFromDialog,
      chatTestCodexCliFromDialog,
       settingsProviderTabBadge,
       settingsProviderMeta,
       settingsProviderTabHtml,
       settingsProviderTabsHtml,
       settingsProviderEditorHtml,
      applyPricingToAllProviders,
       syncCurrentProviderPricesToOthers,
       revealSettingsProviderTab,
       syncSettingsProviderTabNavigation,
       scrollSettingsProviderRail,
       renderSettingsProviderTabs,
      captureSettingsProviderForm,
      priceClipboardValues,
      fillPriceRowFromClipboard,
      updateSettingsProviderDraftStatus,
      markSettingsProviderDirty,
       renderSettingsProviderEditor,
       activateSettingsProviderTab,
       switchSettingsProvider,
      providerConfigDialogLayer,
      toggleProviderApiKeyVisibility,
      testProviderConnectivityFromDialog,
      applyProviderConnectivityStatus,
      showProviderChatTest,
      chatTestProviderFromDialog,
      applyProviderChatTestStatus,
      openProviderConfigDialog,
      applyProviderConfigDialog,
      openProviderRestartConfirmDialog,
      closeProviderRestartConfirmDialog,
      applyProviderConfigWithRestartDecision,
      openProviderDeleteDialog,
      confirmProviderDeleteDialog,
      typedSettingsRequestId,
      renderSettingsModal,
      restoreOpenSettingsModal,
      settingsPanelHtml,
      restReminderStatusTitle,
      supportPanelHtml,
      aboutPanelHtml,
      setSettingsStatus,
      setSettingsRestartVisible,
      setSettingsActionState,
      formatRestReminderClock,
      formatRestReminderInputTime,
      formatRestReminderRemaining,
      syncRestReminderCountdown,
      ensureRestReminderCountdownTicker,
      updateAboutActionButtons,
      showSettingsRestartPrompt,
      setSettingsLoadingText,
      syncSettingsUpdateLoading,
      applySettingsCommandStatus,
      refreshSessionIndexIfStale,
      openSessionTransferDialog,
      renderSessionTransferDialog,
      closeSessionTransferDialog,
      requestSessionTransferScan,
      requestSessionTransferCancel,
      submitSessionTransfer,
      sessionTransferSelectableIds,
      syncSessionTransferSelection,
      syncSessionTransferSelectAll,
      moveSessionTransferPage,
      applySessionTransferPayload,
      submitSettingsCommand,
      settingsDialogRoot,
      closeSettingsConfirm,
      openSettingsLoading,
      collectSettingsForm,
      collectCodexProviderUpdates,
      commitSettingsFromDraft,
      settingsWithPersistedPriceTables,
      autoSaveNonPricingSettingsFromModal,
      applyModelPricesFromModal,
      saveSettingsFromModal,
      fetchPricesFromModal,
      confirmPricingEffectiveAt,
      updatePricingApplyAllPreview,
      openPricingImportDialog,
      readPricingImportFile,
      previewPricingImport,
      commitPricingImport,
      copyPricingExample,
      restartHudFromModal,
      restartCodexFromModal,
      installDesktopOverlayFromModal,
      enableDesktopOverlayFromModal,
      exitHudFromModal,
      checkUpdateFromModal,
      installUpdateFromModal,
      openSettingsExitConfirm,
      runUpdateAction,
      dismissWarningsToday,
      addModelPriceRow,
      openSettingsDiscardConfirm,
      closeSettingsModal,
      applySettingsPayload,
      syncCodexCliQuickLaunchMenu,
    };
  }

  const settingsShellDomain = ctx.domains.register(
    "settings_shell",
    createSettingsShellDomain(ctx, shared),
  );
  const {
    settingsChromeMarkup,
    loadPersistedSupportImages,
    persistSupportImages,
    defaultHudSettings,
    hudSettingsFromPayload,
    normalizePriceModel,
    priceModelPatternMatches,
    configuredPriceModels,
    observedPriceModels,
    unknownPriceModels,
    settingsBridgeUrl,
    settingsPathLabel,
    appVersion,
    currentUpdateState,
    workOverlaySelectableMax,
    desktopOverlayDependency,
    desktopOverlayDependencyHtml,
    syncDesktopOverlayDependency,
    updateStateFromPayload,
    updateActionGlyph,
    renderUpdateButtons,
    thresholdText,
    priceRowsHtml,
    detectedPriceModelsHtml,
    settingsProviderNames,
    cloneSettingsPriceTable,
    suggestedProviderEnvironmentKey,
    ensureCodexProviderDraft,
    providerDraftFromSettings,
    ensureSettingsProviderDraft,
    openCodexCliDialog,
    closeCodexCliDialog,
    refreshCodexCliDialog,
    requestCodexCliModels,
    codexCliCopyCommand,
    codexCliFieldInput,
    codexCliFieldChange,
    launchCodexCliFromDialog,
    chatTestCodexCliFromDialog,
    settingsProviderTabBadge,
    settingsProviderMeta,
    settingsProviderTabHtml,
    settingsProviderTabsHtml,
      settingsProviderEditorHtml,
      applyPricingToAllProviders,
    syncCurrentProviderPricesToOthers,
    revealSettingsProviderTab,
    syncSettingsProviderTabNavigation,
    scrollSettingsProviderRail,
    renderSettingsProviderTabs,
      captureSettingsProviderForm,
      priceClipboardValues,
      fillPriceRowFromClipboard,
      updateSettingsProviderDraftStatus,
      markSettingsProviderDirty,
    renderSettingsProviderEditor,
    activateSettingsProviderTab,
    switchSettingsProvider,
    providerConfigDialogLayer,
    syncCodexCliQuickLaunchMenu,
    toggleProviderApiKeyVisibility,
    testProviderConnectivityFromDialog,
    applyProviderConnectivityStatus,
    showProviderChatTest,
    chatTestProviderFromDialog,
    applyProviderChatTestStatus,
    openProviderConfigDialog,
    applyProviderConfigDialog,
    openProviderRestartConfirmDialog,
    closeProviderRestartConfirmDialog,
    applyProviderConfigWithRestartDecision,
    openProviderDeleteDialog,
    confirmProviderDeleteDialog,
    typedSettingsRequestId,
    renderSettingsModal,
    restoreOpenSettingsModal,
    settingsPanelHtml,
    restReminderStatusTitle,
    supportPanelHtml,
    aboutPanelHtml,
    setSettingsStatus,
    setSettingsRestartVisible,
    setSettingsActionState,
    formatRestReminderClock,
    formatRestReminderInputTime,
    formatRestReminderRemaining,
    syncRestReminderCountdown,
    ensureRestReminderCountdownTicker,
    updateAboutActionButtons,
    showSettingsRestartPrompt,
    setSettingsLoadingText,
    syncSettingsUpdateLoading,
    applySettingsCommandStatus,
    openSessionTransferDialog,
    renderSessionTransferDialog,
    closeSessionTransferDialog,
    requestSessionTransferScan,
    submitSessionTransfer,
    sessionTransferSelectableIds,
    syncSessionTransferSelection,
    syncSessionTransferSelectAll,
    moveSessionTransferPage,
    applySessionTransferPayload,
    submitSettingsCommand,
    settingsDialogRoot,
    closeSettingsConfirm,
    openSettingsLoading,
    collectSettingsForm,
    collectCodexProviderUpdates,
    commitSettingsFromDraft,
    settingsWithPersistedPriceTables,
    autoSaveNonPricingSettingsFromModal,
    applyModelPricesFromModal,
    saveSettingsFromModal,
    fetchPricesFromModal,
    confirmPricingEffectiveAt,
    updatePricingApplyAllPreview,
    openPricingImportDialog,
    readPricingImportFile,
    previewPricingImport,
    commitPricingImport,
    copyPricingExample,
    restartHudFromModal,
    restartCodexFromModal,
    installDesktopOverlayFromModal,
    enableDesktopOverlayFromModal,
    exitHudFromModal,
    checkUpdateFromModal,
    installUpdateFromModal,
    openSettingsExitConfirm,
    runUpdateAction,
    dismissWarningsToday,
    addModelPriceRow,
    openSettingsDiscardConfirm,
    closeSettingsModal,
    applySettingsPayload,
  } = settingsShellDomain;
"""

TEXT = _TEXT_PREFIX + SETTINGS_SUPPORT_PANELS + _TEXT_SUFFIX

__all__ = ["TEXT"]
