using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace CritterCrafter.Editor
{
    /// <summary>
    /// Imports a built critter-crafter library (folder or release zip) into
    /// Assets/CritterLibraries/&lt;library_id&gt;/&lt;version&gt;/ and creates the CritterLibrary asset,
    /// materials (active pipeline's default lit shader) and one AnimatorController per skeleton.
    /// </summary>
    public static class LibraryImporter
    {
        public class Report
        {
            public CritterLibrary Library;
            public string AssetFolder;
            public readonly List<string> Problems = new List<string>();
        }

        [MenuItem("Tools/Critter Crafter/Import Library Folder...")]
        static void ImportFolderMenu()
        {
            var dir = EditorUtility.OpenFolderPanel("Critter library folder (contains catalog.json)", "", "");
            if (!string.IsNullOrEmpty(dir)) LogReport(Import(dir));
        }

        [MenuItem("Tools/Critter Crafter/Import Library Zip...")]
        static void ImportZipMenu()
        {
            var zip = EditorUtility.OpenFilePanel("Critter library release zip", "", "zip");
            if (string.IsNullOrEmpty(zip)) return;
            var tmp = Path.Combine(Path.GetTempPath(), "critter_import_" + Guid.NewGuid().ToString("N"));
            ZipFile.ExtractToDirectory(zip, tmp);
            try { LogReport(Import(tmp)); }
            finally { Directory.Delete(tmp, true); }
        }

        static void LogReport(Report r)
        {
            foreach (var p in r.Problems) Debug.LogWarning("[CritterCrafter] " + p);
            Debug.Log($"[CritterCrafter] imported {r.Library.LibraryId} v{r.Library.Version} into {r.AssetFolder} ({r.Problems.Count} problem(s))");
            Selection.activeObject = r.Library;
        }

        public static Report Import(string sourceDir)
        {
            var catalogPath = Path.Combine(sourceDir, "catalog.json");
            if (!File.Exists(catalogPath)) throw new FileNotFoundException("catalog.json not found", catalogPath);
            var catalog = JsonUtility.FromJson<CatalogData>(File.ReadAllText(catalogPath));
            if (catalog.schema_version != RecipeGenerator.SchemaVersion || catalog.document_kind != "critter_library"
                || catalog.version != RecipeGenerator.LibraryVersion || catalog.frame != "gltf_rh_yup_zfwd_m"
                || catalog.generator?.algorithm != RecipeGenerator.Algorithm)
                throw new InvalidDataException("not a supported critter library v3 catalog: " + catalogPath);

            var report = new Report();
            ValidateCatalogContract(catalog, report.Problems, true);
            string folder = CritterModelPostprocessor.LibrariesRoot + catalog.library_id + "/" + catalog.version;
            report.AssetFolder = folder;
            string abs = Path.GetFullPath(folder);
            if (Directory.Exists(abs)) AssetDatabase.DeleteAsset(folder);
            Directory.CreateDirectory(abs);

            // Copy catalog + FBX models only (GLBs are for Blender/previews; skipping them avoids double
            // import when a glTF importer package is installed).
            File.Copy(catalogPath, Path.Combine(abs, "catalog.json"));
            foreach (var file in Directory.GetFiles(sourceDir, "*.fbx", SearchOption.AllDirectories))
            {
                var rel = Path.GetRelativePath(sourceDir, file);
                var dst = Path.Combine(abs, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(dst));
                File.Copy(file, dst);
            }
            // Real parts carry a base-colour map next to their FBX (FBX exports strip texture paths).
            foreach (var p in catalog.parts)
            {
                if (string.IsNullOrEmpty(p.asset?.albedo_png)) continue;
                var src = Path.GetFullPath(Path.Combine(sourceDir, p.asset.albedo_png));
                var dst = Path.GetFullPath(Path.Combine(abs, p.asset.albedo_png));
                // A tampered catalog must not read or overwrite files outside the library folders.
                if (!IsUnder(sourceDir, src) || !IsUnder(abs, dst))
                { report.Problems.Add($"part texture path escapes the library {p.part_id}: {p.asset.albedo_png}"); continue; }
                if (!File.Exists(src)) { report.Problems.Add($"part texture missing {p.part_id}: {p.asset.albedo_png}"); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(dst));
                File.Copy(src, dst, true);
            }
            Directory.CreateDirectory(Path.Combine(abs, "Materials"));
            Directory.CreateDirectory(Path.Combine(abs, "Controllers"));
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            var shader = DefaultLitShader();
            var parts = new List<CritterLibrary.PartEntry>();
            foreach (var p in catalog.parts)
            {
                if (string.IsNullOrEmpty(p.asset?.fbx)) { report.Problems.Add("part without asset: " + p.part_id); continue; }
                var model = AssetDatabase.LoadAssetAtPath<GameObject>(folder + "/" + p.asset.fbx);
                if (model == null) { report.Problems.Add("model failed to import: " + p.asset.fbx); continue; }
                var mat = new Material(shader) { name = "M_" + p.part_id };
                if (ColorUtility.TryParseHtmlString(p.albedo, out var col))
                {
                    if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", col);
                    if (mat.HasProperty("_Color")) mat.SetColor("_Color", col);
                }
                if (!string.IsNullOrEmpty(p.asset.albedo_png))
                {
                    var albedo = AssetDatabase.LoadAssetAtPath<Texture2D>(folder + "/" + p.asset.albedo_png);
                    if (albedo == null) report.Problems.Add($"part texture failed to import {p.part_id}: {p.asset.albedo_png}");
                    else
                    {
                        if (mat.HasProperty("_BaseMap")) mat.SetTexture("_BaseMap", albedo);
                        if (mat.HasProperty("_MainTex")) mat.SetTexture("_MainTex", albedo);
                        if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", Color.white);
                        if (mat.HasProperty("_Color")) mat.SetColor("_Color", Color.white);
                    }
                }
                if (mat.HasProperty("_Smoothness")) mat.SetFloat("_Smoothness", 0.25f);
                if (mat.HasProperty("_Glossiness")) mat.SetFloat("_Glossiness", 0.25f);
                AssetDatabase.CreateAsset(mat, $"{folder}/Materials/M_{p.part_id}.mat");
                var sourceRenderers = model.GetComponentsInChildren<SkinnedMeshRenderer>(true);
                if (sourceRenderers.Length != 1)
                    report.Problems.Add($"part renderer count {p.part_id}: {sourceRenderers.Length}");
                var smr = sourceRenderers.FirstOrDefault();
                if (smr == null) report.Problems.Add("part has no SkinnedMeshRenderer: " + p.part_id);
                else
                {
                    if (CreatureAssembler.TriangleCount(smr.sharedMesh) != p.asset.triangles)
                        report.Problems.Add($"triangle mismatch {p.part_id}: unity {CreatureAssembler.TriangleCount(smr.sharedMesh)} catalog {p.asset.triangles}");
                    if (smr.sharedMesh.subMeshCount > 2 || smr.sharedMesh.subMeshCount > p.max_material_slots)
                        report.Problems.Add($"material slots {p.part_id}: {smr.sharedMesh.subMeshCount}>{Math.Min(2, p.max_material_slots)}");
                    ValidateSkinnedPartMesh(p, smr, report.Problems);
                }
                int materialCount = smr?.sharedMesh == null ? 1 : Math.Max(1, smr.sharedMesh.subMeshCount);
                var materials = new Material[materialCount];
                materials[0] = mat;
                for (int i = 1; i < materials.Length; i++)
                {
                    var slot = new Material(mat) { name = $"M_{p.part_id}_{i}" };
                    AssetDatabase.CreateAsset(slot, $"{folder}/Materials/M_{p.part_id}_{i}.mat");
                    materials[i] = slot;
                }
                parts.Add(new CritterLibrary.PartEntry { partId = p.part_id, model = model, material = mat, materials = materials });
            }

            var skeletons = new List<CritterLibrary.SkeletonEntry>();
            foreach (var s in catalog.skeletons)
            {
                string path = folder + "/" + s.asset.fbx;
                var model = AssetDatabase.LoadAssetAtPath<GameObject>(path);
                if (model == null) { report.Problems.Add("skeleton failed to import: " + path); continue; }
                var modelImporter = AssetImporter.GetAtPath(path) as ModelImporter;
                if (modelImporter == null || modelImporter.animationType != ModelImporterAnimationType.Generic)
                    report.Problems.Add("skeleton is not Generic: " + s.skeleton_id);
                var clips = AnimatorBuilder.LoadClips(path);
                foreach (var required in RequiredClips)
                    if (!clips.ContainsKey(required)) report.Problems.Add($"skeleton {s.skeleton_id} missing required clip {required}");
                foreach (var c in s.asset.clips)
                {
                    if (!clips.TryGetValue(c.name, out var imported)) report.Problems.Add($"skeleton {s.skeleton_id} missing clip {c.name}");
                    else ValidateClip(catalog, s, c, imported, model, report.Problems);
                }
                ValidateMotionArtifact(sourceDir, s, catalog, model, clips, report.Problems);
                var ctrl = AnimatorBuilder.Build($"{folder}/Controllers/{s.skeleton_id}.controller", clips, s);
                skeletons.Add(new CritterLibrary.SkeletonEntry { skeletonId = s.skeleton_id, model = model, controller = ctrl });
                FrameProbe.Validate(model, s, report.Problems);
            }

            var lib = ScriptableObject.CreateInstance<CritterLibrary>();
            lib.EditorSetContents(AssetDatabase.LoadAssetAtPath<TextAsset>(folder + "/catalog.json"), skeletons.ToArray(), parts.ToArray());
            AssetDatabase.CreateAsset(lib, $"{folder}/{catalog.library_id}.asset");
            AssetDatabase.SaveAssets();
            report.Library = lib;
            return report;
        }

        static bool IsUnder(string root, string fullPath)
        {
            var prefix = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                         + Path.DirectorySeparatorChar;
            var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            return fullPath.StartsWith(prefix, comparison);
        }

        public static Shader DefaultLitShader()
        {
            var rp = GraphicsSettings.defaultRenderPipeline;
            if (rp != null && rp.defaultMaterial != null) return rp.defaultMaterial.shader;
            return Shader.Find("Standard");
        }

        static readonly string[] RequiredClips = { "idle", "walk", "run", "attack", "telegraph", "hit", "stun", "death" };

        public static List<string> ValidateCatalogContract(CatalogData catalog, bool requireBuiltAssets = true)
        {
            var problems = new List<string>();
            ValidateCatalogContract(catalog, problems, requireBuiltAssets);
            return problems;
        }

        static void ValidateSkinnedPartMesh(PartData part, SkinnedMeshRenderer renderer, List<string> problems)
        {
            bool connector = part.category == "connector";
            if (connector && part.connector_interface == null)
            { problems.Add("connector interface missing " + part.part_id); return; }
            var names = renderer.bones == null ? Array.Empty<string>() : renderer.bones.Select(b => b.name).ToArray();
            bool namedChain = names.Length > 0 && names.SequenceEqual(Enumerable.Range(0, names.Length).Select(i => "b" + i));
            if (connector && (names.Length != 2 || names[0] != "b0" || names[1] != "b1"))
                problems.Add($"connector bones {part.part_id}: {string.Join(",", names)}");
            else if (!connector && !namedChain)
                problems.Add($"part bones {part.part_id}: {string.Join(",", names)}");
            int maxInfluences = connector ? part.connector_interface.max_influences : 4;
            string kind = connector ? "connector" : "part";
            try
            {
                var mesh = renderer.sharedMesh;
                if (mesh == null) { problems.Add($"{kind} mesh missing {part.part_id}"); return; }
                foreach (var weight in mesh.boneWeights)
                {
                    if (weight.weight0 < 0f || weight.weight1 < 0f || weight.weight2 < 0f || weight.weight3 < 0f)
                    {
                        problems.Add($"{kind} weights {part.part_id}: negative");
                        break;
                    }
                    int influences = (weight.weight0 > 0f ? 1 : 0) + (weight.weight1 > 0f ? 1 : 0)
                        + (weight.weight2 > 0f ? 1 : 0) + (weight.weight3 > 0f ? 1 : 0);
                    float total = weight.weight0 + weight.weight1 + weight.weight2 + weight.weight3;
                    int[] indices = { weight.boneIndex0, weight.boneIndex1, weight.boneIndex2, weight.boneIndex3 };
                    float[] values = { weight.weight0, weight.weight1, weight.weight2, weight.weight3 };
                    bool badIndex = false;
                    for (int i = 0; i < 4; i++)
                        if (values[i] > 0f && (indices[i] < 0 || indices[i] >= names.Length))
                        { badIndex = true; break; }
                    if (badIndex)
                    {
                        problems.Add($"{kind} weights {part.part_id}: joint index");
                        break;
                    }
                    if (influences > maxInfluences || Mathf.Abs(total - 1f) > 1e-4f)
                    {
                        problems.Add($"{kind} weights {part.part_id}: influences={influences} sum={total:F6}");
                        break;
                    }
                }
            }
            catch (UnityException e) { problems.Add($"{kind} weights unreadable {part.part_id}: {e.Message}"); }
        }

        static void ValidateCatalogContract(CatalogData catalog, List<string> problems, bool requireBuiltAssets)
        {
            if (catalog.limits == null || catalog.limits.max_bones > 120 || catalog.limits.max_parts > 16
                || catalog.limits.max_triangles > 30000 || catalog.limits.max_influences > 4)
                problems.Add("catalog exceeds hard runtime limits");
            foreach (var skeleton in catalog.skeletons)
            {
                if (skeleton.bones == null || skeleton.bones.Length > 120) problems.Add("bone budget " + skeleton.skeleton_id);
                if (requireBuiltAssets && (skeleton.asset?.clips == null || skeleton.asset.clips.Length != RequiredClips.Length
                    || RequiredClips.Any(name => !skeleton.asset.clips.Any(c => c.name == name))))
                    problems.Add("required clip metadata " + skeleton.skeleton_id);
                else if (requireBuiltAssets) foreach (var clip in skeleton.asset.clips)
                {
                    var rateRange = clip.playback?.rate_range;
                    if (rateRange == null || rateRange.Length != 2 || rateRange[0] <= 0.0
                        || rateRange[0] > 1.0 || rateRange[1] < 1.0 || rateRange[0] > rateRange[1])
                        problems.Add($"playback rate range {skeleton.skeleton_id}/{clip.name}");
                    string expectedContinuation = clip.name == "telegraph" ? "attack" : "locomotion";
                    if (clip.playback?.continuation != expectedContinuation)
                        problems.Add($"playback continuation {skeleton.skeleton_id}/{clip.name}: {clip.playback?.continuation}");
                    if (clip.contact_schedule == null)
                    {
                        problems.Add($"contact schedule metadata {skeleton.skeleton_id}/{clip.name}");
                        continue;
                    }
                    int expectedContacts = skeleton.branches.Sum(b => b.contacts?.Length ?? 0);
                    if (clip.contact_schedule.Length != expectedContacts)
                        problems.Add($"contact schedule count {skeleton.skeleton_id}/{clip.name}: {clip.contact_schedule.Length}!={expectedContacts}");
                    var ids = new HashSet<string>();
                    foreach (var declared in clip.contact_schedule)
                    {
                        var contactBranch = skeleton.FindBranch(declared.branch_id);
                        if (!ids.Add(declared.contact_id) || declared.contact_id != declared.branch_id + ":" + declared.contact_index
                            || contactBranch?.contacts == null || declared.contact_index < 0
                            || declared.contact_index >= contactBranch.contacts.Length
                            || contactBranch.contacts[declared.contact_index].kind != declared.kind
                            || declared.phase_offset < 0.0 || declared.phase_offset > 1.0
                            || declared.stance_fraction < 0.0 || declared.stance_fraction > 1.0)
                            problems.Add($"contact schedule entry {skeleton.skeleton_id}/{clip.name}/{declared.contact_id}");
                    }
                }
                var locomotionRates = !requireBuiltAssets || skeleton.asset?.clips == null ? Array.Empty<double[]>()
                    : skeleton.asset.clips.Where(c => c.name == "idle" || c.name == "walk" || c.name == "run")
                        .Select(c => c.playback?.rate_range).ToArray();
                if (locomotionRates.Length == 3 && (locomotionRates.Any(r => r == null || r.Length != 2)
                    || locomotionRates.Skip(1).Any(r => Math.Abs(r[0] - locomotionRates[0][0]) > 1e-6
                        || Math.Abs(r[1] - locomotionRates[0][1]) > 1e-6)))
                    problems.Add("locomotion playback rate range " + skeleton.skeleton_id);
                if (skeleton.neutral_pose?.rotations == null || skeleton.neutral_pose.rotations.Length != skeleton.bones.Length)
                    problems.Add("neutral pose bone count " + skeleton.skeleton_id);
                else for (int i = 0; i < skeleton.bones.Length; i++)
                {
                    var rotation = skeleton.neutral_pose.rotations[i];
                    if (rotation.bone_name != skeleton.bones[i].name)
                        problems.Add($"neutral pose order {skeleton.skeleton_id}: {rotation.bone_name}!={skeleton.bones[i].name}");
                    if (!UnitQuaternion(rotation.rotation_xyzw)) problems.Add($"neutral pose quaternion {skeleton.skeleton_id}/{rotation.bone_name}");
                }
                foreach (var branch in skeleton.branches)
                {
                    var profile = catalog.FindBindingProfile(branch.binding_profile_id, branch.binding_profile_version);
                    if (profile == null || profile.binding_profile_hash != branch.binding_profile_hash)
                        problems.Add($"binding profile identity {skeleton.skeleton_id}/{branch.branch_id}");
                    else if (branch.joint_order == null || branch.bone_fractions == null
                        || branch.joint_order.Length != branch.bone_names.Length
                        || branch.bone_fractions.Length != branch.bone_names.Length
                        || !branch.joint_order.SequenceEqual(profile.joint_order))
                        problems.Add($"binding topology {skeleton.skeleton_id}/{branch.branch_id}");
                    if (!UnitQuaternion(branch.socket?.rotation_xyzw))
                        problems.Add($"socket quaternion {skeleton.skeleton_id}/{branch.branch_id}");
                    else ValidateSocketCompilation(catalog, skeleton, branch, problems);
                    var connector = branch.connector_interface;
                    if (!string.IsNullOrEmpty(connector?.interface_id) && (connector.interface_id != "skinned_parent_child"
                        || connector.interface_version != "1.0.0" || connector.parent_role != "parent"
                        || connector.child_role != "child" || connector.parent_bone != branch.attach_bone
                        || connector.child_bone != branch.bone_names[0] || !ZeroVector(connector.position_m)
                        || !IdentityQuaternion(connector.rotation_xyzw)))
                        problems.Add($"connector interface {skeleton.skeleton_id}/{branch.branch_id}");
                    if (branch.contacts != null)
                        foreach (var contact in branch.contacts)
                            if (contact.bone_index < 0 || contact.bone_index >= branch.bone_names.Length)
                                problems.Add($"contact bone {skeleton.skeleton_id}/{branch.branch_id}/{contact.bone_index}");
                }
            }
            foreach (var part in catalog.parts)
            {
                var profile = catalog.FindBindingProfile(part.binding_profile_id, part.binding_profile_version);
                if (profile == null || profile.binding_profile_hash != part.binding_profile_hash)
                    problems.Add("part binding profile identity " + part.part_id);
            }
        }

        static bool UnitQuaternion(double[] q)
        {
            if (q == null || q.Length != 4) return false;
            double n = 0;
            foreach (double value in q) { if (double.IsNaN(value) || double.IsInfinity(value)) return false; n += value * value; }
            return Math.Abs(n - 1.0) <= 1e-5;
        }

        static bool ZeroVector(double[] v) => v != null && v.Length == 3 && v.All(x => x == 0.0);
        static bool IdentityQuaternion(double[] q) => q != null && q.Length == 4
            && q[0] == 0.0 && q[1] == 0.0 && q[2] == 0.0 && q[3] == 1.0;

        static void ValidateSocketCompilation(CatalogData catalog, SkeletonData skeleton, BranchData branch,
            List<string> problems)
        {
            Vector3 origin;
            Quaternion parentFrame;
            if (string.IsNullOrEmpty(branch.parent_branch))
            {
                var root = Array.Find(skeleton.bones, b => b.name == "root");
                if (root == null || branch.socket.parent_joint != "root")
                {
                    problems.Add($"socket parent {skeleton.skeleton_id}/{branch.branch_id}");
                    return;
                }
                origin = CritterFrame.Position(root.head_m);
                parentFrame = Quaternion.identity;
            }
            else
            {
                var parent = skeleton.FindBranch(branch.parent_branch);
                var profile = parent == null ? null
                    : catalog.FindBindingProfile(parent.binding_profile_id, parent.binding_profile_version);
                int index = profile?.joint_order == null ? -1
                    : Array.IndexOf(profile.joint_order, branch.socket.parent_joint);
                if (parent == null || profile == null || index < 0 || index >= parent.bone_names.Length)
                {
                    problems.Add($"socket parent {skeleton.skeleton_id}/{branch.branch_id}");
                    return;
                }
                var parentBone = Array.Find(skeleton.bones, b => b.name == parent.bone_names[index]);
                var joint = profile.joints == null ? null : Array.Find(profile.joints, j => j.joint_id == branch.socket.parent_joint);
                if (parentBone == null || joint == null || !UnitQuaternion(joint.canonical_rotation_xyzw))
                {
                    problems.Add($"socket parent frame {skeleton.skeleton_id}/{branch.branch_id}");
                    return;
                }
                origin = CritterFrame.Position(parentBone.head_m);
                parentFrame = CritterFrame.Rotation(parent.snap.rotation_xyzw)
                    * CritterFrame.Rotation(joint.canonical_rotation_xyzw);
            }
            Vector3 expectedPosition = origin + parentFrame * CritterFrame.Position(branch.socket.position_m);
            Quaternion expectedRotation = parentFrame * CritterFrame.Rotation(branch.socket.rotation_xyzw);
            float positionError = Vector3.Distance(expectedPosition, CritterFrame.Position(branch.snap.position_m));
            float rotationError = Quaternion.Angle(expectedRotation, CritterFrame.Rotation(branch.snap.rotation_xyzw));
            if (positionError > FrameProbe.Tolerance)
                problems.Add($"source socket position {skeleton.skeleton_id}/{branch.branch_id}: {positionError:F6}m");
            if (rotationError > FrameProbe.RotationToleranceDeg)
                problems.Add($"source socket orientation {skeleton.skeleton_id}/{branch.branch_id}: {rotationError:F4}deg");
        }

        static void ValidateClip(CatalogData catalog, SkeletonData skeleton, ClipInfo info, AnimationClip clip, GameObject model, List<string> problems)
        {
            if (clip == null) return;
            float expectedDuration = info.duration_s > 0 ? (float)info.duration_s
                : info.fps > 0 ? (float)info.frames / info.fps : clip.length;
            if (Mathf.Abs(clip.length - expectedDuration) > 1f / Mathf.Max(1, info.fps))
                problems.Add($"clip duration {skeleton.skeleton_id}/{info.name}: unity {clip.length:F4}s catalog {expectedDuration:F4}s");
            if (clip.isLooping != info.loop)
                problems.Add($"clip loop setting {skeleton.skeleton_id}/{info.name}: unity {clip.isLooping} catalog {info.loop}");
            var go = UnityEngine.Object.Instantiate(model);
            try
            {
                var transforms = go.GetComponentsInChildren<Transform>(true);
                var rest = SkeletonRest.Get(model);
                SkeletonRest.ApplyBindPose(go.transform, rest, Matrix4x4.identity);
                var root = Array.Find(transforms, t => t.name == "root");
                Vector3 modelStart = go.transform.position;
                Vector3 rootStart = root == null ? Vector3.zero : root.position;
                bool moved = false;
                bool modelRootMoved = false;
                bool horizontalRootMoved = false;
                var rotations = new Quaternion[transforms.Length];
                for (int i = 0; i < transforms.Length; i++) rotations[i] = transforms[i].localRotation;
                var byName = transforms.ToDictionary(t => t.name, t => t);
                var bindLocal = transforms.ToDictionary(t => t.name, t => t.localRotation);
                int samples = Math.Max(2, info.frames);
                for (int frame = 0; frame <= samples; frame++)
                {
                    clip.SampleAnimation(go, clip.length * frame / samples);
                    modelRootMoved |= (go.transform.position - modelStart).sqrMagnitude > 1e-8f;
                    horizontalRootMoved |= root != null && Mathf.Abs(root.position.x - rootStart.x) + Mathf.Abs(root.position.z - rootStart.z) > 1e-4f;
                    for (int i = 0; i < transforms.Length; i++)
                        moved |= Quaternion.Angle(rotations[i], transforms[i].localRotation) > 0.01f;
                    ValidateImportedJointLimits(catalog, skeleton, byName, bindLocal, info.name, frame, problems);
                }
                if (modelRootMoved) problems.Add($"clip root motion {skeleton.skeleton_id}/{info.name}: model root moved");
                if (horizontalRootMoved) problems.Add($"clip horizontal root motion {skeleton.skeleton_id}/{info.name}");
                if (!moved) problems.Add($"clip has no post-import bone movement: {skeleton.skeleton_id}/{info.name}");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }

        static void ValidateImportedJointLimits(CatalogData catalog, SkeletonData skeleton,
            Dictionary<string, Transform> transforms, Dictionary<string, Quaternion> bindLocal,
            string clipName, int frame, List<string> problems)
        {
            foreach (var branch in skeleton.branches)
            {
                var profile = catalog.FindBindingProfile(branch.binding_profile_id, branch.binding_profile_version);
                if (profile?.joints == null) continue;
                for (int i = 0; i < branch.bone_names.Length && i < profile.joints.Length; i++)
                {
                    string boneName = branch.bone_names[i];
                    var bone = Array.Find(skeleton.bones, b => b.name == boneName);
                    if (bone == null || !transforms.TryGetValue(boneName, out var t)
                        || !bindLocal.TryGetValue(boneName, out var bind)) continue;
                    Quaternion localDelta = ImportedMotionDelta(t, bind);
                    ValidateDeltaLimits(localDelta, profile.joints[i].limits_deg,
                        $"post-import joint limit {skeleton.skeleton_id}/{clipName}/{boneName} frame {frame}", problems);
                }
            }
        }

        // Profile axes: canonical twist +Z, swing_y +Y, swing_x +X. Bone-delta basis maps
        // these to local +Y, +Z, and -X respectively.
        static void ValidateDeltaLimits(Quaternion boneDelta, JointLimits limits, string label, List<string> problems)
        {
            if (limits == null) return;
            boneDelta.Normalize();
            // Explicit XYZ extraction matches Blender's Quaternion.to_euler("XYZ") and the
            // catalog/schema validator; Unity's Quaternion.eulerAngles uses a different order.
            double x = boneDelta.x, y = boneDelta.y, z = boneDelta.z, w = boneDelta.w;
            float swingX = (float)(-Math.Atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)) * Mathf.Rad2Deg);
            float twist = (float)(Math.Asin(Math.Max(-1.0, Math.Min(1.0, 2.0 * (w * y - z * x)))) * Mathf.Rad2Deg);
            float swingY = (float)(Math.Atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)) * Mathf.Rad2Deg);
            if (!InRange(swingX, limits.swing_x) || !InRange(swingY, limits.swing_y) || !InRange(twist, limits.twist))
                AddOnce(problems, $"{label}: swing=({swingX:F2},{swingY:F2}) twist={twist:F2}");
        }

        static Quaternion ImportedMotionDelta(Transform actual, Quaternion bindLocal)
        {
            // Rest transforms satisfy R_unity=C*B_catalog*C*F, where C is the RH->LH X
            // reflection and F=Ry(180) is Unity's FBX bone-basis reparameterization. Therefore
            // deltaImported=F^-1*C*deltaCatalog*C*F and the combined inverse maps quaternion
            // components as (-x,-y,+z,+w), up to the equivalent all-component sign flip.
            Quaternion imported = Quaternion.Inverse(bindLocal) * actual.localRotation;
            return FrameProbe.RecoverCatalogMotionDelta(imported);
        }
        static bool InRange(float value, double[] range) => range == null || range.Length != 2
            || value >= range[0] - 0.5 && value <= range[1] + 0.5;
        static void AddOnce(List<string> problems, string problem)
        {
            string key = problem.Split(new[] { " frame " }, StringSplitOptions.None)[0];
            if (!problems.Any(p => p.StartsWith(key, StringComparison.Ordinal))) problems.Add(problem);
        }

        [Serializable] class MotionDocument
        {
            public string schema_version;
            public string skeleton_id;
            public int fps;
            public string root_motion;
            public MotionClip[] clips;
        }
        [Serializable] class MotionClip
        {
            public string name;
            public bool loop;
            public int frames;
            public int fps;
            public double duration_s;
            public double cadence_hz;
            public double nominal_speed_mps;
            public double speed_mps;
            public double stride_m;
            public ClipPlayback playback;
            public ContactScheduleEntry[] contact_schedule;
            public MotionSample[] samples;
        }
        [Serializable] class MotionSample
        {
            public int frame;
            public double phase;
            public MotionBone[] bones;
            public MotionContact[] contacts;
        }
        [Serializable] class MotionBone
        {
            public string name;
            public double[] head_m;
            public double[] tail_m;
            public double[] rotation_xyzw;
        }
        [Serializable] class MotionContact
        {
            public string branch_id;
            public int contact_index;
            public string kind;
            public bool planted;
            public double phase;
            public double[] position_m;
            public double[] target_m;
            public double error_m;
        }

        public static void ValidateMotionArtifact(string sourceDir, SkeletonData skeleton, CatalogData catalog,
            GameObject model, Dictionary<string, AnimationClip> importedClips, List<string> problems)
        {
            string relative = Path.GetDirectoryName(skeleton.asset.fbx) ?? "";
            string path = Path.Combine(sourceDir, relative, "motion.json");
            if (!File.Exists(path)) { problems.Add("missing motion artifact: " + skeleton.skeleton_id); return; }
            MotionDocument doc;
            try { doc = JsonUtility.FromJson<MotionDocument>(File.ReadAllText(path)); }
            catch (Exception e) { problems.Add($"motion artifact {skeleton.skeleton_id}: {e.Message}"); return; }
            if (doc == null || doc.skeleton_id != skeleton.skeleton_id || doc.root_motion != "fixed_horizontal")
                problems.Add("motion identity/root motion: " + skeleton.skeleton_id);
            if (doc?.clips == null) return;
            foreach (var required in RequiredClips)
                if (!doc.clips.Any(c => c.name == required))
                    problems.Add($"motion artifact {skeleton.skeleton_id} missing required clip {required}");
            foreach (var clip in doc.clips)
            {
                var info = skeleton.asset?.clips == null ? null : Array.Find(skeleton.asset.clips, c => c.name == clip.name);
                if (info != null) ValidateMotionMetadata(skeleton, info, clip, problems);
                if (clip.samples == null || clip.samples.Length != clip.frames + 1)
                    problems.Add($"motion sample count {skeleton.skeleton_id}/{clip.name}");
                if (clip.samples == null) continue;
                foreach (var sample in clip.samples)
                {
                    if (sample.bones != null)
                        foreach (var motionBone in sample.bones)
                        {
                            var bone = Array.Find(skeleton.bones, b => b.name == motionBone.name);
                            if (bone == null) { AddOnce(problems, $"motion unknown bone {skeleton.skeleton_id}/{motionBone.name}"); continue; }
                            var branch = Array.Find(skeleton.branches, b => Array.IndexOf(b.bone_names, motionBone.name) >= 0);
                            if (branch == null) continue; // root has no chain profile
                            int index = Array.IndexOf(branch.bone_names, motionBone.name);
                            var profile = catalog.FindBindingProfile(branch.binding_profile_id, branch.binding_profile_version);
                            if (profile?.joints == null || index >= profile.joints.Length) continue;
                            var q = motionBone.rotation_xyzw;
                            if (!UnitQuaternion(q)) AddOnce(problems, $"motion quaternion {skeleton.skeleton_id}/{motionBone.name}");
                            else ValidateDeltaLimits(new Quaternion((float)q[0], (float)q[1], (float)q[2], (float)q[3]),
                                profile.joints[index].limits_deg,
                                $"motion joint limit {skeleton.skeleton_id}/{clip.name}/{motionBone.name} frame {sample.frame}", problems);
                        }
                    if (sample.contacts != null)
                        foreach (var contact in sample.contacts)
                        {
                            var branch = skeleton.FindBranch(contact.branch_id);
                            if (branch == null) { AddOnce(problems, $"motion unknown contact {skeleton.skeleton_id}/{contact.branch_id}"); continue; }
                            double maxError = Math.Max(0.005, 0.01 * branch.length_m);
                            if (contact.planted && contact.error_m > maxError)
                                AddOnce(problems, $"contact drift {skeleton.skeleton_id}/{clip.name}/{contact.branch_id}: {contact.error_m:F5}m");
                        }
                }
                ValidateContactSchedule(skeleton, clip, problems);
                if (importedClips.TryGetValue(clip.name, out var imported))
                    ValidateImportedMotionSamples(skeleton, clip, imported, model, problems);
            }
            ValidateCorrespondingFootfalls(skeleton, doc.clips, problems);
        }

        static void ValidateMotionMetadata(SkeletonData skeleton, ClipInfo catalog, MotionClip source,
            List<string> problems)
        {
            bool mismatch = catalog.loop != source.loop || catalog.frames != source.frames || catalog.fps != source.fps
                || Math.Abs(catalog.duration_s - source.duration_s) > 1e-6
                || Math.Abs(catalog.cadence_hz - source.cadence_hz) > 1e-6
                || Math.Abs(catalog.nominal_speed_mps - source.nominal_speed_mps) > 1e-6
                || Math.Abs(catalog.speed_mps - source.speed_mps) > 1e-6
                || Math.Abs(catalog.stride_m - source.stride_m) > 1e-6;
            if (mismatch) problems.Add($"motion metadata {skeleton.skeleton_id}/{source.name}");
            var playback = catalog.playback;
            if (playback == null || playback.phase_range == null || playback.phase_range.Length != 2
                || playback.rate_range == null || playback.rate_range.Length != 2
                || playback.phase_range[0] < 0.0 || playback.phase_range[1] > 1.0
                || playback.phase_range[0] >= playback.phase_range[1]
                || playback.rate_range[0] <= 0.0 || playback.rate_range[0] > 1.0
                || playback.rate_range[1] < 1.0 || playback.rate_range[0] > playback.rate_range[1]
                || (catalog.loop && playback.wrap != "loop") || (!catalog.loop && playback.wrap == "loop"))
                problems.Add($"playback metadata {skeleton.skeleton_id}/{source.name}");
            else if (source.playback == null || source.playback.wrap != playback.wrap
                || source.playback.continuation != playback.continuation
                || source.playback.phase_range == null || source.playback.phase_range.Length != 2
                || source.playback.rate_range == null || source.playback.rate_range.Length != 2
                || Math.Abs(source.playback.phase_range[0] - playback.phase_range[0]) > 1e-6
                || Math.Abs(source.playback.phase_range[1] - playback.phase_range[1]) > 1e-6
                || Math.Abs(source.playback.rate_range[0] - playback.rate_range[0]) > 1e-6
                || Math.Abs(source.playback.rate_range[1] - playback.rate_range[1]) > 1e-6)
                problems.Add($"motion playback {skeleton.skeleton_id}/{source.name}");
            if (!SameContactSchedule(catalog.contact_schedule, source.contact_schedule))
                problems.Add($"motion contact schedule metadata {skeleton.skeleton_id}/{source.name}");
        }

        static bool SameContactSchedule(ContactScheduleEntry[] catalog, ContactScheduleEntry[] source)
        {
            if (catalog == null || source == null || catalog.Length != source.Length) return false;
            for (int i = 0; i < catalog.Length; i++)
            {
                var a = catalog[i]; var b = source[i];
                if (a.contact_id != b.contact_id || a.branch_id != b.branch_id
                    || a.contact_index != b.contact_index || a.kind != b.kind || a.support != b.support
                    || Math.Abs(a.phase_offset - b.phase_offset) > 1e-6
                    || Math.Abs(a.stance_fraction - b.stance_fraction) > 1e-6) return false;
            }
            return true;
        }

        static void ValidateImportedMotionSamples(SkeletonData skeleton, MotionClip source,
            AnimationClip imported, GameObject model, List<string> problems)
        {
            if (source.samples == null || source.samples.Length == 0 || imported == null) return;
            var go = UnityEngine.Object.Instantiate(model);
            try
            {
                var transforms = go.GetComponentsInChildren<Transform>(true)
                    .GroupBy(t => t.name).ToDictionary(g => g.Key, g => g.First());
                var rest = SkeletonRest.Get(model);
                SkeletonRest.ApplyBindPose(go.transform, rest, Matrix4x4.identity);
                var bindLocal = transforms.ToDictionary(pair => pair.Key, pair => pair.Value.localRotation);
                foreach (var sample in source.samples)
                {
                    float time = source.fps > 0 ? (float)sample.frame / source.fps
                        : imported.length * sample.frame / Math.Max(1, source.frames);
                    imported.SampleAnimation(go, Mathf.Min(time, imported.length));
                    var motion = sample.bones == null
                        ? new Dictionary<string, MotionBone>()
                        : sample.bones.ToDictionary(b => b.name, b => b);
                    foreach (var bone in skeleton.bones)
                    {
                        if (!motion.TryGetValue(bone.name, out var sourceBone)
                            || !transforms.TryGetValue(bone.name, out var actual)
                            || !UnitQuaternion(sourceBone.rotation_xyzw)) continue;
                        Quaternion expectedDelta = new Quaternion((float)sourceBone.rotation_xyzw[0],
                            (float)sourceBone.rotation_xyzw[1], (float)sourceBone.rotation_xyzw[2],
                            (float)sourceBone.rotation_xyzw[3]).normalized;

                        if (sourceBone.head_m != null && sourceBone.head_m.Length == 3)
                        {
                            float positionError = Vector3.Distance(actual.position, CritterFrame.Position(sourceBone.head_m));
                            if (positionError > 0.001f)
                                AddOnce(problems, $"post-import motion position {skeleton.skeleton_id}/{source.name}/{bone.name} frame {sample.frame}: {positionError:F6}m");
                        }
                        Quaternion actualDelta = ImportedMotionDelta(actual, bindLocal[bone.name]);
                        float rotationError = Quaternion.Angle(actualDelta, expectedDelta);
                        if (rotationError > 0.5f)
                            AddOnce(problems, $"post-import motion orientation {skeleton.skeleton_id}/{source.name}/{bone.name} frame {sample.frame}: {rotationError:F4}deg");
                        if (sourceBone.head_m != null && sourceBone.tail_m != null
                            && sourceBone.head_m.Length == 3 && sourceBone.tail_m.Length == 3)
                        {
                            Vector3 expectedDirection = CritterFrame.Position(sourceBone.tail_m) - CritterFrame.Position(sourceBone.head_m);
                            Quaternion recoveredBasis = FrameProbe.RecoverCanonicalBasis(actual.rotation);
                            float directionError = Vector3.Angle(recoveredBasis * Vector3.up, expectedDirection);
                            if (directionError > 0.5f)
                                AddOnce(problems, $"post-import motion tail {skeleton.skeleton_id}/{source.name}/{bone.name} frame {sample.frame}: {directionError:F4}deg");
                        }
                    }
                    if (sample.contacts == null) continue;
                    foreach (var contact in sample.contacts)
                    {
                        var branch = skeleton.FindBranch(contact.branch_id);
                        if (branch?.contacts == null || contact.position_m == null || contact.position_m.Length != 3) continue;
                        var definition = contact.contact_index >= 0 && contact.contact_index < branch.contacts.Length
                            ? branch.contacts[contact.contact_index] : null;
                        if (definition != null && definition.kind != contact.kind) definition = null;
                        if (definition == null || definition.bone_index < 0 || definition.bone_index >= branch.bone_names.Length) continue;
                        if (!transforms.TryGetValue(branch.bone_names[definition.bone_index], out var contactBone)) continue;
                        Vector3 local = new Vector3((float)definition.local_point_m[0], (float)definition.local_point_m[1],
                            (float)definition.local_point_m[2]);
                        local = Quaternion.Inverse(FrameProbe.FbxBoneBasis) * local;
                        float contactError = Vector3.Distance(contactBone.TransformPoint(local), CritterFrame.Position(contact.position_m));
                        if (contactError > 0.002f)
                            AddOnce(problems, $"post-import contact position {skeleton.skeleton_id}/{source.name}/{contact.branch_id} frame {sample.frame}: {contactError:F6}m");
                    }
                }
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }

        static void ValidateContactSchedule(SkeletonData skeleton, MotionClip clip, List<string> problems)
        {
            if (clip.samples == null || clip.samples.Length == 0) return;
            if (clip.contact_schedule == null)
            {
                problems.Add($"missing contact schedule {skeleton.skeleton_id}/{clip.name}");
                return;
            }
            int uniqueCount = clip.loop ? Math.Min(clip.frames, clip.samples.Length) : clip.samples.Length;
            double phaseTolerance = 1.0 / Math.Max(1, clip.frames) + 1e-6;
            bool locomotionSchedule = clip.name == "walk" || clip.name == "run";
            foreach (var declared in clip.contact_schedule)
            {
                var branch = skeleton.FindBranch(declared.branch_id);
                if (branch?.contacts == null || declared.contact_index < 0
                    || declared.contact_index >= branch.contacts.Length
                    || branch.contacts[declared.contact_index].kind != declared.kind
                    || declared.contact_id != declared.branch_id + ":" + declared.contact_index)
                {
                    problems.Add($"contact schedule identity {skeleton.skeleton_id}/{clip.name}/{declared.contact_id}");
                    continue;
                }
                // Action clips may release contacts through attack_plan.support_release; their
                // contact_schedule remains the gait summary. Exact sampled stance/onset applies
                // only to locomotion clips, where the schedule is authoritative.
                if (!locomotionSchedule) continue;
                // Non-support annotations (for example hands on a biped locomotion clip)
                // follow the evaluated limb and publish clip phase directly. Their gait phase
                // offset is descriptive and does not drive a planted contact trajectory.
                if (!declared.support) continue;
                for (int i = 0; i < uniqueCount; i++)
                {
                    var actual = FindContact(clip.samples[i], declared);
                    if (actual == null)
                    {
                        AddOnce(problems, $"contact sample missing {skeleton.skeleton_id}/{clip.name}/{declared.contact_id}");
                        continue;
                    }
                    double expectedPhase = UnitPhase(clip.samples[i].phase + declared.phase_offset);
                    if (CircularDistance(UnitPhase(actual.phase), expectedPhase) > 1e-5)
                        AddOnce(problems, $"contact sample phase {skeleton.skeleton_id}/{clip.name}/{declared.contact_id}: {actual.phase:F6}!={expectedPhase:F6}");

                    // Body and sliding contacts are continuously constrained by the motion
                    // solver. Their stance_fraction is gait metadata, rather than a sampled
                    // planted-state duty cycle.
                    if (declared.kind != "body" && declared.kind != "sliding")
                    {
                        bool expectedPlanted = expectedPhase <= declared.stance_fraction;
                        if (actual.planted != expectedPlanted)
                            AddOnce(problems, $"contact planted phase {skeleton.skeleton_id}/{clip.name}/{declared.contact_id} frame {clip.samples[i].frame}");
                    }
                }
                if (declared.kind == "body" || declared.kind == "sliding") continue;
                int planted = 0;
                for (int i = 0; i < uniqueCount; i++) if (IsPlanted(clip.samples[i], declared)) planted++;
                double actualStance = uniqueCount == 0 ? 0.0 : (double)planted / uniqueCount;
                if (Math.Abs(actualStance - declared.stance_fraction) > phaseTolerance)
                    AddOnce(problems, $"contact stance {skeleton.skeleton_id}/{clip.name}/{declared.contact_id}: {actualStance:F4}!={declared.stance_fraction:F4}");
                if (declared.stance_fraction <= 0.0 || declared.stance_fraction >= 1.0 || uniqueCount == 0) continue;
                double? onset = FirstPlantPhase(clip.samples, uniqueCount, declared);
                double expectedOnset = UnitPhase(-declared.phase_offset);
                if (!onset.HasValue || CircularDistance(onset.Value, expectedOnset) > phaseTolerance)
                    AddOnce(problems, $"contact phase {skeleton.skeleton_id}/{clip.name}/{declared.contact_id}: {(onset.HasValue ? onset.Value.ToString("F4") : "none")}!={expectedOnset:F4}");
            }
        }

        static void ValidateCorrespondingFootfalls(SkeletonData skeleton, MotionClip[] clips, List<string> problems)
        {
            var walk = Array.Find(clips, c => c.name == "walk");
            var run = Array.Find(clips, c => c.name == "run");
            if (walk?.samples == null || run?.samples == null) return;
            if (walk.contact_schedule == null || run.contact_schedule == null) return;
            foreach (var a in walk.contact_schedule)
            {
                if (!a.support || (a.kind != "foot" && a.kind != "hand")) continue;
                var b = Array.Find(run.contact_schedule, x => x.contact_id == a.contact_id);
                if (b == null || CircularDistance(a.phase_offset, b.phase_offset) > 1e-6)
                    problems.Add($"walk/run footfall phase {skeleton.skeleton_id}/{a.contact_id}: {a.phase_offset:F4}!={(b == null ? "missing" : b.phase_offset.ToString("F4"))}");
            }
        }

        static double? FirstPlantPhase(MotionSample[] samples, int count, ContactScheduleEntry contact)
        {
            bool previous = IsPlanted(samples[count - 1], contact);
            for (int i = 0; i < count; i++)
            {
                bool planted = IsPlanted(samples[i], contact);
                if (planted && !previous)
                {
                    return UnitPhase(samples[i].phase);
                }
                previous = planted;
            }
            return null;
        }

        static bool IsPlanted(MotionSample sample, ContactScheduleEntry declared) =>
            FindContact(sample, declared)?.planted == true;

        static MotionContact FindContact(MotionSample sample, ContactScheduleEntry declared) =>
            sample.contacts == null ? null : Array.Find(sample.contacts, c => c.branch_id == declared.branch_id
                && c.contact_index == declared.contact_index && c.kind == declared.kind);

        static double UnitPhase(double phase) => phase - Math.Floor(phase);

        static double CircularDistance(double a, double b)
        {
            double d = Math.Abs(a - b);
            return Math.Min(d, 1.0 - d);
        }
    }

    /// <summary>Checks the catalog->Unity frame conversion against the imported skeleton (docs/frame.md).</summary>
    public static class FrameProbe
    {
        public const float Tolerance = 1e-4f; // 0.1 mm
        public const float RotationToleranceDeg = 0.1f;
        /// <summary>
        /// Unity's Generic FBX importer keeps Blender bone +Y as the length axis but represents
        /// Blender/catalog +X and +Z as Unity bone -X and -Z. This is a coordinate
        /// reparameterization (a local 180-degree Y rotation), not a change to the anatomical frame.
        /// </summary>
        public static readonly Quaternion FbxBoneBasis = Quaternion.AngleAxis(180f, Vector3.up);

        public static Quaternion RecoverCanonicalBasis(Quaternion importedFbxBasis) =>
            importedFbxBasis * Quaternion.Inverse(FbxBoneBasis);

        /// <summary>Undo both RH->LH reflection and the FBX local-bone reparameterization.</summary>
        public static Quaternion RecoverCatalogMotionDelta(Quaternion importedDelta) =>
            new Quaternion(-importedDelta.x, -importedDelta.y, importedDelta.z, importedDelta.w).normalized;

        public static void Validate(GameObject skeletonModel, SkeletonData skeleton, List<string> problems)
        {
            Dictionary<string, Matrix4x4> rest;
            try { rest = SkeletonRest.Get(skeletonModel); }
            catch (Exception e) { problems.Add($"bind pose {skeleton.skeleton_id}: {e.Message}"); return; }
            foreach (var bone in skeleton.bones)
            {
                if (!rest.TryGetValue(bone.name, out var actual))
                {
                    problems.Add($"bone missing {skeleton.skeleton_id}/{bone.name}");
                    continue;
                }
                float positionError = Vector3.Distance(actual.GetPosition(), CritterFrame.Position(bone.head_m));
                if (positionError > Tolerance)
                    problems.Add($"bone position {skeleton.skeleton_id}/{bone.name}: {positionError:F6}m");
                Quaternion recovered = RecoverCanonicalBasis(actual.rotation);
                float rotationError = Quaternion.Angle(recovered, SkeletonPose.CanonicalBoneBasis(bone));
                if (rotationError > RotationToleranceDeg)
                    problems.Add($"bone orientation {skeleton.skeleton_id}/{bone.name}: {rotationError:F4}deg");
            }
            foreach (var branch in skeleton.branches)
            {
                if (branch.bone_names == null || branch.bone_names.Length == 0
                    || !rest.TryGetValue(branch.bone_names[0], out var root))
                {
                    problems.Add($"socket bone missing {skeleton.skeleton_id}/{branch.branch_id}");
                    continue;
                }
                float positionError = Vector3.Distance(root.GetPosition(), CritterFrame.Position(branch.snap.position_m));
                if (positionError > Tolerance)
                    problems.Add($"socket position {skeleton.skeleton_id}/{branch.branch_id}: {positionError:F6}m");
                var bone = Array.Find(skeleton.bones, b => b.name == branch.bone_names[0]);
                if (bone == null) continue;
                Quaternion snap = CritterFrame.Rotation(branch.snap.rotation_xyzw);
                Quaternion basis = SkeletonPose.CanonicalBoneBasis(bone);
                float directionError = Vector3.Angle(snap * Vector3.forward, basis * Vector3.up);
                float upError = Vector3.Angle(snap * Vector3.up, basis * Vector3.forward);
                float rotationError = Mathf.Max(directionError, upError);
                if (rotationError > RotationToleranceDeg)
                    problems.Add($"socket orientation {skeleton.skeleton_id}/{branch.branch_id}: {rotationError:F4}deg");
            }
        }

        /// <summary>Max distance between each branch's catalog snap and its root bone's true rest position.</summary>
        public static float MaxSnapError(GameObject skeletonModel, SkeletonData skeleton, out string worst)
        {
            var rest = SkeletonRest.Get(skeletonModel);
            float max = 0f;
            worst = "";
            foreach (var b in skeleton.branches)
            {
                if (!rest.TryGetValue(b.bone_names[0], out var m)) { worst = "missing " + b.bone_names[0]; return float.PositiveInfinity; }
                float e = Vector3.Distance(m.GetPosition(), CritterFrame.Position(b.snap.position_m));
                if (e > max) { max = e; worst = b.branch_id; }
            }
            return max;
        }

        /// <summary>A world point in the catalog frame = the model root's parent space (docs/frame.md).</summary>
        public static Vector3 CatalogFramePoint(Transform modelRoot, Vector3 world)
        {
            var toParent = Matrix4x4.TRS(modelRoot.localPosition, modelRoot.localRotation, modelRoot.localScale) * modelRoot.worldToLocalMatrix;
            return toParent.MultiplyPoint3x4(world);
        }
    }
}
