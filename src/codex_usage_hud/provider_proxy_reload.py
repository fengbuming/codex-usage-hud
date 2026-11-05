"""Swap request-handler generations while keeping the listener and streams alive."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import importlib.abc
import importlib.util
from pathlib import Path
import sys
from threading import RLock
import uuid


def bootstrap_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for name in ("provider_proxy_service.py", "provider_proxy_reload.py"):
        path = root / name
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


class _SnapshotImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Keep lazy relative imports on the same immutable source generation."""

    def __init__(self, root: Path):
        self.root = root
        self.prefix = "_codex_hud_proxy_" + uuid.uuid4().hex
        self.sources = {str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
                        for p in root.rglob("*.py") if "__pycache__" not in p.parts}
        digest = hashlib.sha256()
        for name, source in sorted(self.sources.items()):
            digest.update(name.encode() + b"\0" + source + b"\0")
        self.digest = digest.hexdigest()

    def _file(self, fullname):
        relative = fullname[len(self.prefix):].lstrip(".").replace(".", "/")
        package = (relative + "/" if relative else "") + "__init__.py"
        return (package, True) if package in self.sources else (relative + ".py", False)

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.prefix and not fullname.startswith(self.prefix + "."):
            return None
        name, package = self._file(fullname)
        if name not in self.sources:
            raise ModuleNotFoundError(f"Module is absent from proxy source snapshot: {fullname}")
        spec = importlib.util.spec_from_loader(fullname, self, is_package=package)
        spec.origin = str(self.root / name)
        if package:
            spec.submodule_search_locations = [str((self.root / name).parent)]
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name, package = self._file(module.__name__)
        module.__file__ = str(self.root / name)
        if package:
            module.__path__ = [str((self.root / name).parent)]
        exec(compile(self.sources[name], module.__file__, "exec"), module.__dict__)

    def close(self):
        if self in sys.meta_path:
            sys.meta_path.remove(self)
        for name in tuple(sys.modules):
            if name == self.prefix or name.startswith(self.prefix + "."):
                sys.modules.pop(name, None)


@dataclass
class _Generation:
    name: str
    relay: object
    handler: type
    snapshot: _SnapshotImporter | None = None
    active: int = 0


class ProxyReloadManager:
    def __init__(self, relay, *, source_root: Path | None = None):
        from .provider_proxy import ProviderProxyRelay

        self.owner = relay
        self.source_root = source_root or Path(__file__).parent
        self.bootstrap_digest = bootstrap_digest(self.source_root)
        self.lock = RLock()
        self.reload_lock = RLock()
        self.current = _Generation("initial", relay, ProviderProxyRelay._handler_type(relay))
        self.generations = [self.current]

    def health(self):
        with self.lock:
            return {"reloadSupported": not getattr(sys, "frozen", False),
                    "generation": self.current.name,
                    "bootstrapDigest": self.bootstrap_digest,
                    "sourceDigest": self.current.snapshot.digest if self.current.snapshot else "",
                    "activeRequests": sum(g.active for g in self.generations),
                    "retiredGenerations": sum(g is not self.current for g in self.generations)}

    def dispatch(self, request):
        with self.lock:
            generation = self.current
            generation.active += 1
        try:
            # HTTP parsing and socket ownership stay with the stable server.
            # A view using the captured handler class gets its own methods but
            # shares request state, including rfile/wfile and close_connection.
            view = object.__new__(generation.handler)
            view.__dict__ = request.__dict__
            return view._forward()
        finally:
            with self.lock:
                generation.active -= 1
                self._retire_finished()

    def route(self, metadata, config_path):
        with self.lock:
            return self.current.relay.route(metadata, config_path)

    def _retire_finished(self):
        for generation in tuple(self.generations):
            if generation is not self.current and not generation.active:
                if generation.snapshot:
                    generation.snapshot.close()
                self.generations.remove(generation)

    def reload(self):
        if getattr(sys, "frozen", False):
            raise RuntimeError("Proxy source reload is available only for a source checkout.")
        with self.reload_lock:
            snapshot = _SnapshotImporter(self.source_root)
            with self.lock:
                if self.current.snapshot and self.current.snapshot.digest == snapshot.digest:
                    return {**self.health(), "changed": False}
            sys.meta_path.insert(0, snapshot)
            try:
                module = importlib.import_module(snapshot.prefix + ".provider_proxy")
                # Preflight routing/auth imports before publishing a generation;
                # failures cannot replace the working request handler.
                importlib.import_module(snapshot.prefix + ".provider_gateway")
                importlib.import_module(snapshot.prefix + ".codex_provider_config")
                relay = module.ProviderProxyRelay(self.owner.port)
                for attribute in ("server", "routes", "config_paths", "lock", "source_evidence_listeners"):
                    setattr(relay, attribute, getattr(self.owner, attribute))
                generation = _Generation(snapshot.prefix, relay, relay._handler_type(), snapshot)
            except BaseException:
                snapshot.close()
                raise
            with self.lock:
                self.current = generation
                self.generations.append(generation)
                self._retire_finished()
                return {**self.health(), "changed": True}

    def close(self):
        with self.lock:
            if any(g.active for g in self.generations):
                raise RuntimeError("Active proxy requests must finish before closing generations.")
            for generation in self.generations:
                if generation.snapshot:
                    generation.snapshot.close()
            self.generations.clear()
