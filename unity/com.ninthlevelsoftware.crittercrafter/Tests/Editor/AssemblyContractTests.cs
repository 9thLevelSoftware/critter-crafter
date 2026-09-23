using System.Collections.Generic;
using CritterCrafter.Editor;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace CritterCrafter.Tests
{
    public class AssemblyContractTests
    {
        readonly List<Object> _objects = new List<Object>();
        readonly List<string> _assets = new List<string>();

        [TearDown]
        public void Cleanup()
        {
            foreach (var o in _objects) if (o != null) Object.DestroyImmediate(o);
            foreach (var path in _assets) AssetDatabase.DeleteAsset(path);
            _objects.Clear();
            _assets.Clear();
        }

        [Test]
        public void BindAndNeutralPoseAreDistinctAndNeutralIsAppliedOnce()
        {
            var skeletonGo = new GameObject("Skeleton");
            var root = new GameObject("root");
            root.transform.SetParent(skeletonGo.transform, false);
            _objects.Add(skeletonGo);
            var rest = new Dictionary<string, Matrix4x4>
            {
                ["root"] = Matrix4x4.TRS(new Vector3(0f, 1f, 0f), Quaternion.identity, Vector3.one),
            };
            var skeleton = new SkeletonData
            {
                skeleton_id = "pose_test",
                bones = new[]
                {
                    new BoneData { name = "root", parent = "", head_m = new[] { 0.0, 1.0, 0.0 },
                        tail_m = new[] { 0.0, 2.0, 0.0 }, up_m = new[] { 0.0, 0.0, 1.0 } },
                },
                neutral_pose = new NeutralPoseData
                {
                    root_offset_m = new[] { 0.0, 0.2, 0.0 },
                    rotations = new[]
                    {
                        new NeutralRotationData { bone_name = "root", rotation_xyzw = Q(Quaternion.AngleAxis(30f, Vector3.right)) },
                    },
                },
            };

            SkeletonRest.ApplyBindPose(skeletonGo.transform, rest, Matrix4x4.identity);
            Assert.That(Vector3.Distance(root.transform.position, new Vector3(0f, 1f, 0f)), Is.LessThan(1e-6f));
            Assert.That(Quaternion.Angle(root.transform.rotation, Quaternion.identity), Is.LessThan(1e-4f));

            SkeletonPose.ApplyNeutralPose(skeletonGo.transform, skeleton, rest, Matrix4x4.identity);
            Assert.That(root.transform.position.y, Is.EqualTo(1.2f).Within(1e-5f));
            Assert.That(Quaternion.Angle(root.transform.rotation, Quaternion.identity), Is.EqualTo(30f).Within(0.01f));

            SkeletonRest.ApplyBindPose(skeletonGo.transform, rest, Matrix4x4.identity);
            Assert.That(root.transform.position.y, Is.EqualTo(1f).Within(1e-5f));
            Assert.That(Quaternion.Angle(root.transform.rotation, Quaternion.identity), Is.LessThan(1e-4f));
        }

        [Test]
        public void TwoSubmeshMaterialsArePreserved()
        {
            var shader = Shader.Find("Hidden/InternalErrorShader") ?? Shader.Find("Standard");
            var a = new Material(shader);
            var b = new Material(shader);
            var mesh = new Mesh { subMeshCount = 2 };
            _objects.Add(a); _objects.Add(b); _objects.Add(mesh);
            var entry = new CritterLibrary.PartEntry { material = a, materials = new[] { a, b } };
            var assigned = CreatureAssembler.MaterialsForPart(entry, mesh);
            Assert.AreEqual(2, assigned.Length);
            Assert.AreSame(a, assigned[0]);
            Assert.AreSame(b, assigned[1]);
        }

        [Test]
        public void ChainMappingNeverClampsExtraBones()
        {
            var branch = new BranchData { branch_id = "leg", bone_names = new[] { "leg_b0", "leg_b1" } };
            Assert.AreEqual("leg_b1", CreatureAssembler.MapBone("b1", branch, false));
            Assert.Throws<AssemblyException>(() => CreatureAssembler.MapBone("b2", branch, false));
        }

        [Test]
        public void FbxBoneReparameterizationRecoversRotatedAsymmetricCanonicalBasis()
        {
            var bone = new BoneData
            {
                name = "asymmetric",
                head_m = new[] { 0.23, -0.41, 0.17 },
                tail_m = new[] { 0.74, 0.08, 0.93 },
                up_m = new[] { 0.21, 0.97, -0.31 },
            };
            Quaternion canonical = SkeletonPose.CanonicalBoneBasis(bone);
            Quaternion imported = canonical * FrameProbe.FbxBoneBasis;
            Quaternion recovered = FrameProbe.RecoverCanonicalBasis(imported);
            Assert.That(Quaternion.Angle(canonical, recovered), Is.LessThan(0.0001f));
            Assert.That(Vector3.Angle(imported * Vector3.up, canonical * Vector3.up), Is.LessThan(0.0001f));
            Assert.That(Vector3.Angle(imported * Vector3.forward, canonical * -Vector3.forward), Is.LessThan(0.0001f));
        }

        [Test]
        public void ImportedAsymmetricMotionDeltaRecoversCatalogAxisSigns()
        {
            Quaternion catalog = Quaternion.Euler(17f, -23f, 31f);
            // Forward and inverse component mappings are the same reflection.
            Quaternion imported = new Quaternion(-catalog.x, -catalog.y, catalog.z, catalog.w);
            Quaternion recovered = FrameProbe.RecoverCatalogMotionDelta(imported);
            Assert.That(Quaternion.Angle(catalog, recovered), Is.LessThan(0.0001f));
        }

        [Test]
        public void RuntimePlaybackMatchesMetadataThresholdsAndLimits()
        {
            Assert.AreEqual(1f, CreatureMotion.PlaybackRateForSpeed(0f, 1.2f, 3f));
            Assert.AreEqual(1f, CreatureMotion.PlaybackRateForSpeed(0.2f, 1.2f, 3f));
            Assert.AreEqual(1f, CreatureMotion.PlaybackRateForSpeed(0.9f, 1.2f, 3f), 1e-6f);
            Assert.AreEqual(1f, CreatureMotion.PlaybackRateForSpeed(1.2f, 1.2f, 3f), 1e-6f);
            Assert.AreEqual(1f, CreatureMotion.PlaybackRateForSpeed(2f, 1.2f, 3f), 1e-6f);
            Assert.AreEqual(1.5f, CreatureMotion.PlaybackRateForSpeed(6f, 1.2f, 3f), 1e-6f);
            Assert.AreEqual(1f, CreatureMotion.PlaybackRateForSpeed(0.2f, 1.2f, 3f, 0.7f, 1.2f), 1e-6f);
            Assert.AreEqual(1.2f, CreatureMotion.PlaybackRateForSpeed(6f, 1.2f, 3f, 0.7f, 1.2f), 1e-6f);
            Assert.AreEqual(1f, CreatureMotion.PlaybackRateForSpeed(0.01519125f, 0.0303825f, 0.04051f), 1e-6f,
                "idle/walk blend already scales a slow authored gait to the requested speed");
            Assert.AreEqual(1.125f, CreatureMotion.PlaybackRateForSpeed(0.6f, 1.2f, 3f,
                2f, 1.6f, 1.2f, 0.5f, 1.5f), 1e-6f,
                "different idle/walk durations require only a normalized-clock correction");
        }

        [Test]
        public void SlowCreepEntersMovingState()
        {
            var go = new GameObject("slow-creep");
            _objects.Add(go);
            var motion = go.AddComponent<CreatureMotion>();
            motion.SetVelocity(new Vector3(0.01f, 0f, 0f));
            Assert.AreEqual(CreatureState.Moving, motion.State);
            motion.SetVelocity(Vector3.zero);
            Assert.AreEqual(CreatureState.Idle, motion.State);
        }

        [Test]
        public void ControllerUsesContinuationMetadataAndCompletesActions()
        {
            var clips = new Dictionary<string, AnimationClip>();
            foreach (var name in new[] { "idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death" })
            {
                var clip = new AnimationClip { name = name };
                clips[name] = clip;
                _objects.Add(clip);
            }
            var skeleton = new SkeletonData
            {
                asset = new SkeletonAssetInfo
                {
                    clips = new[]
                    {
                        Clip("idle", "locomotion"), Clip("walk", "locomotion"), Clip("run", "locomotion"),
                        Clip("stun", "locomotion"), Clip("telegraph", "attack"), Clip("attack", "locomotion"),
                        Clip("hit", "locomotion"), Clip("death", "locomotion"),
                    },
                },
            };
            System.Array.Find(skeleton.asset.clips, c => c.name == "walk").speed_mps = 0.0303825;
            System.Array.Find(skeleton.asset.clips, c => c.name == "run").speed_mps = 0.04051;
            const string path = "Assets/__CritterContinuationTest.controller";
            _assets.Add(path);
            var controller = AnimatorBuilder.Build(path, clips, skeleton);
            var machine = controller.layers[0].stateMachine;
            AnimatorState State(string name) => System.Array.Find(machine.states, s => s.state.name == name).state;
            var locomotion = State("Locomotion");
            var tree = (BlendTree)locomotion.motion;
            Assert.AreEqual(0.0303825f, tree.children[1].threshold, 1e-7f);
            Assert.AreEqual(0.04051f, tree.children[2].threshold, 1e-7f);
            var telegraph = State("Telegraph");
            var attack = State("Attack");
            var telegraphNext = System.Array.Find(telegraph.transitions, t => t.destinationState == attack);
            Assert.IsNotNull(telegraphNext, "telegraph must continue automatically into attack");
            Assert.AreEqual(1f, telegraphNext.exitTime);
            Assert.AreEqual(0f, telegraphNext.duration);
            var attackNext = System.Array.Find(attack.transitions, t => t.destinationState == locomotion);
            Assert.IsNotNull(attackNext);
            Assert.AreEqual(1f, attackNext.exitTime, "attack recovery must play through its endpoint");
            Assert.IsTrue(System.Array.Exists(machine.anyStateTransitions,
                t => t.destinationState == attack), "explicit Attack trigger must enter attack directly");
        }

        static ClipInfo Clip(string name, string continuation) => new ClipInfo
        {
            name = name,
            playback = new ClipPlayback { wrap = name == "idle" || name == "walk" || name == "run" || name == "stun" ? "loop" : "once",
                phase_range = new[] { 0.0, 1.0 }, rate_range = new[] { 0.5, 1.5 }, continuation = continuation },
        };

        static double[] Q(Quaternion q) => new[] { (double)q.x, q.y, q.z, q.w };
    }
}
