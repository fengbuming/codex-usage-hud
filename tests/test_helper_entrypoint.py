"""Helper processes must not load the renderer/coordinator to show bubbles."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from codex_usage_hud import helper_entrypoint


@pytest.mark.parametrize("kind", ["work-overlay", "loading-feedback"])
def test_helper_entry_does_not_import_renderer_or_start_hud(kind):
    root = Path(__file__).resolve().parents[1]
    script = r"""
import json, sys
from unittest.mock import patch
import codex_usage_hud.desktop_overlay_setup as overlay
import codex_usage_hud.loading_feedback as loading
from codex_usage_hud.helper_entrypoint import main
with patch.object(overlay, 'run_work_overlay_helper', return_value=17) as bubble, patch.object(loading, 'run_loading_feedback_helper', return_value=18) as startup:
    result = main(['--'+sys.argv[1]+'-helper', '--'+sys.argv[1]+'-state-file', 'chosen.json'])
    called = bubble.call_args if sys.argv[1] == 'work-overlay' else startup.call_args
    print(json.dumps({'result':result, 'path':called.args[0], 'renderer_imported':any(n.startswith('codex_usage_hud.renderer_assets') for n in sys.modules), 'root_imported':'codex_usage_hud.runtime_orchestration' in sys.modules}))
"""
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    completed = subprocess.run(
        [sys.executable, "-c", script, kind],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert json.loads(completed.stdout) == {
        "result": 17 if kind == "work-overlay" else 18,
        "path": "chosen.json",
        "renderer_imported": False,
        "root_imported": False,
    }


def test_normal_entry_preserves_public_cli_dispatch():
    from unittest.mock import MagicMock

    owner = MagicMock(return_value=7)
    with patch.dict(
        sys.modules,
        {"codex_usage_hud.runtime_orchestration": SimpleNamespace(main=owner)},
    ):
        assert helper_entrypoint.main(["--once"]) == 7
        owner.assert_called_once_with(["--once"])


def test_helper_unknown_options_are_still_rejected():
    with pytest.raises(SystemExit) as result:
        helper_entrypoint.main(["--work-overlay-helper", "--invalid"])
    assert result.value.code == 2


def test_shared_speed_art_does_not_assemble_renderer_manifest():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from codex_usage_hud.renderer_assets.speed_titles import VEHICLE_ART; assert VEHICLE_ART; assert 'codex_usage_hud.renderer_assets.manifest' not in sys.modules",
        ],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(root / "src")),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert completed.returncode == 0
