// Mirrors the compiled library.v3 catalog for JsonUtility.
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
        public BindingProfile[] binding_profiles;
        public GaitProfile[] gait_profiles;
        public BranchTemplate[] branch_templates;
        public SkeletonData[] skeletons;
        public PartData[] parts;
        public PoolData[] pools;

        public SkeletonData FindSkeleton(string id) => Array.Find(skeletons, s => s.skeleton_id == id);
        public PartData FindPart(string id) => Array.Find(parts, p => p.part_id == id);
        public PoolData FindPool(string id) => Array.Find(pools, p => p.pool_id == id);
        public GaitProfile FindGait(string hint) => Array.Find(gait_profiles, g => g.hint == hint);
        public BindingProfile FindBindingProfile(string id, string version) =>
            Array.Find(binding_profiles, p => p.binding_profile_id == id && p.binding_profile_version == version);
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
        public int girth_tolerance_pct;
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
    public class BindingProfile
    {
        public string schema_version;
        public string binding_profile_id;
        public string binding_profile_version;
        public string binding_profile_hash;
        public string template;
        public string[] joint_order;
        public double[] bone_fractions;
        public double girth_ratio;
        public BindingJoint[] joints;
        public BindingLandmark[] landmarks;
    }

    [Serializable]
    public class BindingJoint
    {
        public string joint_id;
        public string parent_joint;
        public double[] canonical_position_n;
        public double[] canonical_rotation_xyzw;
        public double[] primary_axis;
        public double[] secondary_axis;
        public JointLimits limits_deg;
    }

    [Serializable]
    public class JointLimits
    {
        public double[] swing_x;
        public double[] swing_y;
        public double[] twist;
    }

    [Serializable]
    public class BindingLandmark
    {
        public string landmark_id;
        public string parent_joint;
        public double[] position_n;
        public double[] rotation_xyzw;
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
        public NeutralPoseData neutral_pose;
        public AnatomyData anatomy;
        public LocomotionData locomotion;
        public SkeletonAssetInfo asset;

        public BranchData FindBranch(string id) => Array.Find(branches, b => b.branch_id == id);
    }

    /// <summary>Runtime foot-placement data (catalog frame, metres); see docs/locomotion.md.</summary>
    [Serializable]
    public class LocomotionData
    {
        public string version;
        /// <summary>"legs" when the runtime step planner owns locomotion, otherwise "none".</summary>
        public string mode;
        /// <summary>"drag" for grounded torsos hauled by the arms; otherwise walking gaits.</summary>
        public string gait;
        /// <summary>The torso rests on the ground: no bob, no body height or tilt from the feet.</summary>
        public bool body_on_ground;
        /// <summary>Branch whose baked strike plays during telegraph/attack (IK released).</summary>
        public string attack_branch_id;
        public double hip_height_m;
        public double leg_length_m;
        public double usable_stroke_m;
        public int min_support;
        public double duty_walk;
        public double duty_run;
        public double cadence_max_hz;
        public double v_walk_mps;
        public double v_run_mps;
        public double v_max_mps;
        /// <summary>Slide mode: ground travel per undulation cycle of the phase-driven walk/run clips.</summary>
        public double travel_per_cycle_m;
        public LocomotionLeg[] legs;

        public bool HasLegs => mode == "legs" && legs != null && legs.Length > 0;
        public bool Slides => mode == "slide" && travel_per_cycle_m > 0.0;
        /// <summary>Walk/run clips are one-cycle overlays driven by CreatureGait's GaitPhase.</summary>
        public bool IsPhaseDriven => HasLegs || Slides;
    }

    [Serializable]
    public class LocomotionLeg
    {
        public string branch_id;
        /// <summary>"two_bone" (limb3) or "chain" (insect_leg4, tentacle8).</summary>
        public string solver;
        public string[] chain_bones;
        public double[] tip_local_m;
        public double[] hip_m;
        public double[] home_m;
        public double reach_m;
        public double stroke_m;
        public double clearance_m;
        /// <summary>Drag gaits: forward shift (m) of the stance centre from the neutral contact.</summary>
        public double stance_shift_m;
        public double walk_phase;
        public double run_phase;
        public bool support;
    }

    [Serializable]
    public class BoneData
    {
        public string name;
        public string parent;
        public string joint_id;
        public double[] head_m;
        public double[] tail_m;
        public double[] up_m;
    }

    [Serializable]
    public class BranchData
    {
        public string branch_id;
        public string template;
        public string binding_profile_id;
        public string binding_profile_version;
        public string binding_profile_hash;
        public string parent_branch;
        public string attach_bone;
        public string[] bone_names;
        public string[] joint_order;
        public double[] bone_fractions;
        public double length_m;
        public int length_mm;
        public double girth_m;
        public int girth_mm;
        public string size_class;
        public string side;
        public bool required;
        public int optional_fill_pct;
        public string mirror_of;
        public Accepts accepts;
        public string connector_size_class;
        public string gait_role;
        public double gait_phase_rad;
        public GaitData gait;
        public ContactData[] contacts;
        public SnapData snap;
        public SocketData socket;
        public BranchConnectorInterface connector_interface;
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
    public class SocketData
    {
        public string parent_joint;
        public double[] position_m;
        public double[] rotation_xyzw;
    }

    [Serializable]
    public class BranchConnectorInterface
    {
        public string interface_id;
        public string interface_version;
        public string parent_role;
        public string child_role;
        public string parent_bone;
        public string child_bone;
        public double[] position_m;
        public double[] rotation_xyzw;
    }

    [Serializable]
    public class PartConnectorInterface
    {
        public string interface_id;
        public string interface_version;
        public ConnectorBoneGroup[] bone_groups;
        public int max_influences;
        public bool weights_normalized;
        public double[] position_m;
        public double[] rotation_xyzw;
    }

    [Serializable]
    public class ConnectorBoneGroup
    {
        public string group;
        public string role;
    }

    [Serializable]
    public class GaitData
    {
        public string role;
        public double phase_rad;
        public double support_phase;
        public double[] bend_pole_m;
        public double stride_m;
        public double clearance_m;
        public double cadence_hz;
    }

    [Serializable]
    public class ContactData
    {
        public string kind;
        public int bone_index;
        public double[] local_point_m;
    }

    [Serializable]
    public class NeutralPoseData
    {
        public double[] root_offset_m;
        public NeutralRotationData[] rotations;
    }

    [Serializable]
    public class NeutralRotationData
    {
        public string bone_name;
        public double[] rotation_xyzw;
    }

    [Serializable]
    public class AnatomyData
    {
        public string archetype_id;
        public string body_plan;
        public string style;
        public string[] support_branches;
        public string[] contact_branches;
        public AnatomySymmetry symmetry;
        public AnatomyLandmarks landmarks;
        public AnatomySilhouette silhouette;
        public AnatomyBudgets budgets;
    }

    [Serializable]
    public class AnatomySymmetry
    {
        public string kind;
        public int ring_count;
    }

    [Serializable]
    public class AnatomyLandmarks
    {
        public string pelvis;
        public string shoulder;
        public string neck;
    }

    [Serializable]
    public class AnatomySilhouette
    {
        public double height_m;
        public double length_m;
        public double width_m;
    }

    [Serializable]
    public class AnatomyBudgets
    {
        public int bones;
        public int parts;
        public int triangles;
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
        public double duration_s;
        public double cadence_hz;
        public double nominal_speed_mps;
        public double speed_mps;
        public double stride_m;
        public ClipPlayback playback;
        public ContactScheduleEntry[] contact_schedule;
    }

    [Serializable]
    public class ClipPlayback
    {
        public string wrap;
        public double[] phase_range;
        public double[] rate_range;
        public string continuation;
    }

    [Serializable]
    public class ContactScheduleEntry
    {
        public string contact_id;
        public string branch_id;
        public int contact_index;
        public string kind;
        public double phase_offset;
        public double stance_fraction;
        public bool support;
    }

    [Serializable]
    public class PartData
    {
        public string part_id;
        public string category;
        public string template;
        public string binding_profile_id;
        public string binding_profile_version;
        public string binding_profile_hash;
        public string[] species_tags;
        public string[] roles;
        public string size_class;
        public string status;
        public string inventory_kind;
        public string side;
        public string style_profile;
        public double[] dimensions_m;
        public double length_m;
        public int length_mm;
        public double girth_m;
        public int girth_mm;
        public int max_triangles;
        public int max_material_slots;
        public double connector_radius_m;
        public double[] connector_span_m;
        public PartConnectorInterface connector_interface;
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
