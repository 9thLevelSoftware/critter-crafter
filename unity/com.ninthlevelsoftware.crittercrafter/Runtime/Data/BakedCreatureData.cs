using System;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace CritterCrafter
{
    [Serializable]
    public sealed class BakedCreatureInput
    {
        public string path;
        public string sha256;
    }

    [Serializable]
    public sealed class BakedCreatureData
    {
        public string key, skeleton_id, composition;
        public string source_sha256, producer_sha256, material_sha256, asset_sha256, texture_sha256;
        public string fbx, albedo_png;
        public BakedCreatureInput[] inputs;
    }

    [Serializable]
    public sealed class BakedCreatureIndex
    {
        public string schema_version, library_id, library_version, source_catalog_sha256;
        public BakedCreatureData[] entries;
    }

    [Serializable]
    public sealed class ImportedBakedCreature
    {
        public BakedCreatureData data;
        public GameObject model;
        public Material material;
        public string sourceCatalogSha256;
        public string importedKey, importedSourceSha256, importedProducerSha256;
        public string importedAssetSha256, importedTextureSha256, importedMaterialSha256;
    }

    public static class BakedCreatureIdentity
    {
        public static string Key(CritterRecipe recipe) => Hash(Encoding.UTF8.GetBytes(
            recipe.library_id + "\n" + recipe.library_version + "\n" + recipe.Canonical()));

        public static string Hash(byte[] bytes)
        {
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(bytes);
                var text = new StringBuilder(64);
                foreach (var b in digest) text.Append(b.ToString("x2"));
                return text.ToString();
            }
        }

        public static bool IsHash(string value) => value != null && value.Length == 64
            && value.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f');

        public static string ManifestHash(BakedCreatureInput[] inputs)
        {
            if (inputs == null) throw new AssemblyException("CC_BAKE_INVALID: missing inputs");
            var json = new StringBuilder("[");
            var ordered = inputs.OrderBy(i => i.path, StringComparer.Ordinal).ToArray();
            for (int i = 0; i < ordered.Length; i++)
            {
                if (i > 0) json.Append(',');
                json.Append("{\"path\":").Append(Quote(ordered[i].path))
                    .Append(",\"sha256\":").Append(Quote(ordered[i].sha256)).Append('}');
            }
            return Hash(Encoding.UTF8.GetBytes(json.Append(']').ToString()));
        }

        static string Quote(string text)
        {
            if (text == null) throw new AssemblyException("CC_BAKE_INVALID: null manifest value");
            var result = new StringBuilder("\"");
            foreach (char c in text)
            {
                switch (c)
                {
                    case '"': result.Append("\\\""); break;
                    case '\\': result.Append("\\\\"); break;
                    case '\b': result.Append("\\b"); break;
                    case '\f': result.Append("\\f"); break;
                    case '\n': result.Append("\\n"); break;
                    case '\r': result.Append("\\r"); break;
                    case '\t': result.Append("\\t"); break;
                    default:
                        if (c < 32) result.Append("\\u").Append(((int)c).ToString("x4"));
                        else result.Append(c);
                        break;
                }
            }
            return result.Append('"').ToString();
        }
    }
}
