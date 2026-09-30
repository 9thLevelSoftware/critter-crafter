# Recipe generator `cc-gen-3` (normative)

Python (`critter_crafter.recipes.generator`) and C# (`CritterCrafter.RecipeGenerator`)
must produce **identical** recipes. The golden files in `tests/golden_v3/` are asserted by
both test suites, and the copies in the Unity package
(`unity/com.ninthlevelsoftware.crittercrafter/Tests/Editor/GoldenV3/`) must stay
byte-identical to them. (`cc-gen-2` and `tests/golden/` are frozen v2 history.)

Schema `3.x.y`. A catalog is accepted when its schema major is 3 and its generator is
`cc-gen-3` with `splitmix64`; anything else is rejected (`CC_GEN_SCHEMA_VERSION`,
`CC_GEN_ALGORITHM`, `CC_GEN_RNG`; the C# runtime throws the same codes). The library's own
content version (`0.2.0`, `0.2.1`, `0.3.0` ...) is **not** gated: a library can be re-released and
still generate. A saved recipe records the library id and version it was made on and replays only on that
exact one (`CC_RECIPE_LIBRARY_VERSION` in Python, `CC_LIBRARY_VERSION` from the C# validator), so any
library bump invalidates saved recipes; games that save recipes should keep the library version they
saved against, or regenerate from the stored pool and seed.

## RNG: SplitMix64
```
state = seed (uint64); seed 0 is replaced by 1
next():  state += 0x9E3779B97F4A7C15
         z = state
         z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9
         z = (z ^ (z >> 27)) * 0x94D049BB133111EB
         return z ^ (z >> 31)                 (all arithmetic mod 2^64)
below(n): (next() >> 33) % n                   (n > 0)
pick(list): list[below(len(list))]            list is ordinally sorted by id
roll(pct):  below(100) < pct
```

## Integer rules
Lengths and girths are integer millimetres (`length_mm`, `girth_mm`, computed once at
catalog compile). No floating point is used in any decision.

- **Length fit:** `80 * part_mm <= 100 * branch_mm <= 125 * part_mm` (scale 0.80 to 1.25).
- **Girth fit:** after the part's single uniform length scale is applied, its girth must be
  within ±10% of the branch girth: with `scaled = part_girth * branch_length` and
  `target = branch_girth * part_length`, require `90 * target <= 100 * scaled <= 110 * target`.
- **Side:** the part is `symmetric`, or its side equals the branch side.
- **Binding profile identity:** `binding_profile_id`, `binding_profile_version` and
  `binding_profile_hash` must be equal on part and branch. A part fits a branch only through
  the same immutable profile.
- **Category, template, tags:** the part's category is in `accepts.categories`; `accepts.templates`
  is empty or contains the part's template; `accepts.tags_any` is empty or intersects the part's
  `species_tags`.

## Usable inventory
- **Skeletons:** only `status == "approved"`. Drafts are not generatable.
- **Parts:** `inventory_kind == "reference"` with `status == "reference"`, or
  `inventory_kind == "production"` with `status == "approved"`.

Reference parts are the auto-generated per-branch parts and connectors from
`library/catalog.py` (`reference_part_id`). Goldens (and `recipe sweep --review-drafts`) use
an in-memory approved copy of the drafts (`fixture_only_approval`); source records are never
changed.

## Algorithm: `generate(catalog, pool_id, seed)`
1. Skeleton candidates = usable skeletons whose `family` is in the pool's `families` or whose
   id is in the pool's `skeleton_ids`, sorted by id. None: `CC_GEN_NO_SKELETON`.
   `skeleton = pick`.
2. `budget = limits.max_triangles`; `connector_slots = max(0, limits.max_parts - branch_count)`.
   For each branch in skeleton order (parents precede children):
   1. skip if it has an unfilled `parent_branch` (required: error `CC_GEN_REQUIRED_PARENT`);
   2. if `mirror_of` names a filled branch and `roll(skeleton.symmetry_pct)`: reuse that
      part if the branch accepts it and it fits the budget (else fall through);
   3. else if not `required` and not `roll(branch.optional_fill_pct)`: leave empty;
   4. candidates = usable non-connector parts accepted by the branch with
      `max_triangles <= budget`, sorted by id. Empty and required: `CC_GEN_NO_CANDIDATE`;
      empty and optional: skip;
   5. `part = pick(candidates)`; `budget -= part.max_triangles`;
   6. if the branch has a `connector_size_class` and `connector_slots > 0`: connector candidates
      = usable connectors accepted by `connector_accepted` (same profile identity and side, equal
      girth, the `skinned_parent_child` interface at version 1.0.0 with two bone groups, at most
      two influences, normalized weights, identity socket, span straddling the joint, and radius
      matching girth) that fit the budget, sorted by id; if non-empty `connector = pick`,
      `budget -= its max_triangles`, `connector_slots -= 1`.
3. Each fill records `length_scale = branch_mm / part_mm` and
   `girth_scale = branch_girth / (part_girth * length_scale)`, both rounded to 6 decimals.
4. Recipe id = `gen_<pool_id>_<seed>`.

## Review path: `generate_for_skeleton(catalog, skeleton_id, seed)`
Fills an explicitly named skeleton, drafts included, for anatomy review. Each branch takes its
dedicated reference part (and reference connector) instead of a random pick; optional branches
are still decided by `roll(optional_fill_pct)`. A missing or non-accepted reference part is
`CC_GEN_REFERENCE_MISSING`. Its pool id is `review_<skeleton_id>`.

## Canonical form (used for golden comparison)
`<skeleton_id>|<branch_id>=<part_id>+<connector_id or ->;...` in branch order.
