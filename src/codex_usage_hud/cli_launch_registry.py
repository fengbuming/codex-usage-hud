"""Stable terminal labels for CLI launches; never infer sessions from workdirs."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import uuid

from .runtime_paths import hud_runtime_dir

CLI_LAUNCH_ID_ENV = "CODEX_USAGE_HUD_CLI_LAUNCH_ID"


def register_cli_launch(title: str, terminal_kind: str, workdir: str) -> dict[str, str]:
    launch_id = str(uuid.uuid4())
    record = {"launchId": launch_id, "title": f"{title} · CLI#{launch_id[:8]}",
              "terminalKind": terminal_kind, "workdir": workdir}
    path = hud_runtime_dir() / "cli-launches" / f"{launch_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as stream:
        json.dump(record, stream, ensure_ascii=False)
        temporary = Path(stream.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return record


def cli_launch_for_id(launch_id: str) -> dict[str, str] | None:
    try:
        if str(uuid.UUID(launch_id)) != launch_id:
            return None
        path = hud_runtime_dir() / "cli-launches" / f"{launch_id}.json"
        if path.stat().st_size > 8192:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("launchId") == launch_id:
            return record
    except (ValueError, OSError, TypeError):
        pass
    return None


def remove_cli_launch(launch_id: str) -> None:
    if str(uuid.UUID(launch_id)) == launch_id:
        (hud_runtime_dir() / "cli-launches" / f"{launch_id}.json").unlink(missing_ok=True)
