# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

critter-crafter builds procedural monsters for Unity from three shared libraries: rigged **skeletons**, body **parts**, and biomass **connectors**. Authoring uses Python 3.12 (`uv`) and headless Blender 5.2. The runtime is the Unity UPM package `unity/com.ninthlevelsoftware.crittercrafter`. The first consumer is the game `9thLevelSoftware/synaptic-sea-unity`.

**Read `docs/handoff.md` first.** It holds the current state, the owner's standing decisions, the environment, and the hard-won lessons. Read the locomotion and real-parts lessons in it before changing either area.

## Commands

```powershell
uv sync
uv run critter doctor                           # finds Blender/Unity, checks the environment
uv run pytest                                   # full suite, several minutes (Blender tests bake real assets)
uv run pytest -m "not blender"                  # fast suite (~15 s), no Blender
uv run pytest tests/test_recipes.py::test_name  # a single test
uv run critter schema validate                  # v3 sources + cross-record rules
uv run critter skeleton vary --force            # regenerate the 39 draft skeletons from archetypes.py
uv run critter recipe golden                    # writes tests/golden_v3/{catalog,recipes,locomotion}.json
Copy-Item tests/golden_v3/*.json unity/com.ninthlevelsoftware.crittercrafter/Tests/Editor/GoldenV3/
uv run critter library build                    # ~5 min: Blender bakes every skeleton/part -> library/
uv run critter skeleton qa                      # motion + export + locomotion QA per skeleton
```

Blender-marked tests skip automatically when no Blender is found (`CRITTER_BLENDER` overrides detection). `tests/test_real_parts_blender.py` also needs the private asset archive (`synaptic-sea-asset-archive`) cloned next to this repo, or `CRITTER_ASSET_ARCHIVE` set.

Unity, headless (close any editor that has `unity/TestProject` open first):

```powershell
& "F:\Unity\6000.6.0f1\Editor\Unity.exe" -batchmode -projectPath unity/TestProject -runTests `
  -testPlatform EditMode -testResults work/unity_results.xml -logFile work/unity_tests.log
```

`docs/handoff.md` has the locomotion capture command. Frames become GIFs with `tools/frames_to_gif.py`, and the owner judges locomotion from those GIFs.

**Change loop:** edit sources → `skeleton vary --force` → `recipe golden` and copy the goldens into the Unity package → pytest → `library build` and `skeleton qa` → Unity tests and a capture. The golden, Unity-test and QA steps fail loudly if skipped.

## Architecture

- **Data (`data/`, `schemas/`).**
  - v3 sources: `data/library.json` (schema `3.0.0`), `data/skeletons/*_v3.skeleton.json` (13 archetypes × 3 presets), `data/parts/`, `data/pools/pools.json`, and `data/binding_profiles/`.
  - Binding profiles are immutable chain definitions identified by SHA-256. A branch names a profile, and a part fits a branch through that profile.
  - Branch sockets are local to their parent joint. Neutral pose is a separate delta applied once, after bind.
  - v2 data and `tests/golden/` are frozen historical evidence. v3 loading rejects v2 inputs.
- **Skeletons come from code.** `src/critter_crafter/skeletons/archetypes.py` builds every archetype. The JSON files in `data/skeletons/` are its generated output, so change the archetype code, then run `vary`, rather than hand-editing the JSON.
- **Recipe generator `cc-gen-3` is implemented twice and must match exactly.**
  - The implementations are Python `recipes/generator.py` and C# `Runtime/Generation/RecipeGenerator.cs`.
  - Both use SplitMix64 and integer-millimetre fit rules (`docs/generator.md`).
  - The goldens in `tests/golden_v3/` are asserted by both pytest and the Unity EditMode tests, so the two copies of the goldens must stay byte-identical.
  - Only `approved` skeletons and parts are generatable. Goldens use an in-memory approved copy (`fixture_only_approval`).
- **Blender boundary.**
  - Host code never imports `bpy`. `blender/runner.py:run_op` launches `blender -b --factory-startup -P blender/_entry.py -- <op> args.json result.json`.
  - `<op>` maps to module `blender/ops_<op>.py`, which exposes `run(args) -> dict`. A `batch` op runs several jobs in one process.
  - `critter blender snippet` emits the same op for the Blender MCP.
- **Library build** (`library/`): compiles the catalog and bakes FBX/GLB assets plus eight clips per skeleton into `library/biomass_core-v<version>/`. The Unity side imports that directory through `Editor/LibraryImporter.cs`.
- **Locomotion is runtime, not baked.**
  - The game moves the creature. `Runtime/Locomotion/CreatureGait.cs` steps the feet with Animation Rigging IK.
  - Each skeleton's catalog `locomotion` block (`locomotion/block.py`) publishes stroke, duty factor and walk/run/max speeds. The Python `locomotion/stepper.py` is the reference planner, golden-tested against Unity's `StepPlanner`.
  - Locomotion tests and captures must run in real Play Mode (see the handoff lessons).
- **Real parts.** `critter part import` fits Meshy scout meshes from the private archive:
  - `parts/fit.py` is pure-Python geometry. `blender/ops_realpart.py` handles import, cleanup and export. `ops_partqa.py` handles deformation QA against the placeholder the part replaces.
  - The repo is public and the archive is paid-private. **Never commit Meshy meshes, textures or renders.** Part records keep only the archive path, SHA-256 and fit parameters.
- **Build output** in `work/`, `library/` and `dist/` is gitignored.

## Owner rules

- **Approval is the owner's job.**
  - Never set a skeleton or part to `approved` yourself, whether by editing JSON or by running `critter skeleton approve` / `critter part approve`.
  - The owner approves after looking at review sheets and GIFs.
- **Keep process light.** Heavy governance (receipts, gates, pinned tooling) stalled an earlier attempt, so don't add new process machinery.
- **Paid Meshy calls need the owner's cost confirmation first.**
- **Don't relitigate the standing decisions** in the handoff: skeleton-first assembly, runtime IK locomotion, speeds from the catalog, the retired tentacle radial archetype, and the dragger crawl style.
