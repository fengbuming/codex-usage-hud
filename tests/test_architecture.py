import ast
import hashlib
from importlib import resources
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from codex_usage_hud.ui.renderer_domains import RENDERER_HUD_SCRIPT


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/codex_usage_hud"
CONTRACTS = ROOT / "tests/contracts"


def _contract(name: str) -> dict[str, object]:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _from_imports(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def test_domains_do_not_import_compatibility_facades() -> None:
    forbidden = {"cli", "runtime_orchestration", "ui.renderer_hud"}
    exempt = {SRC / "cli.py", SRC / "__main__.py", SRC / "helper_entrypoint.py", SRC / "ui/renderer_hud.py"}
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        if path in exempt:
            continue
        for imported in _imports(path):
            normalized = imported.removeprefix("codex_usage_hud.")
            if normalized in forbidden or normalized.endswith(".ui.renderer_hud"):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert violations == []


def test_usage_contribution_owner_is_provider_neutral_and_facade_free() -> None:
    path = SRC / "usage_contributions.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports(path)

    assert "included_providers" not in source
    assert not {
        "codex_usage_hud.cli",
        "codex_usage_hud.runtime_orchestration",
        "codex_usage_hud.usage_cache",
    } & imports


def test_usage_domain_owners_do_not_import_coordinator_or_facades() -> None:
    forbidden_suffixes = {"cli", "runtime_orchestration", "ui.renderer_hud"}
    violations: list[str] = []
    for name in (
        "usage_contributions.py",
        "usage_cache.py",
        "usage_insights.py",
        "session_cleanup_runtime.py",
    ):
        path = SRC / name
        for imported in _imports(path):
            normalized = imported.removeprefix("codex_usage_hud.")
            if normalized in forbidden_suffixes:
                violations.append(f"{name} -> {imported}")
    assert violations == []


def test_overlay_domain_owners_do_not_import_coordinator_or_facades() -> None:
    forbidden_suffixes = {"cli", "runtime_orchestration", "ui.renderer_hud"}
    violations: list[str] = []
    for name in (
        "overlay_ipc.py",
        "overlay_projection.py",
        "desktop_overlay.py",
        "loading_feedback.py",
        "overlay_command_pump.py",
        "overlay_command_channel.py",
        "overlay_commands.py",
        "overlay_supervision.py",
        "overlay_transition_audit.py",
        "overlay_state.py",
        "overlay_window.py",
    ):
        path = SRC / name
        for imported in _imports(path):
            normalized = imported.removeprefix("codex_usage_hud.")
            if normalized in forbidden_suffixes:
                violations.append(f"{name} -> {imported}")
    assert violations == []


def test_overlay_supervision_is_a_pure_policy_owner() -> None:
    supervision = SRC / "overlay_supervision.py"
    imports = _imports(supervision)
    assert "subprocess" not in imports
    assert "threading" not in imports
    assert "codex_usage_hud.platforms.file_watcher" not in imports
    source = supervision.read_text(encoding="utf-8")
    assert "def evaluate_helper_health(" in source
    assert "def route_system_action_commands(" in source
    assert "overlay_supervision" in (SRC / "desktop_overlay.py").read_text(
        encoding="utf-8"
    )


def test_overlay_command_channel_is_protocol_only() -> None:
    channel = SRC / "overlay_command_channel.py"
    imports = _imports(channel)
    assert "subprocess" not in imports
    assert "threading" not in imports
    assert "codex_usage_hud.platforms.file_watcher" not in imports
    assert "codex_usage_hud.runtime_orchestration" not in imports
    assert "codex_usage_hud.cli" not in imports
    source = channel.read_text(encoding="utf-8")
    assert "PySide6" not in source
    assert "FileChangeWatcher" not in source


def test_snapshot_settings_owners_do_not_import_coordinator_or_facades() -> None:
    forbidden_suffixes = {"cli", "runtime_orchestration", "ui.renderer_hud"}
    violations: list[str] = []
    for name in (
        "runtime_config.py",
        "runtime_commands.py",
        "runtime_context.py",
        "runtime_settings.py",
        "active_work.py",
        "renderer_file_events.py",
        "renderer_bridge.py",
        "renderer_connection.py",
        "renderer_event_loop.py",
        "renderer_runtime.py",
        "session_snapshots.py",
        "snapshot_builder.py",
    ):
        path = SRC / name
        for imported in _imports(path):
            normalized = imported.removeprefix("codex_usage_hud.")
            if normalized in forbidden_suffixes:
                violations.append(f"{name} -> {imported}")
    assert violations == []


def test_renderer_presenter_owners_do_not_import_coordinator_or_facades() -> None:
    forbidden_suffixes = {"cli", "runtime_orchestration", "ui.renderer_hud"}
    violations: list[str] = []
    for path in (SRC / "renderer_presenters").glob("*.py"):
        for imported in _imports(path):
            normalized = imported.removeprefix("codex_usage_hud.")
            if normalized in forbidden_suffixes or normalized.endswith(".ui.renderer_hud"):
                violations.append(f"{path.name} -> {imported}")
    assert violations == []


def test_renderer_request_projection_is_pure_and_wired_through_builder() -> None:
    owner = SRC / "renderer_request_projection.py"
    imports = _imports(owner)
    forbidden = {
        "codex_usage_hud.renderer_payload_builder",
        "codex_usage_hud.renderer_runtime",
        "codex_usage_hud.renderer_client",
        "codex_usage_hud.desktop_overlay",
        "codex_usage_hud.runtime_orchestration",
    }
    assert not forbidden & imports
    source = owner.read_text(encoding="utf-8")
    assert "class RequestProjectionContext" in source
    assert "def request_rows(" in source
    assert "def request_row_details(" in source
    builder_source = (SRC / "renderer_payload_builder.py").read_text(encoding="utf-8")
    assert "renderer_request_projection" in builder_source
    assert "def _request_projection_context(" in builder_source


def test_runtime_coordinator_no_longer_defines_snapshot_settings_owners() -> None:
    tree = ast.parse(
        (SRC / "runtime_orchestration.py").read_text(encoding="utf-8")
    )
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "SessionSnapshotCache" not in classes
    assert "RuntimeContext" not in classes
    assert "SnapshotBuilder" not in classes
    assert not {
        "apply_pre_send_and_activity",
        "apply_pre_send_pricing",
        "clone_cached_snapshot",
    } & functions


def test_runtime_coordinator_no_longer_defines_usage_owner_classes() -> None:
    tree = ast.parse(
        (SRC / "runtime_orchestration.py").read_text(encoding="utf-8")
    )
    top_level_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert not {
        "UsageSummaryCache",
        "_UsageInsightAggregate",
        "_UsageCacheEntry",
        "_UsageInsightsWorker",
        "_SessionCleanupWorker",
    } & top_level_classes


def test_runtime_coordinator_no_longer_defines_desktop_overlay_owner() -> None:
    tree = ast.parse(
        (SRC / "runtime_orchestration.py").read_text(encoding="utf-8")
    )
    top_level_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert "DesktopWorkOverlay" not in top_level_classes


def test_runtime_coordinator_no_longer_defines_active_work_pump() -> None:
    tree = ast.parse(
        (SRC / "runtime_orchestration.py").read_text(encoding="utf-8")
    )
    top_level_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert "_RendererActiveWorkPump" not in top_level_classes


def test_runtime_coordinator_no_longer_defines_event_loop_contracts() -> None:
    tree = ast.parse(
        (SRC / "runtime_orchestration.py").read_text(encoding="utf-8")
    )
    classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert not {
        "_RendererEventRefreshRequest",
        "_RendererTickInputs",
        "_RendererLoopState",
    } & classes


def test_legacy_facade_patch_inventory_can_only_decrease() -> None:
    result = subprocess.run(
        [sys.executable, "tools/check_facade_patch_inventory.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_renderer_session_control_plane_tests_use_injected_services() -> None:
    path = ROOT / "tests/test_ui.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    construction_targets = {
        "AutoUpdateManager",
        "DesktopWorkOverlay",
        "RendererHudClient",
        "SettingsBridgeServer",
        "_RendererActiveWorkPump",
        "_RendererFileEventSource",
        "WorkOverlayCommandPump",
        "build_runtime_context",
        "build_snapshot",
    }
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls_renderer_session = any(
            isinstance(call, ast.Call)
            and (
                isinstance(call.func, ast.Name)
                and call.func.id == "run_renderer_hud_session"
            )
            for call in ast.walk(node)
        )
        if not calls_renderer_session:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or not call.args:
                continue
            is_patch = (
                isinstance(call.func, ast.Name) and call.func.id == "patch"
            ) or (
                isinstance(call.func, ast.Attribute) and call.func.attr == "patch"
            )
            target = call.args[0]
            if (
                is_patch
                and isinstance(target, ast.Constant)
                and isinstance(target.value, str)
                and target.value.startswith("codex_usage_hud.cli.")
                and target.value.rsplit(".", 1)[-1] in construction_targets
            ):
                violations.append(f"{node.name}:{call.lineno}:{target.value}")
    assert violations == []


def test_public_cli_import_inventory_resolves() -> None:
    import codex_usage_hud.cli as cli
    import codex_usage_hud.runtime_orchestration as owner

    inventory = _contract("public_imports.json")
    assert all(hasattr(cli, name) for name in inventory["supportedCliImports"])
    assert cli is not owner
    assert cli.__all__ == inventory["supportedCliImports"]
    assert all(
        getattr(cli, name) is getattr(owner, name)
        for name in inventory["supportedCliImports"]
    )
    assert inventory["privateMonkeypatchPropagationIsPublic"] is False


def test_cli_facade_is_explicit_and_small() -> None:
    source = (SRC / "cli.py").read_text(encoding="utf-8")

    assert len(source.splitlines()) <= 80
    assert "sys.modules[__name__] =" not in source
    assert "sys.modules[__name__].__class__" not in source
    assert "_CompatibilityModule" not in source
    assert "_COMPATIBILITY_EXPORTS" not in source
    assert "def __getattr__" not in source
    assert "__all__" in source


def test_ui_uses_runtime_owners_instead_of_cli_facade() -> None:
    imports = _imports(ROOT / "tests/test_ui.py")

    assert "codex_usage_hud.cli" not in imports


def test_cli_facade_import_does_not_eagerly_load_qt_hud() -> None:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(ROOT / "src")
        if not existing_pythonpath
        else str(ROOT / "src") + os.pathsep + existing_pythonpath
    )
    script = (
        "import sys\n"
        "import codex_usage_hud.cli\n"
        "names = [\n"
        "    'PySide6',\n"
        "    'PySide6.QtCore',\n"
        "    'codex_usage_hud.ui.qt_hud',\n"
        "    'codex_usage_hud.ui.tk_hud',\n"
        "    'codex_usage_hud.ui.work_overlay_qt',\n"
        "]\n"
        "print('\\n'.join(f'{name}={name in sys.modules}' for name in names))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "PySide6=False" in result.stdout
    assert "PySide6.QtCore=False" in result.stdout
    assert "codex_usage_hud.ui.qt_hud=False" in result.stdout
    assert "codex_usage_hud.ui.tk_hud=False" in result.stdout
    assert "codex_usage_hud.ui.work_overlay_qt=False" in result.stdout


def test_work_overlay_facade_is_small_and_lazy() -> None:
    facade_path = SRC / "ui/work_overlay_qt.py"
    source = facade_path.read_text(encoding="utf-8")

    assert len(source.splitlines()) <= 120
    assert not {name for name in _imports(facade_path) if name.startswith("PySide6")}
    assert "class OverlayWindow" not in source
    assert "QFileSystemWatcher" not in source
    assert "from .work_overlay.qt_runtime import" in source

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(ROOT / "src")
        if not env.get("PYTHONPATH")
        else str(ROOT / "src") + os.pathsep + env["PYTHONPATH"]
    )
    script = (
        "import sys\n"
        "import codex_usage_hud.ui.work_overlay_qt\n"
        "print('PySide6=' + str('PySide6' in sys.modules))\n"
        "print('qt_runtime=' + str('codex_usage_hud.ui.work_overlay.qt_runtime' in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "PySide6=False" in result.stdout
    assert "qt_runtime=False" in result.stdout


def test_work_overlay_owner_dependency_direction_is_explicit() -> None:
    pure_owner_paths = [
        SRC / "ui/work_overlay/constants.py",
        SRC / "ui/work_overlay/model.py",
        SRC / "ui/work_overlay/geometry.py",
        SRC / "ui/work_overlay/theme.py",
    ]
    for path in pure_owner_paths:
        assert not {name for name in _imports(path) if name.startswith("PySide6")}, path

    runtime_source = (SRC / "ui/work_overlay/qt_runtime.py").read_text(encoding="utf-8")
    assert "QFileSystemWatcher" in runtime_source
    assert "from .work_overlay" not in runtime_source
    assert "from .qt_window import OverlayWindow" in runtime_source

    facade_source = (SRC / "ui/work_overlay_qt.py").read_text(encoding="utf-8")
    assert "from .work_overlay.constants import *" in facade_source
    assert "from .work_overlay.geometry import *" in facade_source
    assert "from .work_overlay.model import *" in facade_source
    assert "from .work_overlay.theme import *" in facade_source


def test_usage_cache_public_facade_resolves_to_owner() -> None:
    import codex_usage_hud.cli as cli
    from codex_usage_hud.usage_cache import UsageSummaryCache

    assert cli.UsageSummaryCache is UsageSummaryCache


def test_renderer_facade_inventory_resolves_to_current_owner() -> None:
    import codex_usage_hud.ui.renderer_domains as owner
    import codex_usage_hud.ui.renderer_hud as facade

    inventory = _contract("public_imports.json")
    assert facade is not owner
    assert facade.__all__ == sorted(inventory["rendererFacadeImports"])
    assert all(
        getattr(facade, name) is getattr(owner, name)
        for name in inventory["rendererFacadeImports"]
    )


def test_public_import_inventory_matches_every_repository_consumer() -> None:
    inventory = _contract("public_imports.json")
    expected_cli = {
        item["path"]: set(item["imports"])
        for item in inventory["consumers"]
        if item["kind"] == "absolute-import"
    }
    actual_cli: dict[str, set[str]] = {}
    actual_renderer: dict[str, set[str]] = {}
    for root_name in ("src", "tools"):
        for path in (ROOT / root_name).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if relative == "src/codex_usage_hud/cli.py":
                continue
            names = _from_imports(path, "codex_usage_hud.cli")
            if names:
                actual_cli[relative] = names
            renderer_names = _from_imports(path, "codex_usage_hud.ui.renderer_hud")
            if renderer_names:
                actual_renderer[relative] = renderer_names
    assert actual_cli == expected_cli
    inventory_consumed = set().union(
        *(set(item["imports"]) for item in inventory["consumers"])
    )
    assert inventory_consumed | set(inventory["explicitCompatibilityExports"]) == set(
        inventory["supportedCliImports"]
    )
    expected_renderer = {
        item["path"]: set(item["imports"])
        for item in inventory["rendererConsumers"]
    }
    assert actual_renderer == expected_renderer
    assert set().union(*actual_renderer.values()) <= set(
        inventory["rendererFacadeImports"]
    )
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'codex-hud = "codex_usage_hud.helper_entrypoint:main"' in pyproject
    assert _from_imports(SRC / "__main__.py", "helper_entrypoint") == {"main"}
    build_exe = (ROOT / "tools/build_exe.py").read_text(encoding="utf-8")
    assert 'from codex_usage_hud.helper_entrypoint import main' in build_exe


def test_package_resource_inventory_is_complete_for_current_assets() -> None:
    inventory = _contract("package_resources.json")
    actual = {
        path.relative_to(SRC.parent).as_posix()
        for path in (SRC / "assets").iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".png"}
    }
    assert actual == set(inventory["resources"])
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    build_exe = (ROOT / "tools/build_exe.py").read_text(encoding="utf-8")
    for resource in inventory["resources"]:
        relative = resource.removeprefix("codex_usage_hud/")
        assert f"src/codex_usage_hud/{relative}" in pyproject
        assert Path(relative).name in build_exe
        package_path = resources.files("codex_usage_hud.assets").joinpath(
            Path(relative).name
        )
        assert package_path.is_file()
        assert package_path.read_bytes() == (SRC / relative).read_bytes()


def test_renderer_bundle_matches_frozen_p0_contract() -> None:
    contract = _contract("renderer_contract.json")
    assert contract["p0Baseline"]["byteLength"] == 548705
    assert contract["p0Baseline"]["sha256"] == (
        "f83d65265c55143fa775f509f09ce109c2aa75a03946b42cf462cb6cbd4ea637"
    )
    current = contract["currentBundle"]
    encoded = RENDERER_HUD_SCRIPT.encode("utf-8")
    actual_length = len(encoded)
    actual_hash = hashlib.sha256(encoded).hexdigest()
    assert actual_length == current["byteLength"], (
        "Renderer injected bundle contract is stale: "
        f"expected {current['byteLength']}, actual {actual_length}. "
        "Run `python tools/update_renderer_contract.py --update` after review."
    )
    assert actual_hash == current["sha256"], (
        "Renderer injected bundle hash contract is stale: "
        f"expected {current['sha256']}, actual {actual_hash}. "
        "Run `python tools/update_renderer_contract.py --update` after review."
    )

    globals_found = sorted(
        set(re.findall(r"window\.(__codexUsageHud[A-Za-z0-9_]+)", RENDERER_HUD_SCRIPT))
    )
    assert globals_found == contract["publicGlobals"]
    bindings_found = sorted(
        set(re.findall(r'const\s+\w+BindingName\s*=\s*"(codexUsageHud[A-Za-z0-9]+)"', RENDERER_HUD_SCRIPT))
    )
    assert bindings_found == contract["bindings"]
    assert sorted(set(re.findall(r"codexUsageHud[A-Za-z0-9]*:v\d+", RENDERER_HUD_SCRIPT))) == contract["storageKeys"]


def test_renderer_payload_order_and_lifecycle_match_contract() -> None:
    contract = _contract("renderer_contract.json")
    function = RENDERER_HUD_SCRIPT.split("function applyPayloadDomains", 1)[1].split(
        "window.__codexUsageHudUpdate", 1
    )[0]
    positions = [function.index(f'"{domain}" in domains') for domain in contract["payloadApplyOrder"]]
    assert positions == sorted(positions)
    lifecycle_counts = {
        "mutationObservers": RENDERER_HUD_SCRIPT.count("new MutationObserver"),
        "resizeObservers": RENDERER_HUD_SCRIPT.count("new ResizeObserver"),
        "setIntervals": RENDERER_HUD_SCRIPT.count("ctx.lifecycle.interval("),
        "setTimeouts": RENDERER_HUD_SCRIPT.count("ctx.lifecycle.timeout("),
        "kernelSetIntervals": RENDERER_HUD_SCRIPT.count("setInterval("),
        "kernelSetTimeouts": RENDERER_HUD_SCRIPT.count("setTimeout("),
    }
    assert lifecycle_counts == contract["lifecycleCounts"], (
        "Renderer lifecycle contract is stale: "
        f"actual {lifecycle_counts}, expected {contract['lifecycleCounts']}. "
        "Run `python tools/update_renderer_contract.py --update` after review."
    )
    for key in contract["sequenceKeys"]:
        assert key in RENDERER_HUD_SCRIPT
    inventory = contract["currentLifecycleInventory"]
    assert {entry["kind"] for entry in inventory} == {
        "MutationObserver",
        "ResizeObserver",
        "setInterval",
    }
    for entry in inventory:
        assert entry["creation"] in RENDERER_HUD_SCRIPT
        assert entry["cleanup"] in RENDERER_HUD_SCRIPT
        assert entry["owner"] and entry["startCondition"]
    timeout_owners = contract["currentTimeoutOwners"]
    assert len(timeout_owners) == contract["lifecycleCounts"]["setTimeouts"]
    for owner in set(timeout_owners):
        assert re.search(
            rf'ctx\.lifecycle\.timeout\(\s*"{re.escape(owner)}"',
            RENDERER_HUD_SCRIPT,
        )
    assert "ctx.lifecycle.clearTimeout(" in RENDERER_HUD_SCRIPT

    renderer_owner = "\n".join(
        (
            (SRC / "ui/renderer_domains.py").read_text(encoding="utf-8"),
            (SRC / "renderer_client.py").read_text(encoding="utf-8"),
        )
    )
    binding_inventory = contract["bindingInventory"]
    assert sorted(entry["name"] for entry in binding_inventory) == contract["bindings"]
    assert [entry["installOrder"] for entry in binding_inventory] == [1, 2, 3, 4, 5]
    for entry in binding_inventory:
        assert entry["name"] in renderer_owner
        assert entry["cleanup"] in renderer_owner
        assert entry["owner"] and entry["startCondition"]


def test_renderer_facades_and_cdp_package_boundaries_are_explicit() -> None:
    domain_path = SRC / "ui/renderer_domains.py"
    renderer_facade = SRC / "ui/renderer_hud.py"
    cdp_file = SRC / "renderer_cdp.py"
    cdp_package = SRC / "renderer_cdp"

    assert len(domain_path.read_text(encoding="utf-8").splitlines()) <= 200
    renderer_source = renderer_facade.read_text(encoding="utf-8")
    assert "sys.modules" not in renderer_source
    assert "__all__" in renderer_source
    assert not cdp_file.exists()
    assert len((cdp_package / "__init__.py").read_text(encoding="utf-8").splitlines()) <= 100

    forbidden = {
        "codex_usage_hud.cli",
        "codex_usage_hud.runtime_orchestration",
        "codex_usage_hud.renderer_payload_builder",
        "codex_usage_hud.runtime_settings",
        "codex_usage_hud.overlay_commands",
    }
    violations: list[str] = []
    for path in cdp_package.glob("*.py"):
        imports = _imports(path)
        for imported in sorted(forbidden & imports):
            violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert violations == []


def test_representative_renderer_screenshot_is_current_and_frozen() -> None:
    screenshot = _contract("renderer_contract.json")["representativeScreenshot"]
    path = ROOT / screenshot["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == screenshot["sha256"]
    assert screenshot["status"] == "current-live-cdp-capture"
    assert screenshot["platform"] == "windows"
    assert screenshot["targetUrl"] == "app://-/index.html"
    assert screenshot["rootSelector"] == "#codex-usage-hud-root"
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(data[16:20], "big") == screenshot["width"]
    assert int.from_bytes(data[20:24], "big") == screenshot["height"]
