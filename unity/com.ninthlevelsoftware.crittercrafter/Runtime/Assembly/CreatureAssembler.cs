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
        /// <summary>
        /// Accept a review recipe whose pool_id is review_{skeleton_id}. Game loads must leave this false.
        /// </summary>
        public bool allowReview;

        public static AssemblyOptions Default => new AssemblyOptions
        {
            layer = 0, collision = CreatureCollision.SingleCapsule, collidersAreTriggers = true, fallbackOnInvalid = true,
            groundMask = ~0,
        };

        public static AssemblyOptions Review
        {
            get
            {
                var options = Default;
                options.allowReview = true;
                return options;
            }
        }
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
        /// <summary>Motion bounds per skeleton (not per part combination): the skeleton's clips don't depend on the parts on it.</summary>
        static readonly Dictionary<string, Bounds> MotionBoundsCache = new Dictionary<string, Bounds>();

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
            MotionBoundsCache.Clear();
        }

        public static AssembledCreature Assemble(CritterLibrary library, CritterRecipe recipe, AssemblyOptions options,
            bool preferBaked = true)
        {
            var catalog = library != null ? library.Catalog : null;
            if (catalog == null)
            {
                const string noCatalog = "CC_NO_CATALOG: library has no catalog";
                if (!options.fallbackOnInvalid) throw new AssemblyException(noCatalog);
                return BuildFallback(recipe, options, new List<string> { noCatalog });
            }
            var diags = RecipeValidator.Validate(catalog, recipe, options.allowReview);
            var skelEntry = recipe == null ? null : library.FindSkeleton(recipe.skeleton_id);
            if (recipe != null && (skelEntry == null || skelEntry.model == null)) diags.Add("CC_MISSING_ASSET: skeleton " + recipe.skeleton_id);
            if (diags.Count > 0)
            {
                if (!options.fallbackOnInvalid) throw new AssemblyException(string.Join("; ", diags));
                return BuildFallback(recipe, options, diags);
            }

            // Binding and rig errors (a missing part asset, an unmapped bone) must not leave a half-built
            // creature in the game's scene: destroy it, then fall back or rethrow.
            GameObject root = null;
            try
            {
                var baked = preferBaked ? library.FindBakedValidated(recipe) : null;
                return AssembleValidated(library, catalog, recipe, options, skelEntry, baked, created => root = created);
            }
            catch (Exception e)
            {
                if (root != null) DestroyNow(root);
                if (!options.fallbackOnInvalid) throw;
                return BuildFallback(recipe, options, new List<string> { "CC_ASSEMBLY_FAILED: " + e.Message });
            }
        }

        static void DestroyNow(GameObject go)
        {
            // Immediate, so nothing half-built is visible to the caller's next line (or a scene query) this frame.
            UnityEngine.Object.DestroyImmediate(go);
        }

        static AssembledCreature AssembleValidated(CritterLibrary library, CatalogData catalog, CritterRecipe recipe,
            AssemblyOptions options, CritterLibrary.SkeletonEntry skelEntry, ImportedBakedCreature baked, Action<GameObject> onCreated)
        {
            var skeleton = catalog.FindSkeleton(recipe.skeleton_id);
            var root = new GameObject("Creature_" + recipe.recipe_id);
            onCreated(root);
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
            creature.Locomotion = CreatureLocomotion.Build(catalog, recipe);

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
            if (baked != null)
                triangles = BindBaked(baked, bones, catalogToWorld, root.transform, creature);
            else
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

            string boundsKey = BoundsKey(library, recipe) + (baked == null ? "|live" : "|baked:" + baked.data.asset_sha256);
            if (!BoundsCache.TryGetValue(boundsKey, out var measured))
            {
                measured.bind = MeasureBounds(creature);
                creature.ApplyBindPose();
                creature.ApplyNeutralPose();
                measured.neutral = MeasureBounds(creature);
                // The motion box comes from the catalog (or, for an older library, one bone sampling per skeleton):
                // baking every part through every clip cost thousands of BakeMesh calls per spawn.
                measured.animation = SkeletonMotionBounds(library, skeleton, creature, skelGo);
                measured.animation.Encapsulate(measured.bind);
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
        static int BindBaked(ImportedBakedCreature baked, Dictionary<string, Transform> bones,
            Matrix4x4 catalogToWorld, Transform parent, AssembledCreature creature)
        {
            var renderers = baked.model.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            if (renderers.Length != 1 || baked.material == null)
                throw new AssemblyException("CC_BAKE_INVALID: expected one body renderer and material");
            var source = renderers[0];
            var original = source.sharedMesh;
            if (original == null || original.subMeshCount != 1 || source.bones.Length > 120
                || TriangleCount(original) > 30000 || original.bindposes.Length != source.bones.Length)
                throw new AssemblyException("CC_BAKE_INVALID: body exceeds mesh/bone contract");
            var targets = new Transform[source.bones.Length];
            var matrix = MeshToPart(baked.model, source);
            var importedBind = original.bindposes;
            for (int i = 0; i < targets.Length; i++)
            {
                if (source.bones[i] == null || !bones.TryGetValue(source.bones[i].name, out targets[i]))
                    throw new AssemblyException("CC_BAKE_BIND: unknown skeleton bone");
                var expected = catalogToWorld.inverse * targets[i].localToWorldMatrix;
                var actual = matrix * importedBind[i].inverse;
                for (int component = 0; component < 16; component++)
                    if (Mathf.Abs(expected[component] - actual[component]) > 1e-4f)
                        throw new AssemblyException("CC_BAKE_BIND: incompatible rest matrix " + source.bones[i].name);
            }
            string cacheKey = "baked|" + baked.data.key + "|" + baked.data.source_sha256 + "|" + baked.data.asset_sha256;
            if (!MeshCache.TryGetValue(cacheKey, out var mesh) || mesh == null)
            {
                mesh = UnityEngine.Object.Instantiate(original);
                var bind = new Matrix4x4[targets.Length];
                for (int i = 0; i < targets.Length; i++)
                    bind[i] = (catalogToWorld.inverse * targets[i].localToWorldMatrix).inverse * matrix;
                mesh.bindposes = bind;
                MeshCache[cacheKey] = mesh;
            }
            var body = new GameObject("BakedBody");
            body.transform.SetParent(parent, false);
            var renderer = body.AddComponent<SkinnedMeshRenderer>();
            renderer.sharedMesh = mesh;
            renderer.bones = targets;
            renderer.rootBone = bones.TryGetValue(RootBoneName, out var root) ? root : targets[0];
            renderer.sharedMaterial = baked.material;
            renderer.updateWhenOffscreen = false;
            creature.AddRenderer(renderer, "", baked.data.key, false);
            return TriangleCount(mesh);
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
            // One stable root bone for every part: localBounds is read in the rootBone's space, so a limb bone (which
            // moves) would drag the bounds around the body and they'd miss the mesh they are meant to cover.
            Transform rootBone = bones.TryGetValue(RootBoneName, out var creatureRoot) ? creatureRoot : targets[0];
            smr.rootBone = rootBone;
            if (mesh.subMeshCount > 2 || mesh.subMeshCount > part.max_material_slots)
                throw new AssemblyException($"part {part.part_id} has {mesh.subMeshCount} material slots");
            smr.sharedMaterials = MaterialsForPart(entry, mesh);
            smr.updateWhenOffscreen = false;
            smr.localBounds = PaddedBounds(mesh.bounds, rootBone.worldToLocalMatrix * meshToWorld, 0.35f);
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

        /// <summary>The skeleton's bone that every part renderer is rooted at (the catalog's root bone).</summary>
        const string RootBoneName = "root";

        /// <summary>
        /// Where a creature on this skeleton can be, in creature-local space: the catalog's motion box (every bone head
        /// and tail in every baked clip), grown by the thickest part, the runtime IK's foot travel and the body's own
        /// motion (bob, tilt, haul surge). Cached per skeleton.
        /// </summary>
        static Bounds SkeletonMotionBounds(CritterLibrary library, SkeletonData skeleton, AssembledCreature creature, GameObject skeletonGo)
        {
            string key = System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(library) + "|" + skeleton.skeleton_id;
            if (MotionBoundsCache.TryGetValue(key, out var cached)) return cached;
            Bounds box;
            var declared = skeleton.asset?.motion_bounds_m;
            if (declared != null && declared.IsValid)
            {
                // The catalog frame mirrors X into Unity's (CritterFrame.Position).
                Vector3 a = CritterFrame.Position(declared.min), b = CritterFrame.Position(declared.max);
                box = new Bounds((a + b) * 0.5f, Vector3.zero);
                box.Encapsulate(a);
                box.Encapsulate(b);
            }
            else box = SampleBoneBounds(creature, skeletonGo);
            float thickest = 0f;
            foreach (var branch in skeleton.branches) thickest = Mathf.Max(thickest, (float)branch.girth_m);
            float horizontal = 0f, vertical = 0f;
            var locomotion = skeleton.locomotion;
            if (locomotion != null && locomotion.legs != null)
                foreach (var leg in locomotion.legs)
                {
                    horizontal = Mathf.Max(horizontal, (float)(leg.stroke_m * 0.5 + System.Math.Abs(leg.stance_shift_m)));
                    vertical = Mathf.Max(vertical, (float)leg.clearance_m);
                }
            float body = 0.15f * (locomotion != null ? (float)locomotion.hip_height_m : 0f);
            float radius = 0.75f * thickest;
            box.Expand(new Vector3(2f * (radius + horizontal + body), 2f * (radius + vertical + body), 2f * (radius + horizontal + body)));
            MotionBoundsCache[key] = box;
            return box;
        }

        /// <summary>
        /// Older libraries carry no motion box: sample every clip once and take the bone positions (no mesh baking).
        /// Runs once per skeleton, not once per part combination.
        /// </summary>
        static Bounds SampleBoneBounds(AssembledCreature creature, GameObject skeleton)
        {
            var transforms = skeleton.GetComponentsInChildren<Transform>(true);
            Bounds result = new Bounds(creature.transform.InverseTransformPoint(skeleton.transform.position), Vector3.zero);
            var animator = creature.Animator;
            if (animator == null || animator.runtimeAnimatorController == null) return result;
            foreach (var clip in animator.runtimeAnimatorController.animationClips)
            {
                if (clip == null) continue;
                int samples = Mathf.Max(2, Mathf.CeilToInt(clip.length * clip.frameRate));
                for (int i = 0; i <= samples; i++)
                {
                    clip.SampleAnimation(skeleton, clip.length * i / samples);
                    foreach (var t in transforms) result.Encapsulate(creature.transform.InverseTransformPoint(t.position));
                }
            }
            creature.ApplyBindPose();
            creature.ApplyNeutralPose();
            return result;
        }

        static void SetRendererBounds(AssembledCreature creature, Bounds animationBounds)
        {
            foreach (var pr in creature.Renderers)
            {
                // localBounds is read in the space of the renderer's rootBone, not of the renderer's own transform.
                Matrix4x4 creatureToRoot = pr.renderer.rootBone.worldToLocalMatrix * creature.transform.localToWorldMatrix;
                pr.renderer.localBounds = TransformBounds(animationBounds, creatureToRoot);
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

        /// <summary>A grey stand-in carrying <paramref name="diagnostics"/>, for callers that fail before assembly.</summary>
        public static AssembledCreature CreateFallback(CritterRecipe recipe, AssemblyOptions options, IEnumerable<string> diagnostics) =>
            BuildFallback(recipe, options, new List<string>(diagnostics));

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
