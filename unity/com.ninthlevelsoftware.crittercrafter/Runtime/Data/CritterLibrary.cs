using System;
using UnityEngine;

namespace CritterCrafter
{
    /// <summary>
    /// An imported critter-crafter library: the compiled catalog plus references to the imported
    /// skeleton models (with generated AnimatorControllers) and part models/materials.
    /// Created by Tools/Critter Crafter/Import Library.
    /// </summary>
    [CreateAssetMenu(menuName = "Critter Crafter/Library (empty)", fileName = "CritterLibrary")]
    public class CritterLibrary : ScriptableObject
    {
        [Serializable]
        public class SkeletonEntry
        {
            public string skeletonId;
            public GameObject model;
            public RuntimeAnimatorController controller;
        }

        [Serializable]
        public class PartEntry
        {
            public string partId;
            public GameObject model;
            public Material material;
            public Material[] materials = Array.Empty<Material>();
        }

        [SerializeField] TextAsset catalogJson;
        [SerializeField] SkeletonEntry[] skeletons = Array.Empty<SkeletonEntry>();
        [SerializeField] PartEntry[] parts = Array.Empty<PartEntry>();
        [SerializeField] string sourceCatalogSha256;
        [SerializeField] string bakedLibraryId, bakedLibraryVersion, bakedSourceCatalogSha256;
        [SerializeField] ImportedBakedCreature[] bakedCreatures = Array.Empty<ImportedBakedCreature>();

        [NonSerialized] CatalogData _catalog;

        public CatalogData Catalog
        {
            get
            {
                if (_catalog == null && catalogJson != null) _catalog = JsonUtility.FromJson<CatalogData>(catalogJson.text);
                return _catalog;
            }
        }

        public string LibraryId => Catalog?.library_id;
        public string Version => Catalog?.version;
        public string SourceCatalogSha256 => sourceCatalogSha256;

        public ImportedBakedCreature FindBaked(CritterRecipe recipe, bool allowReview = false)
        {
            var diagnostics = RecipeValidator.Validate(Catalog, recipe, allowReview);
            if (diagnostics.Count > 0) throw new AssemblyException(string.Join("; ", diagnostics));
            return FindBakedValidated(recipe);
        }

        internal ImportedBakedCreature FindBakedValidated(CritterRecipe recipe)
        {
            if (bakedCreatures == null) throw new AssemblyException("CC_BAKE_INVALID: missing imported index");
            if (bakedCreatures.Length == 0) return null;
            string key = BakedCreatureIdentity.Key(recipe);
            ImportedBakedCreature found = null;
            foreach (var entry in bakedCreatures)
            {
                if (entry?.data == null) throw new AssemblyException("CC_BAKE_INVALID: null indexed entry");
                if (entry.data.key != entry.importedKey)
                    throw new AssemblyException("CC_BAKE_STALE: modified indexed key");
                if (entry.data.key != key) continue;
                if (found != null) throw new AssemblyException("CC_BAKE_INVALID: duplicate key " + key);
                found = entry;
            }
            if (found == null) return null;
            var data = found.data;
            if (bakedLibraryId != LibraryId || bakedLibraryVersion != Version
                || bakedSourceCatalogSha256 != sourceCatalogSha256 || found.sourceCatalogSha256 != sourceCatalogSha256
                || !BakedCreatureIdentity.IsHash(sourceCatalogSha256)
                || data.skeleton_id != recipe.skeleton_id || data.composition != recipe.Canonical()
                || data.source_sha256 != BakedCreatureIdentity.ManifestHash(data.inputs)
                || data.source_sha256 != found.importedSourceSha256 || data.producer_sha256 != found.importedProducerSha256
                || data.asset_sha256 != found.importedAssetSha256 || data.texture_sha256 != found.importedTextureSha256
                || data.material_sha256 != found.importedMaterialSha256
                || !BakedCreatureIdentity.IsHash(data.source_sha256) || !BakedCreatureIdentity.IsHash(data.asset_sha256)
                || !BakedCreatureIdentity.IsHash(data.texture_sha256) || !BakedCreatureIdentity.IsHash(data.material_sha256)
                || !BakedCreatureIdentity.IsHash(data.producer_sha256)
                || found.model == null || found.material == null)
                throw new AssemblyException("CC_BAKE_STALE: indexed creature " + key);
            return found;
        }

        public SkeletonEntry FindSkeleton(string id) => Array.Find(skeletons, s => s.skeletonId == id);
        public PartEntry FindPart(string id) => Array.Find(parts, p => p.partId == id);

        public CritterRecipe Generate(string poolId, long seed) => RecipeGenerator.Generate(Catalog, poolId, seed);

#if UNITY_EDITOR
        public void EditorSetContents(TextAsset catalog, SkeletonEntry[] skeletonEntries, PartEntry[] partEntries,
            string originalCatalogSha256 = null, BakedCreatureIndex index = null, ImportedBakedCreature[] baked = null)
        {
            catalogJson = catalog;
            skeletons = skeletonEntries;
            parts = partEntries;
            _catalog = null;
            sourceCatalogSha256 = originalCatalogSha256 ?? (catalog != null ? BakedCreatureIdentity.Hash(catalog.bytes) : null);
            bakedLibraryId = index?.library_id;
            bakedLibraryVersion = index?.library_version;
            bakedSourceCatalogSha256 = index?.source_catalog_sha256;
            bakedCreatures = baked ?? Array.Empty<ImportedBakedCreature>();
        }

        /// <summary>
        /// Test/review helper. Approval is applied only to a cloned in-memory catalog; <paramref name="edit"/>
        /// may then damage that clone (tests use it to build a broken catalog over the real models).
        /// </summary>
        public CritterLibrary EditorCreateApprovedSkeletonClone(Action<CatalogData> edit = null) =>
            EditorCreateSkeletonStatusClone("approved", edit);

        /// <summary>
        /// Like <see cref="EditorCreateApprovedSkeletonClone"/> with every skeleton set to <paramref name="status"/>.
        /// Tests that mean "nothing is approved" use "draft" so they keep meaning that once the owner approves real
        /// content in the source data.
        /// </summary>
        public CritterLibrary EditorCreateSkeletonStatusClone(string status, Action<CatalogData> edit = null)
        {
            var cloneCatalog = JsonUtility.FromJson<CatalogData>(catalogJson.text);
            foreach (var skeleton in cloneCatalog.skeletons) skeleton.status = status;
            edit?.Invoke(cloneCatalog);
            // Not assets, so Unity would destroy them when a test enters Play Mode; the clone must outlive that.
            var clone = CreateInstance<CritterLibrary>();
            clone.hideFlags = HideFlags.HideAndDontSave;
            clone.catalogJson = new TextAsset(JsonUtility.ToJson(cloneCatalog)) { hideFlags = HideFlags.HideAndDontSave };
            clone.skeletons = skeletons;
            clone.parts = parts;
            clone.sourceCatalogSha256 = sourceCatalogSha256;
            clone.bakedLibraryId = bakedLibraryId;
            clone.bakedLibraryVersion = bakedLibraryVersion;
            clone.bakedSourceCatalogSha256 = bakedSourceCatalogSha256;
            clone.bakedCreatures = bakedCreatures;
            return clone;
        }
#endif
    }
}
