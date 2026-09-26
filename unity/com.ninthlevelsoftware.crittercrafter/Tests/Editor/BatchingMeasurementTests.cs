using System.Collections.Generic;
using System.IO;
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
    /// counters are logged when ProfilerRecorder can see them without a GPU;
    /// missing counters do not fail. No millisecond budget. Does not enable
    /// GPU-batched skinning.
    /// </summary>
    public class BatchingMeasurementTests
    {
        static readonly int[] InstanceCounts = { 1, 8, 32 };

        LibraryImporter.Report _report;
        CritterLibrary _approvedLibrary;
        CritterLibrary Lib => _approvedLibrary;
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
            _approvedLibrary = _report.Library.EditorCreateApprovedSkeletonClone();
        }

        [OneTimeTearDown]
        public void CleanupLibrary() { if (_approvedLibrary != null) Object.DestroyImmediate(_approvedLibrary); }

        [TearDown]
        public void Cleanup()
        {
            foreach (var go in _spawned)
            {
                if (go == null) continue;
                var cam = go.GetComponent<Camera>();
                if (cam != null && cam.targetTexture != null)
                {
                    var rt = cam.targetTexture;
                    cam.targetTexture = null;
                    rt.Release();
                    Object.DestroyImmediate(rt);
                }
                Object.DestroyImmediate(go);
            }
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
                int smrs = CountSmrs(c);
                int materials = UniqueMaterials(new[] { c });
                lines.AppendLine($"{family}\t{c.Skeleton.skeleton_id}\t{smrs}\t{c.Triangles}\t{c.Skeleton.bones.Length}\t{materials}");
                Assert.Greater(smrs, 0, family);
                Assert.Greater(c.Triangles, 0, family);
                Assert.AreEqual(smrs, c.Renderers.Count, family);
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
                c.transform.position = new Vector3((i % 8) * 4f, 0f, (i / 8) * 4f);
                creatures.Add(c);
            }

            int smr1 = CountSmrs(creatures[0]);
            int tri1 = creatures[0].Triangles;
            Assert.Greater(smr1, 0);
            Assert.Greater(tri1, 0);

            var cam = MakeIsoCamera();
            var lines = new StringBuilder();
            lines.AppendLine("instances\tsmrs\ttriangles\tbones\tmaterials\tbatches");
            foreach (int n in InstanceCounts)
            {
                for (int i = 0; i < creatures.Count; i++)
                    creatures[i].gameObject.SetActive(i < n);
                var slice = creatures.GetRange(0, n);
                int smrs = slice.Sum(CountSmrs);
                int tris = slice.Sum(c => c.Triangles);
                int bones = slice.Sum(c => c.Skeleton.bones.Length);
                int materials = UniqueMaterials(slice);
                FitIsoCamera(cam, slice);
                string batches = RenderAndReadBatches(cam);
                lines.AppendLine($"{n}\t{smrs}\t{tris}\t{bones}\t{materials}\t{batches}");
                Assert.AreEqual(n * smr1, smrs, "SMR count must scale with instance count");
                Assert.AreEqual(n * tri1, tris, "triangle count must scale with instance count");
            }
            LogReport("any/seed 1 at 1/8/32", lines.ToString());
        }

        SkeletonData PickBalanced(string family)
        {
            var all = Lib.Catalog.skeletons.Where(s => s.family == family && s.skeleton_id.EndsWith("_v3")).ToList();
            return all.FirstOrDefault(s => s.skeleton_id.Contains("_balanced_")) ?? all.FirstOrDefault();
        }

        static int CountSmrs(AssembledCreature c) =>
            c.GetComponentsInChildren<SkinnedMeshRenderer>(true).Count(r => r.name != SkeletonRest.ProxyName);

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

        Camera MakeIsoCamera()
        {
            var rt = new RenderTexture(320, 320, 24, RenderTextureFormat.ARGB32);
            var go = new GameObject("BatchingMeasureCamera");
            _spawned.Add(go);
            var cam = go.AddComponent<Camera>();
            cam.orthographic = true;
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.12f, 0.13f, 0.16f);
            cam.targetTexture = rt;
            cam.farClipPlane = 200f;
            cam.enabled = false;
            var lightGo = new GameObject("BatchingMeasureLight");
            lightGo.transform.SetParent(go.transform, false);
            lightGo.transform.rotation = Quaternion.Euler(50f, -30f, 0f);
            var light = lightGo.AddComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 1.2f;
            return cam;
        }

        static void FitIsoCamera(Camera cam, List<AssembledCreature> creatures)
        {
            var b = WorldBounds(creatures[0]);
            for (int i = 1; i < creatures.Count; i++) b.Encapsulate(WorldBounds(creatures[i]));
            var target = b.center;
            cam.transform.position = target + new Vector3(16f, 18f, 16f).normalized * 20f;
            cam.transform.LookAt(target);
            cam.orthographicSize = Mathf.Max(2f, Mathf.Max(b.size.y, Mathf.Max(b.size.x, b.size.z)) * 0.7f);
        }

        static Bounds WorldBounds(AssembledCreature c)
        {
            var local = c.NeutralBoundsLocal;
            var world = new Bounds(c.transform.TransformPoint(local.center), Vector3.zero);
            var e = local.extents;
            for (int i = 0; i < 8; i++)
                world.Encapsulate(c.transform.TransformPoint(local.center + new Vector3(
                    (i & 1) == 0 ? -e.x : e.x, (i & 2) == 0 ? -e.y : e.y, (i & 4) == 0 ? -e.z : e.z)));
            return world;
        }

        /// <summary>
        /// CPU-side render counters. Headless/no-GPU editors often have no Batches Count
        /// marker; that is reported, not a failure.
        /// </summary>
        static string RenderAndReadBatches(Camera cam)
        {
            using (var batches = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Batches Count"))
            using (var draws = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Draw Calls Count"))
            {
                cam.Render();
                long b = ReadRecorder(batches);
                long d = ReadRecorder(draws);
                if (b >= 0 || d >= 0)
                    return $"batches={(b >= 0 ? b.ToString() : "n/a")} drawCalls={(d >= 0 ? d.ToString() : "n/a")} source=ProfilerRecorder";
            }
            return "unavailable (ProfilerRecorder has no Batches/Draw Calls counters without a GPU)";
        }

        static long ReadRecorder(ProfilerRecorder rec)
        {
            if (!rec.Valid) return -1;
            long v = rec.LastValue != 0 ? rec.LastValue : rec.CurrentValue;
            return v > 0 ? v : -1;
        }

        static void LogReport(string title, string body)
        {
            var text = "[CritterCrafter] batching report (" + title + ")\n" + body.TrimEnd();
            Debug.Log(text);
            TestContext.WriteLine(text);
        }
    }
}
