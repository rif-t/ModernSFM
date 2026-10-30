# ModernSFM

ModernSFM is a planned rendering and post-processing pipeline for Source Filmmaker. SFM remains responsible for scene creation and rendering; ModernSFM is intended to process SFM beauty/depth PFM outputs into validated multilayer OpenEXR files.

## Project status

This repository currently contains architecture and roadmap documentation only. No Bridge, Worker, SFM integration, or processing features have been implemented. The first planned work is evidence gathering: inspect real SFM PFM files, establish render completion behavior, and verify the Python-to-Bridge binding in SFM's embedded Python 2.7 environment.

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

The documents distinguish confirmed architecture from proposals and unresolved questions. Future capabilities such as frame-range processing, multiple Workers, and multi-instance SFM rendering are not implemented or approved for immediate development.
