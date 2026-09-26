using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using CritterCrafter.Editor;
using NUnit.Framework;
using Unity.Profiling;
using UnityEditor;
using UnityEngine;

namespace CritterCrafter.Tests
{
    /// <summary>
    /// Measurement report for per-part SkinnedMeshRenderers at 1/8/32 instances.
    /// Ignores when no library is built (same as <see cref="LibraryTests"/>). Batch
    /// counters are logged when ProfilerRecorder has samples; missing counters or a
    /// graphics failure do not fail. No millisecond budget. Does not enable
    /// GPU-batched skinning.
    /// </summary>
    public class BatchingMeasurementTests
    {
        static readonly int[] InstanceCounts = { 1, 8, 32 };
        const float GridSpacing = 4f;

        LibraryImporter.Report _report;
        CritterLibrary _approvedLibrary;
        CritterLibrary Lib => _approvedLibrary;
        readonly List<GameObject> _spawned = new List<GameObject>();

        [OneTimeSetUp]
        public void ImportLibrary()
        {
            var dir = LibraryTests.FindLibraryDir();
            if (dir == null) Assert.Ignore("no built critter library (run `critter library build`)");
            _report = LibraryImporter.Import(dir);
            _approvedLibrary = _report.Library.EditorCreateApprovedSkeletonClone();
        }

        [OneTimeTearDown]
        public void CleanupLibrary() { if (_approvedLibrary != null) UnityEngine.Object.DestroyImmediate(_approvedLibrary); }

        [TearDown]
        public void Cleanup()
        {
            foreach (var go in _spawned) if (go != null) UnityEngine.Object.DestroyImmediate(go);
            _spawned.Clear();
        }

        AssembledCreature Assemble(CritterRecipe recipe, AssemblyOptions options)
        {
            var c = CreatureAssembler.Assemble(Lib, recipe, options);
            _spawned.Add(c.gameObject);
            return c;
        }

        [Test]
        public void ReportsOneCreaturePerFamilyAtSeed1()
        {
            var lines = new StringBuilder();
            lines.AppendLine("family\tskeleton\tsmrs\ttriangles\tbones\tmaterials");
            var families = Lib.Catalog.skeletons
                .Where(s => s.skeleton_id.EndsWith("_v3"))
                .Select(s => s.family)
                .Distinct()
                .OrderBy(f => f);
            foreach (var family in families)
            {
                AssembledCreature c;
                if (Lib.Catalog.FindPool(family) != null)
                    c = Assemble(Lib.Generate(family, 1), AssemblyOptions.Default);
                else
                {
                    var skeleton = PickBalanced(family);
                    Assert.IsNotNull(skeleton, family);
                    c = Assemble(LocomotionCapture.ReferenceRecipe(Lib.Catalog, skeleton), AssemblyOptions.Review);
                }
                Assert.IsFalse(c.IsFallback, family + ": " + string.Join(";", c.Diagnostics));
                int smrs = c.Renderers.Count;
                int materials = UniqueMaterials(new[] { c });
                lines.AppendLine($"{family}\t{c.Skeleton.skeleton_id}\t{smrs}\t{c.Triangles}\t{c.Skeleton.bones.Length}\t{materials}");
                Assert.Greater(smrs, 0, family);
                Assert.Greater(c.Triangles, 0, family);
            }
            LogReport("per-family seed 1", lines.ToString());
        }

        [Test]
        public void ReportsSkinnedRendererCountsAtOneEightAndThirtyTwoInstances()
        {
            var recipe = Lib.Generate("any", 1);
            var creatures = new List<AssembledCreature>(32);
            for (int i = 0; i < 32; i++)
            {
                var c = Assemble(recipe, AssemblyOptions.Default);
                Assert.IsFalse(c.IsFallback, c.Diagnostics.Count > 0 ? string.Join(";", c.Diagnostics) : "fallback");
                c.transform.position = new Vector3((i % 8) * GridSpacing, 0f, (i / 8) * GridSpacing);
                creatures.Add(c);
            }

            int smr1 = creatures[0].Renderers.Count;
            int tri1 = creatures[0].Triangles;
            Assert.Greater(smr1, 0);
            Assert.Greater(tri1, 0);

            var rows = new List<(int n, int smrs, int tris, int bones, int materials)>();
            foreach (int n in InstanceCounts)
            {
                for (int i = 0; i < creatures.Count; i++)
                    creatures[i].gameObject.SetActive(i < n);
                var slice = creatures.GetRange(0, n);
                int smrs = slice.Sum(c => c.Renderers.Count);
                int tris = slice.Sum(c => c.Triangles);
                Assert.AreEqual(n * smr1, smrs, "SMR count must scale with instance count at n=" + n);
                Assert.AreEqual(n * tri1, tris, "triangle count must scale with instance count at n=" + n);
                rows.Add((n, smrs, tris, slice.Sum(c => c.Skeleton.bones.Length), UniqueMaterials(slice)));
            }

            var batchByN = new Dictionary<int, string>();
            var cam = TryMakeFixedIsoCamera();
            foreach (int n in InstanceCounts)
            {
                for (int i = 0; i < creatures.Count; i++)
                    creatures[i].gameObject.SetActive(i < n);
                batchByN[n] = cam != null
                    ? TryReadBatches(cam)
                    : "unavailable (no camera)";
            }

            var lines = new StringBuilder();
            lines.AppendLine("skeleton=" + recipe.skeleton_id
                + " meshDeformation=" + PlayerSettings.meshDeformation);
            lines.AppendLine("instances\tsmrs\ttriangles\tbones\tmaterials\tbatches");
            foreach (var row in rows)
                lines.AppendLine($"{row.n}\t{row.smrs}\t{row.tris}\t{row.bones}\t{row.materials}\t{batchByN[row.n]}");
            LogReport("any/seed 1 at 1/8/32", lines.ToString());
        }

        SkeletonData PickBalanced(string family)
        {
            var all = Lib.Catalog.skeletons.Where(s => s.family == family && s.skeleton_id.EndsWith("_v3")).ToList();
            return all.FirstOrDefault(s => s.skeleton_id.Contains("_balanced_")) ?? all.FirstOrDefault();
        }

        static int UniqueMaterials(IEnumerable<AssembledCreature> creatures)
        {
            var mats = new HashSet<Material>();
            foreach (var c in creatures)
                foreach (var pr in c.Renderers)
                    if (pr.renderer != null)
                        foreach (var m in pr.renderer.sharedMaterials)
                            if (m != null) mats.Add(m);
            return mats.Count;
        }

        Camera TryMakeFixedIsoCamera()
        {
            try
            {
                var go = new GameObject("BatchingMeasureCamera");
                _spawned.Add(go);
                var cam = go.AddComponent<Camera>();
                cam.orthographic = true;
                cam.clearFlags = CameraClearFlags.SolidColor;
                cam.backgroundColor = new Color(0.12f, 0.13f, 0.16f);
                cam.farClipPlane = 200f;
                cam.enabled = false;
                var target = new Vector3(3.5f * GridSpacing, 1f, 1.5f * GridSpacing);
                cam.transform.position = target + new Vector3(16f, 18f, 16f).normalized * 40f;
                cam.transform.LookAt(target);
                cam.orthographicSize = 20f;
                return cam;
            }
            catch (Exception e)
            {
                Debug.Log("[CritterCrafter] batching camera unavailable: " + e.GetType().Name);
                return null;
            }
        }

        /// <summary>
        /// Optional batch column. Invalid recorder or zero samples → unavailable.
        /// A graphics throw is also unavailable; it must not fail the test.
        /// </summary>
        static string TryReadBatches(Camera cam)
        {
            try
            {
                using (var batches = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Batches Count"))
                using (var draws = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Draw Calls Count"))
                {
                    cam.Render();
                    bool hasB = HasSample(batches);
                    bool hasD = HasSample(draws);
                    if (!hasB && !hasD) return "unavailable (no ProfilerRecorder samples)";
                    return "batches=" + (hasB ? batches.LastValue.ToString() : "n/a")
                        + " drawCalls=" + (hasD ? draws.LastValue.ToString() : "n/a")
                        + " source=ProfilerRecorder";
                }
            }
            catch (Exception e)
            {
                return "unavailable (" + e.GetType().Name + ")";
            }
        }

        static bool HasSample(ProfilerRecorder rec) => rec.Valid && rec.Count > 0;

        static void LogReport(string title, string body)
        {
            var text = "[CritterCrafter] batching report (" + title + ")\n" + body.TrimEnd();
            Debug.Log(text);
            TestContext.WriteLine(text);
        }
    }
}
