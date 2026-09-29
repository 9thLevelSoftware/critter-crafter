# Runtime locomotion

Creatures move with **runtime foot placement**: the game moves the creature (NavMeshAgent, character controller, script) and the Unity package steps its feet to match. Walk and run are not baked travel clips. Feet stay planted in the world at any speed, follow slopes, and re-step after the instant heading changes of an agent with a huge `angularSpeed`.

Idle, stun, telegraph, attack, hit and death stay baked clips. Walk and run are one-cycle **overlay** clips (upper body, head, tail, body undulation) whose time is driven by the gait clock, so they stay in step with the footfalls.

## Modes

Every compiled skeleton carries a `locomotion` block (catalog frame, metres). Its `mode` is one of:

| Mode | Used by | Runtime behaviour |
|---|---|---|
| `legs` | bipeds, quadrupeds, hexapods, crawlers, the radial walker, legged serpentines | Scheduled stepping plus Animation Rigging IK per leg |
| `legs` with `gait: "drag"` | draggers | The torso lies on the ground and only the planted hands move it: it rests while a hand grips, lunges through the pull, and stops (see [Dragging](#dragging)) |
| `slide` | limbless serpentines | No feet; the baked undulation advances one cycle per `travel_per_cycle_m` of ground travel |

## Speeds (the game should read these)

Each skeleton publishes `v_walk_mps`, `v_run_mps` and `v_max_mps`:

- **Legs.** Walk sits near Froude number 0.25 and run near 1.0, from hip height, capped by leg stroke and a step-rate limit that scales with leg size (`cadence_max_hz`, small legs step faster).
- **Drag.** Full-stroke pulls at about 1 pull per second when walking and 1.8 when running.
- **Slide.** 1 and 2 undulations per second.

Above `v_max_mps` the feet slide, reported as `overspeed`. Use the creature's `move_speed_mps` (see [Per-creature speeds](#per-creature-speeds-build-1)) as the threat's move speed rather than forcing one speed on every body plan; the skeleton's own `v_run_mps` is the fallback. All 42 current skeletons can reach 2.5 m/s (the current Synaptic Sea threat speed) within `v_max_mps` (the lowest is 2.53), but 26 of them have `v_run_mps` below 2.5, so forcing 2.5 m/s runs those bodies above their natural run speed.

## Per-creature speeds (`build-1`)

The skeleton's speeds describe a bare body plan. A creature assembled on it gets its own block from
`locomotion/build.py` (Python) and `Runtime/Generation/CreatureLocomotion.cs` (C#, `AssembledCreature.Locomotion`;
the gait follows `AssembledCreature.GaitBlock`). Leg geometry, duties, phases and strokes are the skeleton's, so
the stroke limit still keeps planted feet from sliding. Only the speeds, `cadence_max_hz` and a new `build` object change.

- **Mass** of each fill = density × shape × branch length × branch girth × (part thickness `dimensions_m[1]` × length scale × girth scale). Density (kg/m³) and shape by part category: limb 1050 / .60, core 1000 / .70, head 1100 / .52, tail and appendage 1000 / .55. Connectors add no mass. `_scales` sizes a part to its branch, so a part moves the build only through its thickness and its category; most of the spread comes from the skeleton.
- **Build factors:** the mass-weighted centre of mass (from the catalog `segments`, each branch's neutral-pose centroid); `muscle_fraction` (locomotor-limb mass ÷ total); `load_imbalance` (distance from the COM's ground point to the centroid of the leg homes, ÷ (largest home distance from that centroid + .05 m); 0 for drag and slide); `arm_fraction` (share of limb fills bound to `limb3_brachial`).
- **Performance** `P = clamp(sqrt(muscle/.45) · (1 − .4·min(1, imbalance)) · (1 − .25·arm) · (60/mass)^.1, .4, 1.8)`. Slide uses the mass term alone; no locomotion (`none`) keeps P = 1.
- **Legs:** `cadence' = clamp(cadence_max · P, 2, 6)`, `v_max' = min(cadence', (1 − duty_run) / .12 s) · .9 · stroke / duty_run` (a swing shorter than about 4 frames cannot land its foot, which matters for high-duty gaits such as the tripod), and `v_run' = max(min(1, cap), min(P² · sqrt(2.2 g h), cap))` where `cap = .9 · min(v_max', v_froude)`. `v_froude` is the speed at which the planner's Froude-limited stride reaches that same limited cadence, so the run never overspeeds. `v_walk' = min(.5 · sqrt(g h) · sqrt(P), .5 v_max', .6 v_run')`.
- **Drag:** `v_max' = stride_run · cadence'`, `v_run' = min(stride_run · 1.8 · P², v_max')`, `v_walk' = min(stride_walk · P, .5 v_max', .6 v_run')`. **Slide:** the same with `travel_per_cycle_m` in place of the stride, at 1 and 2 undulations per second.
- Output: `build {model, mass_kg, com_m, muscle_fraction, load_imbalance, arm_fraction, performance}` and `move_speed_mps` (= `v_run_mps`). Golden: `tests/golden_v3/creatures.json` (the reference recipe of every skeleton, then every pool at seeds 1–10 with the Meshy parts approved in memory), matched by `tests/test_build.py` and `CreatureLocomotionGoldenTests`.

**Contract for the game (M5).** Chase at `move_speed_mps`, patrol at `v_walk_mps`, never exceed `v_max_mps`. Across the golden the run speeds span about 0.3–5.3 m/s. Quadrupeds and bipeds stay near 2–4 m/s because a trot plants half its feet; a faster, airborne run (lower `duty_run` and a minimum-support setting that `CanLift` respects) is deferred.

## Legs block fields

| Field | Meaning |
|---|---|
| `legs[]` | IK-able legs: `chain_bones`, `solver` (`two_bone` for limb3, `hinge4` for insect legs, `chain` otherwise), `home_m` (neutral contact), `stance_shift_m`, `hip_m`, `reach_m`, `stroke_m`, `clearance_m`, `walk_phase` / `run_phase` (fraction of the cycle), `support` |
| `duty_walk`, `duty_run` | Fraction of the cycle a foot is planted |
| `min_support` | Planted supports that must remain when a foot lifts (0 for draggers) |
| `usable_stroke_m` | Smallest leg stroke, used to cap the stride |
| `hip_height_m`, `leg_length_m`, `cadence_max_hz` | Inputs to the speed model |
| `attack_branch_id` | Leg released from IK during telegraph and attack, so the baked strike plays |
| `body_on_ground`, `body_pivot_m` | Drag only: the torso stays grounded and heaves about its rear |

### Gait patterns

- **Quadrupeds** walk in lateral sequence (hind L, fore L, hind R, fore R, a quarter cycle apart) and trot when running.
- **Hexapods** use a tripod gait.
- **Eight-legged crawlers** use an alternating tetrapod gait.
- **Bipeds and draggers** alternate left and right.
- **The radial walker** walks with a wave around its ring and runs with alternating tripods.

## Unity

The package depends on **Animation Rigging** (a core package in Unity 6). `CritterCrafter.Locomotion` registers an assembly hook, so `CreatureAssembler.Assemble` adds the rig and the gait automatically:

- **`CreatureGait`** (on the creature root) does the following:
  - measures the real planar velocity;
  - advances the gait clock;
  - schedules steps, and forces early steps when a foot overruns its reach (turns);
  - raycasts the ground (`AssemblyOptions.groundMask`);
  - sets body height, pitch and roll from the planted feet (draggers: the haul-driven torso instead);
  - smooths the visible body yaw after instant turns (`bodyTurnRateDeg`);
  - drives the Animator's `Speed`, `Gait` (0 = walk, 1 = run) and `GaitPhase`.
- **`ikWeight`** at 0 falls back to the overlay with neutral legs, for LOD or off-screen creatures.
- **IK solvers:**
  - limb3 legs use `TwoBoneIKConstraint` with a knee hint.
  - Insect legs yaw the coxa with a `MultiAimConstraint`, then solve femur and tibia with `TwoBoneIKConstraint`. The tarsus keeps its neutral angle relative to the yawed coxa frame.
  - IK targets sit on the ankle; the foot contact is offset from it in body space.
- **`CreatureMotion`** keeps its game-facing API (`SetVelocity`, `SetState`, `PlayAttack`, `PlayHit`). Leg weights follow its state: the attack leg is released during telegraph and attack, and every leg fades out at death.

Game integration: move the creature root with the agent (`updatePosition = true`) and leave the feet to `CreatureGait`, which should run after movement (it uses execution order 1000). No root motion is applied.

## Review and verification

- **Capture a skeleton on the review course** (flat ground, a 20° ramp, a plateau, an instant 90° turn, a stop, an instant 180° turn in place). This runs in real Play Mode with the game's orthographic isometric camera and writes frames, `metrics.json` and per-frame and per-leg CSVs:

  ```powershell
  $root = (Get-Location).Path   # repo root; -critterLibrary and -critterOut resolve against unity/TestProject
  Start-Process "F:\Unity\6000.6.0f1\Editor\Unity.exe" -Wait -PassThru -ArgumentList '-batchmode','-projectPath','unity/TestProject',
    '-executeMethod','CritterCrafter.Editor.LocomotionCapture.CaptureFromCommandLine',
    '-critterLibrary',"$root/library/biomass_core-v0.2.0",'-critterSkeletons','hexapod_compact_insect_balanced_v3',
    '-critterOut',"$root/work/review/locomotion",'-critterSpeeds','walk,run,2.5'
  ```

- **`critter skeleton qa`** checks every skeleton's locomotion block: speed bands, support at the walk and run duty factors, step rate and stance stroke at the published speeds.
- **`critter recipe golden`** writes `tests/golden_v3/locomotion.json`. The Unity `StepPlannerGoldenTests` requires the C# `StepPlanner` to match the Python reference (`src/critter_crafter/locomotion/stepper.py`) within 1e-6.
- **The Unity `RuntimeLegsPlantFeetAtGameSpeed` Play Mode test** runs one skeleton per legged family at 2.5 m/s and checks IK residual, planted slip (after a one-second warmup), support, step rate and the overlay state. For the dragger it also checks that the torso travels in hauls: it nearly stops between pulls (under 0.15× its mean speed) and lunges through them (over 1.8×).

## Dragging

The root (the agent) is the travel intent and still moves at an even speed. The dragger's torso does not follow it evenly; its lead over the root comes from the arms:

- Each haul is half a gait cycle, one per arm, and moves the torso exactly as far as the root travels in that time, so the two never drift apart.
- **Grip** (`dragGrip`, default 0.2 of the haul, and never less than the hand-over overlap): the new hand has just planted and the trailing hand has not lifted yet. The torso rests.
- **Pull:** the torso lunges toward the planted hand on a smoothstep, peaking at about twice the average speed. The chest lifts (`dragHeaveDeg`), and the shoulders roll (`dragRollDeg`) and turn (`dragYawDeg`) toward the pulling arm.
- **Settle** (`dragSettle`, default 0.15): the torso comes to rest before the next grip.
- **Stopping:** the torso eases onto the root over `dragStopSettle` (0.3 s).
- **Arms, not legs:** the dragger arm branches bend with the elbow pointing up, out and back (branch `up` in `_dragger`), and the forearm and wrist twist so the palm lies flat. Before, the elbows pointed straight up and read as knees.

- **One arm, one leg** (`dragger_arm_leg_crawler`, asymmetric): the single arm `arm_L` pulls from ahead of its shoulder and the single leg `leg_R` pushes from behind its hip, half a cycle apart. The catalog marks each drag leg `drag_drive` = `pull` or `push`. A pull stroke runs from the arm's far reach back to the shoulder plane; a push stroke runs from the leg's far reach forward to the hip plane, and a pushing leg lifts less (0.18 of its reach against 0.38 for a reaching arm). The torso lunges on the push too, but heaves the chest less (`dragPushHeave`, 0.5 of a pull). The leg is the shorter stroke, so it sets the creature's speed (the usable stroke is the minimum over the limbs). The leg is a `limb3_digitigrade` limb with a heel-up foot and a `hand` contact, the same clearance and ground band a sloped hand gets: a flat `foot` contact leaves only 7.5 mm of clearance and the foot's rounded tip dipped through the floor (5–36 mm depending on preset, against a 5 mm limit).

An agent's instant turn can leave a gripping hand out of reach (the torso yaws at a limited rate and lunges along the new heading). The hand must then re-grip by stepping. For bodies that lie on the ground (`body_on_ground`) the torso carries the weight, so a strained hand may re-step at once even while the other hand is in the air: the swing cap in `CanLift` and the one-group-at-a-time rule in `LiftGroup` do not apply. Both hands may be off the ground for a few frames after a sharp turn. Without this the re-step was refused, the reach clamp rewrote the plant point every frame and the hand slid. `LibraryTests.DraggerHandsHoldThroughInstantTurns` sweeps four haul phases at walk, run and 2.5 m/s and fails on any refused re-step. The recorder reports `max_plant_rewrite_m`, `clamped_planted_frames`, `lift_blocked_frames` and the slipping leg and frame, and `legs.csv`/`frames.csv` carry `clamped`, `lift_blocked`, `plant_rewrite_m`, `yaw_lag_deg` and `surge_m`.

The torso is at most about 0.2 of one haul (≈ 0.12 m at walk, ≈ 0.14 m at 2.5 m/s) ahead of or behind the root, so colliders on the root sit slightly off the torso.

The grip-pull-stop rhythm reads best at walk speed, about one haul per arm per second. At 2.5 m/s a dragger hauls almost twice as often and it reads as a scramble. If the crawl should look deliberate in the game, use the dragger's own `v_walk_mps` or `v_run_mps` as its move speed rather than the shared threat speed.

## Known limitations

- **Instant agent turns** cause a brief shuffle while re-stepping groups catch up with the smoothed body yaw.
- **Dragger hands after instant turns** can still be dragged for one frame (a centimetre or two; at most 3 cm in the test sweep) between the reach clamp and the re-step, because the clamp is only seen after the body has moved. Before the fix in "Dragging" below they slid 4–20 cm.
- **On a 20° ramp,** downhill feet can reach the end of their reach and slide a few centimetres.
- **Starting from standing to full speed in one frame** can drag a foot during the first stride. Tests allow a one-second warmup.
- **Draggers use placeholder parts,** so the hands read as feet and the head is small. Real parts (M2) will improve the read.
