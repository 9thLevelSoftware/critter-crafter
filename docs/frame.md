# Coordinate frames (normative)

## Canonical catalog frame: `gltf_rh_yup_zfwd_m`
Every number in a critter-crafter catalog (bone heads/tails, snap positions, snap
rotations, collision shapes) is expressed in the glTF frame:

* right-handed, **+Y up**, **+Z forward**, **+X left** (glTF convention), meters.
* Quaternions are `[x, y, z, w]`.

### Part space
Every part is authored in its own part space:

* origin = the part's attachment point (where its branch root / snap sits),
* **+Z = along the part's length** (away from the attachment),
* **+Y = the part's "up" reference** (dorsal side / face direction),
* `dimensions_m = [width_x, thickness_y, length_z]`.

### Skeleton space
Skeletons are authored standing on the ground plane at the origin, facing +Z.
Each branch has an `origin_m`, a `direction` (part +Z maps onto it) and an `up`
(part +Y maps onto it after orthonormalisation). The **snap frame** of a branch
is the rigid transform that maps part space onto that branch.

## Blender
Blender is Z-up. Parts and skeletons are authored so that the catalog's forward (glTF +Z) points
along **Blender +Y**:

    blender = (-x_gltf, z_gltf, y_gltf)        gltf = (-x_bl, z_bl, y_bl)     (an involution)

The mapping is a proper rotation, so face winding and chirality survive it. It lives in exactly one
place: `src/critter_crafter/blender/frame.py`. GLB exports turn the root objects 180 deg about Blender Z
for the export only (see `rigkit.export_glb`), so GLB files are in the catalog frame too.

## Unity
Unity's FBX importer reads the FBX axis metadata and always lands Blender -Y ("front") on Unity -Z;
the exporter's `axis_forward`/`axis_up` flags only change the rotation Unity puts on the model's root
node, never the result in the model's parent space. Measured with `FrameProbe` (M0):

    unity = (-x_gltf, y_gltf, z_gltf)          quaternion: (x, -y, -z, w)

i.e. catalog +Z forward is Unity +Z forward (the same convention glTFast uses). Consequences:

* The **catalog frame in Unity is the model root's parent space**, not the model root's own space
  (the importer may leave a root rotation such as (270, 180, 0) on skeleton and part models).
  `CreatureAssembler` uses the creature root; `CreatureAssembler.MeshToPart` and
  `FrameProbe.CatalogFramePoint` apply the root's local TRS explicitly.
* The conversion lives in exactly one place, `CritterFrame` (Runtime), and is locked by the Unity test
  `FrameProbeSnapsCoincideWithBranchRootBones` (every converted snap within 1e-4 m of its imported
  branch-root bone) plus the import-time probe in `LibraryImporter`.

## Binding rule (why bone axes don't matter)
A part mesh is bound to skeleton bones with

    bindpose_i = inverse(skeletonBone_i.bindWorld) * snapWorld * Scale(s) * meshToPart

so the imported bone axis conventions of FBX/Unity never leak into placement:
only bone *positions over time* matter. `s = branch.length_m / part.length_m`
(valid range 0.8–1.25). Skeleton branches are straight in the rest pose; bends
come from animation clips.
