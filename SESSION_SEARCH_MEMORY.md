# On-demand session search

Renderer session management uses `OnDemandSessionSearchIndex`. The original
SQLite document table remains the durable extracted corpus. The disposable
`.sqlite.query` sidecar stores the existing CJK bigram / ASCII trigram document
bitmaps and tiny token-stream offsets. `.sqlite.streams` holds the unique token
streams as UTF-8 buffers and path data and is mapped read-only on demand.
Extraction, field weights, substring verification, phrase hints, native-find
fallback and result ordering share the existing implementation.

Startup/background maintenance loads only session IDs, bitmap slots and document
stamps plus small offset descriptors. Normal startup does not unpickle `.memory`
or materialize a corpus-wide token set. An existing installation converts a
compatible `.memory` snapshot once to preserve existing find-bar hints; without
one, it streams source documents. Conversion accumulates bitmap masks and commits
them together, avoiding hundreds of slow transactions. Subsequent starts only
reconcile changed stamps. Migration follows the existing background readiness flow.

Queries fetch just the requested gram postings and candidate token buffers.
Candidate buffers use a 16 MiB LRU budget (including per-document overhead),
with oversized candidates read through transient mapped views. Cache hits use
compact byte buffers and share the existing ranker; they do not need Python
objects for every token. The gram cache holds at most 4096
entries and SQLite's connection page cache is limited to 1 MiB. Original
rollout extraction retains its existing separate 8 MiB bound. Preview evidence
retains the existing 512-entry bound and is invalidated on document updates.

Sixty seconds after the last explicit search/preview, a one-shot timer releases query
buffers, posting cache, extraction state, previews and the SQLite connection.
It does not delete the index or change coverage. Reusing it reads only needed
postings/candidates, rather than reloading the corpus. The index lock prevents
release during a query/update and timer generations reject obsolete expiry
callbacks. Background refreshes neither extend the expiry nor retain query
buffers after expiry. Worker shutdown and index clearing also release handles.

Each sidecar document and its posting changes commit atomically. A crash after
the authoritative document commit is repaired using `indexed_at` on restart.
Stream truncation and a corrupt SQLite sidecar are rebuilt from the durable
documents. Replaced token streams are compacted during maintenance when garbage
exceeds the live data and the file reaches 32 MiB; generation headers detect
interrupted compaction. Clearing includes only explicitly owned `.query`, WAL,
SHM, journal, `.streams` and compaction temporary artifacts alongside the previous
owned artifacts; session rollouts are retained.

Run focused checks with `python -m pytest tests/test_session_search_disk.py
tests/test_session_search.py tests/test_session_cleanup.py
tests/test_session_cleanup_runtime.py`. For corpus parity and timing, use
`python tools/benchmark_session_search_memory.py PATH/TO/session-search.sqlite`.
The benchmark uses a read-only SQLite backup and isolated child processes,
cleans up its temporary files, compares complete search-result hashes, and
reports retained Python allocations separately from untraced query timings,
mapped file size, and Windows process private/working-set measurements. A file
mapping's virtual size is not its resident size; Python allocation figures do
not include SQLite's native cache or OS file pages. It does not flush OS disk
caches or reload the running HUD.

## Local verification, 2026-11-11

Windows / Python 3.14, read-only backup of the current 269-session index.
Twenty-four mixed Chinese, English, path, phrase, short-term and missing-term
queries produced identical complete result objects, including ordering, scores,
match kinds and native-find hints. Five warm passes ran without allocation
tracing. Representative final run:

| Measurement | Resident index | On-demand index |
| --- | ---: | ---: |
| Ready Python allocations | 125.64 MiB | 0.11 MiB |
| After querying, Python allocations | 125.65 MiB | 11.32 MiB |
| After release, Python allocations | 125.65 MiB | 0.12 MiB |
| Warm query median / P95 | 14.98 / 29.38 ms | 5.10 / 8.04 ms |
| Application-cache-cold median / P95 | 15.83 / 27.65 ms | 13.86 / 17.30 ms |
| Application-cache-cold maximum | 32.30 ms | 19.65 ms |
| Isolated process private memory, active | 173.40 MiB | 41.36 MiB |
| Isolated process private memory, released | 173.40 MiB | 31.73 MiB |

The on-demand mapped file was 10.54 MiB, separately from Python allocations;
its mapping is released at expiry. First sidecar conversion took 1.47 s. The
untraced fresh-instance load of an already converted sidecar took 76 ms in this
run (resident snapshot: 2.62 s). These are local measurements with OS disk cache
available, not a remote platform matrix or a guarantee for every corpus/device.
Repeated measurements showed application-cache-cold medians varying by a few
milliseconds in either direction relative to the already-resident baseline;
warm latency and P95 improved while the corpus stopped living in RAM. Full
timings and allocation/process counters are in `SESSION_SEARCH_MEMORY.json`.

The focused search/extraction/manager/runtime/warm-job tests cover incremental
changes, pruning and slot reuse, crash reconciliation, corruption/truncation,
oversized candidates, deterministic expiry, concurrent release, clearing and
compaction: 227 tests plus 19 unittest subtests passed; 5 focused index-command
tests also passed. Compilation, Ruff, diff whitespace and renderer contract
checks passed. The running HUD was not reloaded during verification.

## Restarted HUD acceptance, 2026-11-11

Verified the actual renderer input/Enter path against running HUD PID 11580.
The persisted document table and query sidecar both held 272 sessions; the
inventory contained 878 sessions, with the configured month coverage complete.
English (`renderer`: 49), Chinese (`扫描`: 177), and filename
(`session_search.py`: 23) queries returned the same counts before and after
idle expiry. A newly generated absent term returned zero results. Clearing
the query restored the inventory list. Renderer runtime errors and ERROR-level
HUD logs since restart were empty.

Read-only Windows process mapping enumeration confirmed the `.streams` and
SQLite SHM mappings appeared during use and disappeared after inactivity.
They remained absent in subsequent samples despite continuing ordinary HUD
activity. Reopening/searching after expiry restored results. This verifies
release in the live process, rather than inferring it from disk files or tests.
Whole-process memory fluctuated with unrelated activity/caches and does not
isolate the candidate cache; no precise live index heap-size claim is made.

Observed UI round trips were 1.26–1.56 seconds for first `renderer` searches
after opening, and 0.23–0.88 seconds for the subsequent Chinese/filename
queries. These include queueing, CDP transport, payload publication and page
rendering. They are separate from the core-index benchmark above; without a
pre-change live UI baseline, this acceptance does not prove an end-to-end UI
speed improvement. No missing results, search errors or stuck loading occurred.

Evidence: `SESSION_SEARCH_LIVE_ACCEPTANCE.json`; screenshot:
`output/playwright/search-memory-live.png`. The temporary verification scripts
were removed, the search query was cleared, and the settings dialog was closed.

## Compressed-path repair, 2026-11-11

The live acceptance above did not establish corpus completeness. Investigation
of `Linux.do & IDCFlare 回帖足迹` found four October 27–28 conversations whose
source files existed as `.jsonl.zst` while Codex's state database still named
the missing `.jsonl`. The index contained empty extracted bodies with negative
file fingerprints, so old/new query parity alone could not detect the omission.

The streaming reader, search candidate paths, fingerprints and range timestamps
now resolve the exact same-directory compression sibling when the recorded
file is absent. Existing recorded files take precedence. There is no directory
search or recorder-ID guessing, and permanent-delete mapping checks are not
relaxed. Compressed file events trigger the existing event-driven updater.

The ordinary startup warm job detects changed fingerprints and rebuilds affected
documents incrementally, including stale documents already covered but now older
than the selected range. New older sessions are not added and the selected range
is preserved. No extraction-version bump or whole-index reset is required.

A read-only backup of the current 260-document index reproduced 190 affected
documents (157 within 30 days, 33 older but already indexed). Repairing just these
190 took 40.76 seconds locally; stale count became zero and all four expected
LinuxDo conversations matched the full keyword, before/after cache release.
See `SESSION_SEARCH_COMPRESSION_REPAIR.json`. This verified the repaired corpus
on a copy; the running HUD needs a user-chosen restart to load the fix and perform
the same incremental repair on its live index. No automatic reload was performed.

Validation: 218 focused rollout/search/manager/runtime/warm-job tests and 19
unittest subtests passed, plus the existing compressed parser regression. Syntax,
Ruff, diff whitespace and the renderer contract check passed.
