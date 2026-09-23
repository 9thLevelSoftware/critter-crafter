using System;
using System.IO;
using NUnit.Framework;
using UnityEngine;

namespace CritterCrafter.Tests
{
    public class GeneratorTests
    {
        const string GoldenDir = "Packages/com.ninthlevelsoftware.crittercrafter/Tests/Editor/Golden";

        [Serializable] class GoldenRow { public string pool_id; public long seed; public string canonical; }
        [Serializable] class GoldenDoc { public string generator; public GoldenRow[] rows; }

        static CatalogData LoadCatalog() =>
            JsonUtility.FromJson<CatalogData>(File.ReadAllText(Path.GetFullPath(GoldenDir + "/catalog.json")));

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
        public void LengthFitIsInclusiveIntegerRange()
        {
            Assert.IsTrue(RecipeGenerator.LengthFits(1000, 800));
            Assert.IsTrue(RecipeGenerator.LengthFits(800, 1000));
            Assert.IsFalse(RecipeGenerator.LengthFits(1000, 799));
            Assert.IsFalse(RecipeGenerator.LengthFits(799, 1000));
        }

        [Test]
        public void MatchesPythonGoldenRecipes()
        {
            var catalog = LoadCatalog();
            var doc = JsonUtility.FromJson<GoldenDoc>(File.ReadAllText(Path.GetFullPath(GoldenDir + "/recipes.json")));
            Assert.AreEqual(RecipeGenerator.Algorithm, doc.generator);
            Assert.GreaterOrEqual(doc.rows.Length, 500);
            foreach (var row in doc.rows)
            {
                var r = RecipeGenerator.Generate(catalog, row.pool_id, row.seed);
                Assert.AreEqual(row.canonical, r.Canonical(), $"{row.pool_id} seed {row.seed}");
                CollectionAssert.IsEmpty(RecipeValidator.Validate(catalog, r), $"{row.pool_id} seed {row.seed}");
            }
        }

        [Test]
        public void RecipeRoundTripsThroughJsonUtility()
        {
            var r = RecipeGenerator.Generate(LoadCatalog(), "any", 42);
            var back = JsonUtility.FromJson<CritterRecipe>(JsonUtility.ToJson(r));
            Assert.AreEqual(r.Canonical(), back.Canonical());
            Assert.AreEqual(42, back.seed);
        }

        [Test]
        public void ValidatorReportsStableCodes()
        {
            var catalog = LoadCatalog();
            var r = RecipeGenerator.Generate(catalog, "biped", 3);
            r.fills = Array.FindAll(r.fills, f => f.branch_id != "head");
            Assert.That(RecipeValidator.Validate(catalog, r), Has.Some.StartsWith("CC_REQUIRED_UNFILLED: head"));
            r = RecipeGenerator.Generate(catalog, "biped", 3);
            r.fills[0].part_id = "claw_v1";
            Assert.That(RecipeValidator.Validate(catalog, r), Has.Some.StartsWith("CC_PART_REJECTED"));
        }
    }
}
