# Option B: runtime procedural locomotion with Unity Animation Rigging

## Context
The review of the v3 foundation (checkpoint `7b48908`) found that the baked walk and run clips are far too slow for the game.

**The mismatch**
- The clips move at **0.03–0.26 m/s**.
- Synaptic Sea threats move at **2.5–2.8 m/s**.
- `PlaybackRate` is clamped to 1.5×, so feet slide 10–50× relative to the ground.

**Three causes**
1. `_clip_frames` floors every walk cycle at 48–60 frames.
2. `_fit_motion_to_reach` caps stride at 0.12 × leg length.
3. Legs are about 1.6× hip height, so the foot has almost no reach at the ground.

**Related problems**
- Quadrupeds pace instead of walking.
- Fore and hind legs bend the same way.

**Your decisions**
- **Option B:** runtime foot placement.
- **IK through Unity Animation Rigging.**
- **Speed comes from the catalog.** Each skeleton publishes its natural walk, run and max speed, and the game's M5 adapter uses that as the threat's `move_speed`. Small creatures may step faster, with a step-rate limit scaled to leg size.

**What stays and what changes**
- Baked clips stay for idle, stun, telegraph, attack, hit and death.
- Walk and run become upper-body overlay clips, phase-locked to the runtime gait clock.

**Delivery strategy: one vertical slice first.**
- Deliver the hexapod family end-to-end first: data, planner, Animation Rigging IK, and a TestProject demo at speed, with a GIF for you to review.
- Then roll it out to the other families, and only then build the wider verification.
- This answers "does runtime stepping look right under the iso camera?" before we invest in infrastructure around it.

## Design

### IK: Unity Animation Rigging
- **Packaging:**
  - New asmdef `CritterCrafter.Locomotion` (under `Runtime/Locomotion/`) references `Unity.Animation.Rigging`.
  - `package.json` adds `com.unity.animation.rigging`. It's a core package in 6.x, and 1.4.x also works on 6000.0.
  - `CritterCrafter.Runtime` stays reference-free.
- **Rig build at assembly time**, in a new `LocomotionRigBuilder`, called from the assembler through a small `ICreatureRigHook` so Runtime doesn't reference Locomotion:
  - Add a `RigBuilder` on the Animator GameObject, and one `Rig` child named `LocomotionRig`.
  - `limb3` legs get a `TwoBoneIKConstraint`: root = b0, mid = b1, tip = b2, a target, and a hint placed from `gait.bend_pole_m`. Target rotation weight is partial, so feet follow the ground normal.
  - `insect_leg4` legs and single-contact `tentacle8` legs get a `ChainIKConstraint`, root b0 to the contact bone.
    - ChainIK has no pole and no joint limits. Its FABRIK solve starts from the animated pose, which is the neutral bent stance, and that seeds the correct bend side.
    - A `JointLimitClamp` then runs in `LateUpdate` and clamps each bone to the binding-profile `limits_deg`, reusing `SkeletonPose.CanonicalBoneBasis` in `Runtime/Assembly/AssembledCreature.cs`. The planner keeps targets within reach, so the clamp should rarely activate. The PlayMode tests track how often it does.
  - Call `animator.Rebind()` and then `rigBuilder.Build()` after the constraints are added.
  - `body`/`sliding` contacts get no IK. They only follow the ground for root height.
- **Weights:**
  - Each constraint's weight is driven by the gait component, per state (see below).
  - The rig weight drops to 0 for the LOD/off-screen toggle.

### Gait model (a shared spec; the C# port is the runtime, Python is the reference)
- **Speeds come from the catalog, per skeleton:**
  - `v_walk` at Froude number ≈ 0.25, and `v_run` at Froude ≈ 1.0, from hip height.
  - `v_max` bounded by reach and by a step-rate cap scaled to leg size: `cadence_max = clamp(2.2 / sqrt(L), 2.5, 6.0)` Hz.
  - My estimate: with crouched legs (hip about 0.88 L), running at 2.5 m/s takes about 1–2.6 steps per second. Splayed insect legs and short serpentine legs may need 4–6. The catalog publishes the real values once the anatomy is fixed.
- **Gait clock and stride:**
  - Stride `λ = h·2.3·(v²/gh)^0.3`, clamped so the stance stroke `λ·duty` is at most 0.9 × usable reach. Past that clamp, cadence rises up to `cadence_max`.
  - The duty factor blends from the walk support value to about 0.45 between `v_walk` and `v_run`.
  - Per-leg phase offsets blend from a walk pattern to a run pattern:
    - quadruped: lateral-sequence walk, then trot;
    - hexapod: tripod;
    - octopod: alternating tetrapod;
    - biped: 0/0.5;
    - radial: wave.
- **Scheduled stepping:**
  - Stance: the foot is locked at its world plant point.
  - Swing start: land at the predicted home position at touchdown plus `v·duty·T/2`. A downward raycast (`AssemblyOptions.groundMask`) gives height and normal.
  - Swing path: smoothstep in the plane plus a `clearance·sin²` lift.
- **Turning.** The game agent uses `acceleration = 999` and `angularSpeed = 999`, so heading changes happen instantly.
  - The main turn mechanism is a **reach-overrun re-step**: any planted foot outside its reach disc, or whose strain from home exceeds a threshold, is queued for an early step.
  - At most half the supports may be in swing at once, and support-group order is kept.
  - Idle turn-in-place uses the same rule.
  - Teleport (more than 2 m per frame) snaps all feet to home.
- **Body:** a root height offset from the planted-foot height error, plus pitch and roll from a plane fit to the planted feet. Both are clamped and smoothed.
- **Per-state behaviour:**
  - Telegraph, attack, hit and stun keep the support legs planted at weight 1.
  - The attack effector branch (from `attack_plan`) goes to weight 0 so the baked strike plays.
  - Death fades all weights to 0 over the first 25% of the clip.

### Animator (fixes idle freezing)
- A **Locomotion** state with a 1-D blend of the walk and run overlays on `Gait` (0–1), with `timeParameterActive = GaitPhase`.
- A separate **Idle** state plays the baked idle on its own clock.
- The transition from Idle to Locomotion happens when `Speed > ε`, with about 0.15 s cross-fade in each direction.
- Action states are unchanged.
- `CreatureMotion` sets `Speed`, `Gait` and `GaitPhase`. The `PlaybackRateForSpeed` code becomes obsolete shims for one version.

### Data and catalog (Python)
- **Anatomy fixes in `skeletons/archetypes.py`:**
  - Keep the hip heights and shorten the legs to `hip / 0.88`, so the recorded `silhouette.height_m` becomes accurate.
  - Flip the fore-limb bend (quadrupeds, draggers, serpentine legs).
  - Give `_paired_legs` per-pair phase offsets (lateral-sequence walk).
  - Fan out the crawler legs.
  - Topology variety stays a separate follow-up.
- **A `locomotion` block per skeleton**, compiled in `library/catalog.py`:
  - `leg_length_m`, `hip_height_m` and `usable_reach_m`, from neutral-pose forward kinematics. The FK comes from the `_contact_height` code in `archetypes.py`, moved into a shared `skeletons/kinematics.py`.
  - `v_walk`, `v_run`, `v_max` and `cadence_max`.
  - Per-branch walk and run phases, and duty factors.
  - `travel_per_cycle_m` for serpentine waves.
  - Mirrored in `CatalogData.cs`, the schemas and `LibraryImporter` validation.
- **New `src/critter_crafter/locomotion/stepper.py`:** the reference planner. It's pure and deterministic, and the C# `StepPlanner` is written in `double`/`System.Math` to match it.
- **Overlay clips in `skeletons/motion.py` and `blender/ops_skeleton.py`:**
  - Walk and run become one normalized cycle each (30 frames), with locomotor chains at neutral and no leg IK.
  - Body bob, sway, arm swing and tail are phased to the walk pattern. Serpentine waves are baked with `travel_per_cycle_m`.
  - Remove the 0.12·L stride cap and the 48/60-frame floor.
  - The idle and action clips, and their QA, are unchanged.

### Unity files
- **New in `Runtime/Locomotion/`:**
  - `CritterCrafter.Locomotion.asmdef`
  - `CreatureGait.cs`: the MonoBehaviour that owns the clock, planner, targets, weights and body adjustment
  - `StepPlanner.cs` and `GaitClock.cs`: pure C#
  - `LocomotionRigBuilder.cs`
  - `JointLimitClamp.cs`
- **Modified:**
  - `Runtime/Assembly/CreatureAssembler.cs`: rig hook, `groundMask`
  - `Runtime/Animation/CreatureMotion.cs`
  - `Runtime/Data/CatalogData.cs`
  - `Editor/AnimatorBuilder.cs`: Idle and Locomotion states, `GaitPhase`
  - `Editor/LibraryImporter.cs`
  - `package.json`

## Order of work
1. **S1, hexapod slice:**
   - Anatomy fixes for hexapods and the locomotion block for all skeletons.
   - `stepper.py`.
   - Overlay walk/run bake.
   - `StepPlanner`, `CreatureGait`, `LocomotionRigBuilder`, and the Animator changes.
   - A TestProject demo: NavMeshAgent at the catalog's `v_run` and at 2.5 m/s, on flat ground plus a 20° ramp, with the iso camera (offset 16,18,16, size 22).
   - Capture a GIF with `ReviewCapture` for your review. **Visual gate.**
2. **S2, all families:**
   - Anatomy fixes for the rest.
   - Quadruped, biped and radial patterns.
   - Draggers with hand contacts plus the belly slide.
   - Serpentines with wave overlay and paired legs.
   - A GIF per family. **Visual gate.**
3. **S3, verification:**
   - Planner golden tests on continuous quantities (stride, cadence, duty, phases and home positions for a given velocity and yaw rate) and on per-step landing targets for an exact input phase. No long simulated traces, because step events amplify floating-point drift.
   - Stepper QA in `skeletons/qa.py`: reach, support, and cadence ≤ `cadence_max`.
   - Unity PlayMode locomotion tests.
   - Blender review driven by the stepper.
4. **S4:** docs (`docs/locomotion.md`, `foundation-v3.md`), with a commit per stage.

## Verification
- **Python:** `uv run pytest`, the full suite including the Blender tests.
- **Unity EditMode:**
  - `StepPlanner` matches the Python golden values (continuous quantities and per-step targets) within 1e-6.
  - Rig builder: correct constraint type and bones per branch template.
  - Clamp keeps every bone within limits.
- **Unity PlayMode:** for each skeleton, assemble it and drive it at `v_walk`, `v_run` and 2.5 m/s on straight, circle and instant 90°-turn paths for 10 s. Check:
  - planted-foot world slip < 2 cm;
  - IK residual < 1 cm;
  - no foot more than 5 mm below the ground collider;
  - support ≥ the minimum;
  - the joint-clamp activation rate is reported.

  Repeat on the ramp and a step.
- **Visual:** a per-family GIF from the TestProject at game speed under the iso camera, approved by you.
