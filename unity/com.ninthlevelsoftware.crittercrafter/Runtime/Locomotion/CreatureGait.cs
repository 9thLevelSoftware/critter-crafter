using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Animations.Rigging;

namespace CritterCrafter.Locomotion
{
    /// <summary>
    /// Runtime foot placement. Measures the creature's actual planar velocity (whatever moves it: a
    /// NavMeshAgent, a character controller, a script), advances a gait clock from the catalog's
    /// locomotion block, and drives one Animation Rigging IK target per leg:
    /// planted feet stay fixed in the world, swinging feet arc to a predicted landing point found by a
    /// ground raycast. Runs after movement scripts and before the Animator evaluates the rig.
    /// </summary>
    [DefaultExecutionOrder(1000)]
    public class CreatureGait : MonoBehaviour
    {
        [Serializable]
        public class Leg
        {
            public string branchId;
            public Transform target;
            public Transform hip;
            /// <summary>Two-bone hinge legs: the IK target is the ankle, offset from the foot in the coxa frame.</summary>
            public bool hinge;
            public Vector3 ankleOffset;
            public Quaternion ankleRotation = Quaternion.identity;
            /// <summary>First chain bone; for insect legs it yaws toward the foot via an aim constraint.</summary>
            public Transform coxa;
            public Transform coxaAim;
            public Transform hint;
            public float hingeReach;
            // Neutral vectors in the body frame, measured from the coxa head.
            public Vector3 homeFromCoxa;
            public Vector3 femurFromCoxa;
            public Vector3 hintFromCoxa;
            public Vector3 coxaDirection;
            public MonoBehaviour constraint;
            public Vector3 homeLocal;
            public float reach;
            public float stroke;
            public float clearance;
            public double walkPhase;
            public double runPhase;
            public bool support;
            public bool attack;
            /// <summary>Legs sharing a walk phase offset step together (e.g. a hexapod tripod).</summary>
            public int group;

            [NonSerialized] public bool planted = true;
            [NonSerialized] public Vector3 plant;
            [NonSerialized] public Vector3 swingStart;
            [NonSerialized] public Vector3 position;
            [NonSerialized] public bool forced;
            [NonSerialized] public float swingT;
            [NonSerialized] public float swingDuration;
            [NonSerialized] public double lastSwingCycle = double.MinValue;
            [NonSerialized] public Vector3 home;
            /// <summary>Last frame's target was beyond reach and had to be pulled in.</summary>
            [NonSerialized] public bool clamped;
            [NonSerialized] public float weight = 1f;
        }

        [Tooltip("Layers raycast to find the ground under each foot.")]
        public LayerMask groundMask = ~0;
        [Tooltip("Velocity smoothing time (s).")]
        public float velocitySmoothing = 0.08f;
        [Tooltip("Maximum body pitch/roll from uneven footing (degrees).")]
        public float maxBodyTiltDeg = 15f;
        [Tooltip("Displacement in one frame treated as a teleport (m).")]
        public float teleportDistance = 2f;
        [Tooltip("Maximum visual body turn rate (deg/s). The root may snap to a new heading (NavMeshAgent with a huge angularSpeed); the body follows at this rate so planted feet are not dragged in one frame.")]
        public float bodyTurnRateDeg = 360f;
        [Tooltip("Rig weight; set to 0 to fall back to the baked overlay (LOD / off-screen).")]
        [Range(0f, 1f)] public float ikWeight = 1f;

        [SerializeField] List<Leg> legs = new List<Leg>();
        [SerializeField] Transform body;
        [SerializeField] Rig rig;
        [SerializeField] Vector3 bodyBaseLocalPosition;
        [SerializeField] Quaternion bodyBaseLocalRotation = Quaternion.identity;

        AssembledCreature _creature;
        CreatureMotion _motion;
        LocomotionData _block;
        Animator _animator;
        bool _hasSpeed, _hasGait, _hasPhase;
        bool _initialised;
        bool _run;
        bool _wasMoving;
        Vector3 _lastPosition;
        Vector3 _velocity;
        double _clock;
        float _bodyHeight, _bodyPitch, _bodyRoll;
        float _lastYaw, _yawLag;
        GaitParams _params;

        static readonly int SpeedParam = Animator.StringToHash("Speed");
        static readonly int GaitParam = Animator.StringToHash("Gait");
        static readonly int GaitPhaseParam = Animator.StringToHash("GaitPhase");

        public IReadOnlyList<Leg> Legs => legs;
        public GaitParams Current => _params;
        /// <summary>Gait clock in cycles (unbounded; the fractional part is the phase).</summary>
        public double Clock => _clock;
        public Vector3 Velocity => _velocity;
        public float Speed => new Vector3(_velocity.x, 0f, _velocity.z).magnitude;
        public LocomotionData Block => _block;

        internal void Configure(Transform bodyTransform, Rig locomotionRig, List<Leg> builtLegs, LayerMask mask)
        {
            body = bodyTransform;
            rig = locomotionRig;
            legs = builtLegs;
            var groups = new List<double>();
            foreach (var leg in legs)
            {
                int g = groups.FindIndex(x => Math.Abs(x - leg.walkPhase) < 1e-6);
                if (g < 0) { groups.Add(leg.walkPhase); g = groups.Count - 1; }
                leg.group = g;
            }
            groundMask = mask;
            bodyBaseLocalPosition = body.localPosition;
            bodyBaseLocalRotation = body.localRotation;
        }

        void Awake() => Bind();

        void Bind()
        {
            _creature = GetComponent<AssembledCreature>();
            _motion = GetComponent<CreatureMotion>();
            _block = _creature != null && _creature.Skeleton != null ? _creature.Skeleton.locomotion : null;
            _animator = _creature != null ? _creature.Animator : null;
            if (_animator != null && _animator.runtimeAnimatorController != null)
                foreach (var p in _animator.parameters)
                {
                    if (p.nameHash == SpeedParam) _hasSpeed = true;
                    else if (p.nameHash == GaitParam) _hasGait = true;
                    else if (p.nameHash == GaitPhaseParam) _hasPhase = true;
                }
        }

        /// <summary>Plant every foot at its home on the ground (spawn, teleport).</summary>
        public void ResetFeet()
        {
            if (_block == null) Bind();
            foreach (var leg in legs)
            {
                leg.plant = Ground(transform.TransformPoint(leg.homeLocal), leg.homeLocal.y);
                leg.position = leg.plant;
                leg.planted = true;
                leg.forced = false;
            }
            ApplyTargets();
            _lastPosition = transform.position;
            _lastYaw = transform.eulerAngles.y;
            _yawLag = 0f;
            _velocity = Vector3.zero;
            _initialised = true;
        }

        void OnEnable() => _initialised = false;

        void Update() => Step(Time.deltaTime);

        /// <summary>Advance the planner by <paramref name="dt"/> seconds (public for tests and capture).</summary>
        public void Step(float dt)
        {
            if (_block == null) Bind();
            if (_block == null || !(_block.HasLegs && legs.Count > 0 || _block.Slides)) return;
            if (!_initialised) ResetFeet();
            if (dt <= 0f) return;

            Vector3 position = transform.position;
            if ((position - _lastPosition).sqrMagnitude > teleportDistance * teleportDistance)
            {
                ResetFeet();
                return;
            }
            Vector3 raw = (position - _lastPosition) / dt;
            raw.y = 0f;
            _lastPosition = position;
            float blend = velocitySmoothing > 0f ? 1f - Mathf.Exp(-dt / velocitySmoothing) : 1f;
            _velocity = Vector3.Lerp(_velocity, raw, blend);
            float speed = Speed;
            float yaw = transform.eulerAngles.y;
            _yawLag = Mathf.Clamp(_yawLag - Mathf.DeltaAngle(_lastYaw, yaw), -180f, 180f);
            _yawLag = Mathf.MoveTowards(_yawLag, 0f, bodyTurnRateDeg * dt);
            _lastYaw = yaw;
            // Apply the body yaw now: hip positions used for reach checks below must already reflect it.
            if (body != null) body.localRotation = BodyRotation();

            if (_block.Slides)
            {
                // Limbless travel: advance the phase-driven undulation with ground speed.
                _params = StepPlanner.SlideParams(_block, speed);
                if (speed > 0.05f) _clock += _params.cadenceHz * dt;
                if (body != null) body.localRotation = BodyRotation();
                UpdateAnimator(speed);
                return;
            }

            _params = StepPlanner.Params(_block, speed);
            // Hysteresis around the walk/run pattern switch keeps the pattern from chattering.
            if (_run && _params.weight < 0.4) _run = false;
            else if (!_run && _params.weight > 0.6) _run = true;
            bool moving = speed > 0.05f && _params.cadenceHz > 0.0;
            if (moving && !_wasMoving)
            {
                // Feet standing at home are mid-stance: start the clock there so the first stride is
                // not spent stretching planted legs. Swings already owed this cycle are consumed.
                _clock = 0.5 * _params.duty - StepPlanner.LegOffset(ToPlanner(legs[0]), _run);
                foreach (var leg in legs)
                {
                    double offset = StepPlanner.LegOffset(ToPlanner(leg), _run);
                    if (StepPlanner.InStance(StepPlanner.LegPhase(_clock, offset), _params.duty))
                        leg.lastSwingCycle = Math.Floor(_clock + offset) - 1;
                }
            }
            _wasMoving = moving;
            if (moving) _clock += _params.cadenceHz * dt;

            float swingTime = (float)StepPlanner.SwingTime(_params.cadenceHz, _params.duty);
            float lead = (float)StepPlanner.LandingLead(speed, _params.cadenceHz, _params.duty);
            Vector3 heading = speed > 1e-4f ? _velocity / speed : transform.forward;
            Quaternion lag = Quaternion.Euler(0f, _yawLag, 0f);

            // Pass 1: advance swings. A swing owns its own monotonic progress, so gait changes (duty,
            // cadence, walk/run pattern) never reclassify a foot in the air as planted.
            foreach (var leg in legs)
            {
                leg.home = Ground(transform.TransformPoint(lag * leg.homeLocal), leg.homeLocal.y);
                if (leg.planted) continue;
                leg.swingT += dt;
                float u = Mathf.Clamp01(leg.swingT / leg.swingDuration);
                float remaining = Mathf.Max(0f, leg.swingDuration - leg.swingT);
                Vector3 land = moving
                    ? Ground(leg.home + _velocity * remaining + heading * lead, leg.homeLocal.y)
                    : leg.home;
                leg.position = Swing(leg.swingStart, land, u, leg.forced ? leg.clearance * 0.6f : leg.clearance);
                if (u >= 1f) Plant(leg, land);
            }

            // Pass 2: lift planted feet, on schedule while moving, or early when strained.
            foreach (var leg in legs)
            {
                if (!leg.planted) continue;
                leg.position = leg.plant;
                if (moving)
                {
                    double offset = StepPlanner.LegOffset(ToPlanner(leg), _run);
                    double phase = StepPlanner.LegPhase(_clock, offset);
                    double cycle = Math.Floor(_clock + offset);
                    if (!StepPlanner.InStance(phase, _params.duty) && cycle != leg.lastSwingCycle)
                    {
                        if (CanLift(leg, false))
                        {
                            // Land when the schedule says stance begins again.
                            float duration = (float)((1.0 - phase) / _params.cadenceHz);
                            Lift(leg, Mathf.Max(0.08f, duration), false, cycle);
                        }
                    }
                    else if (Overrun(leg, leg.home) && CanLift(leg, true))
                    {
                        Lift(leg, Mathf.Clamp(swingTime, 0.12f, 0.2f), true,
                            StepPlanner.InStance(phase, _params.duty) ? cycle - 1 : cycle);
                    }
                }
                else if (Overrun(leg, leg.home)
                         || Strain(leg, leg.home) > Mathf.Max(0.05f, 0.25f * Mathf.Max(leg.stroke, 0.2f * leg.reach)))
                {
                    LiftGroup(leg, 0.15f);
                }
            }

            UpdateWeights(dt);
            UpdateBody(dt, speed);
            ApplyTargets();
            UpdateAnimator(speed);
        }

        const float MaxCoxaYawDeg = 45f;
        const float HingeReachFraction = 0.97f;

        /// <summary>
        /// Place every IK target for the foot contacts in leg.position, never asking a chain for more than it
        /// has: an unreachable planted foot drags (a short slide while its group waits to re-step) instead of
        /// the leg snapping toward an impossible target.
        /// Hinge legs: the coxa yaws toward the foot about the body's up axis; the ankle keeps its neutral
        /// offset and orientation relative to that yawed coxa frame; the knee hint yaws with it.
        /// Chain legs aim the contact directly.
        /// </summary>
        void ApplyTargets()
        {
            Quaternion bodyRotation = body != null ? body.rotation : transform.rotation;
            Vector3 up = bodyRotation * Vector3.up;
            foreach (var leg in legs)
            {
                if (leg.target == null) continue;
                if (!leg.hinge || leg.coxa == null)
                {
                    Vector3 reachable = ClampToReach(leg, leg.position);
                    leg.clamped = reachable != leg.position;
                    if (leg.planted && leg.clamped) leg.plant = reachable;
                    leg.position = reachable;
                    leg.target.position = leg.position;
                    continue;
                }
                Vector3 coxa = leg.coxa.position;
                Vector3 neutral = Vector3.ProjectOnPlane(bodyRotation * leg.homeFromCoxa, up);
                Vector3 now = Vector3.ProjectOnPlane(leg.position - coxa, up);
                float yaw = neutral.sqrMagnitude > 1e-8f && now.sqrMagnitude > 1e-8f
                    ? Mathf.Clamp(Vector3.SignedAngle(neutral, now, up), -MaxCoxaYawDeg, MaxCoxaYawDeg) : 0f;
                if (leg.coxaAim == null) yaw = 0f;
                Quaternion frame = Quaternion.AngleAxis(yaw, up) * bodyRotation;

                Vector3 femur = coxa + frame * leg.femurFromCoxa;
                Vector3 ankle = leg.position + frame * leg.ankleOffset;
                Vector3 reach = ankle - femur;
                float max = HingeReachFraction * leg.hingeReach;
                leg.clamped = reach.sqrMagnitude > max * max;
                if (leg.clamped)
                {
                    ankle = femur + reach.normalized * max;
                    leg.position = ankle - frame * leg.ankleOffset;
                    if (leg.planted) leg.plant = leg.position;
                }
                leg.target.SetPositionAndRotation(ankle, frame * leg.ankleRotation);
                if (leg.coxaAim != null) leg.coxaAim.position = coxa + frame * leg.coxaDirection;
                if (leg.hint != null) leg.hint.position = coxa + frame * leg.hintFromCoxa;
            }
        }

        static LocomotionLeg ToPlanner(Leg leg) => new LocomotionLeg { walk_phase = leg.walkPhase, run_phase = leg.runPhase };

        Vector3 Swing(Vector3 start, Vector3 end, float u, float clearance)
        {
            // Ground-plane interpolation and lift are expressed in world up; ramps come from the endpoints.
            var p = StepPlanner.SwingPoint(new double[] { start.x, start.y, start.z }, new double[] { end.x, end.y, end.z }, u, clearance);
            return new Vector3((float)p[0], (float)p[1], (float)p[2]);
        }

        void Plant(Leg leg, Vector3 at)
        {
            leg.plant = at;
            leg.position = at;
            leg.planted = true;
            leg.forced = false;
        }

        const float MaxReachFraction = 0.95f;

        static Vector3 ClampToReach(Leg leg, Vector3 p)
        {
            if (leg.hip == null) return p;
            Vector3 d = p - leg.hip.position;
            float max = MaxReachFraction * leg.reach;
            return d.sqrMagnitude > max * max ? leg.hip.position + d.normalized * max : p;
        }

        static float Strain(Leg leg, Vector3 home)
        {
            Vector3 d = leg.plant - home;
            d.y = 0f;
            return d.magnitude;
        }

        bool Overrun(Leg leg, Vector3 home)
        {
            // Instant heading changes (agents with huge angular speed) leave planted feet far from home or
            // out of reach; re-step them early rather than stretching the chain.
            if (leg.clamped) return true;
            if (!leg.hinge && leg.hip != null && Vector3.Distance(leg.hip.position, leg.plant) > 0.92f * leg.reach) return true;
            return Strain(leg, home) > Mathf.Max(0.6f * leg.stroke, 0.35f * leg.reach);
        }

        int PlantedSupports(Leg excluding = null, int excludeGroup = -1)
        {
            int n = 0;
            foreach (var other in legs)
                if (other.support && other.planted && other != excluding && other.group != excludeGroup) n++;
            return n;
        }

        /// <summary>Lifting keeps at least min_support planted supports; early steps also respect a swing cap.</summary>
        bool CanLift(Leg leg, bool early)
        {
            if (leg.support && PlantedSupports(leg) < _block.min_support) return false;
            if (!early) return true;
            int swinging = 0;
            foreach (var other in legs) if (!other.planted) swinging++;
            return swinging < Mathf.Max(1, legs.Count / 2);
        }

        static void Lift(Leg leg, float duration, bool early, double cycle)
        {
            leg.swingStart = leg.plant;
            leg.swingT = 0f;
            leg.swingDuration = Mathf.Max(0.08f, duration);
            leg.planted = false;
            leg.forced = early;
            leg.lastSwingCycle = cycle;
        }

        /// <summary>
        /// Idle re-step of a strained leg together with its group (tripods, diagonal pairs), so turning in
        /// place alternates whole groups; falls back to the single leg when the group cannot lift.
        /// </summary>
        void LiftGroup(Leg leg, float duration)
        {
            foreach (var other in legs) if (!other.planted) return;   // one group at a time
            if (PlantedSupports(null, leg.group) >= _block.min_support)
            {
                foreach (var other in legs)
                    if (other.group == leg.group && other.planted) Lift(other, duration, true, other.lastSwingCycle);
            }
            else if (CanLift(leg, true))
                Lift(leg, duration, true, leg.lastSwingCycle);
        }

        Vector3 Ground(Vector3 point, float soleHeight)
        {
            float probe = (float)_block.hip_height_m + 1f;
            if (Physics.Raycast(point + Vector3.up * probe, Vector3.down, out var hit, probe + 2f, groundMask,
                    QueryTriggerInteraction.Ignore))
                return new Vector3(point.x, hit.point.y + soleHeight, point.z);
            return new Vector3(point.x, transform.position.y + soleHeight, point.z);
        }

        void UpdateWeights(float dt)
        {
            var state = _motion != null ? _motion.State : CreatureState.Idle;
            float rate = dt / 0.15f;
            foreach (var leg in legs)
            {
                float goal = state == CreatureState.Dead ? 0f
                    : leg.attack && (state == CreatureState.Telegraph || state == CreatureState.Attacking) ? 0f
                    : 1f;
                leg.weight = Mathf.MoveTowards(leg.weight, goal, state == CreatureState.Dead ? dt / 0.4f : rate);
                if (leg.constraint is IRigConstraint c) c.weight = leg.weight;
            }
            if (rig != null) rig.weight = ikWeight;
        }

        void UpdateBody(float dt, float speed)
        {
            if (body == null) return;
            // Least-squares plane y = a + b x + c z through planted-foot height errors (root-local x, z).
            double n = 0, sx = 0, sz = 0, sy = 0, sxx = 0, szz = 0, sxz = 0, sxy = 0, szy = 0;
            foreach (var leg in legs)
            {
                if (!leg.planted || !leg.support) continue;
                Vector3 local = transform.InverseTransformPoint(leg.plant);
                double err = local.y - leg.homeLocal.y;
                double x = leg.homeLocal.x, z = leg.homeLocal.z;
                n++; sx += x; sz += z; sy += err;
                sxx += x * x; szz += z * z; sxz += x * z; sxy += x * err; szy += z * err;
            }
            float height = 0f, pitch = 0f, roll = 0f;
            if (n > 0)
            {
                height = (float)(sy / n);
                if (n >= 3)
                {
                    // Centered normal equations for the slopes.
                    double mx = sx / n, mz = sz / n, my = sy / n;
                    double cxx = sxx / n - mx * mx, czz = szz / n - mz * mz, cxz = sxz / n - mx * mz;
                    double cxy = sxy / n - mx * my, czy = szy / n - mz * my;
                    double det = cxx * czz - cxz * cxz;
                    if (Math.Abs(det) > 1e-9)
                    {
                        double b = (cxy * czz - czy * cxz) / det;
                        double c = (czy * cxx - cxy * cxz) / det;
                        pitch = Mathf.Clamp(-Mathf.Rad2Deg * Mathf.Atan((float)c), -maxBodyTiltDeg, maxBodyTiltDeg);
                        roll = Mathf.Clamp(Mathf.Rad2Deg * Mathf.Atan((float)b), -maxBodyTiltDeg, maxBodyTiltDeg);
                    }
                }
            }
            float k = 1f - Mathf.Exp(-dt / 0.12f);
            _bodyHeight = Mathf.Lerp(_bodyHeight, height, k);
            _bodyPitch = Mathf.Lerp(_bodyPitch, pitch, k);
            _bodyRoll = Mathf.Lerp(_bodyRoll, roll, k);
            // A small vertical bob at twice the stride frequency while moving.
            float bob = speed > 0.05f
                ? 0.03f * (float)_block.hip_height_m * (float)_params.weight * Mathf.Cos((float)(_clock * 4.0 * Math.PI))
                : 0f;
            body.localPosition = bodyBaseLocalPosition + Vector3.up * (_bodyHeight + bob);
            body.localRotation = BodyRotation();
        }

        Quaternion BodyRotation() =>
            Quaternion.Euler(0f, _yawLag, 0f) * Quaternion.Euler(_bodyPitch, 0f, _bodyRoll) * bodyBaseLocalRotation;

        void UpdateAnimator(float speed)
        {
            if (_animator == null) return;
            if (_hasSpeed) _animator.SetFloat(SpeedParam, speed);
            if (_hasGait) _animator.SetFloat(GaitParam, (float)_params.weight);
            if (_hasPhase) _animator.SetFloat(GaitPhaseParam, (float)StepPlanner.LegPhase(_clock, 0.0));
        }
    }
}
