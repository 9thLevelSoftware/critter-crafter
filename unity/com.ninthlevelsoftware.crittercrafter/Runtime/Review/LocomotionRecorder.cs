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
        /// <summary>Torso planar speed after warmup (grounded bodies travel in hauls, not at the root's speed).</summary>
        public float body_speed_min_mps = float.MaxValue;
        public float body_speed_max_mps;
        public float body_speed_mean_mps;
        /// <summary>Largest distance the reach clamp moved a planted foot's plant point in one frame (m).</summary>
        public float max_plant_rewrite_m;
        /// <summary>Frames after warmup in which a planted leg was being clamped to its reach.</summary>
        public int clamped_planted_frames;
        /// <summary>Frames after warmup in which a strained leg wanted to re-step but was not allowed to.</summary>
        public int lift_blocked_frames;
        /// <summary>Highest the body rose above where the feet's plane put it on the first frame (m). A runaway shows here.</summary>
        public float max_body_rise_m;
        /// <summary>Highest a planted supporting foot's sole sat above the ground (m): planted feet must not hover.</summary>
        public float max_planted_hover_m;
        /// <summary>The furthest a foot travelled during one stance (m); the per-frame slip can hide a long drag.</summary>
        public float max_stance_drift_m;
        /// <summary>The leg and frame of the largest planted slip.</summary>
        public string max_slip_leg = "";
        public int max_slip_frame = -1;
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
        /// <summary>Called every frame with the elapsed course time (reaction captures trigger states from it).</summary>
        public System.Action<float> Script;

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
        readonly Dictionary<string, Vector3> _stanceStart = new Dictionary<string, Vector3>();
        float _baseBodyHeight;
        readonly StringBuilder _csv = new StringBuilder("frame,time,speed,body_speed,residual,slip,planted_supports,yaw_lag_deg,surge_m\n");
        Vector3 _lastBody;
        float _bodyTravel, _bodyTime;
        readonly StringBuilder _legCsv = new StringBuilder("frame,leg,planted,forced,ankle_reach_frac,hip_y,foot_x,foot_y,foot_z,clamped,lift_blocked,plant_rewrite_m\n");

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
            _started = true;
        }

        void Place(float time)
        {
            ReviewCourse.Sample(_path, time, out var pos, out var heading);
            pos.y = ReviewCourse.GroundY(pos);
            _gait.transform.SetPositionAndRotation(pos, Quaternion.Euler(0f, heading, 0f));
        }

        void Update()
        {
            if (_gait == null || Done) return;
            _time += Time.deltaTime;
            Place(_time);
            Script?.Invoke(_time);
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
            float residual = 0f, slip = 0f, rewrite = 0f, hover = 0f, drift = 0f;
            string slipLeg = "";
            bool clampedPlanted = false, liftBlocked = false;
            int planted = 0;
            var now = new Dictionary<string, Vector3>();
            foreach (var leg in _gait.Legs)
            {
                rewrite = Mathf.Max(rewrite, leg.plantRewrite);
                clampedPlanted |= leg.planted && leg.clamped;
                liftBlocked |= leg.liftBlocked;
                if (!_tips.TryGetValue(leg.branchId, out var tip) || leg.weight < 0.999f) continue;
                Vector3 p = tip.position;
                residual = Mathf.Max(residual, Vector3.Distance(p, leg.position));
                m.max_penetration_m = Mathf.Max(m.max_penetration_m, ReviewCourse.GroundY(p) - p.y);
                if (!leg.planted) { _stanceStart.Remove(leg.branchId); continue; }
                if (leg.support) planted++;
                now[leg.branchId] = p;
                if (!_stanceStart.TryGetValue(leg.branchId, out var stanceStart)) _stanceStart[leg.branchId] = stanceStart = p;
                drift = Mathf.Max(drift, Vector3.Distance(stanceStart, p));
                if (leg.support) hover = Mathf.Max(hover, leg.plant.y - (ReviewCourse.GroundY(leg.plant) + leg.homeLocal.y));
                if (_lastPlanted.TryGetValue(leg.branchId, out var last))
                {
                    float d = Vector3.Distance(last, p);
                    if (d > slip) { slip = d; slipLeg = leg.branchId; }
                }
            }
            foreach (var leg in _gait.Legs)
            {
                // Hinge legs: ankle target distance from the hinge root as a fraction of thigh + shin.
                float frac = -1f;
                if (leg.hinge && leg.target != null && leg.hip != null && leg.hingeReach > 0f)
                {
                    Transform root = leg.coxaAim != null ? leg.hip.GetChild(0) : leg.hip;
                    frac = Vector3.Distance(root.position, leg.target.position) / leg.hingeReach;
                }
                _legCsv.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0},{1},{2},{3},{4:F3},{5:F3},{6:F3},{7:F3},{8:F3},{9},{10},{11:F3}",
                    _frame, leg.branchId, leg.planted ? 1 : 0, leg.forced ? 1 : 0, frac,
                    leg.hip != null ? leg.hip.position.y : 0f, leg.position.x, leg.position.y, leg.position.z,
                    leg.clamped ? 1 : 0, leg.liftBlocked ? 1 : 0, leg.plantRewrite));
            }
            _lastPlanted.Clear();
            foreach (var kv in now) _lastPlanted[kv.Key] = kv.Value;
            if (_frame > 0)
            {
                m.max_ik_residual_m = Mathf.Max(m.max_ik_residual_m, residual);
                if (_frame >= m.warmup_frames)
                {
                    if (slip > m.max_planted_slip_m) { m.max_slip_leg = slipLeg; m.max_slip_frame = _frame; }
                    m.max_planted_slip_m = Mathf.Max(m.max_planted_slip_m, slip);
                    m.max_plant_rewrite_m = Mathf.Max(m.max_plant_rewrite_m, rewrite);
                    if (clampedPlanted) m.clamped_planted_frames++;
                    if (liftBlocked) m.lift_blocked_frames++;
                }
                m.min_planted_supports = Mathf.Min(m.min_planted_supports, planted);
                if (_frame >= m.warmup_frames)
                {
                    m.max_planted_hover_m = Mathf.Max(m.max_planted_hover_m, hover);
                    m.max_stance_drift_m = Mathf.Max(m.max_stance_drift_m, drift);
                }
            }
            m.cadence_hz = Mathf.Max(m.cadence_hz, (float)_gait.Current.cadenceHz);
            m.overspeed |= _gait.Current.overspeed;
            float bodySpeed = 0f;
            Transform body = _gait.Body != null ? _gait.Body : _gait.transform;
            Vector3 b = body.position;
            float bodyHeight = b.y - _gait.transform.position.y;
            if (_frame == 0) _baseBodyHeight = bodyHeight;
            else if (_frame >= m.warmup_frames) m.max_body_rise_m = Mathf.Max(m.max_body_rise_m, bodyHeight - _baseBodyHeight);
            if (_frame > 0 && Time.deltaTime > 0f)
            {
                Vector3 d = b - _lastBody;
                d.y = 0f;
                bodySpeed = d.magnitude / Time.deltaTime;
                if (_frame >= m.warmup_frames)
                {
                    m.body_speed_min_mps = Mathf.Min(m.body_speed_min_mps, bodySpeed);
                    m.body_speed_max_mps = Mathf.Max(m.body_speed_max_mps, bodySpeed);
                    _bodyTravel += d.magnitude;
                    _bodyTime += Time.deltaTime;
                    m.body_speed_mean_mps = _bodyTravel / _bodyTime;
                }
            }
            _lastBody = b;
            _csv.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0},{1:F3},{2:F3},{3:F3},{4:F4},{5:F4},{6},{7:F2},{8:F3}",
                _frame, _time, _gait.Speed, bodySpeed, residual, slip, planted, _gait.YawLag, _gait.Surge));
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
