# ModernSFM Development Roadmap

## Status and sequencing

The three-component architecture in [ARCHITECTURE.md](ARCHITECTURE.md) remains fixed: SFM Python x86 → Bridge DLL x86 → separate Worker x64. The standalone Worker and x86 Bridge prototype are implemented. A panel-launched three-frame render was verified. The panel now includes a strict lifecycle state machine, durable job journal, asynchronous Worker processing, Worker-stage cancellation, and deferred detection of an SFM process exit. Native exporter cancellation and direct exporter failure signaling remain unverified; broad resolution support also remains open.

## Phase 0 — Evidence and architecture validation

**Goal:** resolve the data and integration assumptions that block implementation.

**Status:** partial. Real PFM parsing and row orientation are verified for the supplied frame. The live export proof also confirms three matching pairs from one session at 1280×720 can be processed. Beauty color interpretation and depth meaning remain unverified; public information found does not define the SFM PFM export transforms or depth equation. A 640×360 test produced 1280×720 depth, so other resolution behavior is unresolved. See [Phase 0 Evidence](PHASE0_EVIDENCE.md) and [Architecture](ARCHITECTURE.md).

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

**Investigation update (2026-10-30):** SFM embedded Python 2.7.5, the x86 Bridge, and x64 Worker passed a live three-frame render-to-EXR proof using the native Sequence Exporter and a disposable session copy. The dialog's Qt controls accepted/read back the tested PFM, depth, frame range, and quality settings; 0..2 inclusive required exporter end field 3 in this run. Repeated stable size/mtime checks and Worker PFM validation produced three validated EXRs while retaining source PFMs. This is a tested POC heuristic, not a production completion contract. Progress UI was not observed; cancellation and native failure behavior remain unverified. The 640×360 attempt had depth/beauty mismatch and was rejected safely. See [Architecture](ARCHITECTURE.md) and the POC log in `build/sfm_sequence_export_poc.txt`.

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

**Status:** single-pair `PACK` request implemented over the x86 Bridge to x64 Worker pipe. Both bundled CPython 2.7.5 x86 and live SFM Python 2.7 passed `PING`, `ECHO`, processing of the existing 3840x2160 beauty/depth pair into a validated FLOAT multilayer EXR, and clean shutdown. Worker reads files directly; source PFMs were retained. Full IPC failure, timeout, cancellation, and protocol negotiation coverage remain outstanding.

- Add automated malformed `PACK` payload, processing failure, and timeout coverage; the current protocol has defensive parsing but no complete automated Bridge-level failure suite.
- Automate initialization, repeated start, malformed/oversized requests, insufficient response buffers, Worker exit, protocol mismatch, timeouts, and shutdown error cases.
- Keep one synchronous request at a time and control-message-only IPC; the Worker reads files directly when file jobs are added.
- Use a one-frame batch to validate the proposed `begin_batch` → `frame_ready` → frame status → end-of-submissions contract, including missing/invalid input handling.
- Add protocol version negotiation and persistent per-component diagnostics.

**Gate:** all protocol and failure scenarios return bounded structured outcomes; the Worker is never loaded into the x86 process and no full image buffers cross IPC.

## Phase 3 — SFM single-frame integration

**Goal:** demonstrate a short SFM render-to-EXR operation through embedded Python and the Bridge, then harden orchestration for real jobs.

**Status:** a panel-launched three-frame end-to-end run passed previously. This reliability pass adds the lifecycle state machine and per-job journal, explicit stage diagnostics, incomplete-PFM-set failure reporting, a Qt thread for PACK calls, safe between-frame Worker cancellation, and stale-job recovery after confirmed SFM process exit. Updated scripts pass the bundled Python 2.7 compile check, lifecycle tests pass, Worker thread failure/cancel tests pass, actual Bridge/Worker cancellation passes on a real 3840×2160 input, and Bridge smoke tests cover missing input and Worker termination. A new panel-launched render has not yet been repeated after these monitoring changes.

- Reuse the live-verified x86 Bridge binding from SFM's actual Python environment.
- Replace the POC's dialog/count/stability heuristics with a reliable render completion and cancellation/failure model, or document the exact practical limitation if SFM exposes no usable signal. Do not add a hook without evidence it is required.
- Extend the real-pair smoke test to verify input and EXR pixel values from the Bridge path, beyond the Worker’s existing reopen-and-compare validation.
- Publish through a temporary output followed by reopen/validation and atomic/failure-safe final publication where supported.
- Keep source retention as the safe default; delete inputs only after validated publication and explicit user policy.

**Current implementation:** `scripts/sfm_sequence_export_poc.py` is the experimentally verified native export path. `scripts/sfm_render_job_panel.py` owns session selection, inclusive frame range, output path, separate render/Worker settings, confirmation, lifecycle status, and a per-job journal/log. It limits jobs to three frames at 1280×720, uses a disposable session copy, and retains source PFMs and partial EXRs. SFM's initial New Session wizard must load a document before Start. Native completion remains based on stable expected files; incomplete sets that stop changing fail with a missing-file diagnostic, and a five-minute watchdog reports timeout. Rendering cancellation is unavailable because no live-verified native Cancel control was found. Processing cancellation is tested: current PACK finishes, later frames are not submitted, and partial output is retained. SFM process termination is recorded as a failure on the next panel startup when the PID is confirmed dead. Exact render progress and direct native exporter failure signaling remain unresolved.

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

## Render Job panel status (2026-10-30)

On 2026-10-30, the user started a real job from the panel using `session5.dmx`, inclusive frames 0–2, and the verified 1280x720 preset. The panel opened a disposable session copy, configured the native Sequence Exporter with an exclusive end frame of 3, and exported three Beauty/Depth PFM pairs. Embedded Python checked matching 1280x720 headers; each pair was processed through the existing x86 Bridge and x64 Worker; all three EXRs passed Worker validation. The panel displayed `Completed: 3 EXR frame(s) validated`, and retained all six source PFMs.

**Session safety and crash fix:** an earlier test crashed SFM when opening the disposable copy directly from the external job folder. The panel now stores an archival copy in the job folder and opens a second per-job copy from SFM's own `elements\\sessions` directory. The successful run verified this location. The original `session5.dmx` and the job's `session_copy.dmx` had matching SHA-256 `59B94147C00A39972DF796C14DC57EDDBB6CC98422B56EFCF6C644ABB1ECBC98`; the original was unchanged. Untracked sample PFM files were not modified.

**Completion and failure behavior:** the successful run completed after the exact requested pair count appeared, PFM sizes and timestamps stabilized, the progress-window check found no recognized render window, and all Worker PACK requests returned validated EXRs. The progress window itself was not observed, so native exporter completion remains inferred from file readiness. Reliability tests now cover valid/invalid transitions, job recovery after process exit, missing PFM rejection, Worker termination, injected Worker failure, and cancellation between actual Worker PACK requests. Native render cancellation remains disabled pending an experimentally verified SFM control. All source PFMs and partial EXRs are retained.

## Key unresolved risks

1. Panel-launched completion uses stable files and an optional progress-window check. Native exporter completion signaling, cancellation, and direct failure distinction still need verification. A special SFM hook is not assumed.
2. Only one real beauty/depth PFM pair has been inspected; beauty color/depth semantics remain unknown.
3. Bridge error recovery, Unicode paths, repeated load/unload, and behavior during an active SFM render remain untested.
4. The initial EXR schema and transform metadata remain proposals.
5. Multi-instance rendering may require patching `launcher.dll`; safety, version stability, and necessity are unverified.
6. Community Multi-Render reports ghost frames at frame-range boundaries and resource pressure; the underlying cause and project-specific frequency need independent testing.
7. Current Bridge ownership does not guarantee Worker independence if SFM or the Bridge exits.
8. Performance gains from multi-threading, multiple Workers, or multiple SFM instances are unknown until measured.
