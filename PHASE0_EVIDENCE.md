# Phase 0 Evidence: SFM Beauty and Depth PFM

**Status:** partial. One real 3840×2160 SFM beauty/depth pair has been parsed and round-tripped through the standalone Worker. Its binary layout and row orientation are confirmed for this sample. Beauty color interpretation and depth meaning remain unverified. Do not begin Bridge or SFM integration from this record alone.

## Confirmed from the local sample

The user supplied one frame pair, rendered by SFM. The standalone Worker accepted both files, wrote a FLOAT multilayer EXR, reopened it, and compared every output sample with the decoded input. The user confirmed that preserving the PFM payload's top-to-bottom row order makes the EXR match the rendered frame. This verifies file handling for this pair; it does not verify the semantics of the samples.

| File | Header and layout | Observed sample range |
| --- | --- | --- |
| Beauty | `PF`, 3840×2160, 3 channels, scale `-1.0` (little-endian), top-to-bottom rows | 0.000443166 to 2.895746; values above 1 are present |
| Depth | `Pf`, 3840×2160, 1 channel, scale `-1` (little-endian), aligned dimensions and row order | 0.038208 to 1.22070315×10^16; 1,868,389 of 8,294,400 samples exceed 10^15 |

The very large depth values may be a background or invalid-pixel sentinel, but that is only a hypothesis. They do not establish a far distance. The Worker currently copies them unchanged into `depth.Z`.

## Beauty: HDR and color space

### Confirmed

- Valve's Source HDR presentation describes multiple HDR implementations, including floating-point RGB render targets and an integer HDR path. This is evidence that Source rendering paths can keep HDR data internally; it is not a specification for SFM's PFM writer. [Valve, *HDR in Valve's Source Engine* (SIGGRAPH 2006)](https://cdn.steamstatic.com/apps/valve/2006/SIGGRAPH06_Course_HDRInValvesSourceEngine_Slides.pdf)
- Valve's Orange Box post-processing presentation describes gamma-space color textures being decoded for linear-space pixel shading. It also says its HDR lighting/environment data are in linear space, pixel shaders apply a tone-map scalar, and the back buffer is RGBA8888. It separately describes histogram-based exposure/tone mapping. These facts concern the documented engine pipeline, not the point in that pipeline from which SFM writes PFM. [Valve, *Post Processing in The Orange Box* (GDC 2008)](https://cdn.fastly.steamstatic.com/apps/valve/2008/GDC2008_PostProcessingInTheOrangeBox.pdf)
- The local beauty PFM contains finite float values greater than 1. That confirms retained numeric headroom in this file, not a particular transfer function or scene-linear interpretation.
- No public SFM export specification or source code was found that states whether beauty PFM is read before or after exposure, tone mapping, gamma conversion, color correction, or other post-processing.

### Evidence-backed hypothesis

**Linear RGB is plausible, but unconfirmed.** Valve documents linear-light processing in parts of the Source HDR pipeline, and this sample has values above 1. However, the same documented pipeline includes tone mapping/exposure and gamma conversions, and the SFM PFM readback point is unknown. HDR-range values do not prove linear RGB. The PFM also carries no color-space declaration in the inspected header.

### Unknowns

- Whether PFM beauty samples are scene-linear, display-referred, or another intermediate representation.
- Whether export applies a fixed exposure multiplier, adaptive exposure, tone mapping, gamma/sRGB encoding, color correction, bloom, or another transform.
- Which primaries/white point the values represent and whether that varies by Source content or SFM settings.
- Whether the observed range and transform are stable across scenes and render settings.

## Depth: representation and the “192 units” claim

### Confirmed

- Valve's SFM 0.9.7.4 release notes say a **Write Depth PFM** option was added to movie layoff “for compositing in other packages.” They do not specify the numeric representation, units, clipping planes, background value, or conversion formula. [Valve, Source Filmmaker 0.9.7.4 release notes](https://store.steampowered.com/news/posts/?appgroupname=Source+Filmmaker&appids=1840&enddate=1406244059)
- A 2022 community article describes enabling “Write PFM Depth file” to export a depth sequence. It reports observations about exposure affecting its examples, but provides no mathematical encoding or calibration. Treat those observations as anecdotal. [Jordy Veenstra, *Depthmaps in the Source Filmmaker* (2022)](https://apixelatedpointofview.blogspot.com/2022/06/depthmaps-in-source-filmmaker.html)
- The supplied Steam URL `id=3481598701` currently resolves to an unrelated item titled **Multi-Run Patch**, not “SFM Depth of Field Post-Production.” [Resolved Steam item](https://steamcommunity.com/sharedfiles/filedetails/?id=3481598701)
- The “192 units” sentence appears on a separate community page, which says Source's depth buffer has a “very short range of just 192 units” but does not define whether this means a clip distance, useful precision range, visualization range, or something else. It provides no derivation or conversion from PFM sample values. Do not use 192 as a maximum depth distance. [Steak's Docs, *SFM Depth of Field Post-Production*](https://thunderysteak.github.io/sfm-dof-postproduct)
- As a generic Direct3D 9 fact, ordinary z-buffer values are not evenly distributed across the depth range and depend on the near/far clipping-plane ratio; a w-buffer has different behavior. Microsoft's documentation also states that the actual interpretation is renderer-specific. This is useful context, not evidence that SFM exports either raw hardware representation. [Microsoft Learn, *Depth Buffers (Direct3D 9)*](https://learn.microsoft.com/en-us/windows/win32/direct3d9/depth-buffers)
- The supplied YouTube URL is a search-results page, not a specific video. No video-specific technical claim can be verified from that reference alone.

### Hypotheses, not conclusions

The PFM could contain linear camera-space Z, Euclidean camera distance, projected/nonlinear z-buffer values, normalized depth, reciprocal or otherwise transformed depth, or an SFM-specific compositing/DOF value with a special background code. The current sample's huge finite values suggest special values may be present, but do not identify which pixels are background or what any ordinary value means.

The broad claim that a conventional perspective z-buffer is nonlinear cannot be applied directly to the exported PFM: the export may linearize, rescale, or otherwise transform the render buffer. Likewise, “192 units” could refer to an effective range or precision limitation rather than the far clipping plane. Evidence currently does not distinguish these possibilities.

### Unknowns

- Whether exported values are linear camera-space Z, Euclidean distance, nonlinear projected depth, normalized depth, inverse depth, or a custom representation.
- Units, near/far planes, useful precision range, and whether any “192 units” value is related to this output at all.
- Background/sky/invalid-pixel encoding and the meaning of values near 1.2207×10^16.
- Whether exposure, DOF, progressive refinement, motion blur, or other render settings affect the depth PFM.
- Pixel alignment/occlusion behavior around transparency, particles, view models, hair, and antialiased edges.

## Tests required before assigning semantics

### Beauty calibration

1. Render a controlled color chart with known material/shader inputs, including neutral patches below 1, at 1, and above 1; use fixed lighting and disable unrelated effects where possible.
2. Render matched frames while changing only camera exposure/tonemap controls. Compare per-patch PFM values to detect exposure scaling or tone mapping in export.
3. Compare PFM against a documented SFM output from the same frame and settings (for example an 8-bit image sequence) to characterize transfer behavior; do not treat that comparison alone as proof of scene-linear values.
4. Where possible, inspect or instrument the exact SFM render/export readback stage. Record transfer function, primaries, white point, exposure behavior, post-processing stage, and variation across SFM settings before labeling the channel Linear RGB.

### Depth calibration

1. Build a test scene with flat opaque planes at measured Source-unit distances in front of the camera, including distances below, near, and above 192 units. Record camera near/far settings and world transforms.
2. Include pairs of points with equal camera-space Z but different off-axis angles. Constant values support camera-space Z; angle-dependent values may support Euclidean distance or another projection-dependent representation.
3. Repeat while changing near/far planes and camera exposure independently. Determine whether values follow linear distance, a projected-depth formula, an exposure-dependent transform, or another mapping.
4. Capture background and known geometry separately; map the large finite values to pixels before deciding whether they are sentinels. Check transparent edges, particles, view models, and alignment with beauty.
5. Fit candidate mappings to measured distances and validate them on held-out distances/scenes. Accept a representation only when its residuals and sentinel behavior are explained. Test the “192 units” claim with geometry beyond 192; do not infer it from a single frame.

## Phase 0 decision

The file-format/round-trip gate is passed for one real frame pair, including the observed top-to-bottom row order. **The color/depth semantics gate is not passed.** Keep beauty and depth values unchanged, attach no Linear RGB or physical-unit metadata, and do not apply AgX or convert depth to Source units/meters. The evidence is not yet sufficient to define a semantic Bridge job contract or begin Bridge/SFM integration.
