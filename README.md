# ModernSFM

ModernSFM is a rendering and post-processing prototype for Source Filmmaker. SFM remains responsible for scene creation and rendering; ModernSFM is intended to process SFM beauty/depth PFM outputs into validated multilayer OpenEXR files.

## Project status

The repository contains a standalone x64 Worker and a first x86 Bridge prototype. The Worker strictly parses a paired RGB beauty PFM and single-channel depth PFM, writes a multilayer FLOAT OpenEXR, reopens it, and checks channel types, dimensions, and sample values. It keeps the top-to-bottom row order observed in an SFM render. The `image-final` layer is currently an identity copy of `raw-sfm`; no color-space interpretation is asserted. Source PFMs are preserved.

The Worker has passed synthetic tests and processed real SFM frames. The x86 Bridge starts the x64 Worker through a local named pipe and forwards `PACK` requests. A live SFM Python 2.7 proof configured and launched the native Sequence Exporter for an inclusive three-frame range, exported matching 1280x720 Beauty/Depth PFMs, then sent all three pairs through the Bridge and Worker. All EXRs validated and the source PFMs remained byte-identical. The proof uses Qt controls in the native export dialog; cancellation/failure status and arbitrary-resolution depth alignment still need work. Color and depth semantics remain unverified.

The first ModernSFM Render Job window is implemented in `scripts/sfm_render_job_panel.py`. It has an explicit state machine and a durable per-job JSON journal/log. The panel keeps an archival session copy in the job directory and opens a disposable copy from SFM's sessions directory. The panel's Python orchestration runs EXR PACK calls on a Qt worker thread, so SFM remains responsive during post-processing. Processing cancellation finishes the current PACK, submits no further frames, and retains PFMs and partial EXRs. The x86 Python side reads only PFM headers; pixel parsing and EXR generation/validation remain in the x64 Worker.

The panel's actual Start workflow completed a real render on 2026-10-30. From the panel, `session5.dmx` frames 0–2 were exported at 1280x720; all three Beauty/Depth PFM pairs had matching dimensions; and the x86 Bridge/x64 Worker generated and validated three FLOAT ZIP EXRs with the expected beauty, depth, and final-image channels. The panel showed `Completed: 3 EXR frame(s) validated`, and retained all six source PFMs. The session opened by SFM was a disposable copy in SFM's sessions directory; its SHA-256 matched the original session before/after the run. The earlier crash while opening a job-folder copy is avoided by this staging path.

The panel still infers native exporter completion from the expected pair count, stable PFM sizes/timestamps, and absence of a recognized progress window. A static incomplete PFM set now fails with the exact observed file list after 60 seconds; a five-minute overall watchdog remains. SFM exposes no cancellation control verified by a live run, so rendering cancellation is disabled. Worker-stage cancellation was tested with an actual 3840×2160 PFM pair: the in-flight EXR completed, the next frame was not submitted, and both sources remained unchanged. The job journal records stage/progress evidence and a later panel startup marks a nonterminal job Failed if its owning SFM process has exited. This is recovery on the next startup, not live monitoring from a process that has already terminated.

## Build and verify

Use Visual Studio 2022 or newer with x86 and x64 C++ workloads, CMake, and vcpkg. The x64 Worker uses OpenEXR; the Bridge has no image-processing dependencies.

```powershell
$env:VCPKG_ROOT = "D:/vcpkg" # set this to your vcpkg checkout
cmake -S . -B build/x64 -G "Visual Studio 18 2026" -A x64 `
  -DCMAKE_TOOLCHAIN_FILE="$env:VCPKG_ROOT/scripts/buildsystems/vcpkg.cmake" `
  -DVCPKG_TARGET_TRIPLET=x64-windows `
  -DMODERNSFM_BUILD_WORKER=ON -DMODERNSFM_BUILD_BRIDGE=OFF
cmake --build build/x64 --config Release
ctest --test-dir build/x64 -C Release --output-on-failure

cmake -S . -B build/x86 -G "Visual Studio 18 2026" -A Win32 `
  -DMODERNSFM_BUILD_WORKER=OFF -DMODERNSFM_BUILD_BRIDGE=ON -DBUILD_TESTING=OFF
cmake --build build/x86 --config Release

# Verify the Bridge + Worker round trip in bundled x86 Python 2.7:
& "<SFM-root>/game/sdktools/python/2.7/win32/python.exe" scripts/sfm_bridge_smoke.py
```

To open the Render Job window in SFM, launch `sfm.exe -game usermod -sfm_startup_script "<ModernSFM-root>/scripts/sfm_render_job_panel.py" -condebug -console`. Open a session through SFM's initial chooser; the ModernSFM window then enables Start. Select the session to render in ModernSFM; each job writes to a new output subfolder and opens a disposable copy. This prototype supports at most three frames at 1280x720.

The independently tested native-export proof remains available as `scripts/sfm_sequence_export_poc.py`; it writes under `build/sfm_sequence_poc` and records its result in `build/sfm_sequence_export_poc.txt`.

The standalone command is `modern_sfm_worker pack --beauty <RGB.pfm> --depth <Z.pfm> --output <result.exr> [--max-memory-mib <MiB>]`. Existing output files are never overwritten.

Lifecycle tests: `tests/job_lifecycle_smoke.py` checks transition rejection and recovery after a terminated process; `tests/worker_thread_cancel_smoke.py` injects cancellation/failure around the Qt worker; `tests/worker_live_thread_cancel_smoke.py` exercises cancellation against the actual x86 Bridge and x64 Worker. `scripts/sfm_bridge_smoke.py` checks missing PFM rejection and unexpected Worker termination. These tests do not establish native Sequence Exporter cancellation or a direct SFM exporter-failure callback.

## Architecture

```text
SFM Python (x86 / Python 2.7)
        -> ModernSFM Bridge DLL (x86)
        -> ModernSFM Worker (x64 process)
```

Python orchestrates the workflow. The Bridge owns Worker lifecycle and local IPC. The x64 Worker performs PFM parsing, image processing, and EXR generation. Image buffers stay out of Python, the Bridge, and IPC. The Bridge protocol supports `PING`, `ECHO`, `PACK`, and graceful `QUIT`. `PACK` sends UTF-8 file paths and a memory limit; the Worker reads the PFM files directly, validates and writes the EXR, and returns a typed result. The current panel uses a Qt worker thread for PACK, a state machine, and job-local diagnostics. Native SFM render cancellation and direct exporter failure signaling remain unresolved.

## Documentation

- [Architecture](ARCHITECTURE.md): boundaries, data pipeline, open questions, compatibility, and future architecture proposals.
- [Roadmap](ROADMAP.md): phased investigations and development gates.
- [Phase 0 Evidence](PHASE0_EVIDENCE.md): real-frame observations, cited research, hypotheses, unknowns, and calibration tests.

The documents distinguish confirmed architecture from proposals and unresolved questions. Future capabilities such as frame-range processing, multiple Workers, and multi-instance SFM rendering are not implemented or approved for immediate development.
