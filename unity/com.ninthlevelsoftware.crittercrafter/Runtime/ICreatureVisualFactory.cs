using UnityEngine;

namespace CritterCrafter
{
    /// <summary>What a game asks for when it needs a creature visual.</summary>
    public struct CreatureSpawnRequest
    {
        public string archetypeId;
        /// <summary>Saved recipe to rebuild exactly (takes precedence over pool + seed).</summary>
        public CritterRecipe recipe;
        public string poolId;
        public long seed;
        public Transform parent;
        public int layer;
    }

    /// <summary>
    /// Seam between a game and critter-crafter. Games implement this in their own assembly (e.g. to
    /// wrap the creature in the game's root/collider/layer conventions) and call CreatureAssembler.
    /// </summary>
    public interface ICreatureVisualFactory
    {
        GameObject Build(CreatureSpawnRequest request);
    }

    /// <summary>Reference factory: recipe (or pool + seed) -> assembled creature with a CreatureMotion driver.</summary>
    public class DefaultCreatureVisualFactory : ICreatureVisualFactory
    {
        readonly CritterLibrary _library;
        readonly AssemblyOptions _options;

        public DefaultCreatureVisualFactory(CritterLibrary library, AssemblyOptions options)
        {
            _library = library;
            _options = options;
        }

        public GameObject Build(CreatureSpawnRequest request)
        {
            var recipe = request.recipe ?? _library.Generate(string.IsNullOrEmpty(request.poolId) ? request.archetypeId : request.poolId, request.seed);
            var opts = _options;
            opts.parent = request.parent;
            opts.layer = request.layer;
            var creature = CreatureAssembler.Assemble(_library, recipe, opts);
            if (!creature.IsFallback) creature.gameObject.AddComponent<CreatureMotion>();
            return creature.gameObject;
        }
    }
}
