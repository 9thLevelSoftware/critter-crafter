using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using CritterCrafter.Locomotion;
using CritterCrafter.Review;
using UnityEditor;
using UnityEngine;

namespace CritterCrafter.Editor
{
    /// <summary>
    /// Play Mode review capture of runtime foot placement on the <see cref="ReviewCourse"/>, rendered
    /// through the game's orthographic isometric camera (frames + metrics per skeleton and speed).
    /// Play Mode is used so the Animator, the Animation Rigging graph and skinning run exactly as in the
    /// game. Headless:
    ///   Unity -batchmode -projectPath ... -executeMethod CritterCrafter.Editor.LocomotionCapture.CaptureFromCommandLine
    ///         -critterLibrary &lt;dir&gt; -critterSkeletons id[,id...] -critterOut &lt;dir&gt; [-critterSpeeds walk,run,2.5]
    /// Draft skeletons are marked approved in memory only, so the review can run before approval.
    /// </summary>
    public static class LocomotionCapture
    {
        public const int Fps = 30;

        struct Job
        {
            public string skeletonId, speedLabel, outDir;
        }

        static CritterLibrary _library;
        static Queue<Job> _jobs;
        static readonly List<LocomotionMetrics> Results = new List<LocomotionMetrics>();
        static string _outRoot;
        static GameObject _holder;
        static LocomotionRecorder _recorder;
        static System.Action _restore;

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
            _library = report.Library;
            _outRoot = Arg("-critterOut", "locomotion_capture");
            _jobs = new Queue<Job>();
            foreach (var id in Arg("-critterSkeletons", "").Split(','))
            {
                if (string.IsNullOrWhiteSpace(id)) continue;
                foreach (var label in Arg("-critterSpeeds", "walk,run,2.5").Split(','))
                    _jobs.Enqueue(new Job { skeletonId = id.Trim(), speedLabel = label.Trim(),
                        outDir = Path.Combine(_outRoot, id.Trim() + "_" + label.Trim()) });
            }
            // Keep static state (library, queue) alive across the Play Mode transition.
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
            EditorApplication.playModeStateChanged += OnPlayModeChanged;
            EditorApplication.update += Tick;
            EditorApplication.EnterPlaymode();
        }

        static void OnPlayModeChanged(PlayModeStateChange change)
        {
            if (change != PlayModeStateChange.EnteredEditMode) return;
            EditorApplication.playModeStateChanged -= OnPlayModeChanged;
            var json = new StringBuilder("[\n");
            for (int i = 0; i < Results.Count; i++)
                json.Append("  ").Append(JsonUtility.ToJson(Results[i])).Append(i + 1 < Results.Count ? ",\n" : "\n");
            json.Append("]\n");
            Directory.CreateDirectory(_outRoot);
            File.WriteAllText(Path.Combine(_outRoot, "metrics.json"), json.ToString());
            EditorApplication.Exit(0);
        }

        static void Tick()
        {
            if (!EditorApplication.isPlaying || _jobs == null) return;
            if (_recorder != null && !_recorder.Done) return;
            if (_recorder != null)
            {
                Results.Add(_recorder.Metrics);
                Debug.Log($"[CritterCrafter] locomotion capture: {JsonUtility.ToJson(_recorder.Metrics)}");
                Object.Destroy(_holder);
                _restore?.Invoke();
                _recorder = null;
            }
            if (_jobs.Count == 0)
            {
                EditorApplication.update -= Tick;
                Time.captureFramerate = 0;
                EditorApplication.ExitPlaymode();
                return;
            }
            var job = _jobs.Dequeue();
            Time.captureFramerate = Fps;
            _holder = new GameObject("LocomotionCapture");
            ReviewCourse.Build(_holder.transform, new Material(LibraryImporter.DefaultLitShader()) { color = new Color(0.22f, 0.23f, 0.27f) });
            Physics.SyncTransforms();
            var creature = Spawn(_library, job.skeletonId, _holder.transform, out _restore);
            var gait = creature.GetComponent<CreatureGait>();
            if (gait == null) throw new System.InvalidOperationException(job.skeletonId + " has no runtime legs");
            float speed = SpeedFor(gait.Block, job.speedLabel);
            _recorder = _holder.AddComponent<LocomotionRecorder>();
            _recorder.Begin(gait, ReviewCourse.Path(speed),
                new LocomotionMetrics { skeleton_id = job.skeletonId, speed_label = job.speedLabel, speed_mps = speed },
                job.outDir);
        }

        public static float SpeedFor(LocomotionData block, string label) =>
            label == "walk" ? (float)block.v_walk_mps
            : label == "run" ? (float)block.v_run_mps
            : float.Parse(label, CultureInfo.InvariantCulture);

        /// <summary>
        /// Assemble a skeleton filled with its reference parts, temporarily marking drafts approved.
        /// Call <paramref name="restore"/> afterwards to put the catalog statuses back.
        /// </summary>
        public static AssembledCreature Spawn(CritterLibrary library, string skeletonId, Transform parent, out System.Action restore)
        {
            var catalog = library.Catalog;
            var skeleton = catalog.FindSkeleton(skeletonId) ?? throw new System.ArgumentException("unknown skeleton " + skeletonId);
            string originalStatus = skeleton.status;
            var parts = new List<(PartData, string)>();
            skeleton.status = "approved";
            var recipe = ReferenceRecipe(catalog, skeleton);
            foreach (var f in recipe.fills)
                foreach (var id in new[] { f.part_id, f.connector_part_id })
                {
                    var part = string.IsNullOrEmpty(id) ? null : catalog.FindPart(id);
                    if (part != null && !RecipeGenerator.Usable(part)) { parts.Add((part, part.status)); part.status = "approved"; }
                }
            restore = () =>
            {
                skeleton.status = originalStatus;
                foreach (var (part, status) in parts) part.status = status;
            };
            var options = AssemblyOptions.Default;
            options.parent = parent;
            return CreatureAssembler.Assemble(library, recipe, options);
        }

        /// <summary>A recipe that fills every branch with its dedicated reference part (review inventory).</summary>
        public static CritterRecipe ReferenceRecipe(CatalogData catalog, SkeletonData skeleton)
        {
            var fills = new List<RecipeFill>();
            // Same connector budget as the generator: parts + connectors never exceed max_parts.
            int connectorSlots = Mathf.Max(0, Mathf.Min(catalog.limits.max_parts, 16) - skeleton.branches.Length);
            foreach (var br in skeleton.branches)
            {
                var part = catalog.FindPart(ReferenceId(skeleton.skeleton_id, br.branch_id, false));
                if (part == null) continue;
                var conn = connectorSlots > 0 ? catalog.FindPart(ReferenceId(skeleton.skeleton_id, br.branch_id, true)) : null;
                if (conn != null && RecipeGenerator.ConnectorAccepted(conn, br)) connectorSlots--;
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

    }
}
