using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace CritterCrafter.Editor
{
    /// <summary>
    /// Imports a built critter-crafter library (folder or release zip) into
    /// Assets/CritterLibraries/&lt;library_id&gt;/&lt;version&gt;/ and creates the CritterLibrary asset,
    /// materials (active pipeline's default lit shader) and one AnimatorController per skeleton.
    /// </summary>
    public static class LibraryImporter
    {
        public class Report
        {
            public CritterLibrary Library;
            public string AssetFolder;
            public readonly List<string> Problems = new List<string>();
        }

        [MenuItem("Tools/Critter Crafter/Import Library Folder...")]
        static void ImportFolderMenu()
        {
            var dir = EditorUtility.OpenFolderPanel("Critter library folder (contains catalog.json)", "", "");
            if (!string.IsNullOrEmpty(dir)) LogReport(Import(dir));
        }

        [MenuItem("Tools/Critter Crafter/Import Library Zip...")]
        static void ImportZipMenu()
        {
            var zip = EditorUtility.OpenFilePanel("Critter library release zip", "", "zip");
            if (string.IsNullOrEmpty(zip)) return;
            var tmp = Path.Combine(Path.GetTempPath(), "critter_import_" + Guid.NewGuid().ToString("N"));
            ZipFile.ExtractToDirectory(zip, tmp);
            try { LogReport(Import(tmp)); }
            finally { Directory.Delete(tmp, true); }
        }

        static void LogReport(Report r)
        {
            foreach (var p in r.Problems) Debug.LogWarning("[CritterCrafter] " + p);
            Debug.Log($"[CritterCrafter] imported {r.Library.LibraryId} v{r.Library.Version} into {r.AssetFolder} ({r.Problems.Count} problem(s))");
            Selection.activeObject = r.Library;
        }

        public static Report Import(string sourceDir)
        {
            var catalogPath = Path.Combine(sourceDir, "catalog.json");
            if (!File.Exists(catalogPath)) throw new FileNotFoundException("catalog.json not found", catalogPath);
            var catalog = JsonUtility.FromJson<CatalogData>(File.ReadAllText(catalogPath));
            if (catalog.document_kind != "critter_library" || catalog.frame != "gltf_rh_yup_zfwd_m")
                throw new InvalidDataException("not a critter library v2 catalog: " + catalogPath);

            var report = new Report();
            string folder = CritterModelPostprocessor.LibrariesRoot + catalog.library_id + "/" + catalog.version;
            report.AssetFolder = folder;
            string abs = Path.GetFullPath(folder);
            if (Directory.Exists(abs)) AssetDatabase.DeleteAsset(folder);
            Directory.CreateDirectory(abs);

            // Copy catalog + FBX models only (GLBs are for Blender/previews; skipping them avoids double
            // import when a glTF importer package is installed).
            File.Copy(catalogPath, Path.Combine(abs, "catalog.json"));
            foreach (var file in Directory.GetFiles(sourceDir, "*.fbx", SearchOption.AllDirectories))
            {
                var rel = Path.GetRelativePath(sourceDir, file);
                var dst = Path.Combine(abs, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(dst));
                File.Copy(file, dst);
            }
            Directory.CreateDirectory(Path.Combine(abs, "Materials"));
            Directory.CreateDirectory(Path.Combine(abs, "Controllers"));
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            var shader = DefaultLitShader();
            var parts = new List<CritterLibrary.PartEntry>();
            foreach (var p in catalog.parts)
            {
                if (string.IsNullOrEmpty(p.asset?.fbx)) { report.Problems.Add("part without asset: " + p.part_id); continue; }
                var model = AssetDatabase.LoadAssetAtPath<GameObject>(folder + "/" + p.asset.fbx);
                if (model == null) { report.Problems.Add("model failed to import: " + p.asset.fbx); continue; }
                var mat = new Material(shader) { name = "M_" + p.part_id };
                if (ColorUtility.TryParseHtmlString(p.albedo, out var col))
                {
                    if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", col);
                    if (mat.HasProperty("_Color")) mat.SetColor("_Color", col);
                }
                if (mat.HasProperty("_Smoothness")) mat.SetFloat("_Smoothness", 0.25f);
                if (mat.HasProperty("_Glossiness")) mat.SetFloat("_Glossiness", 0.25f);
                AssetDatabase.CreateAsset(mat, $"{folder}/Materials/M_{p.part_id}.mat");
                parts.Add(new CritterLibrary.PartEntry { partId = p.part_id, model = model, material = mat });
                var smr = model.GetComponentInChildren<SkinnedMeshRenderer>(true);
                if (smr == null) report.Problems.Add("part has no SkinnedMeshRenderer: " + p.part_id);
                else if (CreatureAssembler.TriangleCount(smr.sharedMesh) != p.asset.triangles)
                    report.Problems.Add($"triangle mismatch {p.part_id}: unity {CreatureAssembler.TriangleCount(smr.sharedMesh)} catalog {p.asset.triangles}");
            }

            var skeletons = new List<CritterLibrary.SkeletonEntry>();
            foreach (var s in catalog.skeletons)
            {
                string path = folder + "/" + s.asset.fbx;
                var model = AssetDatabase.LoadAssetAtPath<GameObject>(path);
                if (model == null) { report.Problems.Add("skeleton failed to import: " + path); continue; }
                var clips = AnimatorBuilder.LoadClips(path);
                foreach (var c in s.asset.clips)
                    if (!clips.ContainsKey(c.name)) report.Problems.Add($"skeleton {s.skeleton_id} missing clip {c.name}");
                var ctrl = AnimatorBuilder.Build($"{folder}/Controllers/{s.skeleton_id}.controller", clips);
                skeletons.Add(new CritterLibrary.SkeletonEntry { skeletonId = s.skeleton_id, model = model, controller = ctrl });
                float err = FrameProbe.MaxSnapError(model, s, out var worst);
                if (err > FrameProbe.Tolerance) report.Problems.Add($"frame probe {s.skeleton_id}: {err:F5} m at {worst}");
            }

            var lib = ScriptableObject.CreateInstance<CritterLibrary>();
            lib.EditorSetContents(AssetDatabase.LoadAssetAtPath<TextAsset>(folder + "/catalog.json"), skeletons.ToArray(), parts.ToArray());
            AssetDatabase.CreateAsset(lib, $"{folder}/{catalog.library_id}.asset");
            AssetDatabase.SaveAssets();
            report.Library = lib;
            return report;
        }

        public static Shader DefaultLitShader()
        {
            var rp = GraphicsSettings.defaultRenderPipeline;
            if (rp != null && rp.defaultMaterial != null) return rp.defaultMaterial.shader;
            return Shader.Find("Standard");
        }
    }

    /// <summary>Checks the catalog->Unity frame conversion against the imported skeleton (docs/frame.md).</summary>
    public static class FrameProbe
    {
        public const float Tolerance = 1e-4f;

        public static float MaxSnapError(GameObject skeletonModel, SkeletonData skeleton, out string worst)
        {
            var bones = new Dictionary<string, Transform>();
            foreach (var t in skeletonModel.GetComponentsInChildren<Transform>(true)) bones[t.name] = t;
            float max = 0f;
            worst = "";
            foreach (var b in skeleton.branches)
            {
                if (!bones.TryGetValue(b.bone_names[0], out var bone)) { worst = "missing " + b.bone_names[0]; return float.PositiveInfinity; }
                var local = CatalogFramePoint(skeletonModel.transform, bone.position);
                float e = Vector3.Distance(local, CritterFrame.Position(b.snap.position_m));
                if (e > max) { max = e; worst = b.branch_id; }
            }
            return max;
        }

        /// <summary>A world point in the catalog frame = the model root's parent space (docs/frame.md).</summary>
        public static Vector3 CatalogFramePoint(Transform modelRoot, Vector3 world)
        {
            var toParent = Matrix4x4.TRS(modelRoot.localPosition, modelRoot.localRotation, modelRoot.localScale) * modelRoot.worldToLocalMatrix;
            return toParent.MultiplyPoint3x4(world);
        }
    }
}
