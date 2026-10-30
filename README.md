# ModernSFM

ModernSFM is a planned rendering and post-processing pipeline for Source Filmmaker. SFM remains responsible for scene creation and rendering; ModernSFM is intended to process SFM beauty/depth PFM outputs into validated multilayer OpenEXR files.

## Project status

The repository contains the architecture documents and a standalone x64 Worker milestone. The Worker strictly parses a paired RGB beauty PFM and single-channel depth PFM, writes a multilayer FLOAT OpenEXR, reopens it, and checks channel types, dimensions, and sample values. It keeps the top-to-bottom row order observed in an SFM render. The `image-final` layer is currently an identity copy of `raw-sfm`; no color-space interpretation is asserted. Source PFMs are preserved.

The Worker has passed synthetic tests and processed one real SFM frame pair. It does not include the x86 Bridge DLL, Python orchestration, SFM integration, color transforms, effects, batching, or cleanup. Color and depth semantics remain unverified.

## Build and verify

Use Visual Studio 2022 or newer with the x64 C++ workload, CMake, and vcpkg. The project manifest installs OpenEXR.

```powershell
cmake -S . -B build -DCMAKE_TOOLCHAIN_FILE=<vcpkg-root>/scripts/buildsystems/vcpkg.cmake -DVCPKG_TARGET_TRIPLET=x64-windows
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
```

The standalone command is `modern_sfm_worker pack --beauty <RGB.pfm> --depth <Z.pfm> --output <result.exr> [--max-memory-mib <MiB>]`. Existing output files are never overwritten.

## Architecture

```text
SFM Python (x86 / Python 2.7)
        -> ModernSFM Bridge DLL (x86)
        -> ModernSFM Worker (x64 process)
```

Python orchestrates the workflow. The Bridge owns Worker lifecycle and local IPC. The x64 Worker performs PFM parsing, image processing, and EXR generation. Image buffers stay out of Python, the Bridge, and IPC.

## Documentation

- [Architecture](ARCHITECTURE.md): boundaries, data pipeline, open questions, compatibility, and future architecture proposals.
- [Roadmap](ROADMAP.md): phased investigations and development gates.
- [Phase 0 Evidence](PHASE0_EVIDENCE.md): real-frame observations, cited research, hypotheses, unknowns, and calibration tests.

The documents distinguish confirmed architecture from proposals and unresolved questions. Future capabilities such as frame-range processing, multiple Workers, and multi-instance SFM rendering are not implemented or approved for immediate development.
