# Handoff: critter-crafter (2026-09-24)

## Current state (one paragraph)

The v3 skeleton foundation and **runtime foot-placement locomotion** are merged to `main` ([PR #1](https://github.com/9thLevelSoftware/critter-crafter/pull/1)). The library has **13 archetypes × 3 presets = 39 draft skeletons**, all passing QA, and all 39 reach the game's 2.5 m/s. Four Meshy scout meshes are fitted into production parts: an insect leg, a frayed arm, an animal skull and a tentacle. All four are built into the library with their textures and pass deformation QA through every clip against the placeholders they replace (see [M2 status](#m2-status-real-parts)). **`meshy_insect_leg_a_v1`, `meshy_animal_skull_a_v1` and `meshy_tentacle_a_v1` are `approved`.** `meshy_frayed_arm_a_v1` is still `draft` pending its own pull request (walking-on-hands; no `tags_any`). EditMode tests assert the Unity texture bind (`_BaseMap`/`_MainTex`, smoothness 0.25) when a built library is present and skip if none is. **Next:** owner visual review and approve of the frayed arm, then **M5** (Synaptic Sea integration).

## What the project is

critter-crafter is a standalone tool that builds procedural monsters from three shared libraries: rigged **skeletons**, body **parts**, and biomass **connectors**. The pieces are:

- **Authoring:** Python 3.12 (`uv`) plus headless Blender 5.2.
- **Runtime:** the Unity UPM package `unity/com.ninthlevelsoftware.crittercrafter`.
- **First consumer:** `9thLevelSoftware/synaptic-sea-unity`.
- **The original plan:** `.claude/plans/floating-wiggling-sprout.md`. Its milestone list is still valid, with the locomotion work inserted.

## Decisions the owner made (don't relitigate)

| Decision | Detail |
|---|---|
| **Skeleton-first assembly** | Branches with snap points accept parts; one Animator per creature. |
| **Runtime locomotion ("option B")** | Unity Animation Rigging IK, replacing baked walk/run travel. The reason: baked clips moved at 0.03–0.26 m/s against a 2.5–2.8 m/s game speed. |
| **Speed comes from the catalog** | Each skeleton publishes `v_walk_mps`, `v_run_mps` and `v_max_mps`. The M5 adapter should use these as the threat's `move_speed` instead of forcing one speed. |
| **Tentacle radial archetype retired** | "Tentacles are complicated." The `tentacle8` chain stays for serpent bodies and dragger bellies. |
| **Draggers crawl like the 7 Days to Die legless crawler** | Chest on the ground, head up, arms reach, plant, and haul, with a body lurch. The owner said "fits for now, refine later". |
| **Keep process light** | Heavy governance stalled the first in-engine attempt. Keep receipts and gates minimal. |
| **Confirm Meshy costs first** | Every paid Meshy call needs the owner's cost confirmation first (M3). |

## Environment

| Item | Value |
|---|---|
| Repo | `D:\critter-creator` (Windows 11) |
| Blender | `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`, auto-detected by `critter doctor` |
| Unity | `F:\Unity\6000.6.0f1\Editor\Unity.exe`; test project `unity/TestProject` |
| Animation Rigging | Resolves to the built-in 6.6.0. The package declares `1.3.0`. |
| Enter Play Mode Options | The TestProject has them enabled (no domain or scene reload), set by the Play Mode test and capture. This is intentional. |
| Asset archive | `9thLevelSoftware/synaptic-sea-asset-archive` (private) cloned **next to** this repo, or `CRITTER_ASSET_ARCHIVE` / `[paths].asset_archive` in `critter.toml`. Needed only to build real parts. |
| glTF-Validator | Optional native Khronos CLI **2.0.0-dev.3.10** (not npm/Node/PATH). Pin `[tools].gltf_validator` + `gltf_validator_sha256` in `critter.toml` (see `critter.toml.example`). Windows exe SHA-256 `4388a152ff90b68c6430ae03862e05e257a9d50a500ed7d0eb1cd420dc75ff96`. Runs only when the SHA-256 pin matches. Missing tool → `CC_GLTF_VALIDATOR_MISSING` warning; Errors fail `library build`. |
| Gitignored build output | `work/` (reviews, logs, part previews), `library/` (built library), `dist/` |
| Linux / cloud sessions | Blender 5.2.2 for Linux (`CRITTER_BLENDER`) runs the whole suite. Workbench/EEVEE renders need Mesa EGL (`apt-get install libegl1 libegl-mesa0 libgl1-mesa-dri`). There is no Unity there. |

## Everyday commands

```powershell
uv run pytest                                   # 279 tests, ~4 min with Blender (-m "not blender": ~15 s)
uv run critter skeleton vary --force            # regenerate the 39 draft skeletons from archetypes.py
uv run critter schema validate
uv run critter recipe golden                    # writes tests/golden_v3/{catalog,recipes,locomotion}.json
Copy-Item tests/golden_v3/*.json unity/com.ninthlevelsoftware.crittercrafter/Tests/Editor/GoldenV3/
uv run critter library build                    # ~5 min: Blender bakes every skeleton/part -> library/; FBX/GLB skin contract always fails the build; Khronos validator Errors fail when the binary is pinned
uv run critter skeleton qa                      # motion + export + locomotion QA for all skeletons
uv run critter part list                        # real parts: status, archive source, last QA verdict
uv run critter part refit --all                 # after changing parts/fit.py or ops_realpart.py (approved -> draft)
uv run critter part review <part_id>            # ~5 min: every clip + IK stride poses on 3 accepting skeletons
uv run critter part approve <part_id>           # owner only, after the sheet; pins the reviewed pipeline
```

Unity, headless (close any open editor on the TestProject first):

```powershell
$U = "F:\Unity\6000.6.0f1\Editor\Unity.exe"
# Tests (~2 min): expect 34 passed + 1 skipped (PrototypeImportTests needs env vars)
& $U -batchmode -projectPath unity/TestProject -runTests -testPlatform EditMode `
     -testResults work/unity_results.xml -logFile work/unity_tests.log
# Locomotion review capture (Play Mode, iso camera), then GIFs
& $U -batchmode -projectPath unity/TestProject `
     -executeMethod CritterCrafter.Editor.LocomotionCapture.CaptureFromCommandLine `
     -critterLibrary library/biomass_core-v0.2.0 -critterSkeletons quadruped_stocky_plantigrade_balanced_v3 `
     -critterOut work/review/locomotion -critterSpeeds walk,run,2.5 -logFile work/unity_capture.log
uv run python tools/frames_to_gif.py work/review/locomotion
```

The normal change loop:

1. Edit sources.
2. Run `skeleton vary --force`.
3. Run `recipe golden`, then copy the goldens into the Unity package.
4. Run pytest.
5. Run `library build`, then `skeleton qa`.
6. Run the Unity tests and a capture.

The golden, Unity-test and QA steps fail loudly if skipped.

## Where things live

| Area | Files |
|---|---|
| Archetypes (anatomy, stances, gait phases) | `src/critter_crafter/skeletons/archetypes.py`: `ARCHETYPES`, the `_biped` … `_dragger` builders, `_tune_extension`, `_fit_leg_to_hip`, `_ground_by_first_joint`, `_insect_leg`, `_mammal_leg` |
| Neutral-pose FK | `skeletons/kinematics.py` |
| Locomotion block (catalog) | `locomotion/block.py`: strokes, duty factors, speeds, drag and slide blocks |
| Reference planner | `locomotion/stepper.py`. `locomotion/qa.py` holds the static QA and the golden rows. |
| Baked clips | `skeletons/motion.py`: walk and run are `phase_driven` overlays for legs and slide modes. Blender baking is in `blender/ops_skeleton.py`. |
| Unity runtime | `Runtime/Locomotion/`: `CreatureGait`, `StepPlanner`, `LocomotionRigBuilder` (the assembler hook) |
| Unity review | `Runtime/Review/` (`ReviewCourse`, `LocomotionRecorder`), `Editor/LocomotionCapture.cs` |
| Animator | `Editor/AnimatorBuilder.cs`: an Idle state plus a Locomotion blend on `Gait`, with Motion Time set to `GaitPhase` |
| Tests | `tests/test_locomotion.py`; Unity `Tests/Editor/StepPlannerGoldenTests.cs` and `LibraryTests.RuntimeLegsPlantFeetAtGameSpeed` |
| Real parts | `parts/fit.py` (pure-Python centerline, straightening, joint remap, envelope, weights), `parts/commands.py` (`critter part ...`, QA judge), `blender/ops_realpart.py` (import, clean, loops, texture, export), `blender/ops_partqa.py` (clip deformation metrics and review renders), `data/parts/meshy_*.part.json` (source records) |
| Tests | also `tests/test_real_parts.py` (no Blender) and `tests/test_real_parts_blender.py` (needs Blender and the archive) |
| Docs | `docs/locomotion.md` (model and integration), `docs/parts.md` (real parts), `docs/foundation-v3.md`, `docs/frame.md` |

## Hard-won lessons (read before touching locomotion)

1. **Edit-mode Unity capture lies.**
   - Skinned meshes don't re-skin without the player loop.
   - A separately evaluated Animation Rigging graph starts clip-animated bones from the straight bind pose.
   - An embedded graph in edit mode doesn't solve at all.
   - So all capture and locomotion tests run in **real Play Mode**, with `Time.captureFramerate = 30`, measuring in `LateUpdate`. `WaitForEndOfFrame` never fires in batch mode.
2. **Animation Rigging's "maintain offset" stores world vectors** that don't turn with the target. `CreatureGait` places hinge targets on the ankle itself, as the foot position plus the ankle offset rotated by the body's rotation.
3. **Stroke must match how the runtime solves the leg.**
   - limb3 legs: measure at the ankle with thigh plus shin.
   - Insect legs: model the coxa yaw (±45°) plus the femur/tibia hinge, and **centre the stance in the reachable chord** (`stance_shift_m`). Otherwise fanned legs land out of reach and plant hovering in the air.
4. **Stance tuning clamps to binding-profile joint limits.** Profile flexion is bone −X, and the knee joints only bend one way. Quadruped front legs flip the branch's `up` vector instead of negating their angles.
5. **Catalog quaternions are rounded to 6 decimals.** `CritterFrame.Rotation` normalizes them; `Quaternion.Angle` misreads unit-length error as real angles.
6. **Unity's `JsonUtility` can't read nested arrays.** The landings in the golden file are flattened.
7. **`AnimatorStateInfo.normalizedTime` keeps counting time even under Motion Time.** The pose follows `GaitPhase`, so test the pose, not the reported time.
8. **The body bob only dips.** Raising the hips costs a trailing foot its reach.
9. **Grounded (dragger) bodies heave about the rear of the torso** (`body_pivot_m`). Pivoting at the creature origin sank the dragged tail through the floor.

## Hard-won lessons (real parts)

1. **Meshy low-poly GLBs arrive shredded.** The glTF importer splits every UV seam, so a 1,450-triangle
   arm imports as 600+ islands. Merge by distance (1e-5 of the diagonal) before any topology work.
2. **Don't parameterize a bent mesh by closest-point projection.** On tight bends and bulky root blobs
   it jumps between branches of the centerline and tears the straightened mesh (edge strain 13–30×).
   The level sets of `geodesic_from_root − geodesic_from_tip` are continuous everywhere, and a spike
   keeps its base's value. Map them to arc length and add the offset's tangent component. That took
   straightening strain to about 1.4–1.6×, which is the physical minimum for these bends.
3. **Area-weighted face centroids, not vertex averages.** Low-poly vertex density follows detail
   (fingers, spikes), not shape.
4. **Thick low-poly joints collapse under narrow rigid bands.** The scout insect leg's inner knee hit
   p01 0.49 in the hexapod crouch. Rubber-hose blending plus edge loops at the weight breakpoints fixed
   it. Loops without widening the band made it worse, because the bend just concentrated.
5. **Baked walk/run clips don't bend legs** (the runtime IK does), so clip-only QA misses the stepping
   knee range. `critter part review` adds IK stride poses (front and back of the stroke, swing apex).
6. **The QA judge is relative to the placeholder on the same branch.** Absolute strain limits either
   fail every crouched insect leg or pass everything; the placeholders already survived anatomy review.
7. **Never commit Meshy bytes.** This repository is public and the archive is paid-private. Records keep
   only the path, SHA-256, fit and measured envelope; meshes, textures, previews and review sheets stay
   in `library/` and `work/`.

## Open issues and follow-ups

- **Turns and slopes:**
  - a brief shuffle after instant 180° turns;
  - a few centimetres of downhill foot slide on the 20° ramp;
  - the first stride from standing can drag a foot (tests exclude the first second);
  - see `docs/locomotion.md` → Known limitations.
- **Dragger refinement.** The owner said "refine later". Placeholder parts make the hands read as feet and the head small. The real arm and skull fit dragger branches, so approving them should help. Possible tuning: `dragSurge`, `dragHeaveDeg`, `dragRollDeg` on `CreatureGait`, and the arm stance in `_dragger`.
- **Arms fit leg branches.** A `limb3_plantigrade` part matches arm and leg branches alike (no
  `tags_any` on any branch), so the real arm also becomes humanoid and quadruped legs, walking on
  hands. Decide whether that's a feature; if not, tag leg branches and ship leg parts.
- **Pools** (`data/pools/pools.json`) still list only the biped, quadruped and crawler families. Add the new families when the game needs them; this changes the golden recipes.
- **All 39 skeletons are still `draft`.** `critter skeleton approve` needs a passing `skeleton qa` bound to the current `content_fingerprint`. The 3×4 HTML review bundle is optional spot-check only; a receipt file is not required.
- **Variety** is still 13 body plans × 3 presets, well below M4's target of at least 80 skeletons. The earlier review suggested optional branches (tails, dorsal parts, extra arms) and wider seeded proportions.

## M2 status (real parts)

What was asked (from the plan): import the scout meshes, clean → fit → weight → QA → export. Accept when
at least 3 parts pass QA including deformation under the family clips, the owner approves them, and a
mixed real-plus-placeholder library imports and animates in Unity.

| Part | Profile | Length | Fit | Deformation QA (worst skeleton, real vs placeholder) |
|---|---|---|---|---|
| `meshy_insect_leg_a_v1` | `insect_leg4_articulated` | 0.90 m | modelled with a 110° knee, straightened (strain p99 1.41); radial 0.79; 1,939 tris with edge loops; `smooth` weights | crawler, hexapod, radial: p01 0.62 (0.71), p99 1.40 (1.61), flipped 0.06% (1.33%) |
| `meshy_frayed_arm_a_v1` | `limb3_plantigrade` | 0.85 m | 9° bend (strain p99 1.64); radial 0.94; frayed shoulder tendrils ride rigidly; 1,904 tris | biped, dragger, quadruped: p01 0.60 (0.41–0.64), p99 1.47 (1.74), flipped 0.54% (0.88%) |
| `meshy_tentacle_a_v1` | `tentacle8_flexible` | 1.60 m | S-curve straightened (strain p99 1.40); suckers ventral; 1,832 tris | dragger belly, the only accepting skeleton at 1.6 m: p01 0.84 (0.80), p99 1.17 (1.21), 0 flipped |
| `meshy_animal_skull_a_v1` | `head1_neck` | 0.30 m | rigid, centred on its bounding box, snout on +Z; 1,460 tris | biped, dragger, quadruped: rigid single bone, strain 1.000 |

The QA numbers are the worst over the reviewed skeletons, with the replaced placeholder in brackets; lower
p99 and higher p01 are better. The review covers all 8 baked clips (every second frame) plus IK stride
poses.

Status against acceptance:

- **≥3 parts pass QA:** done. `critter part list` shows the verdicts; the reports are in
  `work/review/parts/<id>/qa.json`.
- **Owner approval:** insect leg, skull and tentacle are `approved` (pipeline pin in
  `real.approved_pipeline`). The frayed arm is still `draft` pending its own pull request. Approving a
  part makes it generatable, which changes the golden recipes: re-run `recipe golden` and copy the
  goldens into the Unity package.
- **Unity import and animation:** EditMode tests assert `asset.albedo_png` is copied and bound to
  `_BaseMap`/`_MainTex` (smoothness 0.25) when a built library is present; they skip if none is.
  Until a part is approved, draft-review assemblies still use the placeholders. To look at a real
  part in Unity before approval, use the mixed creature `critter part review` exports
  (`work/review/parts/<id>/<skeleton>_mixed.fbx|.glb`): the first reviewed skeleton, with the real
  part on every accepting branch and all eight clips. The FBX has no texture path, so assign
  `<id>_albedo.png` from the built library.

Not done in M2, by design or for lack of time:

- **Connectors from the frayed stumps and shoulders.** Connectors are two-bone skinned joins with a
  different fit; this is the next real-part job.
- **The high-poly scout meshes** (250k–830k triangles). The decimation path exists, but none was
  reviewed. Two of the high-poly tentacles look straighter than `01a0c123` and are worth a try.
- **The coiled tentacle `01a0c11e`.** Reject or regenerate it; a loop can't be straightened.
- **Size coverage.** One length per part covers 0.8–1.25× of it. Tentacle appendage millimetres now
  have draft length variants of `01a0c123` at 0.55 / 0.70 / 1.05 m plus the existing 1.60 m (no 2.40 m
  appendage; serpentine `body` stays `tail`). Coiled `01a0c11e` is still rejected.
- **Weighting upgrade.** Voxel proxy + bone heat + Robust Skin Weights Transfer (from the tools
  research) remains the upgrade if review shows pinching. Axial weights plus loops passed QA, so it
  wasn't needed yet.

## M5 notes (Synaptic Sea)

- Threats move with `NavMeshAgent` (`acceleration = 999`, `angularSpeed = 999`). The runtime already handles instant turns.
- Set each threat's `move_speed` from the skeleton's published speeds. The in-repo test double uses `v_run_mps` (fallback 2.5); the game may also cap with `v_max_mps`.
- Keep the game's `Threat_<id>` root / `"Mesh"` child contract. `ThreatHierarchy.Wrap` (Runtime/Integration) builds that **without** referencing `NavMeshAgent`, so `CritterCrafter.Runtime` stays AI-free. The game owns `ThreatPlaceholderFactory`; this repo ships `FakeThreatFactory` in EditMode tests (`M5AdapterTests`), not a game-named factory.
- `CreatureGait` runs at execution order 1000, after agent movement.
- `AssemblyOptions.collision` is plumbed through the factory. The game's None vs SingleCapsule choice is unverified; tests assert the option, not a collider.
