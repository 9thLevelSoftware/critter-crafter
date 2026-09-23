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
    /// cc-gen-3 deterministic generator. Binding identity, side, length, girth, status, and budgets
    /// are all part of candidate selection; no compatibility decision depends on floating point.
    /// </summary>
    public static class RecipeGenerator
    {
        public const string SchemaVersion = "3.0.0";
        public const string Algorithm = "cc-gen-3";
        public const string LibraryVersion = "0.2.0";

        public static bool RatioFits(int partMm, int branchMm) =>
            4 * partMm <= 5 * branchMm && 4 * branchMm <= 5 * partMm;

        public static bool LengthFits(int partMm, int branchMm) => RatioFits(partMm, branchMm);

        public static bool GirthFits(PartData part, BranchData branch)
        {
            long scaled = (long)part.girth_mm * branch.length_mm;
            long target = (long)branch.girth_mm * part.length_mm;
            return 90L * target <= 100L * scaled && 100L * scaled <= 110L * target;
        }

        public static bool BindingMatches(PartData part, BranchData branch) =>
            part.binding_profile_id == branch.binding_profile_id
            && part.binding_profile_version == branch.binding_profile_version
            && part.binding_profile_hash == branch.binding_profile_hash;

        public static bool SideFits(string partSide, string branchSide) =>
            partSide == "symmetric" || partSide == branchSide;

        public static bool PartAccepted(PartData part, BranchData branch)
        {
            if (part == null || branch == null || !BindingMatches(part, branch)) return false;
            if (!SideFits(part.side, branch.side)) return false;
            if (!LengthFits(part.length_mm, branch.length_mm) || !GirthFits(part, branch)) return false;
            var acc = branch.accepts;
            if (acc == null) return false;
            if (Array.IndexOf(acc.categories, part.category) < 0) return false;
            if (acc.templates != null && acc.templates.Length > 0 && Array.IndexOf(acc.templates, part.template) < 0) return false;
            if (acc.tags_any != null && acc.tags_any.Length > 0)
            {
                bool any = false;
                foreach (var t in acc.tags_any)
                    if (Array.IndexOf(part.species_tags, t) >= 0) { any = true; break; }
                if (!any) return false;
            }
            return true;
        }

        public static bool Usable(PartData part) => part != null &&
            (part.inventory_kind == "reference" && part.status == "reference"
             || part.inventory_kind == "production" && part.status == "approved");

        public static bool Usable(SkeletonData skeleton) => skeleton != null && skeleton.status == "approved";

        public static bool ConnectorAccepted(PartData part, BranchData branch)
        {
            if (part == null || branch == null || part.category != "connector") return false;
            if (!BindingMatches(part, branch) || !SideFits(part.side, branch.side) || part.girth_mm != branch.girth_mm) return false;
            var expected = branch.connector_interface;
            var actual = part.connector_interface;
            if (expected == null || actual == null || expected.interface_id != "skinned_parent_child"
                || expected.interface_version != "1.0.0" || actual.interface_id != expected.interface_id
                || actual.interface_version != expected.interface_version || expected.parent_role != "parent"
                || expected.child_role != "child" || expected.parent_bone != branch.attach_bone
                || expected.child_bone != branch.bone_names[0] || actual.max_influences != 2
                || !actual.weights_normalized || actual.bone_groups == null
                || actual.bone_groups.Length != 2 || actual.bone_groups[0].group != "b0"
                || actual.bone_groups[0].role != expected.parent_role || actual.bone_groups[1].group != "b1"
                || actual.bone_groups[1].role != expected.child_role) return false;
            double[] zero = { 0.0, 0.0, 0.0 };
            double[] identity = { 0.0, 0.0, 0.0, 1.0 };
            return Exact(expected.position_m, zero, 3) && Exact(expected.rotation_xyzw, identity, 4)
                && Exact(actual.position_m, zero, 3) && Exact(actual.rotation_xyzw, identity, 4)
                && part.connector_span_m != null && part.connector_span_m.Length == 2
                && part.connector_span_m[0] < 0.0 && part.connector_span_m[1] > 0.0
                && Math.Abs(Math.Round(part.connector_radius_m * 2000.0, MidpointRounding.ToEven)
                    - branch.girth_mm) <= 1.0;
        }

        static bool Exact(double[] a, double[] b, int count)
        {
            if (a == null || b == null || a.Length != count || b.Length != count) return false;
            for (int i = 0; i < count; i++) if (a[i] != b[i]) return false;
            return true;
        }

        static void RequireV3(CatalogData catalog)
        {
            if (catalog == null) throw new GenerationException("CC_GEN_UNSUPPORTED_CATALOG", "null");
            if (catalog.schema_version != SchemaVersion)
                throw new GenerationException("CC_GEN_UNSUPPORTED_CATALOG", catalog.schema_version ?? "");
            if (catalog.version != LibraryVersion)
                throw new GenerationException("CC_GEN_UNSUPPORTED_LIBRARY_VERSION", catalog.version ?? "");
            if (catalog.generator == null || catalog.generator.algorithm != Algorithm)
                throw new GenerationException("CC_GEN_UNSUPPORTED_GENERATOR", catalog.generator?.algorithm ?? "");
        }

        public static CritterRecipe Generate(CatalogData catalog, string poolId, long seed)
        {
            RequireV3(catalog);
            var pool = catalog.FindPool(poolId) ?? throw new GenerationException("CC_GEN_UNKNOWN_POOL", poolId);
            var rng = new CritterRng(seed);

            var skeletons = new List<SkeletonData>();
            foreach (var s in catalog.skeletons)
                if (Usable(s) && (Array.IndexOf(pool.families, s.family) >= 0 || Array.IndexOf(pool.skeleton_ids, s.skeleton_id) >= 0))
                    skeletons.Add(s);
            if (skeletons.Count == 0) throw new GenerationException("CC_GEN_NO_SKELETON", poolId);
            skeletons.Sort((a, b) => string.CompareOrdinal(a.skeleton_id, b.skeleton_id));
            var skeleton = rng.Pick(skeletons);

            var body = new List<PartData>();
            var connectors = new List<PartData>();
            var byId = new Dictionary<string, PartData>();
            foreach (var p in catalog.parts)
            {
                if (!Usable(p)) continue;
                byId[p.part_id] = p;
                (p.category == "connector" ? connectors : body).Add(p);
            }
            Comparison<PartData> byPartId = (a, b) => string.CompareOrdinal(a.part_id, b.part_id);
            body.Sort(byPartId);
            connectors.Sort(byPartId);

            int budget = Math.Min(catalog.limits.max_triangles, 30000);
            int connectorSlots = Math.Max(0, Math.Min(catalog.limits.max_parts, 16) - skeleton.branches.Length);
            var filled = new Dictionary<string, string>();
            var fills = new List<RecipeFill>();
            foreach (var br in skeleton.branches)
            {
                if (!string.IsNullOrEmpty(br.parent_branch) && !filled.ContainsKey(br.parent_branch))
                {
                    if (br.required) throw new GenerationException("CC_GEN_REQUIRED_PARENT", skeleton.skeleton_id + "." + br.branch_id);
                    continue;
                }
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
                if (!string.IsNullOrEmpty(br.connector_size_class) && connectorSlots > 0)
                {
                    var cc = connectors.FindAll(c => ConnectorAccepted(c, br) && c.max_triangles <= budget);
                    if (cc.Count > 0)
                    {
                        var conn = rng.Pick(cc);
                        connectorId = conn.part_id;
                        budget -= conn.max_triangles;
                        connectorSlots--;
                    }
                }
                filled[br.branch_id] = chosen.part_id;
                fills.Add(new RecipeFill { branch_id = br.branch_id, part_id = chosen.part_id, connector_part_id = connectorId });
            }

            var result = new CritterRecipe
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
            foreach (var f in result.fills)
            {
                var br = skeleton.FindBranch(f.branch_id);
                var part = catalog.FindPart(f.part_id);
                f.binding_profile_id = br.binding_profile_id;
                f.binding_profile_version = br.binding_profile_version;
                f.binding_profile_hash = br.binding_profile_hash;
                f.length_scale = Math.Round((double)br.length_mm / part.length_mm, 6, MidpointRounding.ToEven);
                f.girth_scale = Math.Round((double)br.girth_mm * part.length_mm / (part.girth_mm * (double)br.length_mm), 6,
                    MidpointRounding.ToEven);
            }
            return result;
        }
    }
}
