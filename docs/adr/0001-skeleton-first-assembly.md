# ADR 0001: Skeleton-first skinned assembly

Status: Accepted (2026-09-22)

## Context
The Godot prototype (`the-synaptic-sea`, branch `feature/procedural-biomass-threat-assembly`)
assembled **rigid** parts into a free-form socket graph and animated them with procedural swings. We
need skinned, rigged output that works with Unity's Animator in any game, and that stays portable.

## Decision
* A third library of **rigged skeletons** is the backbone. Each skeleton is a set of branches built
  from standard **branch templates**, and each branch has a snap point that accepts some part
  categories, templates and tags. Nesting (a claw on an arm tip) is a child branch, so recipes stay
  a flat list of fills.
* Parts are skinned to their template's bones (`b0..bN`). At assembly they bind to the skeleton's
  branch bones by index, clamped to the branch length, using
  `bindpose_i = inverse(bone_i.bind) * snap * Scale(s) * meshToPart`. Here
  `s = branch.length / part.length`, restricted to 0.8–1.25 and checked with integer mm arithmetic.
  Skeleton branches are straight in rest pose; bends come from clips.
* Connectors are skinned to two bones: the parent attach bone and the branch root.
* Every creature has **one skeleton**, which gives a Generic Animator with baked clips per skeleton
  and one AnimatorController per skeleton. There is no root motion.
* Catalog numbers use the glTF frame. The Unity conversion lives in `CritterFrame` and is locked by
  tests (see `docs/frame.md`).

## Consequences
* Variety comes from many skeletons (procedural `skeleton vary` in M1), optional and mirrored
  branches, and parts × connectors.
* Parts built from one template can serve any branch of a compatible category and length.
* Animation quality is per skeleton (the clip generator, then optional hand polish in Blender)
  rather than per creature.
* Draw calls are one per part plus one per connector until `CreatureBaker` (M4) merges them.
