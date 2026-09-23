using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace CritterCrafter.Editor
{
    /// <summary>
    /// Renders a contact sheet of generated creatures (posed with a sampled clip) through an orthographic
    /// isometric camera like the target game's. Runs headless:
    ///   Unity -batchmode -projectPath ... -executeMethod CritterCrafter.Editor.ReviewCapture.CaptureFromCommandLine
    ///         -critterLibrary &lt;dir&gt; -critterOut &lt;png&gt; [-critterPool any] [-critterSeeds 1..8] [-critterClip idle] [-critterTime 0.3]
    /// </summary>
    public static class ReviewCapture
    {
        public static void CaptureFromCommandLine()
        {
            var args = System.Environment.GetCommandLineArgs();
            string Arg(string name, string def)
            {
                int i = System.Array.IndexOf(args, name);
                return i >= 0 && i + 1 < args.Length ? args[i + 1] : def;
            }
            var report = LibraryImporter.Import(Arg("-critterLibrary", ""));
            foreach (var p in report.Problems) Debug.LogWarning("[CritterCrafter] " + p);
            var seeds = Arg("-critterSeeds", "1..8").Split(new[] { ".." }, System.StringSplitOptions.None);
            int a = int.Parse(seeds[0]), b = seeds.Length > 1 ? int.Parse(seeds[1]) : a;
            var list = new List<long>();
            for (int s = a; s <= b; s++) list.Add(s);
            Capture(report.Library, Arg("-critterPool", "any"), list, Arg("-critterClip", "idle"),
                float.Parse(Arg("-critterTime", "0.3"), System.Globalization.CultureInfo.InvariantCulture), Arg("-critterOut", "critter_review.png"));
            EditorApplication.Exit(report.Problems.Count == 0 ? 0 : 1);
        }

        public static void Capture(CritterLibrary library, string pool, IList<long> seeds, string clipName, float time, string outPath, int cell = 384)
        {
            int cols = Mathf.Min(4, seeds.Count), rows = (seeds.Count + cols - 1) / cols;
            var sheet = new Texture2D(cols * cell, rows * cell, TextureFormat.RGB24, false);
            var rt = new RenderTexture(cell, cell, 24, RenderTextureFormat.ARGB32) { antiAliasing = 4 };
            var holder = new GameObject("CritterReview");
            var camGo = new GameObject("Cam");
            camGo.transform.SetParent(holder.transform);
            var cam = camGo.AddComponent<Camera>();
            cam.orthographic = true;
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.12f, 0.13f, 0.16f);
            cam.targetTexture = rt;
            var lightGo = new GameObject("Key");
            lightGo.transform.SetParent(holder.transform);
            var light = lightGo.AddComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 1.2f;
            lightGo.transform.rotation = Quaternion.Euler(50f, -30f, 0f);
            var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
            ground.transform.SetParent(holder.transform);
            ground.transform.localScale = Vector3.one * 4f;
            ground.GetComponent<Renderer>().sharedMaterial = new Material(LibraryImporter.DefaultLitShader()) { color = new Color(0.2f, 0.21f, 0.24f) };

            for (int i = 0; i < seeds.Count; i++)
            {
                var recipe = library.Generate(pool, seeds[i]);
                var creature = CreatureAssembler.Assemble(library, recipe, AssemblyOptions.Default);
                if (creature.Animator != null)
                {
                    var clips = AnimatorBuilder.LoadClips(AssetDatabase.GetAssetPath(library.FindSkeleton(recipe.skeleton_id).model));
                    if (clips.TryGetValue(clipName, out var clip)) clip.SampleAnimation(creature.Animator.gameObject, time);
                }
                var bounds = creature.BindBoundsLocal;
                float h = Mathf.Max(1.2f, bounds.size.y);
                // Game camera: offset (16, 18, 16) looking at the target, orthographic.
                var target = creature.transform.position + Vector3.up * (h * 0.45f);
                cam.transform.position = target + new Vector3(16f, 18f, 16f).normalized * 20f;
                cam.transform.LookAt(target);
                cam.orthographicSize = Mathf.Max(1.1f, Mathf.Max(h, Mathf.Max(bounds.size.x, bounds.size.z)) * 0.7f);
                cam.Render();
                RenderTexture.active = rt;
                var tile = new Texture2D(cell, cell, TextureFormat.RGB24, false);
                tile.ReadPixels(new Rect(0, 0, cell, cell), 0, 0);
                tile.Apply();
                RenderTexture.active = null;
                int x = (i % cols) * cell, y = (rows - 1 - i / cols) * cell;
                sheet.SetPixels(x, y, cell, cell, tile.GetPixels());
                Object.DestroyImmediate(tile);
                Object.DestroyImmediate(creature.gameObject);
            }
            sheet.Apply();
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outPath)));
            File.WriteAllBytes(outPath, sheet.EncodeToPNG());
            Object.DestroyImmediate(holder);
            Object.DestroyImmediate(sheet);
            rt.Release();
            Debug.Log("[CritterCrafter] review sheet written: " + outPath);
        }
    }
}
