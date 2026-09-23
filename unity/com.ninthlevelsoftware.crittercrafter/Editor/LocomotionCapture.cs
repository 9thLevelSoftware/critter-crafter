using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using CritterCrafter.Locomotion;
using UnityEditor;
using UnityEngine;
using UnityEngine.Animations.Rigging;
using UnityEngine.Playables;

namespace CritterCrafter.Editor
{
    /// <summary>
    /// Deterministic review capture of runtime foot placement: a creature follows a scripted path at a
    /// given speed across flat ground, a 20 degree ramp and a plateau, turns instantly (like the game's
    /// NavMeshAgent with angularSpeed 999), stops and turns in place. Frames are rendered through the
    /// game's orthographic isometric camera and written as PNGs with a metrics JSON. Headless:
    ///   Unity -batchmode -projectPath ... -executeMethod CritterCrafter.Editor.LocomotionCapture.CaptureFromCommandLine
    ///         -critterLibrary &lt;dir&gt; -critterSkeletons id[,id...] -critterOut &lt;dir&gt; [-critterSpeeds walk,run,2.5]
    /// Draft skeletons are marked approved in memory only, so the review can run before approval.
    /// </summary>
    public static class LocomotionCapture
    {
        public const float Fps = 30f;

        public class Metrics
        {
            public string skeleton_id;
            public string speed_label;
            public float speed_mps;
            public float cadence_hz;
            public bool overspeed;
            public float max_planted_slip_m;
            public float max_ik_residual_m;
            public float max_penetration_m;
            public int min_planted_supports = int.MaxValue;
            public int frames;
        }

        public static void CaptureFromCommandLine()
        {
            var args = System.Environment.GetCommandLineArgs();
            string Arg(string name, string def)
            {
                int i = System.Array.IndexOf(args, name);
                return i >= 0 && i + 1 < args.Length ? args[i + 1] : def;
            }
            var report = LibraryImporter.Import(Arg("-critterLibrary", ""));
            foreach (var p in report.Problems) Debug.LogWarning("[CritterCrafter] " + p);
            string outDir = Arg("-critterOut", "locomotion_capture");
            var all = new List<Metrics>();
            foreach (var id in Arg("-critterSkeletons", "").Split(','))
            {
                if (string.IsNullOrWhiteSpace(id)) continue;
                foreach (var label in Arg("-critterSpeeds", "walk,run,2.5").Split(','))
                    all.Add(Capture(report.Library, id.Trim(), label.Trim(), Path.Combine(outDir, id.Trim() + "_" + label.Trim())));
            }
            var json = new StringBuilder("[\n");
            for (int i = 0; i < all.Count; i++)
                json.Append("  ").Append(JsonUtility.ToJson(all[i])).Append(i + 1 < all.Count ? ",\n" : "\n");
            json.Append("]\n");
            Directory.CreateDirectory(outDir);
            File.WriteAllText(Path.Combine(outDir, "metrics.json"), json.ToString());
            EditorApplication.Exit(0);
        }

        /// <summary>A recipe that fills every branch with its dedicated reference part (review inventory).</summary>
        public static CritterRecipe ReferenceRecipe(CatalogData catalog, SkeletonData skeleton)
        {
            var fills = new List<RecipeFill>();
            foreach (var br in skeleton.branches)
            {
                var part = catalog.FindPart(ReferenceId(skeleton.skeleton_id, br.branch_id, false));
                if (part == null) continue;
                var conn = catalog.FindPart(ReferenceId(skeleton.skeleton_id, br.branch_id, true));
                fills.Add(new RecipeFill
                {
                    branch_id = br.branch_id, part_id = part.part_id,
                    connector_part_id = conn != null && RecipeGenerator.ConnectorAccepted(conn, br) ? conn.part_id : "",
                    binding_profile_id = br.binding_profile_id, binding_profile_version = br.binding_profile_version,
                    binding_profile_hash = br.binding_profile_hash,
                    length_scale = System.Math.Round((double)br.length_mm / part.length_mm, 6, System.MidpointRounding.ToEven),
                    girth_scale = System.Math.Round((double)br.girth_mm * part.length_mm / (part.girth_mm * (double)br.length_mm), 6,
                        System.MidpointRounding.ToEven),
                });
            }
            return new CritterRecipe
            {
                recipe_id = "review_" + skeleton.skeleton_id, library_id = catalog.library_id,
                library_version = catalog.version, generator = RecipeGenerator.Algorithm, pool_id = "review",
                seed = 0, skeleton_id = skeleton.skeleton_id, fills = fills.ToArray(),
            };
        }

        static string ReferenceId(string skeletonId, string branchId, bool connector)
        {
            string raw = ((connector ? "reference_connector_" : "reference_") + skeletonId + "_" + branchId).ToLowerInvariant();
            var sb = new StringBuilder();
            bool underscore = false;
            foreach (char ch in raw)
            {
                bool ok = (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_';
                if (ok) { sb.Append(ch); underscore = ch == '_'; }
                else if (!underscore) { sb.Append('_'); underscore = true; }
            }
            return sb.ToString().Trim('_') + "_v1";
        }

        struct Waypoint
        {
            public float time;
            public Vector3 position;
            public float headingDeg;
        }

        public static Metrics Capture(CritterLibrary library, string skeletonId, string speedLabel, string outDir, int cell = 320)
        {
            var catalog = library.Catalog;
            var skeleton = catalog.FindSkeleton(skeletonId) ?? throw new System.ArgumentException("unknown skeleton " + skeletonId);
            string originalStatus = skeleton.status;
            var restore = new List<(PartData, string)>();
            skeleton.status = "approved";
            var recipe = ReferenceRecipe(catalog, skeleton);
            foreach (var f in recipe.fills)
                foreach (var id in new[] { f.part_id, f.connector_part_id })
                {
                    var part = string.IsNullOrEmpty(id) ? null : catalog.FindPart(id);
                    if (part != null && !RecipeGenerator.Usable(part)) { restore.Add((part, part.status)); part.status = "approved"; }
                }

            var holder = new GameObject("LocomotionCapture");
            var metrics = new Metrics { skeleton_id = skeletonId, speed_label = speedLabel };
            try
            {
                BuildCourse(holder.transform);
                Physics.SyncTransforms();
                var options = AssemblyOptions.Default;
                options.parent = holder.transform;
                var creature = CreatureAssembler.Assemble(library, recipe, options);
                var gait = creature.GetComponent<CreatureGait>();
                var block = skeleton.locomotion;
                if (gait == null || block == null || !block.HasLegs)
                    throw new System.InvalidOperationException(skeletonId + " has no runtime legs");
                float speed = speedLabel == "walk" ? (float)block.v_walk_mps
                    : speedLabel == "run" ? (float)block.v_run_mps
                    : float.Parse(speedLabel, CultureInfo.InvariantCulture);
                metrics.speed_mps = speed;
                var builder = creature.Animator.GetComponent<RigBuilder>();
                if (builder.graph.IsValid()) builder.graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);

                // Edit-mode capture has no player loop, so skinned meshes are not re-skinned per frame
                // unless forced; without this the leg meshes stay frozen while the bones move.
                foreach (var smr in creature.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                    smr.forceMatrixRecalculationPerRender = true;
                var path = CoursePath(speed);
                const int frameStep = 1;
                var cam = MakeCamera(holder.transform, cell, out var rt);
                float size = Mathf.Max(1.6f, 0.9f * Mathf.Max(creature.NeutralBoundsLocal.size.x, creature.NeutralBoundsLocal.size.z));
                cam.orthographicSize = size;
                Directory.CreateDirectory(outDir);
                float duration = path[path.Count - 1].time;
                int frames = Mathf.CeilToInt(duration * Fps);
                float dt = 1f / Fps;
                var lastTip = new Dictionary<string, Vector3>();
                var csv = new StringBuilder("frame,time,speed,residual,slip,forced,worst,planted\n");
                var legCsv = new StringBuilder("frame,leg,planted,forced,tx,ty,tz,gx,gy,gz,reach_frac\n");
                var tips = new Dictionary<string, Transform>();
                foreach (var t in creature.GetComponentsInChildren<Transform>(true))
                    if (t.name.EndsWith("_ik_tip")) tips[t.name.Substring(0, t.name.Length - "_ik_tip".Length)] = t;

                for (int frame = 0; frame <= frames; frame++)
                {
                    float time = frame * dt;
                    Sample(path, time, out var pos, out var heading);
                    pos.y = GroundY(pos);
                    creature.transform.SetPositionAndRotation(pos, Quaternion.Euler(0f, heading, 0f));
                    if (frame == 0) gait.ResetFeet();
                    gait.Step(frame == 0 ? 0f : dt);
                    creature.Animator.Update(frame == 0 ? 0f : dt);
                    builder.Evaluate(frame == 0 ? 0f : dt);

                    foreach (var leg in gait.Legs)
                        if (tips.TryGetValue(leg.branchId, out var dbgTip))
                            legCsv.AppendLine(string.Format(CultureInfo.InvariantCulture,
                                "{0},{1},{2},{3},{4:F4},{5:F4},{6:F4},{7:F4},{8:F4},{9:F4},{10:F4}",
                                frame, leg.branchId, leg.planted ? 1 : 0, leg.forced ? 1 : 0,
                                dbgTip.position.x, dbgTip.position.y, dbgTip.position.z,
                                leg.target.position.x, leg.target.position.y, leg.target.position.z,
                                leg.hip != null ? Vector3.Distance(leg.hip.position, leg.target.position) / leg.reach : -1f));
                    if (frame > 0)
                    {
                        float residualBefore = metrics.max_ik_residual_m, slipBefore = metrics.max_planted_slip_m;
                        metrics.max_ik_residual_m = 0f; metrics.max_planted_slip_m = 0f;
                        Measure(gait, tips, lastTip, metrics);
                        csv.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0},{1:F3},{2:F3},{3:F4},{4:F4},{5},{6},{7}",
                            frame, time, gait.Speed, metrics.max_ik_residual_m, metrics.max_planted_slip_m,
                            CountForced(gait), WorstLeg(gait, tips), PlantedSupports(gait)));
                        metrics.max_ik_residual_m = Mathf.Max(residualBefore, metrics.max_ik_residual_m);
                        metrics.max_planted_slip_m = Mathf.Max(slipBefore, metrics.max_planted_slip_m);
                    }
                    lastTip.Clear();
                    foreach (var leg in gait.Legs)
                        if (leg.planted && !leg.forced && tips.TryGetValue(leg.branchId, out var tip))
                            lastTip[leg.branchId] = tip.position;
                    metrics.cadence_hz = Mathf.Max(metrics.cadence_hz, (float)gait.Current.cadenceHz);
                    metrics.overspeed |= gait.Current.overspeed;

                    if (frame % frameStep == 0)
                    {
                        var target = pos + Vector3.up * (float)block.hip_height_m * 0.6f;
                        cam.transform.position = target + new Vector3(16f, 18f, 16f).normalized * 30f;
                        cam.transform.LookAt(target);
                        cam.Render();
                        RenderTexture.active = rt;
                        var tex = new Texture2D(cell, cell, TextureFormat.RGB24, false);
                        tex.ReadPixels(new Rect(0, 0, cell, cell), 0, 0);
                        tex.Apply();
                        RenderTexture.active = null;
                        File.WriteAllBytes(Path.Combine(outDir, $"frame_{frame / frameStep:0000}.png"), tex.EncodeToPNG());
                        Object.DestroyImmediate(tex);
                    }
                }
                metrics.frames = frames + 1;
                File.WriteAllText(Path.Combine(outDir, "frames.csv"), csv.ToString());
                File.WriteAllText(Path.Combine(outDir, "legs.csv"), legCsv.ToString());
                rt.Release();
                File.WriteAllText(Path.Combine(outDir, "metrics.json"), JsonUtility.ToJson(metrics, true));
                Debug.Log($"[CritterCrafter] locomotion capture {skeletonId} @ {speedLabel}: {JsonUtility.ToJson(metrics)}");
            }
            finally
            {
                Object.DestroyImmediate(holder);
                skeleton.status = originalStatus;
                foreach (var (part, status) in restore) part.status = status;
            }
            return metrics;
        }

        static void Measure(CreatureGait gait, Dictionary<string, Transform> tips, Dictionary<string, Vector3> lastTip, Metrics m)
        {
            int planted = 0;
            foreach (var leg in gait.Legs)
            {
                if (!tips.TryGetValue(leg.branchId, out var tip) || leg.weight < 0.999f) continue;
                Vector3 p = tip.position;
                m.max_ik_residual_m = Mathf.Max(m.max_ik_residual_m, Vector3.Distance(p, leg.target.position));
                float ground = GroundY(p);
                m.max_penetration_m = Mathf.Max(m.max_penetration_m, ground - p.y);
                if (leg.planted && !leg.forced)
                {
                    if (leg.support) planted++;
                    if (lastTip.TryGetValue(leg.branchId, out var last))
                        m.max_planted_slip_m = Mathf.Max(m.max_planted_slip_m, Vector3.Distance(last, p));
                }
            }
            m.min_planted_supports = Mathf.Min(m.min_planted_supports, planted);
        }

        static int PlantedSupports(CreatureGait gait)
        {
            int n = 0;
            foreach (var leg in gait.Legs) if (leg.support && leg.planted && !leg.forced) n++;
            return n;
        }

        static int CountForced(CreatureGait gait)
        {
            int n = 0;
            foreach (var leg in gait.Legs) if (leg.forced) n++;
            return n;
        }

        static string WorstLeg(CreatureGait gait, Dictionary<string, Transform> tips)
        {
            string worst = "";
            float best = -1f;
            foreach (var leg in gait.Legs)
            {
                if (!tips.TryGetValue(leg.branchId, out var tip)) continue;
                float d = Vector3.Distance(tip.position, leg.target.position);
                if (d > best) { best = d; worst = $"{leg.branchId}:{(leg.planted ? "P" : "S")}:{d:F3}"; }
            }
            return worst;
        }

        static float GroundY(Vector3 p)
        {
            return Physics.Raycast(p + Vector3.up * 5f, Vector3.down, out var hit, 20f, ~0, QueryTriggerInteraction.Ignore)
                ? hit.point.y : 0f;
        }

        /// <summary>Flat start, a 20 degree ramp up to a plateau, then a right turn along the plateau.</summary>
        static void BuildCourse(Transform parent)
        {
            var mat = new Material(LibraryImporter.DefaultLitShader()) { color = new Color(0.22f, 0.23f, 0.27f) };
            var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
            ground.transform.SetParent(parent, false);
            ground.transform.localScale = Vector3.one * 10f;
            ground.GetComponent<Renderer>().sharedMaterial = mat;
            const float angle = 20f, rampLength = 4f;
            float rise = rampLength * Mathf.Sin(angle * Mathf.Deg2Rad), run = rampLength * Mathf.Cos(angle * Mathf.Deg2Rad);
            var ramp = GameObject.CreatePrimitive(PrimitiveType.Cube);
            ramp.transform.SetParent(parent, false);
            ramp.transform.localScale = new Vector3(4f, 0.2f, rampLength);
            ramp.transform.localRotation = Quaternion.Euler(-angle, 0f, 0f);
            // Top surface starts at (z=2, y=0) and ends at (z=2+run, y=rise).
            Vector3 top = new Vector3(0f, rise * 0.5f, 2f + run * 0.5f);
            ramp.transform.localPosition = top - ramp.transform.localRotation * Vector3.up * 0.1f;
            ramp.GetComponent<Renderer>().sharedMaterial = mat;
            var plateau = GameObject.CreatePrimitive(PrimitiveType.Cube);
            plateau.transform.SetParent(parent, false);
            plateau.transform.localScale = new Vector3(8f, rise, 6f);
            plateau.transform.localPosition = new Vector3(2f, rise * 0.5f, 2f + run + 3f - 0.001f);
            plateau.GetComponent<Renderer>().sharedMaterial = mat;
            var light = new GameObject("Key").AddComponent<Light>();
            light.transform.SetParent(parent, false);
            light.type = LightType.Directional;
            light.intensity = 1.2f;
            light.shadows = LightShadows.Soft;
            light.transform.rotation = Quaternion.Euler(50f, -30f, 0f);
        }

        static List<Waypoint> CoursePath(float speed)
        {
            // Start 3 m before the ramp, climb it, walk 2.5 m onto the plateau, turn 90 degrees right
            // instantly, walk 2 m, stop for 1.2 s, turn 180 degrees in place, hold 1.2 s.
            float rampRun = 4f * Mathf.Cos(20f * Mathf.Deg2Rad);
            var a = new Vector3(0f, 0f, -3f);
            var b = new Vector3(0f, 0f, 2f + rampRun + 2.5f);
            var c = b + new Vector3(2f, 0f, 0f);
            float t1 = Vector3.Distance(a, b) / speed;
            float t2 = t1 + 2f / speed;
            return new List<Waypoint>
            {
                new Waypoint { time = 0f, position = a, headingDeg = 0f },
                new Waypoint { time = t1, position = b, headingDeg = 0f },
                new Waypoint { time = t1 + 1e-4f, position = b, headingDeg = 90f },
                new Waypoint { time = t2, position = c, headingDeg = 90f },
                new Waypoint { time = t2 + 1.2f, position = c, headingDeg = 90f },
                new Waypoint { time = t2 + 1.2f + 1e-4f, position = c, headingDeg = 270f },
                new Waypoint { time = t2 + 2.4f, position = c, headingDeg = 270f },
            };
        }

        static void Sample(List<Waypoint> path, float time, out Vector3 position, out float heading)
        {
            for (int i = 1; i < path.Count; i++)
            {
                if (time > path[i].time) continue;
                var a = path[i - 1];
                var b = path[i];
                float u = b.time > a.time ? (time - a.time) / (b.time - a.time) : 1f;
                position = Vector3.Lerp(a.position, b.position, u);
                heading = a.headingDeg;
                if (u >= 1f) heading = b.headingDeg;
                return;
            }
            position = path[path.Count - 1].position;
            heading = path[path.Count - 1].headingDeg;
        }

        static Camera MakeCamera(Transform parent, int cell, out RenderTexture rt)
        {
            rt = new RenderTexture(cell, cell, 24, RenderTextureFormat.ARGB32) { antiAliasing = 4 };
            var go = new GameObject("Cam");
            go.transform.SetParent(parent, false);
            var cam = go.AddComponent<Camera>();
            cam.orthographic = true;
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.12f, 0.13f, 0.16f);
            cam.targetTexture = rt;
            cam.farClipPlane = 200f;
            return cam;
        }
    }
}
