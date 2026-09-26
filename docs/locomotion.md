# Runtime locomotion

Creatures move with **runtime foot placement**: the game moves the creature (NavMeshAgent, character controller, script) and the Unity package steps its feet to match. Walk and run are not baked travel clips. Feet stay planted in the world at any speed, follow slopes, and re-step after the instant heading changes of an agent with a huge `angularSpeed`.

Idle, stun, telegraph, attack, hit and death stay baked clips. Walk and run are one-cycle **overlay** clips (upper body, head, tail, body undulation) whose time is driven by the gait clock, so they stay in step with the footfalls.

## Modes

Every compiled skeleton carries a `locomotion` block (catalog frame, metres). Its `mode` is one of:

| Mode | Used by | Runtime behaviour |
|---|---|---|
| `legs` | bipeds, quadrupeds, hexapods, crawlers, the radial walker, legged serpentines | Scheduled stepping plus Animation Rigging IK per leg |
| `legs` with `gait: "drag"` | draggers | The torso slides on the ground; the arms reach, plant and haul it, with a visual lurch and heave |
| `slide` | limbless serpentines | No feet; the baked undulation advances one cycle per `travel_per_cycle_m` of ground travel |

## Speeds (the game should read these)

Each skeleton publishes `v_walk_mps`, `v_run_mps` and `v_max_mps`:

- **Legs.** Walk sits near Froude number 0.25 and run near 1.0, from hip height, capped by leg stroke and a step-rate limit that scales with leg size (`cadence_max_hz`, small legs step faster).
- **Drag.** Full-stroke pulls at about 1 pull per second when walking and 1.8 when running.
- **Slide.** 1 and 2 undulations per second.

Above `v_max_mps` the feet slide, reported as `overspeed`. Use `v_max_mps` or `v_run_mps` as the threat's move speed rather than forcing one speed on every body plan. All 39 current skeletons reach 2.5 m/s (the current Synaptic Sea threat speed) within their limits.

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
  - sets body height, pitch and roll from the planted feet, plus the drag lurch;
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
  Unity.exe -batchmode -projectPath unity/TestProject `
    -executeMethod CritterCrafter.Editor.LocomotionCapture.CaptureFromCommandLine `
    -critterLibrary library/biomass_core-v0.2.0 -critterSkeletons hexapod_compact_insect_balanced_v3 `
    -critterOut work/review/locomotion -critterSpeeds walk,run,2.5
  ```

- **`critter skeleton qa`** checks every skeleton's locomotion block: speed bands, support at the walk and run duty factors, step rate and stance stroke at the published speeds.
- **`critter recipe golden`** writes `tests/golden_v3/locomotion.json`. The Unity `StepPlannerGoldenTests` requires the C# `StepPlanner` to match the Python reference (`src/critter_crafter/locomotion/stepper.py`) within 1e-6.
- **The Unity `RuntimeLegsPlantFeetAtGameSpeed` Play Mode test** runs one skeleton per legged family at 2.5 m/s and checks IK residual, planted slip (after five warmup frames), support, step rate and the overlay state. `RuntimeInstantTurnSettlesWithoutShuffle` covers the Path 180° waypoint (snap/replant frame excluded from slip). `RuntimeAttackReleasesIkAndKeepsSupport` checks that telegraph/attack releases the attack-branch IK while other support feet stay planted.

## Known limitations

- **On a 20° ramp,** downhill feet can reach the end of their reach and slide a few centimetres.
- **Starting from standing to full speed in one frame** can clamp a foot on the first frame; tests exclude five warmup frames. A `warmup_frames = 0` run may still see that single-frame residual. Plantigrade quadrupeds at 2.5 m/s (legs already near max reach at home, `min_support` 2) can still show ~5–6 cm planted slip in the first second.
- **Draggers use placeholder parts,** so the hands read as feet and the head is small. Real parts (M2) will improve the read.
