using System;
using System.Collections.Generic;
using UnityEngine;

namespace CritterCrafter
{
    /// <summary>Root component of an assembled creature: recipe, animator, renderers, diagnostics.</summary>
    public class AssembledCreature : MonoBehaviour
    {
        [Serializable]
        public struct PartRenderer
        {
            public string branchId;
            public string partId;
            public bool connector;
            public SkinnedMeshRenderer renderer;
        }

        [SerializeField] string recipeJson;
        [SerializeField] Animator animator;
        [SerializeField] List<PartRenderer> renderers = new List<PartRenderer>();
        [SerializeField] Bounds bindBoundsLocal;
        [SerializeField] Bounds neutralBoundsLocal;
        [SerializeField] Bounds animationBoundsLocal;
        [SerializeField] GameObject skeletonModel;
        [SerializeField] float walkSpeed = 1.2f;
        [SerializeField] float runSpeed = 3f;
        [SerializeField] float idleDuration = 1f;
        [SerializeField] float walkDuration = 1f;
        [SerializeField] float runDuration = 1f;
        [SerializeField] float minPlaybackRate = CreatureMotion.MinPlaybackRate;
        [SerializeField] float maxPlaybackRate = CreatureMotion.MaxPlaybackRate;
        bool _neutralApplied;

        public readonly List<string> Diagnostics = new List<string>();
        public int Triangles { get; internal set; }
        public Animator Animator => animator;
        public IReadOnlyList<PartRenderer> Renderers => renderers;
        public bool IsFallback => animator == null;
        /// <summary>Bind-pose bounds in the creature root's local space.</summary>
        public Bounds BindBoundsLocal => bindBoundsLocal;
        public Bounds NeutralBoundsLocal => neutralBoundsLocal;
        public Bounds AnimationBoundsLocal => animationBoundsLocal;
        public SkeletonData Skeleton { get; private set; }
        public float WalkSpeed => walkSpeed;
        public float RunSpeed => runSpeed;
        public float IdleDuration => idleDuration;
        public float WalkDuration => walkDuration;
        public float RunDuration => runDuration;
        public float MinPlaybackRate => minPlaybackRate;
        public float MaxPlaybackRate => maxPlaybackRate;

        /// <summary>The authoritative recipe (persist this, not the seed).</summary>
        public CritterRecipe Recipe => string.IsNullOrEmpty(recipeJson) ? null : JsonUtility.FromJson<CritterRecipe>(recipeJson);

        /// <summary>Put every bone at the asset's immutable straight bind pose.</summary>
        public void ApplyBindPose()
        {
            if (animator == null || skeletonModel == null) return;
            SkeletonRest.ApplyBindPose(animator.transform, SkeletonRest.Get(skeletonModel), transform.localToWorldMatrix);
            _neutralApplied = false;
        }

        /// <summary>Apply schema-v3 anatomical deltas once, after ApplyBindPose.</summary>
        public void ApplyNeutralPose()
        {
            if (_neutralApplied || animator == null || skeletonModel == null || Skeleton == null) return;
            SkeletonPose.ApplyNeutralPose(animator.transform, Skeleton, SkeletonRest.Get(skeletonModel), transform.localToWorldMatrix);
            _neutralApplied = true;
        }

        [Obsolete("Use ApplyBindPose.")]
        public void ResetToBindPose() => ApplyBindPose();

        internal void Init(CritterRecipe recipe, Animator anim, SkeletonData skeleton, GameObject model = null)
        {
            skeletonModel = model;
            recipeJson = recipe != null ? JsonUtility.ToJson(recipe) : "";
            animator = anim;
            Skeleton = skeleton;
            if (skeleton?.asset?.clips != null)
            {
                walkSpeed = ClipSpeed(skeleton.asset.clips, "walk", 1.2f);
                runSpeed = ClipSpeed(skeleton.asset.clips, "run", 3f);
                idleDuration = ClipDuration(skeleton.asset.clips, "idle", 1f);
                walkDuration = ClipDuration(skeleton.asset.clips, "walk", 1f);
                runDuration = ClipDuration(skeleton.asset.clips, "run", 1f);
                var walk = Array.Find(skeleton.asset.clips, c => c.name == "walk");
                if (walk?.playback?.rate_range != null && walk.playback.rate_range.Length == 2)
                {
                    minPlaybackRate = (float)walk.playback.rate_range[0];
                    maxPlaybackRate = (float)walk.playback.rate_range[1];
                }
            }
            bindBoundsLocal = new Bounds(Vector3.up, Vector3.one);
            neutralBoundsLocal = bindBoundsLocal;
            animationBoundsLocal = bindBoundsLocal;
            _neutralApplied = false;
        }

        internal void AddRenderer(SkinnedMeshRenderer smr, string branchId, string partId, bool connector)
        {
            renderers.Add(new PartRenderer { branchId = branchId, partId = partId, connector = connector, renderer = smr });
            var toLocal = transform.worldToLocalMatrix * smr.rootBone.localToWorldMatrix;
            var lb = smr.localBounds;
            var world = new Bounds(toLocal.MultiplyPoint3x4(lb.center), Vector3.zero);
            for (int i = 0; i < 8; i++)
            {
                var e = lb.extents;
                world.Encapsulate(toLocal.MultiplyPoint3x4(lb.center + new Vector3((i & 1) == 0 ? -e.x : e.x, (i & 2) == 0 ? -e.y : e.y, (i & 4) == 0 ? -e.z : e.z)));
            }
            if (renderers.Count == 1) bindBoundsLocal = world;
            else bindBoundsLocal.Encapsulate(world);
        }

        static float ClipSpeed(ClipInfo[] clips, string name, float fallback)
        {
            var clip = Array.Find(clips, c => c.name == name);
            if (clip == null) return fallback;
            double speed = clip.speed_mps > 0.0 ? clip.speed_mps : clip.nominal_speed_mps;
            return speed > 0.0 ? (float)speed : fallback;
        }

        static float ClipDuration(ClipInfo[] clips, string name, float fallback)
        {
            var clip = Array.Find(clips, c => c.name == name);
            if (clip == null) return fallback;
            double duration = clip.duration_s > 0.0 ? clip.duration_s
                : clip.fps > 0 && clip.frames > 0 ? (double)clip.frames / clip.fps : 0.0;
            return duration > 0.0 ? (float)duration : fallback;
        }

        internal void SetMeasuredBounds(Bounds bind, Bounds neutral, Bounds animation)
        {
            bindBoundsLocal = bind;
            neutralBoundsLocal = neutral;
            animationBoundsLocal = animation;
        }
    }

    public static class SkeletonPose
    {
        public static Quaternion CanonicalBoneBasis(BoneData bone)
        {
            Vector3 y = (CritterFrame.Position(bone.tail_m) - CritterFrame.Position(bone.head_m)).normalized;
            Vector3 z = CritterFrame.Position(bone.up_m);
            z = (z - Vector3.Dot(z, y) * y).normalized;
            if (y.sqrMagnitude < 0.99f || z.sqrMagnitude < 0.99f)
                throw new AssemblyException("invalid canonical basis for bone " + bone.name);
            return Quaternion.LookRotation(z, y);
        }

        public static void ApplyNeutralPose(Transform instanceRoot, SkeletonData skeleton,
            Dictionary<string, Matrix4x4> rest, Matrix4x4 catalogToWorld)
        {
            if (skeleton.neutral_pose == null || skeleton.neutral_pose.rotations == null)
                throw new AssemblyException("skeleton " + skeleton.skeleton_id + " has no neutral pose");
            var transforms = new Dictionary<string, Transform>();
            foreach (var t in instanceRoot.GetComponentsInChildren<Transform>(true))
                if (!transforms.ContainsKey(t.name)) transforms.Add(t.name, t);
            var boneData = new Dictionary<string, BoneData>();
            foreach (var b in skeleton.bones) boneData[b.name] = b;

            foreach (var r in skeleton.neutral_pose.rotations)
            {
                if (!transforms.TryGetValue(r.bone_name, out var t) || !rest.TryGetValue(r.bone_name, out var bind)
                    || !boneData.TryGetValue(r.bone_name, out var data))
                    throw new AssemblyException("neutral pose references unknown bone " + r.bone_name);
                var q = r.rotation_xyzw;
                var delta = new Quaternion((float)q[0], (float)q[1], (float)q[2], (float)q[3]).normalized;
                Quaternion basisWorld = catalogToWorld.rotation * CanonicalBoneBasis(data);
                Quaternion bindWorld = (catalogToWorld * bind).rotation;
                Quaternion worldDelta = basisWorld * delta * Quaternion.Inverse(basisWorld);
                Quaternion importedLocalDelta = Quaternion.Inverse(bindWorld) * worldDelta * bindWorld;
                t.localRotation = t.localRotation * importedLocalDelta;
            }

            var offset = skeleton.neutral_pose.root_offset_m;
            if (offset != null && offset.Length == 3 && transforms.TryGetValue("root", out var root))
                root.position += catalogToWorld.MultiplyVector(CritterFrame.Position(offset));
        }
    }
}
