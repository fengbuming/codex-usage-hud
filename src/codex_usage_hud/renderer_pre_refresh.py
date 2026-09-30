"""Command and settings work that precedes renderer snapshot refreshes."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import logging
from threading import Event, Lock, Thread, current_thread

from .renderer_event_loop import RendererLoopState, RendererTickInputs


_LOGGER = logging.getLogger("codex_usage_hud.renderer_event_loop")

@dataclass(frozen=True, slots=True)
class RendererPreRefreshPorts:
    """Command and settings work that precedes snapshot refresh execution."""

    current_config: Callable[[], object]
    execute_command: Callable[[dict[str, object]], dict[str, object]]
    update_status: Callable[[], dict[str, object]]
    reset_background_retry: Callable[[], None]
    renderer_only_status: Callable[[str], dict[str, object]]
    partial_domains_for_command: Callable[
        [dict[str, object], object, object],
        set[str] | None,
    ]
    refresh_latest_snapshot: Callable[
        [dict[str, object], object, object, object],
        None,
    ]
    refresh_usage_insights: Callable[[], None]
    overlay_configure: Callable[[], None]
    overlay_update: Callable[[list[object]], None]
    items_with_background_usage: Callable[[list[object]], list[object]]
    settings_store: object | None
    apply_config: Callable[[object, object], None]
    changed_config_keys: Callable[[object, object], set[str]]
    partial_domains_for_changes: Callable[[set[str]], set[str] | None]
    request_usage_insights_refresh: Callable[[], None] | None = None
    wake: Callable[[], None] | None = None


class RendererPreRefreshExecutor:
    """Apply command/background/settings-file work before snapshot execution."""

    _CODEX_CLI_LAUNCH_ACTION = "codexCliLaunch"
    _CODEX_CLI_LAUNCH_PENDING_ACTION = "codexCliLaunchPending"
    _CODEX_CLI_LAUNCH_CANCEL_ACTION = "codexCliLaunchCancel"
    _PROVIDER_CLONE_ACTION = "providerCloneSwitch"
    _PROVIDER_CLONE_PENDING_ACTION = "providerCloneSwitchPending"
    _BACKGROUND_QUERY_ACTIONS = frozenset(
        {
            "openBackgroundUsage",
            "openBackgroundUsageFromInsights",
            "backgroundUsageQuery",
            "backgroundUsageDetail",
        }
    )
    _REQUEST_ROWS_LOAD_MORE_ACTION = "loadMoreRequestRows"
    _REQUEST_ROWS_PAGE_SIZE = 30
    # 更新类命令由 AutoUpdateManager（payload.updateState）异步驱动最终状态。
    # execute_command 只返回中间态（如 checking/downloading），不能作为粘性
    # settings_command_status 保留，否则会一直覆盖 updateState 的最终状态，
    # 导致状态栏卡在"正在检查更新..."而 loading 弹窗已关闭。
    _ASYNC_UPDATE_ACTIONS = frozenset(
        {"checkUpdate", "installUpdate", "updateAction"}
    )

    def __init__(
        self,
        state: RendererLoopState,
        ports: RendererPreRefreshPorts,
    ) -> None:
        self.state = state
        self.ports = ports
        self._async_lock = Lock()
        self._closed = False
        self._codex_cli_launch_request_id = ""
        self._codex_cli_launch_thread: Thread | None = None
        self._codex_cli_launch_cancel_event: Event | None = None
        self._codex_cli_launch_phase = ""
        self._codex_cli_launch_results: deque[tuple[str, dict[str, object]]] = deque()
        self._provider_clone_request_id = ""
        self._provider_clone_results: deque[tuple[str, dict[str, object]]] = deque()

    def close(self) -> None:
        """Stop accepting launch results and bound the worker during shutdown."""
        with self._async_lock:
            self._closed = True
            thread = self._codex_cli_launch_thread
            cancel_event = self._codex_cli_launch_cancel_event
        if cancel_event is not None:
            cancel_event.set()
        if thread is not None and thread is not current_thread() and thread.is_alive():
            thread.join(timeout=2.0)
        with self._async_lock:
            self._codex_cli_launch_results.clear()
            self._codex_cli_launch_request_id = ""
            self._codex_cli_launch_thread = None
            self._codex_cli_launch_cancel_event = None
            self._codex_cli_launch_phase = ""
            self._provider_clone_results.clear()
            self._provider_clone_request_id = ""

    def apply(self, inputs: RendererTickInputs) -> None:
        self.apply_async_command_results(inputs)
        self.apply_usage_insights_refresh(inputs)
        self.apply_settings_command(inputs)
        self.apply_background_usage_change(inputs)
        self.apply_partial_settings_file_change(inputs)

    def apply_async_command_results(self, inputs: RendererTickInputs) -> None:
        with self._async_lock:
            results = list(self._codex_cli_launch_results)
            self._codex_cli_launch_results.clear()
            provider_clone_results = list(self._provider_clone_results)
            self._provider_clone_results.clear()
        for request_id, status in results:
            with self._async_lock:
                if request_id != self._codex_cli_launch_request_id:
                    continue
                self._codex_cli_launch_request_id = ""
                self._codex_cli_launch_thread = None
                self._codex_cli_launch_cancel_event = None
                self._codex_cli_launch_phase = ""
            self.state.settings_command_status = dict(status)
            inputs.update_state = self.ports.update_status()
            self._request_settings_domain(inputs)
        for request_id, status in provider_clone_results:
            with self._async_lock:
                if request_id != self._provider_clone_request_id:
                    continue
                self._provider_clone_request_id = ""
            self.state.settings_command_status = dict(status)
            inputs.update_state = self.ports.update_status()
            self._request_settings_domain(inputs)

    @staticmethod
    def _command_status(
        *,
        action: str,
        request_id: str,
        message: str,
        kind: str = "",
    ) -> dict[str, object]:
        return {
            "action": action,
            "requestId": request_id,
            "message": message,
            "kind": kind,
            "restartVisible": False,
        }

    def _request_settings_domain(self, inputs: RendererTickInputs) -> None:
        if self._can_replace_snapshot_with_domains(inputs, {"settings"}):
            inputs.event_refresh_request.snapshot = False
        inputs.event_refresh_request.request_domains("settings", force_fast=True)

    def _start_codex_cli_launch(
        self,
        command: dict[str, object],
        inputs: RendererTickInputs,
    ) -> None:
        request_id = str(
            command.get("requestId") or command.get("id") or "codex-cli-launch"
        ).strip()
        with self._async_lock:
            if self._closed:
                self.state.settings_command_status = self._command_status(
                    action=self._CODEX_CLI_LAUNCH_ACTION,
                    request_id=request_id,
                    message="Codex CLI 启动器已停止。",
                    kind="error",
                )
                self._request_settings_domain(inputs)
                return
            if self._codex_cli_launch_request_id:
                self.state.settings_command_status = self._command_status(
                    action=self._CODEX_CLI_LAUNCH_ACTION,
                    request_id=request_id,
                    message="上一次 Codex CLI 启动仍在进行，请稍候。",
                    kind="error",
                )
                self._request_settings_domain(inputs)
                return
            self._codex_cli_launch_request_id = request_id
            cancel_event = Event()
            self._codex_cli_launch_cancel_event = cancel_event
            self._codex_cli_launch_phase = "queued"
            thread = Thread(
                target=self._run_codex_cli_launch,
                args=(dict(command), request_id, cancel_event),
                name="codex-usage-hud-cli-launch",
                daemon=True,
            )
            self._codex_cli_launch_thread = thread
        self.state.settings_command_status = self._command_status(
            action=self._CODEX_CLI_LAUNCH_PENDING_ACTION,
            request_id=request_id,
            message="正在打开终端并启动 Codex CLI...",
        )
        self.state.settings_command_status["cancellable"] = True
        inputs.update_state = self.ports.update_status()
        self._request_settings_domain(inputs)
        _LOGGER.info("renderer_codex_cli_launch_started request_id=%s", request_id)
        try:
            thread.start()
        except Exception as exc:
            _LOGGER.exception("renderer_codex_cli_launch_thread_start_failed")
            with self._async_lock:
                self._codex_cli_launch_request_id = ""
                self._codex_cli_launch_thread = None
                self._codex_cli_launch_cancel_event = None
                self._codex_cli_launch_phase = ""
            self.state.settings_command_status = self._command_status(
                action=self._CODEX_CLI_LAUNCH_ACTION,
                request_id=request_id,
                message=f"Codex CLI 启动失败：{exc}",
                kind="error",
            )
            self._request_settings_domain(inputs)

    def _start_provider_clone(
        self,
        command: dict[str, object],
        inputs: RendererTickInputs,
    ) -> None:
        request_id = str(
            command.get("requestId") or command.get("id") or "provider-clone"
        ).strip()
        with self._async_lock:
            if self._closed:
                self.state.settings_command_status = self._command_status(
                    action=self._PROVIDER_CLONE_ACTION,
                    request_id=request_id,
                    message="供应商克隆工作流已停止。",
                    kind="error",
                )
                self._request_settings_domain(inputs)
                return
            if self._provider_clone_request_id:
                self.state.settings_command_status = self._command_status(
                    action=self._PROVIDER_CLONE_ACTION,
                    request_id=request_id,
                    message="上一次供应商克隆仍在进行，请稍候。",
                    kind="error",
                )
                self._request_settings_domain(inputs)
                return
            self._provider_clone_request_id = request_id
        self.state.settings_command_status = self._command_status(
            action=self._PROVIDER_CLONE_PENDING_ACTION,
            request_id=request_id,
            message="正在写入供应商配置、复制模型单价并切换默认供应商...",
        )
        inputs.update_state = self.ports.update_status()
        self._request_settings_domain(inputs)
        _LOGGER.info("renderer_provider_clone_started request_id=%s", request_id)
        try:
            status = dict(self.ports.execute_command(command))
        except Exception as exc:
            _LOGGER.exception(
                "renderer_provider_clone_failed request_id=%s",
                request_id,
            )
            status = self._command_status(
                action=self._PROVIDER_CLONE_ACTION,
                request_id=request_id,
                message=f"供应商克隆失败：{exc}",
                kind="error",
            )
        status.setdefault("action", self._PROVIDER_CLONE_ACTION)
        status.setdefault("requestId", request_id)
        with self._async_lock:
            if self._closed or request_id != self._provider_clone_request_id:
                return
            self._provider_clone_results.append((request_id, status))
        wake = self.ports.wake
        if callable(wake):
            wake()
        _LOGGER.info(
            "renderer_provider_clone_finished request_id=%s kind=%s",
            request_id,
            str(status.get("kind") or "ok"),
        )

    def _run_codex_cli_launch(
        self,
        command: dict[str, object],
        request_id: str,
        cancel_event: Event,
    ) -> None:
        try:
            with self._async_lock:
                if (
                    self._closed
                    or request_id != self._codex_cli_launch_request_id
                    or cancel_event.is_set()
                ):
                    status = self._cancelled_codex_cli_launch_status(request_id)
                else:
                    self._codex_cli_launch_phase = "validating"
                    status = None
            if status is None:
                launch_command = dict(command)
                launch_command["_codexCliCancelRequested"] = cancel_event.is_set
                launch_command["_codexCliCommitSpawn"] = lambda: self._commit_codex_cli_spawn(
                    request_id,
                    cancel_event,
                )
                status = dict(self.ports.execute_command(launch_command))
        except Exception as exc:
            _LOGGER.exception(
                "renderer_codex_cli_launch_failed request_id=%s",
                request_id,
            )
            status = self._command_status(
                action=self._CODEX_CLI_LAUNCH_ACTION,
                request_id=request_id,
                message=f"Codex CLI 启动失败：{exc}",
                kind="error",
            )
        status.setdefault("action", self._CODEX_CLI_LAUNCH_ACTION)
        status.setdefault("requestId", request_id)
        with self._async_lock:
            if self._closed:
                return
            self._codex_cli_launch_results.append((request_id, status))
        wake = self.ports.wake
        if callable(wake):
            wake()
        _LOGGER.info(
            "renderer_codex_cli_launch_finished request_id=%s kind=%s",
            request_id,
            str(status.get("kind") or "ok"),
        )

    def _commit_codex_cli_spawn(
        self,
        request_id: str,
        cancel_event: Event,
    ) -> bool:
        """Atomically cross the last cancellable boundary before ``Popen``."""
        with self._async_lock:
            if (
                self._closed
                or request_id != self._codex_cli_launch_request_id
                or cancel_event.is_set()
                or self._codex_cli_launch_phase == "spawn_committed"
            ):
                return False
            self._codex_cli_launch_phase = "spawn_committed"
            return True

    def _cancelled_codex_cli_launch_status(self, request_id: str) -> dict[str, object]:
        status = self._command_status(
            action=self._CODEX_CLI_LAUNCH_ACTION,
            request_id=request_id,
            message="已停止 Codex CLI 启动，未创建终端。",
        )
        status["codexCliLaunchCancelled"] = True
        return status

    def _cancel_codex_cli_launch(
        self,
        command: dict[str, object],
        inputs: RendererTickInputs,
    ) -> None:
        cancel_request_id = str(
            command.get("requestId") or command.get("id") or "codex-cli-launch-cancel"
        ).strip()
        launch_request_id = str(command.get("launchRequestId") or "").strip()
        with self._async_lock:
            active_request_id = self._codex_cli_launch_request_id
            cancel_event = self._codex_cli_launch_cancel_event
            spawn_committed = self._codex_cli_launch_phase == "spawn_committed"
            request_matches = bool(
                launch_request_id
                and active_request_id
                and launch_request_id == active_request_id
            )
            accepted = bool(request_matches and cancel_event is not None and not spawn_committed)
            if accepted:
                cancel_event.set()

        if accepted:
            message = "正在停止 Codex CLI 启动；终端创建前会安全取消。"
            kind = ""
        elif request_matches and spawn_committed:
            message = "终端创建已经开始，无法再停止；可以关闭 Loading 等待结果。"
            kind = "warning"
        else:
            message = "该 Codex CLI 启动请求已结束或不是当前请求。"
            kind = "warning"
        status = self._command_status(
            action=self._CODEX_CLI_LAUNCH_CANCEL_ACTION,
            request_id=cancel_request_id,
            message=message,
            kind=kind,
        )
        status.update(
            {
                "launchRequestId": launch_request_id,
                "cancelAccepted": accepted,
                "spawnCommitted": bool(request_matches and spawn_committed),
            }
        )
        self.state.settings_command_status = status
        inputs.update_state = self.ports.update_status()
        self._request_settings_domain(inputs)

    def apply_usage_insights_refresh(self, inputs: RendererTickInputs) -> None:
        if not inputs.event_refresh_request.usage_insights_refresh:
            return
        request = self.ports.request_usage_insights_refresh
        if callable(request):
            request()

    def apply_settings_command(self, inputs: RendererTickInputs) -> None:
        if not inputs.command:
            return
        action = str(inputs.command.get("action") or "").strip()
        if action == self._REQUEST_ROWS_LOAD_MORE_ACTION:
            self.state.request_rows_limit += self._REQUEST_ROWS_PAGE_SIZE
            self.state.settings_command_status = {}
            return
        if action == self._CODEX_CLI_LAUNCH_ACTION:
            self._start_codex_cli_launch(dict(inputs.command), inputs)
            return
        if action == self._PROVIDER_CLONE_ACTION:
            self._start_provider_clone(dict(inputs.command), inputs)
            return
        if action == self._CODEX_CLI_LAUNCH_CANCEL_ACTION:
            self._cancel_codex_cli_launch(dict(inputs.command), inputs)
            return
        previous_config = self.ports.current_config()
        if action in self._BACKGROUND_QUERY_ACTIONS:
            self.ports.reset_background_retry()
        self.state.settings_command_status = self.ports.execute_command(inputs.command)
        inputs.update_state = self.ports.update_status()
        mode_switch = str(
            self.state.settings_command_status.get("switchMode") or ""
        ).strip()
        if mode_switch and mode_switch != "renderer":
            _LOGGER.info("renderer_hud_legacy_switch_ignored mode=%s", mode_switch)
            self.state.settings_command_status = self.ports.renderer_only_status(
                "Renderer-only 版本不再切换到 Qt/Tk。"
            )
        current_config = self.ports.current_config()
        partial_domains = self.ports.partial_domains_for_command(
            inputs.command,
            previous_config,
            current_config,
        )
        if action == "providerSetDefault":
            # Deliver the acknowledgement even when concurrent session/file
            # work still requires a full snapshot. Never leave the menu busy
            # solely because that work prevented partial-snapshot replacement.
            self._request_settings_domain(inputs)
            return
        if not self._can_replace_snapshot_with_domains(inputs, partial_domains):
            if action in self._ASYNC_UPDATE_ACTIONS and self.state.latest_snapshot is None:
                # There is no settings-domain refresh to carry this transient
                # command response, so do not leave it in loop state.
                self.state.settings_command_status = {}
            return
        if self.state.latest_snapshot is not None:
            self.ports.refresh_latest_snapshot(
                inputs.command,
                self.state.latest_snapshot,
                previous_config,
                current_config,
            )
        self._refresh_overlay_for_partial_domains(partial_domains)
        inputs.event_refresh_request.snapshot = False
        inputs.event_refresh_request.request_domains(
            *sorted(partial_domains or set()),
            force_fast=True,
        )

    def apply_background_usage_change(self, inputs: RendererTickInputs) -> None:
        if not inputs.event_refresh_request.background_usage:
            return
        if any(
            str(getattr(event, "type", "") or "")
            == "background_usage_changed"
            for event in inputs.runtime_events
        ):
            self.ports.refresh_usage_insights()
        session_items = (
            list(self.state.latest_snapshot.active_work_items)
            if self.state.latest_snapshot is not None
            else []
        )
        self.ports.overlay_configure()
        self.ports.overlay_update(
            self.ports.items_with_background_usage(session_items)
        )
        if not self.state.activity_wake_pending:
            self.state.activity_wake_pending = "background-usage"

    def apply_partial_settings_file_change(
        self,
        inputs: RendererTickInputs,
    ) -> None:
        event_types = {
            str(getattr(event, "type", "") or "")
            for event in inputs.runtime_events
        }
        if (
            self.state.latest_snapshot is None
            or inputs.command
            or not inputs.event_refresh_request.snapshot
            or inputs.active_session_wakeup
            or inputs.event_refresh_request.active_session
            or inputs.event_refresh_request.diagnostics
            or (
                inputs.file_change_reasons
                and inputs.file_change_reasons != {"settings"}
            )
            or event_types - {"settings_changed"}
        ):
            return
        load = getattr(self.ports.settings_store, "load", None)
        mtime_fn = getattr(self.ports.settings_store, "mtime", None)
        if not callable(load):
            return
        previous_config = self.ports.current_config()
        next_config = load()
        mtime = mtime_fn() if callable(mtime_fn) else None
        self.ports.apply_config(next_config, mtime)
        changed_keys = self.ports.changed_config_keys(
            previous_config,
            next_config,
        )
        partial_domains = self.ports.partial_domains_for_changes(changed_keys)
        if partial_domains is None:
            return
        self.ports.refresh_latest_snapshot(
            {"action": "save"},
            self.state.latest_snapshot,
            previous_config,
            next_config,
        )
        self._refresh_overlay_for_partial_domains(partial_domains)
        inputs.event_refresh_request.snapshot = False
        inputs.event_refresh_request.request_domains(
            *sorted(partial_domains),
            force_fast=True,
        )

    def _refresh_overlay_for_partial_domains(
        self,
        partial_domains: set[str] | None,
    ) -> None:
        if not partial_domains or "overlay" not in partial_domains:
            return
        session_items = (
            list(self.state.latest_snapshot.active_work_items)
            if self.state.latest_snapshot is not None
            else []
        )
        self.ports.overlay_configure()
        self.ports.overlay_update(
            self.ports.items_with_background_usage(session_items)
        )

    @staticmethod
    def _can_replace_snapshot_with_domains(
        inputs: RendererTickInputs,
        partial_domains: set[str] | None,
    ) -> bool:
        return bool(
            partial_domains
            and inputs.event_refresh_request.snapshot
            and not inputs.file_change_reasons
            and not inputs.active_session_wakeup
            and not inputs.event_refresh_request.active_session
            and not inputs.event_refresh_request.diagnostics
        )

__all__ = ["RendererPreRefreshExecutor", "RendererPreRefreshPorts"]
