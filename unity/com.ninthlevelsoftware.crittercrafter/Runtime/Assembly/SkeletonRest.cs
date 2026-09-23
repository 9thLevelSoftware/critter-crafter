using System;
using System.Collections.Generic;
using UnityEngine;

namespace CritterCrafter
{
    /// <summary>
    /// True rest (bind) matrices of a skeleton model, read from its hidden "BindProxy" skinned mesh.
    /// The imported node pose of an animated FBX is NOT reliable (Blender's exporter writes node
    /// transforms from an animated state), so assembly never trusts it; it resets instances to these.
    /// </summary>
    public static class SkeletonRest
    {
        public const string ProxyName = "BindProxy";
        static readonly Dictionary<GameObject, Dictionary<string, Matrix4x4>> Cache = new Dictionary<GameObject, Dictionary<string, Matrix4x4>>();

        /// <summary>Bone name -> rest matrix in the catalog frame (the model root's parent space).</summary>
        public static Dictionary<string, Matrix4x4> Get(GameObject skeletonModel)
        {
            if (Cache.TryGetValue(skeletonModel, out var cached) && cached != null) return cached;
            var proxy = FindProxy(skeletonModel.transform)
                        ?? throw new AssemblyException($"skeleton model {skeletonModel.name} has no {ProxyName} mesh; rebuild the library");
            var meshToCatalog = CreatureAssembler.MeshToPart(skeletonModel, proxy);
            var bind = proxy.sharedMesh.bindposes;
            var bones = proxy.bones;
            var rest = new Dictionary<string, Matrix4x4>(bones.Length);
            for (int i = 0; i < bones.Length; i++) rest[bones[i].name] = meshToCatalog * bind[i].inverse;
            Cache[skeletonModel] = rest;
            return rest;
        }

        public static SkinnedMeshRenderer FindProxy(Transform root)
        {
            foreach (var smr in root.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                if (smr.name == ProxyName) return smr;
            return null;
        }

        /// <summary>Pose an instance's bones at rest. Transforms are visited parent-first.</summary>
        public static void ApplyBindPose(Transform instanceRoot, Dictionary<string, Matrix4x4> rest, Matrix4x4 catalogToWorld)
        {
            foreach (var t in instanceRoot.GetComponentsInChildren<Transform>(true))
            {
                if (!rest.TryGetValue(t.name, out var m)) continue;
                var world = catalogToWorld * m;
                t.SetPositionAndRotation(world.GetPosition(), world.rotation);
            }
        }

        [Obsolete("Use ApplyBindPose; bind and neutral poses are separate operations in schema v3.")]
        public static void ApplyRestPose(Transform instanceRoot, Dictionary<string, Matrix4x4> rest, Matrix4x4 catalogToWorld) =>
            ApplyBindPose(instanceRoot, rest, catalogToWorld);
    }
}
