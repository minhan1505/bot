"""
tests/test_hardware_sla_harness.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Regression test verifying the SLA Hardware Acceptance Harness.
Executes the live BotRuntimeRunner thread across 19 concurrent active regions.
Enforces:
  - max_latency <= 700.0 ms
  - count_gt_700ms == 0
  - passed_hard_sla == True
"""

import pytest
from bot.telemetry.hardware_sla_harness import HardwareSLAAcceptanceHarness


def test_hardware_sla_acceptance_harness_19_regions():
    """
    Verifies that the live BotRuntimeRunner thread executing across 19 active regions
    satisfies the strict <= 700ms hard SLA with 0 violations.
    """
    harness = HardwareSLAAcceptanceHarness(n_regions=19, sample_iterations=5)
    report = harness.run_benchmark()

    assert report.n_regions == 19
    assert report.n_samples == 5
    assert report.count_gt_700ms == 0, f"Hard SLA Violated: {report.count_gt_700ms} samples exceeded 700ms"
    assert report.max_latency_ms <= 700.0, f"Worst-case latency ({report.max_latency_ms:.2f}ms) exceeded 700ms"
    assert report.passed_hard_sla is True
