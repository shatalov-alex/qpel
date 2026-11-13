"""Compare-and-swap under contention on the Apple GPU: TRELLIS.2's hash insert with keys that collide.

The behaviour rows give insertion keys in distinct slots, so the table is the same in any thread
order and can be compared exactly. Here many threads probe the same slots at once (and some
insert the same key), so qpel's Metal retry loop over the weak exchange really retries. The
table then depends on the order, so it is checked for what any order must give: every key
exactly once, reachable by linear probing from its hash before an empty slot, holding one of
the values inserted with it, and nothing else in the table.
"""
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import unavailable  # noqa: E402


def check_table(keys, values, table_keys, table_values, murmur_slot, bits, empty):
    import numpy as np
    """The first broken rule of a hash table built from (keys, values), or None."""
    inserted = {}
    for k, v in zip(keys.tolist(), values.tolist()):
        inserted.setdefault(k, set()).add(v)
    stored = [k for k in table_keys.tolist() if k != empty]
    if sorted(stored) != sorted(inserted):
        return f'{len(stored)} keys stored ({len(set(stored))} distinct) for {len(inserted)} inserted'
    slots = len(table_keys)
    for k, allowed in inserted.items():
        slot = int(murmur_slot(np.array([k], keys.dtype), slots, bits)[0])
        for _ in range(slots):
            if table_keys[slot] == k:
                break
            if table_keys[slot] == empty:
                return f'key {k} is not reachable from its slot'
            slot = (slot + 1) % slots
        if int(table_values[slot]) not in allowed:
            return f'key {k} holds {int(table_values[slot])}, not a value inserted with it'
    return None


class Contention(unittest.TestCase):
    FIXTURES = ('trellis2_hashmap_insert_u32_u32', 'trellis2_hashmap_insert_u32_u64')

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-cas-')

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def setUp(self):
        try:
            import numpy  # noqa: F401
            import torch
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch and NumPy required for the Apple GPU tests')
        if not torch.backends.mps.is_available():
            unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')

    def build(self, name):
        from differential import matrix
        source = next(s for s in matrix.sources() if s[0] == name)
        name, path, dialect, plan, include, _, options = source
        work = Path(self.work.name)/name
        work.mkdir()
        prepared = matrix.Prepared(name, path, dialect, plan, include, options, work)
        return prepared.plan, matrix.load_host(prepared.generated)

    def test_colliding_inserts_build_a_valid_table(self):
        import numpy as np
        from differential import metal
        from differential.cases import murmur_slot
        from differential.simulate import Case, DTYPES
        for name in self.FIXTURES:
            plan, host = self.build(name)
            types = {r['name']: DTYPES[r['type']] for r in plan['resources'] if r['kind'] not in ('shape', 'value')}
            key_type, value_type = types['hashmap_keys'], types['hashmap_values']
            empty = np.iinfo(key_type).max
            rng = np.random.default_rng(7)
            for slots, count, distinct in [(4096, 4000, 4000), (1024, 4096, 600), (257, 20000, 256), (64, 50000, 3)]:
                pool = rng.choice(2**31, distinct, replace=False).astype(key_type)  # never the empty marker
                keys = pool[rng.integers(0, distinct, count)]  # repeats: several threads insert one key
                values = rng.integers(0, np.iinfo(value_type).max, count, dtype=value_type)
                case = Case({'N': (slots, 1, 1), 'M': (count, 1, 1)},
                            {'hashmap_keys': np.full(slots, empty, key_type), 'hashmap_values': np.zeros(slots, value_type),
                             'keys': keys, 'values': values}, {}, f'{slots} slots')
                for attempt in range(3):  # scheduling differs between runs; each must be valid
                    out = metal.run_generated(host, plan, case)
                    error = check_table(keys, values, out['hashmap_keys'], out['hashmap_values'], murmur_slot,
                                        8 * np.dtype(key_type).itemsize, empty)
                    self.assertIsNone(error, f'{name}, {slots} slots, {count} inserts of {distinct} keys, run {attempt}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
