using System.Collections.Generic;

namespace CritterCrafter
{
    /// <summary>Recipe validation; same CC_* codes as recipes/validate.py.</summary>
    public static class RecipeValidator
    {
        public static List<string> Validate(CatalogData catalog, CritterRecipe recipe)
        {
            var diags = new List<string>();
            if (catalog == null) { diags.Add("CC_UNSUPPORTED_CATALOG: null"); return diags; }
            if (catalog.schema_version != RecipeGenerator.SchemaVersion)
            {
                diags.Add("CC_UNSUPPORTED_CATALOG: " + (catalog.schema_version ?? ""));
                return diags;
            }
            if (catalog.version != RecipeGenerator.LibraryVersion)
            {
                diags.Add("CC_UNSUPPORTED_LIBRARY_VERSION: " + (catalog.version ?? ""));
                return diags;
            }
            if (catalog.generator == null || catalog.generator.algorithm != RecipeGenerator.Algorithm)
            {
                diags.Add("CC_UNSUPPORTED_GENERATOR: " + (catalog.generator?.algorithm ?? ""));
                return diags;
            }
            if (recipe == null) { diags.Add("CC_INVALID_RECIPE: null"); return diags; }
            if (recipe.schema_version != RecipeGenerator.SchemaVersion)
                diags.Add("CC_RECIPE_SCHEMA: " + (recipe.schema_version ?? ""));
            if (recipe.generator != RecipeGenerator.Algorithm)
                diags.Add("CC_RECIPE_GENERATOR: " + (recipe.generator ?? ""));
            if (recipe.library_id != catalog.library_id)
                diags.Add($"CC_LIBRARY_ID: {recipe.library_id}!={catalog.library_id}");
            if (recipe.library_version != catalog.version)
                diags.Add($"CC_LIBRARY_VERSION: {recipe.library_version}!={catalog.version}");
            if (diags.Count > 0) { diags.Sort(string.CompareOrdinal); return diags; }
            var skel = catalog.FindSkeleton(recipe.skeleton_id);
            if (skel == null)
            {
                diags.Add("CC_UNKNOWN_SKELETON: " + recipe.skeleton_id);
                return diags;
            }
            if (!RecipeGenerator.Usable(skel)) diags.Add("CC_SKELETON_NOT_APPROVED: " + skel.skeleton_id);
            var order = new Dictionary<string, int>();
            for (int i = 0; i < skel.branches.Length; i++) order[skel.branches[i].branch_id] = i;
            var filled = new HashSet<string>();
            int tris = 0, partCount = 0, last = -1;
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
                if (!RecipeGenerator.Usable(part)) diags.Add("CC_PART_NOT_APPROVED: " + f.part_id);
                if (f.binding_profile_id != br.binding_profile_id
                    || f.binding_profile_version != br.binding_profile_version
                    || f.binding_profile_hash != br.binding_profile_hash)
                    diags.Add("CC_FILL_BINDING_IDENTITY: " + f.branch_id);
                if (part.category == "connector" || !RecipeGenerator.PartAccepted(part, br))
                    diags.Add("CC_PART_REJECTED: " + f.branch_id + "=" + f.part_id);
                else
                {
                    double lengthScale = System.Math.Round((double)br.length_mm / part.length_mm, 6, System.MidpointRounding.ToEven);
                    double girthScale = System.Math.Round((double)br.girth_mm * part.length_mm
                        / (part.girth_mm * (double)br.length_mm), 6, System.MidpointRounding.ToEven);
                    if (System.Math.Abs(f.length_scale - lengthScale) > 1e-6)
                        diags.Add("CC_LENGTH_SCALE: " + f.branch_id);
                    if (System.Math.Abs(f.girth_scale - girthScale) > 1e-6)
                        diags.Add("CC_GIRTH_SCALE: " + f.branch_id);
                }
                tris += part.max_triangles;
                partCount++;
                if (!string.IsNullOrEmpty(f.connector_part_id))
                {
                    var conn = catalog.FindPart(f.connector_part_id);
                    if (conn == null) diags.Add("CC_UNKNOWN_PART: " + f.connector_part_id);
                    else if (!RecipeGenerator.Usable(conn)) diags.Add("CC_PART_NOT_APPROVED: " + f.connector_part_id);
                    else if (!RecipeGenerator.ConnectorAccepted(conn, br))
                        diags.Add("CC_CONNECTOR_MISMATCH: " + f.branch_id + "=" + f.connector_part_id);
                    else { tris += conn.max_triangles; partCount++; }
                }
                filled.Add(f.branch_id);
            }
            foreach (var b in skel.branches)
                if (b.required && !filled.Contains(b.branch_id))
                    diags.Add("CC_REQUIRED_UNFILLED: " + b.branch_id);
            int maxTris = System.Math.Min(catalog.limits.max_triangles, 30000);
            int maxBones = System.Math.Min(catalog.limits.max_bones, 120);
            int maxParts = System.Math.Min(catalog.limits.max_parts, 16);
            if (tris > maxTris) diags.Add($"CC_BUDGET_TRIS: {tris}>{maxTris}");
            if (skel.bones.Length > maxBones) diags.Add($"CC_BUDGET_BONES: {skel.bones.Length}>{maxBones}");
            if (partCount > maxParts) diags.Add($"CC_BUDGET_PARTS: {partCount}>{maxParts}");
            diags.Sort(string.CompareOrdinal);
            return diags;
        }
    }
}
