using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CritterCrafter.Editor;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace CritterCrafter.Tests
{
    public class GeneratorTests
    {
        const string GoldenDir = "Packages/com.ninthlevelsoftware.crittercrafter/Tests/Editor/Golden";
        const string GoldenV3Dir = "Packages/com.ninthlevelsoftware.crittercrafter/Tests/Editor/GoldenV3";

        [Serializable] class GoldenRow { public string pool_id; public long seed; public string canonical; }
        [Serializable] class GoldenDoc { public string generator; public bool fixture_only_approval; public GoldenRow[] rows; }

        static string V3Path(string name)
        {
            string packaged = Path.GetFullPath(GoldenV3Dir + "/" + name);
            return File.Exists(packaged) ? packaged : Path.GetFullPath("../../tests/golden_v3/" + name);
        }

        static CatalogData Catalog(string partSide = "symmetric", string partHash = "hash")
        {
            var branch = new BranchData
            {
                branch_id = "limb_L", template = "limb3", binding_profile_id = "limb3_humanoid",
                binding_profile_version = "1.0.0", binding_profile_hash = "hash", parent_branch = "",
                attach_bone = "root", bone_names = new[] { "limb_L_b0", "limb_L_b1", "limb_L_b2" },
                length_m = 1.0, length_mm = 1000, girth_m = 0.2, girth_mm = 200, side = "L", required = true,
                optional_fill_pct = 100, accepts = new Accepts { categories = new[] { "limb" }, templates = Array.Empty<string>(), tags_any = Array.Empty<string>() },
                connector_size_class = "", snap = new SnapData { position_m = new[] { 0.0, 0.0, 0.0 }, rotation_xyzw = new[] { 0.0, 0.0, 0.0, 1.0 } },
            };
            var skeleton = new SkeletonData
            {
                skeleton_id = "approved_skeleton", family = "biped", status = "approved", symmetry_pct = 100,
                bones = new[] { Bone("root"), Bone("limb_L_b0"), Bone("limb_L_b1"), Bone("limb_L_b2") },
                branches = new[] { branch }, neutral_pose = new NeutralPoseData
                {
                    root_offset_m = new[] { 0.0, 0.0, 0.0 },
                    rotations = new[] { Neutral("root"), Neutral("limb_L_b0"), Neutral("limb_L_b1"), Neutral("limb_L_b2") },
                },
                asset = new SkeletonAssetInfo { clips = Array.Empty<ClipInfo>() },
            };
            var part = new PartData
            {
                part_id = "limb_part", category = "limb", template = "limb3", binding_profile_id = "limb3_humanoid",
                binding_profile_version = "1.0.0", binding_profile_hash = partHash, side = partSide,
                inventory_kind = "production", status = "approved", species_tags = Array.Empty<string>(),
                length_m = 1.0, length_mm = 1000, girth_m = 0.2, girth_mm = 200, max_triangles = 100,
            };
            return new CatalogData
            {
                schema_version = "3.0.0", document_kind = "critter_library", library_id = "test_library", version = "0.2.0",
                generator = new GeneratorInfo { algorithm = "cc-gen-3", rng = "splitmix64" },
                limits = new CatalogLimits { max_triangles = 30000, max_bones = 120, max_parts = 16, max_influences = 4 },
                gait_profiles = Array.Empty<GaitProfile>(), binding_profiles = Array.Empty<BindingProfile>(), branch_templates = Array.Empty<BranchTemplate>(),
                skeletons = new[] { skeleton }, parts = new[] { part },
                pools = new[] { new PoolData { pool_id = "any", families = new[] { "biped" }, skeleton_ids = Array.Empty<string>() } },
            };
        }

        static BoneData Bone(string name) => new BoneData
        {
            name = name, parent = "", head_m = new[] { 0.0, 0.0, 0.0 }, tail_m = new[] { 0.0, 1.0, 0.0 }, up_m = new[] { 0.0, 0.0, 1.0 },
        };

        static NeutralRotationData Neutral(string name) => new NeutralRotationData
        { bone_name = name, rotation_xyzw = new[] { 0.0, 0.0, 0.0, 1.0 } };

        [Test]
        public void SplitMix64MatchesReferenceVector()
        {
            var r = new CritterRng(1234567);
            Assert.AreEqual(6457827717110365317UL, r.Next());
            Assert.AreEqual(3203168211198807973UL, r.Next());
            Assert.AreEqual(9817491932198370423UL, r.Next());
        }

        [Test]
        public void SeedZeroBehavesAsSeedOne() => Assert.AreEqual(new CritterRng(1).Next(), new CritterRng(0).Next());

        [Test]
        public void LengthAndGirthFitsInclusiveIntegerRange()
        {
            Assert.IsTrue(RecipeGenerator.RatioFits(1000, 800));
            Assert.IsTrue(RecipeGenerator.RatioFits(800, 1000));
            Assert.IsFalse(RecipeGenerator.RatioFits(1000, 799));
            Assert.IsFalse(RecipeGenerator.RatioFits(799, 1000));

            var branch = new BranchData { length_mm = 1000, girth_mm = 500 };
            Assert.IsTrue(RecipeGenerator.GirthFits(
                new PartData { length_mm = 800, girth_mm = 360 }, branch),
                "360mm scaled uniformly by 1.25 is exactly the lower 10% girth boundary");
            Assert.IsTrue(RecipeGenerator.GirthFits(
                new PartData { length_mm = 800, girth_mm = 440 }, branch),
                "440mm scaled uniformly by 1.25 is exactly the upper 10% girth boundary");
            Assert.IsFalse(RecipeGenerator.GirthFits(
                new PartData { length_mm = 800, girth_mm = 359 }, branch));
            Assert.IsFalse(RecipeGenerator.GirthFits(
                new PartData { length_mm = 800, girth_mm = 441 }, branch));
        }

        [Test]
        public void GeneratesAndRoundTripsV3Recipe()
        {
            var r = RecipeGenerator.Generate(Catalog(), "any", 42);
            var back = JsonUtility.FromJson<CritterRecipe>(JsonUtility.ToJson(r));
            Assert.AreEqual("3.0.0", back.schema_version);
            Assert.AreEqual("cc-gen-3", back.generator);
            Assert.AreEqual(r.Canonical(), back.Canonical());
            CollectionAssert.IsEmpty(RecipeValidator.Validate(Catalog(), back));
        }

        [Test]
        public void MatchesPythonGoldenV3RecipesWithFixtureOnlyApproval()
        {
            var catalog = JsonUtility.FromJson<CatalogData>(File.ReadAllText(V3Path("catalog.json")));
            var doc = JsonUtility.FromJson<GoldenDoc>(File.ReadAllText(V3Path("recipes.json")));
            Assert.IsTrue(doc.fixture_only_approval);
            Assert.AreEqual(RecipeGenerator.Algorithm, doc.generator);
            Assert.GreaterOrEqual(doc.rows.Length, 700);
            Assert.AreEqual(39, catalog.skeletons.Length);
            Assert.IsTrue(Array.TrueForAll(catalog.skeletons, s => s.status == "draft"));
            foreach (var skeleton in catalog.skeletons) skeleton.status = "approved";
            foreach (var row in doc.rows)
            {
                var recipe = RecipeGenerator.Generate(catalog, row.pool_id, row.seed);
                Assert.AreEqual(row.canonical, recipe.Canonical(), $"{row.pool_id} seed {row.seed}");
                CollectionAssert.IsEmpty(RecipeValidator.Validate(catalog, recipe), $"{row.pool_id} seed {row.seed}");
            }
        }

        [Test]
        public void GoldenV3CatalogHasExactCompiledBindingAndSocketContracts()
        {
            var catalog = JsonUtility.FromJson<CatalogData>(File.ReadAllText(V3Path("catalog.json")));
            CollectionAssert.IsEmpty(LibraryImporter.ValidateCatalogContract(catalog, requireBuiltAssets: false));
        }

        [Test]
        public void LegacyV2CatalogIsExplicitlyRejectedWithoutChangingFrozenGolden()
        {
            var legacy = JsonUtility.FromJson<CatalogData>(File.ReadAllText(Path.GetFullPath(GoldenDir + "/catalog.json")));
            var ex = Assert.Throws<GenerationException>(() => RecipeGenerator.Generate(legacy, "any", 1));
            Assert.AreEqual("CC_GEN_UNSUPPORTED_CATALOG", ex.Code);
        }

        [Test]
        public void PhaseDrivenStunIsReachableFromAnyState()
        {
            const string dir = "Assets/TempAnimatorBuilderTest";
            Directory.CreateDirectory(dir);
            string path = dir + "/phase_driven.controller";
            try
            {
                var clips = new Dictionary<string, AnimationClip>();
                foreach (var name in new[] { "idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death" })
                    clips[name] = new AnimationClip { name = name };
                var skeleton = new SkeletonData
                {
                    locomotion = new LocomotionData { mode = "slide", travel_per_cycle_m = 1.0 },
                };
                var ctrl = AnimatorBuilder.Build(path, clips, skeleton);
                var sm = ctrl.layers[0].stateMachine;
                Assert.IsTrue(sm.anyStateTransitions.Any(t => t.destinationState != null && t.destinationState.name == "Stun"),
                    "Stunned must be reachable from Locomotion as well as Idle");
                var stun = sm.states.First(s => s.state.name == "Stun").state;
                Assert.IsTrue(stun.transitions.Any(t => t.destinationState != null && t.destinationState.name == "Idle"));
            }
            finally
            {
                AssetDatabase.DeleteAsset(dir);
            }
        }

        [Test]
        public void RejectsUnknownPoolAndSkeletonOutsideThePool()
        {
            var catalog = Catalog();
            var recipe = RecipeGenerator.Generate(catalog, "any", 7);
            recipe.pool_id = "does_not_exist";
            Assert.That(RecipeValidator.Validate(catalog, recipe), Has.Member("CC_UNKNOWN_POOL: does_not_exist"));

            recipe.pool_id = "any";
            catalog.skeletons[0].family = "hexapod";
            Assert.That(RecipeValidator.Validate(catalog, recipe),
                Has.Member("CC_POOL_SKELETON: any=approved_skeleton"));

            catalog.pools[0].skeleton_ids = new[] { "approved_skeleton" };
            CollectionAssert.IsEmpty(RecipeValidator.Validate(catalog, recipe));
        }

        [Test]
        public void ReviewPoolSkipsCatalogPoolCheckOnlyWhenAllowReview()
        {
            var catalog = Catalog();
            catalog.skeletons[0].status = "draft";
            var recipe = RecipeGenerator.Generate(
                Catalog(), "any", 7);
            recipe.pool_id = "review_approved_skeleton";
            recipe.skeleton_id = "approved_skeleton";
            Assert.That(RecipeValidator.Validate(catalog, recipe), Has.Member("CC_UNKNOWN_POOL: review_approved_skeleton"));
            Assert.That(RecipeValidator.Validate(catalog, recipe), Has.Member("CC_SKELETON_NOT_APPROVED: approved_skeleton"));
            CollectionAssert.IsEmpty(RecipeValidator.Validate(catalog, recipe, allowReview: true));
        }

        [Test]
        public void RejectsLibraryAndBindingIdentityMismatch()
        {
            var catalog = Catalog();
            var recipe = RecipeGenerator.Generate(catalog, "any", 3);
            recipe.library_version = "9.9.9";
            Assert.That(RecipeValidator.Validate(catalog, recipe), Has.Some.StartsWith("CC_LIBRARY_VERSION"));

            catalog = Catalog(partHash: "different");
            Assert.Throws<GenerationException>(() => RecipeGenerator.Generate(catalog, "any", 3));
            Assert.IsFalse(RecipeGenerator.PartAccepted(catalog.parts[0], catalog.skeletons[0].branches[0]));
        }

        [Test]
        public void AsymmetricSideMustMatchWhileSymmetricFitsEitherSide()
        {
            var branch = Catalog().skeletons[0].branches[0];
            Assert.IsFalse(RecipeGenerator.PartAccepted(Catalog("R").parts[0], branch));
            Assert.IsTrue(RecipeGenerator.PartAccepted(Catalog("symmetric").parts[0], branch));
        }

        [Test]
        public void DraftInventoryIsRejectedByDefault()
        {
            var catalog = Catalog();
            catalog.parts[0].status = "draft";
            Assert.Throws<GenerationException>(() => RecipeGenerator.Generate(catalog, "any", 1));
            catalog = Catalog();
            catalog.skeletons[0].status = "draft";
            Assert.Throws<GenerationException>(() => RecipeGenerator.Generate(catalog, "any", 1));
        }

        [Test]
        public void ConnectorRequiresExactVersionedParentChildContract()
        {
            var branch = Catalog().skeletons[0].branches[0];
            branch.connector_interface = new BranchConnectorInterface
            {
                interface_id = "skinned_parent_child", interface_version = "1.0.0",
                parent_role = "parent", child_role = "child", parent_bone = branch.attach_bone,
                child_bone = branch.bone_names[0], position_m = new[] { 0.0, 0.0, 0.0 },
                rotation_xyzw = new[] { 0.0, 0.0, 0.0, 1.0 },
            };
            var connector = new PartData
            {
                category = "connector", binding_profile_id = branch.binding_profile_id,
                binding_profile_version = branch.binding_profile_version, binding_profile_hash = branch.binding_profile_hash,
                side = branch.side, girth_mm = branch.girth_mm, connector_radius_m = branch.girth_mm / 2000.0,
                connector_span_m = new[] { -0.1, 0.1 },
                connector_interface = new PartConnectorInterface
                {
                    interface_id = "skinned_parent_child", interface_version = "1.0.0", max_influences = 2,
                    weights_normalized = true, position_m = new[] { 0.0, 0.0, 0.0 },
                    rotation_xyzw = new[] { 0.0, 0.0, 0.0, 1.0 },
                    bone_groups = new[]
                    {
                        new ConnectorBoneGroup { group = "b0", role = "parent" },
                        new ConnectorBoneGroup { group = "b1", role = "child" },
                    },
                },
            };
            Assert.IsTrue(RecipeGenerator.ConnectorAccepted(connector, branch));
            connector.connector_interface.interface_version = "2.0.0";
            Assert.IsFalse(RecipeGenerator.ConnectorAccepted(connector, branch));
            connector.connector_interface.interface_version = "1.0.0";
            connector.girth_mm++;
            Assert.IsFalse(RecipeGenerator.ConnectorAccepted(connector, branch));
        }
    }
}
