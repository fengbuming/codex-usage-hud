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

## 不变量：会话清单标题取 Codex 可见名，不取首条消息

Codex 的 `state_5.sqlite` `threads` 表里有两个不同的字段，很容易混：

- `title` = **首条用户消息**（可长达数百字符的原始 prompt）；
- `name` = Codex 会话列表里显示的**会话名**（LLM 生成的短标题），
  `~/.codex/session_index.jsonl` 的 `thread_name` 是同一个值的镜像。

清单行标题必须走 `_visible_session_title()`：`name` → `thread_name` → `title` → `Untitled session`。
**不要再用 `root.title` 当行标题** —— 那会让界面显示一堵首条消息的墙，且用户从 Codex 里
看到的会话名既搜不到也认不出（实测 983 个会话里 406 个 `name != title`）。

- 首条消息仍要可搜：`SessionCleanupItem._search_title` 保留 `threads.title`，只进
  `_metadata_matches` 的 haystack，不进 payload、不上屏。索引关闭时元数据匹配是唯一路径，
  少了它就会丢掉「按记得的 prompt 搜」这个能力。
- 会话身份查询（裸 id / `codex://threads/<id>`）走 `session_identity_query()` +
  `session_identity_matches()`，在 `_metadata_matches` 里**先于**分词匹配判定，
  并在 `_search_matches_locked` 对身份查询短路（否则深链里的 `codex`/`threads`
  会把大量无关内容命中混进来）。
- **UUID 不出 manager 边界**是既有隐私契约：身份比对只在服务端做，渲染器只拿到
  不透明 `session-<token>` 行 id。渲染器本地兜底 haystack 里没有、也不该有会话 id。
- 命中类型（`kinds`）是**用户可见文案的依据**，不是内部细节：`_metadata_match_kinds()`
  返回 `identity` / `metadata`，加上内容索引的 `user`/`assistant`/`tool`/`file`，
  一路带到 `thread_find_for_item` 的 `matches`，浮窗据此显示
  「正文匹配 / 会话 ID 命中 / 会话信息命中 / 索引命中」。新增 kind 时必须同时更新
  `session_view.py` 的 `SEARCH_KIND_LABELS` 与 `searchHitBadge()`、以及
  `session_cleanup.py` 的 `sessionCleanupMatchKindLabel()`——**不要让内部英文标识
  直接上屏**（曾出现「命中来源：metadata」）。

## 不变量：白屏（blank Codex UI）是合成表面重建，不是进程风暴、也不是 HUD 的 CDP

`renderer_client.quiesce()` 的 docstring 点名了一种失效模式：长锁屏期间**继续保留持久 CDP 会话 +
周期性 `Runtime.evaluate`** 会把 Codex renderer 主线程钉住 → 白屏。**但这条路已经被堵住了**，
2026-09-15 实测确认：

- tick loop 在锁屏期间整个停掉（唯一路径是 `renderer_event_loop.py:778` 的 `quiesce_active()` 分支）。
- `renderer_client.py` 四个 CDP 入口全部有 `_quiesced` 守卫：`update():432`、`update_payload():508`、
  `probe_connection():922`、`report_active_session():985`。新增 CDP 入口必须同样加守卫。
- **注入脚本里不能有周期性定时器**：`scheduleInterval` 在 `renderer_assets/` 里从未被调用，
  唯一的调度原语是一次性 `requestAnimationFrame`（`kernel.py:97`，hidden 时 Chromium 直接挂起它）。
  新增循环 UI 时不要引入 `setInterval`。
- overlay keepalive 线程（`desktop_overlay.py:590-652`）只在已发布气泡时存活，空闲不占线程。

→ **白屏的成因是 Chromium 丢弃了长时间不可见窗口的合成表面，重新可见时需要渲染进程重新出首帧。**
关键判据：主线程被阻塞只会让画面**停在最后一帧**（DWM 保留），**不会变白**；变白说明表面没了。
配套证据：hidden 期间 rAF 0 帧/2s、`setInterval` 被钳到 ~1000ms、白屏后 DOM/注入/截图全部完好、
Codex 渲染进程从未重建（9 个 `ChatGPT.exe` 全在 08:56:40–08:57:02 创建）。

- HUD 唯一确定的责任是 resume 延迟：解锁 → 首个 helper 间隔 **10 秒**（quiesce 等待 ≤5s +
  快照 1500ms + 全量重装脚本）。这是症状不是成因，且**成功路径零日志**。
- `crash.log` 的 helper 启动行可当 **HUD renderer tick 心跳**用：按「相邻行间隔 > 10s」分组，
  恒定 121.2s = tick 健康（120s 熔断 + 3 次自旋）；187~330s 甚至 844s/1307s = 循环被 quiesce 或
  页面节流拖慢。`RENDERER_IDLE_POLL_MS = 1500` 是正常节奏。排查白屏先看这条节律。
- **启动行空档 = 锁屏静默窗口的指纹**（重要）。`renderer_event_loop.py:778` 的
  `if self.ports.quiesce_active():` 分支不建 snapshot → 不调 `publish_active_work`
  → 不调 `DesktopWorkOverlay.update()` → `_maybe_start_helper()` 不跑 → 无启动行。
  实测午休那段：静默 3888.3s（64.8min），解锁（Kernel-Power 566）后 **10 秒**才出现首个 helper
  ——那 10 秒就是 resume 后的 CDP 重建 + 全量重装脚本窗口，**成功路径零日志**，
  所以只能靠这个空档反推。统计时**必须先按 `time=` 过滤日期**：crash.log 是多日累积文件。
- **可观测性缺口**：`renderer_hud_quiesced_for_session_lock` 等 INFO 只走 stderr，
  `attach_cli_logger_to_daemon_log` 只挂 cli/file_watcher，**锁屏时间线事后查不到**
  （实测 daemon.log 里 `quiesce|session_lock|session_unlock` 匹配 0 条）。
- Windows 侧：`Kernel-Power` id=566 能拿到 `SessionUnlock`/`InputHid` 会话转换；
  Security 4800/4801 默认没开审计，拿不到精确锁屏时刻。无 42/107 即**没进睡眠**。
- 本机取证坑：`GetProcessTimes` 返回 **UTC FILETIME**，不 +8 换算会把启动时间读成凌晨；
  `nohup ... &` 起的后台采样器会在本轮结束时被回收（不是常驻），**不要对用户宣称它在跑**；
  `wmic` 本机不可用，枚举进程用 `tasklist /FO CSV`。

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

`~/.codex/`（排查会话清单 / 搜索行为时直接取证）：

- `state_5.sqlite` — `threads(id, rollout_path, title, name, cwd, archived, updated_at_ms, ...)`；
  `title` 是首条用户消息、`name` 是可见会话名，两者常不同。**只读打开**（`mode=ro`）。
- `session_index.jsonl` — `{"id","thread_name","updated_at"}`，`thread_name` 与 `threads.name` 同源。
- `sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` — 会话正文；判断"关键字到底存不存在于会话内容里"
  就 grep 这里（决定索引开着时能不能搜到）。

```bash
# 会话清单 / 搜索行为的端到端取证：直接构造 manager 打真实数据（只读）
"C:/Users/zjxqm/AppData/Local/Programs/Python/Python314/python.exe" - <<'PY'
import sys; sys.path.insert(0, "src")
from pathlib import Path
from codex_usage_hud.core.session_cleanup import SessionCleanupManager
c = Path.home() / ".codex"
m = SessionCleanupManager(state_db_path=c/"state_5.sqlite", sessions_root=c/"sessions",
                          session_index_path=c/"session_index.jsonl")
rows = {r["id"]: r["title"] for r in m.scan()["sessions"]}
print(m.search("排查生产迁移")["search"]["matches"])
PY
```

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
