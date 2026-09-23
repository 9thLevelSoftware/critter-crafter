using System.Linq;
using UnityEditor;

namespace CritterCrafter.Editor
{
    /// <summary>
    /// Import settings for every model inside a critter library folder. The FBX axis/scale flags here
    /// pair with rigkit.export_fbx and are verified by FrameProbeTests.
    /// </summary>
    public class CritterModelPostprocessor : AssetPostprocessor
    {
        public const string LibrariesRoot = "Assets/CritterLibraries/";
        static readonly string[] LoopingClips = { "idle", "walk", "run", "stun" };

        static bool InLibrary(string path) => path.Replace('\\', '/').StartsWith(LibrariesRoot);
        static bool IsSkeleton(string path) => path.Replace('\\', '/').Contains("/skeletons/");

        void OnPreprocessModel()
        {
            if (!InLibrary(assetPath)) return;
            var mi = (ModelImporter)assetImporter;
            mi.globalScale = 1f;
            mi.useFileScale = true;
            mi.bakeAxisConversion = true;
            mi.importCameras = false;
            mi.importLights = false;
            mi.importVisibility = false;
            mi.importBlendShapes = false;
            mi.materialImportMode = ModelImporterMaterialImportMode.None;
            mi.animationType = ModelImporterAnimationType.Generic;
            mi.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            mi.optimizeGameObjects = false;
            mi.skinWeights = ModelImporterSkinWeights.Standard;
            mi.importAnimation = IsSkeleton(assetPath);
            // Motion artifacts are the authoritative 30fps samples. Keyframe reduction produced
            // centimetre-scale contact drift and degree-scale joint drift in Unity, so preserve keys.
            mi.animationCompression = ModelImporterAnimationCompression.Off;
            mi.isReadable = false;
        }

        void OnPreprocessAnimation()
        {
            if (!InLibrary(assetPath) || !IsSkeleton(assetPath)) return;
            var mi = (ModelImporter)assetImporter;
            var clips = mi.defaultClipAnimations;
            foreach (var c in clips)
            {
                string shortName = ShortClipName(c.name);
                c.name = shortName;
                c.loopTime = LoopingClips.Contains(shortName);
                c.loopPose = false;
                // No root motion: clips only animate bones; keep the root node where the game puts it.
                c.lockRootRotation = true;
                c.lockRootHeightY = false;
                c.lockRootPositionXZ = true;
                c.keepOriginalPositionXZ = true;
                c.keepOriginalOrientation = true;
                c.keepOriginalPositionY = true;
            }
            mi.clipAnimations = clips;
        }

        /// <summary>Blender FBX takes are named "Skeleton|walk"; keep the part after the last '|'.</summary>
        public static string ShortClipName(string name)
        {
            int i = name.LastIndexOf('|');
            return i >= 0 ? name.Substring(i + 1) : name;
        }
    }
}
