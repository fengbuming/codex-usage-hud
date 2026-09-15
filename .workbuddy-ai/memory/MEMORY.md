# codex-usage-hud 项目长期记忆

## 产品方向（见 AGENTS.md）

- Renderer 模式是唯一产品方向；Qt/Tk 独立 HUD 已废弃，只做移除/迁移/必要维护。
- 渲染性能向事件驱动演进：无事件就不应有周期性 CPU 工作。

## 不变量：子进程重启必须统一门控

`DesktopWorkOverlay` 的 helper 由 renderer tick 驱动，而 rest reminder 的 payload **每个 tick 都会重发一次**。
因此任何"helper 退出就重启"的路径都必须走统一门控，否则会变成进程风暴（实测约 150 个/分钟，
每个含 Python+PySide6 冷启动，直接把 Codex 的 Chromium renderer 饿成白屏）。

- 唯一启动入口：`DesktopWorkOverlay._maybe_start_helper(now)`，同时校验 `_restart_blocked_until` 与
  `_helper_breaker_until`。**不要再新增裸 `self._start()` 调用点。**
- 快速退出熔断：`_note_helper_exit()` 按生命周期 < 5s 记账，30s 内 3 次即熔断 120s。
  长寿命退出视为新生命周期并清空计数，所以"helper 卡死"的 35s 心跳恢复路径不受影响。
- 用户点「启用气泡」走 `reset_runtime_availability()`，会清空熔断，是人工逃生口。
- 反面教训：`evaluate_helper_health` 把 `exit_code==0`（干净退出）视为"立即可重启"（零退避），
  配合调用方清零退避就是风暴的直接成因。零退避语义本身可以留，但**调用方必须限速**。

## 不变量：renderer 缓存草稿必须按 payload 对账

settings shell 的 `settingsProviderDraft` 是「创建时抓取的快照」，**不会自己失效**。后台写入
（价格导入 / 手动保存 / 外部改配置）后，界面会一直显示旧值，直到重开设置界面。

- 唯一权威来源是 settings 域 payload。**不要只依赖 `settingsCommandStatus` 触发重绘**：
  status 会被 `RendererEventLoop._record_success()` 在快照刷新成功时清空，一旦早于域推送
  被清掉，提交就不会再触发任何重绘。
- 对账入口：`syncSettingsProviderDraftFromPayload()`（`applySettingsPayload` 每次 payload
  都会调用）。判定依据是 `settingsProviderPriceSourceSignature()`（单价表指纹），只重建指纹
  真的变了的供应商；`settingsDirtyProviders` 里有未保存编辑的供应商只在真实变更时清脏标记。
- 只有**当前**供应商的单价表变了才重绘编辑器，否则会打断其它供应商正在编辑的表单。
- 新增/修改单价表渲染相关逻辑时，同步更新 `providerDraftFromSettings` 的取值规则与
  `settingsProviderPriceSourceTable()`——两者必须取同一份表，否则指纹会误判。

## 本机环境：改了 renderer 资产必须重启 HUD 才生效

renderer bundle 由 Python 进程启动时装配进内存（`renderer_assets/manifest.py` → `RENDERER_HUD_SCRIPT_TEMPLATE`），
**运行中的 HUD 进程永远用它启动那一刻的 bundle**。因此改了 `renderer_assets/*` 后，
用户看到的仍是旧 JS——"改了但没效果"经常是进程没重启，而不是修复无效。

- 判断方法：`%LOCALAPPDATA%\codex-usage-hud\codex_usage_hud.pid` 与 `work-overlay-<pid>-*.json`
  的文件名时间戳 ≈ 进程启动时间；和源文件 mtime 比一比就知道 bundle 是否过期。
- 生效方式：设置界面里的「立即重启 HUD」，或直接重启 HUD 进程。

## 排查入口（本机运行时目录）

`%LOCALAPPDATA%\codex-usage-hud\`：

- `crash.log` — 每个 HUD 进程启动写一行 `--- codex-usage-hud crash diagnostics enabled pid=...`，
  按分钟统计行数即可看出进程风暴（含 overlay helper / loading helper 子进程）。
- `daemon.log` — daemon 生命周期、file_watcher、Codex 进程替换。
- `renderer_fallback.log` — CDP 相关阶段与 `runtime_error_*`；`initial_connect_failed`、
  `renderer_hung_escalation`、`restart-codex-for-renderer` 都在这里。
- `hud_settings.json` — 用户真实配置：`user.provider_settings[<provider>].model_prices` 是
  按供应商的当前单价，`user.pricing_sync.pending_prices`/`scope_provider` 是官方快照差异，
  `user.pricing_audit`/`pricing_versions` 能还原某次「确认更新」到底写了什么。
- `work-overlay-transitions.jsonl` — overlay 气泡状态迁移审计（含 ownerPid / stateFile）。

## 常用验证命令

```bash
# 聚焦测试（项目约定：不要跑全量）
"C:/Users/zjxqm/AppData/Local/Programs/Python/Python314/python.exe" -m pytest \
  tests/test_overlay_supervision.py tests/test_desktop_overlay.py \
  tests/test_daemon_runtime.py tests/test_renderer_event_loop.py -p no:cacheprovider

# 单价表 / 渲染器契约
"C:/Users/zjxqm/AppData/Local/Programs/Python/Python314/python.exe" -m pytest \
  tests/test_renderer_settings_pricing_draft.py tests/test_renderer_hud.py \
  tests/test_pricing_runtime_commands.py tests/test_renderer_contract_tool.py \
  tests/test_renderer_assets.py -p no:cacheprovider

# 静态检查
"C:/Users/zjxqm/AppData/Local/Programs/Python/Python314/python.exe" -m ruff check <files>
```

- 系统 Python 3.14（`AppData\Local\Programs\Python\Python314`）装了 PySide6 与 pytest，跑测试用这个；
  WorkBuddy 托管 Python 3.13 没有 PySide6。
- 回归测试要断言**行为**（如"进程拉起次数"），不要只断言新属性存在——否则在旧代码上只会
  报 AttributeError，证明不了回归被捕获。
- 渲染器 JS 的行为测试走 node + 最小 DOM 桩，样板见 `tests/test_renderer_settings_pricing_draft.py`：
  拼接 `_HOST_STUBS + SETTINGS_SHELL + 断言`。settings shell 资产近 400KB，
  **必须落临时文件再 `node <file>`**，用 `node -e` 会 `WinError 206`（命令行超长）。
  需要的宿主桩：`ctx`/`shared`、`escapeHtml`、`providerRegistryDisplayName`、`themeDomain`、
  `restReminderDomain`、`sessionViewDomain`、`refreshComposerBadgeState`、`settingsActiveTab`、
  `settingsProviderDraft`、`settingsDirtyProviders`、`readSettingsUiState`/`writeSettingsUiState`。
- `tests/test_ui.py` 有 2 个 overlay helper 重启相关用例在 48bcd99 之后就是红的
  （`test_desktop_rest_reminder_restarts_exited_helper_for_unchanged_payload`、
  `test_renderer_loop_skips_snapshot_when_runtime_signature_is_unchanged`），与单价改动无关，
  已在干净 HEAD 检出上复现。
