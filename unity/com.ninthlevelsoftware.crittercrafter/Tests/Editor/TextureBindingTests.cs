using System;
using System.IO;
using System.Linq;
using CritterCrafter.Editor;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace CritterCrafter.Tests
{
    /// <summary>
    /// Real-part albedo copy/bind. Uses LibraryTests.FindLibraryDir (CRITTER_LIBRARY_DIR or newest
    /// library/*/catalog.json). The bind test is ignored when no library is built, same skip as LibraryTests.
    /// </summary>
    public class TextureBindingTests
    {
        const string ProbeLibraryId = "albedo_path_probe";
        const string ProbeFolder = CritterModelPostprocessor.LibrariesRoot + ProbeLibraryId;

        [TearDown]
        public void CleanupProbe()
        {
            if (AssetDatabase.IsValidFolder(ProbeFolder)) AssetDatabase.DeleteAsset(ProbeFolder);
        }

        [Test]
        public void RealPartAlbedoBindsToBaseMapAndMainTex()
        {
            var sourceDir = LibraryTests.FindLibraryDir();
            if (sourceDir == null) Assert.Ignore("no built critter library (run `critter library build`)");

            var catalog = JsonUtility.FromJson<CatalogData>(File.ReadAllText(Path.Combine(sourceDir, "catalog.json")));
            var textured = (catalog.parts ?? Array.Empty<PartData>())
                .Where(p => p.asset != null && !string.IsNullOrEmpty(p.asset.albedo_png)).ToArray();
            if (textured.Length == 0) Assert.Ignore("built library has no real-part albedo PNG");

            string folder = CritterModelPostprocessor.LibrariesRoot + catalog.library_id + "/" + catalog.version;
            var lib = AssetDatabase.LoadAssetAtPath<CritterLibrary>($"{folder}/{catalog.library_id}.asset");
            if (lib == null) lib = LibraryImporter.Import(sourceDir).Library;
            folder = CritterModelPostprocessor.LibrariesRoot + lib.LibraryId + "/" + lib.Version;

            foreach (var part in textured)
            {
                string rel = part.asset.albedo_png;
                Assert.That(rel.Contains(".."), Is.False, part.part_id);
                var src = Path.GetFullPath(Path.Combine(sourceDir, rel));
                var dst = Path.GetFullPath(Path.Combine(Path.GetFullPath(folder), rel));
                Assert.That(Under(sourceDir, src), Is.True, part.part_id + ": " + rel);
                Assert.That(Under(folder, dst), Is.True, part.part_id + ": " + rel);
                Assert.That(File.Exists(dst), Is.True, part.part_id);

                var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(folder + "/" + rel);
                Assert.IsNotNull(tex, part.part_id);
                Assert.That(AssetDatabase.GetAssetPath(tex).Replace('\\', '/'),
                    Does.StartWith(folder.Replace('\\', '/') + "/"), part.part_id);

                var entry = lib.FindPart(part.part_id);
                Assert.IsNotNull(entry?.material, part.part_id);
                var mat = entry.material;
                bool bound = false;
                if (mat.HasProperty("_BaseMap"))
                {
                    Assert.AreSame(tex, mat.GetTexture("_BaseMap"), part.part_id + " _BaseMap");
                    bound = true;
                }
                if (mat.HasProperty("_MainTex"))
                {
                    Assert.AreSame(tex, mat.GetTexture("_MainTex"), part.part_id + " _MainTex");
                    bound = true;
                }
                Assert.IsTrue(bound, part.part_id + ": shader exposes _BaseMap or _MainTex");

                bool smooth = false;
                if (mat.HasProperty("_Smoothness"))
                {
                    Assert.That(mat.GetFloat("_Smoothness"), Is.EqualTo(0.25f).Within(1e-4f), part.part_id);
                    smooth = true;
                }
                if (mat.HasProperty("_Glossiness"))
                {
                    Assert.That(mat.GetFloat("_Glossiness"), Is.EqualTo(0.25f).Within(1e-4f), part.part_id);
                    smooth = true;
                }
                Assert.IsTrue(smooth, part.part_id + ": shader exposes _Smoothness or _Glossiness");
            }
        }

        [Test]
        public void AlbedoPathThatEscapesTheLibraryIsRejected()
        {
            var tmp = Path.Combine(Path.GetTempPath(), "critter_albedo_probe_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tmp);
            try
            {
                File.WriteAllText(Path.Combine(tmp, "catalog.json"), EscapeCatalogJson());
                File.WriteAllBytes(Path.GetFullPath(Path.Combine(tmp, "../outside.png")), new byte[] { 1, 2, 3, 4 });
                var report = LibraryImporter.Import(tmp);
                Assert.That(report.Problems,
                    Does.Contain("part texture path escapes the library escape_probe: ../outside.png"),
                    string.Join("\n", report.Problems));
                var leakedDest = Path.GetFullPath(Path.Combine(
                    Path.GetFullPath(ProbeFolder + "/" + RecipeGenerator.LibraryVersion), "../outside.png"));
                Assert.That(File.Exists(leakedDest), Is.False);
            }
            finally
            {
                var leaked = Path.GetFullPath(Path.Combine(tmp, "../outside.png"));
                if (File.Exists(leaked)) File.Delete(leaked);
                if (Directory.Exists(tmp)) Directory.Delete(tmp, true);
            }
        }

        static string EscapeCatalogJson() =>
            "{"
            + "\"schema_version\":\"" + RecipeGenerator.SchemaVersion + "\","
            + "\"document_kind\":\"critter_library\","
            + "\"library_id\":\"" + ProbeLibraryId + "\","
            + "\"version\":\"" + RecipeGenerator.LibraryVersion + "\","
            + "\"frame\":\"gltf_rh_yup_zfwd_m\","
            + "\"generator\":{\"algorithm\":\"" + RecipeGenerator.Algorithm + "\",\"rng\":\"splitmix64\"},"
            + "\"limits\":{\"max_triangles\":30000,\"max_bones\":120,\"max_parts\":16,\"max_influences\":4},"
            + "\"binding_profiles\":[],\"gait_profiles\":[],\"branch_templates\":[],\"skeletons\":[],\"pools\":[],"
            + "\"parts\":[{\"part_id\":\"escape_probe\",\"category\":\"limb\",\"status\":\"draft\","
            + "\"inventory_kind\":\"production\",\"asset\":{\"fbx\":\"parts/x.fbx\",\"albedo_png\":\"../outside.png\"}}]"
            + "}";

        static bool Under(string root, string fullPath)
        {
            var prefix = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                         + Path.DirectorySeparatorChar;
            var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            return fullPath.StartsWith(prefix, comparison);
        }
    }
}
