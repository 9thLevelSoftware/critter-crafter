using UnityEngine;

namespace CritterCrafter
{
    public enum CreatureState { Idle, Moving, Telegraph, Attacking, Stunned, Dead }

    /// <summary>
    /// Game-facing driver for an assembled creature's Animator. Movement is owned by the game
    /// (NavMeshAgent etc.); clips never apply root motion. Parameter names match AnimatorBuilder.
    /// </summary>
    [RequireComponent(typeof(AssembledCreature))]
    public class CreatureMotion : MonoBehaviour
    {
        public const float MinPlaybackRate = 0.5f;
        public const float MaxPlaybackRate = 1.5f;
        public const float MovementEpsilon = 0.0001f;
        public static readonly int SpeedParam = Animator.StringToHash("Speed");
        public static readonly int PlaybackRateParam = Animator.StringToHash("PlaybackRate");
        public static readonly int StunnedParam = Animator.StringToHash("Stunned");
        public static readonly int TelegraphTrigger = Animator.StringToHash("Telegraph");
        public static readonly int AttackTrigger = Animator.StringToHash("Attack");
        public static readonly int HitTrigger = Animator.StringToHash("Hit");
        public static readonly int DieTrigger = Animator.StringToHash("Die");

        [Tooltip("Speed (m/s) smoothing time")] public float speedDamp = 0.15f;
        [Tooltip("Rotate the creature to face its velocity (for agents with updateRotation = false)")]
        public bool faceVelocity;
        public float turnSpeedDeg = 360f;

        Animator _animator;
        AssembledCreature _creature;
        Vector3 _velocity;
        CreatureState _state = CreatureState.Idle;

        public CreatureState State => _state;

        bool RuntimeLegs => _creature != null && _creature.Skeleton?.locomotion != null && _creature.Skeleton.locomotion.HasLegs;

        void Awake()
        {
            _creature = GetComponent<AssembledCreature>();
            _animator = _creature.Animator;
            if (_animator != null) _animator.applyRootMotion = false;
        }

        public void SetVelocity(Vector3 worldVelocity)
        {
            _velocity = worldVelocity;
            if (_state == CreatureState.Idle || _state == CreatureState.Moving)
                _state = worldVelocity.sqrMagnitude > MovementEpsilon * MovementEpsilon
                    ? CreatureState.Moving : CreatureState.Idle;
        }

        public void SetState(CreatureState state)
        {
            if (_state == CreatureState.Dead || _animator == null) { _state = state == CreatureState.Dead ? state : _state; return; }
            _animator.SetBool(StunnedParam, state == CreatureState.Stunned);
            switch (state)
            {
                case CreatureState.Telegraph: _animator.SetTrigger(TelegraphTrigger); break;
                case CreatureState.Attacking: _animator.SetTrigger(AttackTrigger); break;
                case CreatureState.Dead: _animator.SetTrigger(DieTrigger); break;
            }
            _state = state;
        }

        public void PlayAttack() => SetState(CreatureState.Attacking);

        public void PlayHit()
        {
            if (_state != CreatureState.Dead && _animator != null) _animator.SetTrigger(HitTrigger);
        }

        public static float PlaybackRateForSpeed(float speed, float walkSpeed, float runSpeed)
            => PlaybackRateForSpeed(speed, walkSpeed, runSpeed, MinPlaybackRate, MaxPlaybackRate);

        public static float PlaybackRateForSpeed(float speed, float walkSpeed, float runSpeed,
            float minPlaybackRate, float maxPlaybackRate)
            => PlaybackRateForSpeed(speed, walkSpeed, runSpeed, 1f, 1f, 1f,
                minPlaybackRate, maxPlaybackRate);

        public static float PlaybackRateForSpeed(float speed, float walkSpeed, float runSpeed,
            float idleDuration, float walkDuration, float runDuration,
            float minPlaybackRate, float maxPlaybackRate)
        {
            if (speed <= MovementEpsilon) return 1f;
            if (walkSpeed <= MovementEpsilon || runSpeed <= walkSpeed
                || idleDuration <= 0f || walkDuration <= 0f || runDuration <= 0f)
                return Mathf.Clamp(speed / Mathf.Max(MovementEpsilon, runSpeed), minPlaybackRate, maxPlaybackRate);
            if (speed <= walkSpeed)
            {
                float weight = speed / walkSpeed;
                float blendedDuration = Mathf.Lerp(idleDuration, walkDuration, weight);
                // The threshold already supplies the speed fraction. Correct only Unity's
                // duration-weighted normalized clock; speed/walk here would slow it twice.
                return Mathf.Clamp(blendedDuration / walkDuration, minPlaybackRate, maxPlaybackRate);
            }
            if (speed <= runSpeed)
            {
                float weight = (speed - walkSpeed) / (runSpeed - walkSpeed);
                float blendedDuration = Mathf.Lerp(walkDuration, runDuration, weight);
                float blendedDistance = Mathf.Lerp(walkSpeed * walkDuration,
                    runSpeed * runDuration, weight);
                float baseSpeed = blendedDistance / blendedDuration;
                return Mathf.Clamp(speed / Mathf.Max(MovementEpsilon, baseSpeed),
                    minPlaybackRate, maxPlaybackRate);
            }
            return Mathf.Clamp(speed / Mathf.Max(MovementEpsilon, runSpeed), minPlaybackRate, maxPlaybackRate);
        }

        void Update()
        {
            if (_animator == null) return;
            Vector3 planar = new Vector3(_velocity.x, 0f, _velocity.z);
            float speed = _state == CreatureState.Dead ? 0f : planar.magnitude;
            // Runtime-leg skeletons: CreatureGait (CritterCrafter.Locomotion) measures the real velocity
            // and owns Speed/Gait/GaitPhase; walk/run are phase-locked overlays with no playback rate.
            if (!RuntimeLegs)
            {
                _animator.SetFloat(SpeedParam, speed, speedDamp, Time.deltaTime);
                _animator.SetFloat(PlaybackRateParam,
                    PlaybackRateForSpeed(speed, _creature.WalkSpeed, _creature.RunSpeed,
                        _creature.IdleDuration, _creature.WalkDuration, _creature.RunDuration,
                        _creature.MinPlaybackRate, _creature.MaxPlaybackRate));
            }
            if (faceVelocity && speed > MovementEpsilon && _state != CreatureState.Dead)
            {
                var target = Quaternion.LookRotation(planar.normalized, Vector3.up);
                transform.rotation = Quaternion.RotateTowards(transform.rotation, target, turnSpeedDeg * Time.deltaTime);
            }
        }
    }
}
