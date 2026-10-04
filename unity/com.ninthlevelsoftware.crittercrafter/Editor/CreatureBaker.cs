using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace CritterCrafter.Editor
{
    public static class CreatureBaker
    {
        public static string ConfinedPath(string root, string relative)
        {
            if (string.IsNullOrEmpty(relative) || relative.Contains('\\') || relative.Contains(':') || relative.Contains('\0')
                || relative.IndexOfAny(new[] { '<', '>', '"', '|', '?', '*' }) >= 0 || relative.Any(c => c < 32)
                || Path.IsPathRooted(relative) || relative.Split('/').Any(p => p == "" || p == "." || p == ".."
                    || p.EndsWith(".", StringComparison.Ordinal) || p.EndsWith(" ", StringComparison.Ordinal)))
                throw Invalid("nonportable or escaping path: " + relative);
            var fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var full = Path.GetFullPath(Path.Combine(fullRoot, relative));
            var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            if (!full.StartsWith(fullRoot + Path.DirectorySeparatorChar, comparison)) throw Invalid("escaping path: " + relative);
            for (var current = full; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
                if ((File.Exists(current) || Directory.Exists(current))
                    && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw Invalid("linked path: " + relative);
            return full;
        }

        static InvalidDataException Invalid(string message) => new InvalidDataException("CC_BAKE_INVALID: " + message);

        public static BakedCreatureIndex ValidateIndex(string sourceDir, CatalogData catalog)
        {
            string path = ConfinedPath(sourceDir, "baked_creatures/index.json");
            if (!File.Exists(path)) return null;
            BakedCreatureIndex index;
            try { index = JsonUtility.FromJson<BakedCreatureIndex>(File.ReadAllText(path)); }
            catch (Exception e) { throw Invalid("cannot parse index: " + e.Message); }
            string catalogHash = BakedCreatureIdentity.Hash(File.ReadAllBytes(ConfinedPath(sourceDir, "catalog.json")));
            if (index == null || index.schema_version != "baked-creatures-1" || index.library_id != catalog.library_id
                || index.library_version != catalog.version || index.source_catalog_sha256 != catalogHash || index.entries == null)
                throw Invalid("index schema/library/catalog identity");
            var keys = new HashSet<string>(StringComparer.Ordinal);
            var outputs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string previous = null;
            foreach (var entry in index.entries)
            {
                if (entry == null || !BakedCreatureIdentity.IsHash(entry.key) || !keys.Add(entry.key)
                    || previous != null && string.CompareOrdinal(previous, entry.key) >= 0)
                    throw Invalid("duplicate, invalid or unsorted key");
                previous = entry.key;
                if (catalog.FindSkeleton(entry.skeleton_id) == null || string.IsNullOrEmpty(entry.composition)
                    || !entry.composition.StartsWith(entry.skeleton_id + "|", StringComparison.Ordinal)
                    || entry.key != BakedCreatureIdentity.Hash(System.Text.Encoding.UTF8.GetBytes(
                        index.library_id + "\n" + index.library_version + "\n" + entry.composition)))
                    throw Invalid("composition identity " + entry.key);
                string prefix = "baked_creatures/" + entry.key + "/";
                if (entry.fbx != prefix + entry.key + ".fbx" || entry.albedo_png != prefix + entry.key + "_albedo.png"
                    || !outputs.Add(entry.fbx) || !outputs.Add(entry.albedo_png)) throw Invalid("output path identity");
                CheckHash(sourceDir, entry.fbx, entry.asset_sha256);
                CheckHash(sourceDir, entry.albedo_png, entry.texture_sha256);
                if (!BakedCreatureIdentity.IsHash(entry.producer_sha256) || !BakedCreatureIdentity.IsHash(entry.material_sha256)
                    || entry.inputs == null || entry.inputs.Length == 0) throw Invalid("missing producer/material/inputs");
                var inputs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                bool includesCatalog = false, includesMaterial = false;
                foreach (var input in entry.inputs)
                {
                    if (input == null || !inputs.Add(input.path)) throw Invalid("duplicate input");
                    if (input.path == entry.fbx || input.path == entry.albedo_png) throw Invalid("output cannot be a source input");
                    CheckHash(sourceDir, input.path, input.sha256);
                    if (input.path == "catalog.json") includesCatalog = input.sha256 == catalogHash;
                    if (input.path == prefix + "material.json") includesMaterial = input.sha256 == entry.material_sha256;
                }
                if (!includesCatalog || !includesMaterial || entry.source_sha256 != BakedCreatureIdentity.ManifestHash(entry.inputs))
                    throw Invalid("source/material manifest " + entry.key);
            }
            return index;
        }

        static void CheckHash(string root, string relative, string expected)
        {
            var full = ConfinedPath(root, relative);
            if (!BakedCreatureIdentity.IsHash(expected) || !File.Exists(full)
                || BakedCreatureIdentity.Hash(File.ReadAllBytes(full)) != expected) throw Invalid("missing or stale artifact: " + relative);
        }

        public static void CopyAssets(string sourceDir, string destination, BakedCreatureIndex index)
        {
            if (index == null) return;
            var paths = new HashSet<string>(StringComparer.Ordinal);
            paths.Add("baked_creatures/index.json");
            foreach (var entry in index.entries)
            {
                paths.Add(entry.fbx);
                paths.Add(entry.albedo_png);
                paths.Add("baked_creatures/" + entry.key + "/material.json");
            }
            foreach (var relative in paths)
            {
                string target = ConfinedPath(destination, relative);
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                File.Copy(ConfinedPath(sourceDir, relative), target, true);
            }
        }

        public static ImportedBakedCreature[] ImportAssets(string assetFolder, BakedCreatureIndex index)
        {
            if (index == null) return Array.Empty<ImportedBakedCreature>();
            var entries = new List<ImportedBakedCreature>();
            foreach (var entry in index.entries)
            {
                string imagePath = assetFolder + "/" + entry.albedo_png;
                var textureImporter = AssetImporter.GetAtPath(imagePath) as TextureImporter;
                if (textureImporter == null) throw Invalid("atlas failed to import " + entry.key);
                textureImporter.maxTextureSize = 4096;
                textureImporter.textureCompression = TextureImporterCompression.Uncompressed;
                textureImporter.sRGBTexture = true;
                textureImporter.alphaSource = TextureImporterAlphaSource.FromInput;
                textureImporter.isReadable = true;
                textureImporter.wrapMode = TextureWrapMode.Clamp;
                textureImporter.SaveAndReimport();
                var image = AssetDatabase.LoadAssetAtPath<Texture2D>(imagePath);
                if (image == null || image.width != 4096 || image.height != 4096) throw Invalid("atlas must be 4096 squared");
                foreach (var pixel in image.GetPixels32())
                    if (pixel.a != 255) throw Invalid("atlas contains unsupported transparency " + entry.key);
                textureImporter.isReadable = false;
                textureImporter.SaveAndReimport();
                var model = AssetDatabase.LoadAssetAtPath<GameObject>(assetFolder + "/" + entry.fbx);
                if (model == null) throw Invalid("body failed to import " + entry.key);
                var renderers = model.GetComponentsInChildren<SkinnedMeshRenderer>(true);
                if (renderers.Length != 1 || model.GetComponentsInChildren<MeshRenderer>(true).Length != 0)
                    throw Invalid("expected exactly one skinned body " + entry.key);
                var body = renderers[0];
                var mesh = body.sharedMesh;
                if (mesh == null || mesh.subMeshCount != 1 || CreatureAssembler.TriangleCount(mesh) > 30000
                    || body.bones.Length == 0 || body.bones.Length > 120 || mesh.bindposes.Length != body.bones.Length
                    || body.bones.Any(b => b == null) || body.bones.Select(b => b.name).Distinct().Count() != body.bones.Length)
                    throw Invalid("mesh/bone contract " + entry.key);
                var weights = mesh.boneWeights;
                if (weights.Length != mesh.vertexCount) throw Invalid("missing weights " + entry.key);
                foreach (var weight in weights)
                {
                    ValidateWeight(weight.boneIndex0, weight.weight0, body.bones.Length);
                    ValidateWeight(weight.boneIndex1, weight.weight1, body.bones.Length);
                    ValidateWeight(weight.boneIndex2, weight.weight2, body.bones.Length);
                    ValidateWeight(weight.boneIndex3, weight.weight3, body.bones.Length);
                    if (Mathf.Abs(weight.weight0 + weight.weight1 + weight.weight2 + weight.weight3 - 1f) > 1e-6f)
                        throw Invalid("unnormalized weights " + entry.key);
                }
                var material = new Material(LibraryImporter.DefaultLitShader()) { name = "Baked_" + entry.key };
                if (material.HasProperty("_BaseMap")) material.SetTexture("_BaseMap", image);
                if (material.HasProperty("_MainTex")) material.SetTexture("_MainTex", image);
                if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", Color.white);
                if (material.HasProperty("_Color")) material.SetColor("_Color", Color.white);
                if (material.HasProperty("_Smoothness")) material.SetFloat("_Smoothness", .25f);
                if (material.HasProperty("_Glossiness")) material.SetFloat("_Glossiness", .25f);
                AssetDatabase.CreateAsset(material, assetFolder + "/Materials/" + material.name + ".mat");
                entries.Add(new ImportedBakedCreature { data = entry, model = model, material = material,
                    importedKey = entry.key, importedSourceSha256 = entry.source_sha256, importedProducerSha256 = entry.producer_sha256,
                    sourceCatalogSha256 = index.source_catalog_sha256, importedAssetSha256 = entry.asset_sha256,
                    importedTextureSha256 = entry.texture_sha256, importedMaterialSha256 = entry.material_sha256 });
            }
            return entries.ToArray();
        }

        static void ValidateWeight(int bone, float weight, int count)
        {
            if (float.IsNaN(weight) || float.IsInfinity(weight) || weight < 0
                || weight > 0 && (bone < 0 || bone >= count)) throw Invalid("invalid skin weights");
        }
    }
}
