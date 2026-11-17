"""One-shot Windows/CPython 3.14 memory census; no HUD reload or polling hook.

Runs a size-only callback through CPython's supported sys.remote_exec API.
Never exports object contents, changes application owners, starts tracemalloc,
or releases/compacts heaps. The callback runs at a main-thread
safe point and can briefly pause the HUD. Output is diagnostic data, not a dump.
An explicit --collect-gc adds a collection experiment. The external VM handshake
holds the GIL in a bounded kernel wait while the controller reads region metadata.
"""
from __future__ import annotations

import bisect
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import re
import sys
import time
import types
import weakref


class Mbi(c.Structure):
    _fields_ = [("base", c.c_void_p), ("allocation", c.c_void_p),
        ("allocation_protect", w.DWORD), ("partition", w.WORD), ("padding", w.WORD),
        ("size", c.c_size_t), ("state", w.DWORD), ("protect", w.DWORD), ("kind", w.DWORD)]


class Region(c.Structure):
    _fields_ = [("base", c.c_void_p), ("protect", w.DWORD), ("flags", w.DWORD),
        ("size", c.c_size_t), ("commit", c.c_size_t)]


class Counters(c.Structure):
    _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [(n, c.c_size_t)
        for n in ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
                  "pagefile", "peak_pagefile", "private")]


class Heap(c.Structure):
    _fields_ = [("cb", w.DWORD)] + [(n, c.c_size_t)
        for n in ("allocated", "committed", "reserved", "max_reserve")]


class ThreadEntry(c.Structure):
    _fields_ = [(n, w.DWORD) for n in ("size", "usage", "tid", "pid")] + [
        ("priority", w.LONG), ("delta", w.LONG), ("flags", w.DWORD)]


class ThreadBasic(c.Structure):
    _fields_ = [("exit", w.LONG), ("teb", c.c_void_p), ("pid", c.c_void_p),
        ("tid", c.c_void_p), ("affinity", c.c_size_t), ("priority", w.LONG), ("base_priority", w.LONG)]


class HeapDetails(c.Union):
    _fields_ = [("raw", c.c_byte * 24)]


class HeapEntry(c.Structure):
    _fields_ = [("data", c.c_void_p), ("size", w.DWORD), ("overhead", c.c_byte),
               ("region_index", c.c_byte), ("flags", w.WORD), ("details", HeapDetails)]


class DebugOffsets(c.Structure):
    # Prefix of CPython 3.14's shipped pycore_debug_offsets.h.
    _fields_ = [('cookie',c.c_char*8),('version',c.c_uint64),('free_threaded',c.c_uint64),
                ('runtime',c.c_uint64*3),('interpreter',c.c_uint64*16),('thread',c.c_uint64*9)]


def thread_state_storage():
    """Read existing thread-state dictionaries; never create or switch states."""
    if sys.version_info[:3] != (3,14,3) or sys.maxsize < 2**32 or not sys._is_gil_enabled():
        return []
    offsets=DebugOffsets.in_dll(c.pythonapi,'_PyRuntime')
    if offsets.cookie != b'xdebugpy' or offsets.version != sys.hexversion or offsets.free_threaded:
        return []
    dict_offset=offsets.thread[4]+7*8
    if dict_offset+8 > offsets.thread[0]:
        return []
    get_interp=c.pythonapi.PyInterpreterState_Get
    get_interp.restype=c.c_void_p
    head=c.pythonapi.PyInterpreterState_ThreadHead
    head.argtypes=[c.c_void_p]
    head.restype=c.c_void_p
    next_thread=c.pythonapi.PyThreadState_Next
    next_thread.argtypes=[c.c_void_p]
    next_thread.restype=c.c_void_p
    state=head(get_interp())
    roots=[]
    while state:
        pointer=c.c_void_p.from_address(state+dict_offset).value
        if pointer:
            # The field address comes from version-checked debug offsets. A
            # dictionary type check guards against a mismatched private layout.
            if c.c_void_p.from_address(pointer+8).value != id(dict):
                raise RuntimeError('thread-state dictionary layout mismatch')
            roots.append(c.cast(pointer,c.py_object).value)
        state=next_thread(state)
    return roots


def clear_probe_ctype_caches():
    """Remove only this diagnostic's transient ctypes classes from global caches."""
    def probe_type(value):
        if isinstance(value,(tuple,list)):
            return any(probe_type(item) for item in value)
        return isinstance(value,type) and value.__module__ == '__hud_live_probe__'

    removed=0
    for value in gc.get_objects():
        if isinstance(value,type) and value.__module__ == '__hud_live_probe__':
            if hasattr(value,'__pointer_type__'):
                del value.__pointer_type__
                removed+=1
    for name in ('_pointer_type_cache_fallback','_pointer_type_cache','_c_functype_cache','_win_functype_cache'):
        cache=getattr(c,name,{})
        if not isinstance(cache,dict):
            continue
        for key in list(cache):
            if probe_type(key):
                cache.pop(key)
                removed+=1
    return removed


def publish_text(path, text):
    pending=path.with_name(path.name+'.pending')
    pending.write_text(text,encoding='utf-8')
    os.replace(pending,path)


class Native:
    def __init__(self, pid):
        self.pid = pid
        # Keep the GIL for these short metadata calls so sampling does not
        # unnecessarily let Python workers mutate owners between each query.
        self.k = c.PyDLL("kernel32", use_last_error=True)
        self.ps = c.PyDLL("psapi", use_last_error=True)
        self.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenProcess.restype = w.HANDLE
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.process = self.k.OpenProcess(0x410, False, pid)
        if not self.process:
            raise c.WinError(c.get_last_error())
        self.ps.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.POINTER(Counters), w.DWORD]
        self.k.VirtualQueryEx.argtypes = [w.HANDLE, c.c_void_p, c.POINTER(Mbi), c.c_size_t]
        self.k.VirtualQueryEx.restype = c.c_size_t
        self.query = c.PyDLL("kernelbase").QueryVirtualMemoryInformation
        self.query.argtypes = [w.HANDLE, c.c_void_p, c.c_int, c.POINTER(Region), c.c_size_t, c.c_void_p]
        self.query.restype = w.BOOL
        self.ps.GetMappedFileNameW.argtypes = [w.HANDLE, c.c_void_p, w.LPWSTR, w.DWORD]

    def close(self):
        if self.process:
            self.k.CloseHandle(self.process)
            self.process = None

    def __del__(self):
        self.close()

    def memory(self):
        data = Counters()
        data.cb = c.sizeof(data)
        if not self.ps.GetProcessMemoryInfo(self.process, c.byref(data), data.cb):
            raise c.WinError(c.get_last_error())
        return {"private_bytes": data.private, "working_set_bytes": data.ws,
                "paged_pool_bytes": data.paged, "nonpaged_pool_bytes": data.nonpaged}

    def regions(self):
        rows, allocations, failures = [], {}, []
        address = 0
        while True:
            data = Mbi()
            if not self.k.VirtualQueryEx(self.process, address, c.byref(data), c.sizeof(data)):
                break
            if data.allocation:
                if data.allocation not in allocations:
                    info = Region()
                    if self.query(self.process, data.allocation, 0, c.byref(info), c.sizeof(info), None):
                        row = {"base": data.allocation, "size": info.size, "commit": info.commit,
                               "kind": data.kind, "flags": info.flags, "protection": info.protect}
                        if data.kind == 0x1000000:
                            name = c.create_unicode_buffer(512)
                            if self.ps.GetMappedFileNameW(self.process, data.allocation, name, len(name)):
                                row["image"] = name.value.rsplit("\\", 1)[-1]
                        allocations[data.allocation] = row
                    else:
                        failures.append(c.get_last_error())
                if data.state == 0x1000:
                    rows.append({"base": data.base, "allocation": data.allocation,
                                 "size": data.size, "kind": data.kind, "protect": data.protect})
            address = (data.base or 0) + data.size
        return rows, allocations, failures

    def heaps(self):
        if self.pid != os.getpid():
            raise ValueError("heap summaries must run inside the target")
        self.k.GetProcessHeaps.argtypes = [w.DWORD, c.POINTER(w.HANDLE)]
        self.k.GetProcessHeaps.restype = w.DWORD
        self.k.HeapSummary.argtypes = [w.HANDLE, w.DWORD, c.POINTER(Heap)]
        count = self.k.GetProcessHeaps(0, None)
        handles = (w.HANDLE * count)()
        self.k.GetProcessHeaps(count, handles)
        rows = []
        for handle in handles:
            data = Heap()
            data.cb = c.sizeof(data)
            if not self.k.HeapSummary(handle, 0, c.byref(data)):
                raise c.WinError(c.get_last_error())
            rows.append({"allocated": data.allocated, "committed": data.committed,
                         "reserved": data.reserved})
        return rows

    def heap_allocations(self):
        """Collect backing allocation bases; hold no Python lock across a GIL release."""
        self.k.GetProcessHeaps.argtypes = [w.DWORD, c.POINTER(w.HANDLE)]
        self.k.GetProcessHeaps.restype = w.DWORD
        for name in ("HeapLock", "HeapUnlock"):
            getattr(self.k, name).argtypes = [w.HANDLE]
        self.k.HeapWalk.argtypes = [w.HANDLE, c.POINTER(HeapEntry)]
        count = self.k.GetProcessHeaps(0, None)
        handles = (w.HANDLE * count)()
        self.k.GetProcessHeaps(count, handles)
        result, all_bases = [], set()
        for handle in handles:
            entry, region = HeapEntry(), Mbi()
            bases = (c.c_size_t * 1024)()
            used = entries = busy = 0
            interval = sys.getswitchinterval()
            gc_enabled=gc.isenabled()
            locked = False
            try:
                sys.setswitchinterval(10.0)
                gc.disable()
                locked = bool(self.k.HeapLock(handle))
                if not locked:
                    raise c.WinError(c.get_last_error())
                while entries < 500000 and self.k.HeapWalk(handle, c.byref(entry)):
                    entries += 1
                    if entry.flags & 4:
                        busy += entry.size
                    if self.k.VirtualQueryEx(self.process, entry.data, c.byref(region), c.sizeof(region)):
                        index = 0
                        while index < used and bases[index] != region.allocation:
                            index += 1
                        if index == used and region.allocation:
                            if used == len(bases):
                                raise RuntimeError("heap allocation census capacity exceeded")
                            bases[used] = region.allocation
                            used += 1
                error = c.get_last_error()
            finally:
                if locked:
                    self.k.HeapUnlock(handle)
                sys.setswitchinterval(interval)
                if gc_enabled:
                    gc.enable()
            if error != 259 or entries == 500000:
                raise RuntimeError(f"incomplete heap walk: error={error}, entries={entries}")
            all_bases.update(bases[:used])
            result.append({"entries": entries, "busy_bytes": busy, "allocation_count": used})
        return all_bases, result

    def stacks(self):
        self.k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        self.k.CreateToolhelp32Snapshot.restype = w.HANDLE
        self.k.Thread32First.argtypes = [w.HANDLE, c.POINTER(ThreadEntry)]
        self.k.Thread32Next.argtypes = [w.HANDLE, c.POINTER(ThreadEntry)]
        self.k.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenThread.restype = w.HANDLE
        self.k.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.c_void_p]
        query = c.PyDLL("ntdll").NtQueryInformationThread
        query.argtypes = [w.HANDLE, c.c_int, c.POINTER(ThreadBasic), w.DWORD, c.c_void_p]
        query.restype = w.LONG
        snapshot = self.k.CreateToolhelp32Snapshot(4, 0)
        entry = ThreadEntry()
        entry.size = c.sizeof(entry)
        rows = []
        try:
            ok = self.k.Thread32First(snapshot, c.byref(entry))
            while ok:
                if entry.pid == self.pid:
                    handle = self.k.OpenThread(0x40, False, entry.tid)
                    if handle:
                        try:
                            basic = ThreadBasic()
                            tib = (c.c_void_p * 3)()
                            if query(handle, 0, c.byref(basic), c.sizeof(basic), None) >= 0 and basic.teb:
                                if self.k.ReadProcessMemory(self.process, basic.teb, tib, c.sizeof(tib), None):
                                    region = Mbi()
                                    if self.k.VirtualQueryEx(self.process, tib[2], c.byref(region), c.sizeof(region)):
                                        row = {"tid": entry.tid, "allocation": region.allocation,
                                               "stack_base": tib[1], "stack_limit": tib[2]}
                                        if self.k.VirtualQueryEx(self.process, basic.teb, c.byref(region), c.sizeof(region)):
                                            row['teb_allocation'] = region.allocation
                                        rows.append(row)
                        finally:
                            self.k.CloseHandle(handle)
                ok = self.k.Thread32Next(snapshot, c.byref(entry))
        finally:
            self.k.CloseHandle(snapshot)
        return rows


def allocator_stats(path):
    crt = c.PyDLL("ucrtbase")
    crt._wfopen.argtypes = [c.c_wchar_p, c.c_wchar_p]
    crt._wfopen.restype = c.c_void_p
    crt.fclose.argtypes = [c.c_void_p]
    crt.fread.argtypes = [c.c_void_p, c.c_size_t, c.c_size_t, c.c_void_p]
    crt.fread.restype = c.c_size_t
    file = crt._wfopen(str(path), "w")
    if not file:
        raise OSError("cannot open allocator statistics file")
    try:
        function = c.pythonapi._PyObject_DebugMallocStats
        function.argtypes = [c.c_void_p]
        function.restype = c.c_int
        function(file)
    finally:
        crt.fclose(file)
    # Read through the CRT too: Path.read_text would release the GIL in the
    # middle of the native census and let another worker change the sample.
    file = crt._wfopen(str(path), "rb")
    try:
        buffer = c.create_string_buffer(8192)
        length = crt.fread(buffer, 1, len(buffer), file)
        text = buffer.raw[:length].decode("utf-8")
    finally:
        crt.fclose(file)
    patterns = {"arenas": r"arenas allocated current\s*=\s*([\d,]+)",
        "highwater_arenas": r"arenas highwater mark\s*=\s*([\d,]+)",
        "used_blocks": r"bytes in allocated blocks\s*=\s*([\d,]+)",
        "available_blocks": r"bytes in available blocks\s*=\s*([\d,]+)",
        "unused_pools": r"unused pools \* \d+ bytes\s*=\s*([\d,]+)",
        "pool_headers": r"bytes lost to pool headers\s*=\s*([\d,]+)",
        "quantization": r"bytes lost to quantization\s*=\s*([\d,]+)",
        "alignment": r"bytes lost to arena alignment\s*=\s*([\d,]+)"}
    return {key: int(re.search(pattern, text).group(1).replace(",", "")) for key, pattern in patterns.items()}


def sqlite_stats():
    try:
        dll = c.PyDLL("sqlite3")
    except OSError:
        return {"available": False}
    function = dll.sqlite3_status64
    function.argtypes = [c.c_int, c.POINTER(c.c_longlong), c.POINTER(c.c_longlong), c.c_int]
    result = {}
    for name, op in (("memory", 0), ("pagecache_overflow", 2), ("malloc_size", 5)):
        current, peak = c.c_longlong(), c.c_longlong()
        code = function(op, c.byref(current), c.byref(peak), 0)
        result[name] = {"current": current.value, "peak": peak.value, "result": code}
    return result


def business_children(value):
    """Follow data ownership, without pulling in a function's module globals."""
    if isinstance(value, dict):
        return [*value.keys(), *value.values()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    if isinstance(value, types.FunctionType):
        return [value.__defaults__, value.__kwdefaults__, *(value.__closure__ or ())]
    if isinstance(value, types.MethodType):
        return [value.__self__]
    if isinstance(value, types.CellType):
        try:
            return [value.cell_contents]
        except ValueError:
            return []
    if isinstance(value, types.FrameType):
        return [ref for ref in gc.get_referents(value)
                if ref is not value.f_globals and ref is not value.f_builtins]
    if isinstance(value, (types.ModuleType, type, types.CodeType)):
        return []
    module = type(value).__module__
    if module in {"_io", "tempfile", "pathlib", "pathlib._local", "pathlib._abc", "_functools", "functools", "_thread"}:
        return gc.get_referents(value)
    if module.startswith("codex_usage_hud") or module in {"threading", "queue", "collections", "argparse"}:
        children = []
        try:
            children.append(object.__getattribute__(value, "__dict__"))
        except AttributeError:
            slots = getattr(type(value), "__slots__", ())
            for name in (slots,) if isinstance(slots, str) else slots:
                try:
                    children.append(object.__getattribute__(value, name))
                except AttributeError:
                    pass
        if module == "collections" and type(value).__name__ == "deque":
            children.extend(value)
        return children
    return []


def walk_sizes(roots, seen, *, full=False, blocked=()):
    pending = list(roots)
    traversed = set()
    blocked = set(blocked) | {id(seen), id(traversed), id(pending)}
    total, count = 0, 0
    by_type = {}
    while pending:
        value = pending.pop()
        identifier = id(value)
        if identifier in traversed or identifier in blocked or (not full and identifier in seen):
            continue
        traversed.add(identifier)
        if isinstance(value, types.FrameType) and (
            not value.f_globals or value.f_globals.get('__name__') == '__hud_live_probe__'
            or Path(value.f_code.co_filename).name.startswith('hud-live-')
        ):
            continue
        if isinstance(value, types.FunctionType) and value.__globals__.get("__name__") == "__hud_live_probe__":
            continue
        if getattr(type(value), "__module__", "") == "__hud_live_probe__":
            continue
        if not full and isinstance(value, (types.ModuleType, type, types.CodeType)):
            continue
        if identifier not in seen:
            seen.add(identifier)
            size = sys.getsizeof(value)
            total += size
            count += 1
            name = type(value).__module__ + "." + type(value).__name__
            by_type[name] = by_type.get(name, 0) + size
        if full:
            pending.extend(gc.get_referents(value))
            if isinstance(value, types.CodeType):
                pending.extend([value.co_consts, value.co_names, value.co_filename,
                                value.co_name, value.co_qualname, value.co_linetable, value.co_exceptiontable])
        else:
            pending.extend(business_children(value))
    return {"bytes": total, "objects": count,
            "top_types": sorted(by_type.items(), key=lambda x: x[1], reverse=True)[:8]}


def app_frames():
    """Never return the current diagnostic frame: that would retain the census."""
    frames = sys._current_frames()
    current = __import__("_thread").get_ident()
    frame = frames.pop(current)
    while frame and not ("codex_usage_hud" in frame.f_code.co_filename and frame.f_globals is not globals()):
        frame = frame.f_back
    if frame is not None:
        frames[current] = frame
    return frames


def python_owner_snapshot(private, diagnostic_data):
    """Capture frame roots and GC cohort in the same GIL-held epoch."""
    interval = sys.getswitchinterval()
    try:
        sys.setswitchinterval(10.0)
        frames = app_frames()
        loop_locals, thread_roots, frame_roots = {}, [], []
        for frame in frames.values():
            while frame:
                if frame.f_globals is not globals():
                    values=list(frame.f_locals.values())
                    thread_roots.extend(values)
                    frame_roots.append((Path(frame.f_code.co_filename).name,frame.f_code.co_name,values))
                    if frame.f_code.co_name == 'run_renderer_hud_session' and 'context' in frame.f_locals:
                        loop_locals = dict(frame.f_locals)
                frame = frame.f_back
        context = loop_locals.get('context')
        owners = []
        if context is not None:
            for name in ('user_config','provider_registry','parser','pre_send_estimator','runtime_events','runtime_errors',
                         'session_snapshot_cache','active_work_snapshot_cache','usage_cache',
                         'session_cleanup_manager','session_index_warm_job','active_session_tracker',
                         'session_resolver','background_usage_runtime','usage_insights_worker','sse_tracker'):
                owners.append((name,[getattr(context,name,None)]))
        client = loop_locals.get('client')
        if client is not None:
            owners.extend([('renderer_history_store',[client.history_store]),('renderer_client',[client])])
        for name in ('loop_state','work_overlay','daemon_manager','assembly','context'):
            owners.append((name,[loop_locals.get(name)]))
        diagnostic_ids = set()
        walk_sizes(diagnostic_data,diagnostic_ids)
        cohort = gc.get_objects()
        blocked = diagnostic_ids | {id(globals()),id(frames),id(loop_locals),id(thread_roots),
                                    id(cohort),id(diagnostic_ids)}
        seen, owned = set(), {}
        boundaries = {id(value) for _name,roots in owners for value in roots if value is not None}
        for name,roots in owners:
            boundary = boundaries.difference(id(value) for value in roots)
            owned[name] = walk_sizes(roots,seen,blocked=blocked|boundary)
        frame_details=[]
        frame_total={'bytes':0,'objects':0}
        for source,function,roots in frame_roots:
            row=walk_sizes(roots,seen,blocked=blocked)
            frame_total['bytes']+=row['bytes']
            frame_total['objects']+=row['objects']
            if row['bytes']>8192:
                frame_details.append({'source':source,'function':function,**row})
        owned['other_thread_and_loop_locals']=frame_total
        owned['thread_state_storage'] = walk_sizes(thread_state_storage(),seen,blocked=blocked)
        owned['module_code_globals_and_interpreter_metadata'] = walk_sizes(list(sys.modules.values()),seen,full=True,blocked=blocked)
        intern_tables = []
        for value in cohort:
            if isinstance(value,dict) and len(value)>10000:
                if all(isinstance(key,str) and key is item and sys._is_interned(key) for key,item in value.items()):
                    intern_tables.append(value)
        owned['interpreter_interned_string_tables'] = walk_sizes(intern_tables,seen,full=True,blocked=blocked)
        weak_maps=[]
        for value in cohort:
            if isinstance(value,dict) and value:
                if all(type(key) is int and type(item) is weakref.ReferenceType
                       and (item() is None or id(item())==key) for key,item in value.items()):
                    weak_maps.append(value)
        owned['static_type_subclass_weak_maps'] = walk_sizes(weak_maps,seen,full=True,blocked=blocked)
        owned['remaining_gc_cohort'] = walk_sizes(cohort,seen,full=True,blocked=blocked)
        starts = sorted(row['base'] for row in private)
        by_base = {row['base']:row for row in private}
        arena_hits, inventory, buffer_regions = set(), {}, {}
        for value in cohort:
            pos = bisect.bisect_right(starts,id(value))-1
            if pos>=0:
                row = by_base[starts[pos]]
                if row['size']==row['commit']==1024*1024 and id(value)<row['base']+row['size']:
                    arena_hits.add(row['base'])
            if type(value).__module__ in {'codex_usage_hud.disk_sequence','codex_usage_hud.core.selected_history'}:
                key=type(value).__name__
                inventory[key]=inventory.get(key,0)+1
            if sys.getsizeof(value)>262144 and isinstance(value,(set,frozenset,dict,list)):
                offset=40 if isinstance(value,(set,frozenset)) else 32 if isinstance(value,dict) else 24
                pointer=c.c_void_p.from_address(id(value)+offset).value
                if pointer:
                    pos=bisect.bisect_right(starts,pointer)-1
                    if pos>=0:
                        row=by_base[starts[pos]]
                        if pointer<row['base']+row['size']:
                            buffer_regions[row['base']]={'base':row['base'],'object_type':type(value).__name__,
                                                        'logical_bytes':sys.getsizeof(value),'commit':row['commit']}
        return {'python_owners':owned,'python_object_size_sum':sum(row['bytes'] for row in owned.values()),
                'one_mib_allocations_with_python_objects':len(arena_hits),'gc_disk_owner_counts':inventory,
                'interned_string_tables_found':len(intern_tables),'coherent_python_root_snapshot':True,
                'python_native_buffer_regions':list(buffer_regions.values()),
                'thread_frame_owners':sorted(frame_details,key=lambda x:x['bytes'],reverse=True)}
    finally:
        sys.setswitchinterval(interval)


def capture(output, *, require_hud=True, collect_gc=False, event_name=None):
    started = time.perf_counter()
    stale_probe_types=clear_probe_ctype_caches()
    gc_before=None
    native = Native(os.getpid())
    baseline = native.memory()
    contexts, loop_locals, frame_data = [], {}, set()
    frames = app_frames()
    for frame in frames.values():
        while frame:
            if frame.f_globals is not globals():
                # CPython 3.14 x64 GIL build, verified against pycore_frame.h:
                # f_frame follows PyObject_HEAD and f_back.
                if sys.maxsize > 2**32 and sys._is_gil_enabled():
                    frame_data.add(c.c_void_p.from_address(id(frame)+24).value)
                local = frame.f_locals
                if frame.f_code.co_name == "run_renderer_hud_session" and "context" in local:
                    loop_locals = dict(local)
                    contexts.append(local["context"])
            frame = frame.f_back
    if require_hud and not contexts:
        raise RuntimeError("target is not an active HUD main runtime")
    frames.clear()
    if collect_gc:
        gc_before=gc.collect()
        baseline=native.memory()
    interval = sys.getswitchinterval()
    try:
        sys.setswitchinterval(10.0)
        heap_bases, heap_walks = native.heap_allocations()
        stats = allocator_stats(output.with_suffix(".allocator.txt"))
        heaps = native.heaps()
        stacks = native.stacks()
        if event_name:
            native.k.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
            native.k.OpenEventW.restype = w.HANDLE
            native.k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            event = native.k.OpenEventW(0x100000, False, event_name)
            ready = native.k.OpenEventW(2, False, event_name+'-ready')
            native.k.SignalObjectAndWait.argtypes = [w.HANDLE,w.HANDLE,w.DWORD,w.BOOL]
            if not event or not ready:
                raise c.WinError(c.get_last_error())
            try:
                # Atomic kernel handshake: the parent cannot observe 'ready'
                # before this main thread actually enters its GIL-held wait.
                wait = native.k.SignalObjectAndWait(ready, event, 3000, False)
                if wait != 0:
                    raise RuntimeError(f"external VM sampler did not acknowledge: {wait}")
            finally:
                native.k.CloseHandle(event)
                native.k.CloseHandle(ready)
            external = json.loads(output.with_suffix('.vm.json').read_text(encoding='utf-8'))
            allocations = {row['base']: row for row in external['allocations']}
            failures = external['query_failures']
        else:
            _regions, allocations, failures = native.regions()
    finally:
        sys.setswitchinterval(interval)
    after_vm = native.memory()
    private = [row for row in allocations.values() if row["kind"] == 0x20000]
    one_mib = [row for row in private if row["size"] == row["commit"] == 1024 * 1024]
    stack_addresses = {row["allocation"] for row in stacks}
    teb_addresses = {row.get('teb_allocation') for row in stacks}
    allocation_starts = sorted(allocations)
    frame_allocations = set()
    for address in frame_data:
        if address:
            index = bisect.bisect_right(allocation_starts,address)-1
            if index >= 0:
                allocation = allocations[allocation_starts[index]]
                if address < allocation['base']+allocation['size']:
                    frame_allocations.add(allocation['base'])
    stack_commit = sum(row["commit"] for row in private if row["base"] in stack_addresses)
    heap_commit = sum(row["commit"] for row in private if row["base"] in heap_bases)
    private_commit = sum(row["commit"] for row in private)
    cow = sum(row["commit"] for row in allocations.values() if row["kind"] != 0x20000)
    arena_commit = stats["arenas"] * 1024 * 1024
    arena_bases = {row['base'] for row in one_mib}
    excluded = heap_bases | stack_addresses | arena_bases
    frame_commit = sum(row['commit'] for row in private if row['base'] in frame_allocations-excluded)
    excluded |= frame_allocations
    teb_commit = sum(row['commit'] for row in private if row['base'] in teb_addresses-excluded)
    excluded |= teb_addresses
    remaining_vm = [row for row in private if row['base'] not in excluded and row['commit']]
    ledger = {"pymalloc_arenas": arena_commit, "native_heap_committed": heap_commit,
        "thread_stack_commit": stack_commit,
        "python_interpreter_frame_stacks": frame_commit, "thread_environment_blocks":teb_commit,
        "other_private_virtual": private_commit - arena_commit - heap_commit - stack_commit - frame_commit - teb_commit,
        "image_and_mapping_cow_commit": cow}
    report = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
        "stale_diagnostic_type_cache_entries_removed":stale_probe_types,"gc_before_census":gc_before,
        "baseline": baseline, "after_vm_census": after_vm, "allocator": stats,
        "heaps": heaps, "heap_walks": heap_walks, "stack_count": len(stacks), "ledger": ledger,
        "vm_commit_sum": sum(ledger.values()), "vm_query_failures": failures, "other_vm_regions":remaining_vm,
        "counter_minus_vm_commit": after_vm["private_bytes"] - sum(ledger.values()),
        "fully_committed_one_mib_allocations": len(one_mib),
        "image_cow": sorted([{"image": row.get("image", "mapped view"), "commit": row["commit"]}
            for row in allocations.values() if row["kind"] != 0x20000 and row["commit"]], key=lambda x:x["commit"], reverse=True),
        "sqlite": sqlite_stats(), "python_module_count": len(sys.modules),
        "tracemalloc_active": bool(sys.modules.get("tracemalloc") and sys.modules["tracemalloc"].is_tracing())}
    if event_name:
        report['external_vm_census'] = {k:v for k,v in external.items() if k != 'allocations'}
        report['counter_minus_vm_commit'] = external['after']['private_bytes'] - sum(ledger.values())
        translation=external['nonzero_commit_translation_pages']*4096
        ledger['address_translation_commit']=translation
        report['address_translation_method']='x64 2 MiB/1 GiB span coverage of allocations with nonzero RegionInfo.CommitSize; independently checked against process counter'
    loop_locals.clear()
    report.update(python_owner_snapshot(private,[report,allocations,heaps,stacks,frame_data,heap_bases]))
    buffer_bases={row['base'] for row in report['python_native_buffer_regions']}
    buffer_commit=sum(row['commit'] for row in remaining_vm if row['base'] in buffer_bases)
    ledger['python_large_container_backing']=buffer_commit
    ledger['other_private_virtual']-=buffer_commit
    report['other_vm_regions']=[row for row in remaining_vm if row['base'] not in buffer_bases]
    report['accounted_private_commit']=sum(ledger.values())
    report['unaccounted_private_commit']=(external['after']['private_bytes'] if event_name else after_vm['private_bytes'])-report['accounted_private_commit']
    report["after_object_walk"] = native.memory()
    if collect_gc:
        # Drop the census roots first; otherwise the diagnostic itself would
        # prevent unreachable application objects from being collected.
        collected = gc.collect()
        report["gc_experiment"] = {"collected_objects": collected, "after_gc": native.memory(),
            "allocator_after_gc": allocator_stats(output.with_suffix(".after-gc.allocator.txt")),
            "heaps_after_gc": native.heaps()}
    report["elapsed_ms"] = round((time.perf_counter()-started)*1000, 1)
    report["limitations"] = [
        "Python owner sizes are unique logical object sizes, not additional to the OS commit ledger.",
        "Shared objects go to the first owner in the recorded traversal order; no object values are exported.",
        "The main-thread callback includes its setup overhead and may briefly pause HUD updates.",
        "VM and OS private counters are sampled sequentially; record their difference rather than forcing equality.",
        "Native heap busy bytes include large Python allocations, SQLite, CRT and other native libraries; the summary is not an allocation-stack trace."]
    report['python_version']=sys.version
    report['windows_build']=sys.getwindowsversion().build
    publish_text(output,json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    native.close()


def main():
    import argparse
    import tempfile
    import uuid

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--collect-gc", action="store_true", help="Controlled GC before and after the census")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:3] != (3, 14, 3) or sys.maxsize < 2**32:
        parser.error("private layout checks were verified on Windows x64 CPython 3.14.3 only")
    if args.self_check:
        capture(args.output.resolve(), require_hud=False, collect_gc=args.collect_gc)
        return
    if args.pid is None:
        parser.error("--pid is required for the HUD census")
    output = args.output.resolve()
    if output.exists() or output.with_suffix('.error.txt').exists():
        parser.error("use a fresh output path so a previous result cannot acknowledge a pending callback")
    script = Path(tempfile.gettempdir()) / ("hud-live-census-"+uuid.uuid4().hex+".py")
    event_name = "Local\\HudMemoryCensus-"+uuid.uuid4().hex
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateEventW.argtypes = [c.c_void_p,w.BOOL,w.BOOL,w.LPCWSTR]
    kernel.CreateEventW.restype = w.HANDLE
    kernel.SetEvent.argtypes = [w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    event = kernel.CreateEventW(None,True,False,event_name)
    ready_event = kernel.CreateEventW(None,True,False,event_name+'-ready')
    kernel.WaitForSingleObject.argtypes = [w.HANDLE,w.DWORD]
    if not event or not ready_event:
        raise c.WinError(c.get_last_error())
    body = (Path(__file__).read_text(encoding="utf-8") + "\ntry:\n    capture(Path("+repr(str(output))+
        "), collect_gc="+repr(args.collect_gc)+",event_name="+repr(event_name)+")\nexcept BaseException:\n    import traceback\n    publish_text(Path("+repr(str(output.with_suffix('.error.txt')))+
        "),traceback.format_exc())\nfinally:\n    try:\n        clear_probe_ctype_caches()\n    finally:\n        (lambda collect=gc.collect: (globals().clear(),collect()))()\n")
    compile(body,str(script),'exec')
    script.write_text("exec("+repr(body)+", {'__name__':'__hud_live_probe__'})\n", encoding="utf-8")
    sys.remote_exec(args.pid, str(script))
    print(json.dumps({"dispatched":True, "pid":args.pid, "script":str(script), "output":str(output)}), flush=True)
    # Keep the script alive until CPython acknowledges it by writing a result.
    deadline = time.monotonic() + 60
    sampled = False
    while time.monotonic() < deadline:
        if not sampled and kernel.WaitForSingleObject(ready_event,0) == 0:
            try:
                sampler = Native(args.pid)
                before = sampler.memory()
                _regions, allocations, failures = sampler.regions()
                after = sampler.memory()
                sampler.close()
                private_regions = sum(row['size'] for row in _regions if row['kind'] == 0x20000)
                page_tables = {21:set(),30:set(),39:set()}
                for row in _regions:
                    for bits, spans in page_tables.items():
                        spans.update(range(row['base']>>bits,((row['base']+row['size']-1)>>bits)+1))
                charged_tables={21:set(),30:set()}
                for row in allocations.values():
                    if row['commit']:
                        for bits,spans in charged_tables.items():
                            spans.update(range(row['base']>>bits,((row['base']+row['size']-1)>>bits)+1))
                payload = {'before':before,'after':after,'allocations':list(allocations.values()),
                           'virtualquery_private_commit':private_regions,'query_failures':failures,
                           'committed_page_table_span_counts':{bits:len(spans) for bits,spans in page_tables.items()},
                           'nonzero_commit_translation_pages':sum(len(spans) for spans in charged_tables.values())}
                output.with_suffix('.vm.json').write_text(json.dumps(payload),encoding='utf-8')
            finally:
                kernel.SetEvent(event)
            sampled = True
        if output.exists() or output.with_suffix('.error.txt').exists():
            script.unlink()
            kernel.CloseHandle(event)
            kernel.CloseHandle(ready_event)
            if output.with_suffix('.error.txt').exists():
                raise RuntimeError(output.with_suffix('.error.txt').read_text(encoding='utf-8'))
            print("Diagnostic callback completed", flush=True)
            return
        time.sleep(.01)
    kernel.SetEvent(event)
    kernel.CloseHandle(event)
    kernel.CloseHandle(ready_event)
    print("Callback still pending; keep the script until it executes: "+str(script), flush=True)


if __name__ == "__main__":
    main()
