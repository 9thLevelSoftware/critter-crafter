using System;
using System.Collections.Generic;

namespace CritterCrafter
{
    public class GenerationException : Exception
    {
        public readonly string Code;
        public GenerationException(string code, string message) : base(code + ": " + message) { Code = code; }
    }

    /// <summary>
    /// cc-gen-2 (docs/generator.md). Decision-for-decision identical to recipes/generator.py;
    /// Tests/Editor/Golden pins both implementations.
    /// </summary>
    public static class RecipeGenerator
    {
        public const string Algorithm = "cc-gen-2";

        public static bool LengthFits(int partMm, int branchMm) =>
            4 * partMm <= 5 * branchMm && 4 * branchMm <= 5 * partMm;

        public static bool PartAccepted(PartData part, BranchData branch)
        {
            var acc = branch.accepts;
            if (Array.IndexOf(acc.categories, part.category) < 0) return false;
            if (acc.templates != null && acc.templates.Length > 0 && Array.IndexOf(acc.templates, part.template) < 0) return false;
            if (acc.tags_any != null && acc.tags_any.Length > 0)
            {
                bool any = false;
                foreach (var t in acc.tags_any)
                    if (Array.IndexOf(part.species_tags, t) >= 0) { any = true; break; }
                if (!any) return false;
            }
            return LengthFits(part.length_mm, branch.length_mm);
        }

        static bool Usable(string status) => status != "draft";

        public static CritterRecipe Generate(CatalogData catalog, string poolId, long seed)
        {
            var pool = catalog.FindPool(poolId) ?? throw new GenerationException("CC_GEN_UNKNOWN_POOL", poolId);
            var rng = new CritterRng(seed);

            var skeletons = new List<SkeletonData>();
            foreach (var s in catalog.skeletons)
                if (Usable(s.status) && (Array.IndexOf(pool.families, s.family) >= 0 || Array.IndexOf(pool.skeleton_ids, s.skeleton_id) >= 0))
                    skeletons.Add(s);
            if (skeletons.Count == 0) throw new GenerationException("CC_GEN_NO_SKELETON", poolId);
            skeletons.Sort((a, b) => string.CompareOrdinal(a.skeleton_id, b.skeleton_id));
            var skeleton = rng.Pick(skeletons);

            var body = new List<PartData>();
            var connectors = new List<PartData>();
            var byId = new Dictionary<string, PartData>();
            foreach (var p in catalog.parts)
            {
                if (!Usable(p.status)) continue;
                byId[p.part_id] = p;
                (p.category == "connector" ? connectors : body).Add(p);
            }
            Comparison<PartData> byPartId = (a, b) => string.CompareOrdinal(a.part_id, b.part_id);
            body.Sort(byPartId);
            connectors.Sort(byPartId);

            int budget = catalog.limits.max_triangles;
            var filled = new Dictionary<string, string>();
            var fills = new List<RecipeFill>();
            foreach (var br in skeleton.branches)
            {
                if (!string.IsNullOrEmpty(br.parent_branch) && !filled.ContainsKey(br.parent_branch)) continue;
                PartData chosen = null;
                if (!string.IsNullOrEmpty(br.mirror_of) && filled.TryGetValue(br.mirror_of, out var mirrorPart)
                    && rng.Roll(skeleton.symmetry_pct))
                {
                    var cand = byId[mirrorPart];
                    if (cand.max_triangles <= budget && PartAccepted(cand, br)) chosen = cand;
                }
                if (chosen == null)
                {
                    if (!br.required && !rng.Roll(br.optional_fill_pct)) continue;
                    var cands = body.FindAll(p => PartAccepted(p, br) && p.max_triangles <= budget);
                    if (cands.Count == 0)
                    {
                        if (br.required) throw new GenerationException("CC_GEN_NO_CANDIDATE", skeleton.skeleton_id + "." + br.branch_id);
                        continue;
                    }
                    chosen = rng.Pick(cands);
                }
                budget -= chosen.max_triangles;
                string connectorId = "";
                if (!string.IsNullOrEmpty(br.connector_size_class))
                {
                    var cc = connectors.FindAll(c => c.size_class == br.connector_size_class && c.max_triangles <= budget);
                    if (cc.Count > 0)
                    {
                        var conn = rng.Pick(cc);
                        connectorId = conn.part_id;
                        budget -= conn.max_triangles;
                    }
                }
                filled[br.branch_id] = chosen.part_id;
                fills.Add(new RecipeFill { branch_id = br.branch_id, part_id = chosen.part_id, connector_part_id = connectorId });
            }

            return new CritterRecipe
            {
                recipe_id = "gen_" + poolId + "_" + seed,
                library_id = catalog.library_id,
                library_version = catalog.version,
                generator = Algorithm,
                pool_id = poolId,
                seed = seed,
                skeleton_id = skeleton.skeleton_id,
                fills = fills.ToArray(),
            };
        }
    }
}
