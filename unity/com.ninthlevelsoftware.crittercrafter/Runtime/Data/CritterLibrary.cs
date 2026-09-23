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
        }

        [SerializeField] TextAsset catalogJson;
        [SerializeField] SkeletonEntry[] skeletons = Array.Empty<SkeletonEntry>();
        [SerializeField] PartEntry[] parts = Array.Empty<PartEntry>();

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

        public SkeletonEntry FindSkeleton(string id) => Array.Find(skeletons, s => s.skeletonId == id);
        public PartEntry FindPart(string id) => Array.Find(parts, p => p.partId == id);

        public CritterRecipe Generate(string poolId, long seed) => RecipeGenerator.Generate(Catalog, poolId, seed);

#if UNITY_EDITOR
        public void EditorSetContents(TextAsset catalog, SkeletonEntry[] skeletonEntries, PartEntry[] partEntries)
        {
            catalogJson = catalog;
            skeletons = skeletonEntries;
            parts = partEntries;
            _catalog = null;
        }
#endif
    }
}
