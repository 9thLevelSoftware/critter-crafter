# Roadmap

The milestone list below was restored from the original plan (`git show 6379277:.claude/plans/floating-wiggling-sprout.md`, "Milestones & acceptance"). That plan file was later overwritten by the runtime-locomotion plan, so this page is now the one place that holds the milestones and their status. Live counts (skeletons, parts, tests) belong here too, so other docs can point at this page instead of repeating them.

**Live counts (2026-10-04):** 14 archetypes × 3 presets = 42 draft anchors, 28 seeded variants and 21 seeded amalgams (91 skeletons), none approved; 4 real parts, all `draft`; 9 authored native-SDF connector variants, all `draft`. All 91 skeletons pass eight-clip QA; all nine connectors pass unchanged deformation QA. The current build, tests, capture measurements and approval gates are recorded in `docs/handoff.md`.

## M0: Scaffold, schemas, placeholders, first Unity assembly — done

- Work: repo, schemas and validator, rng and generator with goldens, 8 branch templates, placeholder parts, UPM package, chirality probe.
- Accepted when: pytest passes, `schema validate` is clean, EditMode passes (100 seeds × 3 skeletons valid, C# byte-identical to the Python goldens, probe within 1e-4), and a TestProject screenshot shows creatures idling.

## M1: Skeleton library and clip generator — implemented, not accepted

- Work: 7 families, `skeleton vary`, the clip generator, review sheets.
- Accepted when: at least 40 skeletons across the 7 families pass QA, clips play without root motion, and **the owner approves the per-family review GIFs**.
- Status: 91 skeletons (42 curated anchors, 28 seeded variants and 21 amalgams), all `draft`, pass eight-clip QA. The owner has approved the original families visually in locomotion captures, but no skeleton has been formally approved; enhancement geometry and captures still require owner review.

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

## M4: Expansion and bake — partially implemented, not accepted

- Work: at least 9 connector variants, at least 24 parts, at least 80 skeletons, species and tag affinity, `CreatureBaker`.
- Accepted when: each family yields at least 80 distinct creatures per 100 seeds, a baked creature takes at most 2 draw calls, and baked and runtime screenshots match.
- **Status:** nine native-SDF connector variants pass full deformation QA; the skeleton target is met (91), but the 24-part target is not. Blender portable baking preserves the eight actions; current real Unity GUI proof records 15 live versus 1 baked body submission in each isolated color, shadow and depth pass, with zero pose error in the portable comparison. Bake proof is complete, but M4 is not accepted: functional-search acceptance, 24 parts and owner review/publication remain open. The earlier distinctness result (`critter recipe sweep`: 100 of 100, serpentine 89) comes from placeholder reference parts, not real-part diversity.

## M5: synaptic-sea-unity integration — owner-gated

- **Owner decision:** package enhancements proceed, but game cutover is blocked until the intended gameplay checkout/revision provides its actual spawn, visual factory/view, movement/event and save implementations. Public main `8dcc95c10ab5e08658546319f51f49b4abd256fc` has no referenced `ThreatPlaceholderFactory` or live threat runtime; the inspected cursor branches are model lineage, not an authorized integration target. Do not invent the missing gameplay subsystem or revive those branches.
- Retained work: one consumer-owned `ICreatureVisualFactory`, preserving `Threat_<id>` root/collider and `"Mesh"` child, game-owned movement/no root motion, catalog speeds for moving biomass, authoritative saved recipe plus seed and exact library-version lookup. Failures retain the existing game placeholder; `drone_swarm` stays mechanical and `hull_tendril` stays anchored at its configured zero speed. Actual source wiring must distinguish outgoing attacks from received hits and preserve terminal death presentation.
- Accepted only after actual game tests, Play Mode spawns of `biomatter_swarm`, `puppet_corpse`, `stalker`, `mimic`, `hull_tendril`, `drone_swarm`, identical-recipe save/load round trip and owner-reviewed iso screenshots. Package mocks cannot release this gate.

## Merged since the locomotion thread

- **#19-#20:** dragger turn fix, the one-arm-one-leg dragger, per-creature speeds (`build-1`), the radial gait fix.
- **#21-#26:** anatomy traits instead of family checks, legs on the right spine bones, clips that move with IK-pinned feet and a collapsing death, the seeded **amalgams** (hauled, walker, slither; 15 committed), optional tails/spines/arms/antennae/crowns, and 28 seeded variants (85 skeletons).
- **#28-#34:** runtime robustness: the reach clamp no longer lifts planted feet (the body used to climb after them), earlier strain detection, reach-gated turning, slope pitch, death/limp fixes, and Play Mode coverage for turns, ramps, starts and death.
- **Consumer readiness (in review):** exception-safe factory, the light `--owner` approval path, pools for every family, renderer bounds, the generator-identity version gate, package docs, the albedo test, CI and a slim release zip.

## Closed pull requests

PRs #3–#15 (branches `execute-plan/0cb15005-*`) came from an automated overnight run and were closed unmerged on 2026-09-26. Some of them hand-approved parts, which the owner reserves for themself. They are references for ideas (skin-contract validation, slope-aware stance, a threat-hierarchy test double, connector baking), never branches to revive: they predate #16 and #17 and would conflict.
