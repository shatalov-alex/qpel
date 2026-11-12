"""Shared by the tests: optional capabilities that skip by default and can be made required.

QPEL_REQUIRE is a comma-separated list of capabilities whose absence must fail the run
instead of skipping: `gpu` (Apple GPU rows), `nvidia` (GPGPU-Sim rows), `metal` (the Metal
toolchain). check_all.py sets it for the steps it was asked to run (--gpu, --nvidia).
QPEL_NVIDIA=1 turns the GPGPU-Sim rows on; they are slow, so a plain run leaves them out.
"""
import os

REQUIRED = {item.strip() for item in os.environ.get('QPEL_REQUIRE', '').split(',') if item.strip()}
NVIDIA = os.environ.get('QPEL_NVIDIA') == '1' or 'nvidia' in REQUIRED


def unavailable(case, capability, reason):
    """Skip `case` for a missing capability, or fail it when the capability is required."""
    if capability in REQUIRED:
        case.fail(f'{reason}, but QPEL_REQUIRE includes {capability}')
    case.skipTest(reason)
