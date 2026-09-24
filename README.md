# critter-crafter

Skeleton-first procedural monster assets for Unity. Package version `0.2.0` uses the deterministic `cc-gen-3` generator and SplitMix64. A recipe selects one skeleton and fills its branches with compatible parts and optional connectors.

The v3 foundation is chain based. Each branch names an immutable binding profile, version, and SHA-256 identity. The current profiles are `appendage1_terminal`, `core1_body`, `head1_neck`, `insect_leg4_articulated`, `limb3_digitigrade`, `limb3_plantigrade`, `spine3_axial`, and `tentacle8_flexible`. Branch sockets are local to their named parent joint and carry a complete orientation. Skeleton neutral pose is a separate set of local anatomical deltas: bind first, then apply neutral exactly once.

The v2 source documents and `tests/golden/` fixtures remain frozen historical compatibility evidence. Active v3 loading selects `data/library.json` schema `3.0.0`; v2 inputs are rejected rather than silently migrated. The archived v2 workflow requires its original package/library version and is not available through the active `cc-gen-3` dispatch. The v3 parity fixtures in `tests/golden_v3/` use an in-memory approved copy (`fixture_only_approval: true`) and do not approve candidate source files.

## Layout

```text
data/                                      v2 and v3 authoring sources
data/binding_profiles/                     immutable v3 chain profiles and registry
data/skeletons/*_v3.skeleton.json          13 archetypes x 3 presets = 39 candidates
schemas/                                   v2 and v3 JSON Schema 2020-12 documents
src/critter_crafter/                       CLI, compiler, generator, Blender and review code
tests/golden/                              frozen v2 parity fixtures
tests/golden_v3/                           v3 memory-fixture parity approvals
unity/com.ninthlevelsoftware.crittercrafter/ UPM package
unity/TestProject/                         Unity 6000.6 test bed
work/review/                               existing review and render output
work/rebuild-foundation/                   contracts, acceptance evidence and reports
```

## Quick start

```powershell
uv sync
uv run critter doctor
uv run critter schema validate
uv run critter recipe golden
uv run pytest
```

The skeleton workflow keeps candidates as drafts until a human reviews built output:

```powershell
uv run critter skeleton vary                  # write all 39 drafts; reviewed candidates are preserved
uv run critter skeleton status                 # counts draft/approved/rejected by family
uv run critter schema validate                 # validate v3 sources and cross-record rules
uv run critter library build                   # build the catalog and actual Blender assets
uv run critter skeleton qa                     # inspect all 8 built clips and the locomotion block per skeleton
uv run critter skeleton review                 # 3 modes x 4 views, actual built clips, fresh receipts
python -m http.server 8765 --directory work/review/foundation-v3
uv run critter skeleton approve --family biped # human approval after reviewing the local site
uv run critter skeleton reject <skeleton_id>   # record human rejection of current reviewed content
```

The review bundle contains bones, mannequin, and assembled modes; front, side, top, and three-quarter views; and the actual built GLB and `motion.json` artifacts. It is a local site and can be served with Python's standard HTTP server. `approve` requires a current passing review receipt. `reject` requires a current receipt for the same built content, including a fresh receipt whose QA result failed, so visibly bad content can be rejected without weakening the approval gate. Content fingerprints make stale review output invalid.

Authored polish is opt-in and separate from generated masters. An override lives at `work/polish/skeletons/<skeleton_id>/override.json` and records the source fingerprint it targets. A compatible override may be resolved during a rebuild; a changed source fingerprint or changed authored polish invalidates the build and requires rebuilding. Generated files are never overwritten by the override workflow.

Automated motion, export, and Unity verification is complete for the current draft foundation. The final report records 42 fresh passing receipts, 336 clips, byte-verified packages, and the remaining human visual approval gate. No paid generation is used by this foundation work.

Final verification artifacts:

- [Delivery verification report](work/rebuild-foundation/delivery-verification.json)
- [Library package](dist/critter-library-biomass_core-v0.2.0.zip)
- [Unity package](dist/com.ninthlevelsoftware.crittercrafter-0.2.0.tgz)
- [Review instructions and foundation contract](docs/foundation-v3.md)

Serve the final review bundle with `python -m http.server 8765 --directory work/review/foundation-v3`, then open `http://127.0.0.1:8765/`. Automated HTTP and Node viewer checks pass; browser UI automation was unavailable, and the 42 candidates remain drafts pending human visual approval.

## Runtime locomotion

Creatures walk with runtime foot placement: the game moves the creature and the Unity package steps its feet with Animation Rigging IK, so feet stay planted at any speed and on slopes. Each skeleton publishes its natural walk, run and maximum speeds in its catalog `locomotion` block for the game to use. Draggers crawl by hauling a grounded torso with their arms; limbless serpentines undulate in step with their travel. See [docs/locomotion.md](docs/locomotion.md) for the model, the Unity components, the review capture tool and the verification.

## Real parts

Meshy scout meshes from the private `synaptic-sea-asset-archive` become production parts through
`critter part import`. The command cleans the mesh, straightens it along its geodesic centerline,
maps its natural joints onto the binding profile, weights it and exports the usual FBX/GLB plus a
base-colour PNG. Source records keep only the archive path, SHA-256 and fit parameters: Meshy output
may be paid-private and this repository is public. `critter part review` deforms a part through
every clip on the skeletons that accept it and compares it with the placeholder it replaces. See
[docs/parts.md](docs/parts.md).

## Existing v2 workflow

The frozen v2 assets are archival compatibility evidence. Their original recipe, assembly, and packaging commands require the archived v2 package/library version; the active v3 `cc-gen-3` source dispatcher rejects v2 inputs. The v2 workflow is therefore not a way to build the current v0.2.0 package.

Current v3 normal recipe generation only uses approved candidates. The rebuilt 42 candidates remain drafts pending visual approval, so a normal production pool can be empty during this review phase. `critter recipe golden` and `recipe sweep --review-drafts` use an explicitly in-memory approved copy for deterministic fixture and review coverage without changing source status. `critter library pack` also requires a fresh catalog and matching assets before packaging.

When an archived v2 package is available, it is dependency-free at runtime and is imported through **Tools > Critter Crafter > Import Library Zip…**. The game owns world movement; clips move bones and do not apply root motion.

## Status

The v3 foundation with runtime locomotion covers 13 archetypes (39 draft candidates). Locomotion for every family has been reviewed visually in Unity captures; the tentacle radial archetype was retired. The delivery notes below predate that work.

For the current state, decisions and next steps, start with [docs/handoff.md](docs/handoff.md).

The v3 foundation implementation and its draft candidates are in review. Automated schema, generator, motion, export, Unity, freshness, and package verification passed; independent visual approval remains open. Candidate visual quality requires human approval, and the acceptance matrix in `work/rebuild-foundation/acceptance.md` remains the source of delivery status.
