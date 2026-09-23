using System;
using System.Collections.Generic;
using UnityEngine;

namespace CritterCrafter
{
    /// <summary>Root component of an assembled creature: recipe, animator, renderers, diagnostics.</summary>
    public class AssembledCreature : MonoBehaviour
    {
        [Serializable]
        public struct PartRenderer
        {
            public string branchId;
            public string partId;
            public bool connector;
            public SkinnedMeshRenderer renderer;
        }

        [SerializeField] string recipeJson;
        [SerializeField] Animator animator;
        [SerializeField] List<PartRenderer> renderers = new List<PartRenderer>();
        [SerializeField] Bounds bindBoundsLocal;

        public readonly List<string> Diagnostics = new List<string>();
        public int Triangles { get; internal set; }
        public Animator Animator => animator;
        public IReadOnlyList<PartRenderer> Renderers => renderers;
        public bool IsFallback => animator == null;
        /// <summary>Bind-pose bounds in the creature root's local space.</summary>
        public Bounds BindBoundsLocal => bindBoundsLocal;
        public SkeletonData Skeleton { get; private set; }

        /// <summary>The authoritative recipe (persist this, not the seed).</summary>
        public CritterRecipe Recipe => string.IsNullOrEmpty(recipeJson) ? null : JsonUtility.FromJson<CritterRecipe>(recipeJson);

        internal void Init(CritterRecipe recipe, Animator anim, SkeletonData skeleton)
        {
            recipeJson = recipe != null ? JsonUtility.ToJson(recipe) : "";
            animator = anim;
            Skeleton = skeleton;
            bindBoundsLocal = new Bounds(Vector3.up, Vector3.one);
        }

        internal void AddRenderer(SkinnedMeshRenderer smr, string branchId, string partId, bool connector)
        {
            renderers.Add(new PartRenderer { branchId = branchId, partId = partId, connector = connector, renderer = smr });
            var toLocal = transform.worldToLocalMatrix * smr.rootBone.localToWorldMatrix;
            var lb = smr.localBounds;
            var world = new Bounds(toLocal.MultiplyPoint3x4(lb.center), Vector3.zero);
            for (int i = 0; i < 8; i++)
            {
                var e = lb.extents;
                world.Encapsulate(toLocal.MultiplyPoint3x4(lb.center + new Vector3((i & 1) == 0 ? -e.x : e.x, (i & 2) == 0 ? -e.y : e.y, (i & 4) == 0 ? -e.z : e.z)));
            }
            if (renderers.Count == 1) bindBoundsLocal = world;
            else bindBoundsLocal.Encapsulate(world);
        }
    }
}
