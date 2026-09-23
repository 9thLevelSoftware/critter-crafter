using System;

namespace CritterCrafter.Locomotion
{
    /// <summary>Gait quantities for one ground speed (see <see cref="StepPlanner.Params"/>).</summary>
    public struct GaitParams
    {
        /// <summary>0 = walk, 1 = run.</summary>
        public double weight;
        public double duty;
        public double strideM;
        public double cadenceHz;
        public bool run;
        /// <summary>The requested speed needs more than cadence_max; feet will slide.</summary>
        public bool overspeed;
    }

    /// <summary>
    /// Double-precision port of src/critter_crafter/locomotion/stepper.py. Pure and Unity-free so the
    /// golden tests can compare it with the Python reference. All distances are metres, times seconds.
    /// </summary>
    public static class StepPlanner
    {
        public const double G = 9.81;
        public const double StrokeFraction = 0.9;
        public const double EpsilonSpeed = 1e-4;
        public const double WalkRunSwitch = 0.5;

        public static double Smoothstep(double x)
        {
            x = Math.Min(1.0, Math.Max(0.0, x));
            return x * x * (3.0 - 2.0 * x);
        }

        public static double GaitWeight(LocomotionData block, double speed)
        {
            if (block.v_run_mps <= block.v_walk_mps) return speed <= block.v_walk_mps ? 0.0 : 1.0;
            return Smoothstep((speed - block.v_walk_mps) / (block.v_run_mps - block.v_walk_mps));
        }

        public static GaitParams Params(LocomotionData block, double speed)
        {
            double w = GaitWeight(block, speed);
            var p = new GaitParams
            {
                weight = w,
                duty = block.duty_walk + (block.duty_run - block.duty_walk) * w,
                run = w >= WalkRunSwitch,
            };
            if (speed <= EpsilonSpeed)
            {
                p.weight = 0.0;
                p.run = false;
                return p;
            }
            double h = block.hip_height_m;
            if (h <= 0.0 || block.usable_stroke_m <= 0.0)
            {
                p.overspeed = true;
                return p;
            }
            double strideMax = StrokeFraction * block.usable_stroke_m / p.duty;
            double stride;
            if (block.gait == "drag")
            {
                // Hauling: every pull uses the whole reach; speed changes the pull rate, not its length.
                stride = strideMax;
            }
            else
            {
                double froude = speed * speed / (G * h);
                stride = Math.Min(h * 2.3 * Math.Pow(froude, 0.3), strideMax);
            }
            double cadence = stride > 0.0 ? speed / stride : block.cadence_max_hz;
            p.overspeed = cadence > block.cadence_max_hz;
            if (p.overspeed) cadence = block.cadence_max_hz;
            p.strideM = stride;
            p.cadenceHz = cadence;
            return p;
        }

        /// <summary>Undulation rate for a sliding body: one cycle per travel_per_cycle_m, capped at cadence_max.</summary>
        public static GaitParams SlideParams(LocomotionData block, double speed)
        {
            var p = new GaitParams { weight = GaitWeight(block, speed) };
            if (speed <= EpsilonSpeed || block.travel_per_cycle_m <= 0.0)
            {
                p.weight = 0.0;
                return p;
            }
            double cadence = speed / block.travel_per_cycle_m;
            p.overspeed = cadence > block.cadence_max_hz;
            p.cadenceHz = Math.Min(cadence, block.cadence_max_hz);
            p.strideM = block.travel_per_cycle_m;
            p.run = p.weight >= WalkRunSwitch;
            return p;
        }

        public static double LegOffset(LocomotionLeg leg, bool run) => run ? leg.run_phase : leg.walk_phase;

        public static double LegPhase(double clock, double offset)
        {
            double p = (clock + offset) % 1.0;
            return p < 0.0 ? p + 1.0 : p;
        }

        public static bool InStance(double phase, double duty) => phase < duty;

        /// <summary>Distance ahead of home to land so the foot passes under home mid-stance.</summary>
        public static double LandingLead(double speed, double cadence, double duty) =>
            cadence <= 0.0 ? 0.0 : speed * duty / cadence * 0.5;

        public static double SwingTime(double cadence, double duty) =>
            cadence > 0.0 ? (1.0 - duty) / cadence : 0.0;

        /// <summary>Landing point in the creature's local (catalog) frame at touchdown, flat ground.</summary>
        public static double[] LandingTargetLocal(LocomotionLeg leg, double speed, double cadence, double duty)
        {
            double lead = LandingLead(speed, cadence, duty);
            return new[] { leg.home_m[0], leg.home_m[1], leg.home_m[2] + leg.stance_shift_m + lead };
        }

        /// <summary>Swing trajectory: smoothstep interpolation plus a sin^2 lift of <paramref name="clearance"/>.</summary>
        public static double[] SwingPoint(double[] start, double[] end, double u, double clearance)
        {
            double s = Smoothstep(u);
            double k = Math.Sin(Math.PI * Math.Min(1.0, Math.Max(0.0, u)));
            double lift = clearance * k * k;
            return new[]
            {
                start[0] + (end[0] - start[0]) * s,
                start[1] + (end[1] - start[1]) * s + lift,
                start[2] + (end[2] - start[2]) * s,
            };
        }

        public static int SupportCount(LocomotionData block, double clock, double duty, bool run)
        {
            int n = 0;
            foreach (var leg in block.legs)
                if (leg.support && InStance(LegPhase(clock, LegOffset(leg, run)), duty)) n++;
            return n;
        }
    }
}
