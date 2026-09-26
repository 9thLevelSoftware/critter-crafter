using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using CritterCrafter.Locomotion;
using UnityEngine;

namespace CritterCrafter.Review
{
    [Serializable]
    public class LocomotionMetrics
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
        /// <summary>Frames at the start (standing to full speed in one frame) excluded from slip.</summary>
        public int warmup_frames;
        /// <summary>Leg lifts in the 0.4 s after an instant heading snap, excluding the replant itself.</summary>
        public int steps_in_turn_window;
        /// <summary>Planted-tip slip in the turn window, excluding the snap/replant frame.</summary>
        public float max_turn_slip_m;
        public int instant_turns;
    }

    /// <summary>
    /// Play Mode review driver: moves an assembled creature along a <see cref="ReviewCourse"/> path like an
    /// agent (instant turns included), then, at the end of each frame (after animation, IK and skinning),
    /// measures foot placement and optionally renders an isometric frame. Use with Time.captureFramerate.
    /// </summary>
    [DefaultExecutionOrder(-1000)]
    public class LocomotionRecorder : MonoBehaviour
    {
        public LocomotionMetrics Metrics { get; private set; }
        public bool Done { get; private set; }

        CreatureGait _gait;
        List<ReviewCourse.Waypoint> _path;
        float _duration, _time;
        int _frame;
        Camera _camera;
        RenderTexture _rt;
        string _outDir;
        int _cell;
        readonly Dictionary<string, Transform> _tips = new Dictionary<string, Transform>();
        readonly Dictionary<string, Vector3> _lastPlanted = new Dictionary<string, Vector3>();
        readonly Dictionary<string, bool> _wasPlanted = new Dictionary<string, bool>();
        readonly StringBuilder _csv = new StringBuilder("frame,time,speed,residual,slip,planted_supports\n");
        readonly StringBuilder _legCsv = new StringBuilder("frame,leg,planted,forced,ankle_reach_frac,hip_y,foot_x,foot_y,foot_z\n");
        float _lastHeading;
        float _turnWindowUntil = -1f;
        bool _awaitingUnwind;
        bool _sawYawLag;
        int _lastReplantCount;
        const float TurnWindowSeconds = 0.4f;
        const float InstantTurnDeg = 90f;
        const float UnwindDoneDeg = 5f;

        /// <summary>Invoked at the start of Update with the new sample time, before the creature is placed.</summary>
        public Action<float> BeforePlace;

        /// <param name="outDir">Frame/metrics folder, or null to measure only.</param>
        public void Begin(CreatureGait gait, List<ReviewCourse.Waypoint> path, LocomotionMetrics metrics,
            string outDir = null, int cell = 320)
        {
            _gait = gait;
            _path = path;
            _duration = path[path.Count - 1].time;
            Metrics = metrics;
            _outDir = outDir;
            _cell = cell;
            foreach (var t in gait.GetComponentsInChildren<Transform>(true))
                if (t.name.EndsWith("_ik_tip")) _tips[t.name.Substring(0, t.name.Length - "_ik_tip".Length)] = t;
            if (outDir != null)
            {
                Directory.CreateDirectory(outDir);
                _rt = new RenderTexture(cell, cell, 24, RenderTextureFormat.ARGB32) { antiAliasing = 4 };
                var go = new GameObject("ReviewCamera");
                go.transform.SetParent(transform.parent, false);
                _camera = go.AddComponent<Camera>();
                _camera.orthographic = true;
                _camera.clearFlags = CameraClearFlags.SolidColor;
                _camera.backgroundColor = new Color(0.12f, 0.13f, 0.16f);
                _camera.targetTexture = _rt;
                _camera.farClipPlane = 200f;
                _camera.enabled = false;
                var bounds = gait.GetComponent<AssembledCreature>().NeutralBoundsLocal;
                _camera.orthographicSize = Mathf.Max(1.6f, 0.9f * Mathf.Max(bounds.size.x, bounds.size.z));
            }
            // Rendering happens in LateUpdate (end-of-frame coroutines never run in batch mode), before
            // the player loop's skinning pass, so skinning is recalculated on render.
            foreach (var smr in gait.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                { smr.forceMatrixRecalculationPerRender = true; smr.updateWhenOffscreen = true; }
            Place(0f);
            gait.ResetFeet();
            _lastReplantCount = gait.ReplantCount;
            _started = true;
        }

        void Place(float time)
        {
            ReviewCourse.Sample(_path, time, out var pos, out var heading);
            pos.y = ReviewCourse.GroundY(pos);
            _gait.transform.SetPositionAndRotation(pos, Quaternion.Euler(0f, heading, 0f));
            _lastHeading = heading;
        }

        void Update()
        {
            if (_gait == null || Done) return;
            _time += Time.deltaTime;
            BeforePlace?.Invoke(_time);
            float prevHeading = _lastHeading;
            Place(_time);
            if (Mathf.Abs(Mathf.DeltaAngle(prevHeading, _lastHeading)) > InstantTurnDeg)
            {
                Metrics.instant_turns++;
                _awaitingUnwind = true;
                _sawYawLag = false;
            }
            // Window starts after visual yaw has actually unwound. Do not open it on the snap
            // frame, when YawLag is still 0 because gait has not stepped yet.
            if (_awaitingUnwind && Mathf.Abs(_gait.YawLag) > InstantTurnDeg * 0.5f)
                _sawYawLag = true;
            if (_awaitingUnwind && _sawYawLag && Mathf.Abs(_gait.YawLag) <= UnwindDoneDeg)
            {
                _turnWindowUntil = _time + TurnWindowSeconds;
                _awaitingUnwind = false;
            }
        }

        bool _started;

        /// <summary>After animation and Animation Rigging have posed the skeleton for this frame.</summary>
        void LateUpdate()
        {
            if (!_started || Done) return;
            {
                Measure();
                Render();
                _frame++;
                if (_time >= _duration)
                {
                    Done = true;
                    Metrics.frames = _frame;
                    if (_outDir != null)
                    {
                        File.WriteAllText(Path.Combine(_outDir, "frames.csv"), _csv.ToString());
                        File.WriteAllText(Path.Combine(_outDir, "legs.csv"), _legCsv.ToString());
                        File.WriteAllText(Path.Combine(_outDir, "metrics.json"), JsonUtility.ToJson(Metrics, true));
                    }
                }
            }
        }

        void Measure()
        {
            var m = Metrics;
            float residual = 0f, slip = 0f;
            int planted = 0;
            var now = new Dictionary<string, Vector3>();
            bool inTurnWindow = _turnWindowUntil >= 0f && _time <= _turnWindowUntil;
            bool snapFrame = _gait.ReplantCount != _lastReplantCount;
            _lastReplantCount = _gait.ReplantCount;
            var groupsLifted = new HashSet<int>();
            foreach (var leg in _gait.Legs)
            {
                bool wasPlanted;
                if (_wasPlanted.TryGetValue(leg.branchId, out wasPlanted) && wasPlanted && !leg.planted
                    && inTurnWindow && !snapFrame)
                    groupsLifted.Add(leg.group);
                _wasPlanted[leg.branchId] = leg.planted;
                if (!_tips.TryGetValue(leg.branchId, out var tip) || leg.weight < 0.999f) continue;
                Vector3 p = tip.position;
                residual = Mathf.Max(residual, Vector3.Distance(p, leg.position));
                m.max_penetration_m = Mathf.Max(m.max_penetration_m, ReviewCourse.GroundY(p) - p.y);
                if (!leg.planted) continue;
                if (leg.support) planted++;
                now[leg.branchId] = p;
                if (_lastPlanted.TryGetValue(leg.branchId, out var last)) slip = Mathf.Max(slip, Vector3.Distance(last, p));
            }
            m.steps_in_turn_window += groupsLifted.Count;
            foreach (var leg in _gait.Legs)
            {
                // Hinge legs: ankle target distance from the hinge root as a fraction of thigh + shin.
                float frac = -1f;
                if (leg.hinge && leg.target != null && leg.hip != null && leg.hingeReach > 0f)
                {
                    Transform root = leg.coxaAim != null ? leg.hip.GetChild(0) : leg.hip;
                    frac = Vector3.Distance(root.position, leg.target.position) / leg.hingeReach;
                }
                _legCsv.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0},{1},{2},{3},{4:F3},{5:F3},{6:F3},{7:F3},{8:F3}",
                    _frame, leg.branchId, leg.planted ? 1 : 0, leg.forced ? 1 : 0, frac,
                    leg.hip != null ? leg.hip.position.y : 0f, leg.position.x, leg.position.y, leg.position.z));
            }
            _lastPlanted.Clear();
            foreach (var kv in now) _lastPlanted[kv.Key] = kv.Value;
            if (_frame > 0)
            {
                m.max_ik_residual_m = Mathf.Max(m.max_ik_residual_m, residual);
                // Warmup does not apply inside the turn window; the snap/replant frame is always excluded.
                bool countSlip = !snapFrame && (_frame >= m.warmup_frames || inTurnWindow);
                if (countSlip) m.max_planted_slip_m = Mathf.Max(m.max_planted_slip_m, slip);
                if (inTurnWindow && !snapFrame) m.max_turn_slip_m = Mathf.Max(m.max_turn_slip_m, slip);
                m.min_planted_supports = Mathf.Min(m.min_planted_supports, planted);
            }
            m.cadence_hz = Mathf.Max(m.cadence_hz, (float)_gait.Current.cadenceHz);
            m.overspeed |= _gait.Current.overspeed;
            _csv.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0},{1:F3},{2:F3},{3:F4},{4:F4},{5}",
                _frame, _time, _gait.Speed, residual, slip, planted));
        }

        void Render()
        {
            if (_camera == null) return;
            var block = _gait.Block;
            float height = block.hip_height_m > 0.0 ? (float)block.hip_height_m : 0.3f;
            var target = _gait.transform.position + Vector3.up * height * 0.6f;
            _camera.transform.position = target + new Vector3(16f, 18f, 16f).normalized * 30f;
            _camera.transform.LookAt(target);
            _camera.Render();
            var previous = RenderTexture.active;
            RenderTexture.active = _rt;
            var tex = new Texture2D(_cell, _cell, TextureFormat.RGB24, false);
            tex.ReadPixels(new Rect(0, 0, _cell, _cell), 0, 0);
            tex.Apply();
            RenderTexture.active = previous;
            File.WriteAllBytes(Path.Combine(_outDir, $"frame_{_frame:0000}.png"), tex.EncodeToPNG());
            Destroy(tex);
        }

        void OnDestroy()
        {
            if (_rt != null) _rt.Release();
        }
    }
}
