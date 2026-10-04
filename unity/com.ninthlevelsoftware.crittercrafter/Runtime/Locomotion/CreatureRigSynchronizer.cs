using UnityEngine;

namespace CritterCrafter.Locomotion
{
    // Publish the rig's actual reach projections before the review driver's LateUpdate (-1000).
    [DefaultExecutionOrder(-2000)]
    public sealed class CreatureRigSynchronizer : MonoBehaviour
    {
        CreatureGait _gait;

        void OnEnable() => _gait = GetComponent<CreatureGait>();

        void LateUpdate()
        {
            if (_gait != null) _gait.SynchronizeRigTargets();
        }
    }
}
