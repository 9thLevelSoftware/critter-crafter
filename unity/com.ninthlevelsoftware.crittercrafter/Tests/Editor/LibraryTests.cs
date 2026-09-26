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

        internal static string FindLibraryDir()
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
            var c = CreatureAssembler.Assemble(Lib, LocomotionCapture.ReferenceRecipe(Lib.Catalog, skeleton), AssemblyOptions.Review);
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

        static readonly string[] OnePerFamily =
        {
            "hexapod_compact_insect_balanced_v3", "quadruped_stocky_plantigrade_balanced_v3",
            "quadruped_lean_digitigrade_balanced_v3", "biped_plantigrade_humanoid_balanced_v3",
            "crawler_bilateral_eight_legged_balanced_v3", "crawler_alien_tripod_balanced_v3",
            "radial_raised_articulated_walker_balanced_v3", "serpentine_segmented_paired_legs_balanced_v3",
            "dragger_forelimb_puller_balanced_v3",
        };

        [UnityTest]
        public IEnumerator RuntimeLegsPlantFeetAtGameSpeed()
        {
            // Real Play Mode (Animator + Animation Rigging + skinning exactly as in the game): drive one
            // skeleton per family like an agent at 2.5 m/s (or 90% of its published v_max if slower) over a
            // ground collider for 3 s. Planted feet must stay put, IK must reach and support must hold. The
            // first five frames (standing to full speed) are excluded from slip.
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
            yield return new EnterPlayMode();
            Time.captureFramerate = 30;
            var results = new List<(string id, LocomotionData block, LocomotionMetrics metrics, bool gait, bool loco)>();
            foreach (var id in OnePerFamily)
            {
                var holder = new GameObject("LocomotionTest");
                var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
                ground.transform.SetParent(holder.transform, false);
                ground.transform.localScale = Vector3.one * 10f;
                Physics.SyncTransforms();
                var c = LocomotionCapture.Spawn(Lib, id, holder.transform, out var restore);
                var gait = c.GetComponent<CritterCrafter.Locomotion.CreatureGait>();
                var block = c.Skeleton.locomotion;
                LocomotionMetrics metrics = null;
                bool sawLocomotionState = false;
                if (gait != null)
                {
                    float speed = Mathf.Min(2.5f, 0.9f * (float)block.v_max_mps);
                    var recorder = holder.AddComponent<LocomotionRecorder>();
                    recorder.Begin(gait, ReviewCourse.Straight(speed, 3f), new LocomotionMetrics { skeleton_id = id, speed_mps = speed, warmup_frames = 5 });
                    while (!recorder.Done)
                    {
                        yield return null;
                        sawLocomotionState |= c.Animator.GetCurrentAnimatorStateInfo(0).IsName("Locomotion");
                    }
                    metrics = recorder.Metrics;
                }
                results.Add((id, block, metrics, gait != null, sawLocomotionState));
                Object.Destroy(holder);
                restore();
                yield return null;
            }
            Time.captureFramerate = 0;
            yield return new ExitPlayMode();

            var fails = new List<string>();
            foreach (var (id, block, metrics, hasGait, loco) in results)
            {
                if (!hasGait) { fails.Add(id + ": runtime-leg skeletons get a CreatureGait"); continue; }
                if (!(metrics.max_ik_residual_m < 0.01f))
                    fails.Add($"{id}: IK residual {metrics.max_ik_residual_m}");
                // Dragging at game speed shows up as ~8 cm/frame; allow brief settling (under 2.5 cm in one
                // frame) when short, fast legs re-step at the edge of their reach.
                if (!(metrics.max_planted_slip_m < 0.025f))
                    fails.Add($"{id}: planted feet slide in the world {metrics.max_planted_slip_m}");
                if (block.min_support > 0 && metrics.min_planted_supports < block.min_support)
                    fails.Add($"{id}: support {metrics.min_planted_supports} < {block.min_support}");
                if (!(metrics.cadence_hz <= block.cadence_max_hz + 1e-4))
                    fails.Add($"{id}: cadence {metrics.cadence_hz}");
                if (metrics.overspeed) fails.Add(id + ": overspeed");
                if (!loco) fails.Add(id + ": moving plays the overlay");
            }
            Assert.That(fails, Is.Empty, string.Join("\n", fails));
        }

        [UnityTest]
        public IEnumerator RuntimeInstantTurnSettlesWithoutShuffle()
        {
            // Two-bone quadruped trot: instant 180 while moving. Snap/replant may pop; the
            // window runs from the heading change through yaw ease. Planted supports must not
            // all lift, and planted slip stays under 0.025 m.
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
            yield return new EnterPlayMode();
            Time.captureFramerate = 30;
            const string id = "quadruped_stocky_plantigrade_balanced_v3";
            var holder = new GameObject("InstantTurnTest");
            var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
            ground.transform.SetParent(holder.transform, false);
            ground.transform.localScale = Vector3.one * 10f;
            Physics.SyncTransforms();
            var c = LocomotionCapture.Spawn(Lib, id, holder.transform, out var restore);
            var gait = c.GetComponent<CritterCrafter.Locomotion.CreatureGait>();
            Assert.IsNotNull(gait, id + ": runtime-leg skeletons get a CreatureGait");
            float speed = Mathf.Min(2.5f, 0.9f * (float)gait.Block.v_max_mps);
            var recorder = holder.AddComponent<LocomotionRecorder>();
            recorder.Begin(gait, ReviewCourse.Turn180(speed), new LocomotionMetrics { skeleton_id = id, speed_mps = speed, warmup_frames = 5 });
            while (!recorder.Done) yield return null;
            var metrics = recorder.Metrics;
            int groups = 0;
            var seen = new HashSet<int>();
            foreach (var leg in gait.Legs)
                if (seen.Add(leg.group)) groups++;
            Object.Destroy(holder);
            restore();
            yield return null;
            Time.captureFramerate = 0;
            yield return new ExitPlayMode();

            Assert.Greater(metrics.instant_turns, 0, id + ": path must include an instant 180");
            Assert.Greater(metrics.turn_window_frames, 0, id + ": turn window never opened");
            Assert.IsTrue(metrics.yaw_eased, id + ": yaw lag never fell to 1 deg; a hold is not a settle");
            Assert.Greater(metrics.supports_planted_at_turn, 0, id + ": no supports were planted at the heading change");
            Assert.Less(metrics.supports_lifted_in_turn, metrics.supports_planted_at_turn,
                id + ": every support planted at the heading change lifted during the ease");
            Assert.LessOrEqual(metrics.steps_in_turn_window, groups, id + ": turn-window group steps exceed phase groups");
            Assert.Less(metrics.max_turn_slip_m, 0.025f, id + ": planted feet slide in the turn window");
        }

        [UnityTest]
        public IEnumerator RuntimeAttackReleasesIkAndKeepsSupport()
        {
            // Walk, then telegraph→attack: the attack-branch IK weight drops within 0.2 s;
            // other support feet stay at weight 1 and planted.
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
            yield return new EnterPlayMode();
            Time.captureFramerate = 30;
            var ids = new[] { "hexapod_compact_insect_balanced_v3", "quadruped_stocky_plantigrade_balanced_v3" };
            var results = new List<(string id, bool attacked, bool sawState, bool hasAttackLeg,
                float attackAfter02, float minOtherWeight, LocomotionMetrics metrics)>();
            foreach (var id in ids)
            {
                var holder = new GameObject("AttackPlantTest");
                var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
                ground.transform.SetParent(holder.transform, false);
                ground.transform.localScale = Vector3.one * 10f;
                Physics.SyncTransforms();
                var c = LocomotionCapture.Spawn(Lib, id, holder.transform, out var restore);
                var gait = c.GetComponent<CritterCrafter.Locomotion.CreatureGait>();
                Assert.IsNotNull(gait, id + ": runtime-leg skeletons get a CreatureGait");
                var motion = c.GetComponent<CreatureMotion>();
                if (motion == null) motion = c.gameObject.AddComponent<CreatureMotion>();
                float speed = (float)gait.Block.v_walk_mps;
                bool telegraphed = false, attacked = false, sawAttackState = false, hasAttackLeg = false, sampled02 = false;
                float telegraphAt = -1f, attackAt = -1f, attackAt02 = 1f, minOtherWeight = 1f;
                var recorder = holder.AddComponent<LocomotionRecorder>();
                recorder.BeforePlace = t =>
                {
                    if (!telegraphed && t >= 1f)
                    {
                        motion.SetState(CreatureState.Telegraph);
                        telegraphed = true;
                        telegraphAt = t;
                    }
                    else if (telegraphed && !attacked && t >= telegraphAt + 2f / 30f)
                    {
                        motion.PlayAttack();
                        attacked = true;
                        attackAt = t;
                    }
                    if (!attacked) return;
                    foreach (var leg in gait.Legs)
                    {
                        if (leg.attack) hasAttackLeg = true;
                        else minOtherWeight = Mathf.Min(minOtherWeight, leg.weight);
                    }
                    // Snapshot at 0.2 s after attack start — a later drop must not pass.
                    if (!sampled02 && t >= attackAt + 0.2f)
                    {
                        sampled02 = true;
                        attackAt02 = 1f;
                        foreach (var leg in gait.Legs)
                            if (leg.attack) attackAt02 = Mathf.Min(attackAt02, leg.weight);
                    }
                };
                recorder.Begin(gait, ReviewCourse.Straight(speed, 2.2f),
                    new LocomotionMetrics { skeleton_id = id, speed_mps = speed, warmup_frames = 5 });
                while (!recorder.Done)
                {
                    yield return null;
                    var info = c.Animator.GetCurrentAnimatorStateInfo(0);
                    sawAttackState |= info.IsName("Telegraph") || info.IsName("Attack");
                }
                results.Add((id, attacked, sawAttackState, hasAttackLeg, attackAt02, minOtherWeight, recorder.Metrics));
                Object.Destroy(holder);
                restore();
                yield return null;
            }
            Time.captureFramerate = 0;
            yield return new ExitPlayMode();

            Assert.IsTrue(results.Exists(r => r.hasAttackLeg), "need a creature whose attack_branch_id is a gait leg");
            foreach (var (id, attacked, sawState, hasAttackLeg, attackAt02, minOtherWeight, metrics) in results)
            {
                Assert.IsTrue(attacked, id + ": attack must play");
                Assert.IsTrue(sawState, id + ": Animator should be in Telegraph or Attack");
                Assert.Greater(minOtherWeight, 0.999f, id + ": non-attack feet should stay at IK weight 1");
                Assert.Less(metrics.max_planted_slip_m, 0.025f, id + ": support feet slide during attack");
                if (hasAttackLeg)
                    Assert.Less(attackAt02, 0.01f, id + ": attack-leg IK weight should drop within 0.2 s of attack start");
            }
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
