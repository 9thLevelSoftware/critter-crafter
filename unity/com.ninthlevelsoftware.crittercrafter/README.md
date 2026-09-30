# Critter Crafter (Unity package)

Assembles skinned, Animator-driven procedural monsters at runtime from seeded recipes. A recipe picks one **skeleton** and fills its branches with **parts** and **connectors** from a built asset **library**. The library is made by the `critter-crafter` authoring tool (Python + Blender) and imported into your project; this package is the runtime that turns a recipe into a creature.

- Unity 6000.6 is the only version this has been run on. `package.json` declares `6000.0`; treat older versions as untested.
- Depends on `com.unity.animation.rigging` (runtime foot IK).
- The authoring side, the data model and the design decisions are documented in the repository: `docs/handoff.md`, `docs/locomotion.md`, `docs/generator.md`, `docs/parts.md`.

## What you get

- **Deterministic recipes.** Pool + seed gives the same recipe everywhere (`cc-gen-3`, SplitMix64, identical in C# and Python). Store the pool and seed, or the recipe itself, to rebuild a creature.
- **Runtime locomotion.** Legs are placed by the game, not baked: `CreatureGait` steps the feet with Animation Rigging IK, follows slopes and turns, and each creature publishes the speeds it walks and runs at.
- **Never throws into the game** (under default options): a bad recipe, a missing asset or a rig error returns a primitive fallback creature with `CC_*` diagnostics.

## Importing a library

1. Build or download a library (`critter library build`, or the slim release zip from the GitHub release: the catalog, FBX models, textures and motion files the importer needs).
2. **Tools > Critter Crafter > Import Library Folder...** (or **Import Library Zip...**). The importer validates the catalog, copies the models into `Assets/CritterLibraries/<library_id>/<version>/`, builds Animator controllers and materials, and creates a `CritterLibrary` asset next to them. Problems are listed in the console; an import with problems should not be shipped.

Re-importing replaces the whole folder, so keep your own assets elsewhere.

**Which versions import.** The importer and the generator accept a catalog whose schema major is 3 and whose generator is `cc-gen-3` with `splitmix64`. The library's own release number (0.2.0, 0.2.1, 0.3.0) is not gated. A *saved recipe* replays only on the library id and version it was made on (`CC_LIBRARY_VERSION`), so a library bump invalidates saved recipes: keep the version you saved against, or store the pool and seed and regenerate.

**Only `approved` content generates.** Skeletons and parts start as `draft`. Until the owner approves some, every pool fails with `CC_GEN_NO_SKELETON` and the factory returns a fallback.

## Spawning a creature

```csharp
using CritterCrafter;

var library = /* the imported CritterLibrary asset */;
ICreatureVisualFactory factory = new DefaultCreatureVisualFactory(library, AssemblyOptions.Default);

GameObject go = factory.Build(new CreatureSpawnRequest {
    poolId = "any",            // or the game's archetype id if poolId is empty: it must match a catalog pool_id
    seed = 1234,
    parent = spawnRoot,
    layer = LayerMask.NameToLayer("Enemies"),
});

var creature = go.GetComponent<AssembledCreature>();
if (creature.IsFallback) Debug.LogWarning(string.Join("; ", creature.Diagnostics));
```

- A saved recipe wins over pool + seed: set `CreatureSpawnRequest.recipe` to rebuild a creature exactly.
- Pools: `any` (the seven curated families), one per family (`biped`, `quadruped`, `crawler`, `hexapod`, `radial`, `serpentine`, `dragger`), themed pools, and `amalgam` (asymmetric fused creatures, deliberately not in `any`).
- `AssemblyOptions`: `parent`, `layer`, `collision` (a single capsule by default), `collidersAreTriggers`, `groundMask` (the layers the feet raycast to find the ground), `fallbackOnInvalid` (true by default; false rethrows). `allowReview` is for review tooling only and must stay false in a game.
- Games usually implement `ICreatureVisualFactory` themselves to wrap the creature in their own root, collider and layer conventions, and call `CreatureAssembler.Assemble`.

## Moving and animating it

The game owns movement (a `NavMeshAgent`, say); the clips never apply root motion.

```csharp
var motion = go.GetComponent<CreatureMotion>();
motion.SetVelocity(agent.velocity);          // drives idle/walk/run blend and the gait clock
motion.PlayHit();
motion.SetState(CreatureState.Telegraph);    // then Attacking; Stunned; Dead
```

- **Use the creature's own speeds.** Read `creature.Locomotion` (`v_walk_mps`, `v_run_mps`, `v_max_mps`, and the per-creature `move_speed_mps`) and set the agent's speed from them, not one speed for every creature. Some creatures are slow on purpose (amalgams crawl at 0.2-0.6 m/s).
- `CreatureGait` runs at execution order 1000, after the agent has moved the creature. Instant turns are handled: the body turns only as fast as its feet can follow.
- Dying: a walking body collapses over its planted feet; a dragged body curls.
- Renderer bounds come from the catalog (`asset.motion_bounds_m`); older libraries without it fall back to one bone sampling per skeleton.

## Fallback diagnostics

`AssembledCreature.IsFallback` is true and `Diagnostics` explains why. Codes you may see:

| Code | Meaning |
|---|---|
| `CC_NO_CATALOG` | the library has no catalog |
| `CC_GEN_NO_SKELETON` | nothing in that pool is approved |
| `CC_GEN_UNKNOWN_POOL` | the pool id (or archetype id used as one) isn't in the catalog |
| `CC_GEN_SCHEMA_VERSION`, `CC_GEN_ALGORITHM`, `CC_GEN_RNG` | the catalog isn't a supported v3 / cc-gen-3 library |
| `CC_MISSING_ASSET` | a recipe refers to a model the library doesn't have |
| `CC_LIBRARY_VERSION` | a saved recipe was made on another library version |
| `CC_ASSEMBLY_FAILED: ...` | binding or rigging failed part-way; the half-built creature was destroyed |

## Textures

Real (Meshy-derived) parts carry an albedo texture. The importer binds it to `_MainTex` and, where the shader has one, `_BaseMap`. The `_MainTex` path is covered by an EditMode test; **the URP `_BaseMap` path has not been run in Unity** (the test project has no URP).

## Review tooling

`CritterCrafter.Review` (the locomotion recorder and review course) compiles only in the Unity editor, is not auto-referenced, and is left out of player builds.

## Tests

The package's EditMode and Play Mode tests live in `Tests/Editor` and run in the repository's `unity/TestProject`. They need a built library (`critter library build`).
