using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CritterCrafter.Editor;
using NUnit.Framework;
using UnityEditor.Animations;
using UnityEngine;

namespace CritterCrafter.Tests
{
    public class BakedCreatureTests
    {
        readonly List<UnityEngine.Object> objects = new List<UnityEngine.Object>();
        string temporary;
        CritterLibrary library;
        CatalogData catalog;
        CritterRecipe recipe;
        ImportedBakedCreature imported;
        BakedCreatureIndex index;
        TextAsset catalogText;

        T Keep<T>(T value) where T : UnityEngine.Object { objects.Add(value); return value; }

        [SetUp]
        public void SetUp()
        {
            temporary = Path.Combine(Path.GetTempPath(), "critter_bake_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(temporary);
            var branch = new BranchData { branch_id = "limb", template = "limb3", binding_profile_id = "profile",
                binding_profile_version = "1.0.0", binding_profile_hash = "profile_hash", attach_bone = "root",
                bone_names = new[] { "limb_b0" }, length_mm = 1000, girth_mm = 200, length_m = 1, girth_m = .2,
                side = "symmetric", required = true, optional_fill_pct = 100,
                accepts = new Accepts { categories = new[] { "limb" }, templates = Array.Empty<string>(), tags_any = Array.Empty<string>() },
                snap = new SnapData { position_m = new[] { 0.0, 0.0, 0.0 }, rotation_xyzw = new[] { 0.0, 0.0, 0.0, 1.0 } } };
            BoneData Bone(string name, string parent) => new BoneData { name = name, parent = parent,
                head_m = new[] { 0.0, 0.0, 0.0 }, tail_m = new[] { 0.0, 1.0, 0.0 }, up_m = new[] { 0.0, 0.0, 1.0 } };
            var skeleton = new SkeletonData { skeleton_id = "fixture", family = "biped", status = "approved",
                bones = new[] { Bone("root", ""), Bone("limb_b0", "root") }, branches = new[] { branch },
                neutral_pose = new NeutralPoseData { root_offset_m = new[] { 0.0, 0.0, 0.0 },
                    rotations = Array.Empty<NeutralRotationData>() }, asset = new SkeletonAssetInfo { clips = Array.Empty<ClipInfo>() } };
            var part = new PartData { part_id = "part", template = "limb3", category = "limb", status = "approved",
                inventory_kind = "production", side = "symmetric", binding_profile_id = "profile", binding_profile_version = "1.0.0",
                binding_profile_hash = "profile_hash", length_mm = 1000, girth_mm = 200, length_m = 1, girth_m = .2,
                dimensions_m = new[] { .2, .2, 1.0 }, max_triangles = 1, max_material_slots = 1, species_tags = Array.Empty<string>() };
            catalog = new CatalogData { schema_version = "3.0.0", document_kind = "critter_library", frame = "gltf_rh_yup_zfwd_m",
                library_id = "fixture_library", version = "0.3.0", generator = new GeneratorInfo { algorithm = "cc-gen-3", rng = "splitmix64" },
                limits = new CatalogLimits { max_parts = 16, max_bones = 120, max_triangles = 30000, max_influences = 4 },
                skeletons = new[] { skeleton }, parts = new[] { part },
                pools = new[] { new PoolData { pool_id = "any", families = new[] { "biped" }, skeleton_ids = Array.Empty<string>() } } };
            recipe = RecipeGenerator.Generate(catalog, "any", 123);
            catalogText = Keep(new TextAsset(JsonUtility.ToJson(catalog)));
            File.WriteAllBytes(Path.Combine(temporary, "catalog.json"), catalogText.bytes);
            var skelModel = Keep(new GameObject("SkeletonModel"));
            var rootBone = new GameObject("root"); rootBone.transform.SetParent(skelModel.transform, false);
            var limb = new GameObject("limb_b0"); limb.transform.SetParent(rootBone.transform, false);
            var proxyObject = new GameObject(SkeletonRest.ProxyName); proxyObject.transform.SetParent(skelModel.transform, false);
            var proxy = proxyObject.AddComponent<SkinnedMeshRenderer>();
            proxy.sharedMesh = Triangle(); proxy.bones = new[] { limb.transform };
            var controller = Keep(new AnimatorController());
            controller.AddLayer("Base Layer");
            controller.AddParameter("PlaybackRate", AnimatorControllerParameterType.Float);
            var partModel = Keep(new GameObject("PartModel"));
            var partBone = new GameObject("b0"); partBone.transform.SetParent(partModel.transform, false);
            var partRenderer = partModel.AddComponent<SkinnedMeshRenderer>();
            partRenderer.sharedMesh = Triangle(); partRenderer.bones = new[] { partBone.transform };
            var bodyModel = Keep(new GameObject("BodyModel"));
            var bodyBone = new GameObject("limb_b0"); bodyBone.transform.SetParent(bodyModel.transform, false);
            var bodyRenderer = bodyModel.AddComponent<SkinnedMeshRenderer>();
            bodyRenderer.sharedMesh = Triangle(); bodyRenderer.bones = new[] { bodyBone.transform };
            var material = Keep(new Material(Shader.Find("Standard")));
            string key = BakedCreatureIdentity.Key(recipe);
            string prefix = "baked_creatures/" + key + "/";
            Directory.CreateDirectory(Path.Combine(temporary, prefix));
            var inputs = new[] { Input("catalog.json", catalogText.bytes), Input(prefix + "material.json", System.Text.Encoding.UTF8.GetBytes("{\"opaque\":true}")) };
            var data = new BakedCreatureData { key = key, skeleton_id = recipe.skeleton_id, composition = recipe.Canonical(), inputs = inputs,
                source_sha256 = BakedCreatureIdentity.ManifestHash(inputs), producer_sha256 = new string('a', 64),
                material_sha256 = inputs[1].sha256, fbx = prefix + key + ".fbx", albedo_png = prefix + key + "_albedo.png" };
            data.asset_sha256 = Input(data.fbx, new byte[] { 1, 2, 3 }).sha256;
            data.texture_sha256 = Input(data.albedo_png, new byte[] { 4, 5, 6 }).sha256;
            index = new BakedCreatureIndex { schema_version = "baked-creatures-1", library_id = catalog.library_id,
                library_version = catalog.version, source_catalog_sha256 = inputs[0].sha256, entries = new[] { data } };
            imported = new ImportedBakedCreature { data = data, model = bodyModel, material = material,
                importedKey = data.key, importedSourceSha256 = data.source_sha256, importedProducerSha256 = data.producer_sha256,
                sourceCatalogSha256 = index.source_catalog_sha256, importedAssetSha256 = data.asset_sha256,
                importedTextureSha256 = data.texture_sha256, importedMaterialSha256 = data.material_sha256 };
            library = Keep(ScriptableObject.CreateInstance<CritterLibrary>());
            library.EditorSetContents(catalogText, new[] { new CritterLibrary.SkeletonEntry { skeletonId = "fixture", model = skelModel, controller = controller } },
                new[] { new CritterLibrary.PartEntry { partId = "part", model = partModel, material = material } }, index.source_catalog_sha256, index, new[] { imported });
            SaveIndex();
        }

        Mesh Triangle()
        {
            var mesh = Keep(new Mesh { vertices = new[] { new Vector3(-.1f, .5f, 0), new Vector3(.1f, .5f, 0), new Vector3(0, 1, 0) },
                triangles = new[] { 0, 1, 2 }, bindposes = new[] { Matrix4x4.identity },
                boneWeights = Enumerable.Repeat(new BoneWeight { boneIndex0 = 0, weight0 = 1 }, 3).ToArray() });
            mesh.RecalculateBounds(); return mesh;
        }
        BakedCreatureInput Input(string path, byte[] bytes)
        {
            File.WriteAllBytes(Path.Combine(temporary, path), bytes);
            return new BakedCreatureInput { path = path, sha256 = BakedCreatureIdentity.Hash(bytes) };
        }
        void SaveIndex() => File.WriteAllText(Path.Combine(temporary, "baked_creatures/index.json"), JsonUtility.ToJson(index));
        AssemblyOptions Options(bool fallback = false) { var value = AssemblyOptions.Default; value.fallbackOnInvalid = fallback; return value; }
        AssembledCreature Spawn(bool preferBaked = true, bool fallback = false)
        {
            var go = Keep(new DefaultCreatureVisualFactory(library, Options(fallback), preferBaked).Build(new CreatureSpawnRequest { recipe = recipe, seed = 999 }));
            return go.GetComponent<AssembledCreature>();
        }

        [TearDown]
        public void TearDown()
        {
            CreatureAssembler.ClearCache();
            foreach (var value in objects.AsEnumerable().Reverse()) if (value != null) UnityEngine.Object.DestroyImmediate(value);
            objects.Clear();
            Directory.Delete(temporary, true);
        }

        [TestCase("scale")][TestCase("profile")][TestCase("generator")][TestCase("approval")][TestCase("version")]
        public void IndexedCompositionNeverBypassesRecipeValidation(string damage)
        {
            if (damage == "scale") recipe.fills[0].girth_scale = 1.01;
            if (damage == "profile") recipe.fills[0].binding_profile_hash = "forged";
            if (damage == "generator") recipe.generator = "forged";
            if (damage == "approval") library.Catalog.skeletons[0].status = "draft";
            if (damage == "version") recipe.library_version = "0.2.0";
            Assert.Throws<AssemblyException>(() => Spawn());
            Assert.IsTrue(Spawn(fallback: true).IsFallback);
        }

        [Test]
        public void RequestedRecipeAndSeedSurviveBakedHitAndLiveOptOut()
        {
            recipe.recipe_id = "saved_authority"; recipe.seed = 789;
            var baked = Spawn(); var live = Spawn(preferBaked: false);
            Assert.AreEqual("BakedBody", baked.Renderers.Single().renderer.name);
            Assert.AreNotEqual("BakedBody", live.Renderers.Single().renderer.name);
            foreach (var creature in new[] { baked, live })
            {
                Assert.AreEqual(789, creature.Recipe.seed);
                Assert.AreEqual("saved_authority", creature.Recipe.recipe_id);
                Assert.AreEqual("0.3.0", creature.Recipe.library_version);
                Assert.AreEqual(recipe.Canonical(), creature.Recipe.Canonical());
                Assert.AreEqual(1, creature.GetComponentsInChildren<Animator>().Length);
                Assert.IsNotNull(creature.GetComponent<CapsuleCollider>());
                Assert.AreEqual(live.BindBoundsLocal, creature.BindBoundsLocal);
                Assert.AreEqual(live.ImportedRest["limb_b0"], creature.ImportedRest["limb_b0"]);
            }
            CollectionAssert.AreEqual(live.Renderers[0].renderer.sharedMesh.bindposes, baked.Renderers[0].renderer.sharedMesh.bindposes);
        }

        [Test]
        public void MissingEntryUsesLiveButStaleIndexedHitUsesFailurePolicy()
        {
            var skeletons = new[] { library.FindSkeleton("fixture") };
            var parts = new[] { library.FindPart("part") };
            library.EditorSetContents(catalogText, skeletons, parts, index.source_catalog_sha256, index, Array.Empty<ImportedBakedCreature>());
            Assert.AreNotEqual("BakedBody", Spawn().Renderers.Single().renderer.name);
            library.EditorSetContents(catalogText, skeletons, parts, index.source_catalog_sha256, index, new[] { imported });
            imported.data.asset_sha256 = new string('c', 64);
            Assert.Throws<AssemblyException>(() => Spawn());
            Assert.IsTrue(Spawn(fallback: true).IsFallback);
            Assert.IsFalse(Spawn(preferBaked: false).IsFallback);
        }

        [Test]
        public void ModifiedIndexedKeyCannotMasqueradeAsMissingBake()
        {
            imported.data.key = new string('b', 64);
            Assert.Throws<AssemblyException>(() => Spawn());
            Assert.IsTrue(Spawn(fallback: true).IsFallback);
        }

        [Test]
        public void ApprovalOnlyCloneRetainsOriginalCatalogIdentityAndBake()
        {
            library.Catalog.skeletons[0].status = "draft";
            var clone = Keep(library.EditorCreateApprovedSkeletonClone());
            Assert.AreEqual(library.SourceCatalogSha256, clone.SourceCatalogSha256);
            Assert.AreSame(imported, clone.FindBaked(recipe));
            recipe.pool_id = "review_" + recipe.skeleton_id;
            Assert.Throws<AssemblyException>(() => clone.FindBaked(recipe));
            Assert.AreSame(imported, clone.FindBaked(recipe, true));
        }

        [TestCase("reference", "draft")][TestCase("reference", "rejected")][TestCase("production", "draft")]
        public void ProofReviewCloneAllowsRequestedBakeButRejectsUnselectedProduction(string inventory, string unselectedStatus)
        {
            catalog.parts[0].inventory_kind = inventory;
            catalog.parts[0].status = inventory == "reference" ? "reference" : "draft";
            catalog.skeletons[0].status = "draft";
            var unselected = JsonUtility.FromJson<PartData>(JsonUtility.ToJson(catalog.parts[0]));
            unselected.part_id = "unused";
            unselected.inventory_kind = "production";
            unselected.status = unselectedStatus;
            catalog.parts = new[] { catalog.parts[0], unselected };
            var sourceText = Keep(new TextAsset(JsonUtility.ToJson(catalog)));
            var catalogInput = Input("catalog.json", sourceText.bytes);
            imported.data.inputs[0] = catalogInput;
            index.source_catalog_sha256 = catalogInput.sha256;
            imported.sourceCatalogSha256 = catalogInput.sha256;
            imported.data.source_sha256 = BakedCreatureIdentity.ManifestHash(imported.data.inputs);
            imported.importedSourceSha256 = imported.data.source_sha256;
            library.EditorSetContents(sourceText, new[] { library.FindSkeleton("fixture") }, new[] { library.FindPart("part") },
                catalogInput.sha256, index, new[] { imported });
            var clone = Keep(CreatureBakeProof.CreateReviewLibrary(library, recipe));
            CollectionAssert.IsEmpty(RecipeValidator.Validate(clone.Catalog, recipe));
            Assert.AreSame(imported, clone.FindBaked(recipe), "requested review recipe must reach its indexed bake");
            Assert.Throws<AssemblyException>(() => library.FindBaked(recipe), "review must not approve the original library");
            var otherRecipe = JsonUtility.FromJson<CritterRecipe>(JsonUtility.ToJson(recipe));
            otherRecipe.fills[0].part_id = unselected.part_id;
            CollectionAssert.Contains(RecipeValidator.Validate(clone.Catalog, otherRecipe), "CC_PART_NOT_APPROVED: unused");
            Assert.Throws<AssemblyException>(() => clone.FindBaked(otherRecipe), "unselected production cannot use review approval");
        }

        [TestCase("asset")][TestCase("texture")][TestCase("material")][TestCase("input")][TestCase("catalog")][TestCase("duplicate")][TestCase("escape")]
        public void ImportRejectsStaleDuplicateOrEscapingEntries(string damage)
        {
            Assert.AreEqual(index.entries[0].key, CreatureBaker.ValidateIndex(temporary, catalog).entries[0].key);
            if (damage == "asset") File.AppendAllText(Path.Combine(temporary, imported.data.fbx), "tamper");
            if (damage == "texture") imported.data.texture_sha256 = new string('a', 64);
            if (damage == "material") imported.data.material_sha256 = new string('a', 64);
            if (damage == "input") imported.data.inputs[0].sha256 = new string('a', 64);
            if (damage == "catalog") File.AppendAllText(Path.Combine(temporary, "catalog.json"), " ");
            if (damage == "duplicate") index.entries = new[] { imported.data, imported.data };
            if (damage == "escape") imported.data.inputs[1].path = "../outside.json";
            SaveIndex();
            Assert.Throws<InvalidDataException>(() => CreatureBaker.ValidateIndex(temporary, catalog));
        }

        [TestCase("catalog.json:payload")][TestCase("C:/catalog.json")][TestCase("../catalog.json")]
        [TestCase("folder/.. /catalog.json")][TestCase("folder\\catalog.json")][TestCase("catalog.json\0payload")]
        public void PortablePathsRejectWindowsAliasesAndEscapes(string path)
        {
            Assert.Throws<InvalidDataException>(() => CreatureBaker.ConfinedPath(temporary, path));
        }

        [Test, Platform("Win")]
        public void ImportRejectsJunctionToExternalInput()
        {
            string outside = temporary + "_outside";
            string junction = Path.Combine(temporary, "junction");
            Directory.CreateDirectory(outside);
            try
            {
                File.WriteAllText(Path.Combine(outside, "input.json"), "{}");
                var start = new System.Diagnostics.ProcessStartInfo("cmd.exe",
                    "/c mklink /J \"" + junction + "\" \"" + outside + "\"")
                { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
                using (var process = System.Diagnostics.Process.Start(start))
                {
                    string stdout = process.StandardOutput.ReadToEnd();
                    string stderr = process.StandardError.ReadToEnd();
                    process.WaitForExit();
                    Assert.AreEqual(0, process.ExitCode, stdout + stderr);
                }
                Assert.Throws<InvalidDataException>(() => CreatureBaker.ConfinedPath(temporary, "junction/input.json"));
            }
            finally
            {
                if (Directory.Exists(junction)) Directory.Delete(junction);
                Directory.Delete(outside, true);
            }
        }

        [Test]
        public void VersionIsPartOfVisualIdentityButSeedAndIdAreNot()
        {
            string original = BakedCreatureIdentity.Key(recipe);
            recipe.recipe_id = "other"; recipe.seed = 99;
            Assert.AreEqual(original, BakedCreatureIdentity.Key(recipe));
            recipe.library_version = "0.2.0";
            Assert.AreNotEqual(original, BakedCreatureIdentity.Key(recipe));
        }
    }
}
