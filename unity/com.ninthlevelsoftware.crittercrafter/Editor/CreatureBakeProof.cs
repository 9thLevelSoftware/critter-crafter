using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using CritterCrafter.Locomotion;
using CritterCrafter.Review;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace CritterCrafter.Editor
{
    public static class CreatureBakeProof
    {
        [Serializable] public sealed class DrawEvent
        {
            public int event_index, draw_calls;
            public string event_name, pass_name, light_mode, category;
        }
        [Serializable] public sealed class ProfilerPeer
        {
            public int guid;
            public string identifier;
            public bool connectable;
        }
        [Serializable] public sealed class BodyIdentity
        {
            public string name, hierarchy, renderer_entity_id, game_object_entity_id, mesh_entity_id;
            public bool active, enabled;
            public int layer, vertices, indices, bones, materials;
            public string[] bone_names, material_names, shader_names;
            public Bounds world_bounds;
        }
        [Serializable] public sealed class NativeEventDiagnostic
        {
            public int event_index, data_frame_index, draw_calls, vertex_count, index_count;
            public bool data_ready, body_match;
            public string event_type, event_name, object_name, object_type, object_entity_id;
            public string component_entity_id, mesh_entity_id, mesh_name, pass_name, light_mode, shader, render_target;
            public string[] mesh_entity_ids;
        }
        [Serializable] public sealed class SceneItem
        {
            public string kind, hierarchy, name, entity_id, scene;
            public bool active, before_enabled, capture_enabled, body;
            public int layer, before_culling_mask, capture_culling_mask;
            public string light_type;
        }
        [Serializable] public sealed class CameraEvidence
        {
            public string hierarchy, rendering_path, target_name;
            public bool active, enabled, orthographic;
            public int culling_mask, target_width, target_height;
            public float orthographic_size, aspect;
            public Vector3 position, euler_angles;
            public Rect game_view_device_flipped_target_rect;
            public bool image_copy_flipped_y;
        }
        [Serializable] public sealed class DrawMeasurement
        {
            public string path, graphics_device, pipeline, problem;
            public string graphics_api, debugger_assembly, event_data_assembly;
            public bool graphics_multithreaded, frame_debugger_supported, batch_mode;
            public bool measurement_complete;
            public bool native_enabled;
            public int captured_event_count;
            public int profiler_connection;
            public string profiler_identifier;
            public ProfilerPeer[] available_peers;
            public bool enable_requested, native_enabled_on_request, toggle_pending, editor_paused;
            public int color_submissions, shadow_submissions, depth_submissions, other_submissions;
            public DrawEvent[] events;
            public BodyIdentity[] bodies;
            public NativeEventDiagnostic[] native_events;
            public bool isolation_verified;
            public string attribution, isolation_problem, lighting;
            public SceneItem[] scene_inventory;
            public CameraEvidence camera;
        }
        [Serializable] sealed class ProofSummary
        {
            public DrawMeasurement[] draws;
            public LocomotionMetrics[] courses;
        }
        struct Job { public bool baked; public string kind, label; }
        sealed class NativeCapture
        {
            public Array events;
            public int eventsHash, index;
            public bool selected, restoring;
            public double selectionStarted;
            public string problem;
            public object data;
            public MethodInfo readData, setLimit;
            public PropertyInfo readHash;
            public SkinnedMeshRenderer[] bodies;
            public object[] rendererIds, gameObjectIds, meshIds;
            public BodyIdentity[] identities;
            public readonly List<NativeEventDiagnostic> diagnostics = new List<NativeEventDiagnostic>();
        }
        static readonly Queue<Job> Jobs = new Queue<Job>();
        static readonly List<DrawMeasurement> Draws = new List<DrawMeasurement>();
        static readonly List<LocomotionMetrics> Metrics = new List<LocomotionMetrics>();
        static CritterLibrary Library;
        static CritterRecipe Recipe;
        static string Output;
        static GameObject Holder;
        static AssembledCreature Creature;
        static LocomotionRecorder Recorder;
        static Camera Camera;
        static RenderTexture Target;
        static Job Current;
        static AnimatorOverrideController Override;
        static int CaptureFrame;
        static int CaptureConnection;
        static bool DebuggerStarted;
        static EditorWindow CaptureView;
        static EditorWindow DebuggerWindow;
        static ProfilerPeer[] AvailablePeers = Array.Empty<ProfilerPeer>();
        static string CaptureIdentifier;
        static bool PreviousPaused, EnabledOnRequest;
        static double CaptureStartedAt;
        static bool WholeFrameRequested;
        static NativeCapture NativeReplay;
        static readonly Dictionary<Camera, (bool enabled, int mask)> OtherCameras = new Dictionary<Camera, (bool, int)>();
        static readonly Dictionary<Renderer, bool> OtherRenderers = new Dictionary<Renderer, bool>();
        static readonly Dictionary<Light, bool> OtherLights = new Dictionary<Light, bool>();
        static readonly Dictionary<Canvas, bool> OtherCanvases = new Dictionary<Canvas, bool>();
        static SceneItem[] SceneInventory;
        static Light ProofLight;
        static bool PreviousOptionsEnabled;
        static EnterPlayModeOptions PreviousOptions;
        static Type DebuggerType, EventDataType;
        static readonly BindingFlags Flags = BindingFlags.Static | BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

        public static void CaptureFromCommandLine()
        {
            try
            {
                string Arg(string name, string fallback = "")
                {
                    var args = Environment.GetCommandLineArgs(); int i = Array.IndexOf(args, name);
                    return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback;
                }
                var report = LibraryImporter.Import(Arg("-critterLibrary"));
                if (report.Problems.Count > 0) throw new InvalidDataException(string.Join("; ", report.Problems));
                Recipe = JsonUtility.FromJson<CritterRecipe>(File.ReadAllText(Arg("-critterRecipe")));
                Library = CreateReviewLibrary(report.Library, Recipe);
                if (Library.FindBaked(Recipe, Recipe.pool_id == "review_" + Recipe.skeleton_id) == null)
                    throw new InvalidDataException("CC_BAKE_PROOF: requested recipe has no bake");
                Output = Path.GetFullPath(Arg("-critterOut", "bake_proof")); Directory.CreateDirectory(Output);
                Jobs.Clear(); Draws.Clear(); Metrics.Clear();
                string mode = Arg("-critterProofMode", "all");
                if (mode != "all" && mode != "draws") throw new ArgumentException("-critterProofMode must be all or draws");
                foreach (bool baked in new[] { false, true })
                {
                    Jobs.Enqueue(new Job { baked = baked, kind = "draws", label = "iso" });
                    if (mode == "draws") continue;
                    foreach (string clip in new[] { "idle", "walk", "run", "telegraph", "attack", "hit", "stun", "death" })
                        Jobs.Enqueue(new Job { baked = baked, kind = "clip", label = clip });
                    foreach (string course in new[] { "review", "turns" })
                        foreach (string speed in new[] { "walk", "run", "max" })
                            Jobs.Enqueue(new Job { baked = baked, kind = course, label = speed });
                    Jobs.Enqueue(new Job { baked = baked, kind = "reactions", label = "run" });
                }
                PreviousOptionsEnabled = EditorSettings.enterPlayModeOptionsEnabled;
                PreviousOptions = EditorSettings.enterPlayModeOptions;
                EditorSettings.enterPlayModeOptionsEnabled = true;
                EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
                EditorApplication.playModeStateChanged += Changed;
                EditorApplication.update += Tick;
                EditorApplication.EnterPlaymode();
            }
            catch (Exception e) { Debug.LogException(e); EditorApplication.Exit(1); }
        }

        public static CritterLibrary CreateReviewLibrary(CritterLibrary source, CritterRecipe recipe)
        {
            var selectedParts = new HashSet<string>();
            foreach (var fill in recipe.fills)
            {
                selectedParts.Add(fill.part_id);
                if (!string.IsNullOrEmpty(fill.connector_part_id)) selectedParts.Add(fill.connector_part_id);
            }
            return source.EditorCreateApprovedSkeletonClone(c =>
            {
                foreach (var skeleton in c.skeletons)
                {
                    string original = source.Catalog.FindSkeleton(skeleton.skeleton_id).status;
                    skeleton.status = skeleton.skeleton_id == recipe.skeleton_id && original != "rejected" ? "approved" : original;
                }
                foreach (var part in c.parts)
                    if (selectedParts.Contains(part.part_id) && part.inventory_kind == "production" && part.status != "rejected")
                        part.status = "approved";
            });
        }

        static void Changed(PlayModeStateChange state)
        {
            if (state != PlayModeStateChange.EnteredEditMode) return;
            EditorApplication.playModeStateChanged -= Changed;
            EditorSettings.enterPlayModeOptionsEnabled = PreviousOptionsEnabled;
            EditorSettings.enterPlayModeOptions = PreviousOptions;
            File.WriteAllText(Path.Combine(Output, "proof.json"), JsonUtility.ToJson(new ProofSummary { draws = Draws.ToArray(), courses = Metrics.ToArray() }, true));
            UnityEngine.Object.DestroyImmediate(Library);
            EditorApplication.Exit(Draws.Count == 2 && Draws.All(d => d.measurement_complete)
                && Draws.Single(d => d.path == "baked").color_submissions <= 2 ? 0 : 1);
        }

        static void Tick()
        {
            if (!EditorApplication.isPlaying) return;
            try
            {
                if (Holder != null)
                {
                    if (Recorder != null && !Recorder.Done) return;
                    if (Current.kind == "draws" && WaitingForNativeFrame()) return;
                    if (Current.kind == "draws") FinishDraws();
                    else if (Current.kind != "clip") Metrics.Add(Recorder.Metrics);
                    if (Target != null) { Target.Release(); UnityEngine.Object.Destroy(Target); Target = null; }
                    if (Override != null) { UnityEngine.Object.Destroy(Override); Override = null; }
                    UnityEngine.Object.DestroyImmediate(Holder); Holder = null; Recorder = null; Camera = null;
                }
                if (Jobs.Count == 0)
                {
                    EditorApplication.update -= Tick; Time.captureFramerate = 0; EditorApplication.ExitPlaymode(); return;
                }
                Current = Jobs.Dequeue(); StartJob();
            }
            catch (Exception e)
            {
                try { DisableDebugger(); } finally { RestoreIsolation(); }
                Debug.LogException(e); EditorApplication.update -= Tick;
                EditorApplication.Exit(1);
            }
        }

        static string DirectoryFor(Job job) => Path.Combine(Output, job.baked ? "baked" : "live", job.kind + "_" + job.label);

        static void StartJob()
        {
            Time.captureFramerate = 30;
            Holder = new GameObject("CreatureBakeProof");
            var groundMaterial = new Material(LibraryImporter.DefaultLitShader()) { color = new Color(.22f, .23f, .27f) };
            ReviewCourse.Build(Holder.transform, groundMaterial, ramp: Current.kind == "review");
            Physics.SyncTransforms();
            var options = AssemblyOptions.Review; options.parent = Holder.transform; options.fallbackOnInvalid = false; options.layer = 30;
            Creature = new DefaultCreatureVisualFactory(Library, options, Current.baked).Build(
                new CreatureSpawnRequest { recipe = Recipe, parent = Holder.transform, layer = 30 }).GetComponent<AssembledCreature>();
            var gait = Creature.GetComponent<CreatureGait>();
            if (gait == null) throw new InvalidOperationException("CC_BAKE_PROOF: missing runtime gait");
            foreach (var renderer in Creature.Renderers) { renderer.renderer.updateWhenOffscreen = true; renderer.renderer.forceMatrixRecalculationPerRender = true; }
            var light = new GameObject("ProofLight"); light.transform.SetParent(Holder.transform, false);
            var source = light.AddComponent<Light>(); source.type = LightType.Directional; source.intensity = 1f; source.shadows = LightShadows.Hard;
            ProofLight = source;
            light.transform.rotation = Quaternion.Euler(45, -35, 0);
            if (Current.kind == "draws") { StartDraws(); return; }
            Recorder = Holder.AddComponent<LocomotionRecorder>();
            float speed = LocomotionCapture.SpeedFor(gait.Block, Current.kind == "clip" ? "walk" : Current.label);
            var path = Current.kind == "turns" ? ReviewCourse.Turns(speed) : Current.kind == "review" ? ReviewCourse.Path(speed)
                : ReviewCourse.Still(Current.kind == "clip" ? 2f : 9f);
            Recorder.UseAuthoredContactSchedules = Current.kind == "reactions";
            Recorder.Begin(gait, path, new LocomotionMetrics { skeleton_id = Recipe.skeleton_id,
                speed_label = (Current.baked ? "baked_" : "live_") + Current.kind + "_" + Current.label, speed_mps = speed }, DirectoryFor(Current));
            if (Current.kind == "clip")
            {
                var selected = Creature.Animator.runtimeAnimatorController.animationClips.First(c => c.name == Current.label);
                Override = new AnimatorOverrideController(Creature.Animator.runtimeAnimatorController);
                var overrides = new List<KeyValuePair<AnimationClip, AnimationClip>>(); Override.GetOverrides(overrides);
                for (int i = 0; i < overrides.Count; i++) overrides[i] = new KeyValuePair<AnimationClip, AnimationClip>(overrides[i].Key, selected);
                Override.ApplyOverrides(overrides);
                Creature.GetComponent<CreatureMotion>().enabled = false;
                gait.enabled = false;
                Creature.Animator.runtimeAnimatorController = Override;
                Creature.Animator.Play("Idle", 0, 0);
            }
            if (Current.kind == "reactions")
            {
                var motion = Creature.GetComponent<CreatureMotion>(); bool hit = false, stun = false, idle = false, dead = false;
                Recorder.Script = t =>
                {
                    if (!hit && t >= 2.5f) { hit = true; motion.PlayHit(); }
                    if (!stun && t >= 4) { stun = true; motion.SetState(CreatureState.Stunned); }
                    if (!idle && t >= 6) { idle = true; motion.SetState(CreatureState.Idle); }
                    if (!dead && t >= 7) { dead = true; motion.SetState(CreatureState.Dead); }
                };
            }
        }

        static void StartDraws()
        {
            Directory.CreateDirectory(DirectoryFor(Current));
            var cameraObject = new GameObject("IsolatedBodyCamera"); cameraObject.transform.SetParent(Holder.transform, false);
            Camera = cameraObject.AddComponent<Camera>(); Camera.orthographic = true;
            Camera.orthographicSize = Mathf.Max(.5f, Creature.NeutralBoundsLocal.size.magnitude * .6f);
            var center = Creature.transform.TransformPoint(Creature.NeutralBoundsLocal.center);
            Camera.transform.position = center + new Vector3(16, 18, 16).normalized * 30; Camera.transform.LookAt(center);
            Camera.clearFlags = CameraClearFlags.SolidColor; Camera.backgroundColor = new Color(.12f, .13f, .16f);
            Camera.cullingMask = 1 << 30; Camera.farClipPlane = 100; Camera.depth = 100;
            Target = new RenderTexture(512, 512, 24) { name = "CritterBakeIsolatedColor" }; Target.Create(); Camera.targetTexture = Target;
            DebuggerType = FindEditorType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerUtility", "UnityEditorInternal.FrameDebuggerUtility");
            EventDataType = FindEditorType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerEventData", "UnityEditorInternal.FrameDebuggerEventData");
            if (DebuggerType != null && EventDataType != null
                && (bool)(DebuggerType.GetProperty("locallySupported", Flags)?.GetValue(null) ?? false))
            {
                var profiler = FindEditorType("UnityEditorInternal.ProfilerDriver");
                if (profiler == null || !(bool)(profiler.GetMethod("IsConnectionEditor", Flags)?.Invoke(null, null) ?? false))
                    throw new NotSupportedException("draw proof requires the local Editor profiler connection");
                CaptureConnection = (int)profiler.GetProperty("connectedProfiler", Flags).GetValue(null);
                CaptureIdentifier = profiler.GetMethod("GetConnectionIdentifier", Flags).Invoke(null, new object[] { CaptureConnection }) as string;
                var peers = (int[])profiler.GetMethod("GetAvailableProfilers", Flags).Invoke(null, null);
                AvailablePeers = peers.Select(guid => new ProfilerPeer { guid = guid,
                    identifier = profiler.GetMethod("GetConnectionIdentifier", Flags).Invoke(null, new object[] { guid }) as string,
                    connectable = (bool)profiler.GetMethod("IsIdentifierConnectable", Flags).Invoke(null, new object[] { guid }) }).ToArray();
                var gameViewType = FindEditorType("UnityEditor.GameView");
                if (gameViewType == null) throw new NotSupportedException("native frame capture requires an actual Game View");
                CaptureView = EditorWindow.GetWindow(gameViewType);
                CaptureView.Show();
                CaptureView.Focus();
                Camera.targetTexture = null;
                IsolateScene();
                CaptureView.Repaint();
                if (NativeDebuggerEnabled()) throw new InvalidOperationException("an existing native frame capture is already enabled");
                var windowType = FindEditorType("UnityEditor.FrameDebuggerWindow");
                if (windowType == null) throw new NotSupportedException("native Frame Debugger window is unavailable");
                PreviousPaused = EditorApplication.isPaused;
                DebuggerWindow = windowType.GetMethod("OpenWindow", Flags).Invoke(null, null) as EditorWindow;
                if (DebuggerWindow == null) throw new InvalidOperationException("native Frame Debugger window failed to open");
                windowType.GetMethod("RequestTogglingFrameDebugger", Flags).Invoke(DebuggerWindow, null);
                DebuggerStarted = true;
                EnabledOnRequest = NativeDebuggerEnabled();
            }
            CaptureFrame = Time.frameCount;
            CaptureStartedAt = EditorApplication.timeSinceStartup;
            WholeFrameRequested = false;
            NativeReplay = null;
        }

        static bool WaitingForNativeFrame()
        {
            if (!DebuggerStarted) return Time.frameCount - CaptureFrame < 4;
            CaptureView.Repaint();
            DebuggerWindow.Repaint();
            EditorApplication.QueuePlayerLoopUpdate();
            if (NativeReplay != null) return ReadSelectedNativeEvent();
            double elapsed = EditorApplication.timeSinceStartup - CaptureStartedAt;
            if (elapsed < .5) return true;
            if (!NativeDebuggerEnabled()) return elapsed < 5;
            var events = DebuggerType.GetMethod("GetFrameEvents", Flags).Invoke(null, null) as Array;
            if (events == null || events.Length == 0) return elapsed < 10;
            if (!WholeFrameRequested)
            {
                int count = (int)DebuggerType.GetProperty("count", Flags).GetValue(null);
                DebuggerWindow.GetType().GetMethod("ChangeFrameEventLimit", Flags, null, new[] { typeof(int) }, null)
                    .Invoke(DebuggerWindow, new object[] { count });
                WholeFrameRequested = true;
                CaptureStartedAt = EditorApplication.timeSinceStartup;
                return true;
            }
            string isolationProblem = IsolationProblem();
            if (isolationProblem != null) throw new InvalidOperationException("CC_BAKE_PROOF_ISOLATION: " + isolationProblem);
            var bodies = Creature.Renderers.Select(r => r.renderer).ToArray();
            NativeReplay = new NativeCapture
            {
                events = events, readHash = DebuggerType.GetProperty("eventsHash", Flags),
                readData = DebuggerType.GetMethod("GetFrameEventData", Flags),
                setLimit = DebuggerWindow.GetType().GetMethod("ChangeFrameEventLimit", Flags, null, new[] { typeof(int) }, null),
                data = Activator.CreateInstance(EventDataType, true), bodies = bodies,
                rendererIds = bodies.Select(r => EntityId(r)).ToArray(),
                gameObjectIds = bodies.Select(r => EntityId(r.gameObject)).ToArray(),
                meshIds = bodies.Select(r => EntityId(r.sharedMesh)).ToArray(),
            };
            NativeReplay.eventsHash = (int)NativeReplay.readHash.GetValue(null);
            NativeReplay.identities = bodies.Select((r, i) => new BodyIdentity { name = r.name, hierarchy = Hierarchy(r.transform),
                active = r.gameObject.activeInHierarchy, enabled = r.enabled, layer = r.gameObject.layer,
                vertices = r.sharedMesh.vertexCount, indices = (int)Enumerable.Range(0, r.sharedMesh.subMeshCount).Sum(s => (long)r.sharedMesh.GetIndexCount(s)),
                bones = r.bones.Length, bone_names = r.bones.Select(b => b == null ? "<missing>" : Hierarchy(b)).ToArray(),
                materials = r.sharedMaterials.Length, material_names = r.sharedMaterials.Select(m => m == null ? "<missing>" : m.name).ToArray(),
                shader_names = r.sharedMaterials.Select(m => m == null ? "<missing>" : m.shader.name).ToArray(), world_bounds = r.bounds,
                renderer_entity_id = EntityText(NativeReplay.rendererIds[i]), game_object_entity_id = EntityText(NativeReplay.gameObjectIds[i]),
                mesh_entity_id = EntityText(NativeReplay.meshIds[i]) }).ToArray();
            return true;
        }

        static bool ReadSelectedNativeEvent()
        {
            var replay = NativeReplay;
            if (replay.problem != null) return false;
            string isolationProblem = IsolationProblem();
            if (isolationProblem != null)
            {
                replay.problem = "scene isolation changed during captured replay: " + isolationProblem;
                return false;
            }
            if (!NativeDebuggerEnabled() || (int)replay.readHash.GetValue(null) != replay.eventsHash)
            {
                replay.problem = "native captured event sequence changed during per-event replay";
                return false;
            }
            double now = EditorApplication.timeSinceStartup;
            int index = replay.restoring ? replay.events.Length - 1 : replay.index;
            if (!replay.selected)
            {
                replay.setLimit.Invoke(DebuggerWindow, new object[] { index + 1 });
                replay.selected = true;
                replay.selectionStarted = now;
                return true;
            }
            bool ready = (bool)replay.readData.Invoke(null, new[] { (object)index, replay.data });
            int returned = Field(replay.data, "m_FrameEventIndex") is int frame ? frame : -1;
            if (!ready || returned != index)
            {
                if (now - replay.selectionStarted < 5) return true;
                if (!replay.restoring) replay.diagnostics.Add(NativeDiagnostic(index, replay.data, false));
                replay.problem = "native event replay timed out at " + index + "; ready=" + ready + "; returned=" + returned;
                return false;
            }
            if (replay.restoring) return now - replay.selectionStarted < .25;
            replay.diagnostics.Add(NativeDiagnostic(index, replay.data, true));
            replay.index++;
            replay.selected = false;
            if (replay.index == replay.events.Length) replay.restoring = true;
            return true;
        }

        static NativeEventDiagnostic NativeDiagnostic(int index, object data, bool ready)
        {
            var replay = NativeReplay;
            object info = replay.events.GetValue(index), obj = Field(info, "m_Obj");
            bool belongs = obj is Component component && component.transform.IsChildOf(Creature.transform)
                || obj is GameObject go && go.transform.IsChildOf(Creature.transform);
            object Value(string field) => data == null ? null : Field(data, field);
            var componentId = Value("m_ComponentEntityId");
            var meshId = Value("m_MeshEntityId");
            var nativeMeshIds = Value("m_MeshEntityIds") as Array;
            if (ready)
                belongs |= replay.bodies.Any(r => Value("m_Mesh") as Mesh == r.sharedMesh)
                    || replay.rendererIds.Any(id => SameEntity(componentId, id))
                    || replay.gameObjectIds.Any(id => SameEntity(componentId, id))
                    || replay.meshIds.Any(id => SameEntity(meshId, id))
                    || nativeMeshIds != null && nativeMeshIds.Cast<object>().Any(id => replay.meshIds.Any(expected => SameEntity(id, expected)));
            if (ready && Field(info, "m_Type")?.ToString() == "Mesh" && IsolationProblem() == null)
                belongs = true;
            return new NativeEventDiagnostic { event_index = index, data_ready = ready, body_match = belongs,
                data_frame_index = Value("m_FrameEventIndex") is int frame ? frame : -1,
                event_type = Field(info, "m_Type")?.ToString(),
                event_name = DebuggerType.GetMethod("GetFrameEventInfoName", Flags)?.Invoke(null, new object[] { index }) as string,
                object_name = (obj as UnityEngine.Object)?.name, object_type = obj?.GetType().FullName,
                object_entity_id = EntityText(EntityId(obj as UnityEngine.Object)),
                component_entity_id = EntityText(componentId), mesh_entity_id = EntityText(meshId),
                mesh_entity_ids = nativeMeshIds?.Cast<object>().Select(EntityText).ToArray(),
                mesh_name = (Value("m_Mesh") as Mesh)?.name,
                draw_calls = Value("m_DrawCallCount") is int calls ? calls : -1,
                vertex_count = Value("m_VertexCount") is int vertices ? vertices : -1,
                index_count = Value("m_IndexCount") is int indices ? indices : -1,
                pass_name = Value("m_PassName") as string, light_mode = Value("m_PassLightMode") as string,
                shader = Value("m_RealShaderName") as string, render_target = Value("m_RenderTargetName") as string };
        }

        static string Hierarchy(Transform transform)
        {
            var names = new List<string>();
            for (var current = transform; current != null; current = current.parent) names.Add(current.name);
            names.Reverse();
            return transform.gameObject.scene.name + "/" + string.Join("/", names);
        }

        static IEnumerable<T> SceneObjects<T>() where T : Component =>
            Resources.FindObjectsOfTypeAll<T>().Where(c => !EditorUtility.IsPersistent(c) && c.gameObject.scene.IsValid());

        static void IsolateScene()
        {
            var bodies = new HashSet<Renderer>(Creature.Renderers.Select(r => (Renderer)r.renderer));
            var inventory = new List<SceneItem>();
            void Record(Behaviour behaviour, string kind, bool before, int beforeMask = 0, int captureMask = 0)
            {
                inventory.Add(new SceneItem { kind = kind, hierarchy = Hierarchy(behaviour.transform), name = behaviour.name,
                    entity_id = EntityText(EntityId(behaviour)), scene = behaviour.gameObject.scene.name,
                    active = behaviour.gameObject.activeInHierarchy, layer = behaviour.gameObject.layer,
                    before_enabled = before, capture_enabled = behaviour.enabled, before_culling_mask = beforeMask,
                    capture_culling_mask = captureMask, light_type = behaviour is Light light ? light.type.ToString() : null });
            }
            foreach (var other in SceneObjects<Camera>())
            {
                bool enabled = other.enabled; int mask = other.cullingMask;
                if (other != Camera) { OtherCameras.Add(other, (enabled, mask)); other.enabled = false; other.cullingMask = 0; }
                Record(other, "Camera", enabled, mask, other.cullingMask);
            }
            foreach (var renderer in SceneObjects<Renderer>())
            {
                bool enabled = renderer.enabled, body = bodies.Contains(renderer);
                if (!body) { OtherRenderers.Add(renderer, enabled); renderer.enabled = false; }
                inventory.Add(new SceneItem { kind = renderer.GetType().Name, hierarchy = Hierarchy(renderer.transform),
                    name = renderer.name, entity_id = EntityText(EntityId(renderer)), scene = renderer.gameObject.scene.name,
                    active = renderer.gameObject.activeInHierarchy, layer = renderer.gameObject.layer,
                    before_enabled = enabled, capture_enabled = renderer.enabled, body = body });
            }
            foreach (var other in SceneObjects<Light>())
            {
                bool enabled = other.enabled;
                if (other != ProofLight) { OtherLights.Add(other, enabled); other.enabled = false; }
                Record(other, "Light", enabled);
            }
            foreach (var canvas in SceneObjects<Canvas>())
            {
                bool enabled = canvas.enabled; OtherCanvases.Add(canvas, enabled); canvas.enabled = false;
                Record(canvas, "Canvas", enabled);
            }
            SceneInventory = inventory.ToArray();
        }

        static string IsolationProblem()
        {
            if (SceneInventory == null) return "scene inventory has not been captured";
            if (Camera == null || !Camera.enabled || !Camera.gameObject.activeInHierarchy || Camera.cullingMask != 1 << 30)
                return "isolated camera is not the sole active body-layer camera";
            if (ProofLight == null || !ProofLight.enabled || !ProofLight.gameObject.activeInHierarchy || ProofLight.type != LightType.Directional)
                return "isolated directional light is unavailable";
            var bodies = new HashSet<Renderer>(Creature.Renderers.Select(r => (Renderer)r.renderer));
            if (bodies.Count == 0 || bodies.Any(r => !r.enabled || !r.gameObject.activeInHierarchy || r.gameObject.layer != 30))
                return "body renderer is missing, disabled, inactive or outside the camera layer";
            foreach (var other in SceneObjects<Camera>())
                if (other != Camera && (!OtherCameras.ContainsKey(other) || other.enabled || other.cullingMask != 0))
                    return "unisolated camera: " + Hierarchy(other.transform);
            foreach (var other in SceneObjects<Renderer>())
                if (!bodies.Contains(other) && (!OtherRenderers.ContainsKey(other) || other.enabled))
                    return "unisolated renderer: " + Hierarchy(other.transform);
            foreach (var other in SceneObjects<Light>())
                if (other != ProofLight && (!OtherLights.ContainsKey(other) || other.enabled))
                    return "unisolated light: " + Hierarchy(other.transform);
            foreach (var canvas in SceneObjects<Canvas>())
                if (!OtherCanvases.ContainsKey(canvas) || canvas.enabled)
                    return "unisolated canvas: " + Hierarchy(canvas.transform);
            return null;
        }

        static void RestoreIsolation()
        {
            foreach (var pair in OtherCameras)
                if (pair.Key != null) { pair.Key.enabled = pair.Value.enabled; pair.Key.cullingMask = pair.Value.mask; }
            foreach (var pair in OtherRenderers) if (pair.Key != null) pair.Key.enabled = pair.Value;
            foreach (var pair in OtherLights) if (pair.Key != null) pair.Key.enabled = pair.Value;
            foreach (var pair in OtherCanvases) if (pair.Key != null) pair.Key.enabled = pair.Value;
            OtherCameras.Clear(); OtherRenderers.Clear(); OtherLights.Clear(); OtherCanvases.Clear(); SceneInventory = null;
        }

        static Type FindEditorType(params string[] names)
        {
            foreach (var name in names)
                foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
                {
                    var type = assembly.GetType(name, false);
                    if (type != null) return type;
                }
            return null;
        }

        static object Field(object value, string name) => value.GetType().GetField(name, Flags)?.GetValue(value);
        static object EntityId(UnityEngine.Object value) => value == null ? null
            : typeof(UnityEngine.Object).GetMethod("GetEntityId", Flags, null, Type.EmptyTypes, null)?.Invoke(value, null);
        static string EntityText(object value) => value?.ToString() ?? "unavailable";
        static bool SameEntity(object a, object b) => a != null && b != null && a.Equals(b);
        static bool NativeDebuggerEnabled() =>
            (bool)(FindEditorType("UnityEngine.FrameDebugger")?.GetProperty("enabled", Flags)?.GetValue(null) ?? false);
        static void DisableDebugger()
        {
            if (!DebuggerStarted) return;
            DebuggerWindow.GetType().GetMethod("DisableFrameDebugger", Flags).Invoke(DebuggerWindow, null);
            EditorApplication.isPaused = PreviousPaused;
            DebuggerWindow.Close();
            DebuggerWindow = null;
            DebuggerStarted = false;
        }

        static void FinishDraws()
        {
            var result = new DrawMeasurement { path = Current.baked ? "baked" : "live", graphics_device = SystemInfo.graphicsDeviceName,
                pipeline = GraphicsSettings.defaultRenderPipeline == null ? "Built-in" : GraphicsSettings.defaultRenderPipeline.GetType().FullName,
                graphics_api = SystemInfo.graphicsDeviceType.ToString(), graphics_multithreaded = SystemInfo.graphicsMultiThreaded,
                batch_mode = Application.isBatchMode, debugger_assembly = DebuggerType?.Assembly.FullName,
                event_data_assembly = EventDataType?.Assembly.FullName,
                frame_debugger_supported = (bool)(DebuggerType?.GetProperty("locallySupported", Flags)?.GetValue(null) ?? false),
                profiler_connection = CaptureConnection,
                native_enabled = NativeDebuggerEnabled(), native_enabled_on_request = EnabledOnRequest,
                enable_requested = DebuggerStarted, editor_paused = EditorApplication.isPaused,
                toggle_pending = (bool)(DebuggerWindow?.GetType().GetProperty("togglingFrameDebuggerRequested", Flags)?.GetValue(DebuggerWindow) ?? false),
                profiler_identifier = CaptureIdentifier, available_peers = AvailablePeers,
                scene_inventory = SceneInventory, isolation_problem = IsolationProblem(),
                attribution = "Native Mesh draw-call counts in verified body-only scene; GPU-job entity IDs are diagnostic only",
                lighting = "One directional light; other scene lights disabled only during isolated capture",
                problem = "", events = Array.Empty<DrawEvent>() };
            try
            {
                if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null || DebuggerType == null || EventDataType == null
                    || !result.frame_debugger_supported)
                    throw new NotSupportedException("native frame capture unavailable: graphics=" + result.graphics_api
                        + ", multithreaded=" + result.graphics_multithreaded + ", batch=" + result.batch_mode
                        + ", debugger=" + result.debugger_assembly + ", supported=" + result.frame_debugger_supported
                        + ". Use a graphics-enabled editor with native locallySupported capture.");
                if (!result.native_enabled) throw new InvalidOperationException("native Frame Debugger lifecycle did not enable capture"
                    + "; local connection=" + result.profiler_connection + " (" + result.profiler_identifier + ")"
                    + "; pending=" + result.toggle_pending + "; paused=" + result.editor_paused);
                if (NativeReplay == null) throw new InvalidOperationException("frame debugger recorded no events");
                if (result.isolation_problem != null) throw new InvalidOperationException(result.isolation_problem);
                result.isolation_verified = true;
                var replay = NativeReplay;
                result.captured_event_count = replay.events.Length;
                result.bodies = replay.identities;
                var diagnostics = new List<NativeEventDiagnostic>(replay.diagnostics);
                while (diagnostics.Count < replay.events.Length)
                    diagnostics.Add(NativeDiagnostic(diagnostics.Count, null, false));
                result.native_events = diagnostics.ToArray();
                var rows = new List<DrawEvent>();
                foreach (var data in diagnostics)
                {
                    if (!data.data_ready || data.data_frame_index != data.event_index || !data.body_match || data.draw_calls <= 0) continue;
                    string text = (data.light_mode + " " + data.pass_name).ToLowerInvariant();
                    string category = data.event_name != null && (data.event_name.Contains("DepthPass") || data.event_name.Contains("UpdateDepthTexture"))
                        || text.Contains("depth") ? "depth" : text.Contains("shadow") ? "shadow"
                        : text.Contains("forward") || text.Contains("gbuffer") || text.Contains("deferred") || text.Contains("unlit") ? "color" : "other";
                    if (category == "shadow") result.shadow_submissions += data.draw_calls;
                    else if (category == "depth") result.depth_submissions += data.draw_calls;
                    else if (category == "color") result.color_submissions += data.draw_calls;
                    else result.other_submissions += data.draw_calls;
                    rows.Add(new DrawEvent { event_index = data.event_index, draw_calls = data.draw_calls,
                        light_mode = data.light_mode, pass_name = data.pass_name, category = category, event_name = data.event_name });
                }
                result.events = rows.ToArray();
                bool unreadable = replay.problem != null || diagnostics.Any(d => !d.data_ready || d.data_frame_index != d.event_index);
                result.measurement_complete = !unreadable && result.color_submissions > 0 && result.other_submissions == 0;
                if (!result.measurement_complete) result.problem = replay.problem ?? "missing/unreadable/unclassified body draw events; no renderer-count substitution";
            }
            catch (Exception e) { result.problem = e.ToString(); result.measurement_complete = false; }
            finally
            {
                try
                {
                    if (CaptureView == null || Camera.targetTexture != null || NativeReplay == null || NativeReplay.problem != null)
                        throw new InvalidOperationException("same-frame native Game View image unavailable; no manual camera-render substitute");
                    var playModeView = FindEditorType("UnityEditor.PlayModeView");
                    var rendered = playModeView?.GetField("m_TargetTexture", Flags)?.GetValue(CaptureView) as RenderTexture;
                    if (rendered == null || !rendered.IsCreated()) throw new InvalidOperationException("captured Game View target is unavailable");
                    var displayRectProperty = CaptureView.GetType().GetProperty("deviceFlippedTargetInView", Flags);
                    if (displayRectProperty == null || displayRectProperty.PropertyType != typeof(Rect))
                        throw new InvalidOperationException("captured Game View display orientation is unavailable");
                    var displayRect = (Rect)displayRectProperty.GetValue(CaptureView);
                    bool flipImageY = displayRect.height < 0;
                    result.camera = new CameraEvidence { hierarchy = Hierarchy(Camera.transform), active = Camera.gameObject.activeInHierarchy,
                        enabled = Camera.enabled, culling_mask = Camera.cullingMask, orthographic = Camera.orthographic,
                        orthographic_size = Camera.orthographicSize, aspect = Camera.aspect, position = Camera.transform.position,
                        euler_angles = Camera.transform.eulerAngles, rendering_path = Camera.actualRenderingPath.ToString(),
                        target_name = rendered.name, target_width = rendered.width, target_height = rendered.height,
                        game_view_device_flipped_target_rect = displayRect, image_copy_flipped_y = flipImageY };
                    Target.Release(); Target.width = rendered.width; Target.height = rendered.height; Target.Create();
                    if (flipImageY) Graphics.Blit(rendered, Target, new Vector2(1, -1), new Vector2(0, 1));
                    else Graphics.Blit(rendered, Target);
                }
                catch (Exception e) { result.measurement_complete = false; result.problem += "; " + e.Message; }
                finally { try { DisableDebugger(); } finally { RestoreIsolation(); } }
            }
            Draws.Add(result);
            File.WriteAllText(Path.Combine(DirectoryFor(Current), "draw_submissions.json"), JsonUtility.ToJson(result, true));
            var previous = RenderTexture.active; RenderTexture.active = Target;
            var image = new Texture2D(Target.width, Target.height, TextureFormat.RGB24, false);
            image.ReadPixels(new Rect(0, 0, Target.width, Target.height), 0, 0); image.Apply(); RenderTexture.active = previous;
            File.WriteAllBytes(Path.Combine(DirectoryFor(Current), "iso.png"), image.EncodeToPNG()); UnityEngine.Object.Destroy(image);
        }
    }
}
