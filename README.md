# critter-crafter

Skeleton-first procedural monster assets for Unity games.

critter-crafter authors three shared asset libraries with Python + headless Blender (and Meshy for
real body parts), and ships a dependency-free Unity package that assembles **skinned, rigged,
Animator-driven** creatures from seeded recipes at runtime.

| Library | What it holds |
|---|---|
| **Skeletons** | Rigged body plans (biped, quadruped, crawler, …) with named branches and snap points, plus a baked clip set (idle, walk, run, stun, telegraph, attack, hit, death) per skeleton |
| **Body parts** | Limbs, heads, cores, tails and appendages, each skinned to a standard branch template (`limb3`, `insect_leg4`, `tentacle8`, …) so it binds to any compatible branch |
| **Biomass connectors** | Gunk collars, stumps and tendrils, skinned across a snap point's two bones so they bend at the joint |

A **recipe** = skeleton + which part (and connector) fills each branch. Recipes are generated
deterministically (`cc-gen-2`, SplitMix64) with bit-identical Python and C# implementations. Once
generated, a recipe is saved and never regenerated.

## Layout
```
data/          authoring sources: branch_templates, skeletons, parts, pools, gait/prompt profiles
schemas/       JSON Schema 2020-12 for sources, compiled catalog and recipes
docs/          frame.md and generator.md (normative), adr/, mcp-workflows.md
src/critter_crafter/   `critter` CLI, generator, validator, Blender ops (blender/ops_*.py)
tests/         pytest (unit + headless Blender); tests/golden is shared with the Unity tests
unity/com.ninthlevelsoftware.crittercrafter/   UPM package (Runtime has no dependencies)
unity/TestProject/     Unity 6000.6 test bed that references the package via file:
```

## Quick start
```powershell
uv sync
uv run critter doctor                 # Blender 5.x, Unity, Git LFS
uv run critter schema validate        # sources + cross-record rules
uv run critter recipe sweep           # 100 seeds per pool: validity + variety
uv run critter library build          # one headless Blender run -> library/<id>-v<ver>/
uv run critter assemble preview --pool any --seeds 1..8   # review contact sheet (work/review/)
uv run critter assemble export --pool biped --seed 42     # baked single-rig FBX/GLB with clips
uv run critter library pack           # dist/critter-library-<id>-v<ver>.zip (release asset)
uv run pytest
```

Unity tests (EditMode) against the built library:
```powershell
Unity.exe -batchmode -projectPath unity/TestProject -runTests -testPlatform EditMode -testResults work/unity_results.xml
```

## Using it in a game
1. Add the package with a git URL:
   `https://github.com/9thLevelSoftware/critter-crafter.git?path=/unity/com.ninthlevelsoftware.crittercrafter#v0.1.0`
2. **Tools > Critter Crafter > Import Library Zip…**, then pick a release zip. This creates
   `Assets/CritterLibraries/<id>/<ver>/<id>.asset` plus materials (your pipeline's default lit shader) and one
   AnimatorController per skeleton.
3. Spawn:
   ```csharp
   var recipe = library.Generate("stalker", seed);            // or load a saved recipe
   var creature = CreatureAssembler.Assemble(library, recipe, AssemblyOptions.Default);
   var motion = creature.gameObject.AddComponent<CreatureMotion>();
   motion.SetVelocity(agent.velocity);                          // Speed drives idle/walk/run
   motion.SetState(CreatureState.Attacking);                    // Telegraph/Attacking/Stunned/Dead
   ```
   Save `JsonUtility.ToJson(creature.Recipe)` with the game. Implement `ICreatureVisualFactory` to
   wrap creatures in your game's own root, collider and layer conventions.

No root motion: the game (NavMeshAgent etc.) owns position; clips only move bones.

## Status
M0 (scaffold, schemas, placeholder library, Unity assembly) is done. The roadmap is in
`.claude/plans/` / `docs/adr/0001-skeleton-first-assembly.md`:
- M1: skeleton families + `skeleton vary` + clip generator polish
- M2: real parts from existing Meshy scouts (clean/fit/weight/QA)
- M3: first paid Meshy batch (credit-capped, user-confirmed)
- M4: library expansion + `CreatureBaker`
- M5: synaptic-sea-unity integration
