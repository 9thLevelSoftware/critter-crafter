using UnityEngine;

namespace CritterCrafter
{
    /// <summary>
    /// The single glTF (catalog) -> Unity conversion (docs/frame.md). A hypothesis locked by
    /// FrameProbeTests: converted snap positions must coincide with imported branch-root bones.
    /// </summary>
    public static class CritterFrame
    {
        public static Vector3 Position(double[] p) => new Vector3(-(float)p[0], (float)p[1], (float)p[2]);

        /// <summary>Catalog quaternions are rounded to 6 decimals; normalize so Unity's unit-quaternion
        /// math (Quaternion.Angle, composition) does not turn rounding into visible angles.</summary>
        public static Quaternion Rotation(double[] q) =>
            new Quaternion((float)q[0], -(float)q[1], -(float)q[2], (float)q[3]).normalized;

        /// <summary>Snap frame (part space -> skeleton-root space) including the uniform length scale.</summary>
        public static Matrix4x4 Snap(SnapData snap, float scale) =>
            Matrix4x4.TRS(Position(snap.position_m), Rotation(snap.rotation_xyzw), Vector3.one * scale);
    }
}
