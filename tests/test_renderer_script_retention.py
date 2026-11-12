"""No duplicate default script is materialized by production client imports."""

import json
import os
from pathlib import Path
import subprocess
import sys


def test_renderer_import_keeps_one_template_and_lazy_compatible_default():
    root = Path(__file__).resolve().parents[1]
    script = r"""
import json, sys
from codex_usage_hud import renderer_client, renderer_catalog, renderer_payload_builder
from codex_usage_hud.ui import renderer_domains
from codex_usage_hud.renderer_assets import settings_shell
modules=(renderer_client,renderer_catalog,renderer_payload_builder,renderer_domains)
assert all('RENDERER_HUD_SCRIPT' not in vars(module) for module in modules)
expected=renderer_catalog.renderer_hud_script_with_model_catalog([])
assert all(module.RENDERER_HUD_SCRIPT==expected for module in modules)
assert all('RENDERER_HUD_SCRIPT' not in vars(module) for module in modules)
assert '_TEXT_PREFIX' not in vars(settings_shell) and '_TEXT_SUFFIX' not in vars(settings_shell)
print(json.dumps({'ok':True}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(root / "src")),
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert json.loads(result.stdout) == {"ok": True}
