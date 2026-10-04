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
            public string case_id;
            public bool support, early, body_on_ground;
            public double reach_m;
            public int remaining_supports, minimum_supports, swinging, leg_count, chosen_slot;
            public LandingCandidate[] candidates;
        }

        [Serializable] class LandingCandidate
        {
            public int index, contact_index;
            public double dx, dy, dz, reach_fraction, coxa_yaw_deg;
            public bool ground_valid, hinge;
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
            foreach (var row in doc.rows)
            {
                if (row.mode == "landing")
                {
                    var candidates = new Candidate[row.candidates.Length];
                    for (int i = 0; i < candidates.Length; i++)
                    {
                        var c = row.candidates[i];
                        double reach = StepPlanner.LandingReachFraction(c.dx, c.dy, c.dz, row.reach_m);
                        if (row.reach_m > 0.0) Assert.AreEqual(c.reach_fraction, reach, Tolerance, row.case_id);
                        candidates[i] = new Candidate { index = c.index, contactIndex = c.contact_index, hinge = c.hinge,
                            reachFraction = reach, coxaYawDeg = c.coxa_yaw_deg, groundValid = c.ground_valid };
                    }
                    bool allowed = StepPlanner.CanLift(row.support, row.remaining_supports, row.minimum_supports,
                        row.early, row.body_on_ground, row.swinging, row.leg_count);
                    Assert.AreEqual(row.chosen_slot,
                        StepPlanner.ChooseLanding(candidates, candidates.Length, row.stride_m, allowed), row.case_id);
                    continue;
                }
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

        [TestCase(false, 0.95)]
        [TestCase(true, 0.97)]
        public void LandingHonoursReachBoundaryAndRejectsNonfiniteValues(bool hinge, double limit)
        {
            var candidates = new[] { new Candidate { index = 0, contactIndex = 0, groundValid = true, hinge = hinge } };
            candidates[0].reachFraction = StepPlanner.LandingReachFraction(0, 0, limit, 1);
            Assert.AreEqual(0, StepPlanner.ChooseLanding(candidates, 1, 0, true));
            candidates[0].reachFraction = StepPlanner.LandingReachFraction(0, 0, limit + 1e-6, 1);
            Assert.AreEqual(-1, StepPlanner.ChooseLanding(candidates, 1, 0, true));
            foreach (double invalid in new[] { double.NaN, double.PositiveInfinity, -1.0 })
            {
                candidates[0].reachFraction = invalid;
                Assert.AreEqual(-1, StepPlanner.ChooseLanding(candidates, 1, 1, true));
            }
        }
    }
}
