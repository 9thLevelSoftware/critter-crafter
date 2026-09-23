using System.Collections.Generic;

namespace CritterCrafter
{
    /// <summary>SplitMix64 exactly as docs/generator.md specifies (bit-identical to recipes/rng.py).</summary>
    public sealed class CritterRng
    {
        ulong _state;

        public CritterRng(long seed)
        {
            _state = unchecked((ulong)seed);
            if (_state == 0) _state = 1;
        }

        public ulong Next()
        {
            unchecked
            {
                _state += 0x9E3779B97F4A7C15UL;
                ulong z = _state;
                z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9UL;
                z = (z ^ (z >> 27)) * 0x94D049BB133111EBUL;
                return z ^ (z >> 31);
            }
        }

        public int Below(int n)
        {
            if (n <= 0) throw new System.ArgumentOutOfRangeException(nameof(n));
            return (int)((Next() >> 33) % (ulong)n);
        }

        public T Pick<T>(IList<T> items) => items[Below(items.Count)];

        public bool Roll(int pct) => Below(100) < pct;
    }
}
