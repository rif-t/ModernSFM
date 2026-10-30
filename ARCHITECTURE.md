# ModernSFM Architecture

## Document status

This document records implemented behavior and the remaining architecture. Terms used below:

- **Confirmed** means explicitly required by the project owner.
- **Proposed** means a recommended design that still needs verification or approval.
- **Unresolved** means evidence or a decision is still required.

The x64 PFM-to-EXR Worker and a minimal x86 Bridge-to-x64 Worker Named Pipe pipeline are implemented. A live SFM embedded-Python proof also configured and launched the native Sequence Exporter for three frames, then processed the resulting Beauty/Depth PFM pairs through the existing Bridge and Worker into validated EXRs. Source PFMs remained in place. This proves a short happy path for one session and 1280x720 output; production job UI, robust render outcome detection, cancellation, and general resolution handling remain unimplemented or unresolved.

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

## Implemented IPC smoke workflow

The current vertical slice is a transport/lifecycle proof only:

1. SFM launches `scripts/sfm_bridge_smoke.py` as its Python 2.7 startup script. Valve added `-sfm_startup_script` for this purpose in SFM 0.9.8.3 ([release notes](https://store.steampowered.com/news/posts/?appgroupname=Source+Filmmaker&appids=1840&enddate=1408130039&feed=steam_updates%2F1000)).
2. Python loads the x86 DLL with `ctypes.CDLL` and calls the exported C ABI.
3. The Bridge creates a local named pipe, launches the x64 Worker as a separate process, and waits for the Worker connection.
4. Python sends `PING` and an `ECHO` test request through the Bridge; the Worker replies `PONG` and the echoed payload.
5. Python sends `QUIT`; the Worker replies `BYE` and exits. The Bridge waits for shutdown and closes its handles.

This path was run inside the installed SFM application using `-sfm_startup_script`; the result log reported CPython 2.7.5, 32-bit, successful `PING`/`ECHO`, a real-pair `PACK` result, and clean shutdown. The built Bridge is x86 and Worker is x64. `PACK` uses a 24-byte little-endian header (`PACK`, three UTF-8 path byte lengths, and a uint64 memory limit in MiB), followed by the beauty, depth, and output paths. The Worker rejects malformed lengths, oversized/empty paths, embedded NULs, and zero memory limits. Its response contains `RSLT`, a uint32 status, a uint32 UTF-8 message length, and the result or error message. Job-level errors are returned as result statuses while keeping the pipe available. The Worker reads source files directly, writes and validates EXR, and retains the PFM inputs.

The sample integration run produced `build/test000240_bridge_sfm_<sfm-pid>.exr` (3840x2160, FLOAT, ZIP; 108,666,892 bytes). The same Python 2.7-compatible script also passed through the bundled 32-bit interpreter. The SFM smoke test checks the response, output existence/nonzero size, and source-file retention. The original smoke test operated on an already-existing pair; the separate native exporter proof below now provides limited live-render evidence.

## Native Sequence Export proof of concept (tested)

`scripts/sfm_sequence_export_poc.py` runs inside SFM's embedded Python 2.7. It opens a disposable copy of the local `session4.dmx`, finds and triggers SFM's native `Movie...` QAction, configures the visible Export Movie Qt controls, and starts a short Image Sequence/PFM export. It sets a ModernSFM-facing inclusive range of frames 0..2; in this tested session, the exporter end field behaved as an exclusive boundary, so the script submitted 0..3 to obtain three frames. It verified the configured 1280x720 size, PFM output, depth enabled, audio disabled, progressive refinement enabled, Depth of Field disabled, and Motion Blur disabled by reading the controls back. The light-sample override remained at its camera default while DOF was off.

The exporter produced three matching Beauty/Depth PFM filename pairs. Their sizes and modification times remained stable over repeated checks. The script sent each pair by path through the existing x86 Bridge to the x64 Worker's `PACK` request. The Worker validated each PFM pair, produced and reopened the EXR, and returned success. The POC also checked nonempty EXR outputs and SHA-256 equality of the source PFMs before and after conversion; all PFMs remained present and unchanged. The run result is recorded in `build/sfm_sequence_export_poc.txt`; outputs are under `build/sfm_sequence_poc/render_20261030_215239`.

This is evidence for the tested session and settings, not a stable SFM API contract. The script drives Qt controls in the native dialog rather than calling a verified Python renderer API. A progress window was not observed in the successful run. Success was inferred from the requested pair count, stable file metadata, and Worker validation; cancellation and native render failure were not successfully exercised. Separately, a 640x360 attempt yielded a depth PFM at 1280x720, which the Worker correctly rejected for dimension mismatch. Keep 1280x720 as the only demonstrated-safe resolution until alignment behavior at other sizes is investigated. No PFM is automatically deleted.

## Job workflow (proposed; not implemented)

1. SFM runs the ModernSFM Python orchestration script.
2. Python initializes the Bridge; the Bridge starts or connects to the x64 Worker.
3. Python configures and triggers the native Sequence Exporter through the SFM Qt UI. This control path has been demonstrated for a three-frame POC but is not yet a production interface.
4. SFM renders beauty and depth PFM files.
5. Python sends a batch configuration through the Bridge, including an inclusive frame range and input/output naming patterns.
6. As SFM renders each frame and Python determines its Beauty and Depth PFMs are ready, Python sends a frame-ready notification with the two paths. The reliability of this readiness determination remains unverified.
7. The Bridge forwards only control data and paths over local IPC; the Worker reads the files directly.
8. The Worker validates both inputs, parses them, processes the data, writes a temporary EXR, reopens and validates it, and publishes the final output.
9. The Worker reports frame acceptance, start, completion, or failure; the Bridge returns statuses to Python.
10. Python closes batch submission with a final outcome (normal end, cancellation, or render failure). The Worker reports batch completion only after submission is closed and every expected frame has a terminal state; missing frames become failures, never successes.
11. Input PFMs may be deleted only after validated output publication and according to the configured retention policy. On any error or interruption, source files remain available.

The POC observed stable file sizes and timestamps and required Worker validation before accepting the resulting EXRs. This worked for the tested three-frame export, but metadata stability alone is not a proven general readiness contract, and no native completion/progress signal was observed. The production frame-ready decision and batch-submission close signal remain **proposed and unresolved**. A special SFM hook is not assumed. Worker validation remains defensive and cannot by itself prove SFM has closed a file or will not replace it. Normal completion, user cancellation, export failure, SFM shutdown, and partial outputs still need repeatable tests before production job status can be trusted.

## Python 2.7 native binding (prototype implemented)

The prototype uses `ctypes.CDLL` with a narrow `__cdecl` C ABI. On the MSVC x86 build the C symbols are underscore-decorated, and the Python helper resolves either decorated or undecorated names. The exported functions are `msfm_abi_version`, `msfm_start`, `msfm_request`, `msfm_shutdown`, and `msfm_last_error`. Worker paths are passed as wide strings; message and response payloads are byte strings with explicit lengths.

The script and Bridge have passed a live load/call/shutdown run inside this SFM installation's embedded Python 2.7. This verifies this specific build and basic path; it does not yet test unload/reload, Unicode paths, error recovery, or use during an active SFM render. A CPython extension remains an alternative only if future needs exceed this C ABI.

### IPC wire protocol

- Bridge owns a unique local named pipe and starts the Worker with its pipe name. Remote clients are rejected.
- Each message is a four-byte little-endian unsigned payload length followed by up to 64 KiB of bytes. Reads and writes loop until the full frame transfers; Bridge I/O and connection waits are overlapped and bounded by caller-specified timeouts.
- Test commands are `PING` → `PONG`, `ECHO <bytes>` → echoed bytes, `PACK` with the binary path/memory request and typed `RSLT` response described above, and `QUIT` → `BYE` followed by process exit. Unknown commands return an error text response.
- The Bridge returns integer result codes and a diagnostic string through `msfm_last_error`: 0 success, 1 invalid argument, 2 invalid state, 3 Win32 error, 4 timeout, 5 protocol error, 6 insufficient response buffer, 7 unexpected Worker exit, and 8 internal error. On pipe failures or timeout, it closes the session and terminates a still-running Worker so the stream cannot be reused out of sync.
- No pixel data is sent through IPC. The Bridge passes only control messages and later will pass paths/configuration.

The C ABI, response-capacity behavior, timeout and error-recovery cases, and protocol version negotiation need further hardening before this prototype is treated as a production interface.

## Render completion and input readiness

**Confirmed:** a live three-frame native export was launched from embedded Python by triggering the native QAction and setting its Qt controls. In that run, all requested Beauty/Depth pairs stabilized and passed Worker validation. The installed script search did not establish a documented render-finished callback. The POC's dialog observation, expected pair count, and file stability checks remain tested heuristics for that run, not a general SFM completion contract.

**Proposed:** Python owns render sequencing and submits completed pairs after a production-grade readiness condition is established. The current POC waits for the export dialog to return, the expected pair count, stable sizes/timestamps, then lets Worker validation decide whether the pair is structurally processable. No special callback or engine hook is added unless investigation proves one is needed.

**Unresolved:** whether SFM's movie layoff writes each PFM pair synchronously before advancing, exposes a usable completion event, or closes files before Python can observe the next frame. The POC did not capture the progress window and did not experimentally exercise cancellation or renderer failure, so accurate distinction between normal completion, cancellation, and failure is still missing. The tested exporter end-boundary behavior and depth-size behavior also need validation across sessions and settings.

### Legacy local prototype (reference only)

The SFM installation contains `game/usermod/scripts/sfm/mainmenu/modern_sfm_frontend.py`, an 88 KB legacy prototype. The project owner confirms this version does not work and prefers a clean rewrite. It is outside this Git repository and must not be edited in place or treated as the implementation source of truth.

Read-only inspection shows that it configures SFM `movieSettings` for PFM output and optional depth, triggers the Export Movie UI, polls for a layoff progress dialog, and writes a `done.sentinel` when the dialog closes after being observed or when a frame count reaches the requested total. It distinguishes the configuration dialog from the progress dialog by Qt widget details, which is a heuristic tied to the current UI. It also launches a configured `ModernSFM.exe --watch` through `rm_*` functions exposed by an x86 DLL. This is an existing prototype's behavior, not a verified SFM API contract. Dialog disappearance can also follow cancellation or failure, and reaching an expected file count does not prove files are fully flushed or that the render succeeded. Neither condition is sufficient evidence of successful render completion.

The script combines render controls with a viewport bridge, has machine-specific absolute build paths, and references `render_manager.cpp`, an x86 DLL, and an x64 executable that were not present in the inspected ModernSFM project directory. No generated PFM samples or RAM ingest directory were found during that initial inspection; the owner later provided one real beauty/depth pair, recorded in [Phase 0 Evidence](PHASE0_EVIDENCE.md). The clean rewrite should live in the ModernSFM repository and target the agreed render pipeline first; viewport work stays out of scope unless separately approved.

Required investigation:

- Inspect SFM's actual image-sequence/export UI and available Python/console interfaces.
- Determine whether a supported SFM call returns only after requested files are closed and flushed, whether beauty and depth completion are reported together, and whether Python can observe each frame boundary without a special hook.
- Verify behavior during normal completion, user cancellation, SFM shutdown, and render/export error.
- Record the observed completion contract and a repeatable pass/fail procedure before choosing the orchestration signal.
- Use the legacy prototype only to identify prior intended controls and failure reports; do not copy its dialog-close sentinel as a success signal.

## Native binding verification status

The SFM startup-script smoke test verified the installed embedded CPython 2.7.5 process can load this x86 Bridge with `ctypes.CDLL`, call the C ABI, pass a worker path, exchange messages, and shut down the x64 child without freezing or crashing SFM. A separate run through the bundled 32-bit `python.exe` also passed. Unicode paths, repeated load/unload, IPC failure recovery, and render-time behavior remain untested.

The POC submits files only after repeated stable metadata checks, then relies on Worker parsing and validation; treat that as a narrow experiment, not a production readiness guarantee. The Worker must reject malformed, truncated, mismatched, or unreadable inputs without modifying or deleting them.

## Batch frame-submission contract (proposed; not implemented)

The user configures one inclusive frame range. For example, `start_frame = 0` and `end_frame = 500` means 501 expected frames. A proposed batch-start message is:

```json
{
  "type": "begin_batch",
  "job_id": "render_001",
  "start_frame": 0,
  "end_frame": 500,
  "beauty_pattern": "frame_%04d.pfm",
  "depth_pattern": "depth_%04d.pfm",
  "output_pattern": "frame_%04d.exr"
}
```

The exact encoding and fields are not finalized. The range and patterns declare the expected work; they do not mean the Worker should treat file existence as a readiness signal. Python submits each rendered pair separately:

```json
{
  "type": "frame_ready",
  "job_id": "render_001",
  "frame": 0,
  "beauty_path": ".../frame_0000.pfm",
  "depth_path": ".../depth_0000.pfm"
}
```

The Worker first checks that the frame belongs to the declared range and that paths conform to policy, then validates both PFM files as readable, structurally valid, and dimension-matched. Only a validated submission is acknowledged as `accepted`; validation errors produce `failed`. The Worker owns subsequent per-frame processing state: `started`, `completed`, `failed`, or `cancelled`. Batch statuses are `completed`, `completed_with_errors`, and `cancelled`. A duplicate frame submission, retry, or already-produced output needs a defined idempotency policy before implementation.

Python must send an explicit end-of-submissions outcome (`normal`, `cancelled`, or `failed`). Only after submissions are closed and every expected frame is terminal may the Worker report a terminal batch result. Frames never submitted by normal or failed close are recorded as missing/failed; frames left unprocessed by cancellation are marked cancelled. A user cancel should produce batch `cancelled`; it must not turn unfinished frames into successes. The exact message names and whether a partial batch can be resumed remain to be specified.

This contract separates the responsibilities: Python sequences SFM rendering and submits paths; the Bridge owns Worker lifecycle and forwards control messages; the x64 Worker owns the expected frame set, validation, processing states, and batch result. Full image buffers do not cross IPC.

The initial implementation should process frames sequentially to bound memory and simplify status/recovery. Parallel frame work remains a later optimization:

- **Sequential batch:** one Worker processes frames one at a time. This is the preferred first batch design because it bounds memory and is simpler to resume.
- **Parallel batch:** independent frames run concurrently in multiple Workers or threads. Do not assume it improves total time; profile CPU, memory, storage throughput, and per-frame working set first.

Streaming a completed frame while SFM renders later frames is the proposed handoff model, but must wait until Python can reliably determine each pair is finished. The user-facing frame range is already the batch boundary; no extra render-completion hook is assumed. The readiness condition, submission-close trigger, idempotent retries, and resume rules remain unresolved. The Worker must never silently count a missing or invalid frame as successfully processed.

## PFM format and data semantics

One user-rendered SFM beauty/depth pair has been inspected and round-tripped. See [Phase 0 Evidence](PHASE0_EVIDENCE.md) for its binary layout, sample ranges, cited research, hypotheses, and tests still needed. Do not assume beauty is scene-linear or that depth is compositing-ready Z.

The eventual parser must validate:

- PFM magic (`PF` versus `Pf`) and implied channel count.
- Positive, supported width and height; checked arithmetic for pixel/payload size.
- Scale value and its sign/endian meaning.
- Expected payload length and complete reads; reject truncation and report unexpected trailing data according to an explicit policy.
- Row orientation and correct alignment between beauty and depth.
- Non-finite values (NaN and infinity) and invalid headers, with a defined reject/report policy.

The current pair establishes layout, scale/endian behavior, row order, channels, and sample ranges for one render only. Inspect more frames/settings to establish which details are stable.

For depth, explicitly determine whether values are linear camera-space distance, Euclidean distance, nonlinear depth-buffer values, normalized/inverse depth, or another representation; determine units/range, background encoding, orientation, and pixel alignment with beauty. Valve confirms the export option was added for compositing, but public release notes do not define its numeric encoding. Do not treat the community-reported 192-unit range as a far-plane distance. Do not label depth as camera distance without calibration evidence.

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

The standalone Phase 1 Worker currently writes these names as a provisional verification schema; they are not a finalized product contract. Final decisions require inspection of input channels and depth semantics. Still to decide: layer/channel naming, whether depth is one channel or another representation, HALF versus FLOAT, compression, metadata, color-space declarations, and data/display windows.

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

## Next implementation work

1. Expand the one-frame sample set across scenes and render settings; the current pair's binary structure, range, and row orientation are documented, while color characteristics and depth semantics remain unresolved.
2. Test Python's per-frame Beauty/Depth readiness condition and its way to close submissions on normal completion, cancellation, or failure. Do not add an SFM hook unless experiments show it is necessary.
3. Harden the Bridge ABI and Named Pipe failure/timeout behavior through automated cases, and add protocol version negotiation.
4. Finalize the EXR schema and precision/compression/metadata after input semantics are known.
5. Extend the current test-command protocol to the proposed job/frame request messages and Worker batch state.
6. Set memory-budget defaults and cleanup policy using actual resolution/working-set measurements; define failure-safe publication and source retention.
7. Define reproducible diagnostic logs and packaging/version/restore strategy for local installation.

## Assumptions still requiring evidence

- One SFM render produced a beauty/depth PFM pair with matching dimensions. Reliable pairing/completion behavior across exports and frames remains unverified.
- Python can reliably identify completed Beauty/Depth pairs and distinguish normal submission close from cancellation or failure without an unnecessary SFM hook.
- The Worker can locate and access the same paths visible to SFM, including Unicode/long paths where required.
- Beauty and depth PFM semantics are sufficient for the proposed `raw-sfm`, `depth`, and processed `image-final` outputs.
- AgX is applicable to a verified interpretation of the source values.

## Verification plan and pass/fail criteria

1. **Collect representative files:** partially passed for one known SFM render pair; collect multiple frames/settings before generalizing.
2. **Document binary structure and semantics:** binary layout, sample values, and row order are recorded for one pair in [Phase 0 Evidence](PHASE0_EVIDENCE.md); beauty color interpretation and depth meaning remain explicitly unknown pending calibration.
3. **Strict PFM parser:** exercise valid and malformed inputs; pass only when valid inputs decode deterministically and bad magic, dimensions, scale, truncation, non-finite values, and mismatched files fail with clear errors and no out-of-bounds access.
4. **Multi-layer EXR:** write/reopen a known synthetic fixture; pass only when expected dimensions/channels and selected data types are present and values match the expected result.
5. **`raw-sfm` preservation:** compare decoded source values with the EXR raw layer; pass only within documented precision (exact for FLOAT when the source values are representable and no transform is applied).
6. **Depth verification:** compare the EXR depth channel to source depth samples and confirm orientation/alignment; pass only when mapping is documented or the channel is explicitly marked semantically unknown.
7. **Bridge-to-Worker IPC outside SFM:** submit/status/cancel/shutdown requests; pass only with version negotiation, bounded failures, structured errors, and no image-buffer IPC.
8. **Native binding inside SFM:** load/call/unload or shut down the Bridge using the actual embedded Python; pass only when bitness, ABI, paths, error propagation, and SFM responsiveness are verified.
9. **End-to-end SFM render-to-EXR:** pass only when the verified Python readiness condition gates each submission, the batch is explicitly closed with the correct outcome, and each resulting EXR validates against its inputs.
10. **Fault handling:** simulate truncated inputs, Worker crash/disconnect, timeout, cancellation, and SFM exit; pass only when SFM does not wait indefinitely, partial final output is not published, and source PFMs remain intact.
11. **Resource/performance benchmark:** measure peak memory, CPU scaling, and SIMD against a scalar correctness baseline; pass only when the configured memory budget is respected and optimized output remains within the correctness criteria.

No milestone in this document authorizes implementation or changes to the SFM installation. Begin implementation only after the architecture and its unresolved decisions have been reviewed and agreed.
