using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CritterCrafter.Editor;
using CritterCrafter.Review;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace CritterCrafter.Tests
{
    /// <summary>
    /// Integration tests against a built library: CRITTER_LIBRARY_DIR, or the newest
    /// &lt;repo&gt;/library/*/catalog.json next to the TestProject. Ignored when no library is built.
    /// </summary>
    public class LibraryTests
    {
        LibraryImporter.Report _report;
        CritterLibrary _approvedLibrary;
        CritterLibrary Lib => _approvedLibrary;
        CritterLibrary RawLib => _report.Library;
        readonly List<GameObject> _spawned = new List<GameObject>();

        static string FindLibraryDir()
        {
            var env = System.Environment.GetEnvironmentVariable("CRITTER_LIBRARY_DIR");
            if (!string.IsNullOrEmpty(env) && File.Exists(Path.Combine(env, "catalog.json"))) return env;
            var root = Path.GetFullPath("../../library");
            if (!Directory.Exists(root)) return null;
            return Directory.GetDirectories(root).Where(d => File.Exists(Path.Combine(d, "catalog.json")))
                .OrderByDescending(Directory.GetLastWriteTimeUtc).FirstOrDefault();
        }

        [OneTimeSetUp]
        public void ImportLibrary()
        {
            var dir = FindLibraryDir();
            if (dir == null) Assert.Ignore("no built critter library (run `critter library build`)");
            _report = LibraryImporter.Import(dir);
            _approvedLibrary = RawLib.EditorCreateApprovedSkeletonClone();
        }

        [OneTimeTearDown]
        public void CleanupLibrary() { if (_approvedLibrary != null) Object.DestroyImmediate(_approvedLibrary); }

        [TearDown]
        public void Cleanup()
        {
            foreach (var go in _spawned) if (go != null) Object.DestroyImmediate(go);
            _spawned.Clear();
        }

        /// <summary>Sliding-body skeleton whose phase-driven overlay animates the body (undulation).</summary>
        const string BakedTravelSkeleton = "serpentine_limbless_articulated_balanced_v3";
        const string RuntimeLegSkeleton = "hexapod_compact_insect_balanced_v3";

        AssembledCreature SpawnSkeleton(string skeletonId)
        {
            var skeleton = Lib.Catalog.FindSkeleton(skeletonId);
            Assert.IsNotNull(skeleton, skeletonId);
            var c = CreatureAssembler.Assemble(Lib, LocomotionCapture.ReferenceRecipe(Lib.Catalog, skeleton), AssemblyOptions.Default);
            _spawned.Add(c.gameObject);
            return c;
        }

        AssembledCreature Spawn(string pool, long seed)
        {
            var c = CreatureAssembler.Assemble(Lib, Lib.Generate(pool, seed), AssemblyOptions.Default);
            _spawned.Add(c.gameObject);
            return c;
        }

        [Test]
        public void ImportReportsNoProblems() =>
            Assert.That(_report.Problems, Is.Empty, string.Join("\n", _report.Problems));

        [Test]
        public void AllSkeletonBindAndMotionImportsReportNoProblems()
        {
            var skeletonProblems = _report.Problems.Where(p => !p.StartsWith("part without asset:")).ToArray();
            Assert.That(skeletonProblems, Is.Empty, string.Join("\n", skeletonProblems));
        }

        [Test]
        public void DraftCandidateLibraryIsRejectedByRuntimeDefault() =>
            Assert.Throws<GenerationException>(() => RawLib.Generate("any", 1));

        [Test]
        public void FrameProbeSnapsCoincideWithBranchRootBones()
        {
            foreach (var s in Lib.Catalog.skeletons)
            {
                float err = FrameProbe.MaxSnapError(Lib.FindSkeleton(s.skeleton_id).model, s, out var worst);
                Assert.Less(err, FrameProbe.Tolerance, $"{s.skeleton_id}: worst branch {worst}");
            }
        }

        [Test]
        public void EveryPoolAssemblesHundredSeedsWithinBudget()
        {
            foreach (var pool in Lib.Catalog.pools)
                for (long seed = 1; seed <= 100; seed++)
                {
                    var c = Spawn(pool.pool_id, seed);
                    Assert.IsFalse(c.IsFallback, $"{pool.pool_id}/{seed}: {string.Join(";", c.Diagnostics)}");
                    Assert.LessOrEqual(c.Triangles, Lib.Catalog.limits.max_triangles);
                    Object.DestroyImmediate(c.gameObject);
                }
        }

        [Test]
        public void BindPosePlacesEveryMeshOnItsSnapFrame()
        {
            foreach (var pool in new[] { "biped", "quadruped", "crawler" })
            {
                var c = Spawn(pool, 5);
                c.ApplyBindPose();
                foreach (var pr in c.Renderers)
                {
                    var branch = c.Skeleton.FindBranch(pr.branchId);
                    var part = Lib.Catalog.FindPart(pr.partId);
                    var entry = Lib.FindPart(pr.partId);
                    var src = entry.model.GetComponentInChildren<SkinnedMeshRenderer>(true);
                    float lengthScale = pr.connector ? 1f : (float)(branch.length_m / part.length_m);
                    var meshToPart = CreatureAssembler.MeshToPart(entry.model, src);
                    var expected = pr.renderer.transform.worldToLocalMatrix * c.transform.localToWorldMatrix
                                   * CritterFrame.Snap(branch.snap, lengthScale) * meshToPart;
                    var baked = new Mesh();
                    pr.renderer.BakeMesh(baked, true);
                    var srcVerts = src.sharedMesh.vertices;
                    var got = baked.vertices;
                    float max = 0f;
                    for (int i = 0; i < srcVerts.Length; i += 7)
                        max = Mathf.Max(max, Vector3.Distance(got[i], expected.MultiplyPoint3x4(srcVerts[i])));
                    Assert.Less(max, 1e-3f, $"{pool}: {pr.branchId}/{pr.partId}");
                    Object.DestroyImmediate(baked);
                }
            }
        }

        [Test]
        public void ConnectorBendsWithTheChildBranch()
        {
            var c = Spawn("biped", 11);
            var maybe = c.Renderers.FirstOrDefault(r => r.connector && r.branchId.StartsWith("leg"));
            if (maybe.renderer == null) Assert.Ignore("catalog has no legacy skinned leg connector");
            var pr = maybe;
            var before = new Mesh();
            pr.renderer.BakeMesh(before, true);
            var childRoot = pr.renderer.bones.Last();  // connector b1 -> branch root bone
            childRoot.localRotation *= Quaternion.Euler(35f, 0f, 0f);
            var after = new Mesh();
            pr.renderer.BakeMesh(after, true);
            var a = before.vertices;
            var b = after.vertices;
            float moved = 0f, still = float.MaxValue;
            for (int i = 0; i < a.Length; i++)
            {
                float d = Vector3.Distance(a[i], b[i]);
                moved = Mathf.Max(moved, d);
                still = Mathf.Min(still, d);
            }
            Assert.Greater(moved, 0.02f, "child side of the connector must follow the branch");
            Assert.Less(still, 1e-3f, "parent side of the connector must stay put");
        }

        [Test]
        public void ClipsHaveNoHorizontalRootMotion()
        {
            foreach (var s in Lib.Catalog.skeletons)
            {
                var entry = Lib.FindSkeleton(s.skeleton_id);
                var go = (GameObject)Object.Instantiate(entry.model);
                _spawned.Add(go);
                var root = go.GetComponentsInChildren<Transform>().First(t => t.name == "root");
                var rootStart = root.position;
                foreach (var clip in AnimatorBuilder.LoadClips(AssetDatabase.GetAssetPath(entry.model)).Values)
                {
                    for (float t = 0f; t <= 10f; t += 0.1f)
                    {
                        clip.SampleAnimation(go, clip.isLooping ? t % clip.length : Mathf.Min(t, clip.length));
                        Assert.AreEqual(Vector3.zero, go.transform.position, $"{s.skeleton_id}/{clip.name} moved the model root");
                        Assert.Less(Mathf.Abs(root.position.x - rootStart.x) + Mathf.Abs(root.position.z - rootStart.z), 1e-4f,
                            $"{s.skeleton_id}/{clip.name} moves the root bone horizontally at t={t}");
                    }
                }
            }
        }

        [Test]
        public void AnimatorDrivesBonesFromController()
        {
            var c = SpawnSkeleton(BakedTravelSkeleton);
            Assert.IsNotNull(c.Animator.runtimeAnimatorController);
            Assert.IsFalse(c.Skeleton.locomotion != null && c.Skeleton.locomotion.HasLegs);
            var locomotor = c.Skeleton.branches.First(b => b.gait_role == "locomotor");
            var leg = c.Animator.GetComponentsInChildren<Transform>().First(t => t.name == locomotor.bone_names[3]);
            var rest = leg.localRotation;
            c.Animator.SetFloat(CreatureMotion.SpeedParam, AnimatorBuilder.WalkSpeed);
            c.Animator.SetFloat(CreatureMotion.PlaybackRateParam, 1f);
            for (int i = 0; i < 5; i++) c.Animator.Update(0.05f);
            Assert.Greater(Quaternion.Angle(rest, leg.localRotation), 1f, "walk clip should swing the leg");
        }

        [UnityTest]
        public IEnumerator RuntimeLegsPlantFeetAtGameSpeed()
        {
            // Real Play Mode (Animator + Animation Rigging + skinning exactly as in the game): drive the
            // creature like an agent at 2.5 m/s over a ground collider for 3 s. Planted feet must stay put in
            // the world, IK must reach its targets and support must never drop below the minimum.
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
            yield return new EnterPlayMode();
            Time.captureFramerate = 30;
            var holder = new GameObject("LocomotionTest");
            var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
            ground.transform.SetParent(holder.transform, false);
            ground.transform.localScale = Vector3.one * 10f;
            Physics.SyncTransforms();
            var c = LocomotionCapture.Spawn(Lib, RuntimeLegSkeleton, holder.transform, out var restore);
            var gait = c.GetComponent<CritterCrafter.Locomotion.CreatureGait>();
            var block = c.Skeleton.locomotion;
            LocomotionMetrics metrics = null;
            bool sawLocomotionState = false;
            if (gait != null)
            {
                var recorder = holder.AddComponent<LocomotionRecorder>();
                recorder.Begin(gait, ReviewCourse.Straight(2.5f, 3f), new LocomotionMetrics { skeleton_id = RuntimeLegSkeleton });
                while (!recorder.Done)
                {
                    yield return null;
                    sawLocomotionState |= c.Animator.GetCurrentAnimatorStateInfo(0).IsName("Locomotion");
                }
                metrics = recorder.Metrics;
            }
            Object.Destroy(holder);
            restore();
            Time.captureFramerate = 0;
            yield return new ExitPlayMode();

            Assert.IsNotNull(gait, "runtime-leg skeletons get a CreatureGait");
            Assert.Less(metrics.max_ik_residual_m, 0.01f, "IK residual");
            // Dragging at game speed shows up as ~8 cm/frame; allow sub-centimetre settling at step changes.
            Assert.Less(metrics.max_planted_slip_m, 0.01f, "planted feet slide in the world");
            Assert.GreaterOrEqual(metrics.min_planted_supports, block.min_support, "support");
            Assert.LessOrEqual(metrics.cadence_hz, block.cadence_max_hz + 1e-4);
            Assert.IsFalse(metrics.overspeed);
            Assert.IsTrue(sawLocomotionState, "moving plays the overlay");
        }

        [Test]
        public void GaitPhaseDrivesTheLocomotionOverlayClock()
        {
            // Phase-driven skeletons: CreatureGait's GaitPhase (Motion Time) poses the Locomotion overlay, so
            // the baked undulation/upper body stays locked to the runtime clock regardless of elapsed time.
            // (AnimatorStateInfo.normalizedTime keeps reporting the state's own clock; the pose is what counts.)
            var c = SpawnSkeleton(BakedTravelSkeleton);
            var animator = c.Animator;
            var bone = animator.GetComponentsInChildren<Transform>().First(t => t.name == "body_b4");
            animator.SetFloat(CreatureMotion.SpeedParam, 2.5f);
            animator.SetFloat("Gait", 0f);
            animator.Play("Locomotion", 0, 0f);
            animator.Update(0f);
            Quaternion Pose(float phase, float dt)
            {
                animator.SetFloat("GaitPhase", phase);
                animator.Update(dt);
                Assert.IsTrue(animator.GetCurrentAnimatorStateInfo(0).IsName("Locomotion"));
                return bone.localRotation;
            }
            var first = Pose(0.2f, 0.1f);
            var other = Pose(0.65f, 0.37f);
            var again = Pose(0.2f, 0.23f);
            Assert.Less(Quaternion.Angle(first, again), 0.01f, "same phase, same pose");
            Assert.Greater(Quaternion.Angle(first, other), 0.5f, "different phase, different pose");
        }

        [Test]
        public void EveryPartAndConnectorKeepsOneRendererAndAtMostTwoMaterials()
        {
            var c = Spawn("any", 7);
            foreach (var part in c.Renderers)
            {
                Assert.IsNotNull(part.renderer);
                Assert.LessOrEqual(part.renderer.sharedMesh.subMeshCount, 2, part.partId);
                Assert.AreEqual(part.renderer.sharedMesh.subMeshCount, part.renderer.sharedMaterials.Length, part.partId);
            }
            Assert.AreEqual(c.Renderers.Count, c.GetComponentsInChildren<SkinnedMeshRenderer>(true)
                .Count(r => r.name != SkeletonRest.ProxyName));
        }

        [Test]
        public void CollisionUsesNeutralPoseAndAnimationBoundsCoverBindAndNeutral()
        {
            var c = Spawn("biped", 5);
            var capsule = c.GetComponent<CapsuleCollider>();
            Assert.IsNotNull(capsule);
            Assert.That(Vector3.Distance(capsule.center, c.NeutralBoundsLocal.center), Is.LessThan(1e-5f));
            Assert.That(capsule.radius, Is.EqualTo(Mathf.Max(0.1f,
                0.5f * Mathf.Max(c.NeutralBoundsLocal.size.x, c.NeutralBoundsLocal.size.z))).Within(1e-5f));
            Assert.That(capsule.height, Is.EqualTo(Mathf.Max(c.NeutralBoundsLocal.size.y,
                capsule.radius * 2f)).Within(1e-5f));
            AssertBoundsContains(c.AnimationBoundsLocal, c.BindBoundsLocal);
            AssertBoundsContains(c.AnimationBoundsLocal, c.NeutralBoundsLocal);
        }

        static void AssertBoundsContains(Bounds outer, Bounds inner)
        {
            const float tolerance = 1e-4f;
            Assert.GreaterOrEqual(inner.min.x, outer.min.x - tolerance);
            Assert.GreaterOrEqual(inner.min.y, outer.min.y - tolerance);
            Assert.GreaterOrEqual(inner.min.z, outer.min.z - tolerance);
            Assert.LessOrEqual(inner.max.x, outer.max.x + tolerance);
            Assert.LessOrEqual(inner.max.y, outer.max.y + tolerance);
            Assert.LessOrEqual(inner.max.z, outer.max.z + tolerance);
        }
    }
}
