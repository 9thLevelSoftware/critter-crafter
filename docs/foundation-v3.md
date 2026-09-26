# Foundation v3

This document describes the implemented v3 foundation contract and its current verification state. It is package/library version `0.2.0`, schema version `3.0.0`, and generator `cc-gen-3`. The 42 v3 candidates remain drafts pending visual approval.

## Source dispatch and identity

`data/library.json` is the dispatch document. `load_sources(data_dir)` reads its schema version first and loads only authoring documents with the exact active version. The v2 source set and `tests/golden/` fixtures stay available as historical compatibility evidence. The active v3 build rejects unsupported library, recipe, and generator versions before looking up records. The archived v2 workflow must use its original package/library version; it is not a v2 compatibility mode inside active `cc-gen-3`.

Profiles in `data/binding_profiles/` are immutable chain definitions. The current profile set is:

```text
appendage1_terminal
core1_body
head1_neck
insect_leg4_articulated
limb3_digitigrade
limb3_plantigrade
spine3_axial
tentacle8_flexible
```

Each profile has an ordered joint list, normalized bone fractions, canonical frames, axes, limits, and landmarks. The compiler computes a lowercase SHA-256 over canonical profile JSON with any existing hash omitted. `registry.json` locks each `(binding_profile_id, binding_profile_version)` to that hash. A changed profile under the same identity fails with `CC_BINDING_PROFILE_IMMUTABLE`; edits require a version or ID change and a registry update.

## Branch binding and neutral pose

Every v3 branch carries the exact profile identity triple, side, length, girth, and a socket. A socket is expressed in the named parent joint's local frame and always includes position and quaternion orientation. A root branch uses the reserved parent joint `root`; a child branch names a semantic joint in its parent profile, which is mapped by index to the compiled bone name.

The skeleton's `neutral_pose` is independent of the chain profile. It contains one local quaternion delta for every compiled bone, in compiled bone order, plus `root_offset_m`. The Blender rest-bone basis is preserved: local +Y follows the bone, +Z is the orthogonal up axis, and +X is `Y cross Z`. Runtime assembly binds geometry to the straight rest skeleton first, then applies the neutral local deltas once. FBX validation accounts for the exporter's local `Ry(180°)` bone-coordinate reparameterization while comparing the recovered semantic basis; GLB uses the canonical rest basis directly.

## Parts and recipes

Production parts carry profile ID/version, side, length, and girth. Compilation adds the locked profile hash and marks them `inventory_kind: production`; only approved production parts are generatable. The compiler also creates deterministic reference inventory for each branch requirement, so anatomy and review can proceed while production coverage is incomplete. Connectors are optional versioned two-bone skinned parts. They use `skinned_parent_child@1.0.0`; `b0` maps to the parent attachment bone and `b1` maps to the branch root bone. Connector matching requires exact chain profile identity/hash, compatible side, exact girth, normalized two-influence weights, preserved submeshes and materials, and the identity local transform. Size class requests a connector but does not establish compatibility.

Recipe matching uses one uniform length scale in the inclusive 0.8–1.25 range and checks scaled girth within 10 percent. Side and exact profile identity must match. The hard budgets are 120 bones, 16 parts, 30,000 triangles, and 4 influences per vertex. A v3 recipe contains schema/library identity, `cc-gen-3`, the profile identity triple on each fill, and derived scale diagnostics.

## Candidate generation

`critter skeleton vary` generates 14 deterministic anatomy archetypes across compact, balanced, and elongated presets: 42 candidates total. The families are biped, crawler, dragger, hexapod, quadruped, radial, and serpentine. Newly generated candidates have status `draft`; generation never auto-approves them. `skeleton status` currently reports six drafts per family, all 42 awaiting human review.

`tests/golden_v3/` is a parity fixture, not candidate approval. `critter recipe golden` uses an in-memory approved copy so deterministic recipe coverage can be tested without changing candidate status. The old `tests/golden/` files remain untouched.

## Runtime locomotion (supersedes baked walk/run travel)

Walk and run are no longer baked travel clips. Every compiled skeleton carries a `locomotion` block and the Unity package places feet at runtime; walk/run bake as one-cycle overlays driven by the gait clock. See [locomotion.md](locomotion.md). The tentacle radial archetype was retired, leaving 13 archetypes and 39 candidates; statements below about 14 archetypes, 42 candidates and 336 clips describe the earlier delivery.

## Runtime batching

The assembler emits one `SkinnedMeshRenderer` per filled part and per connector. It does not merge skinned meshes (`Mesh.CombineMeshes` is not valid for SMR bone weights). TestProject keeps CPU skinning (`PlayerSettings.meshDeformation: 0`). GPU-batched skinning is a later one-line project setting (`PlayerSettings.meshDeformation = GPUBatched`) for TestProject and the game; it is not this package's job and must not be flipped as a side effect of measurement. `CreatureBaker` is not in the first production library. The EditMode report at 1/8/32 instances (`BatchingMeasurementTests`) is the measurement gate for that later PR; there is no millisecond CI frame-time budget.

## Motion, QA, review, and approval

The motion plan emits the eight clips `idle`, `walk`, `run`, `stun`, `telegraph`, `attack`, `hit`, and `death` at 30 FPS, with local bone rotations, contacts, root samples, and loop metadata. `skeleton qa` validates every frame of actual built `motion.json` clips and writes diagnostics bound to `content_fingerprint` in `work/review/foundation-v3/qa.json`. `skeleton approve` requires that passing QA for the current fingerprint (`qa.passed`, `qa_version`, `sample_source == "evaluated_blender"`, 8 clips). `skeleton reject` requires a current QA result bound to the same fingerprint; the QA may have failed. Neither command requires `work/review/receipts/{id}.json`. `skeleton review` remains optional for spot-checks: it still builds a local 3-mode × 4-view HTML bundle. Start it with:

```powershell
python -m http.server 8765 --directory work/review/foundation-v3
```

Any source, profile, setting, baked motion, or assembled mesh change makes a prior QA result stale. The optional review modes are bones/contact overlay, neutral weighted mannequin, and compatible assembled parts; each is presented from front, side, top, and three-quarter views with the actual built clips and travel/grid context.

Unity playback uses the clip timing and nominal speed metadata, with duration-aware rates verified against the requested movement speed. Locomotion contact phases remain aligned in normalized clip time when durations differ, following [Unity's Blend Tree guidance](https://docs.unity3d.com/6000.0/Documentation/Manual/class-BlendTree.html).

## Polish overrides

Generated masters and authored polish are separate. A non-destructive override is stored at `work/polish/skeletons/<id>/override.json`; it records the source fingerprint and points at the authored blend asset. Rebuilds may resolve it only when the fingerprint still matches. Incompatible source changes fail with `CC_POLISH_STALE`; changing the authored polish also invalidates the built asset fingerprint and requires a rebuild. Integration coverage verifies authored key preservation, rest hierarchy and clip completeness, incompatible override rejection, and edited-polish invalidation. Both generated master and authored override remain available for review.

QA treats contact geometry and locomotion drift separately. Planted foot and hand tips may be within a 10 mm anatomical ground-proximity band, while body contacts use a 5 mm band; planted drift remains `max(5 mm, 1% of chain length)`, and penetration remains 5 mm. These bands do not waive drift, support, limit, loop, or transition checks.

## Delivery state

Automated foundation delivery is verified in [delivery-verification.json](../work/rebuild-foundation/delivery-verification.json). The final catalog SHA is `677733d508d911ce3a556d18e904dbd53cd88fe7669d9cf451963ecab93a9745`; all 42 candidates have fresh passing receipts and 336 clips, with 462 reference parts/connectors. Maximum observed planted drift is 0.1485 mm, surface penetration 3.7848 mm, export position error 0.013126 mm, and export angle error 0.0009622°. Actual maxima are 66 bones, 16 parts, 11,664 triangles, and 2 vertex influences. The byte-verified packages are [`critter-library-biomass_core-v0.2.0.zip`](../dist/critter-library-biomass_core-v0.2.0.zip) and [`com.ninthlevelsoftware.crittercrafter-0.2.0.tgz`](../dist/com.ninthlevelsoftware.crittercrafter-0.2.0.tgz); the pack command now refuses stale catalogs or assets. The unique Python test inventory is 245 (`241` existing suite tests plus `4` focused status-predicate regressions; no full-suite rerun followed those focused checks), with the existing Python/Unity reference approval pairs kept exact. Static review rendering produced 103 outputs and motion previews produced 145 outputs including 42 GIFs; serve the final bundle with `python -m http.server 8765 --directory work/review/foundation-v3` and open the verified local review URL. Automated delivery passed, while independent human visual approval remains pending and browser UI automation was unavailable.
