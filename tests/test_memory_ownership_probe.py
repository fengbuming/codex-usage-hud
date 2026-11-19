"""The diagnostic walks ownership without counting globals or aliases twice."""
from __future__ import annotations

import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "memory_ownership_probe", Path(__file__).resolve().parents[1] / "tools/capture_hud_memory_ownership.py",
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_shared_owners_are_counted_once():
    shared = ["unique shared value" * 100]
    seen = set()
    first = probe.walk_sizes([shared], seen)
    second = probe.walk_sizes([shared], seen)
    assert first["bytes"] >= len(shared[0])
    assert second == {"bytes": 0, "objects": 0, "top_types": []}


def test_function_globals_are_not_business_owned():
    namespace = {"sentinel": "private unrelated global" * 100000}
    exec("def callback(): return sentinel", namespace)
    seen = set()
    probe.walk_sizes([namespace["callback"]], seen)
    assert id(namespace["sentinel"]) not in seen
    assert id(namespace) not in seen


def test_slot_owner_can_retain_a_large_payload():
    class Owner:
        __module__ = "codex_usage_hud.diagnostic_fixture"
        __slots__ = "payload"

    owner = Owner()
    owner.payload = ["slot value" * 1000]
    seen = set()
    probe.walk_sizes([owner], seen)
    assert id(owner.payload) in seen
    assert id(owner.payload[0]) in seen


def test_owner_boundary_defers_another_owner():
    shared_owner = ["another owner" * 1000]
    first = {"callback_capture": shared_owner}
    seen = set()
    probe.walk_sizes([first], seen, blocked={id(shared_owner)})
    assert id(shared_owner) not in seen
    result = probe.walk_sizes([shared_owner], seen)
    assert result["bytes"] >= len(shared_owner[0])


def test_frame_inventory_does_not_hold_probe_frames():
    frames = probe.app_frames()
    assert all(frame.f_globals is not vars(probe) for frame in frames.values())


def test_text_file_owner_includes_its_binary_buffer():
    import io

    buffer = io.BytesIO(b"owned buffer" * 1000)
    wrapper = io.TextIOWrapper(buffer, encoding="utf-8")
    try:
        seen = set()
        result = probe.walk_sizes([wrapper], seen)
        assert id(buffer) in seen
        assert result["bytes"] >= buffer.__sizeof__()
    finally:
        wrapper.close()


def test_full_walk_traverses_previously_counted_objects():
    namespace = {"sentinel": "module-only constant" * 1000}
    exec("def callback(): return sentinel", namespace)
    seen = set()
    probe.walk_sizes([namespace["callback"]], seen)
    assert id(namespace["sentinel"]) not in seen
    probe.walk_sizes([namespace["callback"]], seen, full=True)
    assert id(namespace["sentinel"]) in seen


def test_thread_state_layout_agrees_with_current_c_api():
    import ctypes
    import sys

    if sys.platform != "win32" or sys.version_info[:3] != (3,14,3):
        return
    roots = probe.thread_state_storage()
    current = ctypes.pythonapi.PyThreadState_GetDict
    current.restype = ctypes.c_void_p
    assert current() in {id(value) for value in roots}


def test_probe_cleanup_preserves_unrelated_ctypes_cache_entries():
    import ctypes

    class Temporary(ctypes.Structure):
        __module__ = "__hud_live_probe__"
        _fields_ = [("value",ctypes.c_int)]

    class Other(ctypes.Structure):
        _fields_ = [("value",ctypes.c_int)]

    temporary_pointer = ctypes.POINTER(Temporary)
    other_pointer = ctypes.POINTER(Other)
    cache = getattr(ctypes, "_pointer_type_cache", None)
    # CPython 3.14 moved pointer types from a global cache onto their classes.
    caches_on_class = getattr(Temporary, "__pointer_type__", None) is temporary_pointer
    try:
        if caches_on_class:
            assert Other.__pointer_type__ is other_pointer
        else:
            assert cache[Temporary] is temporary_pointer
            assert cache[Other] is other_pointer

        assert probe.clear_probe_ctype_caches() >= 1
        if caches_on_class:
            assert not hasattr(Temporary, "__pointer_type__")
            assert Other.__pointer_type__ is other_pointer
        else:
            assert Temporary not in cache
            assert cache[Other] is other_pointer
    finally:
        for structure in (Temporary, Other):
            if "__pointer_type__" in vars(structure):
                del structure.__pointer_type__
            if isinstance(cache, dict):
                cache.pop(structure, None)
