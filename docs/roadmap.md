# Roadmap

The milestone list below was restored from the original plan (`git show 6379277:.claude/plans/floating-wiggling-sprout.md`, "Milestones & acceptance"). That plan file was later overwritten by the runtime-locomotion plan, so this page is now the one place that holds the milestones and their status. Live counts (skeletons, parts, tests) belong here too, so other docs can point at this page instead of repeating them.

**Live counts (2026-09-29):** 14 archetypes × 3 presets = 42 draft skeletons plus 15 seeded amalgams, none approved; 4 real parts, all `draft`; pytest collects 388 tests; the Unity EditMode suite has 41 cases (40 passed, 1 skipped for missing env vars).

## M0: Scaffold, schemas, placeholders, first Unity assembly — done

- Work: repo, schemas and validator, rng and generator with goldens, 8 branch templates, placeholder parts, UPM package, chirality probe.
- Accepted when: pytest passes, `schema validate` is clean, EditMode passes (100 seeds × 3 skeletons valid, C# byte-identical to the Python goldens, probe within 1e-4), and a TestProject screenshot shows creatures idling.

## M1: Skeleton library and clip generator — implemented, not accepted

- Work: 7 families, `skeleton vary`, the clip generator, review sheets.
- Accepted when: at least 40 skeletons across the 7 families pass QA, clips play without root motion, and **the owner approves the per-family review GIFs**.
- Status: 42 skeletons (the tentacle radial archetype was retired; the one-arm-one-leg dragger was added), all `draft`. That meets the "at least 40" target once approved. The owner has approved every family visually in locomotion captures, but no skeleton has been formally approved. The `skeleton review` receipt process predates runtime locomotion and may deserve simplifying.

## Inserted milestone: runtime locomotion ("option B") — merged

- Foot placement runs in Unity with Animation Rigging IK; the catalog `locomotion` block publishes speeds. Plan: stages S1–S4 in the old `floating-wiggling-sprout.md`.
- Merged in PR #1, with the dragger torso haul in #16 and the `limb3_brachial` arm profile in #17.
- Open: the dragger hand slip at instant turns, the turn shuffle, downhill slide (see `docs/locomotion.md` → Known limitations).

## M2: Real parts from existing scouts, no credits — implemented, waiting on the owner

- Work: import the scout meshes, then clean, fit, weight, QA, export.
- Accepted when: at least 3 parts pass QA including clip deformation and are **approved by the owner**, and a mixed real-plus-placeholder library imports and animates in Unity.
- Status: 4 parts pass QA (see `docs/handoff.md` → M2 status). Approval is pending, and the Unity albedo binding has not been run in Unity.

## M3: First paid Meshy batch — not started

- Work: torso, claw and maw, plus re-runs of rejected scouts, capped at 250 credits. **The owner confirms the cost before every paid call.**
- Accepted when: the ledger is within the cap, all 8 pilot parts are approved, a style sheet shows consistency, `assemble preview` renders exist for at least 10 creatures, and library v0.3.0 is published.

## M4: Expansion and bake — not started

- Work: at least 9 connector variants, at least 24 parts, at least 80 skeletons, species and tag affinity, `CreatureBaker`.
- Accepted when: each family yields at least 80 distinct creatures per 100 seeds, a baked creature takes at most 2 draw calls, and baked and runtime screenshots match.
- Status: connectors exist only as generated reference lofts; there are 42 of the 80 skeletons.

## M5: synaptic-sea-unity integration — not started

- Work: `BiomassThreatVisualFactory : ICreatureVisualFactory` plugged in at `ThreatPlaceholderFactory.Build`, keeping the `Threat_<id>` root and `"Mesh"` child. Event mapping: moved → velocity, attacked → attack, AI state → `SetState`, killed → death. Save recipe and seed; fall back to the old placeholder on failure; `drone_swarm` stays mechanical.
- Accepted when: the game's tests stay green, a PlayMode test spawns all 6 archetypes, a save/load round trip restores an identical recipe, and iso screenshots pass review.
- Preconditions not yet met: every skeleton and part is `draft`, so pool + seed generation yields nothing at runtime, and the pools list only the biped, quadruped and crawler families. The game reads `move_speed_mps` from the creature's own locomotion block (`AssembledCreature.Locomotion`; see `docs/locomotion.md`).

## In flight: locomotion thread (branch `feature/locomotion-thread`)

1. Docs: this roadmap and the count fixes.
2. Dragger hand slip at instant turns.
3. A one-arm-one-leg dragger (a 14th archetype, 42 skeletons): built, awaiting GIF review.
4. Per-creature gait and speed derived from the assembled build (`build-1`, branch `feature/build-speeds`): built, awaiting GIF review (`work/review/build-speeds`). The airborne-run follow-up (3b) is deferred.

## Closed pull requests

PRs #3–#15 (branches `execute-plan/0cb15005-*`) came from an automated overnight run and were closed unmerged on 2026-09-26. Some of them hand-approved parts, which the owner reserves for themself. They are references for ideas (skin-contract validation, slope-aware stance, a threat-hierarchy test double, connector baking), never branches to revive: they predate #16 and #17 and would conflict.
