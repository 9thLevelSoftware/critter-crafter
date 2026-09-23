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
        /// <summary>
        /// Pole target that keeps the knee on its neutral side: pushed from the hip-to-foot chord through
        /// the neutral knee, so insect knees stay high and mammal knees keep their direction.
        /// </summary>
        static MultiAimConstraintData.Axis DominantAxis(Vector3 local)
        {
            Vector3 a = new Vector3(Mathf.Abs(local.x), Mathf.Abs(local.y), Mathf.Abs(local.z));
            if (a.x >= a.y && a.x >= a.z) return local.x >= 0 ? MultiAimConstraintData.Axis.X : MultiAimConstraintData.Axis.X_NEG;
            if (a.y >= a.z) return local.y >= 0 ? MultiAimConstraintData.Axis.Y : MultiAimConstraintData.Axis.Y_NEG;
            return local.z >= 0 ? MultiAimConstraintData.Axis.Z : MultiAimConstraintData.Axis.Z_NEG;
        }

        public static Vector3 KneeHint(Vector3 hip, Vector3 knee, Vector3 foot, float reach)
        {
            Vector3 chord = foot - hip;
            Vector3 onChord = hip + Vector3.Project(knee - hip, chord);
            Vector3 bend = knee - onChord;
            if (bend.sqrMagnitude < 1e-8f) bend = Vector3.up;
            return knee + bend.normalized * (0.5f * reach);
        }

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
                // Targets carry the body's rotation every frame; the foot keeps its neutral angle to the body.
                target.SetPositionAndRotation(home, animRoot.rotation);

                var holder = new GameObject(leg.branch_id + "_ik");
                holder.transform.SetParent(rigGo.transform, false);
                MonoBehaviour constraint;
                // limb3 ("two_bone"): thigh/shin hinge ending at the ankle (b2).
                // insect_leg4 ("hinge4"): the coxa (b0) yaws toward the foot through an aim constraint, then
                // femur/tibia hinge to the tarsus (b3). CreatureGait places the target on the ankle, keeping
                // the neutral ankle-to-contact offset, so the foot segment holds its angle as the knee bends.
                int hingeRoot = leg.solver == "hinge4" && chain.Length == 4 ? 1
                    : leg.solver == "two_bone" && chain.Length == 3 ? 0 : -1;
                var built = new CreatureGait.Leg();
                if (hingeRoot >= 0)
                {
                    Transform upper = chain[hingeRoot], knee = chain[hingeRoot + 1], ankle = chain[hingeRoot + 2];
                    Quaternion toBody = Quaternion.Inverse(animRoot.rotation);
                    Vector3 coxa = chain[0].position;
                    var hint = new GameObject(leg.branch_id + "_ik_hint").transform;
                    hint.SetParent(targets, false);
                    hint.position = KneeHint(upper.position, knee.position, home, (float)leg.reach_m);
                    if (hingeRoot == 1)
                    {
                        var aim = new GameObject(leg.branch_id + "_coxa_aim").transform;
                        aim.SetParent(targets, false);
                        Vector3 direction = (upper.position - coxa).normalized;
                        aim.position = coxa + direction;
                        // Constraints evaluate in hierarchy order: the coxa aim must come before the leg IK.
                        var aimHolder = new GameObject(leg.branch_id + "_coxa");
                        aimHolder.transform.SetParent(rigGo.transform, false);
                        aimHolder.transform.SetSiblingIndex(holder.transform.GetSiblingIndex());
                        var a = aimHolder.AddComponent<MultiAimConstraint>();
                        var ad = a.data;
                        ad.constrainedObject = chain[0];
                        ad.sourceObjects = new WeightedTransformArray { new WeightedTransform(aim, 1f) };
                        ad.aimAxis = DominantAxis(Quaternion.Inverse(chain[0].rotation) * direction);
                        ad.upAxis = ad.aimAxis == MultiAimConstraintData.Axis.Y || ad.aimAxis == MultiAimConstraintData.Axis.Y_NEG
                            ? MultiAimConstraintData.Axis.Z : MultiAimConstraintData.Axis.Y;
                        ad.worldUpType = MultiAimConstraintData.WorldUpType.None;
                        ad.maintainOffset = true;
                        ad.constrainedXAxis = true; ad.constrainedYAxis = true; ad.constrainedZAxis = true;
                        ad.limits = new Vector2(-180f, 180f);
                        a.data = ad;
                        built.coxaAim = aim;
                        built.coxaDirection = toBody * direction;
                    }
                    var c = holder.AddComponent<TwoBoneIKConstraint>();
                    var d = c.data;
                    d.root = upper; d.mid = knee; d.tip = ankle; d.target = target; d.hint = hint;
                    d.targetPositionWeight = 1f; d.targetRotationWeight = 1f; d.hintWeight = 1f;
                    // Animation Rigging's maintained offsets are world vectors that do not turn with the
                    // target, so CreatureGait places the target on the ankle itself.
                    d.maintainTargetPositionOffset = false; d.maintainTargetRotationOffset = false;
                    c.data = d;
                    constraint = c;
                    built.hinge = true;
                    built.hint = hint;
                    built.coxa = chain[0];
                    built.hingeReach = Vector3.Distance(upper.position, knee.position) + Vector3.Distance(knee.position, ankle.position);
                    built.ankleOffset = toBody * (ankle.position - home);
                    built.ankleRotation = toBody * ankle.rotation;
                    built.homeFromCoxa = toBody * (home - coxa);
                    built.femurFromCoxa = toBody * (upper.position - coxa);
                    built.hintFromCoxa = toBody * (hint.position - coxa);
                    target.SetPositionAndRotation(ankle.position, ankle.rotation);
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
                built.branchId = leg.branch_id;
                built.target = target;
                built.hip = chain[0];
                built.constraint = constraint;
                built.homeLocal = CritterFrame.Position(leg.home_m);
                built.reach = (float)leg.reach_m;
                built.stroke = (float)leg.stroke_m;
                built.clearance = (float)leg.clearance_m;
                built.walkPhase = leg.walk_phase;
                built.runPhase = leg.run_phase;
                built.support = leg.support;
                built.attack = leg.branch_id == block.attack_branch_id;
                legs.Add(built);
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
