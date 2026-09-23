using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Animations.Rigging;

namespace CritterCrafter.Locomotion
{
    /// <summary>
    /// Adds Animation Rigging foot IK and a <see cref="CreatureGait"/> to assembled creatures whose
    /// catalog skeleton has a "legs" locomotion block. Registered with the assembler automatically.
    /// </summary>
    public class LocomotionRigHook : ICreatureRigHook
    {
#if UNITY_EDITOR
        [UnityEditor.InitializeOnLoadMethod]
#endif
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        static void Register() => CreatureAssembler.RegisterRigHook(new LocomotionRigHook());

        public void OnAssembled(AssembledCreature creature, AssemblyOptions options) =>
            LocomotionRigBuilder.Build(creature, options.groundMask);
    }

    public static class LocomotionRigBuilder
    {
        public const string RigName = "LocomotionRig";
        public const string TargetsName = "IK_Targets";

        /// <summary>
        /// Build the rig while the skeleton is in its neutral pose (as the assembler leaves it).
        /// Returns null when the skeleton has no runtime legs.
        /// </summary>
        public static CreatureGait Build(AssembledCreature creature, LayerMask groundMask)
        {
            var skeleton = creature != null ? creature.Skeleton : null;
            var block = skeleton?.locomotion;
            var animator = creature != null ? creature.Animator : null;
            if (block == null || !block.HasLegs || animator == null) return null;

            var animRoot = animator.transform;
            var bones = new Dictionary<string, Transform>();
            foreach (var t in animRoot.GetComponentsInChildren<Transform>(true))
                if (!bones.ContainsKey(t.name)) bones[t.name] = t;

            Matrix4x4 catalogToWorld = creature.transform.localToWorldMatrix;
            var targets = new GameObject(TargetsName).transform;
            targets.SetParent(creature.transform, false);
            var rigGo = new GameObject(RigName);
            rigGo.transform.SetParent(animRoot, false);
            var rig = rigGo.AddComponent<Rig>();

            var legs = new List<CreatureGait.Leg>();
            foreach (var leg in block.legs)
            {
                var chain = new Transform[leg.chain_bones.Length];
                for (int i = 0; i < chain.Length; i++)
                    if (!bones.TryGetValue(leg.chain_bones[i], out chain[i]))
                        throw new AssemblyException($"locomotion leg {leg.branch_id}: missing bone {leg.chain_bones[i]}");
                Vector3 home = catalogToWorld.MultiplyPoint3x4(CritterFrame.Position(leg.home_m));

                var tip = new GameObject(leg.branch_id + "_ik_tip").transform;
                tip.SetParent(chain[chain.Length - 1], false);
                tip.position = home;
                var target = new GameObject("IK_" + leg.branch_id).transform;
                target.SetParent(targets, false);
                target.position = home;

                var holder = new GameObject(leg.branch_id + "_ik");
                holder.transform.SetParent(rigGo.transform, false);
                MonoBehaviour constraint;
                if (leg.solver == "two_bone" && chain.Length == 3)
                {
                    // TwoBoneIK moves the ankle (chain[2]); keeping the target offset lets the target sit on
                    // the toe contact while the foot keeps its neutral orientation.
                    var c = holder.AddComponent<TwoBoneIKConstraint>();
                    var d = c.data;
                    d.root = chain[0]; d.mid = chain[1]; d.tip = chain[2]; d.target = target;
                    d.targetPositionWeight = 1f; d.targetRotationWeight = 0f; d.hintWeight = 0f;
                    d.maintainTargetPositionOffset = true; d.maintainTargetRotationOffset = false;
                    c.data = d;
                    constraint = c;
                }
                else
                {
                    // FABRIK starts from the animated (neutral, bent) pose each frame, which keeps the bend side.
                    var c = holder.AddComponent<ChainIKConstraint>();
                    var d = c.data;
                    d.root = chain[0]; d.tip = tip; d.target = target;
                    d.chainRotationWeight = 1f; d.tipRotationWeight = 0f;
                    d.maxIterations = 12; d.tolerance = 0.001f;
                    d.maintainTargetPositionOffset = false; d.maintainTargetRotationOffset = false;
                    c.data = d;
                    constraint = c;
                }
                legs.Add(new CreatureGait.Leg
                {
                    branchId = leg.branch_id,
                    target = target,
                    hip = chain[0],
                    constraint = constraint,
                    homeLocal = CritterFrame.Position(leg.home_m),
                    reach = (float)leg.reach_m,
                    stroke = (float)leg.stroke_m,
                    clearance = (float)leg.clearance_m,
                    walkPhase = leg.walk_phase,
                    runPhase = leg.run_phase,
                    support = leg.support,
                    attack = leg.branch_id == block.attack_branch_id,
                });
            }

            var builder = animRoot.GetComponent<RigBuilder>();
            if (builder == null) builder = animRoot.gameObject.AddComponent<RigBuilder>();
            builder.layers.Clear();
            builder.layers.Add(new RigLayer(rig, true));
            animator.Rebind();
            builder.Build();

            var gait = creature.gameObject.GetComponent<CreatureGait>();
            if (gait == null) gait = creature.gameObject.AddComponent<CreatureGait>();
            gait.Configure(animRoot, rig, legs, groundMask);
            return gait;
        }
    }
}
