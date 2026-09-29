using System;
using System.IO;
using NUnit.Framework;
using UnityEngine;

namespace CritterCrafter.Tests
{
    /// <summary>
    /// CreatureLocomotion.Build must reproduce the Python build-1 model (locomotion/build.py) for the
    /// reference recipe of every skeleton and for generated pool recipes, including the real Meshy parts.
    /// </summary>
    public class CreatureLocomotionGoldenTests
    {
        const string GoldenV3Dir = "Packages/com.ninthlevelsoftware.crittercrafter/Tests/Editor/GoldenV3";
        const double Tolerance = 1e-6;

        [Serializable] class Row
        {
            public string skeleton_id, recipe_id;
            public string[] branch_ids, part_ids;
            public double[] length_scales, girth_scales;
            public double mass_kg, performance, v_walk, v_run, v_max, cadence_max;
            public double[] com_m;
        }

        [Serializable] class Doc
        {
            public string model;
            public Row[] rows;
        }

        static T Load<T>(string name) => JsonUtility.FromJson<T>(File.ReadAllText(Path.GetFullPath(GoldenV3Dir + "/" + name)));

        [Test]
        public void MatchesPythonBuildModel()
        {
            var catalog = Load<CatalogData>("catalog.json");
            var doc = Load<Doc>("creatures.json");
            Assert.AreEqual(CreatureLocomotion.Model, doc.model);
            Assert.Greater(doc.rows.Length, 0);
            foreach (var row in doc.rows)
            {
                var skeleton = catalog.FindSkeleton(row.skeleton_id);
                var recipe = new CritterRecipe { recipe_id = row.recipe_id, skeleton_id = row.skeleton_id };
                recipe.fills = new RecipeFill[row.part_ids.Length];
                for (int i = 0; i < recipe.fills.Length; i++)
                {
                    var branch = skeleton.FindBranch(row.branch_ids[i]);
                    recipe.fills[i] = new RecipeFill
                    {
                        branch_id = branch.branch_id, part_id = row.part_ids[i],
                        binding_profile_id = branch.binding_profile_id,
                        length_scale = row.length_scales[i], girth_scale = row.girth_scales[i],
                    };
                }
                var block = CreatureLocomotion.Build(catalog, recipe);
                string at = row.recipe_id;
                Assert.AreEqual(row.mass_kg, block.build.mass_kg, Tolerance, at + " mass");
                for (int k = 0; k < 3; k++) Assert.AreEqual(row.com_m[k], block.build.com_m[k], Tolerance, at + " com");
                Assert.AreEqual(row.performance, block.build.performance, Tolerance, at + " performance");
                Assert.AreEqual(row.v_walk, block.v_walk_mps, Tolerance, at + " v_walk");
                Assert.AreEqual(row.v_run, block.v_run_mps, Tolerance, at + " v_run");
                Assert.AreEqual(row.v_max, block.v_max_mps, Tolerance, at + " v_max");
                Assert.AreEqual(row.cadence_max, block.cadence_max_hz, Tolerance, at + " cadence");
                Assert.AreEqual(block.v_run_mps, block.move_speed_mps, at + " move speed");
                Assert.Less(0.0, block.v_walk_mps, at);
                Assert.Less(block.v_walk_mps, block.v_run_mps, at);
                Assert.LessOrEqual(block.v_run_mps, block.v_max_mps + 1e-9, at);
                Assert.AreSame(skeleton.locomotion.legs, block.legs, at + " leg geometry is the skeleton's");
            }
        }
    }
}
