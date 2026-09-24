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
| `weights` | `flesh`, `smooth`, `jointed`, `single` | `flesh` for limb3, `smooth` for insect_leg4 and tentacle8, `single` for heads |
| `mirror_x` | Mirror across part X (a left/right variant) | false |
| `root_center` | `slice` (centroid of the root 5%) or `bbox` | `slice`; `bbox` for straight parts |
| `sharp_angle_deg` | Edge angle rendered sharp | 40 |

## Matching

`girth_m` is always the profile ratio times `length_m`. Every branch of the profile has exactly that
ratio, so girth never blocks a match: length does. The default `length_m` is the median branch length
of the profile, rounded to 5 cm. It covers branches within 0.8–1.25× of that. To cover more
branches, import the same source again as a second record with a different `--length`.

Sides are `symmetric` by default: one mesh serves left and right branches. `--mirror-x` makes a true
mirror variant when handedness shows.

Branches accept parts by category, template and profile. There are no tags on the current skeletons,
so a `limb3_plantigrade` arm also fits plantigrade **leg** branches: bipeds and quadrupeds then walk on
hands. Give leg branches `accepts.tags_any` (and legs a matching tag) if that's unwanted.

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
| `meshy_frayed_arm_a_v1` | `biomass_human_arm_v1/01a0c10e…` | `limb3_plantigrade` | 0.85 m | frayed shoulder tendrils, clawed hand |
| `meshy_animal_skull_a_v1` | `biomass_animal_skull_v1/01a0c171…` | `head1_neck` | 0.30 m | rigid; snout on +Z |
| `meshy_tentacle_a_v1` | `biomass_cephalopod_tentacle_v1/01a0c123…` | `tentacle8_flexible` | 1.60 m | S-curve straightened; suckers ventral |

Scout meshes not imported yet:

- `01a0c11e…` (tentacle): coiled into a loop, which the fit can't unwind. Reject it or regenerate.
- The `raw.glb` arms: untextured.
- The high-poly tentacles (`01a0c127…`, `12a…`, `12d…`, `133…`), skull (`01a0c167…`) and stump
  (`01a0c137…`): they need the decimation path. It is implemented but not yet reviewed visually.
- The frayed stumps and shoulders: connector candidates. Connectors are two-bone skinned parts with a
  different fit, not handled yet.

## Known limits

- Linear-blend skinning with axial weights is simple and robust for tubes. It's not a substitute for
  the voxel-proxy + bone-heat + Robust Skin Weights Transfer route from the tools research. That route
  is the upgrade if review shows pinching at sharp knees.
- Straightening a tight bend stretches the inside and compresses the outside by up to about 1.5×. The
  fit's `strain_p99`/`strain_max` report it; texture stretch follows.
- The Unity texture binding (`asset.albedo_png` → `_BaseMap`/`_MainTex`) was written without a Unity
  editor in the loop. Run the EditMode tests and an import of a built library before relying on it.
