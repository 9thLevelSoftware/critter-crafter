# critter-crafter: Procedural Monster Asset Tool (greenfield plan)

## Context
The first attempt at procedural monsters was built inside the Synaptic Sea game, on the Godot branch `feature/procedural-biomass-threat-assembly` of `the-synaptic-sea`. That branch was never merged and never ported to `synaptic-sea-unity`. It proved the concept with primitive placeholders: a part catalog, an attachment-graph recipe, a seeded generator, rigid socket gaits, and 30 approved placeholder renders.

It never shipped a real part, for three reasons:
- A heavy governance/journal layer stalled art production.
- The prompt profile was wrong: it used the "derelict alloy" style meant for ship hardware, not flesh.
- The code was GDScript tied to Godot, with macOS-only paths.

Goal: a **standalone, engine-portable tool**, `9thLevelSoftware/critter-crafter`, living in `D:\critter-creator`. It produces **three libraries** and assembles **skinned, rigged, Animator-driven** monsters in any Unity game that installs the package and a library release.

**The three libraries:**
1. **Skeleton library.** Rigged body plans (biped, quadruped, hexapod, serpentine, radial and others), in many variants. Each skeleton has named **branches**, and each branch ends in **snap points**. A snap point declares which part types it accepts.
2. **Body-part library.** Human and non-human limbs, heads, torsos, tails and appendages. Each part is skinned to a standard **branch bone template**, so it can bind to any compatible branch on any skeleton.
3. **Biomass connector library.** Collars, frayed stumps, tendrils and gunk. These are skinned across a snap point's two bones, so they bend at the joint.

**Settled decisions:**
- Form: a Python CLI with headless Blender, plus a Unity UPM package.
- Shared assets: the libraries this tool generates.
- Animation: skinned rigs with **skeleton-first** assembly. This was the user's proposal, and it replaces the prototype's part-tree approach.
- Tools: Meshy, Blender and Unity MCPs are all available for interactive work.

## Architecture
```
Authoring side (Windows, Python 3.12 + uv, Blender 5.x headless)
  skeleton families (templates + procedural variation) ─► skeleton rigs + baked clip sets per skeleton
  Meshy REST/MCP ─► part candidates ─► [GATE 1 select] ─► clean ─► fit to branch template ─► weight ─► QA
  procedural bmesh ─► placeholders, stumps, collars, tendrils (2-bone skinned)          [GATE 2 approve]
                                                   ▼
                        export FBX (+GLB) + catalog fragments ─► `critter library build`
                        ─► library/<id>-v<semver>/ (catalog.json, skeletons/, parts/, connectors/, clips/, manifest) ─► release zip
Unity side (any 6000.x game): UPM package
  Editor LibraryImporter ─► ScriptableObjects, skeleton prefabs (Animator + controller), part prefabs
  Runtime: RecipeGenerator / RecipeValidator / CreatureAssembler (bind parts to skeleton bones) / CreatureMotion
  Game-owned adapter implements ICreatureVisualFactory
```
The authoring side and Unity side share exactly one contract: the library folder or zip, validated against the v2 JSON Schemas.

## Core model: skeleton-first assembly
- **Branch templates** are the standard bone chains everything binds to. They define bone names, bone count and nominal proportions. Starter set:
  - `limb3` and `limb4` (arm or leg)
  - `insect_leg4`
  - `tentacle8` and `tail8`
  - `neck_head` (neck bones plus head plus jaw)
  - `spine_torso`
  - `appendage1` (claw, maw, eye cluster)
- **A skeleton** is a full Unity-ready rig made of a root, a spine or core, and branches. Each branch instance records:
  - `branch_id` and its `template`
  - its `attach_bone` on the skeleton
  - `length_m`, `size_class` and `side` (L/R/C)
  - its **snap points**
- **Snap points** are named, like `snap_limb_L0` or `snap_head_0`. Each one declares:
  - `accepts` (categories, template, and optional species/role tags)
  - `required` (true/false). Optional snaps stay empty, which lets a skeleton cover several visual variants.
  - `connector_size_class`
  - `span_bones [parentBone, branchRootBone]`, the two bones the connector is weighted between.
- **Parts** are authored on their branch template's bind pose at nominal length. They bind to a skeleton branch like this:
  1. Match bones by template bone name.
  2. Uniformly scale the part so its nominal length matches the branch's `length_m`, clamped to 0.8–1.25×. Outside that range the fill is invalid, so the generator only picks parts whose size class fits.
  3. Recompute bindposes from the skeleton's bind pose. There is **one cached mesh per (part, skeleton branch)**, so no imported asset is ever changed.
- **Nesting** (a claw on an arm, a maw on a tentacle tip) comes from the snap points on each branch's distal end. This keeps the prototype's "maw on a tentacle" depth without a free-form graph.
- **Head-less and torso-less bodies** are allowed. The skeleton's core can be a skull, a sac or a ribcage.
- **The single-skeleton payoff:** every creature has **one skeleton**, and so:
  - It gets a Unity **Generic Animator** with clips.
  - IK and Animation Rigging can be added on top if a game wants them.
  - `CreatureBaker` can merge it into one SkinnedMeshRenderer with one draw call.

## Animation (what you get)
- **Clips per skeleton.** Blender bakes a clip set for every skeleton: `idle`, `locomote` (walk plus a run blend), `telegraph`, `attack`, `hit`, `stun` and `death`.
  - A procedural **clip generator** drives the branch chains and exports the clips in the skeleton's FBX. It is a port of the prototype's gait table, adapted from rigid part swings to bone chains:

    | Gait | Frequency | Swing |
    |---|---|---|
    | biped | 1.8 Hz | 24° |
    | quadruped | 2.2 Hz | 20° |
    | crawl | 2.6 Hz | 28° |
    | drag | 1.4 Hz | 18° |
    | slither | 1.7 Hz | 30° |

    Phase lags run down each chain. Tentacles and tails use travelling waves. Drag pullers use a sawtooth (slow pull, fast reach).
  - Clips can be hand-polished later in Blender, live through the MCP, on a per-skeleton basis.
- **Animator setup.** Skeletons in the same family share an **AnimatorController template** with a Speed float, AI-state triggers and a Death state. Each skeleton gets an `AnimatorOverrideController` that maps in its own clips.
- **No root motion.** `applyRootMotion=false`, and clips never move the root bone horizontally. The NavMeshAgent or the game owns position.
- **Optional runtime layer.** `CreatureMotion` adds idle noise and speed-scaled amplitude. `FootPlantingIK` is off by default, because the orthographic isometric camera hides foot sliding.

## Key design decisions
1. **Sockets and snap points are catalog data.** They are not nodes in the exported mesh files. Each one names a bone plus a bind-pose offset.
2. **Coordinate frame.** The canonical frame is glTF: right-handed, +Y up, +Z forward, meters.
   - Catalog data is converted to Unity exactly once, in the Editor importer (`CritterFrame`).
   - Starting assumption: positions become `(−x, y, z)`, quaternions become `(x, −y, −z, w)`, and axes follow the quaternion rule.
   - This rule is **locked by a chirality probe test in M0** (an asymmetric L-shaped part), not by the docs. The FBX export flags are pinned the same way.
3. **Unity import format is FBX**, which works with no packages and carries the clips. GLB is exported alongside for Blender, MCP and previews. glTFast is supported as an optional `versionDefines` path.
4. **The Runtime asmdef has no references.** Catalogs are array-based JSON so the built-in `JsonUtility` can read them (no Newtonsoft needed).
5. **The generator is deterministic and shared by Python and C#.** It is called `cc-gen-2`, uses SplitMix64, integer-only logic and ordinal sorts, and hard-codes no ids.
   - Steps: pick a skeleton (by pool, hint and tags), fill required snaps, roll optional snaps, pick connectors by size class, then fill nested distal snaps. Everything is bounded by the budgets.
   - Golden files cover seeds 1–100 × each hint. Both test suites must produce byte-identical output.
6. **Connectors are always procedural** (bmesh), never Meshy. Each one is skinned to two bones with a ramp across its span.
7. **Meshy is used only for body parts.** Rules:
   - Text-to-3d, not image-to-3d.
   - One prompt profile, `flesh_stylized_v1`: flesh R175–195 G145–165 B150–170, stylized low-poly, "straight extended, NOT coiled" for elongated parts.
   - Always render a low-angle base view.
8. **Weighting Meshy parts.** Voxel-remesh a proxy copy, weight the proxy to the branch template, then data-transfer the weights to the real mesh. This survives non-manifold output. Then limit to 4 influences, normalize and clean.
9. **Lightweight process.**
   - Two human gates only: select a candidate, then approve the part.
   - Paid Meshy calls follow `plan`, then `--cap`, then a y/N prompt, then logging to `ledger.jsonl`. Meshy MCP Rule 1 also applies: confirm the cost with the user first.
   - No governance layer. The manifest just records a sha256 for each file.
10. **Dropped from the prototype:** `wrapper_scene_path` and the Godot collision fields, the derelict prompt profile, and macOS paths.
    **Bugs fixed:** the `hard_max`/`max` budget key mismatch, and the triangle count that actually counted polygons.

## Data schema v2 (`schemas/`, JSON Schema 2020-12)
- **`branch_template.v2`:** `template_id`, `bones[{name, parent, head_m, tail_m}]` at nominal length, `nominal_length_m`, `chain_kind` (limb|tentacle|tail|spine|neck|appendage) and `swing_axis`/`bend_axis`.
- **`skeleton.v2`:**
  - Identity: `skeleton_id`, `family` (biped|quadruped|hexapod|crawler|serpentine|radial|dragger), `locomotion_hint`, `status`.
  - `bones[]`: the full bind pose.
  - `core{bone, category_hint}`.
  - `branches[{branch_id, template, attach_bone, length_m, size_class, side, gait_group, phase_rad}]`.
  - `snap_points[{name, branch_id, accepts{categories, templates, tags_any}, required, connector_size_class, span_bones, bone, position_m, rotation_xyzw}]`.
  - `clips{idle, locomote_walk, locomote_run, telegraph, attack, hit, stun, death}` (clip names inside the FBX).
  - `collision_shapes[{..., bone}]`, `budget{max_bones}`, `mesh{fbx, glb}`, `provenance`.
- **`part.v2`:**
  - Identity: `part_id`, `category` (core|limb|head|torso|tail|appendage|connector), `template`, `species_tags`, `roles`, `size_class`, `status`, `style_profile`.
  - Geometry: `dimensions_m`, `budget{max_triangles, max_material_slots, texture_size}`, `mesh{fbx, glb, triangles, bounds}`.
  - Rig: `bone_map` (the template bone names used), `max_influences: 4`.
  - Optional `distal_snaps[]`: nested snap points on the part's own template bones.
  - Also `collision_shapes`, `fallback` and `provenance`.
  - Connectors add `connector{size_class, span_m}`.
- **`library.v2`:**
  - `frame: "gltf_rh_yup_zfwd_m"`.
  - `limits`: max_bones 120, max_parts 12, max_triangles 30000 (target 24000), max_influences 4.
  - Contents: `branch_templates[]`, `skeletons[]`, `parts[]`, `pools[]` and `generator{algorithm, rng, fill rules}`.
- **`recipe.v2`:**
  - Identity: `recipe_id`, `library_id`, `library_version`, `seed`, `generator`, `skeleton_id`.
  - `fills[{snap, part_id, connector_part_id, nested[{snap, part_id, connector_part_id}]}]`.
  - Validation rules:
    - All required snaps are filled.
    - Each part's category, template and tags satisfy `accepts`.
    - Connector size classes match.
    - Length scale stays within the clamp.
    - Budgets hold.
  - Once saved, a recipe plus its seed is authoritative and is **never regenerated on load**.
- The prototype's socket-name regex, placement formula (`child = parent_socket * inverse(child_socket)`, used for nested distal parts) and triangle budgets all carry forward:

  | Part type | Max triangles |
  |---|---|
  | core | 5000 |
  | limb | 2500 |
  | head | 3500 |
  | connector | 500 |
  | appendage | 1500 |

## Repo layout (`D:\critter-creator`)
**Repo setup:** the folder already contains `.claude/`, so don't clone into it. Run `git init`, then `git remote add origin https://github.com/9thLevelSoftware/critter-crafter.git`, then fetch.
```
docs/ frame.md, rng.md, generator.md, mcp-workflows.md, adr/
schemas/ branch_template|skeleton|part|library|recipe|manifest .v2.schema.json
data/ branch_templates/*.json  skeleton_families/*.json (param ranges)  skeletons/*.skeleton.json
      parts/*.part.json  prompt_profiles/flesh_stylized_v1.json  gait_profiles.json  pools/  meshy_batches/
assets/masters/{skeletons,parts,connectors}/<id>/master.blend   (Git LFS)
src/critter_crafter/ cli.py config.py schema/ recipes/{rng,generator,validate,budgets}.py
      meshy/{client,prompts,credits,download}.py  library/{build,pack,manifest}.py  review/sheet.py
      blender/ _entry.py ops_{skeleton,clips,placeholder,stump,connector,clean,fit,weight,qa,render,export,assemble}.py frame.py rigkit.py
tests/ (pytest; `blender` marker auto-skips when Blender is missing)  fixtures/  golden/
unity/com.ninthlevelsoftware.crittercrafter/  Runtime/ Editor/ Tests/ Samples~/{PlaceholderLibrary,NavMeshCreatureDriver}
unity/TestProject/  (Unity 6000.6.0f1, URP; references the package via file:)
work/ library/      (gitignored; libraries are published as GitHub release zips)
```
The package installs from the UPM git URL `...critter-crafter.git?path=/unity/com.ninthlevelsoftware.crittercrafter#vX`.

## CLI (`critter`: click, jsonschema, httpx, Pillow)
**Setup and validation**
- `doctor`
- `schema validate`

**Skeletons**
- `skeleton new --family quadruped`
- `skeleton vary --family quadruped --count 20 --seed N`: procedural variants within the family's parameter ranges. Ranges cover branch count and placement, lengths, spine segment count and optional snaps. This keeps the skeleton library large without hand-authoring each one.
- `skeleton clips <id>`
- `skeleton review`

**Parts and connectors**
- `part new|approve`
- `placeholder build`
- `stump build`: a port of `biomass_frayed_stump_generator.py`.
- `connector build`

**Meshy**
- `meshy plan|generate --cap N|fetch|status|import-task`
- `candidates review|select`

**Blender part pipeline**
- `blender clean|fit|weight|qa|render|export`
- `blender snippet <op>`: prints code for the Blender MCP `execute_blender_code` tool. It calls the same pure `(args)->result` op functions on the live scene.

**Library, recipes and assembly**
- `library build [--include-placeholders]|pack`
- `recipe generate|sweep`
- `assemble preview <recipe> [--export]`: a Blender bake to a single rigged FBX/GLB with clips.
- `archive import`: pulls in the scout candidates from `synaptic-sea-asset-archive`.

**Headless runs:** `blender -b --factory-startup -P _entry.py -- <op> <args.json>`. Arguments go through a JSON file to avoid Windows quoting problems. The Blender executable is found via `CRITTER_BLENDER`, then `critter.toml`, then `C:\Program Files\Blender Foundation\Blender 5.*`, then `PATH`.

## Pipelines
- **Skeleton pipeline.**
  1. Take a family template plus parameters.
  2. Build the armature: spine/core plus branch instances, each built from its branch template and scaled to `length_m`.
  3. Write the snap points.
  4. Run the clip generator (gait and state clips).
  5. QA: bone count, naming, a no-root-horizontal-motion check, and snap-point sanity.
  6. Render a review sheet: the rig with a placeholder-filled preview and a clip turntable GIF.
  7. Export FBX with clips (no mesh, or a tiny proxy mesh) plus the catalog fragment.
- **Part pipeline.**
  1. Ingest (detect the file type from its first bytes, since Meshy downloads have no extension).
  2. Orient the main axis to the template axis.
  3. Clean: loose geometry, merge by distance, scale to nominal template length, origin at the template root.
  4. Decimate to the **triangle** budget, then triangulate.
  5. Limit to ≤2 materials and 1024 px textures.
  6. **Fit** the template armature. Optionally snap bones to the centroid of the mesh's cross-section.
  7. Proxy-transfer the weights.
  8. QA:
     - every vertex is weighted, with ≤4 influences;
     - every bone covers some vertices;
     - stress poses at ±30° and ±60°: edge stretch ≤1.6 and volume change ≤15%;
     - the part plays the family's `locomote` clip on a test skeleton without obvious artifacts.
  9. Render a review sheet: front, side, 3/4, **low-angle base**, stress poses.
  10. Export FBX and GLB, `master.blend` and the catalog fragment.
- **Connector pipeline.** Procedural bmesh at S/M/L sizes with seeded variants, weighted with a two-bone ramp across the span.

## Unity package
- **Runtime** (`CritterCrafter.Runtime`, no references):
  - Library data: `CritterLibrary`, `SkeletonDefinition` and `PartDefinition` ScriptableObjects.
  - Recipes: `CritterRecipe` is `[Serializable]`, so it round-trips into save files. `CritterRng`, `RecipeGenerator` and `RecipeValidator` (with stable `CC_*` diagnostic codes) sit alongside it.
  - Assembly: `CreatureAssembler.Assemble(library, recipe, options{parent, layer, collision None|SingleCapsule|PerBone, triggers, fallback})` returns an `AssembledCreature`. It:
    1. instantiates the skeleton prefab, which has an Animator and an override controller;
    2. for each fill, instantiates the part's SkinnedMeshRenderer, remaps `bones[]` to the skeleton's branch bones by template name, and applies the cached rebased mesh at the branch's length scale;
    3. adds a connector across `span_bones`;
    4. handles nested distal parts;
    5. checks the budgets.
  - Motion: `CreatureMotion` exposes `SetVelocity`, `SetState`, `PlayAttack` and `PlayHit`, and drives the Animator's parameters.
  - Helpers: `FootPlantingIK` and `CreatureFacing`, both optional.
  - Game seam: `ICreatureVisualFactory` plus `RecipePool` (archetype → skeleton pool, tags and curated recipes).
- **Editor:**
  - `Tools/Critter Crafter/Import Library…` takes a zip or folder and copies it to `Assets/CritterLibraries/<id>/<ver>/`.
  - An `AssetPostprocessor` sets Generic rig, no avatar, `skinWeights=4`, `optimizeGameObjects=false`, clip import on skeleton FBXs, and clip loop flags from the catalog.
  - Materials use the active pipeline's default lit shader (URP, HDRP or Built-in).
  - The importer builds the skeleton prefabs (Animator plus override controller) and part prefabs, and checks that every bone name and triangle count matches the catalog.
  - `CreaturePreviewWindow`: pick a skeleton and seed, scrub speed and state.
  - A 100-seed sweep tool.
  - `CreatureBaker`: merges all renderers into one SkinnedMeshRenderer, with an optional texture atlas.
- **Tests** (Unity Test Framework):
  - RNG golden values; C# generator output byte-identical to the Python golden files.
  - Validator cases.
  - Chirality probe.
  - Bone-remap correctness: part vertices follow the skeleton's bones within tolerance.
  - **No root motion** after 10 simulated seconds of locomote.
  - Budget limits.
  - The baked creature matches the runtime-assembled one.

## Milestones & acceptance
- **M0: Scaffold, schemas, placeholders, first Unity assembly.**
  - Work:
    - Repo init; uv, LFS and `.gitattributes`.
    - v2 schemas and the validator.
    - rng and generator, with golden files.
    - 8 branch templates.
    - **3 hand-specified skeletons** (biped, quadruped, crawler) with placeholder clips.
    - Procedural rigged placeholder parts for the prototype's 8 pilot parts: human_arm, insect_leg, cephalopod_tentacle, animal_skull, humanoid_torso, claw and maw, using the prototype's dimensions, plus S/M/L collars.
    - UPM package: importer, assembler and C# generator.
    - Chirality probe.
  - Accept when:
    - pytest passes, including headless Blender.
    - `schema validate` reports 0 diagnostics.
    - EditMode tests pass: 100 seeds × 3 skeletons are all valid, C# matches Python golden byte-for-byte, and the probe is within 1e-4.
    - A TestProject screenshot shows the assembled placeholder creatures playing their idle clip in an Animator.
- **M1: Skeleton library and clip generator.**
  - Work:
    - Family parameter files for biped, quadruped, hexapod, crawler, serpentine, radial and dragger.
    - `skeleton vary`, the procedural clip generator (gait table plus state clips), and skeleton review sheets.
  - Accept when:
    - At least 40 skeletons across the 7 families pass QA.
    - Every skeleton's clips play in Unity without root motion.
    - The user approves the per-family review GIFs.
- **M2: Real parts from existing scouts, no credits.**
  - Work:
    - `archive import` of the arm, insect leg, tentacle, skull and stump candidates.
    - Clean, fit, weight, QA, render and export.
    - Port the stump generator.
    - Regression tests for the budget-key and triangle-count bugs.
  - Accept when:
    - At least 3 parts pass QA, including the clip-deformation check, and are **approved by the user**.
    - A mixed real-plus-placeholder library imports and animates in Unity.
- **M3: First paid Meshy batch.**
  - Work: torso, claw and maw, plus re-runs of rejected scouts. Text-to-3d then refine, 2–3 candidates per part, `--cap 250` credits, with the **user confirming the cost before each call**.
  - Accept when:
    - The ledger total is within the cap.
    - All 8 pilot parts are approved.
    - A cross-part style sheet shows they look consistent.
    - `assemble preview` renders exist for at least 10 generated creatures.
    - Library v0.3.0 is published.
- **M4: Expansion and bake.**
  - Work:
    - At least 9 connector variants.
    - New parts: spider leg, crab claw, vertebral tail, eye cluster, mandible, ribcage core and flesh-sac core, for at least 24 parts in total.
    - At least 80 skeletons.
    - Species and tag affinity in the generator.
    - `CreatureBaker`.
  - Accept when:
    - Each family yields at least 80 distinct creatures per 100 seeds.
    - A baked creature takes at most 2 draw calls.
    - Baked and runtime creatures produce matching screenshots.
- **M5: synaptic-sea-unity integration.** The adapter lives in the game repo.
  - Work:
    - `BiomassThreatVisualFactory : ICreatureVisualFactory` keeps the game's existing contract: a root named `Threat_<id>` on the Threat layer, a `"Mesh"` child with a trigger CapsuleCollider sized from the creature's bounds, and room for a NavMeshAgent. It plugs in at `ThreatPlaceholderFactory.Build(...)`.
    - Event mapping:
      - `PlaceholderMoved` → velocity
      - `ThreatAttacked` → attack
      - AI state → `SetState`
      - `ThreatKilled` → death
    - The recipe and seed are saved with the game.
    - Fall back to the old placeholder if assembly fails. `drone_swarm` stays mechanical.
  - Accept when:
    - The game's tests stay green.
    - A PlayMode test spawns all 6 archetypes.
    - A save/load round trip restores an identical recipe.
    - Screenshots from the orthographic isometric camera pass review.

## Sources to port and reuse
From the prototype branch `feature/procedural-biomass-threat-assembly` of `the-synaptic-sea`:

| Source | Use |
|---|---|
| `scripts/systems/biomass_recipe_generator.gd` | seeded fill logic, becomes `cc-gen-2` |
| `scripts/threats/biomass_gait_controller.gd` | gait table, feeds the clip generator |
| `scripts/threats/biomass_assembler.gd` | placement math for nested distal parts |
| `data/combat/biomass_{part,recipe}_catalog.json`, `data/combat/schemas/*_v1.schema.json` | 8 pilot parts, 5 recipes, which become the first skeletons |
| `tools/biomass_catalog_validate.py`, `tools/meshy_blender_master.py` | logic to mine |
| `tools/biomass_texture_cleanup.py`, `tools/biomass_frayed_stump_generator.py` | logic to mine; fix the bugs listed above |

From other repos:
- `hermes-skills`: `biomass-asset-generation/references/biomass-design-rules.md` for the style and prompt rules.
- `synaptic-sea-asset-archive`: `procedural-biomass-assembly/artifacts/{meshy_mcp_scout,biomass_frayed_stumps}` supplies the M2 input meshes.

## Verification (end-to-end)
1. **Python and Blender:** `uv run pytest` (unit plus headless Blender), `critter schema validate`, and `critter recipe sweep --seeds 1..100` for every family.
2. **Unity:**
   - Open `unity/TestProject` and run EditMode and PlayMode tests, via the Unity MCP / `com.unity.pipeline` or `Unity.exe -batchmode -runTests`.
   - Import the built library.
   - Assemble 20 seeds per family in the preview window and scrub the Animator states.
3. **Visual review:**
   - Use the Blender MCP to inspect skeletons, clips and weights live in `master.blend`.
   - Check the review sheets and assemble-preview turntables.
   - Take Unity screenshots and GIFs with an orthographic isometric camera that matches the game's (offset 16,18,16, size 22).
4. **Meshy spend:** compare the totals in `work/meshy/ledger.jsonl` against each batch's `--cap`.

## Top risks
| Risk | Mitigation |
|---|---|
| Skeleton variety still feels repetitive | Procedural `skeleton vary` within families, optional snaps, nested distal snaps, part × connector combinations, species affinity |
| Parts deform badly on branches of other lengths | Uniform length scale clamped to 0.8–1.25×; size classes; QA plays the family clip on a test skeleton |
| Meshy topology breaks weighting | Proxy weight transfer; stress-pose QA gate; claws and maws on 1-bone `appendage1` |
| Style clash between Meshy tasks | Single prompt profile; cross-part style sheet gate; optional `meshy_retexture` |
| Handedness bugs | The chirality probe locks conversion and export flags; bone axes are computed from the bind pose at runtime |
| Draw calls or bone counts too high | Validator limits; `CreatureBaker` |
| Python and C# generators drift apart | Integer-only algorithm; golden files checked in both test suites |
| Process creep | Two human gates only; no governance layer |
