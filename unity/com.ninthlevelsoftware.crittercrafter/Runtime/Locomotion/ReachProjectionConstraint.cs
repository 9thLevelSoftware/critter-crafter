using System;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Animations.Rigging;

namespace CritterCrafter.Locomotion
{
    public struct ReachProjectionResult
    {
        public bool clamped;
        public float reachFraction;
    }

    [Serializable]
    public struct ReachProjectionData : IAnimationJobData
    {
        public Transform root;
        public Transform mid;
        public Transform tip;
        [SyncSceneToStream] public Transform target;
        [SyncSceneToStream, SerializeField] float m_Planted;

        public bool planted { get => m_Planted >= 0.5f; set => m_Planted = value ? 1f : 0f; }
        internal string plantedProperty => ConstraintsUtils.ConstructConstraintDataPropertyName(nameof(m_Planted));

        public bool IsValid() => root != null && mid != null && tip != null && target != null
            && mid.IsChildOf(root) && tip.IsChildOf(mid);

        public void SetDefaultValues()
        {
            root = mid = tip = target = null;
            m_Planted = 1f;
        }
    }

    public struct ReachProjectionJob : IWeightedAnimationJob
    {
        public ReadOnlyTransformHandle root, mid, tip;
        public ReadWriteTransformHandle target;
        public FloatProperty planted;
        public NativeArray<ReachProjectionResult> result;
        public FloatProperty jobWeight { get; set; }

        public void ProcessRootMotion(AnimationStream stream) { }

        public void ProcessAnimation(AnimationStream stream)
        {
            var measured = new ReachProjectionResult();
            if (jobWeight.Get(stream) > 0f)
            {
                Vector3 origin = root.GetPosition(stream);
                Vector3 knee = mid.GetPosition(stream);
                float length = Vector3.Distance(origin, knee) + Vector3.Distance(knee, tip.GetPosition(stream));
                float limit = CreatureGait.HingeReachFraction * length;
                Vector3 point = target.GetPosition(stream);
                Vector3 distance = point - origin;
                if (limit > 0f)
                {
                    measured.reachFraction = distance.magnitude / limit;
                    measured.clamped = distance.sqrMagnitude > limit * limit;
                    if (measured.clamped)
                    {
                        Vector3 projected = planted.Get(stream) >= 0.5f
                            ? CreatureGait.ClampHorizontally(point, origin, limit)
                            : origin + distance.normalized * limit;
                        target.SetPosition(stream, projected);
                    }
                }
            }
            result[0] = measured;
        }
    }

    public sealed class ReachProjectionBinder : AnimationJobBinder<ReachProjectionJob, ReachProjectionData>
    {
        public override ReachProjectionJob Create(Animator animator, ref ReachProjectionData data, Component component)
        {
            var job = new ReachProjectionJob
            {
                root = ReadOnlyTransformHandle.Bind(animator, data.root),
                mid = ReadOnlyTransformHandle.Bind(animator, data.mid),
                tip = ReadOnlyTransformHandle.Bind(animator, data.tip),
                target = ReadWriteTransformHandle.Bind(animator, data.target),
                planted = FloatProperty.Bind(animator, component, data.plantedProperty),
                result = new NativeArray<ReachProjectionResult>(1, Allocator.Persistent)
            };
            ((ReachProjectionConstraint)component).result = job.result;
            return job;
        }

        public override void Destroy(ReachProjectionJob job) => job.result.Dispose();
    }

    public sealed class ReachProjectionConstraint : RigConstraint<ReachProjectionJob, ReachProjectionData, ReachProjectionBinder>
    {
        internal NativeArray<ReachProjectionResult> result;

        internal bool TryGetResult(out ReachProjectionResult value)
        {
            value = default;
            if (!isActiveAndEnabled || !result.IsCreated) return false;
            value = result[0];
            return true;
        }
    }
}
