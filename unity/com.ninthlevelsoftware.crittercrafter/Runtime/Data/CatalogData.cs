// Mirrors the compiled library.v2 catalog (schemas/library.v2.schema.json) for JsonUtility.
// Field names intentionally match the JSON keys (snake_case). All numbers are in the glTF frame
// (docs/frame.md); convert with CritterFrame before using them in Unity space.
using System;

namespace CritterCrafter
{
    [Serializable]
    public class CatalogData
    {
        public string schema_version;
        public string document_kind;
        public string library_id;
        public string version;
        public string frame;
        public CatalogLimits limits;
        public GeneratorInfo generator;
        public GaitProfile[] gait_profiles;
        public BranchTemplate[] branch_templates;
        public SkeletonData[] skeletons;
        public PartData[] parts;
        public PoolData[] pools;

        public SkeletonData FindSkeleton(string id) => Array.Find(skeletons, s => s.skeleton_id == id);
        public PartData FindPart(string id) => Array.Find(parts, p => p.part_id == id);
        public PoolData FindPool(string id) => Array.Find(pools, p => p.pool_id == id);
        public GaitProfile FindGait(string hint) => Array.Find(gait_profiles, g => g.hint == hint);
    }

    [Serializable]
    public class CatalogLimits
    {
        public int max_triangles;
        public int target_triangles;
        public int max_bones;
        public int max_parts;
        public int max_influences;
        public int min_length_scale_pct;
        public int max_length_scale_pct;
    }

    [Serializable]
    public class GeneratorInfo
    {
        public string algorithm;
        public string rng;
    }

    [Serializable]
    public class GaitProfile
    {
        public string hint;
        public double frequency_hz;
        public double amplitude_deg;
        public double chain_lag_rad;
        public double bob_m;
    }

    [Serializable]
    public class BranchTemplate
    {
        public string template_id;
        public string chain_kind;
        public double nominal_length_m;
        public double[] bone_fractions;
    }

    [Serializable]
    public class SkeletonData
    {
        public string skeleton_id;
        public string family;
        public string locomotion_hint;
        public string status;
        public int symmetry_pct;
        public BoneData[] bones;
        public BranchData[] branches;
        public SkeletonAssetInfo asset;

        public BranchData FindBranch(string id) => Array.Find(branches, b => b.branch_id == id);
    }

    [Serializable]
    public class BoneData
    {
        public string name;
        public string parent;
        public double[] head_m;
        public double[] tail_m;
        public double[] up_m;
    }

    [Serializable]
    public class BranchData
    {
        public string branch_id;
        public string template;
        public string parent_branch;
        public string attach_bone;
        public string[] bone_names;
        public double length_m;
        public int length_mm;
        public string size_class;
        public string side;
        public bool required;
        public int optional_fill_pct;
        public string mirror_of;
        public Accepts accepts;
        public string connector_size_class;
        public string gait_role;
        public double gait_phase_rad;
        public SnapData snap;
    }

    [Serializable]
    public class Accepts
    {
        public string[] categories;
        public string[] templates;
        public string[] tags_any;
    }

    [Serializable]
    public class SnapData
    {
        public double[] position_m;
        public double[] rotation_xyzw;
    }

    [Serializable]
    public class SkeletonAssetInfo
    {
        public string fbx;
        public string glb;
        public ClipInfo[] clips;
    }

    [Serializable]
    public class ClipInfo
    {
        public string name;
        public bool loop;
        public int frames;
        public int fps;
    }

    [Serializable]
    public class PartData
    {
        public string part_id;
        public string category;
        public string template;
        public string[] species_tags;
        public string[] roles;
        public string size_class;
        public string status;
        public string style_profile;
        public double[] dimensions_m;
        public double length_m;
        public int length_mm;
        public int max_triangles;
        public int max_material_slots;
        public double connector_radius_m;
        public double[] connector_span_m;
        public string fallback_primitive;
        public string albedo;
        public string source;
        public PartAssetInfo asset;
    }

    [Serializable]
    public class PartAssetInfo
    {
        public string fbx;
        public string glb;
        public int triangles;
    }

    [Serializable]
    public class PoolData
    {
        public string pool_id;
        public string[] families;
        public string[] skeleton_ids;
    }
}
