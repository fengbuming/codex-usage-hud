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

## 排查入口（本机运行时目录）

`%LOCALAPPDATA%\codex-usage-hud\`：

- `crash.log` — 每个 HUD 进程启动写一行 `--- codex-usage-hud crash diagnostics enabled pid=...`，
  按分钟统计行数即可看出进程风暴（含 overlay helper / loading helper 子进程）。
- `daemon.log` — daemon 生命周期、file_watcher、Codex 进程替换。
- `renderer_fallback.log` — CDP 相关阶段与 `runtime_error_*`；`initial_connect_failed`、
  `renderer_hung_escalation`、`restart-codex-for-renderer` 都在这里。
- `hud_settings.json` — `runtime.rest_reminder`（enabled/interval/cycleStartedAtMs）等；
  时间戳与 `crash.log` 爆发窗口做交叉比对很有用。
- `work-overlay-transitions.jsonl` — overlay 气泡状态迁移审计（含 ownerPid / stateFile）。

## 常用验证命令

```bash
# 聚焦测试（项目约定：不要跑全量）
"C:/Users/zjxqm/AppData/Local/Programs/Python/Python314/python.exe" -m pytest \
  tests/test_overlay_supervision.py tests/test_desktop_overlay.py \
  tests/test_daemon_runtime.py tests/test_renderer_event_loop.py -p no:cacheprovider

# 静态检查
"C:/Users/zjxqm/AppData/Local/Programs/Python/Python314/python.exe" -m ruff check <files>
```

- 系统 Python 3.14（`AppData\Local\Programs\Python\Python314`）装了 PySide6 与 pytest，跑测试用这个；
  WorkBuddy 托管 Python 3.13 没有 PySide6。
- 回归测试要断言**行为**（如"进程拉起次数"），不要只断言新属性存在——否则在旧代码上只会
  报 AttributeError，证明不了回归被捕获。
