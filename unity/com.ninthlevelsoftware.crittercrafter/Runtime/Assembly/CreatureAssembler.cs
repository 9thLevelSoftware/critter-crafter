using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEngine;

namespace CritterCrafter
{
    public enum CreatureCollision { None, SingleCapsule }

    [Serializable]
    public struct AssemblyOptions
    {
        public Transform parent;
        public int layer;
        public CreatureCollision collision;
        public bool collidersAreTriggers;
        /// <summary>Build a primitive stand-in instead of throwing when the recipe is invalid.</summary>
        public bool fallbackOnInvalid;
        /// <summary>Layers raycast by runtime foot placement to find the ground.</summary>
        public LayerMask groundMask;

        public static AssemblyOptions Default => new AssemblyOptions
        {
            layer = 0, collision = CreatureCollision.SingleCapsule, collidersAreTriggers = true, fallbackOnInvalid = true,
            groundMask = ~0,
        };
    }

    /// <summary>
    /// Post-assembly extension point. Optional assemblies (e.g. CritterCrafter.Locomotion, which needs
    /// Animation Rigging) register here so the reference-free Runtime assembly can stay dependency-free.
    /// </summary>
    public interface ICreatureRigHook
    {
        void OnAssembled(AssembledCreature creature, AssemblyOptions options);
    }

    public class AssemblyException : Exception
    {
        public AssemblyException(string message) : base(message) { }
    }

    /// <summary>
    /// Assembles a creature from a recipe: instantiate the skeleton (Animator + clips), then bind every
    /// part/connector mesh to the skeleton's bones with
    ///   bindpose_i = inverse(bone_i.bindWorld) * skeletonRoot * snap * Scale(s) * meshToPart
    /// (docs/frame.md), so imported bone-axis conventions never affect placement.
    /// </summary>
    public static class CreatureAssembler
    {
        static readonly List<ICreatureRigHook> RigHooks = new List<ICreatureRigHook>();

        /// <summary>Register a hook run after every successful assembly (idempotent).</summary>
        public static void RegisterRigHook(ICreatureRigHook hook)
        {
            if (hook != null && !RigHooks.Exists(h => h.GetType() == hook.GetType())) RigHooks.Add(hook);
        }

        static readonly Dictionary<string, Mesh> MeshCache = new Dictionary<string, Mesh>();
        struct BoundsSet { public Bounds bind, neutral, animation; }
        static readonly Dictionary<string, BoundsSet> BoundsCache = new Dictionary<string, BoundsSet>();

        public static void ClearCache()
        {
            foreach (var m in MeshCache.Values)
                if (m != null)
                {
                    if (Application.isPlaying) UnityEngine.Object.Destroy(m);
                    else UnityEngine.Object.DestroyImmediate(m);
                }
            MeshCache.Clear();
            BoundsCache.Clear();
        }

        public static AssembledCreature Assemble(CritterLibrary library, CritterRecipe recipe, AssemblyOptions options)
        {
            var catalog = library.Catalog ?? throw new AssemblyException("library has no catalog");
            var diags = RecipeValidator.Validate(catalog, recipe);
            var skelEntry = recipe == null ? null : library.FindSkeleton(recipe.skeleton_id);
            if (recipe != null && (skelEntry == null || skelEntry.model == null)) diags.Add("CC_MISSING_ASSET: skeleton " + recipe.skeleton_id);
            if (diags.Count > 0)
            {
                if (!options.fallbackOnInvalid) throw new AssemblyException(string.Join("; ", diags));
                return BuildFallback(recipe, options, diags);
            }

            var skeleton = catalog.FindSkeleton(recipe.skeleton_id);
            var root = new GameObject("Creature_" + recipe.recipe_id);
            if (options.parent != null) root.transform.SetParent(options.parent, false);

            var skelGo = UnityEngine.Object.Instantiate(skelEntry.model, root.transform, false);
            skelGo.name = "Skeleton";
            var bones = new Dictionary<string, Transform>();
            foreach (var t in skelGo.GetComponentsInChildren<Transform>(true))
                if (!bones.ContainsKey(t.name)) bones[t.name] = t;

            var animator = skelGo.GetComponent<Animator>();
            if (animator == null) animator = skelGo.AddComponent<Animator>();
            animator.runtimeAnimatorController = skelEntry.controller;
            animator.applyRootMotion = false;
            animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            animator.SetFloat(CreatureMotion.PlaybackRateParam, 1f);

            var creature = root.AddComponent<AssembledCreature>();
            creature.Init(recipe, animator, skeleton, skelEntry.model);

            // The catalog frame is the creature root's space: the skeleton model keeps whatever root rotation
            // the FBX importer gave it, which is part of the asset, not of the catalog (docs/frame.md).
            // Bones are reset to their true rest pose (from the BindProxy bindposes) before binding.
            Matrix4x4 catalogToWorld = root.transform.localToWorldMatrix;
            creature.ApplyBindPose();
            var proxy = SkeletonRest.FindProxy(skelGo.transform);
            if (proxy != null) proxy.gameObject.SetActive(false);
            // Skeleton FBXs may contain a preview renderer used to author and verify the
            // animation. Runtime geometry comes exclusively from recipe parts, so retaining
            // the preview would add an untracked renderer and duplicate the creature surface.
            foreach (var sourceRenderer in skelGo.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                if (sourceRenderer != proxy)
                {
                    sourceRenderer.enabled = false;
                    if (Application.isPlaying) UnityEngine.Object.Destroy(sourceRenderer);
                    else UnityEngine.Object.DestroyImmediate(sourceRenderer);
                }
            int triangles = 0;
            foreach (var fill in recipe.fills)
            {
                var branch = skeleton.FindBranch(fill.branch_id);
                var part = catalog.FindPart(fill.part_id);
                triangles += BindPart(library, skeleton, branch, part, (float)fill.length_scale,
                    false, bones, catalogToWorld, root.transform, creature);
                if (!string.IsNullOrEmpty(fill.connector_part_id))
                {
                    var conn = catalog.FindPart(fill.connector_part_id);
                    triangles += BindPart(library, skeleton, branch, conn, 1f, true, bones, catalogToWorld, root.transform, creature);
                }
            }
            creature.Triangles = triangles;

            string boundsKey = BoundsKey(library, recipe);
            if (!BoundsCache.TryGetValue(boundsKey, out var measured))
            {
                measured.bind = MeasureBounds(creature);
                measured.animation = SampleAnimationBounds(creature, skelGo, measured.bind);
                creature.ApplyBindPose();
                creature.ApplyNeutralPose();
                measured.neutral = MeasureBounds(creature);
                measured.animation.Encapsulate(measured.neutral);
                BoundsCache[boundsKey] = measured;
            }
            else
            {
                creature.ApplyBindPose();
                creature.ApplyNeutralPose();
            }
            SetRendererBounds(creature, measured.animation);
            creature.SetMeasuredBounds(measured.bind, measured.neutral, measured.animation);

            SetLayerRecursive(root, options.layer);
            if (options.collision == CreatureCollision.SingleCapsule) AddCapsule(root, creature, options.collidersAreTriggers);
            foreach (var hook in RigHooks) hook.OnAssembled(creature, options);
            return creature;
        }

        static string BoundsKey(CritterLibrary library, CritterRecipe recipe)
        {
            var key = new StringBuilder();
            key.Append(System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(library));
            key.Append('|').Append(recipe.skeleton_id);
            foreach (var fill in recipe.fills)
            {
                key.Append('|').Append(fill.branch_id).Append('=').Append(fill.part_id).Append('+')
                    .Append(fill.connector_part_id).Append('@')
                    .Append(fill.length_scale.ToString("R", CultureInfo.InvariantCulture));
            }
            return key.ToString();
        }

        static int BindPart(CritterLibrary library, SkeletonData skeleton, BranchData branch, PartData part,
            float lengthScale,
            bool connector, Dictionary<string, Transform> bones, Matrix4x4 catalogToWorld, Transform parent, AssembledCreature creature)
        {
            var entry = library.FindPart(part.part_id);
            if (entry == null || entry.model == null) throw new AssemblyException("missing part asset " + part.part_id);
            var srcSmr = entry.model.GetComponentInChildren<SkinnedMeshRenderer>(true);
            if (srcSmr == null || srcSmr.sharedMesh == null) throw new AssemblyException("part has no skinned mesh " + part.part_id);

            // Map each source bone (b0..bn) to a skeleton transform.
            var srcBones = srcSmr.bones;
            var targets = new Transform[srcBones.Length];
            for (int i = 0; i < srcBones.Length; i++)
            {
                string target = MapBone(srcBones[i].name, branch, connector);
                if (!bones.TryGetValue(target, out targets[i]))
                    throw new AssemblyException($"skeleton {skeleton.skeleton_id} lacks bone {target}");
            }

            Matrix4x4 meshToPart = MeshToPart(entry.model, srcSmr);
            Matrix4x4 meshToCatalog = CritterFrame.Snap(branch.snap, lengthScale) * meshToPart;
            Matrix4x4 meshToWorld = catalogToWorld * meshToCatalog;

            string key = $"{System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(library)}|{skeleton.skeleton_id}|{branch.branch_id}|{part.part_id}";
            if (!MeshCache.TryGetValue(key, out var mesh) || mesh == null)
            {
                mesh = UnityEngine.Object.Instantiate(srcSmr.sharedMesh);
                mesh.name = part.part_id + "@" + skeleton.skeleton_id + "." + branch.branch_id;
                // Bindposes are expressed in the catalog frame so the cached mesh is valid for any instance placement.
                var bind = new Matrix4x4[targets.Length];
                Matrix4x4 worldToCatalog = catalogToWorld.inverse;
                for (int i = 0; i < targets.Length; i++)
                    bind[i] = (worldToCatalog * targets[i].localToWorldMatrix).inverse * meshToCatalog;
                mesh.bindposes = bind;
                MeshCache[key] = mesh;
            }

            var go = new GameObject(connector ? $"{branch.branch_id}__{part.part_id}" : $"{branch.branch_id}_{part.part_id}");
            go.transform.SetParent(parent, false);
            var smr = go.AddComponent<SkinnedMeshRenderer>();
            smr.sharedMesh = mesh;
            smr.bones = targets;
            smr.rootBone = targets[0];
            if (mesh.subMeshCount > 2 || mesh.subMeshCount > part.max_material_slots)
                throw new AssemblyException($"part {part.part_id} has {mesh.subMeshCount} material slots");
            smr.sharedMaterials = MaterialsForPart(entry, mesh);
            smr.updateWhenOffscreen = false;
            smr.localBounds = PaddedBounds(mesh.bounds, targets[0].worldToLocalMatrix * meshToWorld, 0.35f);
            creature.AddRenderer(smr, branch.branch_id, part.part_id, connector);
            return TriangleCount(mesh);
        }

        /// <summary>
        /// Part mesh space -> part (catalog) space. The catalog frame is the model root's *parent* space,
        /// so any root rotation the FBX importer adds is kept as part of the asset (docs/frame.md).
        /// </summary>
        public static Matrix4x4 MeshToPart(GameObject partModel, SkinnedMeshRenderer smr)
        {
            var root = partModel.transform;
            return Matrix4x4.TRS(root.localPosition, root.localRotation, root.localScale)
                   * root.worldToLocalMatrix * smr.transform.localToWorldMatrix;
        }

        public static int TriangleCount(Mesh mesh)
        {
            long n = 0;
            for (int i = 0; i < mesh.subMeshCount; i++) n += mesh.GetIndexCount(i);
            return (int)(n / 3);
        }

        /// <summary>Part bone b&lt;i&gt; -> skeleton bone. Profile identity guarantees exact chain length.</summary>
        public static string MapBone(string sourceBone, BranchData branch, bool connector)
        {
            int idx = 0;
            if (sourceBone.Length <= 1 || sourceBone[0] != 'b' || !int.TryParse(sourceBone.Substring(1), out idx))
                throw new AssemblyException("unrecognized part bone " + sourceBone);
            if (connector)
            {
                if (idx == 0) return branch.attach_bone;
                if (idx == 1) return branch.bone_names[0];
                throw new AssemblyException("connector bone " + sourceBone + " exceeds two-bone connector profile");
            }
            if (idx < 0 || idx >= branch.bone_names.Length)
                throw new AssemblyException($"part bone {sourceBone} exceeds branch {branch.branch_id} chain");
            return branch.bone_names[idx];
        }

        public static Material[] MaterialsForPart(CritterLibrary.PartEntry entry, Mesh mesh)
        {
            if (entry.materials != null && entry.materials.Length == mesh.subMeshCount)
                return (Material[])entry.materials.Clone();
            var assigned = new Material[mesh.subMeshCount];
            for (int i = 0; i < assigned.Length; i++) assigned[i] = entry.material;
            return assigned;
        }

        static Bounds MeasureBounds(AssembledCreature creature)
        {
            Bounds result = default;
            bool any = false;
            foreach (var pr in creature.Renderers)
            {
                var baked = new Mesh();
                pr.renderer.BakeMesh(baked, true);
                Bounds b = TransformBounds(baked.bounds, creature.transform.worldToLocalMatrix * pr.renderer.transform.localToWorldMatrix);
                if (!any) { result = b; any = true; }
                else result.Encapsulate(b);
                if (Application.isPlaying) UnityEngine.Object.Destroy(baked); else UnityEngine.Object.DestroyImmediate(baked);
            }
            return any ? result : new Bounds(Vector3.zero, Vector3.zero);
        }

        static Bounds SampleAnimationBounds(AssembledCreature creature, GameObject skeleton, Bounds initial)
        {
            Bounds result = initial;
            var animator = creature.Animator;
            if (animator == null || animator.runtimeAnimatorController == null) return result;
            foreach (var clip in animator.runtimeAnimatorController.animationClips)
            {
                if (clip == null) continue;
                int samples = Mathf.Max(2, Mathf.CeilToInt(clip.length * clip.frameRate));
                for (int i = 0; i <= samples; i++)
                {
                    clip.SampleAnimation(skeleton, clip.length * i / samples);
                    result.Encapsulate(MeasureBounds(creature));
                }
            }
            return result;
        }

        static void SetRendererBounds(AssembledCreature creature, Bounds animationBounds)
        {
            foreach (var pr in creature.Renderers)
            {
                Matrix4x4 creatureToRenderer = pr.renderer.transform.worldToLocalMatrix * creature.transform.localToWorldMatrix;
                pr.renderer.localBounds = TransformBounds(animationBounds, creatureToRenderer);
            }
        }

        static Bounds TransformBounds(Bounds b, Matrix4x4 m)
        {
            var result = new Bounds(m.MultiplyPoint3x4(b.center), Vector3.zero);
            Vector3 e = b.extents;
            for (int i = 0; i < 8; i++)
                result.Encapsulate(m.MultiplyPoint3x4(b.center + new Vector3(
                    (i & 1) == 0 ? -e.x : e.x, (i & 2) == 0 ? -e.y : e.y, (i & 4) == 0 ? -e.z : e.z)));
            return result;
        }

        static Bounds PaddedBounds(Bounds b, Matrix4x4 m, float pad)
        {
            var result = new Bounds(m.MultiplyPoint3x4(b.center), Vector3.zero);
            Vector3 e = b.extents;
            for (int i = 0; i < 8; i++)
            {
                var corner = b.center + new Vector3((i & 1) == 0 ? -e.x : e.x, (i & 2) == 0 ? -e.y : e.y, (i & 4) == 0 ? -e.z : e.z);
                result.Encapsulate(m.MultiplyPoint3x4(corner));
            }
            result.Expand(pad);
            return result;
        }

        static void AddCapsule(GameObject root, AssembledCreature creature, bool trigger)
        {
            Bounds b = creature.NeutralBoundsLocal;
            var cap = root.AddComponent<CapsuleCollider>();
            cap.isTrigger = trigger;
            cap.direction = 1;
            cap.center = b.center;
            cap.radius = Mathf.Max(0.1f, 0.5f * Mathf.Max(b.size.x, b.size.z));
            cap.height = Mathf.Max(b.size.y, cap.radius * 2f);
        }

        static void SetLayerRecursive(GameObject go, int layer)
        {
            go.layer = layer;
            foreach (Transform c in go.transform) SetLayerRecursive(c.gameObject, layer);
        }

        static AssembledCreature BuildFallback(CritterRecipe recipe, AssemblyOptions options, List<string> diags)
        {
            var root = new GameObject("Creature_" + (recipe?.recipe_id ?? "invalid") + "_fallback");
            if (options.parent != null) root.transform.SetParent(options.parent, false);
            var prim = GameObject.CreatePrimitive(PrimitiveType.Capsule);
            prim.name = "Fallback";
            prim.transform.SetParent(root.transform, false);
            prim.transform.localPosition = Vector3.up;
            UnityEngine.Object.DestroyImmediate(prim.GetComponent<Collider>());
            var creature = root.AddComponent<AssembledCreature>();
            creature.Init(recipe, null, null);
            creature.Diagnostics.AddRange(diags);
            SetLayerRecursive(root, options.layer);
            if (options.collision == CreatureCollision.SingleCapsule)
            {
                var cap = root.AddComponent<CapsuleCollider>();
                cap.isTrigger = options.collidersAreTriggers;
                cap.center = Vector3.up;
                cap.height = 2f;
                cap.radius = 0.5f;
            }
            Debug.LogWarning("[CritterCrafter] fallback creature: " + string.Join("; ", diags));
            return creature;
        }
    }
}
