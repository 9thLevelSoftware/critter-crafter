using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CritterCrafter.Editor;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace CritterCrafter.Tests
{
    /// <summary>
    /// Opt-in probe for a freshly exported FBX before a complete library build exists. Set
    /// CRITTER_PROTOTYPE_FBX and CRITTER_PROTOTYPE_CATALOG when invoking the Unity test runner.
    /// </summary>
    public class PrototypeImportTests
    {
        const string ImportFolder = "Assets/CritterLibraries/PrototypeProbe";
        const string ModelPath = ImportFolder + "/skeletons/prototype.fbx";
        const string ConnectorPath = ImportFolder + "/parts/fixture_connector.fbx";

        [TearDown]
        public void Cleanup()
        {
            AssetDatabase.DeleteAsset(ImportFolder);
        }

        [Test]
        public void ExportedSkeletonMatchesCatalogBindAndSocketFrames()
        {
            string fbx = Environment.GetEnvironmentVariable("CRITTER_PROTOTYPE_FBX");
            string catalogPath = Environment.GetEnvironmentVariable("CRITTER_PROTOTYPE_CATALOG");
            if (string.IsNullOrEmpty(fbx) || string.IsNullOrEmpty(catalogPath))
                Assert.Ignore("Set CRITTER_PROTOTYPE_FBX and CRITTER_PROTOTYPE_CATALOG to run the export probe.");
            Assert.That(File.Exists(fbx), Is.True, fbx);
            Assert.That(File.Exists(catalogPath), Is.True, catalogPath);

            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(ModelPath)));
            File.Copy(fbx, Path.GetFullPath(ModelPath), true);
            AssetDatabase.ImportAsset(ModelPath, ImportAssetOptions.ForceSynchronousImport);

            var model = AssetDatabase.LoadAssetAtPath<GameObject>(ModelPath);
            Assert.IsNotNull(model, "prototype FBX did not import");
            var importer = AssetImporter.GetAtPath(ModelPath) as ModelImporter;
            Assert.AreEqual(ModelImporterAnimationType.Generic, importer?.animationType);

            var catalog = JsonUtility.FromJson<CatalogData>(File.ReadAllText(catalogPath));
            var skeleton = catalog.FindSkeleton("biped_plantigrade_humanoid_balanced_v3");
            Assert.IsNotNull(skeleton);
            var problems = new List<string>();
            FrameProbe.Validate(model, skeleton, problems);
            var rest = SkeletonRest.Get(model);
            Assert.That(problems, Is.Empty, string.Join("\n", problems));

            Assert.That(rest.Keys, Is.EquivalentTo(skeleton.bones.Select(b => b.name)));
            var clips = AnimatorBuilder.LoadClips(ModelPath);
            CollectionAssert.IsSubsetOf(
                new[] { "idle", "walk", "run", "attack", "telegraph", "hit", "stun", "death" },
                clips.Keys.ToArray());
            var motionProblems = new List<string>();
            LibraryImporter.ValidateMotionArtifact(Path.GetDirectoryName(fbx), skeleton, catalog, model, clips, motionProblems);
            Assert.That(motionProblems, Is.Empty, string.Join("\n", motionProblems));
        }

        [Test]
        public void AsymmetricConnectorImportsAsOneNormalizedTwoBoneTwoMaterialRenderer()
        {
            string source = Environment.GetEnvironmentVariable("CRITTER_CONNECTOR_FIXTURE");
            if (string.IsNullOrEmpty(source))
                source = Path.GetFullPath("../../work/binding-fixture/connectors/fixture_connector.fbx");
            if (!File.Exists(source)) Assert.Ignore("binding connector fixture is not available");
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(ConnectorPath)));
            File.Copy(source, Path.GetFullPath(ConnectorPath), true);
            AssetDatabase.ImportAsset(ConnectorPath, ImportAssetOptions.ForceSynchronousImport);

            var model = AssetDatabase.LoadAssetAtPath<GameObject>(ConnectorPath);
            Assert.IsNotNull(model);
            var renderers = model.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            Assert.AreEqual(1, renderers.Length, "connector must normalize to one skinned renderer");
            var renderer = renderers[0];
            CollectionAssert.AreEqual(new[] { "b0", "b1" }, renderer.bones.Select(b => b.name).ToArray());
            Assert.AreEqual(2, renderer.sharedMesh.subMeshCount);
            bool mixedSeam = false;
            foreach (var weight in renderer.sharedMesh.boneWeights)
            {
                int influences = (weight.weight0 > 0f ? 1 : 0) + (weight.weight1 > 0f ? 1 : 0)
                    + (weight.weight2 > 0f ? 1 : 0) + (weight.weight3 > 0f ? 1 : 0);
                float sum = weight.weight0 + weight.weight1 + weight.weight2 + weight.weight3;
                Assert.LessOrEqual(influences, 2);
                Assert.That(sum, Is.EqualTo(1f).Within(1e-5f));
                mixedSeam |= weight.weight0 > 0.01f && weight.weight1 > 0.01f;
            }
            Assert.IsTrue(mixedSeam, "fixture must retain the asymmetric parent/child blend seam");
        }
    }
}
