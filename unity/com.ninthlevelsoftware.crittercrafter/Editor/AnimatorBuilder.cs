using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace CritterCrafter.Editor
{
    /// <summary>
    /// Builds one AnimatorController per skeleton from its imported clips:
    ///   Locomotion (1D blend on Speed m/s: idle 0, walk 1.2, run 3.0) [default]
    ///   Stun (bool Stunned), Telegraph -> Attack (continuous metadata continuation), Attack / Hit
    ///   (triggers, complete before returning to Locomotion), Death (trigger Die, final).
    /// Parameter names are the CreatureMotion constants.
    /// </summary>
    public static class AnimatorBuilder
    {
        public const float WalkSpeed = 1.2f;
        public const float RunSpeed = 3.0f;

        public static Dictionary<string, AnimationClip> LoadClips(string modelPath)
        {
            var clips = new Dictionary<string, AnimationClip>();
            foreach (var c in AssetDatabase.LoadAllAssetsAtPath(modelPath).OfType<AnimationClip>())
            {
                if (c.name.StartsWith("__preview__")) continue;
                clips[CritterModelPostprocessor.ShortClipName(c.name)] = c;
            }
            return clips;
        }

        public static AnimatorController Build(string controllerPath, Dictionary<string, AnimationClip> clips, SkeletonData skeleton = null)
        {
            AssetDatabase.DeleteAsset(controllerPath);
            var ctrl = AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            ctrl.AddParameter("Speed", AnimatorControllerParameterType.Float);
            ctrl.AddParameter("PlaybackRate", AnimatorControllerParameterType.Float);
            ctrl.AddParameter("Stunned", AnimatorControllerParameterType.Bool);
            ctrl.AddParameter("Telegraph", AnimatorControllerParameterType.Trigger);
            ctrl.AddParameter("Attack", AnimatorControllerParameterType.Trigger);
            ctrl.AddParameter("Hit", AnimatorControllerParameterType.Trigger);
            ctrl.AddParameter("Die", AnimatorControllerParameterType.Trigger);
            var sm = ctrl.layers[0].stateMachine;

            AnimationClip Clip(string n) => clips.TryGetValue(n, out var c) ? c : null;
            float Speed(string n, float fallback)
            {
                var info = skeleton?.asset?.clips == null ? null : System.Array.Find(skeleton.asset.clips, c => c.name == n);
                double value = info == null ? 0.0 : (info.speed_mps > 0.0 ? info.speed_mps : info.nominal_speed_mps);
                return value > 0.0 ? (float)value : fallback;
            }

            if (skeleton?.locomotion != null && skeleton.locomotion.IsPhaseDriven)
            {
                BuildRuntimeLegStates(ctrl, sm, Clip, out var idleState);
                AddActionStates(ctrl, sm, idleState, Clip, skeleton);
                EditorUtility.SetDirty(ctrl);
                return ctrl;
            }

            var locomotion = ctrl.CreateBlendTreeInController("Locomotion", out var tree, 0);
            tree.blendType = BlendTreeType.Simple1D;
            tree.blendParameter = "Speed";
            tree.useAutomaticThresholds = false;
            tree.AddChild(Clip("idle"), 0f);
            tree.AddChild(Clip("walk"), Speed("walk", WalkSpeed));
            tree.AddChild(Clip("run"), Speed("run", RunSpeed));
            var locomotionChildren = tree.children;
            string[] locomotionNames = { "idle", "walk", "run" };
            for (int i = 0; i < locomotionChildren.Length && i < locomotionNames.Length; i++)
            {
                var info = skeleton?.asset?.clips == null ? null
                    : System.Array.Find(skeleton.asset.clips, c => c.name == locomotionNames[i]);
                if (info?.playback?.phase_range != null && info.playback.phase_range.Length == 2)
                    locomotionChildren[i].cycleOffset = (float)info.playback.phase_range[0];
            }
            tree.children = locomotionChildren;
            locomotion.speedParameterActive = true;
            locomotion.speedParameter = "PlaybackRate";
            sm.defaultState = locomotion;

            AddActionStates(ctrl, sm, locomotion, Clip, skeleton);
            EditorUtility.SetDirty(ctrl);
            return ctrl;
        }

        /// <summary>
        /// Runtime-leg skeletons: Idle plays on its own clock; Locomotion blends the walk/run overlays on
        /// Gait (0 walk .. 1 run) with normalized time driven by GaitPhase from CreatureGait.
        /// </summary>
        static void BuildRuntimeLegStates(AnimatorController ctrl, AnimatorStateMachine sm,
            System.Func<string, AnimationClip> clip, out AnimatorState idle)
        {
            ctrl.AddParameter("Gait", AnimatorControllerParameterType.Float);
            ctrl.AddParameter("GaitPhase", AnimatorControllerParameterType.Float);
            idle = sm.AddState("Idle");
            idle.motion = clip("idle");
            sm.defaultState = idle;
            var locomotion = ctrl.CreateBlendTreeInController("Locomotion", out var tree, 0);
            tree.blendType = BlendTreeType.Simple1D;
            tree.blendParameter = "Gait";
            tree.useAutomaticThresholds = false;
            tree.AddChild(clip("walk"), 0f);
            tree.AddChild(clip("run"), 1f);
            locomotion.timeParameterActive = true;
            locomotion.timeParameter = "GaitPhase";
            var start = idle.AddTransition(locomotion);
            start.hasExitTime = false;
            start.duration = 0.15f;
            start.AddCondition(AnimatorConditionMode.Greater, MovingThreshold, "Speed");
            var stop = locomotion.AddTransition(idle);
            stop.hasExitTime = false;
            stop.duration = 0.2f;
            stop.AddCondition(AnimatorConditionMode.Less, MovingThreshold, "Speed");
        }

        public const float MovingThreshold = 0.05f;

        static void AddActionStates(AnimatorController ctrl, AnimatorStateMachine sm, AnimatorState locomotion,
            System.Func<string, AnimationClip> Clip, SkeletonData skeleton)
        {
            var stun = sm.AddState("Stun");
            stun.motion = Clip("stun");
            // Any State so Stun is reachable from Idle and Locomotion (phase-driven skeletons
            // keep those as separate states) as well as from the clip-driven blend tree.
            var toStun = sm.AddAnyStateTransition(stun);
            toStun.hasExitTime = false;
            toStun.duration = 0.1f;
            toStun.canTransitionToSelf = false;
            toStun.AddCondition(AnimatorConditionMode.If, 0, "Stunned");
            var fromStun = stun.AddTransition(locomotion);
            fromStun.hasExitTime = false;
            fromStun.duration = 0.15f;
            fromStun.AddCondition(AnimatorConditionMode.IfNot, 0, "Stunned");

            var actionStates = new Dictionary<string, AnimatorState>();
            foreach (var name in new[] { "telegraph", "attack", "hit" })
            {
                string displayName = char.ToUpperInvariant(name[0]) + name.Substring(1);
                var st = sm.AddState(displayName);
                st.motion = Clip(name);
                actionStates[name] = st;
                var enter = sm.AddAnyStateTransition(st);
                enter.hasExitTime = false;
                enter.duration = name == "hit" ? 0.05f : 0.1f;
                enter.canTransitionToSelf = name == "hit";
                enter.AddCondition(AnimatorConditionMode.If, 0, displayName);
            }
            foreach (var pair in actionStates)
            {
                var info = skeleton?.asset?.clips == null ? null
                    : System.Array.Find(skeleton.asset.clips, c => c.name == pair.Key);
                string continuation = info?.playback?.continuation ?? "locomotion";
                AnimatorState target = continuation == "locomotion" ? locomotion
                    : actionStates.TryGetValue(continuation, out var continuationState) ? continuationState : locomotion;
                var next = pair.Value.AddTransition(target);
                next.hasExitTime = true;
                next.exitTime = 1f;
                // Telegraph's endpoint is attack frame zero; preserve that authored seam exactly.
                next.duration = continuation == "attack" ? 0f : 0.1f;
            }

            var death = sm.AddState("Death");
            death.motion = Clip("death");
            var die = sm.AddAnyStateTransition(death);
            die.hasExitTime = false;
            die.duration = 0.1f;
            die.canTransitionToSelf = false;
            die.AddCondition(AnimatorConditionMode.If, 0, "Die");

        }
    }
}
