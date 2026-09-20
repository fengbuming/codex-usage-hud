# codex-usage-hud 项目长期记忆

## 产品方向（见 AGENTS.md）

- Renderer 模式是唯一产品方向；Qt/Tk 独立 HUD 已废弃，只做移除/迁移/必要维护。
- 渲染性能向事件驱动演进：无事件就不应有周期性 CPU 工作。

## 不变量：子进程重启必须统一门控

`DesktopWorkOverlay` 的 helper 由 renderer tick 驱动，rest reminder 的 payload **每 tick 重发一次**，
所以任何「helper 退出就重启」的路径都必须走统一门控，否则是进程风暴（实测约 150 个/分钟，
每个含 Python+PySide6 冷启动，把 Codex Chromium renderer 饿成白屏）。

- 唯一启动入口 `DesktopWorkOverlay._maybe_start_helper(now)`，同时校验 `_restart_blocked_until` 与
  `_helper_breaker_until`。**不要再新增裸 `self._start()` 调用点。**
- 快速退出熔断 `_note_helper_exit()`：生命周期 < 5s 记账，30s 内 3 次熔断 120s；长寿命退出视为新
  生命周期并清空计数，所以 35s 心跳恢复路径不受影响。用户点「启用气泡」走
  `reset_runtime_availability()` 清熔断，是人工逃生口。
- 反面教训：`evaluate_helper_health` 把 `exit_code==0` 当「立即可重启」（零退避），零退避语义可留，
  但**调用方必须限速**。

## 不变量：renderer 缓存草稿必须按 payload 对账

`settingsProviderDraft` 是「创建时抓取的快照」，**不会自己失效**；后台写入（价格导入/手动保存/外部
改配置）后界面会一直显示旧值，直到重开设置界面。

- 唯一权威来源是 settings 域 payload。**不要只依赖 `settingsCommandStatus` 触发重绘**：它会被
  `RendererEventLoop._record_success()` 在快照刷新成功时清空，一旦早于域推送被清掉，提交就不再重绘。
- 对账入口 `syncSettingsProviderDraftFromPayload()`（`applySettingsPayload` 每次 payload 都调用），
  依据 `settingsProviderPriceSourceSignature()`（单价表指纹）只重建指纹真变了的供应商。
- 只有**当前**供应商的单价表变了才重绘编辑器，否则会打断其它供应商正在编辑的表单。
- 改单价表渲染逻辑时同步更新 `providerDraftFromSettings` 与 `settingsProviderPriceSourceTable()`
  ——两者必须取同一份表，否则指纹误判。

## 不变量：迁移对话框的行可用性是「两条轴」的组合，不是单个 selectable

scan core 里 `selectable` 是**永久删除保护**（`selectable = not blocked_reason`），而 `status`
把两条轴塌缩成了一条、且 `current` 优先于 `active`：

- `status=current`（HUD 当前跟随/打开的会话）→ `blockedReason="The current session cannot be
  permanently deleted."`。**停止任务（`turn_aborted`）不会改变这一条**，它只影响 `running`
  （`This session tree still has active work.`）。所以「我已经停了这个会话」不足以解除 current。
- 于是 payload 额外发布 `active`（会话树是否仍有在飞工作），因为只靠 `status` 分不出
  「已停止的当前会话」和「正在跑的当前会话」。

对话框侧的判定（`sessionTransferRowEligible`，2026-09-18 修复后）：

- 复制模式：`status=current && active !== true` **允许**——复制不会删除源会话；
- 迁移模式：`current` 一律拦住（迁移要删源），`running` 两种模式都拦住（分叉写了一半的 transcript）。
- **不要把这条规则退回成 `item.selectable`**：那会让「已停止的当前会话」在复制模式下无谓禁用。

配套约束（都是同一个 bug 的组成部分，改动时不要漏）：

- 行内必须给**每一个** blocked 行渲染 `transferBlockedReason`，不只是 `archived` 行；文案由
  `sessionTransferBlockedHint()` 从 `status` 派生中文，**内部英文 `blockedReason` 不允许上屏**。
- 行可用性依赖 mode，所以 `bindSessionTransferModeControls` 的 apply 在 mode 真变化时必须
  `renderSessionTransferDialog()` 重绘列表。
- `openSessionTransferDialog()` 必须用 `sessionTransferSnapshotStale()`（`generatedAt` 超过
  `sessionTransferSnapshotStaleMs = 10000`）判龄并重扫。旧代码只在 `!data.revision` 时才扫，
  于是「切换会话后再打开对话框」永远复用旧快照，标志位一直是陈旧的 `current`。

回归测试：`tests/test_renderer_session_transfer_rows.py`（node + 最小 DOM 桩，装配
`session_cleanup` + `settings_shell` 两个资产）+ `tests/test_session_cleanup.py` 的
`test_current_and_running_session_trees_are_blocked` / `test_current_session_that_is_still_working_reports_active`。

## 不变量：会话清单标题取 Codex 可见名，不取首条消息

`state_5.sqlite` 的 `threads` 表有两个易混字段：`title` = **首条用户消息**（可能是数百字符原始
prompt）；`name` = Codex 列表里显示的**会话名**（`session_index.jsonl` 的 `thread_name` 同源）。

- 行标题必须走 `_visible_session_title()`：`name` → `thread_name` → `title` → `Untitled session`。
  **不要再用 `root.title` 当行标题**（实测 983 个会话里 406 个 `name != title`）。
- 首条消息仍要可搜：`SessionCleanupItem._search_title` 保留 `threads.title`，只进 `_metadata_matches`
  的 haystack，不进 payload、不上屏。索引关闭时元数据匹配是唯一路径。
- 会话身份查询（裸 id / `codex://threads/<id>`）走 `session_identity_query()` +
  `session_identity_matches()`，在 `_metadata_matches` 里**先于**分词匹配，并在 `_search_matches_locked`
  对身份查询短路（否则深链里的 `codex`/`threads` 会混进大量无关命中）。
- **UUID 不出 manager 边界**：身份比对只在服务端做，渲染器只拿到不透明 `session-<token>` 行 id。
- 命中类型（`kinds`）是**用户可见文案的依据**：新增 kind 必须同时更新 `session_view.py` 的
  `SEARCH_KIND_LABELS`/`searchHitBadge()` 与 `session_cleanup.py` 的 `sessionCleanupMatchKindLabel()`
  ——**不要让内部英文标识直接上屏**（曾出现「命中来源：metadata」）。

## 不变量：白屏（blank Codex UI）是合成表面重建，不是进程风暴、也不是 HUD 的 CDP

`renderer_client.quiesce()` 点名的那种失效模式（长锁屏期间保留持久 CDP 会话 + 周期性
`Runtime.evaluate` 钉住 renderer）**已经被堵住**，2026-09-15 实测确认：

- tick loop 在锁屏期间整个停掉（唯一路径 `renderer_event_loop.py:778` 的 `quiesce_active()` 分支）。
- `renderer_client.py` 四个 CDP 入口都有 `_quiesced` 守卫：`update():432`、`update_payload():508`、
  `probe_connection():922`、`report_active_session():985`。**新增 CDP 入口必须同样加守卫。**
- **注入脚本里不能有周期性定时器**：唯一调度原语是一次性 `requestAnimationFrame`
  （`kernel.py:97`，hidden 时 Chromium 直接挂起）。新增循环 UI 不要引入 `setInterval`。
- overlay keepalive 线程（`desktop_overlay.py:590-652`）只在已发布气泡时存活。

→ 成因是 Chromium 丢弃了长时间不可见窗口的合成表面。判据：主线程被阻塞只会让画面**停在最后一帧**
（DWM 保留），**不会变白**。配套证据：hidden 期间 rAF 0 帧/2s、`setInterval` 被钳到 ~1000ms、
白屏后 DOM/注入/截图完好、Codex 渲染进程从未重建。

- HUD 唯一确定的责任是 resume 延迟：解锁 → 首个 helper 间隔 **10 秒**（quiesce ≤5s + 快照 1500ms +
  全量重装脚本）。是症状不是成因，且**成功路径零日志**。
- `crash.log` 的 helper 启动行可当 **renderer tick 心跳**：按「相邻行间隔 > 10s」分组，恒定 121.2s =
  健康（120s 熔断 + 3 次自旋）；187~330s 甚至 844s/1307s = 被 quiesce 或页面节流拖慢。
  `RENDERER_IDLE_POLL_MS = 1500` 是正常节奏。排查白屏先看这条节律。
- **启动行空档 = 锁屏静默窗口的指纹**：`quiesce_active()` 分支不建 snapshot → 不调
  `publish_active_work` → 不跑 `_maybe_start_helper()` → 无启动行。实测午休静默 3888.3s，解锁后
  10 秒才出现首个 helper。统计时**必须先按 `time=` 过滤日期**（crash.log 是多日累积文件）。
- **可观测性缺口**：`renderer_hud_quiesced_for_session_lock` 等 INFO 只走 stderr，
  `attach_cli_logger_to_daemon_log` 只挂 cli/file_watcher，锁屏时间线事后查不到。
- Windows：`Kernel-Power` id=566 能拿到 `SessionUnlock`/`InputHid`；Security 4800/4801 默认没审计。
  无 42/107 即没进睡眠。
- 取证坑：`GetProcessTimes` 返回 **UTC FILETIME**（不 +8 会把启动时间读成凌晨）；`nohup ... &`
  起的采样器本轮结束即被回收，**不要对用户宣称它在跑**；`wmic` 本机不可用，枚举进程用 `tasklist /FO CSV`。

## 本机环境：改了 renderer 资产只有**重启进程**才生效（软重启无效）

renderer bundle 是**模块级常量**：`renderer_catalog.py:178` 的
`RENDERER_HUD_SCRIPT = renderer_hud_script_with_model_catalog([])` 在 import 时从
`manifest.RENDERER_HUD_SCRIPT_TEMPLATE` 装配。**运行中的 HUD 永远用它启动那一刻的 bundle。**

- **2026-09-18 实测修正**：设置界面「立即重启 HUD」（= 桥接 `POST /restart` = 命令
  `{action:"restart"}` → `ports.request_restart()` → `restart_event`）只是**重建 renderer 会话并
  重新注入内存里那份旧 bundle**，pid 不变、bundle 指纹不变、新增字段不出现。所以它**不能**用来
  验证 renderer 资产或 Python 代码的改动。必须真正重启 HUD 进程（退出后重新 `python -m
  codex_usage_hud`）。判断依据见下。
- 本机是 **editable 安装**：`site-packages/_editable_impl_codex_usage_hud.pth` 指向
  `E:\Project\codex-usage-hud\src`，所以新进程会加载工作区代码（不需要重装）。
- **判断 bundle 是否过期（最可靠）**：比较源码指纹与页面指纹——
  源码：`re.search(r'const bundleFingerprint = "([^"]+)"', RENDERER_HUD_SCRIPT).group(1)`；
  页面：CDP `Runtime.evaluate` 读 `document.getElementById('codex-usage-hud-root').dataset.bundleFingerprint`。
  两者不等即进程仍在用旧 bundle。
- **不要从我的 shell 里 `nohup ... &` 重启 HUD**：本轮结束时会被回收，用户会失去 HUD。
  重启这一步交给用户（或让他确认后再做）。

## 排查入口

`%LOCALAPPDATA%\codex-usage-hud\`：

- `crash.log` — 每个 HUD 进程启动一行 `--- ... crash diagnostics enabled pid=...`，按分钟统计行数看
  进程风暴（含 overlay helper / loading helper 子进程）。
- `daemon.log` — daemon 生命周期、file_watcher、Codex 进程替换。
- `renderer_fallback.log` — CDP 阶段与 `runtime_error_*`；`initial_connect_failed`、
  `renderer_hung_escalation`、`restart-codex-for-renderer` 都在这里。
- `renderer_cdp_state.json` — 当前 CDP 端口（`lastSuccessfulPort`），可用于直连取证。
- `hud_settings.json` — `user.provider_settings[<provider>].model_prices` 是按供应商单价；
  `user.pricing_sync.pending_prices`/`scope_provider` 是官方快照差异；`user.pricing_audit`/
  `pricing_versions` 能还原某次「确认更新」写了什么。
- `migrated_session_sources.json` — 已迁移源 + 待清理源的持久账本；**存在时该源会被整体隐藏**。
- `work-overlay-<pid>-*.json` — 气泡当前 items（含 status/modelProvider），判断「谁在活跃」最直接。

`~/.codex/`：

- `state_5.sqlite` — `threads(id, rollout_path, title, name, cwd, archived, updated_at_ms, ...)`。
  **只读打开**（`mode=ro`）。注意 `rollout_path` 常带 `\\?\` 前缀。
- `session_index.jsonl` — `{"id","thread_name","updated_at"}`。
- `sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` — 会话正文；判断关键字是否存在就 grep 这里。
  首行 `session_meta` 有 `model_provider`/`source`/`originator`/`forked_from_id`/`history_mode`。

### 取真实数据的两个入口

```bash
# 1) 直连 renderer CDP 读实时 payload（最权威，含 sessionCleanup 全量行）
"C:/Users/zjxqm/AppData/Local/Programs/Python/Python314/python.exe" - <<'PY'
import sys, json, urllib.request; sys.path.insert(0, "src")
from codex_usage_hud.renderer_cdp.connection import connect_websocket, send_command, receive_message
port = json.loads(open("C:/Users/zjxqm/AppData/Local/codex-usage-hud/renderer_cdp_state.json").read())["lastSuccessfulPort"]
t = [x for x in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3).read()) if x.get("type")=="page" and x.get("url","").startswith("app://-/index.html")][0]
sock = connect_websocket(t["webSocketDebuggerUrl"], 5.0)
send_command(sock, 1, "Runtime.evaluate", {"expression": "JSON.stringify(window.__codexUsageHudState.payload.sessionCleanup.sessions.filter(r=>r.modelProvider==='oceanhong'))", "returnByValue": True})
while True:
    m = json.loads(receive_message(sock))
    if m.get("id") == 1: print(m["result"]["result"]["value"]); break
sock.close()
PY

# 2) 只读重建 manager 打真实数据（可注入 current/active 复现状态）
"C:/Users/zjxqm/AppData/Local/Programs/Python/Python314/python.exe" - <<'PY'
import sys; sys.path.insert(0, "src")
from pathlib import Path
from codex_usage_hud.core.session_cleanup import SessionCleanupManager
c = Path.home() / ".codex"
m = SessionCleanupManager(state_db_path=c/"state_5.sqlite", sessions_root=c/"sessions",
                          session_index_path=c/"session_index.jsonl",
                          thread_history_db_path=c/"thread_history_1.sqlite",
                          current_session_ids=lambda: (), active_session_ids=lambda: ())
print(m.search("排查生产迁移")["search"]["matches"])
PY
```

## 常用验证命令

```bash
# 聚焦测试（项目约定：不要跑全量）
".../python.exe" -m pytest tests/test_overlay_supervision.py tests/test_desktop_overlay.py \
  tests/test_daemon_runtime.py tests/test_renderer_event_loop.py -p no:cacheprovider

# 单价表 / 渲染器契约 / 会话迁移
".../python.exe" -m pytest tests/test_renderer_settings_pricing_draft.py tests/test_renderer_hud.py \
  tests/test_pricing_runtime_commands.py tests/test_renderer_contract_tool.py \
  tests/test_renderer_assets.py tests/test_session_transfer.py tests/test_session_cleanup.py \
  -p no:cacheprovider

".../python.exe" -m ruff check <files>
```

- 系统 Python 3.14（`AppData\Local\Programs\Python\Python314`）装了 PySide6 与 pytest，跑测试用这个；
  WorkBuddy 托管 Python 3.13 没有 PySide6。
- 回归测试要断言**行为**（如「进程拉起次数」），不要只断言新属性存在。
- 渲染器 JS 行为测试走 node + 最小 DOM 桩，样板见 `tests/test_renderer_settings_pricing_draft.py`：
  拼接 `_HOST_STUBS + SETTINGS_SHELL + 断言`。settings shell 资产近 400KB，
  **必须落临时文件再 `node <file>`**（`node -e` 会 `WinError 206`）。宿主桩：`ctx`/`shared`、
  `escapeHtml`、`providerRegistryDisplayName`、`themeDomain`、`restReminderDomain`、
  `sessionViewDomain`、`refreshComposerBadgeState`、`settingsActiveTab`、`settingsProviderDraft`、
  `settingsDirtyProviders`、`readSettingsUiState`/`writeSettingsUiState`。
- `tests/test_ui.py` 有 2 个 overlay helper 重启用例自 48bcd99 起就是红的，与单价改动无关，
  已在干净 HEAD 检出上复现。
