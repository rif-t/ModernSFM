# ModernSFM Architecture

## Document status

This is an architecture specification, not an implementation plan authorization. No system implementation has started. Terms used below:

- **Confirmed** means explicitly required by the project owner.
- **Proposed** means a recommended design that still needs verification or approval.
- **Unresolved** means evidence or a decision is still required.

The repository currently contains architecture documentation only; there are no implementation files to describe as existing behavior. The process design below is an agreed architectural boundary, not a claim that components have been built or verified.

## Purpose and scope

ModernSFM extends Source Filmmaker's render workflow with native post-processing and OpenEXR output. SFM remains responsible for scene editing and rendering. The initial product processes SFM-generated beauty and depth PFM files. It does not replace the Source engine, editor, or viewport.

Longer-term goals include frame-sequence processing, parallel post-processing, and possibly coordinating multiple SFM instances. These are proposed future capabilities, not part of the initial implementation.

## Confirmed architecture and boundaries

```text
Source Filmmaker (x86, embedded Python 2.7)
        |
        | native binding calls
        v
ModernSFM Bridge DLL (x86, loaded in SFM)
        |
        | local IPC control messages
        v
ModernSFM Worker (x64, separate process)
```

The three components have mandatory process and bitness boundaries:

### SFM Python orchestration (x86)

- Coordinates SFM commands, render workflow, input/output paths, settings, and status reporting.
- Calls the Bridge DLL through a native binding.
- Does not process pixels, pass full image buffers, or launch/manage the Worker directly.

### ModernSFM Bridge DLL (x86)

- Loads inside SFM and exposes a native interface to its Python runtime.
- Owns Worker startup/connection/lifecycle and IPC.
- Sends paths and control/configuration data; receives progress and results.
- Does not parse PFM pixels, process images, tone-map, generate EXR, or perform GPU work.

### ModernSFM Worker (x64)

- Runs as a separate 64-bit process with its own address space.
- Owns all PFM parsing, image processing, HDR handling, AgX processing, EXR generation and validation, CPU multithreading, and optional SIMD.
- Reads PFM files and writes EXR files directly through the filesystem. Full image buffers do not cross Python, the Bridge, or IPC.

The x64 Worker must never be loaded into SFM's x86 process. The Bridge is not the processing engine. This architecture must not be replaced by direct Python process launching or by moving processing into the Bridge.

## Job workflow

1. SFM starts the ModernSFM Python orchestration script.
2. Python initializes the Bridge; the Bridge starts or connects to the x64 Worker.
3. SFM renders beauty and depth PFM files.
4. After render completion has been reliably established, Python submits paths and settings through the Bridge.
5. The Bridge sends a job request over local IPC.
6. The Worker validates both inputs, parses them, processes the data, and writes a temporary EXR.
7. The Worker reopens and validates the EXR, then publishes the final output.
8. The Worker sends a structured success/failure result through IPC; the Bridge returns it to Python.
9. Input PFMs may be deleted only after validated output publication and according to the configured retention policy. On any error or interruption, source files remain available.

Step 4 is **unresolved** until SFM's render-completion behavior is established; the workflow must not infer completion from file existence or an arbitrary size-stability timeout.

## Python 2.7 native binding

Two approaches are under consideration:

1. **Proposed initial option: `ctypes` over a narrow exported C ABI.** This avoids building a Python-specific extension module and can expose a small set of opaque handles, status values, and UTF-8/UTF-16 path arguments. It is preferred only if an end-to-end test in this exact SFM installation proves that its embedded x86 Python 2.7 can load the x86 Bridge DLL safely.
2. **Alternative: CPython 2.7 x86 extension module.** This uses Python's extension API and may provide a more natural Python interface, but couples the binding to the exact interpreter ABI, headers, compiler/runtime choices, and packaging.

Compatibility is not assumed. Verify interpreter version/bitness, DLL search behavior, calling convention, exported symbol ABI, string encoding, error propagation, and unload/shutdown behavior inside SFM. Keep the public Bridge boundary narrow and versionable; do not expose C++ classes or STL types across the ABI. No binding has been selected as final.

## Render completion and input readiness

**Status: unresolved; no SFM completion API is asserted here.** A local inspection of `game/sdktools/python/global/Scripts` and `game/sdktools/python/global/lib/site-packages/sfm` found general Python utilities but did not establish a render-finished callback or reliable completion signal for beauty/depth PFM export. This limited search is not proof that no such API or engine behavior exists.

Required investigation:

- Inspect SFM's actual image-sequence/export UI and available Python/console interfaces.
- Determine whether a supported SFM call returns only after all requested files are closed and flushed, and whether beauty and depth completion are reported together.
- Verify behavior during normal completion, user cancellation, SFM shutdown, and render/export error.
- Record the observed completion contract and a repeatable pass/fail procedure before choosing the orchestration signal.

Until then, do not submit files based solely on existence or a fixed delay. Regardless of the completion signal, the Worker must reject malformed, truncated, mismatched, or unreadable inputs without modifying or deleting them.

## Batch processing (planned)

The Worker may later accept a frame-range job with input/output patterns, per-frame status, failure reporting, cancellation, validation, and resume support. Two strategies remain to be measured:

- **Sequential batch:** one Worker processes frames one at a time. This is the preferred first batch design because it bounds memory and is simpler to resume.
- **Parallel batch:** independent frames run concurrently in multiple Workers or threads. Do not assume it improves total time; profile CPU, memory, storage throughput, and per-frame working set first.

Streaming a completed frame while SFM renders later frames is a possible optimization only after reliable per-frame render completion has been established. It must never read a partially written PFM. Batch manifests, frame naming, idempotent retries, and resume rules remain unresolved.

## PFM format and data semantics

No PFM sample from the user's SFM workflow has yet been inspected. Do not assume beauty is scene-linear or that depth is compositing-ready Z.

The eventual parser must validate:

- PFM magic (`PF` versus `Pf`) and implied channel count.
- Positive, supported width and height; checked arithmetic for pixel/payload size.
- Scale value and its sign/endian meaning.
- Expected payload length and complete reads; reject truncation and report unexpected trailing data according to an explicit policy.
- Row orientation and correct alignment between beauty and depth.
- Non-finite values (NaN and infinity) and invalid headers, with a defined reject/report policy.

Inspect actual SFM beauty and depth files to establish their binary layout, scale/endian behavior, row order, channels, and representative value ranges.

For depth, explicitly determine whether values are linear camera-space distance or nonlinear depth-buffer values, their units/range, background encoding, orientation, and pixel alignment with beauty. Only after these checks can the project decide whether the values are useful as a compositing Z-pass or require conversion/metadata. Do not label depth as camera distance without evidence.

## Image and color pipeline

Keep three concepts separate:

1. **`raw-sfm`:** exact original beauty PFM pixel values copied into the EXR, without tone mapping or other image transforms. This is source preservation; it does not prove scene-linear HDR data.
2. **Scene-linear beauty:** an interpretation that may be used only if inspection verifies the source's transfer function, primaries, range, and relevant SFM behavior.
3. **`image-final`:** a processed result. AgX is a display transform/tone-mapping operation, not a generic conversion that makes arbitrary input HDR. Define the input interpretation before applying it.

Preserve source beauty and depth independently of processed output. Do not write AgX output back over `raw-sfm`. Do not claim the PFM values are scene-linear or HDR-correct without measured evidence.

## Proposed EXR schema (not finalized)

Illustrative channel organization only:

```text
raw-sfm.R, raw-sfm.G, raw-sfm.B
depth.Z
image-final.R, image-final.G, image-final.B
```

These names are placeholders; do not treat them as the agreed schema. Final decisions require inspection of input channels and depth semantics. Still to decide: layer/channel naming, whether depth is one channel or another representation, HALF versus FLOAT, compression, metadata, color-space declarations, and data/display windows.

For correctness work, **proposed default: FLOAT** to avoid introducing half-float quantization while comparing input and output values. Evaluate HALF as a configurable option later. Select compression and color metadata only after data semantics are known. Validation must confirm dimensions, channels, layers, metadata contract, and preservation of the original values within the selected format's defined precision.

## Worker memory and performance

Target development system: Intel Core i5-12400 (6 cores/12 threads), NVIDIA RTX 4060 (8 GB VRAM), and 32 GB RAM. Initial processing is CPU-only; GPU/CUDA is out of scope until CPU correctness and measured bottlenecks justify a later design review.

Worker requirements:

- Avoid unnecessary full-frame copies and reuse buffers where safe.
- Use checked allocation-size calculations and handle allocation failures without corrupting inputs or publishing partial output.
- Release temporary allocations at job completion; reuse bounded buffers when it measurably helps.
- Provide a configurable memory budget and refuse/defer jobs that cannot safely fit it.
- Initially allow one active processing job unless benchmarks justify concurrency.
- Consider tiled/chunk processing only if measured working-set size or target resolutions require it; do not add complexity preemptively.
- Use multithreading where measured beneficial. Detect AVX2 at runtime and retain a scalar fallback; do not execute unsupported instructions.
- Benchmark throughput, peak working set, CPU scaling, and SIMD benefit against a correctness baseline.

## Proposed IPC and asynchronous job model

**Named pipes are the proposed initial local IPC**, carrying control messages only. Shared memory is not proposed for image data because the Worker reads files directly. The protocol must define version negotiation, request/job IDs, message types, submission, progress, completion, structured errors, deadlines/timeouts, cancellation, disconnect/restart, and graceful shutdown. Pipe access should be restricted to the current user/session as appropriate.

JSON may be suitable for low-volume control messages, but is not selected yet. Compare its simplicity with framing/serialization cost and robust parsing. Whatever format is selected must have explicit framing (for example, a bounded length prefix), encoding, maximum message size, and protocol-version behavior.

**One active Worker job at a time is the proposed initial limit.** The Bridge API may be asynchronous so the SFM Python call does not wait for the full conversion. Candidate API, not final:

```text
initialize()
submit_job(...)
get_job_status(job_id)
cancel_job(job_id)
shutdown()
```

Verify this polling model in SFM's actual Python/UI event environment. Do not add threads inside SFM unless compatibility and safety are demonstrated. If a supported nonblocking callback/timer mechanism is unavailable, document the limitation and propose a tested alternative before implementation; the Bridge must never leave SFM indefinitely blocked.

## Worker lifecycle and failures

Proposed Bridge-visible states:

```text
NOT_STARTED -> STARTING -> READY -> BUSY -> READY
                         |         |       |
                         v         v       v
                       FAILED   STOPPING  DISCONNECTED
                                   |          |
                                   v          v
                                STOPPED    STARTING (explicit recovery)
```

This is a starting model, not a finalized state machine. Specify legal transitions and terminal conditions before implementation. The Bridge must use bounded startup and response deadlines; surface structured errors to Python; detect process exit/pipe break; retain input PFMs after failure; and avoid automatic retry of non-idempotent operations unless output publication makes retries safe. Cancellation and shutdown must have defined acknowledgements and timeout behavior. If SFM exits, the Worker must either finish safely or stop at a defined checkpoint without deleting inputs or leaving a partial final EXR.

## Planned parallel processing and multi-instance rendering

### Multiple Worker processes

The initial architecture has one Bridge-managed Worker and one active job. A future Worker Manager may dispatch independent frames to a bounded pool of x64 Workers:

```text
Validated PFM frame queue
          |
          v
   Future Worker Manager
      |      |      |
    W#1    W#2    W#N
      \      |      /
       validated EXRs
```

This is a future proposal, not part of the current Bridge contract. Worker count and per-Worker thread count must share a resource budget to avoid CPU/RAM oversubscription. Durable job manifests, accepted-job acknowledgements, process ownership, queue persistence, retries, and whether a Worker can finish after its originating Bridge exits must be designed before a pool is introduced. The current Bridge-owned lifecycle does not guarantee detached Worker survival.

### Multiple SFM instances

A future Render Coordinator may launch multiple SFM processes and divide a frame range among them. Each instance would need independent frame ranges, output paths, progress, and failure reporting. The Coordinator is a proposed external component; it must not be inserted into the Bridge DLL by assumption.

Potential risks include duplicated scene/asset memory, RAM/VRAM pressure, CPU contention, shared temporary state, output collisions, renderer behavior at range boundaries, and DXVK compatibility. Parallel SFM rendering is not guaranteed to scale linearly or to be safe for every project. A controlled experiment must determine whether instances can render separate frame ranges of the same project reliably.

### Community project review

These links were reviewed as of 2026-10-30. Their descriptions document mechanisms and limitations, but they do not establish that the same approaches are suitable for ModernSFM:

- [Multi-Run Patch Steam Workshop page](https://steamcommunity.com/sharedfiles/filedetails/?id=3481598701) says its script modifies `launcher.dll` on disk to disable creation/checking of SFM's singleton mutex, then restarts SFM. The page says command-line `-multirun` / `-allowmultiple` did not work for SFM and in-memory patching was too late because the launcher loads before Python scripts. The same page identifies it as the companion to SFM Multi-Render. This is a description of that project's method, not a ModernSFM decision. It is invasive and would require a local backup, hash/version check, restore path, and explicit compatibility review before any future adoption.
- [SFM Multi-Render guide](https://steamcommunity.com/sharedfiles/filedetails/?id=3483343067) and its [public repository README](https://github.com/amooose/SFM-Multi-Render) describe distributing a frame range among multiple SFM sessions, copying one session's project/output settings to others, and showing aggregate progress. The guide notes resource requirements increase with each SFM process, reports ghost frames at the beginning of split ranges that need manual rerendering, mentions possible DXVK VRAM pressure, and says administrator privileges may be needed for process management/progress inspection. Its auto-hide feature changes an SFM window-position registry value and attempts to back it up and restore it.

The public guide/README were available for review; the Multi-Render source files and the Workshop patch script were not independently audited in this documentation pass. Therefore no claims are made here about their internal implementation beyond what their published descriptions state. The exact mechanism for progress reporting, process control, frame-range rendering, output collision prevention, and ghost-frame reproduction should be verified from source or isolated experiments before designing an integration.

Both Steam pages currently display removal/incompatibility notices, which may affect access and support. Potential integration point: reuse the high-level concept of dividing frame ranges and aggregating per-instance progress, while keeping ModernSFM's current EXR Worker separate. Do not depend on a launcher patch, memory inspection, or registry window manipulation until each is separately justified, reversible, and tested.

## Logging and diagnostics

Maintain separate logs for Python orchestration, Bridge DLL, and Worker. Include timestamps, severity, job/request ID, component/protocol version, error code, input/output paths as appropriate, and relevant dimensions/processing settings. Avoid per-pixel or excessively chatty logs. Define log location, rotation/retention, and how Bridge/Worker errors are returned to SFM before release.

Future batch and coordination logs should add frame number and SFM instance ID. A future Coordinator needs its own log stream and correlation IDs spanning Coordinator, SFM instance, Bridge, and Worker.

## Compatibility concerns

- Confirm the Bridge build is x86 and loads under this SFM installation's actual embedded CPython 2.7 environment.
- Confirm calling convention, ABI, runtime dependencies, DLL search path, and Unicode/long-path behavior.
- Named-pipe access and Worker launch must work under the user's normal SFM account without requiring unexplained elevation.
- Handle spaces, non-ASCII paths, SFM shutdown, Worker startup failure, IPC loss, protocol mismatch, timeout, cancellation, and crash recovery.
- Keep stock SFM, DXVK, and ReShade compatibility as a later file-output integration check; the Worker does not share SFM's graphics device.
- Do not modify the Steam installation during architecture investigation. Any later install change needs backup, hash/version checks, and restore capability. Never redistribute Valve files.
- Multi-instance SFM may require a launcher behavior change as described by the community project; no such patch is part of the current design. Any future choice must account for Steam updates, integrity verification, backups, and clean restoration.

## Out of scope for the first milestone

- SFM UI/viewport replacement or Source engine rewrite.
- Loading the x64 Worker or processing engine into SFM.
- Direct Python Worker launch/management.
- Pixel processing in Python or the Bridge DLL.
- GPU/CUDA processing before CPU correctness and profiling.
- AO, DOF, motion blur, or other advanced effects until required inputs and quality are demonstrated.
- Batch streaming, multiple Worker processes, multi-instance SFM, and a Render Coordinator.

## Development roadmap

Detailed sequencing and pass/fail gates are tracked in [ROADMAP.md](ROADMAP.md). The roadmap is aspirational beyond the single-frame foundation; future phases require their own design review and do not imply approval to implement them.

## Prioritized investigations before implementation

1. Obtain representative beauty/depth PFM files from this SFM installation and document binary structure, value ranges, color characteristics, row orientation, and depth semantics.
2. Establish a real SFM render-completion signal and interruption behavior from inspected interfaces or controlled observation. This currently blocks reliable orchestration.
3. Test native binding feasibility inside SFM's embedded Python 2.7; compare `ctypes` against a CPython extension and select one based on evidence.
4. Finalize the EXR schema and precision/compression/metadata after input semantics are known.
5. Specify the Named Pipe framing/protocol, Bridge API, Worker state transitions, deadlines, cancellation, and recovery.
6. Set memory-budget defaults and cleanup policy using actual resolution/working-set measurements; define failure-safe publication and source retention.
7. Define reproducible diagnostic logs and packaging/version/restore strategy for local installation.

## Assumptions still requiring evidence

- The SFM render/export flow can produce paired beauty and depth PFM files with matching dimensions and timing.
- The embedded SFM Python environment can load a separately built x86 Bridge through a supported binding mechanism.
- A dependable completion signal is accessible to Python or the Bridge without a fragile file-stability heuristic.
- The Worker can locate and access the same paths visible to SFM, including Unicode/long paths where required.
- Beauty and depth PFM semantics are sufficient for the proposed `raw-sfm`, `depth`, and processed `image-final` outputs.
- AgX is applicable to a verified interpretation of the source values.

## Verification plan and pass/fail criteria

1. **Collect representative files:** obtain beauty and depth PFMs from a known SFM render; pass only when both files are available with recorded provenance and dimensions.
2. **Document binary structure and semantics:** independently inspect headers/payloads and value ranges; pass only when channel count, scale/endian, row order, completeness, beauty color interpretation, and depth meaning are recorded or explicitly marked unknown.
3. **Strict PFM parser:** exercise valid and malformed inputs; pass only when valid inputs decode deterministically and bad magic, dimensions, scale, truncation, non-finite values, and mismatched files fail with clear errors and no out-of-bounds access.
4. **Multi-layer EXR:** write/reopen a known synthetic fixture; pass only when expected dimensions/channels and selected data types are present and values match the expected result.
5. **`raw-sfm` preservation:** compare decoded source values with the EXR raw layer; pass only within documented precision (exact for FLOAT when the source values are representable and no transform is applied).
6. **Depth verification:** compare the EXR depth channel to source depth samples and confirm orientation/alignment; pass only when mapping is documented or the channel is explicitly marked semantically unknown.
7. **Bridge-to-Worker IPC outside SFM:** submit/status/cancel/shutdown requests; pass only with version negotiation, bounded failures, structured errors, and no image-buffer IPC.
8. **Native binding inside SFM:** load/call/unload or shut down the Bridge using the actual embedded Python; pass only when bitness, ABI, paths, error propagation, and SFM responsiveness are verified.
9. **End-to-end SFM render-to-EXR:** pass only when a documented completion signal gates submission and the resulting EXR validates against its inputs.
10. **Fault handling:** simulate truncated inputs, Worker crash/disconnect, timeout, cancellation, and SFM exit; pass only when SFM does not wait indefinitely, partial final output is not published, and source PFMs remain intact.
11. **Resource/performance benchmark:** measure peak memory, CPU scaling, and SIMD against a scalar correctness baseline; pass only when the configured memory budget is respected and optimized output remains within the correctness criteria.

No milestone in this document authorizes implementation or changes to the SFM installation. Begin implementation only after the architecture and its unresolved decisions have been reviewed and agreed.
