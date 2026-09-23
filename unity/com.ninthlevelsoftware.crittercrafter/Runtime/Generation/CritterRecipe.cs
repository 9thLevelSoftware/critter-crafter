using System;
using System.Text;

namespace CritterCrafter
{
    /// <summary>
    /// A creature recipe (schemas/recipe.v2.schema.json). Once saved it is authoritative: persist it
    /// (JsonUtility.ToJson) and never regenerate it from the seed on load.
    /// </summary>
    [Serializable]
    public class CritterRecipe
    {
        public string schema_version = "2.0.0";
        public string recipe_id;
        public string library_id;
        public string library_version;
        public string generator;
        public string pool_id;
        public long seed;
        public string skeleton_id;
        public RecipeFill[] fills = Array.Empty<RecipeFill>();

        /// <summary>Canonical form shared with the Python generator (docs/generator.md).</summary>
        public string Canonical()
        {
            var sb = new StringBuilder(skeleton_id).Append('|');
            for (int i = 0; i < fills.Length; i++)
            {
                if (i > 0) sb.Append(';');
                var f = fills[i];
                sb.Append(f.branch_id).Append('=').Append(f.part_id).Append('+')
                  .Append(string.IsNullOrEmpty(f.connector_part_id) ? "-" : f.connector_part_id);
            }
            return sb.ToString();
        }
    }

    [Serializable]
    public class RecipeFill
    {
        public string branch_id;
        public string part_id;
        public string connector_part_id;
    }
}
