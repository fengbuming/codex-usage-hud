# HUD hot-path optimization measurements

Measured on Windows / Python 3.14.3 on 2026-11-10. These are focused offline
comparisons against the checkout's original implementation, not measurements
of the running HUD after a restart. No active requests or production databases
were modified.

## Changes

- Background bubbles reuse bounded parsed state for unchanged rollouts, keyed
  by file identity, size, nanosecond timestamps, parser identity and pricing
  version. File replacement, truncation and racing writes invalidate reuse.
  Time-dependent status is still evaluated on every bubble projection.
- Bubble parsing skips historical navigation/copy data that is constructed
  separately when a conversation is selected. The cache retains the same
  current-task activity rows and text limits as the existing bubble payload.
- Search memberships use reusable bitmap slots; field grams use compact
  tuples and shared strings. Search content, verification and ranking rules
  are unchanged. Version 5 snapshots load directly; version 6 snapshots omit
  redundant postings and preserve changed-file paths.
- File-change scheduling runs one reconciliation at a time and coalesces
  subsequent paths. Superseded generations retain unfinished file changes.
  Search progress snapshots omit the unchanged session inventory.
- Bubble/ranking titles are fetched in batches while preserving their prior
  title precedence. No timer-based title cache delays renames.
- Renderer updates reuse acknowledged inventories and send sparse patches
  for changed ranking rows. Failed sends do not advance the baseline; page
  loss and reinstall require full hydration again. Animations are unchanged.

## Offline results

A consistent SQLite backup and the existing snapshot were copied to a
temporary directory. Both implementations loaded that same 265-document
corpus. Thirty Chinese/English, short, substring and multi-term queries
returned identical results, including ordering and result details.

| Measurement | Original | Optimized |
| --- | ---: | ---: |
| Resident search object graph (unique reachable Python objects) | 286.29 MiB | 108.88 MiB |
| Median query time across 30 queries | 8.03 ms | 6.72 ms |
| Largest query time in that sample | 19.24 ms | 18.17 ms |
| Native snapshot load | 0.50 s | 0.78 s |
| New snapshot size | approximately 22 MiB | 12.21 MiB |

The first conversion from the old snapshot took 1.18 s. Reconstructing compact
memberships adds a small cold-load cost in exchange for lower steady memory.
Object-graph sizes are not total process private bytes or working set.

Sixteen recent rollouts (61.03 MiB) were copied before comparison so appends
could not affect either side. All resulting bubble items matched at the same
observation time.

| Measurement | Original | Optimized |
| --- | ---: | ---: |
| First parse of 16 candidates | 2,068.58 ms | 931.02 ms |
| Next unchanged candidate pass | 2,068.58 ms representative full parse | 0.732 ms |
| Unchanged-pass rollout body reads | 61.03 MiB | 0 |
| Bounded bubble cache object graph | none | 0.49 MiB |

The unchanged pass made zero parser calls. This does not claim that changing
large rollouts are free: their bubble state still requires parsing, but only
changed candidates are parsed and unused historical UI work is skipped.
Filesystem inventory checks and real incoming log/IPC traffic remain.

Focused regression cases cover bitmap replacement/deletion/slot reuse, old
snapshot migration, file identity and pricing invalidation, serial event
coalescing, title renames, renderer failure/retry and page reset. A Node.js
execution of the actual renderer handler verifies list reorder, truncation
and empty-list reconstruction. A 100-row test with one changed row reduces
the serialized ranking envelope to less than 5% of its full size.

## Live acceptance

The running HUD was not restarted. Total process memory, CPU and physical
disk activity must be measured after the user chooses to restart, with the
same visible bubbles, sessions and workload. Preserve the independent proxy
and active requests. Compare idle and active windows separately; cold index
builds and opening large historical sessions are separate workloads.


## Live restart verification, 2026-11-10 19:39–19:42 (Asia/Shanghai)

The old main process (8024) exited on the development handoff. The reload
coordinator stopped on its identity check during shutdown; after independently
confirming the old process had exited and the PID file was removed, the same
source-launch mechanism started main PID 33940. Renderer readiness was
confirmed through its HTTP health receipt and the actual page. Bubble helper
PID is 29292. Proxy PIDs 13620/20476 and ports 57323/57324 remained unchanged;
the active gateway continued reporting active requests on the same generation.
Codex was not restarted.

Immediately before restart, a 15.27 s active-window sample of the main process
measured 851.6 MiB private bytes, 791.1 MiB working set, 102.5% of one logical
CPU core and 19.2 MiB/s process I/O reads.

After startup/index warm-up, the first 32.48 s sample measured main private
bytes 311.4–318.0 MiB, about 35.8% of one core and 14.2 MiB/s reads. A second
20.20 s sample measured 322.1 MiB private bytes, 340.1 MiB working set, 26.8%
of one core and 7.33 MiB/s reads. Its bubble helper measured 91.3 MiB private
bytes, 148.8 MiB working set and 17.9% of one core. Total private memory was
approximately 413 MiB (total working set approximately 489 MiB).

These are changing live workloads, not a controlled replay. Process I/O read
counters include cached file, socket and pipe activity; they are not physical
disk throughput. The main-process private-memory reduction is approximately
62%; sampled main CPU reduction 65–74%; read-counter reduction 26–62%.
The originally proposed active CPU/read targets are not yet fully achieved.

Read-only CDP closure inspection confirmed the actual reconstructed state,
not only the sparse wire payload: 873 inventory rows, totals agreeing with
that count, and today/week/month each containing 8 session rows and 10 usage
ranking rows with no missing entries. Ranking state was ready with no error
and no retained patch markers. Two bubble items continued to update. The
warm-index status completed 255/255 candidates with an empty error. Page
runtime errors were empty and observed CDP updates succeeded.

In logged 60-second windows, old/new median payload sizes were 661/500 KiB;
logged payload totals 16.10/3.55 MiB. Logging is sampled, so these are not full
transport byte counts. Median recorded refresh times were 757/672 ms. The
remaining busy stacks were mainly changed-rollout search extraction and
index rebuilding; background bubble history parsing was no longer dominant.
Large history/current-session payloads and animated helper rendering still
contribute. Live acceptance confirms substantial improvement, not completion
of every aspirational performance target.

## Follow-up: live search appends

The first round is committed as `72e5236`. The next round targets the search
extraction/indexing work found in the live stacks:

- Skip expensive UI-snapshot regexes when their required literal markers are
  absent, retaining the same cleaning rules when they are present.
- Reuse bounded extraction state for resident single-file, uncompressed
  rollouts. Only appended records are decoded/cleaned; compressed and multi-file
  families retain the full streaming reader. Cache limits are four entries and
  8 MiB, including retained strings and witness buffers. Oversized states are
  discarded. Valid final records without newlines still have the full reader's
  semantics, but their partial boundary is not cached.
- Before reusing state for a growing file, verify the entire previously
  consumed prefix with SHA-256. File identity, size, nanosecond timestamps and
  small witnesses reject obvious replacements/truncation first. Full prefix
  verification also detects an in-place middle edit followed by growth even
  when the head/tail witnesses are unchanged. This deliberately keeps some
  historical byte reads to preserve rewrite detection, while avoiding repeated
  JSON decoding, cleaning and tokenisation of those bytes.
- A small durable extraction-cursor table tracks file fingerprints and a
  content digest against the document's indexed stamp. Token counts, hidden
  analysis and other appends that do not change searchable text update this
  small row without reading/writing the large document text or rebuilding its
  resident postings. Real text changes retain full matching/ranking semantics.
- Batch fingerprints are observed before extraction, so bytes appended during
  indexing cannot be incorrectly marked as already indexed. The next event
  still sees the file as stale.

Comparison against `72e5236` used independent copies of one 4.29 MiB real
rollout, each with the normal covering stamp index and a warm resident index.
Four alternating token-count/body-message appends were synchronously indexed:

| Incremental update | Before | After |
| --- | ---: | ---: |
| Token-count-only append | 458–467 ms | 11–12 ms |
| Its process read counter | about 6.55 MiB | about 4.33 MiB |
| Assistant-body append | 470–479 ms | 123–126 ms |
| Its process read counter | about 8.79 MiB | about 8.82 MiB |

The retained extraction state in this sample was 5.39 MiB. Six representative
queries returned identical results. A separate comparison of all extracted
fields for 16 real rollouts (71.0 MiB) also matched exactly; cold extraction
decreased from 3,177 ms to 1,408 ms. The original live databases were untouched.
These are hot-path/offline measurements, not new whole-process CPU claims.
The final implementation retains full-prefix verification: the earlier
witness-only prototype's roughly 40 KiB metadata read figure is not the shipped
behavior. Metadata read volume improves by about one third; body-update read
volume remains essentially unchanged in exchange for complete rewrite checks.

Focused regressions additionally cover partial UTF-8/CRLF/final records,
replacement/truncation, cache limits and eviction, document ceilings, cursor
persistence/pruning, indexing races and optional legacy FTS repair. SQLite
authorizer tests reject document-blob reads or updates during metadata-only
sync, proving this path uses the small cursor instead.

This follow-up has not been loaded into the running HUD automatically.

## User restart acceptance, 2026-11-11

Main PID 18536 and bubble helper 23012 were running the second round. The live
cursor table existed with valid document stamps and caught up to a captured
rollout version within 0.5 s. Inventory held 874 rows matching its totals;
search/rankings were ready without missing rows or retained patch markers.
Page runtime errors were empty and bubble revisions/heartbeat kept advancing.

A 38.47 s main sample measured 287.2–288.9 MiB private bytes, 12.83% of one
logical core and 5.159 MiB/s process reads. A 25.21 s sample measured 294.1 MiB,
19.09% of one core and 4.145 MiB/s reads. Multiple no-change intervals had
0–0.98% main CPU and zero reads. The helper used 95.1–95.5 MiB private bytes
and 18.39–24.36% of one core. Combined private memory was approximately 390 MiB.

Different live workloads prevent a controlled percentage comparison with the
prior day. Process reads include cached file, socket and pipe I/O, not only
physical disk throughput. Main CPU improved relative to the first round's
27–36% samples, while helper animation rendering remained a separate hotspot.
No restart or source modification was performed during that acceptance.

## Follow-up: completed-bubble painting, 2026-11-11

Live stack sampling of helper PID 23012 still found shimmer, completed badge,
liquid and tide painting. A focused offscreen paint profile identified repeated
completed-badge foreground work (arc title layout, numbers, stat boxes and theme
palette generation) on every liquid frame. This is maintenance of the existing
helper, without introducing new Qt product behavior or changing Renderer mode.

The completed badge now records its static foreground as vector commands.
Settled frames cache one transparent premultiplied image at the actual device
pixel density and fractional physical-pixel offset. Item/theme/DPI changes
invalidate it; transformed morph/fade/launch frames replay vectors, and unusual
logical-DPI devices retain the original direct painting. The cache keeps just
one recording and one raster per widget. Theme palettes are computed on theme
changes. Liquid sample coordinates and frame-local color mixes are reused;
tide calculations hoist constants out of the sample loop. All existing timers,
frame rates, wave samples, phases, animation paths and click behavior remain.

A three-repeat offscreen comparison (250 frames/repeat, median CPU time, real
Windows fonts loaded) against `6eff4d5` measured:

| Device pixel ratio | Original ms/frame | Optimized ms/frame | Raster cache |
| --- | ---: | ---: | ---: |
| 1.0 | 2.688 | 1.250 | 118.1 KiB |
| 1.5 | 6.062 | 2.062 | 265.8 KiB |
| 2.0 | 7.125 | 3.188 | 472.5 KiB |

This is a 53–66% reduction for the measured completed-badge painting workload,
not a claim that total helper CPU falls by the same amount. Shimmer and other
visible widget costs remain. Full before/after settled frames differed by at
most 1–2/255 per color channel (mean differences 0.004–0.007/255), due to the
extra source-over composition rounding. Tide frames at heat 0.03, 0.5 and 1.0
were pixel-identical. No animation frame skipping or reduced sample count was
used.

64 focused tests passed, including direct-vs-cached pixels at 100%, 125%, 150%
and 200% pixel ratios, fractional widget positions, light/dark themes, morphs,
click launches, missing/low/high speeds, logical-DPI fallback, invalidation,
bounded cache size, hide/show timers and existing overlay rendering regressions.
One existing legacy UI case was skipped. Compilation and diff checks passed.
No live HUD reload was performed for this follow-up; a restart is required to
measure the actual helper process improvement. No macOS live acceptance is
claimed.
