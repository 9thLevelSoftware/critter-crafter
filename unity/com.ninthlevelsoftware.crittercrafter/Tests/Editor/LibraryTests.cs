using System.Collections.Generic;
using System.IO;
using System.Linq;
using CritterCrafter.Editor;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace CritterCrafter.Tests
{
    /// <summary>
    /// Integration tests against a built library: CRITTER_LIBRARY_DIR, or the newest
    /// &lt;repo&gt;/library/*/catalog.json next to the TestProject. Ignored when no library is built.
    /// </summary>
    public class LibraryTests
    {
        LibraryImporter.Report _report;
        CritterLibrary Lib => _report.Library;
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
        }

        [TearDown]
        public void Cleanup()
        {
            foreach (var go in _spawned) if (go != null) Object.DestroyImmediate(go);
            _spawned.Clear();
        }

        AssembledCreature Spawn(string pool, long seed)
        {
            var c = CreatureAssembler.Assemble(Lib, Lib.Generate(pool, seed), AssemblyOptions.Default);
            _spawned.Add(c.gameObject);
            return c;
        }

        [Test]
        public void ImportReportsNoProblems() => CollectionAssert.IsEmpty(_report.Problems);

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
                foreach (var pr in c.Renderers)
                {
                    var branch = c.Skeleton.FindBranch(pr.branchId);
                    var part = Lib.Catalog.FindPart(pr.partId);
                    var entry = Lib.FindPart(pr.partId);
                    var src = entry.model.GetComponentInChildren<SkinnedMeshRenderer>(true);
                    float s = pr.connector ? 1f : (float)(branch.length_m / part.length_m);
                    var meshToPart = CreatureAssembler.MeshToPart(entry.model, src);
                    var expected = pr.renderer.transform.worldToLocalMatrix * c.transform.localToWorldMatrix
                                   * CritterFrame.Snap(branch.snap, s) * meshToPart;
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
            var pr = c.Renderers.First(r => r.connector && r.branchId.StartsWith("leg"));
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
            var c = Spawn("quadruped", 2);
            Assert.IsNotNull(c.Animator.runtimeAnimatorController);
            var leg = c.Animator.GetComponentsInChildren<Transform>().First(t => t.name == "leg_FL_b0");
            var rest = leg.localRotation;
            c.Animator.SetFloat(CreatureMotion.SpeedParam, AnimatorBuilder.WalkSpeed);
            for (int i = 0; i < 5; i++) c.Animator.Update(0.05f);
            Assert.Greater(Quaternion.Angle(rest, leg.localRotation), 1f, "walk clip should swing the leg");
        }
    }
}
