using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CritterCrafter.Editor;
using CritterCrafter.Locomotion;
using CritterCrafter.Review;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Animations.Rigging;
using UnityEngine.TestTools;

namespace CritterCrafter.Tests
{
    public class ReviewMeasurementLibraryTests
    {
        CritterLibrary _library;

        [OneTimeSetUp]
        public void ImportLibrary()
        {
            var dir = System.Environment.GetEnvironmentVariable("CRITTER_LIBRARY_DIR");
            if (string.IsNullOrEmpty(dir))
            {
                var root = Path.GetFullPath("../../library");
                if (Directory.Exists(root))
                    dir = Directory.GetDirectories(root).Where(d => File.Exists(Path.Combine(d, "catalog.json")))
                        .OrderByDescending(Directory.GetLastWriteTimeUtc).FirstOrDefault();
            }
            if (dir == null) Assert.Ignore("no built critter library");
            _library = LibraryImporter.Import(dir).Library;
        }

        [UnityTest]
        public IEnumerator LeglessDeformedBodiesAreMeasuredEvenDuringWarmup()
        {
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
            yield return new EnterPlayMode();
            Time.captureFramerate = 30;
            var holder = new GameObject("LeglessMeasurement");
            var material = new Material(LibraryImporter.DefaultLitShader());
            ReviewCourse.Build(holder.transform, material, ramp: false);
            Physics.SyncTransforms();
            var creature = LocomotionCapture.Spawn(_library, "serpentine_limbless_articulated_balanced_v3", holder.transform, out var restore);
            restore();
            var gait = creature.GetComponent<CreatureGait>();
            var recorder = holder.AddComponent<LocomotionRecorder>();
            var metrics = new LocomotionMetrics { warmup_frames = 1000 };
            recorder.Begin(gait, ReviewCourse.Still(.7f), metrics);
            gait.enabled = false;
            var original = creature.Animator.transform.localPosition;
            recorder.Script = time => creature.Animator.transform.localPosition = original + Vector3.up * (time < .1f ? 0f : time < .4f ? .25f : -.25f);
            while (!recorder.Done) yield return null;
            Object.Destroy(holder);
            Object.Destroy(material);
            Time.captureFramerate = 0;
            yield return new ExitPlayMode();
            Assert.IsTrue(metrics.grounding_measurement_complete, metrics.grounding_measurement_problem);
            Assert.IsTrue(metrics.support_measurement_complete);
            Assert.AreEqual(0, metrics.support_violations, "sliding bodies need no planted feet");
            Assert.That(metrics.max_body_contact_hover_m, Is.GreaterThan(.20f));
            Assert.That(metrics.max_surface_penetration_m, Is.GreaterThan(.15f));
            Assert.That(metrics.all_frames_max_body_rise_m, Is.GreaterThan(.24f));
            Assert.AreEqual(0f, metrics.max_body_rise_m, "legacy warmup behavior must not change");
        }

        [UnityTest]
        public IEnumerator AllFrameFootExtremaDoNotDiscardWarmupIntervals()
        {
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
            yield return new EnterPlayMode();
            Time.captureFramerate = 30;
            var results = new List<LocomotionMetrics>();
            foreach (int warmup in new[] { 0, 1000 })
            {
                var holder = new GameObject("WarmupMeasurement");
                var material = new Material(LibraryImporter.DefaultLitShader());
                ReviewCourse.Build(holder.transform, material, ramp: false);
                Physics.SyncTransforms();
                var creature = LocomotionCapture.Spawn(_library, "biped_plantigrade_humanoid_balanced_v3", holder.transform, out var restore);
                restore();
                var gait = creature.GetComponent<CreatureGait>();
                var builder = creature.Animator.GetComponent<RigBuilder>();
                builder.enabled = false;
                creature.Animator.enabled = false;
                creature.ApplyBindPose();
                creature.ApplyNeutralPose();
                var recorder = holder.AddComponent<LocomotionRecorder>();
                var metrics = new LocomotionMetrics { warmup_frames = warmup };
                results.Add(metrics);
                recorder.Begin(gait, ReviewCourse.Still(.6f), metrics);
                gait.enabled = false;
                var original = creature.Animator.transform.localPosition;
                recorder.Script = time =>
                {
                    float rise = time > .1f ? .2f : 0f;
                    creature.Animator.transform.localPosition = original + new Vector3(.4f * time, rise, 0f);
                    foreach (var leg in gait.Legs)
                        leg.plant = creature.transform.TransformPoint(leg.homeLocal) + Vector3.up * rise;
                };
                while (!recorder.Done) yield return null;
                Object.Destroy(holder);
                Object.Destroy(material);
                yield return null;
            }
            Time.captureFramerate = 0;
            yield return new ExitPlayMode();
            foreach (var metrics in results)
            {
                Assert.IsTrue(metrics.grounding_measurement_complete, metrics.grounding_measurement_problem);
                Assert.That(metrics.all_frames_max_planted_slip_m, Is.GreaterThan(.01f));
                Assert.That(metrics.all_frames_max_stance_drift_m, Is.GreaterThan(.1f));
                Assert.That(metrics.all_frames_max_planted_hover_m, Is.GreaterThan(.19f));
                Assert.That(metrics.all_frames_max_body_rise_m, Is.GreaterThan(.19f));
                Assert.That(metrics.all_frames_max_planted_slip_m, Is.GreaterThanOrEqualTo(metrics.max_planted_slip_m));
                Assert.That(metrics.all_frames_max_stance_drift_m, Is.GreaterThanOrEqualTo(metrics.max_stance_drift_m));
                Assert.That(metrics.all_frames_max_planted_hover_m, Is.GreaterThanOrEqualTo(metrics.max_planted_hover_m));
                Assert.That(metrics.all_frames_max_body_rise_m, Is.GreaterThanOrEqualTo(metrics.max_body_rise_m));
                if (metrics.warmup_frames == 0)
                {
                    Assert.AreEqual(metrics.max_planted_slip_m, metrics.all_frames_max_planted_slip_m);
                    Assert.AreEqual(metrics.max_stance_drift_m, metrics.all_frames_max_stance_drift_m);
                    Assert.AreEqual(metrics.max_planted_hover_m, metrics.all_frames_max_planted_hover_m);
                    Assert.AreEqual(metrics.max_body_rise_m, metrics.all_frames_max_body_rise_m);
                }
                else
                {
                    Assert.AreEqual(0f, metrics.max_planted_slip_m);
                    Assert.AreEqual(0f, metrics.max_stance_drift_m);
                    Assert.AreEqual(0f, metrics.max_planted_hover_m);
                    Assert.AreEqual(0f, metrics.max_body_rise_m);
                }
            }
        }

        [UnityTest]
        public IEnumerator LosingRenderedCoverageFailsClosedForLeglessBodies()
        {
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
            yield return new EnterPlayMode();
            Time.captureFramerate = 30;
            var holder = new GameObject("IncompleteMeasurement");
            var material = new Material(LibraryImporter.DefaultLitShader());
            ReviewCourse.Build(holder.transform, material, ramp: false);
            Physics.SyncTransforms();
            var creature = LocomotionCapture.Spawn(_library, "serpentine_limbless_articulated_balanced_v3", holder.transform, out var restore);
            restore();
            var gait = creature.GetComponent<CreatureGait>();
            var recorder = holder.AddComponent<LocomotionRecorder>();
            var metrics = new LocomotionMetrics();
            recorder.Begin(gait, ReviewCourse.Still(.4f), metrics);
            bool removed = false;
            recorder.Script = time =>
            {
                if (!removed && time >= .1f) { removed = true; creature.Renderers[0].renderer.enabled = false; }
                if (time >= .2f) creature.Renderers[0].renderer.enabled = true;
            };
            while (!recorder.Done) yield return null;
            Object.Destroy(holder);
            Object.Destroy(material);
            Time.captureFramerate = 0;
            yield return new ExitPlayMode();
            Assert.IsFalse(metrics.grounding_measurement_complete, "coverage loss must stay incomplete even after recovery");
            Assert.That(metrics.grounding_measurement_problem, Does.Contain("surface"));
        }
    }
}
