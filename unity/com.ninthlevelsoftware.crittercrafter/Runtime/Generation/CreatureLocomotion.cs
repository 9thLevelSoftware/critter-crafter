using System;
using System.Collections.Generic;

namespace CritterCrafter
{
    /// <summary>
    /// Per-creature gait and speed from the assembled build (model build-1): a line-for-line port of
    /// src/critter_crafter/locomotion/build.py. Leg geometry, duties, phases and strokes come from the
    /// skeleton's block untouched; mass, centre of mass and a performance factor P rescale the speeds.
    /// Only arithmetic, min, max, clamp, sqrt and pow are used and nothing branches on a float. Outputs are
    /// not rounded here, so they differ from the (6-decimal) golden by rounding only.
    /// </summary>
    public static class CreatureLocomotion
    {
        public const string Model = "build-1";
        const string ArmProfile = "limb3_brachial";
        const double Gravity = 9.81, StrokeFraction = .9;
        const double MuscleReference = .45, ReferenceMassKg = 60.0;
        const double PerformanceMin = .4, PerformanceMax = 1.8, CadenceMin = 2.0, CadenceMax = 6.0;
        const double SupportMarginM = .05, RunFroude = 2.2;
        const double WalkFroudeRoot = .5, WalkFractionOfMax = .5, WalkFractionOfRun = .6;
        const double RunFractionOfMax = .9, MinRunMps = 1.0, MinSwingS = .12;
        const double DragCadenceWalk = 1.0, DragCadenceRun = 1.8, SlideCadenceWalk = 1.0, SlideCadenceRun = 2.0;

        static void Density(string category, out double density, out double shape)
        {
            switch (category)
            {
                case "limb": density = 1050.0; shape = .60; break;
                case "core": density = 1000.0; shape = .70; break;
                case "head": density = 1100.0; shape = .52; break;
                case "tail": density = 1000.0; shape = .55; break;
                case "appendage": density = 1000.0; shape = .55; break;
                default: density = 1000.0; shape = .55; break;   // connectors add no mass (never a fill's part)
            }
        }

        static double Clamp(double x, double lo, double hi) => Math.Max(lo, Math.Min(hi, x));

        /// <summary>The skeleton's locomotion block with speeds and build replaced by this creature's.</summary>
        public static LocomotionData Build(CatalogData catalog, CritterRecipe recipe)
        {
            var skeleton = catalog.FindSkeleton(recipe.skeleton_id);
            var source = skeleton?.locomotion;
            if (source == null) return null;
            var block = source.ShallowCopy();
            string mode = block.mode ?? "none";
            bool drag = block.gait == "drag";

            var segments = new Dictionary<string, double[]>();
            if (block.segments != null) foreach (var s in block.segments) segments[s.branch_id] = s.centroid_m;
            var legIds = new HashSet<string>();
            if (block.legs != null) foreach (var leg in block.legs) legIds.Add(leg.branch_id);

            double total = 0.0, muscleMass = 0.0, cx = 0.0, cy = 0.0, cz = 0.0;
            int limbFills = 0, armFills = 0;
            foreach (var fill in recipe.fills)   // recipe order
            {
                var part = catalog.FindPart(fill.part_id);
                var branch = skeleton.FindBranch(fill.branch_id);
                Density(part.category, out double density, out double shape);
                double thickness = part.dimensions_m[1] * fill.length_scale * fill.girth_scale;
                double mass = density * shape * (branch.length_mm / 1000.0) * (branch.girth_mm / 1000.0) * thickness;
                total += mass;
                if (legIds.Contains(fill.branch_id)) muscleMass += mass;
                if (segments.TryGetValue(fill.branch_id, out var c)) { cx += c[0] * mass; cy += c[1] * mass; cz += c[2] * mass; }
                if (part.category == "limb")
                {
                    limbFills++;
                    if (fill.binding_profile_id == ArmProfile) armFills++;
                }
            }
            total = Math.Max(total, 1e-9);
            double[] com = { cx / total, cy / total, cz / total };
            double muscle = muscleMass / total;
            double arm = (double)armFills / Math.Max(1, limbFills);
            double massTerm = Math.Pow(ReferenceMassKg / total, .1);

            double imbalance = 0.0;
            if (mode == "legs" && !drag && block.legs != null && block.legs.Length > 0)
            {
                double hx = 0.0, hz = 0.0;
                foreach (var leg in block.legs) { hx += leg.home_m[0]; hz += leg.home_m[2]; }
                hx /= block.legs.Length; hz /= block.legs.Length;
                double radius = 0.0;
                foreach (var leg in block.legs)
                    radius = Math.Max(radius, Hypot(leg.home_m[0] - hx, leg.home_m[2] - hz));
                imbalance = Hypot(com[0] - hx, com[2] - hz) / (radius + SupportMarginM);
            }

            double performance;
            if (mode == "legs")
                performance = Clamp(Math.Sqrt(muscle / MuscleReference) * (1.0 - .4 * Math.Min(1.0, imbalance))
                                    * (1.0 - .25 * arm) * massTerm, PerformanceMin, PerformanceMax);
            else if (mode == "slide") performance = Clamp(massTerm, PerformanceMin, PerformanceMax);
            else performance = 1.0;

            double cadence, vWalk, vRun, vMax;
            if (mode == "legs")
            {
                cadence = Clamp(block.cadence_max_hz * performance, CadenceMin, CadenceMax);
                double stroke = block.usable_stroke_m, dutyRun = block.duty_run;
                if (drag)
                {
                    double strideWalk = StrokeFraction * stroke / block.duty_walk;
                    double strideRun = StrokeFraction * stroke / dutyRun;
                    vMax = strideRun * cadence;
                    vRun = Math.Min(strideRun * DragCadenceRun * performance * performance, vMax);
                    vWalk = Math.Min(strideWalk * DragCadenceWalk * performance,
                        Math.Min(WalkFractionOfMax * vMax, WalkFractionOfRun * vRun));
                }
                else
                {
                    double h = block.hip_height_m;
                    double runCadence = Math.Min(cadence, (1.0 - dutyRun) / MinSwingS);
                    vMax = runCadence * StrokeFraction * stroke / dutyRun;
                    double vFroude = Math.Pow(runCadence * 2.3 * h * Math.Pow(Gravity * h, -.3), 2.5);
                    double vCap = RunFractionOfMax * Math.Min(vMax, vFroude);
                    vRun = Math.Max(Math.Min(MinRunMps, vCap),
                        Math.Min(performance * performance * Math.Sqrt(RunFroude * Gravity * h), vCap));
                    vWalk = Math.Min(WalkFroudeRoot * Math.Sqrt(Gravity * h) * Math.Sqrt(performance),
                        Math.Min(WalkFractionOfMax * vMax, WalkFractionOfRun * vRun));
                }
            }
            else if (mode == "slide")
            {
                double travel = block.travel_per_cycle_m;
                cadence = Clamp(block.cadence_max_hz * performance, CadenceMin, CadenceMax);
                vMax = travel * cadence;
                vRun = Math.Min(travel * SlideCadenceRun * performance * performance, vMax);
                vWalk = Math.Min(travel * SlideCadenceWalk * performance,
                    Math.Min(WalkFractionOfMax * vMax, WalkFractionOfRun * vRun));
            }
            else
            {
                cadence = block.cadence_max_hz;
                vWalk = block.v_walk_mps; vRun = block.v_run_mps; vMax = block.v_max_mps;
            }

            block.cadence_max_hz = cadence;
            block.v_walk_mps = vWalk; block.v_run_mps = vRun; block.v_max_mps = vMax;
            block.move_speed_mps = vRun;
            block.build = new LocomotionBuild
            {
                model = Model, mass_kg = total, com_m = com, muscle_fraction = muscle,
                load_imbalance = imbalance, arm_fraction = arm, performance = performance,
            };
            return block;
        }

        static double Hypot(double x, double z) => Math.Sqrt(x * x + z * z);
    }
}
