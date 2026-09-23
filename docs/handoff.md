# Handoff: critter-crafter (2026-09-23)

## Current state (one paragraph)

The v3 skeleton foundation and **runtime foot-placement locomotion** are complete, tested and reviewed visually in Unity by the owner. They are on branch `feature/runtime-locomotion`, open as [PR #1](https://github.com/9thLevelSoftware/critter-crafter/pull/1) against `main` (10 commits, `7b48908`..`3084c72`); `main` is still at M0 (`6379277`). The library has **13 archetypes × 3 presets = 39 draft skeletons**, all passing QA. Every skeleton publishes walk, run and max speeds, and all 39 reach the game's 2.5 m/s. **Next milestone: M2** (real parts from the existing Meshy scout meshes, no credits). **After that: M5** (Synaptic Sea integration).

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
| Gitignored build output | `work/` (reviews, logs), `library/` (built library), `dist/` |

## Everyday commands

```powershell
uv run pytest                                   # 254 tests, ~5 min with Blender (-m "not blender": ~15 s)
uv run critter skeleton vary --force            # regenerate the 39 draft skeletons from archetypes.py
uv run critter schema validate
uv run critter recipe golden                    # writes tests/golden_v3/{catalog,recipes,locomotion}.json
Copy-Item tests/golden_v3/*.json unity/com.ninthlevelsoftware.crittercrafter/Tests/Editor/GoldenV3/
uv run critter library build                    # ~5 min: Blender bakes every skeleton/part -> library/
uv run critter skeleton qa                      # motion + export + locomotion QA for all skeletons
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
| Docs | `docs/locomotion.md` (model and integration), `docs/foundation-v3.md`, `docs/frame.md` |

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

## Open issues and follow-ups

- **Turns and slopes:**
  - a brief shuffle after instant 180° turns;
  - a few centimetres of downhill foot slide on the 20° ramp;
  - the first stride from standing can drag a foot (tests exclude the first second);
  - see `docs/locomotion.md` → Known limitations.
- **Dragger refinement.** The owner said "refine later". Placeholder parts make the hands read as feet and the head small; real parts will help. Possible tuning: `dragSurge`, `dragHeaveDeg`, `dragRollDeg` on `CreatureGait`, and the arm stance in `_dragger`.
- **Pools** (`data/pools/pools.json`) still list only the biped, quadruped and crawler families. Add the new families when the game needs them; this changes the golden recipes.
- **All 39 skeletons are still `draft`.** `critter skeleton approve` needs a passing review receipt from `critter skeleton review`. This process predates runtime locomotion and may deserve simplifying, given the owner's no-governance preference.
- **Variety** is still 13 body plans × 3 presets, well below M4's target of at least 80 skeletons. The earlier review suggested optional branches (tails, dorsal parts, extra arms) and wider seeded proportions.

## Next: M2 (real parts, no credits)

From the plan:

- import the scout meshes in `synaptic-sea-asset-archive` (`procedural-biomass-assembly/artifacts/{meshy_mcp_scout,biomass_frayed_stumps}`);
- then clean → fit → weight → QA → export.

Recommended tooling from the tools research:

- **Weighting:** voxel-remesh proxy plus Blender bone-heat weights, then **Robust Skin Weights Transfer** (the Blender port `sentfromspacevr/robust-weight-transfer`, which has 5.2 forks) onto the real Meshy mesh.
- **Biomass joins and a watertight proxy:** Blender 5 SDF volume-grid nodes, or `manifold3d`.
- **Checks and LODs:** glTF-Validator and meshoptimizer.

Accept M2 when:

- at least 3 parts pass QA, including deformation under the family clips;
- the owner approves them;
- a mixed real-plus-placeholder library imports and animates in Unity.

## M5 notes (Synaptic Sea)

- Threats move with `NavMeshAgent` (`acceleration = 999`, `angularSpeed = 999`). The runtime already handles instant turns.
- Set each threat's `move_speed` from the skeleton's published speeds.
- Keep the game's `Threat_<id>` root / `"Mesh"` child contract. The adapter plugs in at `ThreatPlaceholderFactory.Build`.
- `CreatureGait` runs at execution order 1000, after agent movement.
