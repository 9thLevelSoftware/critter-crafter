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
            /// <summary>Centre of the stance stroke (homeLocal unless the gait's stroke is lopsided).</summary>
            public Vector3 stanceLocal;
            public float reach;
            public float stroke;
            public float clearance;
            public double walkPhase;
            public double runPhase;
            public bool support;
            public bool attack;
            /// <summary>A limping leg: the body dips this far (m) toward it while it is planted.</summary>
            public float limp;
            /// <summary>Drag gaits: a leg that drives the torso from behind (an arm pulls it from ahead).</summary>
            public bool push;
            /// <summary>Legs sharing a walk phase offset step together (e.g. a hexapod tripod).</summary>
            public int group;
            public int contactIndex;
            public Vector3 hipFromBody;
            [NonSerialized] public Vector3 hipPoseFromBody;
            [NonSerialized] public ReachProjectionConstraint reachProjection;

            [NonSerialized] public bool planted = true;
            [NonSerialized] public Vector3 plant;
            [NonSerialized] public Vector3 swingStart;
            [NonSerialized] public Vector3 position;
            [NonSerialized] public bool forced;
            [NonSerialized] public float swingT;
            [NonSerialized] public float swingDuration;
            [NonSerialized] public double lastSwingCycle = double.MinValue;
            [NonSerialized] public Vector3 home;
            [NonSerialized] public Vector3 landingOffsetLocal;
            [NonSerialized] public int landingCandidate = -1;
            [NonSerialized] public Vector3 swingLanding;
            /// <summary>Last frame's target was beyond reach and had to be pulled in.</summary>
            [NonSerialized] public bool clamped;
            /// <summary>How far the target sat from the leg's root last frame, as a fraction of the reach limit (1 = clamped).</summary>
            [NonSerialized] public float reachFrac;
            /// <summary>Seconds since the foot landed; a foot that landed at its reach limit must not re-lift at once.</summary>
            [NonSerialized] public float plantedTime;
            [NonSerialized] public float weight = 1f;
            /// <summary>
            /// Review diagnostics: how far the reach clamp dragged a foot that was already gripping last frame
            /// (m). A foot landing out of reach is pulled in on touchdown; that is not a slide.
            /// </summary>
            [NonSerialized] public float plantRewrite;
            [NonSerialized] public bool gripped;
            [NonSerialized] public Vector3 plantBeforeTargets;
            [NonSerialized] public bool heldBeforeTargets;
            /// <summary>Review diagnostics: the leg was strained but could not re-step this frame.</summary>
            [NonSerialized] public bool liftBlocked;
        }

        [Tooltip("Layers raycast to find the ground under each foot.")]
        public LayerMask groundMask = ~0;
        [Tooltip("Velocity smoothing time (s).")]
        public float velocitySmoothing = 0.08f;
        [Tooltip("Maximum body pitch/roll from uneven footing (degrees).")]
        public float maxBodyTiltDeg = 25f;
        [Tooltip("Displacement in one frame treated as a teleport (m).")]
        public float teleportDistance = 2f;
        [Tooltip("Maximum visual body turn rate (deg/s). The root may snap to a new heading (NavMeshAgent with a huge angularSpeed); the body follows at this rate so planted feet are not dragged in one frame.")]
        public float bodyTurnRateDeg = 360f;
        [Tooltip("How much the body's turn is held back so its feet can follow: 0 = always bodyTurnRateDeg (feet may drag after an " +
                 "instant turn), 1 = never faster than the most strained planted foot can take.")]
        [Range(0f, 1f)] public float turnReachGate = 1f;
        [Tooltip("Reach gating: the time (s) a foot has to re-step before a turn may use up its remaining reach.")]
        public float turnGateSeconds = 0.25f;
        [Tooltip("Reach gating: the slowest the body will turn (degrees per second), so a turn always finishes.")]
        public float turnGateMinDeg = 60f;
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
        RigBuilder _rigBuilder;
        bool _hasSpeed, _hasGait, _hasPhase;
        bool _initialised;
        bool _run;
        bool _wasMoving;
        Vector3 _lastPosition;
        Vector3 _velocity;
        /// <summary>Root velocity with much lighter smoothing, for placing landings (the body-speed smoothing lags a turn).</summary>
        Vector3 _landVelocity;
        /// <summary>The yaw catch-up rate in force this frame (degrees per second): bodyTurnRateDeg, slowed by reach gating.</summary>
        float _turnRateNow;
        List<Leg> _order;
        double _clock;
        float _bodyHeight, _bodyPitch, _bodyRoll, _bodyYaw, _bodySurge, _limpDip, _limpRoll, _limpYaw;
        float _lastYaw, _yawLag;
        GaitParams _params;
        readonly Candidate[] _landingCandidates = new Candidate[5];
        Candidate[] _groupLandings;
        static readonly Comparison<Leg> StrainOrder = ByStrain;

        static readonly int SpeedParam = Animator.StringToHash("Speed");
        static readonly int GaitParam = Animator.StringToHash("Gait");
        static readonly int GaitPhaseParam = Animator.StringToHash("GaitPhase");

        public IReadOnlyList<Leg> Legs => legs;
        public GaitParams Current => _params;
        /// <summary>Gait clock in cycles (unbounded; the fractional part is the phase).</summary>
        public double Clock => _clock;
        public Vector3 Velocity => _velocity;
        /// <summary>Visual body yaw behind the root after an instant heading change (degrees).</summary>
        public float YawLag => _yawLag;
        /// <summary>Grounded bodies: the torso's lead over the root along the body heading (m).</summary>
        public float Surge => _bodySurge;
        public float Speed => new Vector3(_velocity.x, 0f, _velocity.z).magnitude;
        public LocomotionData Block => _block;
        /// <summary>How far the body is raised (+) or dropped (-) by the planted feet (m); review diagnostics.</summary>
        public float BodyHeight => _bodyHeight;
        /// <summary>The posed torso (grounded bodies travel in hauls relative to the root).</summary>
        public Transform Body => body;

        internal void Configure(Transform bodyTransform, Rig locomotionRig, List<Leg> builtLegs, LayerMask mask)
        {
            body = bodyTransform;
            rig = locomotionRig;
            legs = builtLegs;
            _groupLandings = new Candidate[legs.Count];
            var groups = new List<double>();
            for (int index = 0; index < legs.Count; index++)
            {
                var leg = legs[index];
                leg.contactIndex = index;
                if (leg.hip != null) leg.hipFromBody = Quaternion.Inverse(body.rotation) * (leg.hip.position - body.position);
                int g = groups.FindIndex(x => Math.Abs(x - leg.walkPhase) < 1e-6);
                if (g < 0) { groups.Add(leg.walkPhase); g = groups.Count - 1; }
                leg.group = g;
            }
            groundMask = mask;
            _order = new List<Leg>(legs);
            bodyBaseLocalPosition = body.localPosition;
            bodyBaseLocalRotation = body.localRotation;
            Bind();
        }

        void Awake() => Bind();

        void Bind()
        {
            _creature = GetComponent<AssembledCreature>();
            _motion = GetComponent<CreatureMotion>();
            _block = _creature != null ? _creature.GaitBlock : null;
            _animator = _creature != null ? _creature.Animator : null;
            _rigBuilder = _animator != null ? _animator.GetComponent<RigBuilder>() : null;
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
                leg.landingOffsetLocal = Vector3.zero;
                leg.landingCandidate = -1;
                leg.swingLanding = leg.plant;
            }
            ApplyTargets();
            _lastPosition = transform.position;
            _lastYaw = transform.eulerAngles.y;
            _yawLag = 0f;
            _velocity = Vector3.zero;
            _landVelocity = Vector3.zero;
            // Standing again: the next move re-seeds the gait clock with every foot mid-stance.
            _wasMoving = false;
            _initialised = true;
        }

        void OnEnable() => _initialised = false;

        internal void SynchronizeRigTargets()
        {
            if (_rigBuilder == null || !_rigBuilder.graph.IsValid()) return;
            foreach (var leg in legs)
            {
                if (!leg.hinge || leg.target == null || leg.reachProjection == null
                    || !leg.reachProjection.TryGetResult(out ReachProjectionResult result) || !result.clamped) continue;
                Quaternion frame = leg.target.rotation * Quaternion.Inverse(leg.ankleRotation);
                leg.position = leg.target.position - frame * leg.ankleOffset;
                leg.clamped = true;
                leg.reachFrac = Mathf.Max(leg.reachFrac, result.reachFraction);
                if (leg.planted)
                {
                    leg.plant = leg.position;
                    if (leg.heldBeforeTargets)
                        leg.plantRewrite = Vector3.Distance(leg.plantBeforeTargets, leg.plant);
                }
            }
        }

        void Update() => Step(Time.deltaTime);

        /// <summary>Advance the planner by <paramref name="dt"/> seconds (public for tests and capture).</summary>
        public void Step(float dt)
        {
            if (_block == null) Bind();
            if (_block == null || !(_block.HasLegs && legs.Count > 0 || _block.Slides)) return;
            if (!_initialised) ResetFeet();
            if (dt <= 0f) return;
            Quaternion inverseBody = Quaternion.Inverse(body != null ? body.rotation : transform.rotation);
            Vector3 bodyOrigin = body != null ? body.position : transform.position;
            foreach (var leg in legs)
                leg.hipPoseFromBody = leg.hip != null ? inverseBody * (leg.hip.position - bodyOrigin) : leg.hipFromBody;

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
            // The first moving frame takes the real velocity: smoothing it up from rest ran the clock and the walk/run
            // choice at a fraction of the real speed for the first strides, and feet dragged.
            bool startingToMove = !_wasMoving && raw.magnitude > 0.05f;
            if (startingToMove) blend = 1f;
            _velocity = Vector3.Lerp(_velocity, raw, blend);
            _landVelocity = Vector3.Lerp(_landVelocity, raw, startingToMove || velocitySmoothing <= 0f ? 1f : 1f - Mathf.Exp(-dt / (0.25f * velocitySmoothing)));
            float speed = Speed;
            float yaw = transform.eulerAngles.y;
            _yawLag = Mathf.Clamp(_yawLag - Mathf.DeltaAngle(_lastYaw, yaw), -180f, 180f);
            _turnRateNow = TurnRate();
            _yawLag = Mathf.MoveTowards(_yawLag, 0f, _turnRateNow * dt);
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
                _clock = 0.5 * _params.duty - PhaseOffset(legs[0], _run);
                foreach (var leg in legs)
                {
                    double offset = PhaseOffset(leg, _run);
                    double phase = StepPlanner.LegPhase(_clock, offset);
                    if (StepPlanner.InStance(phase, _params.duty))
                        leg.lastSwingCycle = Math.Floor(_clock + offset) - 1;
                }

            }
            _wasMoving = moving;
            if (moving) _clock += _params.cadenceHz * dt;
            // Haul pose is clock-driven: reach/lift checks must see this frame's hips, not the last haul pose.
            if (_block.body_on_ground) UpdateBody(dt, speed);

            // A dying creature stops stepping: an idle re-step of a strained foot looked like a tidy-up while it fell.
            bool dead = _motion != null && _motion.State == CreatureState.Dead;
            float swingTime = (float)StepPlanner.SwingTime(_params.cadenceHz, _params.duty);
            Vector3 heading = TravelHeading();
            Quaternion lag = Quaternion.Euler(0f, _yawLag, 0f);

            // Pass 1: advance swings. A swing owns its own monotonic progress, so gait changes (duty,
            // cadence, walk/run pattern) never reclassify a foot in the air as planted.
            foreach (var leg in legs)
            {
                leg.home = Ground(transform.TransformPoint(lag * leg.stanceLocal), leg.homeLocal.y);
                if (leg.planted) continue;
                leg.swingT += dt;
                float u = Mathf.Clamp01(leg.swingT / leg.swingDuration);
                float remaining = Mathf.Max(0f, leg.swingDuration - leg.swingT);
                Vector3 predicted = leg.landingCandidate > 0
                    ? ReachableLanding(leg, remaining, out _, out _)
                    : PredictLanding(leg, remaining, out _, out _);
                Vector3 offset = Vector3.Cross(Vector3.up, heading) * leg.landingOffsetLocal.x
                    + heading * leg.landingOffsetLocal.z;
                if (TryGround(predicted + offset, leg.homeLocal.y, out Vector3 projected))
                    leg.swingLanding = projected;
                Vector3 land = leg.swingLanding;
                // A limping leg scuffs: it lifts less than a sound one.
                float clearance = leg.forced ? leg.clearance * 0.6f : leg.clearance;
                if (leg.limp > 0f) clearance *= LimpClearance;
                leg.position = Swing(leg.swingStart, land, u, clearance);
                if (dead) leg.swingT += dt;            // a swing in the air finishes in about a tenth of a second
                if (u >= 1f) Plant(leg, land);
            }

            if (_block.body_on_ground) UpdatePlantedReach();

            // Pass 2: lift planted feet, on schedule while moving, or early when strained. The most strained leg
            // goes first: in list order the first legs used up the swing cap and the worst one waited, dragging.
            bool strainedBlocked = false;
            _order.Sort(StrainOrder);
            foreach (var leg in _order)
            {
                leg.liftBlocked = false;
                if (!leg.planted) continue;
                leg.plantedTime += dt;
                leg.position = leg.plant;
                if (dead) continue;
                if (moving)
                {
                    double offset = PhaseOffset(leg, _run);
                    double phase = StepPlanner.LegPhase(_clock, offset);
                    double cycle = Math.Floor(_clock + offset);
                    // A limping leg's stance is cut short: it can't carry weight as long as a sound one.
                    double scheduledDuty = leg.limp > 0f ? _params.duty * LimpStanceFraction : _params.duty;
                    if (!StepPlanner.InStance(phase, scheduledDuty) && cycle != leg.lastSwingCycle)
                    {
                        if (CanLift(leg, false))
                        {
                            // Land when the schedule says stance begins again.
                            float duration = (float)((1.0 - phase) / _params.cadenceHz);
                            if (!TryLift(leg, Mathf.Max(0.08f, duration), false, cycle))
                                leg.liftBlocked = true;
                        }
                        else
                            leg.liftBlocked = true;
                    }
                    else if (Overrun(leg, leg.home))
                    {
                        if (CanLift(leg, true))
                        {
                            if (!TryLift(leg, Mathf.Clamp(swingTime, 0.12f, 0.2f), true,
                                StepPlanner.InStance(phase, _params.duty) ? cycle - 1 : cycle))
                            {
                                leg.liftBlocked = true;
                                strainedBlocked = true;
                            }
                        }
                        else
                        {
                            leg.liftBlocked = true;
                            strainedBlocked = true;
                        }
                    }
                }
                else if (Overrun(leg, leg.home)
                         || Strain(leg, leg.home) > Mathf.Max(0.05f, 0.25f * Mathf.Max(leg.stroke, 0.2f * leg.reach)))
                {
                    LiftGroup(leg, 0.15f);
                    leg.liftBlocked = leg.planted;
                }
            }

            if (strainedBlocked) HurryLandings(dt);
            UpdateWeights(dt);
            if (!_block.body_on_ground) UpdateBody(dt, speed);
            ApplyTargets();
            UpdateAnimator(speed);
        }

        const float MaxCoxaYawDeg = 45f;
        internal const float HingeReachFraction = 0.97f;

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
                leg.plantRewrite = 0f;
                bool held = leg.planted && leg.gripped;
                leg.plantBeforeTargets = leg.plant;
                leg.heldBeforeTargets = held;
                if (leg.reachProjection != null) leg.reachProjection.data.planted = leg.planted;
                leg.gripped = leg.planted;
                if (leg.target == null) continue;
                if (!leg.hinge || leg.coxa == null)
                {
                    if (leg.hip != null) leg.reachFrac = Vector3.Distance(leg.position, leg.hip.position) / (MaxReachFraction * leg.reach);
                    Vector3 reachable = leg.planted ? ClampPlantedToReach(leg, leg.position) : ClampToReach(leg, leg.position);
                    leg.clamped = reachable != leg.position;
                    if (leg.planted && leg.clamped)
                    {
                        if (held) leg.plantRewrite = Vector3.Distance(leg.plant, reachable);
                        leg.plant = reachable;
                    }
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
                leg.reachFrac = reach.magnitude / max;
                leg.clamped = reach.sqrMagnitude > max * max;
                if (leg.clamped)
                {
                    // A planted foot is pulled in along the ground; a swinging one may be pulled in any direction.
                    ankle = leg.planted ? ClampHorizontally(ankle, femur, max) : femur + reach.normalized * max;
                    leg.position = ankle - frame * leg.ankleOffset;
                    if (leg.planted)
                    {
                        if (held) leg.plantRewrite = Vector3.Distance(leg.plant, leg.position);
                        leg.plant = leg.position;
                    }
                }
                leg.target.SetPositionAndRotation(ankle, frame * leg.ankleRotation);
                if (leg.coxaAim != null) leg.coxaAim.position = coxa + frame * leg.coxaDirection;
                if (leg.hint != null) leg.hint.position = coxa + frame * leg.hintFromCoxa;
            }
        }

        /// <summary>The leg's phase offset in the current pattern (same rule as StepPlanner.LegOffset, without a per-frame object).</summary>
        static double PhaseOffset(Leg leg, bool run) => run ? leg.runPhase : leg.walkPhase;

        Vector3 Swing(Vector3 start, Vector3 end, float u, float clearance)
        {
            // Ground-plane interpolation and lift are expressed in world up; ramps come from the endpoints.
            StepPlanner.SwingPoint(start.x, start.y, start.z, end.x, end.y, end.z, u, clearance, out double x, out double y, out double z);
            return new Vector3((float)x, (float)y, (float)z);
        }

        void Plant(Leg leg, Vector3 at)
        {
            leg.plant = at;
            leg.position = at;
            leg.planted = true;
            leg.forced = false;
            leg.clamped = false;        // a foot that has just landed is not being dragged; ApplyTargets decides again
            leg.plantedTime = 0f;
            leg.landingOffsetLocal = Vector3.zero;
            leg.landingCandidate = -1;
        }

        const float MaxReachFraction = 0.95f;
        /// <summary>A planted foot this close to its reach limit is re-stepped before the clamp has to drag it.</summary>
        const float OverrunReachFraction = 0.97f;
        /// <summary>A foot has to have stood this long before nearing its limit counts as strain (else landing at the limit loops).</summary>
        const float MinPlantedForOverrun = 0.1f;
        // The limping leg of a walker amalgam: shorter stance, shorter reach at landing, lower lift.
        const float LimpStanceFraction = 0.88f;
        const float LimpLead = 0.7f;
        const float LimpClearance = 0.55f;

        /// <summary>
        /// Pull <paramref name="point"/> within <paramref name="max"/> of <paramref name="centre"/> by shortening it
        /// horizontally and keeping its height, so a planted foot slides along the ground instead of rising. If
        /// the height alone is already out of reach, fall back to pulling it in straight (there is no ground point
        /// that reaches).
        /// </summary>
        internal static Vector3 ClampHorizontally(Vector3 point, Vector3 centre, float max)
        {
            Vector3 d = point - centre;
            if (d.sqrMagnitude <= max * max) return point;
            float room = max * max - d.y * d.y;
            if (room <= 0f) return centre + d.normalized * max;
            var flat = new Vector3(d.x, 0f, d.z);
            return centre + flat.normalized * Mathf.Sqrt(room) + Vector3.up * d.y;
        }

        static Vector3 ClampPlantedToReach(Leg leg, Vector3 p) =>
            leg.hip == null ? p : ClampHorizontally(p, leg.hip.position, MaxReachFraction * leg.reach);

        static Vector3 ClampToReach(Leg leg, Vector3 p)
        {
            if (leg.hip == null) return p;
            Vector3 d = p - leg.hip.position;
            float max = MaxReachFraction * leg.reach;
            return d.sqrMagnitude > max * max ? leg.hip.position + d.normalized * max : p;
        }

        /// <summary>
        /// The body's yaw catch-up rate. Instant agent turns leave the body far from its heading and it swings round at
        /// bodyTurnRateDeg, so a hip at radius r sweeps 0.2 r m per frame while its foot is planted: the feet run out of
        /// reach at once and drag. Each planted foot has some reach left and sits some distance from the pivot; the body
        /// may only turn as fast as the tightest of them can take within turnGateSeconds (never below turnGateMinDeg).
        /// Grounded bodies keep their own turn model.
        /// </summary>
        float TurnRate()
        {
            if (turnReachGate <= 0f || _block.body_on_ground || legs == null) return bodyTurnRateDeg;
            float allowed = bodyTurnRateDeg;
            foreach (var leg in legs)
            {
                if (!leg.planted) continue;
                float margin = Mathf.Max(0f, 1f - leg.reachFrac) * MaxReachFraction * leg.reach;
                float radius = Mathf.Max(0.2f, new Vector2(leg.homeLocal.x, leg.homeLocal.z).magnitude);
                allowed = Mathf.Min(allowed, Mathf.Rad2Deg * margin / (radius * Mathf.Max(0.05f, turnGateSeconds)));
            }
            allowed = Mathf.Clamp(allowed, Mathf.Min(turnGateMinDeg, bodyTurnRateDeg), bodyTurnRateDeg);
            return Mathf.Lerp(bodyTurnRateDeg, allowed, turnReachGate);
        }

        static int ByStrain(Leg a, Leg b)
        {
            int strain = b.reachFrac.CompareTo(a.reachFrac);
            return strain != 0 ? strain : a.contactIndex.CompareTo(b.contactIndex);
        }

        /// <summary>
        /// A strained leg is waiting on a support that is still in the air: bring the airborne legs down faster
        /// (their swing runs at double speed, so the position stays continuous) so the waiting one is not
        /// dragged for a whole swing. Bodies lying on the ground don't need it: their legs may lift at once.
        /// </summary>
        void HurryLandings(float dt)
        {
            if (_block.body_on_ground) return;
            foreach (var leg in legs)
                if (!leg.planted) leg.swingT += dt;
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
            // Grounded bodies (draggers) keep their own model: they re-step when the clamp has already dragged a hand.
            if (leg.clamped) return true;
            if (!_block.body_on_ground && leg.reachFrac > OverrunReachFraction && leg.plantedTime > MinPlantedForOverrun) return true;
            if (!leg.hinge && leg.hip != null && Vector3.Distance(leg.hip.position, leg.plant) > 0.92f * leg.reach) return true;
            return Strain(leg, home) > Mathf.Max(0.6f * leg.stroke, 0.35f * leg.reach);
        }

        int PlantedSupports(Leg excluding = null, int excludeGroup = -1)
        {
            int n = 0;
            foreach (var other in legs)
                if (other.support && other.planted && other.weight >= 0.5f && other != excluding && other.group != excludeGroup) n++;
            return n;
        }

        /// <summary>
        /// Lifting keeps at least min_support planted supports; early steps also respect a swing cap. A body
        /// that lies on the ground carries its own weight, so a strained hand may re-grip at once even while
        /// the other hand is in the air; capping it drags the clamped hand along the ground instead.
        /// </summary>
        bool CanLift(Leg leg, bool early)
        {
            int swinging = 0;
            foreach (var other in legs) if (!other.planted) swinging++;
            return StepPlanner.CanLift(leg.support, PlantedSupports(leg), _block.min_support,
                early, _block.body_on_ground, swinging, legs.Count);
        }

        Vector3 TravelHeading()
        {
            Vector3 velocity = _block.body_on_ground ? _velocity : _landVelocity;
            return velocity.sqrMagnitude > 1e-6f ? velocity.normalized : transform.forward;
        }

        Vector3 PredictLanding(Leg leg, float remaining, out Vector3 travel, out Quaternion yawDelta)
        {
            bool moving = Speed > 0.05f && _params.cadenceHz > 0.0;
            float lead = moving ? (float)StepPlanner.LandingLead(Speed, _params.cadenceHz, _params.duty) : 0f;
            float yawThen = _yawLag;
            float touchdownYaw = _yawLag;
            if (!_block.body_on_ground)
            {
                float landIn = remaining + (moving ? 0.5f * (float)_params.duty / (float)_params.cadenceHz : 0f);
                yawThen = Mathf.MoveTowards(_yawLag, 0f, _turnRateNow * landIn);
                touchdownYaw = Mathf.MoveTowards(_yawLag, 0f, _turnRateNow * remaining);
                if (leg.limp > 0f) lead *= LimpLead;
            }
            yawDelta = Quaternion.AngleAxis(touchdownYaw - _yawLag, Vector3.up);
            travel = moving ? (_block.body_on_ground ? _velocity : _landVelocity) * remaining : Vector3.zero;
            // The stance home is aimed for mid-stance; the body frame separately forecasts touchdown yaw.
            Vector3 home = transform.TransformPoint(Quaternion.Euler(0f, yawThen, 0f) * leg.stanceLocal);
            return home + travel + TravelHeading() * lead;
        }


        void PredictBodyFrame(float duration, Vector3 travel, Quaternion yawDelta,
            out Vector3 origin, out Quaternion rotation)
        {
            if (body == null)
            {
                origin = transform.position + travel;
                rotation = yawDelta * transform.rotation;
                return;
            }
            if (!_block.body_on_ground)
            {
                float walkSpeed = Speed;
                double clock = _clock + duration * _params.cadenceHz;
                WalkerBodyGoal(walkSpeed, clock, duration, out float walkHeight, out float walkPitch, out float walkRoll,
                    out float limpDip, out float limpRoll, out float limpYaw);
                float terrainBlend = 1f - Mathf.Exp(-duration / 0.12f);
                float limpBlend = 1f - Mathf.Exp(-duration / 0.07f);
                Vector3 walkLocal = bodyBaseLocalPosition + Vector3.up *
                    (Mathf.Lerp(_bodyHeight, walkHeight, terrainBlend) + WalkerBob(walkSpeed, clock) - Mathf.Lerp(_limpDip, limpDip, limpBlend));
                Quaternion walkTilt = Quaternion.Euler(Mathf.Lerp(_bodyPitch, walkPitch, terrainBlend),
                    _bodyYaw + Mathf.Lerp(_limpYaw, limpYaw, limpBlend),
                    Mathf.Lerp(_bodyRoll, walkRoll, terrainBlend) + Mathf.Lerp(_limpRoll, limpRoll, limpBlend));
                origin = (body.parent != null ? body.parent.TransformPoint(walkLocal) : walkLocal) + travel;
                Quaternion localRotation = yawDelta * Quaternion.Euler(0f, _yawLag, 0f) * walkTilt * bodyBaseLocalRotation;
                rotation = (body.parent != null ? body.parent.rotation : Quaternion.identity) * localRotation;
                return;
            }
            float speed = Speed;
            bool moving = speed > 0.05f && _params.cadenceHz > 0.0;
            float pull = -1f, surge = 0f;
            int side = 0;
            bool push = false;
            if (moving) surge = HaulSurge(speed, _clock + duration * _params.cadenceHz, out pull, out side, out push);
            float strength = Mathf.Clamp01(speed / Mathf.Max(0.1f, (float)_block.v_walk_mps));
            float arc = pull >= 0f ? Mathf.Sin(Mathf.PI * pull) : 0f;
            float tiltBlend = 1f - Mathf.Exp(-duration / 0.06f);
            float pitch = Mathf.Lerp(_bodyPitch, -dragHeaveDeg * (push ? dragPushHeave : 1f) * strength * arc, tiltBlend);
            float roll = Mathf.Lerp(_bodyRoll, dragRollDeg * side * strength * arc, tiltBlend);
            float yaw = Mathf.Lerp(_bodyYaw, dragYawDeg * side * strength * arc, tiltBlend);
            float surgeBlend = 1f - Mathf.Exp(-duration / (moving ? 0.03f : Mathf.Max(0.01f, dragStopSettle)));
            surge = Mathf.Lerp(_bodySurge, surge, surgeBlend);
            Quaternion lag = yawDelta * Quaternion.Euler(0f, _yawLag, 0f);
            Quaternion tilt = Quaternion.Euler(pitch, yaw, roll);
            Vector3 pivot = _block.body_pivot_m != null && _block.body_pivot_m.Length == 3
                ? CritterFrame.Position(_block.body_pivot_m) : Vector3.zero;
            Vector3 local = bodyBaseLocalPosition + lag * (Vector3.forward * surge + pivot - tilt * pivot);
            origin = (body.parent != null ? body.parent.TransformPoint(local) : local) + travel;
            rotation = (body.parent != null ? body.parent.rotation : Quaternion.identity) * lag * tilt * bodyBaseLocalRotation;
        }


        void LandingGeometry(Leg leg, Vector3 point, Vector3 bodyOrigin, Quaternion bodyRotation,
            out Vector3 distance, out float reach, out float yaw)
        {
            yaw = 0f;
            if (!leg.hinge || leg.coxa == null)
            {
                Vector3 hip = leg.hip != null ? bodyOrigin + bodyRotation * leg.hipPoseFromBody : point;
                distance = point - hip;
                reach = leg.hip != null ? leg.reach : 0f;
                return;
            }
            Vector3 up = bodyRotation * Vector3.up;
            Vector3 coxa = bodyOrigin + bodyRotation * leg.hipPoseFromBody;
            Vector3 neutral = Vector3.ProjectOnPlane(bodyRotation * leg.homeFromCoxa, up);
            Vector3 now = Vector3.ProjectOnPlane(point - coxa, up);
            if (leg.coxaAim != null && neutral.sqrMagnitude > 1e-8f && now.sqrMagnitude > 1e-8f)
                yaw = Vector3.SignedAngle(neutral, now, up);
            // The foot's azimuth is not the coxa command: the lower hinge still reaches from the bounded frame.
            yaw = Mathf.Clamp(yaw, -MaxCoxaYawDeg, MaxCoxaYawDeg);
            Quaternion frame = Quaternion.AngleAxis(yaw, up) * bodyRotation;
            Vector3 femur = coxa + frame * leg.femurFromCoxa;
            distance = point + frame * leg.ankleOffset - femur;
            reach = leg.hingeReach;
        }

        void UpdatePlantedReach()
        {
            Quaternion rotation = body != null ? body.rotation : transform.rotation;
            foreach (var leg in legs)
            {
                if (!leg.planted || leg.hip == null) continue;
                Vector3 origin = leg.hip.position - rotation * leg.hipPoseFromBody;
                LandingGeometry(leg, leg.plant, origin, rotation, out Vector3 distance, out float reach, out _);
                float limit = (leg.hinge ? HingeReachFraction : MaxReachFraction) * reach;
                if (limit <= 0f) continue;
                leg.reachFrac = distance.magnitude / limit;
                leg.clamped = distance.sqrMagnitude > limit * limit;
            }
        }

        Vector3 ReachableLanding(Leg leg, float duration, out Vector3 bodyOrigin, out Quaternion bodyRotation)
        {
            Vector3 point = PredictLanding(leg, duration, out Vector3 travel, out Quaternion yawDelta);
            PredictBodyFrame(duration, travel, yawDelta, out bodyOrigin, out bodyRotation);
            point.y = leg.home.y;
            // Preserve the existing solver's horizontal destination projection before proposing offsets.
            // The chooser compares double reach fractions, but the projected destination and coxa
            // frame are evaluated in world-space floats; leave a coordinate-scaled representable margin.
            for (int pass = 0; pass < 3; pass++)
            {
                LandingGeometry(leg, point, bodyOrigin, bodyRotation, out Vector3 distance, out float reach, out _);
                float limit = (leg.hinge ? HingeReachFraction : MaxReachFraction) * reach;
                if (limit <= 0f || distance.y * distance.y >= limit * limit) break;
                Vector3 centre = point - distance;
                float worldScale = Mathf.Max(Mathf.Max(Mathf.Abs(point.x), Mathf.Abs(point.z)),
                    Mathf.Max(Mathf.Abs(centre.x), Mathf.Abs(centre.z)));
                float margin = Mathf.Max(1e-6f, Mathf.Max(limit * 1e-5f, worldScale * 2.4e-7f));
                point = ClampHorizontally(point, centre, Mathf.Max(0f, limit - margin));
            }
            return point;
        }

        bool TryChooseLanding(Leg leg, float duration, out Candidate landing)
        {
            Vector3 predicted = ReachableLanding(leg, duration, out Vector3 bodyOrigin, out Quaternion bodyRotation);
            Vector3 heading = TravelHeading();
            Vector3 lateralDirection = Vector3.Cross(Vector3.up, heading);
            for (int index = 0; index < _landingCandidates.Length; index++)
            {
                StepPlanner.CandidateOffset(index, _params.strideM, out double lateral, out double forward);
                Vector3 point = predicted + lateralDirection * (float)lateral + heading * (float)forward;
                var candidate = new Candidate { index = index, contactIndex = leg.contactIndex, hinge = leg.hinge };
                LandingGeometry(leg, point, bodyOrigin, bodyRotation, out Vector3 distance, out float reach, out float yaw);
                float limit = (leg.hinge ? HingeReachFraction : MaxReachFraction) * reach;
                // Height is unknown until the probe, but excessive XZ reach can never be repaired by it.
                if ((_params.strideM > 0.0 || index == 0) && limit > 0f
                    && distance.x * distance.x + distance.z * distance.z <= limit * limit
                    && Mathf.Abs(yaw) <= MaxCoxaYawDeg
                    && TryGround(point, leg.homeLocal.y, out Vector3 projected))
                {
                    LandingGeometry(leg, projected, bodyOrigin, bodyRotation, out distance, out reach, out yaw);
                    candidate.groundValid = true;
                    candidate.x = projected.x; candidate.y = projected.y; candidate.z = projected.z;
                    candidate.reachFraction = StepPlanner.LandingReachFraction(distance.x, distance.y, distance.z, reach);
                    candidate.coxaYawDeg = yaw;
                }
                _landingCandidates[index] = candidate;
            }
            int selected = StepPlanner.ChooseLanding(_landingCandidates, _landingCandidates.Length, _params.strideM, true);
            landing = selected >= 0 ? _landingCandidates[selected] : default;
            return selected >= 0;
        }

        bool TryLift(Leg leg, float duration, bool early, double cycle)
        {
            if (!TryChooseLanding(leg, duration, out Candidate landing)) return false;
            Lift(leg, duration, early, cycle, landing, _params.strideM);
            return true;
        }

        static void Lift(Leg leg, float duration, bool early, double cycle, Candidate landing, double stride)
        {
            StepPlanner.CandidateOffset(landing.index, stride, out double lateral, out double forward);
            leg.landingOffsetLocal = new Vector3((float)lateral, 0f, (float)forward);
            leg.landingCandidate = landing.index;
            leg.swingLanding = new Vector3((float)landing.x, (float)landing.y, (float)landing.z);
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
            // One group at a time, unless the torso lies on the ground and carries its own weight.
            if (!_block.body_on_ground)
                foreach (var other in legs) if (!other.planted) return;
            if (PlantedSupports(null, leg.group) >= _block.min_support)
            {
                bool complete = true;
                for (int index = 0; index < legs.Count; index++)
                {
                    var other = legs[index];
                    if (other.group != leg.group || !other.planted) continue;
                    if (!TryChooseLanding(other, duration, out _groupLandings[index])) complete = false;
                }
                if (complete)
                {
                    for (int index = 0; index < legs.Count; index++)
                    {
                        var other = legs[index];
                        if (other.group == leg.group && other.planted)
                            Lift(other, duration, true, other.lastSwingCycle, _groupLandings[index], _params.strideM);
                    }
                    return;
                }
            }
            if (CanLift(leg, true))
                TryLift(leg, duration, true, leg.lastSwingCycle);
        }

        Vector3 Ground(Vector3 point, float soleHeight)
        {
            return TryGround(point, soleHeight, out Vector3 projected)
                ? projected : new Vector3(point.x, transform.position.y + soleHeight, point.z);
        }

        bool TryGround(Vector3 point, float soleHeight, out Vector3 projected)
        {
            float probe = (float)_block.hip_height_m + 1f;
            if (Physics.Raycast(point + Vector3.up * probe, Vector3.down, out var hit, probe + 2f, groundMask,
                    QueryTriggerInteraction.Ignore))
            {
                projected = new Vector3(point.x, hit.point.y + soleHeight, point.z);
                return true;
            }
            projected = point;
            return false;
        }

        void UpdateWeights(float dt)
        {
            var state = _motion != null ? _motion.State : CreatureState.Idle;
            float rate = dt / 0.15f;
            foreach (var leg in legs)
            {
                // A walking body collapses over its planted feet (the baked death clip was made with them pinned), so
                // its IK stays on. A grounded body curls instead, and its legs fade out.
                bool fade = state == CreatureState.Dead && _block.body_on_ground;
                float goal = fade ? 0f
                    : state == CreatureState.Dead ? 1f
                    : leg.attack && (state == CreatureState.Telegraph || state == CreatureState.Attacking) ? 0f
                    : 1f;
                leg.weight = Mathf.MoveTowards(leg.weight, goal, fade ? dt / 0.4f : rate);
                if (leg.constraint is IRigConstraint c) c.weight = leg.weight;
            }
            if (rig != null) rig.weight = ikWeight;
        }

        [Tooltip("Shoulder roll toward the pulling arm for grounded (dragging) bodies (degrees).")]
        public float dragRollDeg = 8f;
        [Tooltip("Crawl heave: chest lift at mid-pull (degrees).")]
        public float dragHeaveDeg = 6f;
        [Tooltip("Crawl: chest heave of a pushing leg, as a fraction of a pulling arm's.")]
        [Range(0f, 1f)] public float dragPushHeave = 0.5f;
        [Tooltip("Crawl: shoulders turn toward the pulling arm (degrees).")]
        public float dragYawDeg = 6f;
        [Tooltip("Crawl: fraction of each haul the torso rests while the new hand grips (at least the hand-over overlap).")]
        [Range(0f, 0.45f)] public float dragGrip = 0.2f;
        [Tooltip("Walkers with a limping leg: how far (degrees) the body rolls toward it while it carries weight.")]
        [Range(0f, 15f)] public float limpRollDeg = 5f;
        [Tooltip("Walkers with a limping leg: how far (degrees) the body turns toward it while it carries weight.")]
        [Range(0f, 10f)] public float limpYawDeg = 3f;
        [Tooltip("Crawl: fraction at the end of each haul the torso rests after the pull.")]
        [Range(0f, 0.45f)] public float dragSettle = 0.15f;
        [Tooltip("Crawl: time constant (s) for the torso to settle onto the root once travel stops.")]
        public float dragStopSettle = 0.3f;

        /// <summary>
        /// Haul-driven torso travel for grounded bodies. Each haul (one per arm, 1/groups of a cycle) moves
        /// the torso the same distance the root travels, but only while the new hand pulls: the torso rests
        /// as the hand grips, lunges through the pull and stops. Returns the torso's lead over the root along
        /// the heading (m), and the pull progress (0..1, or -1 outside the pull) and its side.
        /// </summary>
        float HaulSurge(float speed, double clock, out float pull, out int side, out bool push)
        {
            pull = -1f;
            side = 0;
            push = false;
            int groups = 0;
            foreach (var leg in legs) groups = Math.Max(groups, leg.group + 1);
            if (groups < 1 || _params.cadenceHz <= 0.0) return 0f;
            float span = 1f / groups;
            float w = -1f;
            foreach (var leg in legs)
            {
                // The arm that planted most recently (phase within its first haul span) is the one pulling.
                double phase = StepPlanner.LegPhase(clock, PhaseOffset(leg, _run));
                if (phase >= span) continue;
                w = (float)(phase / span);
                side = leg.homeLocal.x < 0f ? -1 : 1;
                push = leg.push;
                break;
            }
            if (w < 0f) return 0f;
            // The trailing hand stays planted for the hand-over overlap; the torso must not move before it lifts.
            float grip = Mathf.Max(dragGrip, (float)(groups * _params.duty - 1.0));
            float settle = Mathf.Min(dragSettle, 0.9f - grip);
            float u = Mathf.Clamp01((w - grip) / Mathf.Max(0.05f, 1f - grip - settle));
            if (w >= grip && w <= 1f - settle) pull = u;
            float eased = (float)StepPlanner.Smoothstep(u);
            float perHaul = speed * span / (float)_params.cadenceHz;
            return perHaul * (eased - w);
        }

        void UpdateBody(float dt, float speed)
        {
            if (body == null) return;
            if (_block.body_on_ground)
            {
                // Crawling haul: the torso lies on the ground and only the planted hands move it. The root
                // (agent) is the travel intent and moves smoothly; the torso rests while a hand grips, lunges
                // toward it through the pull, and stops. The chest lifts mid-pull and the shoulders roll and
                // turn toward the hauling arm.
                bool moving = speed > 0.05f;
                float pull = -1f, surge = 0f;
                int side = 0;
                bool push = false;
                if (moving) surge = HaulSurge(speed, _clock, out pull, out side, out push);
                float strength = Mathf.Clamp01(speed / Mathf.Max(0.1f, (float)_block.v_walk_mps));
                float arc = pull >= 0f ? Mathf.Sin(Mathf.PI * pull) : 0f;
                float kg = 1f - Mathf.Exp(-dt / 0.06f);
                // Travel follows the haul closely; once stopped, the torso settles onto the root slowly.
                float ks = moving ? 1f - Mathf.Exp(-dt / 0.03f) : 1f - Mathf.Exp(-dt / Mathf.Max(0.01f, dragStopSettle));
                _bodyHeight = Mathf.Lerp(_bodyHeight, 0f, kg);
                _bodySurge = Mathf.Lerp(_bodySurge, surge, ks);
                // A leg shoves the hips forward instead of hauling the chest up to a hand, so it heaves less.
                float heave = push ? dragPushHeave : 1f;
                _bodyPitch = Mathf.Lerp(_bodyPitch, -dragHeaveDeg * heave * strength * arc, kg);
                _bodyRoll = Mathf.Lerp(_bodyRoll, dragRollDeg * side * strength * arc, kg);
                _bodyYaw = Mathf.Lerp(_bodyYaw, dragYawDeg * side * strength * arc, kg);
                // Pitch and roll about the rear of the torso so the hips and trailing body stay grounded.
                Vector3 pivot = _block.body_pivot_m != null && _block.body_pivot_m.Length == 3
                    ? CritterFrame.Position(_block.body_pivot_m) : Vector3.zero;
                Quaternion tilt = Quaternion.Euler(_bodyPitch, _bodyYaw, _bodyRoll);
                Quaternion yawLag = Quaternion.Euler(0f, _yawLag, 0f);
                Vector3 about = yawLag * pivot;
                // Surge runs along the lagged body heading, so an instant root turn does not swing it sideways.
                body.localPosition = bodyBaseLocalPosition + yawLag * (Vector3.forward * _bodySurge) + about - yawLag * (tilt * pivot);
                body.localRotation = BodyRotation();
                return;
            }
            WalkerBodyGoal(speed, _clock, 0f, out float height, out float pitch, out float roll,
                out float limpDip, out float limpRoll, out float limpYaw);
            float kl = 1f - Mathf.Exp(-dt / 0.07f);
            _limpDip = Mathf.Lerp(_limpDip, limpDip, kl);
            _limpRoll = Mathf.Lerp(_limpRoll, limpRoll, kl);
            _limpYaw = Mathf.Lerp(_limpYaw, limpYaw, kl);
            float k = 1f - Mathf.Exp(-dt / 0.12f);
            _bodyHeight = Mathf.Lerp(_bodyHeight, height, k);
            _bodyPitch = Mathf.Lerp(_bodyPitch, pitch, k);
            _bodyRoll = Mathf.Lerp(_bodyRoll, roll, k);
            body.localPosition = bodyBaseLocalPosition + Vector3.up * (_bodyHeight + WalkerBob(speed, _clock) - _limpDip);
            body.localRotation = BodyRotation();
        }

        void WalkerBodyGoal(float speed, double clock, float forecast,
            out float height, out float pitch, out float roll, out float limpDip, out float limpRoll, out float limpYaw)
        {
            // Least-squares plane y = a + b x + c z through the ground height errors under every supporting leg's
            // home (root-local x, z). Homes exist for all legs every frame (pass 1 already raycast them), so the
            // plane no longer collapses whenever fewer than three feet happen to be planted (every trot frame).
            // The height still follows the *planted* feet (hips must stay a reachable distance above them: following the
            // ground under the homes lifted the body ahead of feet that were still low on a ramp and dragged them).
            double n = 0, sx = 0, sz = 0, sy = 0, sxx = 0, szz = 0, sxz = 0, sxy = 0, szy = 0;
            double plantedCount = 0, plantedSum = 0;
            foreach (var leg in legs)
            {
                if (!leg.support) continue;
                Vector3 local = transform.InverseTransformPoint(leg.home);
                double err = local.y - leg.homeLocal.y;
                double x = leg.homeLocal.x, z = leg.homeLocal.z;
                n++; sx += x; sz += z; sy += err;
                sxx += x * x; szz += z * z; sxz += x * z; sxy += x * err; szy += z * err;
                if (ForecastPlanted(leg, clock, forecast))
                {
                    plantedCount++;
                    plantedSum += transform.InverseTransformPoint(leg.plant).y - leg.homeLocal.y;
                }
            }
            height = 0f; pitch = 0f; roll = 0f;
            if (n > 0)
            {
                // Bounded: a foot that couldn't reach its plant must never drag the body up with it.
                float hip = (float)_block.hip_height_m;
                double meanHeight = plantedCount > 0 ? plantedSum / plantedCount : sy / n;
                height = Mathf.Clamp((float)meanHeight, -MaxBodyDropFraction * hip, MaxBodyRiseFraction * hip);
                if (n >= 2)
                {
                    // Centered normal equations for the slopes; legs spread along one axis only (a biped's two
                    // side-by-side feet) give that axis' slope alone.
                    double mx = sx / n, mz = sz / n, my = sy / n;
                    double cxx = sxx / n - mx * mx, czz = szz / n - mz * mz, cxz = sxz / n - mx * mz;
                    double cxy = sxy / n - mx * my, czy = szy / n - mz * my;
                    double det = cxx * czz - cxz * cxz;
                    double b = 0, c = 0;
                    if (n >= 3 && Math.Abs(det) > 1e-9)
                    {
                        b = (cxy * czz - czy * cxz) / det;
                        c = (czy * cxx - cxy * cxz) / det;
                    }
                    else
                    {
                        if (cxx > 1e-4) b = cxy / cxx;
                        if (czz > 1e-4) c = czy / czz;
                    }
                    pitch = Mathf.Clamp(-Mathf.Rad2Deg * Mathf.Atan((float)c), -maxBodyTiltDeg, maxBodyTiltDeg);
                    roll = Mathf.Clamp(Mathf.Rad2Deg * Mathf.Atan((float)b), -maxBodyTiltDeg, maxBodyTiltDeg);
                }
            }
            // A limping leg: the body drops and rolls toward it while it carries weight, and lifts as it swings,
            // so the walk lurches instead of gliding level over its feet.
            limpDip = 0f; limpRoll = 0f; limpYaw = 0f;
            if (_block.body_limp_m > 0.0 && speed > 0.05f)
            {
                float strength = Mathf.Clamp01(speed / Mathf.Max(0.1f, (float)_block.v_walk_mps));
                foreach (var leg in legs)
                {
                    if (leg.limp <= 0f || !ForecastPlanted(leg, clock, forecast)) continue;
                    limpDip = leg.limp * strength;
                    float side = leg.homeLocal.x >= 0f ? 1f : -1f;        // +1: the limping leg is on the right
                    float share = strength * leg.limp / (float)_block.body_limp_m;
                    limpRoll = -side * limpRollDeg * share;                // a positive roll raises +X, so the right side drops with a negative one
                    limpYaw = side * limpYawDeg * share;                   // and the body turns a little toward the weak side
                }
            }
        }
        bool ForecastPlanted(Leg leg, double clock, float forecast)
        {
            if (forecast <= 0f) return leg.planted;
            if (!leg.planted && forecast < leg.swingDuration - leg.swingT) return false;
            double scheduledDuty = leg.limp > 0f ? _params.duty * LimpStanceFraction : _params.duty;
            return StepPlanner.InStance(StepPlanner.LegPhase(clock, PhaseOffset(leg, _run)), scheduledDuty);
        }


        float WalkerBob(float speed, double clock) => speed > 0.05f
            ? -0.03f * (float)_block.hip_height_m * (float)_params.weight
              * (0.5f + 0.5f * Mathf.Cos((float)(clock * 4.0 * Math.PI)))
            : 0f;

        const float MaxBodyDropFraction = 0.35f;
        const float MaxBodyRiseFraction = 0.15f;

        Quaternion BodyRotation() =>
            Quaternion.Euler(0f, _yawLag, 0f) * Quaternion.Euler(_bodyPitch, _bodyYaw + _limpYaw, _bodyRoll + _limpRoll) * bodyBaseLocalRotation;

        void UpdateAnimator(float speed)
        {
            if (_animator == null) return;
            if (_hasSpeed) _animator.SetFloat(SpeedParam, speed);
            if (_hasGait) _animator.SetFloat(GaitParam, (float)_params.weight);
            if (_hasPhase) _animator.SetFloat(GaitPhaseParam, (float)StepPlanner.LegPhase(_clock, 0.0));
        }
    }
}
