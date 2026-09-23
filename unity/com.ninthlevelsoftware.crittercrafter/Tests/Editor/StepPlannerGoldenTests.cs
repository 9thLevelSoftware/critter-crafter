using System;
using System.IO;
using CritterCrafter.Locomotion;
using NUnit.Framework;
using UnityEngine;

namespace CritterCrafter.Tests
{
    /// <summary>
    /// The C# StepPlanner must reproduce the Python reference planner (locomotion/stepper.py) for every
    /// skeleton: continuous gait quantities and per-step landing targets at several speeds.
    /// </summary>
    public class StepPlannerGoldenTests
    {
        const string GoldenV3Dir = "Packages/com.ninthlevelsoftware.crittercrafter/Tests/Editor/GoldenV3";
        const double Tolerance = 1e-6;

        [Serializable] class Row
        {
            public string skeleton_id, mode;
            public double speed, weight, duty, stride_m, cadence_hz;
            public bool run, overspeed;
            public double[] landings;
        }

        [Serializable] class Doc
        {
            public string planner;
            public Row[] rows;
        }

        static T Load<T>(string name) => JsonUtility.FromJson<T>(File.ReadAllText(Path.GetFullPath(GoldenV3Dir + "/" + name)));

        [Test]
        public void MatchesPythonReferencePlanner()
        {
            var catalog = Load<CatalogData>("catalog.json");
            var doc = Load<Doc>("locomotion.json");
            Assert.AreEqual("stepper-1", doc.planner);
            Assert.Greater(doc.rows.Length, 0);
            foreach (var row in doc.rows)
            {
                var block = catalog.FindSkeleton(row.skeleton_id).locomotion;
                string at = $"{row.skeleton_id} @ {row.speed}";
                if (row.mode == "slide")
                {
                    var slide = StepPlanner.SlideParams(block, row.speed);
                    Assert.AreEqual(row.cadence_hz, slide.cadenceHz, Tolerance, at);
                    Assert.AreEqual(row.overspeed, slide.overspeed, at);
                    continue;
                }
                var p = StepPlanner.Params(block, row.speed);
                Assert.AreEqual(row.weight, p.weight, Tolerance, at + " weight");
                Assert.AreEqual(row.duty, p.duty, Tolerance, at + " duty");
                Assert.AreEqual(row.stride_m, p.strideM, Tolerance, at + " stride");
                Assert.AreEqual(row.cadence_hz, p.cadenceHz, Tolerance, at + " cadence");
                Assert.AreEqual(row.run, p.run, at + " run");
                Assert.AreEqual(row.overspeed, p.overspeed, at + " overspeed");
                for (int i = 0; i < block.legs.Length; i++)
                {
                    var landing = StepPlanner.LandingTargetLocal(block.legs[i], row.speed, p.cadenceHz, p.duty);
                    for (int k = 0; k < 3; k++)
                        Assert.AreEqual(row.landings[i * 3 + k], landing[k], Tolerance, $"{at} leg {i} landing");
                }
            }
        }
    }
}
