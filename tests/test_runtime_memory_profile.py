from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def test_memory_profile_is_opt_in_and_captures_only_once(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "profile" / "memory.json"
    script = r"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / 'src'))
from codex_usage_hud.runtime_memory_profile import (
    capture_profile_once,
    record_stage,
    start_if_enabled,
)

assert not start_if_enabled({})
assert not capture_profile_once(output_path=Path(sys.argv[1]))
assert start_if_enabled(
    {'CODEX_USAGE_HUD_MEMORY_PROFILE': '1'},
    stage='renderer-client-init',
)
retained = [bytearray(4096) for _ in range(128)]
assert record_stage('runtime-context-ready')
assert capture_profile_once(reason='test', output_path=Path(sys.argv[1]))
assert not capture_profile_once(reason='second', output_path=Path(sys.argv[1]))
assert len(retained) == 128
"""
    subprocess.run(
        [sys.executable, "-B", "-c", script, str(output)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["reason"] == "test"
    assert report["profiling"]["trace_started_stage"] == "renderer-client-init"
    assert [item["stage"] for item in report["stage_samples"]] == [
        "renderer-client-init",
        "runtime-context-ready",
        "test",
    ]
    assert report["stage_samples"][1]["interval_peak_growth_bytes"] >= 128 * 4096
    assert report["profiling"]["peak_traced_bytes"] >= 128 * 4096
    assert report["profiling"]["current_traced_bytes"] > 128 * 4096
    assert report["profiling"]["peak_traced_bytes"] >= report["profiling"]["current_traced_bytes"]
    assert report["top_allocation_files"]
    assert report["process"]["source"] in {"GetProcessMemoryInfo", "unavailable"}
    assert "session_id" not in report
