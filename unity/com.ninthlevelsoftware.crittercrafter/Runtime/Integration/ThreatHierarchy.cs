using System;
using UnityEngine;

namespace CritterCrafter
{
    /// <summary>
    /// Game-facing hierarchy: Threat_{id}/Mesh/&lt;assembled creature&gt;.
    /// NavMeshAgent stays out of this assembly so Runtime remains AI-free.
    /// </summary>
    public static class ThreatHierarchy
    {
        public const string MeshChildName = "Mesh";

        public static GameObject Wrap(AssembledCreature creature, string threatId)
        {
            if (creature == null) throw new ArgumentNullException(nameof(creature));
            if (string.IsNullOrEmpty(threatId)) throw new ArgumentException("threat id is required", nameof(threatId));

            var creatureTransform = creature.transform;
            var parent = creatureTransform.parent;
            var localPosition = creatureTransform.localPosition;
            var localRotation = creatureTransform.localRotation;
            int layer = creature.gameObject.layer;

            var threat = new GameObject("Threat_" + threatId);
            threat.layer = layer;
            threat.transform.SetParent(parent, false);
            threat.transform.localPosition = localPosition;
            threat.transform.localRotation = localRotation;

            var mesh = new GameObject(MeshChildName);
            mesh.layer = layer;
            mesh.transform.SetParent(threat.transform, false);

            creatureTransform.SetParent(mesh.transform, false);
            creatureTransform.localPosition = Vector3.zero;
            creatureTransform.localRotation = Quaternion.identity;
            return threat;
        }
    }
}
