# Real parts (M2)

A **real part** is a body part fitted from a sourced mesh: today, a Meshy download in the private
`synaptic-sea-asset-archive`. It binds exactly like a placeholder part: same part space, same `b0..bN`
chain, same FBX/GLB transports. It also carries its source's base-colour texture. Recipes, the
Blender assembler and the Unity assembler treat it like any other production part.

## Licensing: never publish the bytes

Meshy outputs in the archive may be `paid-private`. **This repository is public.** So:

- a source record (`data/parts/<part_id>.part.json`) stores only the archive-relative path, the
  source SHA-256, the fit parameters and the measured envelope;
- every derived mesh, texture, preview and review render is build output under the gitignored
  `library/` and `work/` directories;
- `library pack` zips are release assets for the private game. Don't attach them to a public release.

The archive is found through `CRITTER_ASSET_ARCHIVE`, then `[paths].asset_archive` in `critter.toml`,
then a sibling clone named `synaptic-sea-asset-archive` next to this repository.

## Commands

```powershell
# Fit a mesh onto a binding profile and write its source record (status draft).
uv run critter part import procedural-biomass-assembly/artifacts/meshy_mcp_scout/biomass_insect_leg_v1/01a0c114-0370-7365-a89c-30082aacfb18 `
    --part-id meshy_insect_leg_a_v1 --profile insect_leg4_articulated --axis +x --tag insectoid --role locomotor
uv run critter part list                      # status, archive availability, last QA verdict
uv run critter part refit --all               # re-fit records with their own parameters (after fitter changes)
uv run critter library build                  # builds real parts with everything else
uv run critter part review meshy_insect_leg_a_v1   # deform through clips on accepting skeletons
uv run critter part approve meshy_insect_leg_a_v1  # needs a passing review of the current build
uv run critter part reject meshy_insect_leg_a_v1
```

`import` writes `work/parts/<id>/preview.png`, which shows the fitted part from its side, top, end and
three-quarter views in part space. `review` writes `work/review/parts/<id>/qa.json` and `sheet.png`.
The sheet shows the assembled creature at idle, walk (two phases), attack and hit. `review` also
writes `<skeleton>_mixed.fbx` and `.glb`: the first skeleton with the real part on every accepting
branch and all eight clips, for a look in Unity or any viewer before approval.

Approving a part makes it generatable, which changes the golden recipes. Afterwards run `critter recipe
golden`, copy the goldens into the Unity package and rebuild the library.

An approval is bound to what was reviewed:

- `approve` refuses a review whose inputs have changed since it ran. The inputs are:
  - the part build and the reviewed skeletons' built clips;
  - every part in the compared recipes;
  - the QA code and thresholds.
- `approve` records the part pipeline fingerprint in `real.approved_pipeline`. After a fitter change,
  `library build` stops with `CC_APPROVAL_STALE` rather than put unreviewed geometry into generation.
- `refit` returns an approved part to `draft`. Build, review and approve it again.

## Pipeline

`blender/ops_realpart.py` runs these steps, and each one is deterministic for the same source bytes:

1. **Import:** the binary glTF (Meshy downloads have no extension, so it checks the magic bytes).
   Meshes are joined and transforms applied.
2. **Clean:**
   - merge the UV-seam duplicate vertices the glTF importer creates (1e-5 of the diagonal);
   - drop loose geometry and islands of four triangles or fewer, while keeping spikes, claws and teeth;
   - recalculate normals;
   - decimate to 97% of the triangle budget if needed. High-poly Meshy outputs have 250k–830k triangles.
3. **Fit** (`parts/fit.py`, pure Python, unit-tested without Blender):
   - **Centerline.** Geodesic distance from the root end (the extreme 3% along `fit.axis`) and from
     the far tip are combined into one parameter, `g_root − g_tip`. Its level sets cut the shape into
     near-perpendicular cross-sections even through a bulky shoulder or coxa. Climbing a spike raises
     both distances equally, so spikes, tendrils and splayed fingers keep the parameter of the
     cross-section they grow from and stay rigid. Area-weighted face centroids per parameter bin form
     the centerline, which is smoothed and then re-binned three times by arc length.
   - **Straighten.** Each vertex is expressed in a rotation-minimising frame at its arc length and laid
     along +Z. The offset's tangent component is added to its Z, which corrects the level-set lead or
     lag on bends to first order.
   - **Up.** Part +Y (dorsal) is the convex side of the modelled bend when the bend is clear (sagitta
     ≥ 4% of the chord). Otherwise it falls back to `fit.up` / `fit.up_fallback`. The profiles' knee
     and elbow flexion folds toward −Y, so a leg modelled bent flexes the way it was modelled.
   - **Joints.** `fit.joints_n` gives the mesh's natural interior joints as fractions of its length.
     They are mapped piecewise-linearly onto the profile's bone fractions. The default is the profile's
     own fractions.
   - **Envelope.** One uniform scale sets the declared `length_m`. With `radial_scale: auto` (the default
     for chains), the proximal cross-section (10–50% of the length, 90th-percentile radius, median over
     slices) is scaled to the profile girth. The scale is clamped to 0.6–1.8 and recorded. Heads keep
     their proportions (`radial_scale: 1`).
   - **Weights.** These follow the chain coordinate:
     - `flesh` (limbs): bands sized by the local radius;
     - `smooth` (insect legs, tentacles): rubber-hose blending between bone centres;
     - `jointed` (opt-in): rigid segments, narrow joint bands;
     - `single` (heads).

     There are at most two influences per vertex, normalized. Why insect legs are `smooth`: the scout
     leg is thick and low-poly at its joints, and narrow rigid bands collapsed the inner knee in the
     crouched stance (p01 0.49 against the placeholder's 0.72).
   - **Edge loops.** The straightened mesh is cut with planes where the weights change slope: at
     each joint and both band edges, or at joints and bone centres for `smooth`. This way a low-poly
     mesh bends at a loop instead of across one long triangle. Band-edge loops are dropped first if
     the cuts would exceed the triangle budget.
4. **Export:**
   - sharp edges above 40°, smooth elsewhere;
   - the texture resized to the budget (1024) and written as `<id>_albedo.png`;
   - a straight `b0..bN` armature;
   - FBX and GLB through the same pinned `rigkit` exporters as placeholders;
   - a master `.blend` under `work/masters/`.

The build refuses a changed source (`CC_REALPART_SOURCE_CHANGED`). It also refuses a rebuild whose
envelope drifts more than 2 mm from the record (`CC_PART_FIT_DRIFT`). `critter part refit <id>` (or
`--all`) re-fits records with their own parameters and refreshes them. Real parts have their own
pipeline fingerprint, so changing the fitter doesn't rebuild placeholders.

### Fit parameters (`real.fit`)

| Key | Meaning | Default |
|---|---|---|
| `axis` | Source-mesh glTF axis from the attachment end to the tip | required |
| `up` | `auto` or a signed source axis for part +Y | `auto` |
| `up_fallback` | Signed axis used when `auto` finds no clear bend | first of +y, +z, +x not along the root |
| `straighten` | Fit a bent centerline (false: rigid, straight axis) | true for chains, false for heads |
| `trim_n` | Root and tip quantiles of the chain coordinate that define 0 and `length_m` | `[0.005, 0.995]` |
| `joints_n` | Natural interior joint fractions | the profile's fractions |
| `radial_scale` | `auto` or a number | `auto` for chains, 1 for heads |
| `weights` | `flesh`, `smooth`, `jointed`, `single`, or opt-in `learned` | `flesh` for limb3, `smooth` for insect_leg4 and tentacle8, `single` for heads |
| `joint_loop_mode` | Geometry-only loop/band policy (`flesh`, `smooth`, `jointed`, `single`), independent of learned weight selection | template's analytical mode |
| `mirror_x` | Mirror across part X (a left/right variant) | false |
| `root_center` | `slice` (centroid of the root 5%) or `bbox` | `slice`; `bbox` for straight parts |
| `sharp_angle_deg` | Edge angle rendered sharp | 40 |

## Offline learned weights (owner-managed worker)

Preparation and production share the same import, cleanup, fit and post-joint-loop geometry path.
Preparation computes **no weights**. It freezes canonical vertex indices, polygons, UV loops and
the exact profile bind data; changing only `real.fit.weights` or learned artifact metadata does not
change its identity. Geometry-affecting fit settings, profile, source bytes and producers do.

```powershell
uv run critter authoring skin prepare --part meshy_insect_leg_a_v1 --out work/authoring/jobs/insect
# Repeat for meshy_frayed_arm_a_v1, meshy_animal_skull_a_v1 and meshy_tentacle_a_v1.
# Transfer jobs manually to the owner's private worker. No automatic upload/provisioning.
```

SkinTokens runs in a separate Python 3.11 environment with torch 2.7.0/cu128 and an NVIDIA GPU
with at least 16 GB. Set `CRITTER_SKINTOKENS_ROOT` to the official checkout at
`273b691d35989d71cd17ff2895fdc735097b92d1`; install its documented requirements and both
official MIT checkpoints locally. The adapter checks their exact SHA-256 hashes before inference.
It supplies `skeleton_tokens` to TokenRig's actual `generate` API, captures/inverts preprocessing
and maps skin columns bijectively back to original profile indices. It never calls `use_transfer`,
adopts decoded joints, or exports a replacement mesh/armature.

```sh
python tools/authoring_worker/cli.py skin --job /owner/jobs/insect --out /owner/results/insect
```

Return the entire result directory (including `input/`) to the host:

```powershell
uv run critter authoring skin import --part meshy_insect_leg_a_v1 --result work/authoring/results/insect
uv run critter library build
uv run critter part review meshy_insect_leg_a_v1
```

Import verifies manifests, confined paths, hashes, unchanged fitted geometry and profile identity.
Blender reads `weights.npz` CSR arrays without pickle, rejects invalid topology/indices/values,
keeps the strongest four influences with bone-index tie breaks, and renormalizes. It retains the
canonical mesh/UVs/materials/bones. Imported records are **draft**, never automatically approved.
Missing model/artifact errors are `CC_SKIN_MODEL_UNAVAILABLE`; stale inputs/bytes are
`CC_SKIN_STALE`; topology, bind and influence failures use `CC_SKIN_TOPOLOGY`,
`CC_SKIN_BIND`, and `CC_SKIN_WEIGHTS`. No analytical fallback is presented as learned output.

For a separately selected UniRig comparison, prepare with `--backend unirig`, set
`CRITTER_UNIRIG_ROOT` to official revision `6793c6640ff01c8fb389f3993434124bb43d2933` in
its own upstream environment, and use the same worker command with its official skin checkpoint
(`--checkpoint` can locate it). This is a distinct proposal under the same weight-only contract,
not a fallback. Model/checkpoint/settings/source hashes are retained in the result.

Public asymmetric transfer smoke, without private Meshy bytes:

```powershell
uv run python tools/authoring_worker/skin_fixture.py prepare --job work/authoring/jobs/public-asymmetric
# Run the same real worker command after manual transfer, then return its result.
uv run python tools/authoring_worker/skin_fixture.py validate --job work/authoring/jobs/public-asymmetric --result work/authoring/results/public-asymmetric
```

On 2026-10-02, actual Blender preparation completed for all four owned parts under
`work/authoring/jobs/<part_id>/`, and for the public asymmetric fixture under
`work/authoring/jobs/public-asymmetric/`. Starting the worker on the fixture failed explicitly
with `CC_SKIN_MODEL_UNAVAILABLE` because the upstream checkout is absent.
GPU inference and improved deformation have **not** been demonstrated. Acceptance still requires
returned real-worker results, the unchanged relative QA judge below, comparison against
smooth/flesh/jointed weighting, and owner review showing improved distortion. The local 8 GB
laptop is not the supported model worker.

Primary APIs: [SkinTokens](https://github.com/VAST-AI-Research/SkinTokens),
[official MIT checkpoints](https://huggingface.co/VAST-AI/SkinTokens),
[UniRig](https://github.com/VAST-AI-Research/UniRig).

## Matching

`girth_m` is always the profile ratio times `length_m`. Every branch of the profile has exactly that
ratio, so girth never blocks a match: length does. The default `length_m` is the median branch length
of the profile, rounded to 5 cm. It covers branches within 0.8–1.25× of that. To cover more
branches, import the same source again as a second record with a different `--length`.

Sides are `symmetric` by default: one mesh serves left and right branches. `--mirror-x` makes a true
mirror variant when handedness shows.

Branches accept parts by category, template and profile. Arms and legs are different profiles
(`limb3_brachial` for arms, `limb3_plantigrade`/`limb3_digitigrade` for legs), so an arm part fits only
arm branches.

## QA

`critter part review` picks up to three skeletons that accept the part (one per family first). It
assembles each skeleton's draft-review recipe with the real part on every accepting branch, plays
every baked clip (every second frame) and measures the real part's skinned surface against the
straight bind pose:

- **edge strain:** p01 and p99 of deformed over bind edge length, worst frame per clip;
- **flipped faces:** faces whose normal turned against the normal carried by their dominant bone.

The baked walk and run clips leave the locomotor chains at neutral, because Unity's runtime IK
places the feet. So for `legs`-mode skeletons the review adds a `stride_ik` pseudo-clip. Blender IK
starts from the neutral stance and puts every foot at the front and back of its stroke (`home_m ±
stroke_m / 2`) and at the swing apex (`home_m + clearance_m`), approximating the knee range the
runtime reaches.

The same recipe with the original placeholders is measured too. The real part passes when it is no
worse than that placeholder: p99 ≤ max(1.6, 1.25 × reference), p01 ≥ min(0.6, 0.8 × reference) and
flipped ≤ max(0.5%, 1.5 × reference + 0.2%). The placeholder already survived the anatomy review, so
this compares like with like.

## Current real parts

| Part | Source | Profile | Length | Notes |
|---|---|---|---|---|
| `meshy_insect_leg_a_v1` | `biomass_insect_leg_v1/01a0c114…` | `insect_leg4_articulated` | 0.90 m | modelled with a ~110° knee; straightened, dorsal spikes kept |
| `meshy_frayed_arm_a_v1` | `biomass_human_arm_v1/01a0c10e…` | `limb3_brachial` | 0.85 m | frayed shoulder tendrils, clawed hand |
| `meshy_animal_skull_a_v1` | `biomass_animal_skull_v1/01a0c171…` | `head1_neck` | 0.30 m | rigid; snout on +Z |
| `meshy_tentacle_a_v1` | `biomass_cephalopod_tentacle_v1/01a0c123…` | `tentacle8_flexible` | 1.60 m | S-curve straightened; suckers ventral |

Scout meshes not imported yet:

- `01a0c11e…` (tentacle): coiled into a loop, which the fit can't unwind. Reject it or regenerate.
- The `raw.glb` arms: untextured.
- The high-poly tentacles (`01a0c127…`, `12a…`, `12d…`, `133…`), skull (`01a0c167…`) and stump
  (`01a0c137…`): they need the decimation path. It is implemented but not yet reviewed visually.
- The frayed stumps and shoulders: connector candidates. Connectors are two-bone skinned parts with a
  different fit, not handled yet.

## Native organic connectors

Enhanced v3 connector authoring uses Blender 5.2 native SDF grids; ordinary parts and the frozen
v2 connector sources are unchanged. Optional source `connector.surface` compiles to
`connector_surface`: `{"method":"sdf_grid","voxel_divisions":24,"fillet_iterations":3,"bulge":1.08}`.
Divisions are 12–64, fillet iterations 0–8 and bulge 1–1.2. New compiled reference connectors use
these settings by default. This is an offline baked surface, not a Unity runtime mesher or a fusion
of the adjoining production meshes.

The closed collar remains in its authored elliptical `dimensions_m` envelope and axial `span_m`.
The owner-selected taper redesign replaces the rejected fixed 0.83 waist/full central bulge.
The new waist follows a span-wide Jacobian radius bound derived from the profile's tested
single-axis angle extrema, with rounded caps and parent-side fullness away from the largest
smoothstep weight gradient. At a symmetric span midpoint the bound is
`radius <= (1-margin)*span_length/(3*tan(angle/2))`. Inputs retain a 60% relative Jacobian
margin; native union/fillet is intersected with the same safe-envelope SDF before meshing.
Simplified triangle interiors must retain the looser 25% numerical margin, and their actual
single-axis deformed normals are checked without changing deformation QA tolerances.
Mesh-to-SDF uses the smallest envelope radius/span divided by the voxel divisions and a
three-voxel narrow band. Padded grids exceeding 1,024 cells on any axis or 256³ total cells fail,
never silently coarsen. Native union and concave filleting evaluate to a mesh simplified only to
the existing connector budget (300 triangles for reference inventory). Nonmanifold, degenerate,
inverted or out-of-envelope results fail before export. Blender coordinates are converted back
to the canonical frame before axial smoothstep weights; the identity socket, scale-one assembly,
`b0=parent`, `b1=child` and normalized two-group contract remain unchanged.

Nine `organic_collar_{s,m,l}_{straight,bulged,asymmetric}_v1` records are **draft** procedural
fixtures using the released `limb3_brachial` profile. Their girths match the compact, balanced
and elongated plantigrade biped arms respectively. The asymmetric records have unequal
parent/child spans and reduced transverse thickness, not guessed socket translations.

Review uses the existing runner op `connectorqa`. Its complete inputs are produced by
`library.commands.connector_review_inputs(built_catalog, part_id, library_dir, review_dir)`.
For example, after a verified v0.3.0 library build:

```powershell
uv run python -c "import json; from pathlib import Path; from critter_crafter.library.commands import connector_review_inputs; from critter_crafter.blender.runner import run_op; root=Path('library/biomass_core-v0.3.0'); c=json.loads((root/'catalog.json').read_text()); p='organic_collar_m_bulged_v1'; a=connector_review_inputs(c,p,root,Path('work/review/connectors')/p); print(run_op('connectorqa',a)['result'])"
```

The op exports paired loft/SDF FBX and GLB, checks the actual asymmetric FBX attachment and
parent/child deformation, then measures all frames of all eight clips, bind/neutral, IK stride
extremes and selected socket profile-limit extremes. Stretch/compression/flipped-face acceptance
matches `parts.commands.judge`; surface penetration is limited to the existing 5 mm tolerance.
Paired bind, neutral, clip and limit stills use the same material and shared cameras/bounds.
Failed QA writes `qa.json` then raises `CC_CONNECTORQA_FAILED`; reviewed distortion, buried caps,
socket gaps and ground collision still require owner visual judgment. `structure_only: true`
exercises export/binding without claiming complete deformation acceptance.

Review input identity includes exact built assets, recipe, profile, settings and producer hashes;
normal part build identity includes the SDF producer/settings. Changed inputs invalidate old
cached artifacts. The initial fixed-waist implementation passed 23 structural/export cases but
failed full eight-clip and profile-extreme QA on all nine shoulder variants for flipped faces;
the large bulged and small asymmetric variants also failed compression. These failures are
retained under `work/review/connectors/`, not relabeled as acceptance. In the balanced medium
bulged fixture,
recorded SDF faces flipped at shoulder ±120°/±90° extremes (maximum 7.5%) and attack frame 8
(0.667%); the matched original loft remained at zero flips, including a triangle-only diagnostic.
Those failures preceded the owner-authorized taper redesign. The redesigned producer applies
to all new authored/reference connectors, not a substitute lower-angle fixture set. The 35
focused structural/analytical tests passed, and all nine redesigned variants completed fresh
FBX/GLB export, asymmetric assembly/bind checks, every frame of all eight clips, IK stride
extremes and the unchanged profile-limit extrema. All nine passed the existing deformation
criteria: zero flipped faces and surface penetration, p01 0.54486–0.60731, and worst p99 1.44003.
Every exported SDF fixture had 300 triangles; maximum asymmetric attachment error was
5.96e-8 m. Paired images share cameras/materials and live under
`work/review/connectors-safe-taper/<part_id>/`; aggregate reports are in
`work/enhancements-connectors-safe-taper-results.json`.

The whole-library refresh exposed a separate coarse-triangle twist reversal in
`reference_connector_amalgam_hauled_s0013_v3_flail2_v1`; a positive continuous Jacobian
alone does not guarantee deformation-safe simplified triangle chords. Simplification now
tests each budget-compliant candidate against the unchanged manifold, envelope and actual
profile-extreme normal guards before accepting it. Up to 16 decreasing collapse ratios select
the highest-detail passing result; no passing topology means explicit failure. That exact
reference subsequently exported 284 triangles/144 vertices with two bones, at most two
influences and normalized weights, and passed its asymmetric fixture. The focused 35-case
suite includes this regression alongside all nine authored variants and binding checks.

This is measured geometry/deformation evidence, not owner approval or completed visual
acceptance. All nine source records remain draft. The enhanced v0.3.0 reference/placeholder
refresh and final whole-library QA are separate integration steps. Keep the original failed
reports/renders, and judge continuity, buried caps, visible gaps and collision from the paired
images. QA records worst-pose witnesses; its continuous edge-interval certificate covers
triangle interiors rather than merely sampling vertices or comparing unrelated axial locations.

The proof and existing QA cover each selected single-axis profile extreme and all eight
authored clips, not every Cartesian combination of independent Euler limits. For example,
the brachial profile permits a combined `(swing_x,twist,swing_y)=(90,90,90)` rotation of
180°. At half weights, two-bone linear blend skinning then loses two transverse dimensions
for any positive-radius surface, including the old loft. This shared LBS limitation is not
solved by tapering; future combined motion must pass its actual deformation review.
Do not rebuild or mutate immutable v0.2.0 exports; all new local output belongs to v0.3.0.

## Known limits

- Linear-blend skinning with axial weights is simple and robust for tubes. It's not a substitute for
  the voxel-proxy + bone-heat + Robust Skin Weights Transfer route from the tools research. That route
  is the upgrade if review shows pinching at sharp knees.
- Straightening a tight bend stretches the inside and compresses the outside by up to about 1.5×. The
  fit's `strain_p99`/`strain_max` report it; texture stretch follows.
- The Unity texture binding (`asset.albedo_png` → `_BaseMap`/`_MainTex`) was written without a Unity
  editor in the loop. Run the EditMode tests and an import of a built library before relying on it.
