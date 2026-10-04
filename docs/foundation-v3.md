# Foundation v3

This document describes the implemented v3 foundation contract and its current verification state. It is package/library version `0.2.0`, schema version `3.0.0`, and generator `cc-gen-3`. The v3 candidates remain drafts pending visual approval (live counts are in [roadmap.md](roadmap.md); the delivery notes below were written when there were 42).

## Source dispatch and identity

`data/library.json` is the dispatch document. `load_sources(data_dir)` reads its schema version first and loads only authoring documents with the exact active version. The v2 source set and `tests/golden/` fixtures stay available as historical compatibility evidence. The active v3 build rejects unsupported library, recipe, and generator versions before looking up records. The archived v2 workflow must use its original package/library version; it is not a v2 compatibility mode inside active `cc-gen-3`.

Profiles in `data/binding_profiles/` are immutable chain definitions. The current profile set is:

```text
appendage1_terminal
core1_body
head1_neck
insect_leg4_articulated
limb3_brachial
limb3_digitigrade
limb3_plantigrade
spine3_axial
tentacle8_flexible
```

Each profile has an ordered joint list, normalized bone fractions, canonical frames, axes, limits, and landmarks. The compiler computes a lowercase SHA-256 over canonical profile JSON with any existing hash omitted. `registry.json` locks each `(binding_profile_id, binding_profile_version)` to that hash. A changed profile under the same identity fails with `CC_BINDING_PROFILE_IMMUTABLE`; edits require a version or ID change and a registry update.

## Branch binding and neutral pose

Every v3 branch carries the exact profile identity triple, side, length, girth, and a socket. A socket is expressed in the named parent joint's local frame and always includes position and quaternion orientation. A root branch uses the reserved parent joint `root`; a child branch names a semantic joint in its parent profile, which is mapped by index to the compiled bone name.

The skeleton's `neutral_pose` is independent of the chain profile. It contains one local quaternion delta for every compiled bone, in compiled bone order, plus `root_offset_m`. The Blender rest-bone basis is preserved: local +Y follows the bone, +Z is the orthogonal up axis, and +X is `Y cross Z`. Runtime assembly binds geometry to the straight rest skeleton first, then applies the neutral local deltas once. FBX validation accounts for the exporter's local `Ry(180°)` bone-coordinate reparameterization while comparing the recovered semantic basis; GLB uses the canonical rest basis directly.

### Capabilities and semantic actions

Regenerated v3 source branches require a unique `capabilities` array drawn from `support`, `strike`, `bite`, `whip`, `grasp`, and `slide`; an empty array is valid for decorative branches. Every contact-bearing branch declares `support`, and sliding/body-contact branches also declare `slide`. Optional `effector_slot` values are unique within the skeleton. Action profiles select exactly one slot with the required capability, never a branch-name or family fallback. Resolved plans still publish concrete branch IDs, bone names and contact IDs, so renaming an effector does not change its trajectory or support schedule. Amalgams explicitly author `primary_strike`.

An optional `anatomy.action_support` block contains `released_contact_ids` and `minimum_preserved` for generated topology. It releases exactly the selected effector's declared contacts, preserves every other support contact, records that preserved remainder, and must leave the authored `traits.min_support` floor. Without this block, curated action requirements remain unchanged (including seven preserved crawler contacts and five radial contacts). Source validation, compilation and action resolution reject missing/duplicate slots, incompatible capabilities and invalid support policies. Unity catalog data carries these fields; frozen compiled libraries can still load their baked assets without reauthoring or compatibility aliases.

The capability migration smoke exercised all 91 generated sources: geometry/neutral-pose/gait/provenance matched their previous source records after excluding capability/slot metadata and owner review/status, all 91 actions resolved, all produced eight-clip motion plans, and a temporary regenerated source tree passed schema and semantic validation. This is host authoring evidence, not a Blender bake or Unity visual review. Changed sources return to `draft` when regenerated; only the owner approves them.

## Parts and recipes

Production parts carry profile ID/version, side, length, and girth. Compilation adds the locked profile hash and marks them `inventory_kind: production`; only approved production parts are generatable. The compiler also creates deterministic reference inventory for each branch requirement, so anatomy and review can proceed while production coverage is incomplete. Connectors are optional versioned two-bone skinned parts. They use `skinned_parent_child@1.0.0`; `b0` maps to the parent attachment bone and `b1` maps to the branch root bone. Connector matching requires exact chain profile identity/hash, compatible side, exact girth, normalized two-influence weights, preserved submeshes and materials, and the identity local transform. Size class requests a connector but does not establish compatibility.

Enhanced connector sources optionally author native-SDF surface settings; new compiled v3
connectors publish `connector_surface` and generated reference inventory defaults to this strategy.
The physical ellipse/span, identity socket and exact parent/child bone remapping stay authoritative.
SDF generation is offline: Blender exports a closed, budgeted mesh with analytical two-bone
weights, and Unity consumes the same FBX binding interface without running an SDF mesher.
See [parts.md](parts.md#native-organic-connectors) for limits, paired loft/SDF review and pending
visual acceptance. Frozen previously published v0.2.0 assets are not rewritten.

The owner-authorized deformation-safe taper supersedes the initially prescribed full waist,
which failed actual shoulder review. Native union/fillet/intersection now bounds the surface
using the tested profile's smoothstep-LBS Jacobian along the full unchanged span; sockets,
two-bone weights, physical envelope and QA criteria did not change. All nine draft S/M/L
straight/bulged/asymmetric variants passed fresh exports, asymmetric bind checks, all eight
clips and profile extrema: zero flipped faces/penetration, p01 0.54486–0.60731 and p99 at most
1.44003, each at 300 triangles. The focused suite passed 35 cases, including the default flail
reference that exposed a coarse-triangle twist reversal during the whole-library refresh.
Constraint-aware simplification retained its unchanged guards and exported a passing
284-triangle mesh within the same envelope and two-bone contract. The original failed reports
remain available; measured success does not approve any content or replace owner visual review.
This covers actual clips and selected single-axis extrema, not arbitrary combined rotations:
the shared two-bone LBS singularity at 180 degrees remains documented in `parts.md`.

Recipe matching uses one uniform length scale in the inclusive 0.8–1.25 range and checks scaled girth within 10 percent. Side and exact profile identity must match. The hard budgets are 120 bones, 16 parts, 30,000 triangles, and 4 influences per vertex. A v3 recipe contains schema/library identity, `cc-gen-3`, the profile identity triple on each fill, and derived scale diagnostics.

## Candidate generation

`critter skeleton vary` generates deterministic anatomy archetypes across compact, balanced, and elongated presets (14 archetypes and 42 candidates now: the first delivery had 14 and 42, the retired tentacle radial took it to 13 and 39, and the one-arm-one-leg dragger brought it back to 14 and 42). The families are biped, crawler, dragger, hexapod, quadruped, radial, and serpentine. Newly generated candidates have status `draft`; generation never auto-approves them. `skeleton status` reports the drafts per family, all awaiting human review.

`tests/golden_v3/` is a parity fixture, not candidate approval. `critter recipe golden` uses an in-memory approved copy so deterministic recipe coverage can be tested without changing candidate status. The old `tests/golden/` files remain untouched.

## Runtime locomotion (supersedes baked walk/run travel)

Walk and run are no longer baked travel clips. Every compiled skeleton carries a `locomotion` block and the Unity package places feet at runtime; walk/run bake as one-cycle overlays driven by the gait clock. See [locomotion.md](locomotion.md). The tentacle radial archetype was retired, leaving 13 archetypes and 39 candidates; statements below about 14 archetypes, 42 candidates and 336 clips describe the earlier delivery.

## Motion, QA, review, and approval

The motion plan emits the eight clips `idle`, `walk`, `run`, `stun`, `telegraph`, `attack`, `hit`, and `death` at 30 FPS, with local bone rotations, contacts, root samples, and loop metadata. `skeleton qa` validates every frame of actual built `motion.json` clips and writes diagnostics. `skeleton review` requires those built assets, then creates a local review bundle with three modes—bones, mannequin, assembled—and four views—front, side, top, three-quarter. Start it with:

```powershell
python -m http.server 8765 --directory work/review/foundation-v3
```

Each reviewed bundle receives a content-fingerprint receipt under `work/review/receipts/`, including a fresh receipt when QA fails. `skeleton approve` requires a passing receipt; `skeleton reject` only requires a current receipt matching the built content. Any source, profile, setting, baked motion, assembled mesh, or review output change makes a prior receipt stale. The review modes are bones/contact overlay, neutral weighted mannequin, and compatible assembled parts; each is presented from front, side, top, and three-quarter views with the actual built clips and travel/grid context.

Unity playback uses the clip timing and nominal speed metadata, with duration-aware rates verified against the requested movement speed. Locomotion contact phases remain aligned in normalized clip time when durations differ, following [Unity's Blend Tree guidance](https://docs.unity3d.com/6000.0/Documentation/Manual/class-BlendTree.html).

## Polish overrides

Generated masters and authored polish are separate. A non-destructive override is stored at `work/polish/skeletons/<id>/override.json`; it records the source fingerprint and points at the authored blend asset. Rebuilds may resolve it only when the fingerprint still matches. Incompatible source changes fail with `CC_POLISH_STALE`; changing the authored polish also invalidates the built asset fingerprint and requires a rebuild. Integration coverage verifies authored key preservation, rest hierarchy and clip completeness, incompatible override rejection, and edited-polish invalidation. Both generated master and authored override remain available for review.

QA treats contact geometry and locomotion drift separately. Planted foot and hand tips may be within a 10 mm anatomical ground-proximity band, while body contacts use a 5 mm band; planted drift remains `max(5 mm, 1% of chain length)`, and penetration remains 5 mm. These bands do not waive drift, support, limit, loop, or transition checks.

## Binding profile releases

Two registries guard profile identity (`index_profiles` in `binding/profiles.py`):

- `data/binding_profiles/registry.json` locks the hash of every profile in the working tree. Editing a profile without updating its registry entry fails with `CC_BINDING_PROFILE_IMMUTABLE`.
- `data/binding_profiles/released_registry.json` is the released baseline. A profile listed there can never be removed (`CC_BINDING_PROFILE_BASELINE`), and any new version of the same profile id must have a higher semver (`CC_BINDING_PROFILE_VERSION`).

`limb3_brachial` is in `registry.json` but not yet in the released baseline, which is intentional: it has not shipped to a consumer. Add it to the baseline at the next `critter library pack` or M5 shipment.

## Delivery state (historical: the first foundation delivery, 42 candidates)

Automated foundation delivery is verified in [delivery-verification.json](../work/rebuild-foundation/delivery-verification.json). The final catalog SHA is `677733d508d911ce3a556d18e904dbd53cd88fe7669d9cf451963ecab93a9745`; all 42 candidates have fresh passing receipts and 336 clips, with 462 reference parts/connectors. Maximum observed planted drift is 0.1485 mm, surface penetration 3.7848 mm, export position error 0.013126 mm, and export angle error 0.0009622°. Actual maxima are 66 bones, 16 parts, 11,664 triangles, and 2 vertex influences. The byte-verified packages are [`critter-library-biomass_core-v0.2.0.zip`](../dist/critter-library-biomass_core-v0.2.0.zip) and [`com.ninthlevelsoftware.crittercrafter-0.2.0.tgz`](../dist/com.ninthlevelsoftware.crittercrafter-0.2.0.tgz); the pack command now refuses stale catalogs or assets. The unique Python test inventory is 245 (`241` existing suite tests plus `4` focused status-predicate regressions; no full-suite rerun followed those focused checks), with the existing Python/Unity reference approval pairs kept exact. Static review rendering produced 103 outputs and motion previews produced 145 outputs including 42 GIFs; serve the final bundle with `python -m http.server 8765 --directory work/review/foundation-v3` and open the verified local review URL. Automated delivery passed, while independent human visual approval remains pending and browser UI automation was unavailable.
