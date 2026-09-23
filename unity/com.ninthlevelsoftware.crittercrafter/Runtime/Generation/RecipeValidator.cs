using System.Collections.Generic;

namespace CritterCrafter
{
    /// <summary>Recipe validation; same CC_* codes as recipes/validate.py.</summary>
    public static class RecipeValidator
    {
        public static List<string> Validate(CatalogData catalog, CritterRecipe recipe)
        {
            var diags = new List<string>();
            var skel = catalog.FindSkeleton(recipe.skeleton_id);
            if (skel == null)
            {
                diags.Add("CC_UNKNOWN_SKELETON: " + recipe.skeleton_id);
                return diags;
            }
            var order = new Dictionary<string, int>();
            for (int i = 0; i < skel.branches.Length; i++) order[skel.branches[i].branch_id] = i;
            var filled = new HashSet<string>();
            int tris = 0, last = -1;
            foreach (var f in recipe.fills)
            {
                var br = skel.FindBranch(f.branch_id);
                if (br == null) { diags.Add("CC_UNKNOWN_BRANCH: " + f.branch_id); continue; }
                if (order[f.branch_id] <= last) diags.Add("CC_FILL_ORDER: " + f.branch_id);
                last = System.Math.Max(last, order[f.branch_id]);
                if (filled.Contains(f.branch_id)) { diags.Add("CC_BRANCH_OCCUPIED: " + f.branch_id); continue; }
                if (!string.IsNullOrEmpty(br.parent_branch) && !filled.Contains(br.parent_branch))
                    diags.Add("CC_PARENT_UNFILLED: " + f.branch_id);
                var part = catalog.FindPart(f.part_id);
                if (part == null) { diags.Add("CC_UNKNOWN_PART: " + f.part_id); continue; }
                if (part.category == "connector" || !RecipeGenerator.PartAccepted(part, br))
                    diags.Add("CC_PART_REJECTED: " + f.branch_id + "=" + f.part_id);
                tris += part.max_triangles;
                if (!string.IsNullOrEmpty(f.connector_part_id))
                {
                    var conn = catalog.FindPart(f.connector_part_id);
                    if (conn == null) diags.Add("CC_UNKNOWN_PART: " + f.connector_part_id);
                    else if (conn.category != "connector" || conn.size_class != br.connector_size_class)
                        diags.Add("CC_CONNECTOR_MISMATCH: " + f.branch_id + "=" + f.connector_part_id);
                    else tris += conn.max_triangles;
                }
                filled.Add(f.branch_id);
            }
            foreach (var b in skel.branches)
                if (b.required && !filled.Contains(b.branch_id)
                    && (string.IsNullOrEmpty(b.parent_branch) || filled.Contains(b.parent_branch)))
                    diags.Add("CC_REQUIRED_UNFILLED: " + b.branch_id);
            var lim = catalog.limits;
            if (tris > lim.max_triangles) diags.Add($"CC_BUDGET_TRIS: {tris}>{lim.max_triangles}");
            if (skel.bones.Length > lim.max_bones) diags.Add($"CC_BUDGET_BONES: {skel.bones.Length}>{lim.max_bones}");
            if (filled.Count > lim.max_parts) diags.Add($"CC_BUDGET_PARTS: {filled.Count}>{lim.max_parts}");
            diags.Sort(string.CompareOrdinal);
            return diags;
        }
    }
}
