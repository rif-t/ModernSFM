# ModernSFM Development Roadmap

## Status and sequencing

The three-component architecture in [ARCHITECTURE.md](ARCHITECTURE.md) remains fixed: SFM Python x86 → Bridge DLL x86 → separate Worker x64. The standalone PFM/EXR Worker milestone is complete. A minimal Named Pipe command round trip through an x86 Bridge and x64 Worker has also passed inside SFM's embedded Python 2.7. PFM readiness, SFM render integration, and batch processing remain open; see [Phase 0 Evidence](PHASE0_EVIDENCE.md).

## Phase 0 — Evidence and architecture validation

**Goal:** resolve the data and integration assumptions that block implementation.

**Status:** partial. One 3840×2160 real beauty/depth pair has been parsed and round-tripped with top-to-bottom row orientation confirmed by the user. Beauty contains values above 1; depth contains large finite values that may be sentinels. Valve documents Source HDR processing and the existence/purpose of SFM's depth-PFM option, but public information found does not define the SFM PFM export transforms or depth equation. See [Phase 0 Evidence](PHASE0_EVIDENCE.md).

- Expand the one collected SFM beauty/depth pair to representative scenes and settings.
- Verify which PFM header/payload, channel, scale/endian, and row-orientation properties are stable across frames.
- Establish beauty color characteristics and whether scene-linear interpretation is justified.
- Establish depth encoding, range/units, background value, and alignment with beauty using the calibration tests in [Phase 0 Evidence](PHASE0_EVIDENCE.md).
- Investigate whether Python can reliably determine per-frame Beauty/Depth readiness and normal batch-end/cancel/failure without a special SFM hook.
- Treat the installed `modern_sfm_frontend.py` as a user-confirmed non-working legacy prototype. Record useful behavior and reported failures, but cleanly rewrite render orchestration in this repository after its boundary is clear; do not port the mixed viewport implementation or edit the installed copy.
- Test x86 native binding options inside this SFM installation's embedded Python 2.7; evaluate `ctypes` first and retain a CPython extension as alternative.
- Approve initial EXR channels, precision, compression, metadata, and data/display windows.
- Specify Bridge↔Worker IPC, API, timeouts, cancellation, and failure semantics.

**Deferred by user:** Beauty color-space and depth-value calibration may remain open for the initial opaque PFM-to-EXR path. Preserve sample values unchanged and do not add semantic transforms or metadata. This deferral does not waive the need to establish that each input pair is complete and readable before processing.

**Investigation update (2026-10-30):** The installed bundled Python executable reports CPython 2.7.5, 32-bit, imports `ctypes`, and can call a basic Win32 API. This is only a bundled-runtime baseline, not an in-SFM Bridge load test. Inspection of the non-working legacy frontend confirms it polls a Qt layoff progress dialog and/or expected output counts and writes a sentinel; neither has been verified against cancellation, failure, or file flushing. No reliable per-frame readiness or batch-end contract has been established. The proposed model is per-frame `frame_ready` notifications from Python, with no special hook assumed, and an explicit end-of-submissions outcome. See [Architecture](ARCHITECTURE.md) for the proposed protocol and required investigation.

**Later integration check:** the x86 Bridge and x64 Worker have since completed a PING/ECHO/QUIT round trip when launched by SFM's own Python 2.7 startup-script facility. This verifies the native process boundary, not frame readiness or render completion.

**Gate:** render and PFM processing may use the agreed opaque-value path while color/depth semantics remain explicitly unknown; per-frame readiness must still be verified before streaming.

## Phase 1 — Standalone data-format proof

**Goal:** verify file-format handling without coupling it to SFM.

**Status:** synthetic-data subset implemented and verified; one real SFM frame pair has also been processed. The x64 standalone Worker reads paired RGB (`PF`) beauty and single-channel (`Pf`) depth PFM inputs, preserves the top-to-bottom row order observed in that SFM output, writes FLOAT `raw-sfm`, `depth`, and `image-final` EXR channels, reopens the file, and checks dimensions, channel types, and pixel values. `image-final` is an identity copy of `raw-sfm`. Synthetic smoke tests cover valid inputs, row order, truncated payload rejection, dimension mismatch, non-finite samples, memory-budget acceptance and rejection, output collision, and PFM retention. Color and depth semantics remain unresolved.

- Define synthetic PFM fixtures including valid RGB/grayscale and malformed headers/payloads.
- Specify strict parser behavior for non-finite values, endian/scale, orientation, truncation, dimensions, and overflow.
- Write a multilayer EXR from synthetic known values using the proposed schema and FLOAT channels.
- Reopen and validate dimensions, channels, layer organization, and values; test `raw-sfm` preservation.

**Gate:** passed for the accepted synthetic-data subset. No source PFMs are deleted in this phase.

## Phase 2 — Single-frame x86 Bridge to x64 Worker

**Goal:** validate process boundaries and IPC independently of SFM rendering.

**Status:** minimal command/lifecycle slice implemented. `ctypes.CDLL` in live SFM Python 2.7 loaded the 32-bit Bridge, launched the 64-bit Worker, completed `PING` and `ECHO`, and shut the Worker down cleanly. The same smoke script passed in the bundled x86 Python 2.7.5 executable. Existing PFM Worker tests still pass. Full IPC failure, timeout, cancellation, and protocol negotiation coverage remain outstanding.

- Finish the implemented minimal IPC slice with automated failure and timeout coverage before expanding the protocol.
- Automate initialization, repeated start, malformed/oversized requests, insufficient response buffers, Worker exit, protocol mismatch, timeouts, and shutdown error cases.
- Keep one synchronous request at a time and control-message-only IPC; the Worker reads files directly when file jobs are added.
- Use a one-frame batch to validate the proposed `begin_batch` → `frame_ready` → frame status → end-of-submissions contract, including missing/invalid input handling.
- Add protocol version negotiation and persistent per-component diagnostics.

**Gate:** all protocol and failure scenarios return bounded structured outcomes; the Worker is never loaded into the x86 process and no full image buffers cross IPC.

## Phase 3 — SFM single-frame integration

**Goal:** complete one SFM render-to-EXR operation through embedded Python and the Bridge.

**Status:** Bridge loading and Worker round trip are verified inside SFM. No SFM render or PFM-to-EXR job has yet been triggered through this integration.

- Reuse the live-verified x86 Bridge binding from SFM's actual Python environment.
- Have Python submit a frame only after its readiness condition is met; the condition must be experimentally verified, and a special SFM hook is not assumed.
- Process a real beauty/depth pair and compare source and EXR values.
- Publish through a temporary output followed by reopen/validation and atomic/failure-safe final publication where supported.
- Add configurable source retention; delete inputs only after validated publication.

**Gate:** repeatable end-to-end success and failure tests show no partial EXR published as final, no premature input deletion, and no indefinite SFM blocking.

## Phase 4 — Sequential frame-range processing

**Goal:** process animation outputs reliably before introducing concurrency.

- Implement the proposed inclusive frame range and naming patterns; define duplicate submission, output ownership, retries, resume behavior, and cancellation.
- Track `accepted` only after input validation, then `started`, `completed`, `failed`, and `cancelled` frame states in the Worker.
- Require an explicit close-of-submissions outcome; mark unsubmitted expected frames missing/failed on normal/error close and cancelled on user cancel. Report `completed`, `completed_with_errors`, or `cancelled` only after expected frames are terminal.
- Process one frame at a time to bound memory.
- Report failed frames and validate every output before marking completion.
- Stream frames from SFM only after the Python-side per-frame readiness condition is reliable; do not rely on file existence alone.

**Gate:** an interrupted batch resumes without corrupting completed frames or deleting unprocessed inputs; frame ordering and naming are deterministic.

## Phase 5 — CPU and memory optimization

**Goal:** improve throughput against a correctness baseline.

- Measure elapsed time and peak working set for representative resolutions.
- Add configurable memory budget and graceful allocation-failure behavior.
- Profile thread scaling on the target i5-12400 system.
- Add runtime AVX2 detection with scalar fallback only where measured beneficial.
- Profile disk throughput and reduce copies/buffer churn based on measurement.
- Consider tiling only if measured memory pressure warrants it.

**Gate:** optimized runs remain within documented numerical tolerances, respect memory limits, and demonstrate reproducible gains over the baseline.

## Phase 6 — Parallel post-processing (future proposal)

**Goal:** process independent frames with a bounded Worker pool.

- Design a Worker Manager or Coordinator boundary; do not overload the Bridge with pool policy.
- Define durable job manifests, accepted-job acknowledgements, process ownership, queue persistence, retries, and crash recovery.
- Set coordinated limits for Worker count, per-Worker threads, memory, and disk throughput.
- Test output collisions and duplicate submissions.

**Gate:** measured throughput improves for representative workloads without exceeding configured resource budgets or weakening recovery guarantees.

## Phase 7 — Multi-instance SFM rendering (future proposal)

**Goal:** determine whether multiple SFM sessions can reliably render disjoint frame ranges.

- Review community references and inspect source where available; do not infer unpublished internals.
- First test multiple instances without modifying SFM binaries if possible; confirm whether the singleton restriction blocks that route.
- If considering a launcher patch, design a separate reversible local-only patch procedure with file hashing, backup, version compatibility, restore, and Steam update detection. No Valve files may be redistributed.
- Test same-project concurrent rendering, output isolation, temporary/shared state, ghost-frame boundaries, startup options, DXVK, and crash recovery.
- Measure actual speedup and CPU/RAM/VRAM cost; a higher instance count is not assumed faster.

**Gate:** stable outputs across repeated projects and a measured benefit on the target system. Any launcher patch requires separate explicit design approval before implementation.

## Phase 8 — Render Coordinator (future proposal)

**Goal:** centrally manage SFM instances, frame allocation, Workers, jobs, retries, resource limits, and aggregate progress.

- Evaluate an external x64 Coordinator as a separate component.
- Specify ownership of SFM processes, Worker pool, queue state, restart behavior, and logs.
- Keep it outside the Bridge DLL; define IPC/API boundaries before coding.

**Gate:** separate architecture review approves its responsibilities and failure model. This phase is not implied by completing the single-frame pipeline.

## Cross-phase verification strategy

- **PFM:** synthetic and real fixtures; parser edge cases; documented beauty/depth semantics.
- **EXR:** synthetic round trips, channel/layer/schema validation, original value preservation, depth checks, and AgX reference comparisons after input interpretation is verified.
- **IPC:** lifecycle, protocol version, malformed/oversized messages, timeouts, cancellation, disconnect, restart, and shutdown outside SFM.
- **Binding:** bitness, ABI, loading, path encoding, error propagation, and shutdown in SFM's actual embedded Python 2.7.
- **End to end:** single frame, then batch; no premature deletion; partial output not presented as final; recovery after interruption.
- **Performance:** baseline and optimized CPU runs, peak memory, thread scaling, SIMD benefit, Worker concurrency, and later SFM instance scaling.
- **Compatibility:** stock SFM first; test DXVK/ReShade configurations only after deterministic CPU processing works.

## Key unresolved risks

1. Python's per-frame PFM readiness condition and normal-end/cancel/failure detection have not been established; a special SFM hook is not assumed.
2. Only one real beauty/depth PFM pair has been inspected; beauty color/depth semantics remain unknown.
3. Bridge error recovery, Unicode paths, repeated load/unload, and behavior during an active SFM render remain untested.
4. The initial EXR schema and transform metadata remain proposals.
5. Multi-instance rendering may require patching `launcher.dll`; safety, version stability, and necessity are unverified.
6. Community Multi-Render reports ghost frames at frame-range boundaries and resource pressure; the underlying cause and project-specific frequency need independent testing.
7. Current Bridge ownership does not guarantee Worker independence if SFM or the Bridge exits.
8. Performance gains from multi-threading, multiple Workers, or multiple SFM instances are unknown until measured.
