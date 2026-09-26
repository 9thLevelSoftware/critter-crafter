using System;
using System.IO;
using System.Linq;
using System.Text;
using CritterCrafter.Editor;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace CritterCrafter.Tests
{
    /// <summary>
    /// Real-part albedo copy/bind. Uses LibraryTests.FindLibraryDir (CRITTER_LIBRARY_DIR or newest
    /// library/*/catalog.json). The bind test is ignored only when no library is built, same skip as LibraryTests.
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
            Assert.That(textured, Is.Not.Empty, "built library has no real-part albedo PNG");

            var report = LibraryImporter.Import(sourceDir);
            var lib = report.Library;
            Assert.IsNotNull(lib);
            string folder = CritterModelPostprocessor.LibrariesRoot + lib.LibraryId + "/" + lib.Version;

            foreach (var part in textured)
            {
                string rel = part.asset.albedo_png;
                Assert.That(IsRelativeWithoutDotDot(rel), Is.True, part.part_id + ": " + rel);
                var src = Path.GetFullPath(Path.Combine(sourceDir, rel));
                var dst = Path.GetFullPath(Path.Combine(Path.GetFullPath(folder), rel));
                Assert.That(Under(sourceDir, src), Is.True, part.part_id + ": " + rel);
                Assert.That(Under(folder, dst), Is.True, part.part_id + ": " + rel);
                Assert.That(File.Exists(dst), Is.True, part.part_id);
                Assert.That(File.ReadAllBytes(dst), Is.EqualTo(File.ReadAllBytes(src)), part.part_id);

                var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(folder + "/" + rel);
                Assert.IsNotNull(tex, part.part_id);
                Assert.That(Under(folder, AssetDatabase.GetAssetPath(tex)), Is.True, part.part_id);

                var entry = lib.FindPart(part.part_id);
                Assert.IsNotNull(entry?.material, part.part_id);
                AssertAlbedoMaterial(entry.material, tex, part.part_id);
            }
        }

        [Test]
        public void AlbedoPathThatEscapesTheLibraryIsRejected()
        {
            var tmp = Path.Combine(Path.GetTempPath(), "critter_albedo_probe_" + Guid.NewGuid().ToString("N"));
            var sourceDir = Path.Combine(tmp, "library");
            Directory.CreateDirectory(sourceDir);
            var baitBytes = Encoding.UTF8.GetBytes("critter-albedo-bait-" + Path.GetFileName(tmp));
            try
            {
                File.WriteAllText(Path.Combine(sourceDir, "catalog.json"), EscapeCatalogJson());
                File.WriteAllBytes(Path.Combine(tmp, "outside.png"), baitBytes);
                var report = LibraryImporter.Import(sourceDir);
                Assert.That(report.Problems,
                    Does.Contain("part texture path escapes the library escape_probe: ../outside.png"),
                    string.Join("\n", report.Problems));
                var versionFolder = Path.GetFullPath(ProbeFolder + "/" + RecipeGenerator.LibraryVersion);
                var leakedDest = Path.GetFullPath(Path.Combine(versionFolder, "../outside.png"));
                Assert.That(File.Exists(leakedDest), Is.False);
                if (Directory.Exists(versionFolder))
                {
                    foreach (var file in Directory.GetFiles(versionFolder, "*", SearchOption.AllDirectories))
                        Assert.That(File.ReadAllBytes(file), Is.Not.EqualTo(baitBytes), file);
                }
            }
            finally
            {
                if (Directory.Exists(tmp)) Directory.Delete(tmp, true);
            }
        }

        static void AssertAlbedoMaterial(Material mat, Texture2D tex, string partId)
        {
            bool bound = false;
            foreach (var name in new[] { "_BaseMap", "_MainTex" })
            {
                if (!mat.HasProperty(name)) continue;
                Assert.AreSame(tex, mat.GetTexture(name), partId + " " + name);
                bound = true;
            }
            Assert.IsTrue(bound, partId + ": shader exposes _BaseMap or _MainTex");

            bool smooth = false;
            foreach (var name in new[] { "_Smoothness", "_Glossiness" })
            {
                if (!mat.HasProperty(name)) continue;
                Assert.That(mat.GetFloat(name), Is.EqualTo(0.25f).Within(1e-4f), partId + " " + name);
                smooth = true;
            }
            Assert.IsTrue(smooth, partId + ": shader exposes _Smoothness or _Glossiness");

            foreach (var name in new[] { "_BaseColor", "_Color" })
            {
                if (!mat.HasProperty(name)) continue;
                Assert.AreEqual(Color.white, mat.GetColor(name), partId + " " + name);
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

        static bool IsRelativeWithoutDotDot(string rel)
        {
            if (string.IsNullOrEmpty(rel) || Path.IsPathRooted(rel)) return false;
            return rel.Replace('\\', '/').Split('/').All(segment => segment != "..");
        }

        static bool Under(string root, string path)
        {
            var prefix = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                         + Path.DirectorySeparatorChar;
            var full = Path.GetFullPath(path);
            var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            return full.StartsWith(prefix, comparison);
        }
    }
}
