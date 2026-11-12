# Cache and helper memory investigation, 2026-11-11

The previous live measurement reported 523 MiB private commit across four HUD
processes (456 MiB private working set). The older report was 450 / 407 MiB.
Those different live workloads did not prove an overall reduction. This change
uses fixed source/corpus copies and separate processes to assess specific costs.

## Findings

The selected-session snapshot cache retains complete decoded records plus UI
snapshots, which remain needed for historical activity/requests. UsageSummaryCache
independently retained full decoded JSON records, including tool output, prompts
and replies that its billing/event aggregation never reads. The live object
probe measured approximately 44 MiB in selected-session snapshots and 23 MiB
in usage cache. These were distinct object graphs, not merely two references
to the same data.

The desktop helper started through the complete renderer composition root.
Even after separating that entry, its vehicle-art leaf import initialized
renderer_assets, eagerly assembling the full JavaScript/CSS manifest. The
previous live probe measured about 18 MiB of large renderer string constants
in *each* process. The helper needs only the shared vehicle art.

A read-only Windows HeapSummary/GetProcessHeaps probe of the still-running
main process found 110.35 MiB allocated in Windows process heaps versus
187.98 MiB committed there: a 77.63 MiB gap. This is allocator capacity and
overhead, not another 77.63 MiB of identified live business objects. Python
arenas and direct VirtualAlloc regions are additional/overlapping layers;
these counters must not be added to the Python object census. No forced GC,
heap compaction, working-set trimming, cache eviction or reload was performed.
The evidence is in output/hud-native-heap-2026-11-11.json.

## Changes

- The aggregate reader retains only token counts, model/provider/identity
  metadata, timestamps and structural record positions. Legacy subagent parent
  tags and fork-replay boundaries remain available. The full UI reader uses
  separate states and preserves prompts, outputs and historical display data.
- Incremental binary reading streams complete lines rather than holding an
  entire appended file, a complete-prefix copy and all split-line copies
  together. Partial trailing records stay unread until the next append.
- Module, installed-console and frozen executable entries dispatch desktop and
  loading helpers before the renderer coordinator imports. Shared vehicle art
  can be imported without assembling the renderer manifest. Widget behavior,
  settings, command contracts and rendering assets remain unchanged.

## Controlled comparison

Baseline: unchanged src snapshot from commit 5c7502c. Three fixed copies of real
rollouts, 13,965,706 source bytes total. Both child processes use the same logical
clock to compare refreshed/elapsed UI fields. Usage totals, every per-file
contribution and full selected-session snapshot hashes match.

| Measurement | Before | After |
| --- | ---: | ---: |
| Usage tail object graph | 25.88 MiB | 3.00 MiB |
| Snapshot + usage child private commit | 114.98 MiB | 89.38 MiB |
| Largest reader transient Python allocation | 11.21 MiB | 0.14 MiB |
| Helper class/import child private commit | 64.65 MiB | 40.48 MiB |
| Helper renderer manifest loaded | yes | no |

These isolate roughly 25.6 MiB less cache-process private commit and 24.2 MiB
less helper import private commit on this corpus. They do not establish an exact
50 MiB reduction of the full live HUD: workload, native allocation reuse and
actual Qt window/font/rendering caches differ. No live code reload was done.
The user can choose a restart, after which the whole HUD must be sampled again
under a comparable workload. In particular the previous total must not simply
be reduced by these isolated deltas and presented as a new measurement.

The helper benchmark imports the production Qt widget classes without opening
windows or running their event loop. Benchmark input copies and baseline sources
are temporary; evidence remains in output/hud-cache-memory-comparison.json.
The standalone tool tools/benchmark_hud_cache_memory.py accepts --baseline-src
and --corpus to repeat the comparison. Allocation tracing is restricted to the
reader-peak phase and is not used for elapsed usage-query timing.

Validation covers aggregate replay/pricing/metadata parity, incremental append,
file truncation/replacement, invalid/partial lines, full-state restoration, helper
dispatch/import isolation, CLI/build bootstrap, renderer assets and architecture.
176 focused tests plus 7 unittest subtests passed. Source compilation, Ruff,
renderer contract update/check and diff whitespace checks passed. Full packaging,
platform-matrix and live post-restart acceptance were not run.

## Second round: history text and renderer copies

The next fixed-corpus probe found the same long clipboard/locate text retained
separately by session-wide and per-task RequestRound histories. Request objects
must stay independent because round numbering and mutable status can differ.
The parser now shares only equal immutable strings of at least 256 characters,
using a parse-local pool seeded from the prior snapshot on incremental refresh.
The pool itself is discarded after parsing and never pins an evicted session.
Full text, history rows, activity lookup strings, tokens and costs are preserved.

Renderer assembly also retained an eagerly generated default no-catalog script,
even though production installs a model-catalog-specific script. Compatibility
exports now calculate the same string only on access; ordinary client/payload
imports do not retain that second complete copy. Assembly-only settings prefix
and suffix constants are released after constructing the public asset. The
canonical template, public generated script and renderer contract hashes stay
identical. There are no additional background timers or polling tasks.

Comparison baseline for this round was the previous round's source snapshot,
not commit 5c7502c. It used the same three fixed files and fixed logical clock:

| Measurement | Before this round | After this round |
| --- | ---: | ---: |
| Full selected-session snapshot objects | 27.07 MiB | 14.58 MiB |
| Snapshot + raw-state + usage object graph | 55.40 MiB | 42.92 MiB |
| Cache child process private commit | 90.21 MiB | 77.93 MiB |
| Renderer large module strings | 18.07 MiB | 10.84 MiB |
| Renderer-import child private commit | 47.91 MiB | 40.20 MiB |
| Three-file cold snapshot parse | 391.52 ms | 377.66 ms |
| Reparse median, five passes | 326.94 ms | 333.86 ms |

The reparse difference is about 2% in this local run; timing varies with load.
It must not be described as a proven speed improvement. The comparison produced
identical hashes for full snapshots, usage contributions and generated renderer
script. The cache and renderer figures come from separate child processes and
cannot simply be summed into a claimed live-HUD total. Whole-process acceptance
still needs a user-chosen restart. Evidence:
output/hud-memory-round2-comparison.json.

180 focused parser/cache/CLI/helper/renderer/architecture checks plus 7 subtests,
and 9 focused renderer script/model-catalog tests passed. Compilation, Ruff,
renderer contract update/check and diff whitespace checks passed. These are
focused checks, not full packaging, live reload or cross-platform acceptance.

## Restarted live acceptance, 2026-11-12

The user restarted the HUD. Main PID 14104, desktop helper PID 20052, legacy
relay PID 18840 and gateway PID 3824 were verified by their command lines and
start times. Both optimization rounds are loaded: no default script copies
are pinned, no settings assembly prefix remains, and the helper imports neither
the renderer manifest nor the runtime coordinator. All four retained usage
tails use the billing projection. Four full UI snapshot states remain available;
854 long clipboard occurrences use 427 distinct string values and exactly
427 string objects, confirming text sharing without truncating history.

Four-process samples during acceptance:

| Measurement | 2026-11-11 previous observation | 2026-11-12 acceptance |
| --- | ---: | ---: |
| Private commit | 523.2 MiB | 245.8–265.7 MiB |
| Private resident working set | 456.4 MiB | 229.1–249.7 MiB |
| Main process private commit | 357.3 MiB | 124.3–143.5 MiB |
| Desktop helper private commit | 99.8 MiB | 64.0–68.4 MiB |

The new live totals are lower, but session workloads, uptime, native allocation
reuse and gateway traffic differ from the previous day. These observations
must not replace the controlled fixed-corpus comparison or be described as a
guaranteed percentage reduction for every workload. Embedded HUD objects in
the Codex renderer remain outside the four-process total.

Live input/Enter searches returned 5 matches for the LinuxDo/IDCFlare keyword,
70 for session_search.py and 260 for 扫描. The four previously missing LinuxDo
documents have extracted content and valid actual-file fingerprints. A new
absent term returned zero and clearing restored all 882 inventory sessions.
The month coverage is complete. The helper heartbeat/state was current and no
runtime or ERROR-level HUD failures were found. After more than 60 seconds
without a search, the live index reported zero candidate bytes, no mapping
and no open query connection; coverage remained ready.

Repeated filename searches completed in about 275 ms. One initial filename
search took 5.87 s and a renderer search took 2.61 s end to end; this includes
queueing, publication and renderer work and did not persist on repeats. Thus
acceptance establishes current memory behavior and search availability, not
an end-to-end speed improvement or a guaranteed latency ceiling.

189 focused tests plus 7 unittest subtests passed on the accepted source;
compilation, Ruff, renderer contract update/check and diff checks passed.
No automatic restart or forceful memory trimming occurred during acceptance.
Search input was cleared, settings closed, temporary probes removed. Compact
evidence is retained in HUD_MEMORY_LIVE_ACCEPTANCE.json.
