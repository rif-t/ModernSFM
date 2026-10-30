# ModernSFM

ModernSFM is a rendering and post-processing prototype for Source Filmmaker. SFM remains responsible for scene creation and rendering; ModernSFM is intended to process SFM beauty/depth PFM outputs into validated multilayer OpenEXR files.

## Project status

The repository contains a standalone x64 Worker and a first x86 Bridge prototype. The Worker strictly parses a paired RGB beauty PFM and single-channel depth PFM, writes a multilayer FLOAT OpenEXR, reopens it, and checks channel types, dimensions, and sample values. It keeps the top-to-bottom row order observed in an SFM render. The `image-final` layer is currently an identity copy of `raw-sfm`; no color-space interpretation is asserted. Source PFMs are preserved.

The Worker has passed synthetic tests and processed one real SFM frame pair. The x86 Bridge starts the x64 Worker through a local named pipe and forwards a single-pair `PACK` request. The Python 2.7 smoke script passed under both the bundled 32-bit interpreter and live SFM embedded interpreter, producing a validated 3840x2160 FLOAT multilayer EXR from the real sample pair. Source PFMs were retained. SFM render orchestration, color transforms, effects, batching, and cleanup are not implemented. Color and depth semantics remain unverified.

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

To run in SFM itself, launch `sfm.exe -game usermod -sfm_startup_script "<ModernSFM-root>/scripts/sfm_bridge_smoke.py" -condebug -console`. The script writes `build/sfm_bridge_smoke_result.txt` and prints results to SFM's Python output.

The standalone command is `modern_sfm_worker pack --beauty <RGB.pfm> --depth <Z.pfm> --output <result.exr> [--max-memory-mib <MiB>]`. Existing output files are never overwritten.

## Architecture

```text
SFM Python (x86 / Python 2.7)
        -> ModernSFM Bridge DLL (x86)
        -> ModernSFM Worker (x64 process)
```

Python orchestrates the workflow. The Bridge owns Worker lifecycle and local IPC. The x64 Worker performs PFM parsing, image processing, and EXR generation. Image buffers stay out of Python, the Bridge, and IPC. The Bridge protocol supports `PING`, `ECHO`, `PACK`, and graceful `QUIT`. `PACK` sends UTF-8 file paths and a memory limit; the Worker reads the PFM files directly, validates and writes the EXR, and returns a typed result. Batch states and SFM render sequencing are not implemented.

## Documentation

- [Architecture](ARCHITECTURE.md): boundaries, data pipeline, open questions, compatibility, and future architecture proposals.
- [Roadmap](ROADMAP.md): phased investigations and development gates.
- [Phase 0 Evidence](PHASE0_EVIDENCE.md): real-frame observations, cited research, hypotheses, unknowns, and calibration tests.

The documents distinguish confirmed architecture from proposals and unresolved questions. Future capabilities such as frame-range processing, multiple Workers, and multi-instance SFM rendering are not implemented or approved for immediate development.
