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

        /// <summary>
        /// With <see cref="AssemblyOptions.fallbackOnInvalid"/> (the default) this never throws: a failure to
        /// generate (nothing approved, unknown pool) or to assemble returns a grey stand-in whose
        /// <see cref="AssembledCreature.Diagnostics"/> start with the error code (CC_GEN_*, CC_ASSEMBLY_FAILED...).
        /// </summary>
        public GameObject Build(CreatureSpawnRequest request)
        {
            var opts = _options;
            opts.parent = request.parent;
            opts.layer = request.layer;
            var recipe = request.recipe;
            if (recipe == null)
            {
                try
                {
                    if (_library == null) throw new GenerationException("CC_NO_LIBRARY", "no critter library was given");
                    recipe = _library.Generate(string.IsNullOrEmpty(request.poolId) ? request.archetypeId : request.poolId, request.seed);
                }
                catch (System.Exception e)
                {
                    if (!opts.fallbackOnInvalid) throw;
                    return CreatureAssembler.CreateFallback(null, opts, new[] { e.Message }).gameObject;
                }
            }
            var creature = CreatureAssembler.Assemble(_library, recipe, opts);
            if (!creature.IsFallback) creature.gameObject.AddComponent<CreatureMotion>();
            return creature.gameObject;
        }
    }
}
