"""differential/hardware.py's Kaggle runner against a fake Kaggle CLI: the run's time limit counts from
when the kernel starts, not while Kaggle keeps it queued for a T4, and a queue that never moves fails."""

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from differential import hardware  # noqa: E402

FAKE = '''#!/bin/sh
state="$(dirname "$0")/statuses"
case "$1 $2" in
  "config view") echo "- username: someone";;
  "kernels push") ;;
  "kernels status") line=$(head -1 "$state"); [ "$(wc -l < "$state")" -gt 1 ] && sed -i.bak 1d "$state"
                    echo "someone/qpel-nvidia has status \\"KernelWorkerStatus.$line\\"";;
  "kernels output") touch "$5/results.tar.gz";;
esac
'''


class FakeClock:
    """time.time and time.sleep for the runner: each poll advances the clock by its sleep."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class KaggleWait(unittest.TestCase):
    def run_with(self, statuses, timeout=100, queue_limit=1000):
        folder = Path(tempfile.mkdtemp(prefix='qpel-fake-kaggle-'))
        cli = folder/'kaggle'
        cli.write_text(FAKE)
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
        (folder/'statuses').write_text('\n'.join(statuses) + '\n')
        clock = FakeClock()
        with mock.patch.object(hardware.time, 'time', clock.time), \
                mock.patch.object(hardware.time, 'sleep', clock.sleep), \
                mock.patch.object(hardware.sys, 'stderr', open(os.devnull, 'w')):
            remote = hardware.Kaggle(str(cli), timeout=timeout, queue_limit=queue_limit)
            return remote.run('someone/bundle', folder/'out', 'id')

    def test_long_queue_does_not_count_against_the_run(self):
        # 40 polls queued (800 s, beyond timeout + 600 = 700 s counted from the push), then a short run
        result = self.run_with(['QUEUED'] * 40 + ['RUNNING'] * 3 + ['COMPLETE'])
        self.assertEqual(result.name, 'results.tar.gz')

    def test_queue_limit(self):
        with self.assertRaisesRegex(RuntimeError, 'still queued'):
            self.run_with(['QUEUED'], queue_limit=300)

    def test_run_limit_counts_from_the_start(self):
        with self.assertRaisesRegex(RuntimeError, 'still running'):
            self.run_with(['QUEUED'] * 5 + ['RUNNING'])


if __name__ == '__main__':
    unittest.main()
