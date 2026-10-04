using System;
using System.Collections.Generic;
using CritterCrafter.Locomotion;
using UnityEngine;

namespace CritterCrafter.Review
{
    public sealed class ReviewGroundingMeasurement : IDisposable
    {
        sealed class BoundContact
        {
            public string branchId;
            public int index;
            public bool body, support;
            public Transform bone, tip;
            public Vector3 localPoint;
            public CreatureGait.Leg leg;
        }

        sealed class Surface
        {
            public SkinnedMeshRenderer renderer;
            public Mesh mesh;
            public readonly List<Vector3> vertices = new List<Vector3>();
            public int vertexCount;
        }

        readonly AssembledCreature _creature;
        readonly CreatureGait _gait;
        readonly List<BoundContact> _contacts = new List<BoundContact>();
        readonly List<Surface> _surfaces = new List<Surface>();
        readonly List<AnimatorClipInfo> _clips = new List<AnimatorClipInfo>(4);
        readonly List<AnimatorClipInfo> _nextClips = new List<AnimatorClipInfo>(4);
        string _problem;

        public ReviewGroundingMeasurement(AssembledCreature creature, CreatureGait gait)
        {
            _creature = creature;
            _gait = gait;
            try { Bind(); }
            catch (Exception e) { _problem = e.Message; }
        }

        public static Vector3 ImportedLocalPoint(BoneData bone, Matrix4x4 importedRest, double[] localPoint)
        {
            var canonicalPoint = CritterFrame.Position(bone.head_m)
                + SkeletonPose.CanonicalBoneBasis(bone) * CritterFrame.Position(localPoint);
            return importedRest.inverse.MultiplyPoint3x4(canonicalPoint);
        }

        public static bool ScheduledGrounding(ContactScheduleEntry entry, ClipInfo clip, double phase)
        {
            if (entry == null || clip == null || !Finite(phase) || !entry.grounding_declared || !Finite(entry.grounding_fraction)
                || entry.grounding_fraction < 0 || entry.grounding_fraction > 1 || !Finite(entry.phase_offset))
                throw new InvalidOperationException("missing or invalid authored grounding schedule");
            if (entry.grounding_fraction == 1) return true;
            if (entry.grounding_fraction == 0) return false;
            double u = clip.loop ? phase - Math.Floor(phase) : Math.Max(0, Math.Min(1, phase));
            u += entry.phase_offset;
            if (clip.loop) u -= Math.Floor(u);
            return u >= 0 && u < entry.grounding_fraction;
        }

        void Bind()
        {
            if (_creature == null || _creature.Skeleton == null || _creature.Animator == null
                || _creature.ImportedRest == null || _gait == null)
                throw new InvalidOperationException("missing skeleton, animator, gait or imported bind correspondence");
            var skeleton = _creature.Skeleton;
            var transforms = new Dictionary<string, Transform>();
            foreach (var t in _creature.Animator.GetComponentsInChildren<Transform>(true))
                if (!transforms.ContainsKey(t.name)) transforms.Add(t.name, t);
            var bones = new Dictionary<string, BoneData>();
            foreach (var b in skeleton.bones) bones.Add(b.name, b);
            var filled = new HashSet<string>();
            var recipe = _creature.Recipe;
            if (recipe == null || recipe.skeleton_id != skeleton.skeleton_id || recipe.fills == null || recipe.fills.Length == 0)
                throw new InvalidOperationException("missing or mismatched authoritative recipe coverage");
            foreach (var fill in recipe.fills)
            {
                if (fill == null || string.IsNullOrEmpty(fill.branch_id))
                    throw new InvalidOperationException("missing authoritative recipe fill coverage");
                filled.Add(fill.branch_id);
            }
            foreach (var part in _creature.Renderers)
            {
                var renderer = part.renderer;
                if (renderer == null || renderer.sharedMesh == null || renderer.sharedMesh.vertexCount == 0)
                    throw new InvalidOperationException("missing surface for part " + part.partId);
                var surface = new Surface { renderer = renderer, mesh = new Mesh(), vertexCount = renderer.sharedMesh.vertexCount };
                surface.mesh.MarkDynamic();
                surface.vertices.Capacity = surface.vertexCount;
                _surfaces.Add(surface);
            }
            if (_surfaces.Count == 0) throw new InvalidOperationException("no creature surfaces to measure");
            foreach (var branch in skeleton.branches)
            {
                if (branch.contacts == null) continue;
                for (int i = 0; i < branch.contacts.Length; i++)
                {
                    var contact = branch.contacts[i];
                    if (!filled.Contains(branch.branch_id) || branch.bone_names == null || contact == null
                        || contact.bone_index < 0 || contact.bone_index >= branch.bone_names.Length
                        || contact.local_point_m == null || contact.local_point_m.Length != 3)
                        throw new InvalidOperationException("missing declared contact coverage: " + branch.branch_id + ":" + i);
                    string name = branch.bone_names[contact.bone_index];
                    if (!transforms.TryGetValue(name, out var bone) || !bones.TryGetValue(name, out var data)
                        || !_creature.ImportedRest.TryGetValue(name, out var rest))
                        throw new InvalidOperationException("missing contact bone/bind: " + name);
                    bool body = contact.kind == "body" || contact.kind == "sliding";
                    if (!body && contact.kind != "foot" && contact.kind != "hand")
                        throw new InvalidOperationException("unknown contact kind: " + contact.kind);
                    CreatureGait.Leg leg = null;
                    foreach (var candidate in _gait.Legs)
                        if (candidate.branchId == branch.branch_id) { leg = candidate; break; }
                    if (!body && branch.gait_role == "locomotor" && leg == null)
                        throw new InvalidOperationException("missing runtime leg: " + branch.branch_id);
                    var point = body ? ImportedLocalPoint(data, rest, contact.local_point_m) : Vector3.zero;
                    if (!Finite(point)) throw new InvalidOperationException("invalid contact bind: " + name);
                    transforms.TryGetValue(branch.branch_id + "_ik_tip", out var tip);
                    if (!body && leg != null && tip == null)
                        throw new InvalidOperationException("missing actual IK tip: " + branch.branch_id);
                    _contacts.Add(new BoundContact { branchId = branch.branch_id, index = i, bone = bone,
                        localPoint = point, body = body, leg = leg, tip = tip,
                        support = Array.IndexOf(skeleton.anatomy?.support_branches ?? Array.Empty<string>(), branch.branch_id) >= 0 });
                }
            }
            if (_contacts.Count == 0) throw new InvalidOperationException("no declared contacts to measure");
        }

        bool Activity(BoundContact contact, bool reactions, out bool support)
        {
            support = contact.support;
            if (!reactions) return contact.body || (contact.leg != null && contact.leg.planted && contact.leg.weight >= .999f);
            var animator = _creature.Animator;
            bool active = true, seen = false;
            CheckClips(contact, _clips, animator.GetCurrentAnimatorStateInfo(0).normalizedTime, ref active, ref seen, ref support);
            if (animator.IsInTransition(0))
                CheckClips(contact, _nextClips, animator.GetNextAnimatorStateInfo(0).normalizedTime, ref active, ref seen, ref support);
            if (!seen) throw new InvalidOperationException("no evaluated clip grounding schedule");
            return active;
        }

        void CheckClips(BoundContact contact, List<AnimatorClipInfo> clips, double phase,
            ref bool active, ref bool seen, ref bool support)
        {
            foreach (var evaluated in clips)
            {
                if (evaluated.weight <= .0001f) continue;
                ClipInfo info = null;
                var metadata = _creature.Skeleton.asset?.clips;
                if (metadata != null)
                    foreach (var candidate in metadata)
                        if (candidate.name == evaluated.clip.name) { info = candidate; break; }
                ContactScheduleEntry entry = null;
                if (info?.contact_schedule != null)
                    foreach (var candidate in info.contact_schedule)
                        if (candidate.branch_id == contact.branchId && candidate.contact_index == contact.index)
                        {
                            if (entry != null) throw new InvalidOperationException("duplicate grounding schedule entry");
                            entry = candidate;
                        }
                if (info == null || entry == null)
                    throw new InvalidOperationException("missing grounding schedule: " + evaluated.clip.name + "/" + contact.branchId + ":" + contact.index);
                seen = true;
                active &= ScheduledGrounding(entry, info, phase);
                support &= entry.support;
            }
        }

        public void Measure(LocomotionMetrics metrics, bool reactions)
        {
            if (_problem != null) { Incomplete(metrics, _problem); return; }
            try
            {
                if (reactions)
                {
                    _creature.Animator.GetCurrentAnimatorClipInfo(0, _clips);
                    _nextClips.Clear();
                    if (_creature.Animator.IsInTransition(0)) _creature.Animator.GetNextAnimatorClipInfo(0, _nextClips);
                }
                int required = 0, planted = 0;
                foreach (var contact in _contacts)
                {
                    bool active = Activity(contact, reactions, out var support);
                    if (!active) continue;
                    if (contact.body ? contact.bone == null : contact.tip == null)
                        throw new InvalidOperationException("destroyed declared contact bone or IK tip");
                    Vector3 point = contact.body ? contact.bone.TransformPoint(contact.localPoint) : contact.tip.position;
                    if (!Finite(point) || !ReviewCourse.TryGroundY(point, out var ground))
                        throw new InvalidOperationException("unmeasured ground under contact: " + contact.branchId + ":" + contact.index);
                    float hover = Mathf.Max(0f, point.y - ground);
                    if (contact.body) metrics.max_body_contact_hover_m = Mathf.Max(metrics.max_body_contact_hover_m, hover);
                    else if (reactions && support)
                    {
                        required++;
                        if (contact.leg != null && contact.leg.support && contact.leg.planted && contact.leg.weight >= .5f) planted++;
                    }
                }
                // CanLift deliberately permits zero planted feet when the body rests/slides on ground.
                if (!_gait.Block.body_on_ground && !_gait.Block.Slides)
                {
                    if (!reactions)
                        foreach (var leg in _gait.Legs)
                            if (leg.support && leg.planted && leg.weight >= .5f) planted++;
                    int minimum = reactions ? required : _gait.Block.min_support;
                    if (planted < minimum) metrics.support_violations++;
                }
                foreach (var surface in _surfaces)
                {
                    var renderer = surface.renderer;
                    if (renderer == null || !renderer.enabled || !renderer.gameObject.activeInHierarchy
                        || renderer.sharedMesh == null || renderer.sharedMesh.vertexCount != surface.vertexCount)
                        throw new InvalidOperationException("missing or changed creature surface");
                    renderer.BakeMesh(surface.mesh, false);
                    surface.mesh.GetVertices(surface.vertices);
                    if (surface.vertices.Count != surface.vertexCount) throw new InvalidOperationException("incomplete deformed surface");
                    var toWorld = renderer.transform.localToWorldMatrix;
                    foreach (var vertex in surface.vertices)
                    {
                        var point = toWorld.MultiplyPoint3x4(vertex);
                        if (!Finite(point) || !ReviewCourse.TryGroundY(point, out var ground))
                            throw new InvalidOperationException("unmeasured ground under deformed surface");
                        metrics.max_surface_penetration_m = Mathf.Max(metrics.max_surface_penetration_m, ground - point.y);
                    }
                }
            }
            catch (Exception e) { _problem = e.Message; Incomplete(metrics, _problem); }
        }

        static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);
        static bool Finite(Vector3 value) => Finite(value.x) && Finite(value.y) && Finite(value.z);
        static void Incomplete(LocomotionMetrics metrics, string problem)
        {
            metrics.grounding_measurement_complete = false;
            metrics.support_measurement_complete = false;
            if (string.IsNullOrEmpty(metrics.grounding_measurement_problem)) metrics.grounding_measurement_problem = problem;
        }

        public void Dispose()
        {
            foreach (var surface in _surfaces)
            {
                if (Application.isPlaying) UnityEngine.Object.Destroy(surface.mesh);
                else UnityEngine.Object.DestroyImmediate(surface.mesh);
            }
            _surfaces.Clear();
        }
    }
}
