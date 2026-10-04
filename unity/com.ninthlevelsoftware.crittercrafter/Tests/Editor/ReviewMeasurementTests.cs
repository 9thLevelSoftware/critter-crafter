using System;
using System.Collections.Generic;
using System.Reflection;
using CritterCrafter.Editor;
using CritterCrafter.Locomotion;
using CritterCrafter.Review;
using NUnit.Framework;
using UnityEngine;

namespace CritterCrafter.Tests
{
    public class ReviewMeasurementTests
    {
        readonly List<UnityEngine.Object> _objects = new List<UnityEngine.Object>();

        T Keep<T>(T value) where T : UnityEngine.Object { _objects.Add(value); return value; }

        [TearDown]
        public void Cleanup()
        {
            for (int i = _objects.Count - 1; i >= 0; i--)
                if (_objects[i] != null) UnityEngine.Object.DestroyImmediate(_objects[i]);
            _objects.Clear();
        }

        AssembledCreature ConsolidatedFixture(string damage = null)
        {
            var model = Keep(new GameObject("ReviewSkeletonModel"));
            var bone = new GameObject("body_b0");
            bone.transform.SetParent(model.transform, false);
            bone.transform.localPosition = Vector3.up * .5f;
            model.AddComponent<Animator>();
            var mesh = Keep(new Mesh
            {
                vertices = new[] { new Vector3(-.1f, -.1f, 0f), new Vector3(.1f, .15f, 0f), new Vector3(0f, .2f, 0f) },
                triangles = new[] { 0, 1, 2 },
                bindposes = new[] { Matrix4x4.Translate(Vector3.down * .5f) },
                boneWeights = new[]
                {
                    new BoneWeight { boneIndex0 = 0, weight0 = 1 },
                    new BoneWeight { boneIndex0 = 0, weight0 = 1 },
                    new BoneWeight { boneIndex0 = 0, weight0 = 1 },
                },
            });
            mesh.RecalculateBounds();
            var proxyObject = new GameObject(SkeletonRest.ProxyName);
            proxyObject.transform.SetParent(model.transform, false);
            var proxy = proxyObject.AddComponent<SkinnedMeshRenderer>();
            proxy.sharedMesh = mesh;
            proxy.bones = new[] { bone.transform };
            proxy.rootBone = bone.transform;
            var branch = new BranchData
            {
                branch_id = "body", bone_names = new[] { "body_b0" }, gait_role = "locomotor",
                binding_profile_id = "fixture_profile", binding_profile_version = "1.0.0", binding_profile_hash = "fixture_hash",
                contacts = new[] { new ContactData { kind = "sliding", bone_index = 0, local_point_m = new[] { 0.0, -.45, 0.0 } } },
            };
            var skeleton = new SkeletonData
            {
                skeleton_id = "review_fixture", branches = new[] { branch },
                bones = new[] { new BoneData { name = "body_b0", head_m = new[] { 0.0, .5, 0.0 },
                    tail_m = new[] { 0.0, 1.5, 0.0 }, up_m = new[] { 0.0, 0.0, 1.0 } } },
                anatomy = new AnatomyData { support_branches = new[] { "body" } },
                locomotion = new LocomotionData { mode = "slide", body_on_ground = true, travel_per_cycle_m = .5 },
            };
            var recipe = new CritterRecipe
            {
                recipe_id = "review_fixture", library_id = "fixture", library_version = "0.3.0",
                generator = RecipeGenerator.Algorithm, pool_id = "review_review_fixture", skeleton_id = skeleton.skeleton_id,
                fills = new[] { new RecipeFill { branch_id = "body", part_id = "fixture_body",
                    binding_profile_id = branch.binding_profile_id, binding_profile_version = branch.binding_profile_version,
                    binding_profile_hash = branch.binding_profile_hash, length_scale = 1, girth_scale = 1 } },
            };
            if (damage == "recipe") recipe = null;
            else if (damage == "fill") recipe.fills = Array.Empty<RecipeFill>();
            var root = Keep(new GameObject("ConsolidatedReviewCreature"));
            var instance = UnityEngine.Object.Instantiate(model, root.transform, false);
            instance.GetComponentInChildren<SkinnedMeshRenderer>().gameObject.SetActive(false);
            var creature = root.AddComponent<AssembledCreature>();
            typeof(AssembledCreature).GetMethod("Init", BindingFlags.NonPublic | BindingFlags.Instance)
                .Invoke(creature, new object[] { recipe, instance.GetComponent<Animator>(), skeleton, model });
            var bodyObject = new GameObject("BakedBody");
            bodyObject.transform.SetParent(root.transform, false);
            var renderer = bodyObject.AddComponent<SkinnedMeshRenderer>();
            renderer.sharedMesh = mesh;
            renderer.bones = new[] { instance.transform.Find("body_b0") };
            renderer.rootBone = renderer.bones[0];
            typeof(AssembledCreature).GetMethod("AddRenderer", BindingFlags.NonPublic | BindingFlags.Instance)
                .Invoke(creature, new object[] { renderer, "", "baked_fixture", false });
            LocomotionRigBuilder.Build(creature, ~0);
            var ground = Keep(GameObject.CreatePrimitive(PrimitiveType.Cube));
            ground.transform.position = Vector3.down * .5f;
            ground.transform.localScale = new Vector3(4f, 1f, 4f);
            Physics.SyncTransforms();
            return creature;
        }

        [Test]
        public void ConsolidatedRendererMeasuresDeclaredContactAndDeformedSurfaceCoverage()
        {
            var creature = ConsolidatedFixture();
            var metrics = new LocomotionMetrics { grounding_measurement_complete = true, support_measurement_complete = true };
            using (var measurement = new ReviewGroundingMeasurement(creature, creature.GetComponent<CreatureGait>()))
                measurement.Measure(metrics, false);
            Assert.AreEqual(1, creature.Renderers.Count);
            Assert.AreEqual("", creature.Renderers[0].branchId);
            Assert.IsTrue(metrics.grounding_measurement_complete, metrics.grounding_measurement_problem);
            Assert.IsTrue(metrics.support_measurement_complete, metrics.grounding_measurement_problem);
            Assert.That(metrics.max_body_contact_hover_m, Is.EqualTo(.05f).Within(1e-5f));
            Assert.That(metrics.max_surface_penetration_m, Is.EqualTo(.1f).Within(1e-5f));
        }

        [TestCase("recipe")]
        [TestCase("fill")]
        public void ExistingConsolidatedSurfaceCannotReplaceMissingAuthoritativeFillCoverage(string damage)
        {
            var creature = ConsolidatedFixture(damage);
            var metrics = new LocomotionMetrics { grounding_measurement_complete = true, support_measurement_complete = true };
            using (var measurement = new ReviewGroundingMeasurement(creature, creature.GetComponent<CreatureGait>()))
                measurement.Measure(metrics, false);
            Assert.IsFalse(metrics.grounding_measurement_complete);
            Assert.IsFalse(metrics.support_measurement_complete);
            Assert.That(metrics.grounding_measurement_problem, Does.Contain("authoritative recipe coverage"));
        }

        [Test]
        public void AsymmetricContactUsesCanonicalLocalAxesThroughImportedBind()
        {
            var bone = new BoneData { name = "asymmetric", head_m = new[] { .3, .7, .2 },
                tail_m = new[] { .3, .7, 1.2 }, up_m = new[] { 0.0, 1.0, 0.0 } };
            var rest = Matrix4x4.TRS(new Vector3(-.3f, .7f, .2f), Quaternion.Euler(17, 61, -33), Vector3.one);
            var local = ReviewGroundingMeasurement.ImportedLocalPoint(bone, rest, new[] { .11, .27, -.08 });
            // Catalog local +X points -world X, +Y points +world Z, +Z points +world Y.
            var expected = new Vector3(-.19f, .62f, .47f);
            Assert.That(Vector3.Distance(rest.MultiplyPoint3x4(local), expected), Is.LessThan(1e-6f));
            var worldDelta = Matrix4x4.TRS(new Vector3(2f, .1f, -3f), Quaternion.Euler(23, 117, 9), Vector3.one);
            Assert.That(Vector3.Distance((worldDelta * rest).MultiplyPoint3x4(local), worldDelta.MultiplyPoint3x4(expected)), Is.LessThan(1e-6f));
        }

        [Test]
        public void ReactionGroundingStopsAtAuthoredReleaseWithoutLoopingTerminalClips()
        {
            var entry = new ContactScheduleEntry { grounding_declared = true, grounding_fraction = .25 };
            var death = new ClipInfo { name = "death", loop = false };
            Assert.IsTrue(ReviewGroundingMeasurement.ScheduledGrounding(entry, death, .249999));
            Assert.IsFalse(ReviewGroundingMeasurement.ScheduledGrounding(entry, death, .25));
            Assert.IsFalse(ReviewGroundingMeasurement.ScheduledGrounding(entry, death, 1.05));
            var looping = new ClipInfo { name = "stun", loop = true };
            Assert.IsTrue(ReviewGroundingMeasurement.ScheduledGrounding(entry, looping, 1.1));
            entry.phase_offset = .2;
            Assert.IsTrue(ReviewGroundingMeasurement.ScheduledGrounding(entry, looping, .01));
            Assert.IsFalse(ReviewGroundingMeasurement.ScheduledGrounding(entry, looping, .1));
            entry.grounding_fraction = 0;
            Assert.IsFalse(ReviewGroundingMeasurement.ScheduledGrounding(entry, death, 0));
            entry.grounding_fraction = 1;
            Assert.IsTrue(ReviewGroundingMeasurement.ScheduledGrounding(entry, death, 4));
        }

        [Test]
        public void MissingOrInvalidScheduleIsNotZeroGroundingPenalty()
        {
            var clip = new ClipInfo { name = "idle", loop = true };
            Assert.Throws<InvalidOperationException>(() => ReviewGroundingMeasurement.ScheduledGrounding(new ContactScheduleEntry(), clip, 0));
            foreach (double fraction in new[] { -.01, 1.01, double.NaN, double.PositiveInfinity })
                Assert.Throws<InvalidOperationException>(() => ReviewGroundingMeasurement.ScheduledGrounding(
                    new ContactScheduleEntry { grounding_declared = true, grounding_fraction = fraction }, clip, 0));
            var metrics = new LocomotionMetrics { grounding_measurement_complete = true, support_measurement_complete = true };
            using (var measurement = new ReviewGroundingMeasurement(null, null)) measurement.Measure(metrics, false);
            Assert.IsFalse(metrics.grounding_measurement_complete);
            Assert.IsFalse(metrics.support_measurement_complete);
            Assert.That(metrics.grounding_measurement_problem, Does.Contain("missing skeleton"));
        }

        [Test]
        public void GroundAtZeroAndMissingGroundRemainDistinct()
        {
            var plane = GameObject.CreatePrimitive(PrimitiveType.Cube);
            try
            {
                plane.transform.position = new Vector3(10000f, -.5f, 10000f);
                plane.transform.localScale = new Vector3(2f, 1f, 2f);
                Physics.SyncTransforms();
                Assert.IsTrue(ReviewCourse.TryGroundY(new Vector3(10000f, .1f, 10000f), out var height));
                Assert.That(height, Is.EqualTo(0f).Within(1e-5f));
                Assert.IsFalse(ReviewCourse.TryGroundY(new Vector3(10010f, .1f, 10000f), out _));
            }
            finally { UnityEngine.Object.DestroyImmediate(plane); }
        }

        [Test]
        public void CaptureMaxUsesCatalogMaximumAndPreservesNumericSpeeds()
        {
            var block = new LocomotionData { v_walk_mps = .4, v_run_mps = 1.3, v_max_mps = 2.75 };
            Assert.AreEqual(2.75f, LocomotionCapture.SpeedFor(block, "max"));
            Assert.AreEqual(.4f, LocomotionCapture.SpeedFor(block, "walk"));
            Assert.AreEqual(1.3f, LocomotionCapture.SpeedFor(block, "run"));
            Assert.AreEqual(2.5f, LocomotionCapture.SpeedFor(block, "2.5"));
        }
    }
}
