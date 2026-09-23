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
        public static readonly int SpeedParam = Animator.StringToHash("Speed");
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
        Vector3 _velocity;
        CreatureState _state = CreatureState.Idle;

        public CreatureState State => _state;

        void Awake()
        {
            _animator = GetComponent<AssembledCreature>().Animator;
            if (_animator != null) _animator.applyRootMotion = false;
        }

        public void SetVelocity(Vector3 worldVelocity)
        {
            _velocity = worldVelocity;
            if (_state == CreatureState.Idle || _state == CreatureState.Moving)
                _state = worldVelocity.sqrMagnitude > 0.0025f ? CreatureState.Moving : CreatureState.Idle;
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

        void Update()
        {
            if (_animator == null) return;
            Vector3 planar = new Vector3(_velocity.x, 0f, _velocity.z);
            float speed = _state == CreatureState.Dead ? 0f : planar.magnitude;
            _animator.SetFloat(SpeedParam, speed, speedDamp, Time.deltaTime);
            if (faceVelocity && speed > 0.05f && _state != CreatureState.Dead)
            {
                var target = Quaternion.LookRotation(planar.normalized, Vector3.up);
                transform.rotation = Quaternion.RotateTowards(transform.rotation, target, turnSpeedDeg * Time.deltaTime);
            }
        }
    }
}
