# ModernSFM Development Roadmap

## Status and sequencing

This roadmap describes proposed phases. The repository currently contains documentation only. No feature in this roadmap should be described as implemented until code exists and its verification gate passes. The three-component architecture in [ARCHITECTURE.md](ARCHITECTURE.md) remains fixed: SFM Python x86 → Bridge DLL x86 → separate Worker x64.

## Phase 0 — Evidence and architecture validation

**Goal:** resolve the data and integration assumptions that block implementation.

- Collect representative SFM beauty and depth PFM files with provenance.
- Document PFM headers, payload, channel count, dimensions, scale/endian, row order, value range, and incomplete-file behavior.
- Establish beauty color characteristics and whether scene-linear interpretation is justified.
- Establish depth encoding, range/units, background value, and alignment with beauty.
- Identify and verify an SFM render-completion signal; inspect actual workflow/API behavior without inventing undocumented APIs.
- Test x86 native binding options inside this SFM installation's embedded Python 2.7; evaluate `ctypes` first and retain a CPython extension as alternative.
- Approve initial EXR channels, precision, compression, metadata, and data/display windows.
- Specify Bridge↔Worker IPC, API, timeouts, cancellation, and failure semantics.

**Gate:** implementation starts only after the architecture questions that affect component boundaries and input/output correctness are resolved. Unknown color/depth semantics may remain explicitly marked unknown rather than guessed.

## Phase 1 — Standalone data-format proof

**Goal:** verify file-format handling without coupling it to SFM.

- Define synthetic PFM fixtures including valid RGB/grayscale and malformed headers/payloads.
- Specify strict parser behavior for non-finite values, endian/scale, orientation, truncation, dimensions, and overflow.
- Write a multilayer EXR from synthetic known values using the proposed schema and FLOAT channels.
- Reopen and validate dimensions, channels, layer organization, and values; test `raw-sfm` preservation.

**Gate:** the parser deterministically accepts valid fixtures, rejects invalid fixtures safely, and EXR round-trip checks pass against expected values. No source PFMs are deleted in this phase.

## Phase 2 — Single-frame x86 Bridge to x64 Worker

**Goal:** validate process boundaries and IPC independently of SFM rendering.

- Implement only after Phase 0 design gate.
- Build the x86 Bridge and x64 Worker as separate binaries.
- Exercise initialize, submit, status/progress, cancellation, errors, shutdown, Worker exit, protocol mismatch, and timeout behavior through IPC.
- Verify one active job and control-message-only IPC; Worker reads image files directly.
- Add separate logs and version/protocol diagnostics.

**Gate:** all protocol and failure scenarios return bounded structured outcomes; the Worker is never loaded into the x86 process and no full image buffers cross IPC.

## Phase 3 — SFM single-frame integration

**Goal:** complete one SFM render-to-EXR operation through embedded Python and the Bridge.

- Load and call the x86 Bridge using the selected binding in SFM's actual Python environment.
- Use the verified render-completion signal to gate submission.
- Process a real beauty/depth pair and compare source and EXR values.
- Publish through a temporary output followed by reopen/validation and atomic/failure-safe final publication where supported.
- Add configurable source retention; delete inputs only after validated publication.

**Gate:** repeatable end-to-end success and failure tests show no partial EXR published as final, no premature input deletion, and no indefinite SFM blocking.

## Phase 4 — Sequential frame-range processing

**Goal:** process animation outputs reliably before introducing concurrency.

- Define frame-range manifests, file patterns, per-frame status, output ownership, retries, resume behavior, and cancellation.
- Process one frame at a time to bound memory.
- Report failed frames and validate every output before marking completion.
- Evaluate streaming from SFM only after per-frame completion detection is reliable.

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

1. A reliable supported SFM render-completion signal has not been established.
2. The real beauty/depth PFM format and color/depth semantics have not been inspected.
3. Bridge loading through `ctypes` in SFM's embedded Python 2.7 remains unverified.
4. The initial EXR schema and transform metadata remain proposals.
5. Multi-instance rendering may require patching `launcher.dll`; safety, version stability, and necessity are unverified.
6. Community Multi-Render reports ghost frames at frame-range boundaries and resource pressure; the underlying cause and project-specific frequency need independent testing.
7. Current Bridge ownership does not guarantee Worker independence if SFM or the Bridge exits.
8. Performance gains from multi-threading, multiple Workers, or multiple SFM instances are unknown until measured.
