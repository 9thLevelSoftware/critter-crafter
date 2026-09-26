using System.Collections.Generic;
using UnityEngine;

namespace CritterCrafter.Review
{
    /// <summary>
    /// The locomotion review course: flat start, a 20 degree ramp up to a plateau, an instant 90 degree
    /// turn, a stop, and an instant 180 degree turn in place (NavMeshAgent with angularSpeed 999).
    /// </summary>
    public static class ReviewCourse
    {
        public struct Waypoint
        {
            public float time;
            public Vector3 position;
            public float headingDeg;
        }

        public const float RampAngleDeg = 20f;
        public const float RampLength = 4f;

        public static void Build(Transform parent, Material material)
        {
            var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
            ground.transform.SetParent(parent, false);
            ground.transform.localScale = Vector3.one * 10f;
            ground.GetComponent<Renderer>().sharedMaterial = material;
            float rise = RampLength * Mathf.Sin(RampAngleDeg * Mathf.Deg2Rad);
            float run = RampLength * Mathf.Cos(RampAngleDeg * Mathf.Deg2Rad);
            var ramp = GameObject.CreatePrimitive(PrimitiveType.Cube);
            ramp.transform.SetParent(parent, false);
            ramp.transform.localScale = new Vector3(4f, 0.2f, RampLength);
            ramp.transform.localRotation = Quaternion.Euler(-RampAngleDeg, 0f, 0f);
            // Top surface runs from (z=2, y=0) to (z=2+run, y=rise).
            Vector3 top = new Vector3(0f, rise * 0.5f, 2f + run * 0.5f);
            ramp.transform.localPosition = top - ramp.transform.localRotation * Vector3.up * 0.1f;
            ramp.GetComponent<Renderer>().sharedMaterial = material;
            var plateau = GameObject.CreatePrimitive(PrimitiveType.Cube);
            plateau.transform.SetParent(parent, false);
            plateau.transform.localScale = new Vector3(8f, rise, 6f);
            plateau.transform.localPosition = new Vector3(2f, rise * 0.5f, 2f + run + 3f - 0.001f);
            plateau.GetComponent<Renderer>().sharedMaterial = material;
            var light = new GameObject("Key").AddComponent<Light>();
            light.transform.SetParent(parent, false);
            light.type = LightType.Directional;
            light.intensity = 1.2f;
            light.shadows = LightShadows.Soft;
            light.transform.rotation = Quaternion.Euler(50f, -30f, 0f);
        }

        public static List<Waypoint> Path(float speed)
        {
            float rampRun = RampLength * Mathf.Cos(RampAngleDeg * Mathf.Deg2Rad);
            var a = new Vector3(0f, 0f, -3f);
            var b = new Vector3(0f, 0f, 2f + rampRun + 2.5f);
            var c = b + new Vector3(2f, 0f, 0f);
            float t1 = Vector3.Distance(a, b) / speed;
            float t2 = t1 + 2f / speed;
            return new List<Waypoint>
            {
                new Waypoint { time = 0f, position = a, headingDeg = 0f },
                new Waypoint { time = t1, position = b, headingDeg = 0f },
                new Waypoint { time = t1 + 1e-4f, position = b, headingDeg = 90f },
                new Waypoint { time = t2, position = c, headingDeg = 90f },
                new Waypoint { time = t2 + 1.2f, position = c, headingDeg = 90f },
                new Waypoint { time = t2 + 1.2f + 1e-4f, position = c, headingDeg = 270f },
                new Waypoint { time = t2 + 2.4f, position = c, headingDeg = 270f },
            };
        }

        /// <summary>
        /// Trot in, instant 180 in place (dwell through yaw ease), then trot out the other way.
        /// </summary>
        public static List<Waypoint> Turn180(float speed, float before = 1.2f, float dwell = 4f, float after = 1.2f)
        {
            var a = new Vector3(0f, 0f, -3f);
            var b = a + Vector3.forward * speed * before;
            var c = b + Vector3.back * speed * after;
            return new List<Waypoint>
            {
                new Waypoint { time = 0f, position = a, headingDeg = 0f },
                new Waypoint { time = before, position = b, headingDeg = 0f },
                new Waypoint { time = before + 1e-4f, position = b, headingDeg = 180f },
                new Waypoint { time = before + dwell, position = b, headingDeg = 180f },
                new Waypoint { time = before + dwell + after, position = c, headingDeg = 180f },
            };
        }

        /// <summary>A straight line at constant speed (used by tests).</summary>
        public static List<Waypoint> Straight(float speed, float seconds)
        {
            var a = new Vector3(0f, 0f, -3f);
            return new List<Waypoint>
            {
                new Waypoint { time = 0f, position = a, headingDeg = 0f },
                new Waypoint { time = seconds, position = a + Vector3.forward * speed * seconds, headingDeg = 0f },
            };
        }

        public static void Sample(List<Waypoint> path, float time, out Vector3 position, out float heading)
        {
            for (int i = 1; i < path.Count; i++)
            {
                if (time > path[i].time) continue;
                var a = path[i - 1];
                var b = path[i];
                float u = b.time > a.time ? (time - a.time) / (b.time - a.time) : 1f;
                position = Vector3.Lerp(a.position, b.position, u);
                heading = u >= 1f ? b.headingDeg : a.headingDeg;
                return;
            }
            position = path[path.Count - 1].position;
            heading = path[path.Count - 1].headingDeg;
        }

        public static float GroundY(Vector3 p) =>
            Physics.Raycast(p + Vector3.up * 5f, Vector3.down, out var hit, 20f, ~0, QueryTriggerInteraction.Ignore)
                ? hit.point.y : 0f;
    }
}
