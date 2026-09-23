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
    ///   Stun (bool Stunned), Telegraph / Attack / Hit (triggers, return to Locomotion), Death (trigger Die, final).
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

        public static AnimatorController Build(string controllerPath, Dictionary<string, AnimationClip> clips)
        {
            AssetDatabase.DeleteAsset(controllerPath);
            var ctrl = AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            ctrl.AddParameter("Speed", AnimatorControllerParameterType.Float);
            ctrl.AddParameter("Stunned", AnimatorControllerParameterType.Bool);
            ctrl.AddParameter("Telegraph", AnimatorControllerParameterType.Trigger);
            ctrl.AddParameter("Attack", AnimatorControllerParameterType.Trigger);
            ctrl.AddParameter("Hit", AnimatorControllerParameterType.Trigger);
            ctrl.AddParameter("Die", AnimatorControllerParameterType.Trigger);
            var sm = ctrl.layers[0].stateMachine;

            AnimationClip Clip(string n) => clips.TryGetValue(n, out var c) ? c : null;

            var locomotion = ctrl.CreateBlendTreeInController("Locomotion", out var tree, 0);
            tree.blendType = BlendTreeType.Simple1D;
            tree.blendParameter = "Speed";
            tree.useAutomaticThresholds = false;
            tree.AddChild(Clip("idle"), 0f);
            tree.AddChild(Clip("walk"), WalkSpeed);
            tree.AddChild(Clip("run"), RunSpeed);
            sm.defaultState = locomotion;

            var stun = sm.AddState("Stun");
            stun.motion = Clip("stun");
            var toStun = locomotion.AddTransition(stun);
            toStun.hasExitTime = false;
            toStun.duration = 0.1f;
            toStun.AddCondition(AnimatorConditionMode.If, 0, "Stunned");
            var fromStun = stun.AddTransition(locomotion);
            fromStun.hasExitTime = false;
            fromStun.duration = 0.15f;
            fromStun.AddCondition(AnimatorConditionMode.IfNot, 0, "Stunned");

            foreach (var name in new[] { "Telegraph", "Attack", "Hit" })
            {
                var st = sm.AddState(name);
                st.motion = Clip(name.ToLowerInvariant());
                var enter = sm.AddAnyStateTransition(st);
                enter.hasExitTime = false;
                enter.duration = name == "Hit" ? 0.05f : 0.1f;
                enter.canTransitionToSelf = name == "Hit";
                enter.AddCondition(AnimatorConditionMode.If, 0, name);
                var back = st.AddTransition(locomotion);
                back.hasExitTime = true;
                back.exitTime = 0.95f;
                back.duration = 0.1f;
            }

            var death = sm.AddState("Death");
            death.motion = Clip("death");
            var die = sm.AddAnyStateTransition(death);
            die.hasExitTime = false;
            die.duration = 0.1f;
            die.canTransitionToSelf = false;
            die.AddCondition(AnimatorConditionMode.If, 0, "Die");

            EditorUtility.SetDirty(ctrl);
            return ctrl;
        }
    }
}
