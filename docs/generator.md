# Recipe generator `cc-gen-2` (normative)

Python (`critter_crafter.recipes.generator`) and C# (`CritterCrafter.RecipeGenerator`)
must produce **identical** recipes. Golden files in `tests/golden/` are checked by
both test suites.

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
Lengths are compared as integer millimetres: `mm(x) = round(x * 1000)` (round half
away from zero, done once at catalog load). A part fits a branch iff
`4 * part_mm <= 5 * branch_mm` and `4 * branch_mm <= 5 * part_mm`
(scale 0.8 ... 1.25). No floating point is used in any decision.

## Algorithm
Input: library, `pool_id`, `seed`.
1. Skeleton candidates = skeletons whose `family` is in the pool's `families` or whose
   id is in the pool's `skeleton_ids`, `status != "draft"`, sorted by id. `skeleton = pick`.
2. `tri_budget = limits.max_triangles`. For each branch in skeleton order
   (parents are guaranteed to precede children):
   1. skip if it has a `parent_branch` that is unfilled;
   2. if `mirror_of` names a filled branch and `roll(skeleton.symmetry_pct)`:
      reuse that part if it is accepted by this branch and still fits the budget (else fall through to 3);
   3. else if not `required` and not `roll(branch.optional_fill_pct)`: leave empty;
   4. candidates = parts with `category in accepts.categories`,
      (`accepts.templates` empty or part.template in it),
      (`accepts.tags_any` empty or intersecting part.species_tags),
      length fit, `status != "draft"`, `max_triangles <= remaining budget`;
      sorted by id. Empty & required -> error `CC_GEN_NO_CANDIDATE`; empty & optional -> skip.
   5. `part = pick(candidates)`; budget -= part.max_triangles.
   6. if branch.connector_size_class is not null: connector candidates = parts with
      category `connector`, same size class, fitting the remaining budget, sorted;
      if non-empty `connector = pick`, budget -= its max_triangles.
3. Recipe id = `gen_<pool_id>_<seed>`.

## Canonical form (used for golden comparison)
`<skeleton_id>|<branch_id>=<part_id>+<connector_id or ->;...` in branch order.
