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

            var threat = new GameObject("Threat_" + threatId);
            var mesh = new GameObject(MeshChildName);
            mesh.transform.SetParent(threat.transform, false);

            var creatureTransform = creature.transform;
            threat.transform.SetParent(creatureTransform.parent, false);
            creatureTransform.SetParent(mesh.transform, false);
            return threat;
        }
    }
}
