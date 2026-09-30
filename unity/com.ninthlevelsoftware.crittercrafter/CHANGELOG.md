# Changelog

The package is versioned with the library it was built against. Library and recipe compatibility rules are in the README.

## Unreleased

### Added
- **Amalgams:** seeded asymmetric creatures (hauled, walker, slither) in their own `amalgam` pool, with vestigial limbs that flail.
- **Seeded variants** of every curated archetype, and optional tails, spines, second arms, antennae and crowns.
- Pools for every curated family.
- `CritterLibrary.EditorCreateSkeletonStatusClone` for tests that mean "nothing is approved".
- Review captures for idle, hit, stun and death (`-critterCourse reactions`), and gait metrics for body rise, planted-foot hover and drift per stance.

### Changed
- **The factory and assembler never throw under default options:** bad recipes, missing assets and rig errors return a fallback creature with `CC_*` diagnostics and leave no half-built creature in the scene.
- **Version gate:** the runtime accepts a catalog by schema major and generator identity (`cc-gen-3` / `splitmix64`), not by the library's release number. Error codes now match the Python generator (`CC_GEN_SCHEMA_VERSION`, `CC_GEN_ALGORITHM`, `CC_GEN_RNG`). A saved recipe still replays only on its own library version.
- **Renderer bounds:** every part is rooted at the skeleton's `root` bone and the motion box comes from the catalog (`asset.motion_bounds_m`), replacing thousands of `BakeMesh` calls per spawn.
- **Gait:** a planted foot is never pulled up by the reach clamp (the body used to climb after it, to metres after an instant turn); strain is detected before the clamp drags a foot; landings target where the body will be; the body turns only as fast as its feet can follow (`turnReachGate`); slopes pitch the body to the ground; dying bodies stop stepping and collapse over their planted feet; a walker's limp rolls the right way.
- Clips: feet are IK-pinned so the torso moves in every clip; new idle, stun, hit and death.
- The per-frame gait no longer allocates; `CritterCrafter.Review` is editor-only and not auto-referenced.

### Fixed
- A limping walker rolled away from its weak leg.

## 0.2.0

- Schema v3 skeleton foundation: 14 archetypes x 3 presets, immutable binding profiles, `cc-gen-3` recipes.
- Runtime foot-placement locomotion (Animation Rigging IK) with per-skeleton speeds and per-creature speeds derived from the assembled build.
- Real parts from Meshy scouts (draft, awaiting approval), with textures.
- Skeleton-first assembly, one Animator per creature, `CreatureMotion`.
