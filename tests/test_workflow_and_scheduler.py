"""
tests/test_workflow_and_scheduler.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for Generic Region State Machine, Two-Tier Scheduler, and Session Ledger.
"""

import pytest
from bot.core.models import Workflow, WorkflowStep, RegionModel
from bot.core.coordinates import Rect
from bot.workflow.state_machine import RegionInstance, RegionState
from bot.workflow.scheduler import TwoTierScheduler
from bot.workflow.ledger import SessionLedger


def make_test_workflow(n_steps=3) -> Workflow:
    steps = [WorkflowStep(step_index=i, target_id=f"target_{i+1}") for i in range(n_steps)]
    return Workflow(workflow_id="wf_test", name="Test Workflow", steps=steps)


def test_region_state_machine_advances_through_n_steps():
    wf = make_test_workflow(3)
    inst = RegionInstance(region_id="r1", workflow=wf)

    assert inst.state == RegionState.IDLE

    # Start
    inst.start_workflow()
    assert inst.state == RegionState.WAIT_STEP
    assert inst.expected_target_id == "target_1"

    # Step 1: Detect -> Verify -> Action -> Advance
    inst.on_target_detected("target_1", (100, 100))
    assert inst.state == RegionState.TARGET_DETECTED
    inst.on_fresh_verified()
    assert inst.state == RegionState.VERIFIED
    inst.on_action_dispatched()
    assert inst.state == RegionState.ACTION_PENDING

    inst.advance_step()
    # Should now be on Step 2
    assert inst.state == RegionState.WAIT_STEP
    assert inst.current_step_index == 1
    assert inst.expected_target_id == "target_2"

    # Step 2: Detect -> Verify -> Action -> Advance
    inst.on_target_detected("target_2", (100, 100))
    inst.on_fresh_verified()
    inst.on_action_dispatched()
    inst.advance_step()

    # Step 3: Advance -> DONE
    assert inst.current_step_index == 2
    assert inst.expected_target_id == "target_3"
    inst.on_target_detected("target_3", (100, 100))
    inst.on_fresh_verified()
    inst.on_action_dispatched()
    inst.advance_step()

    assert inst.state == RegionState.DONE


def test_two_tier_scheduler_prioritizes_mid_workflow_steps():
    wf = make_test_workflow(3)
    scheduler = TwoTierScheduler(tier2_group_count=3)

    # Create 19 regions: 4 in Step 2 (Tier 1), 15 in Step 1 (Tier 2)
    regions = {}
    for i in range(19):
        r_id = f"region_{i+1}"
        inst = RegionInstance(region_id=r_id, workflow=wf)
        inst.start_workflow()
        if i < 4:
            # Advance to Step 2 (Tier 1)
            inst.advance_step()
        regions[r_id] = inst

    # Frame 1
    scheduled_f1 = scheduler.select_regions_for_frame(regions)
    scheduled_f1_ids = [r[0] for r in scheduled_f1]

    # All 4 Tier 1 regions MUST be included
    for i in range(4):
        assert f"region_{i+1}" in scheduled_f1_ids

    # 15 Tier 2 regions divided across 3 frames -> 5 regions per frame
    assert len(scheduled_f1) == 4 + 5 # 9 regions

    # Frame 2
    scheduled_f2 = scheduler.select_regions_for_frame(regions)
    scheduled_f2_ids = [r[0] for r in scheduled_f2]
    for i in range(4):
        assert f"region_{i+1}" in scheduled_f2_ids

    # Over 3 consecutive frames, ALL 19 regions must be serviced (zero starvation)
    scheduled_f3 = scheduler.select_regions_for_frame(regions)
    all_serviced = set(scheduled_f1_ids) | set(scheduled_f2_ids) | set([r[0] for r in scheduled_f3])
    assert len(all_serviced) == 19


def test_session_ledger_ownership_isolation():
    ledger = SessionLedger()
    wf = make_test_workflow(1)

    r1_cfg = RegionModel(region_id="r1", name="Region 1", x=0, y=0, w=100, h=100)
    r2_cfg = RegionModel(region_id="r2", name="Region 2", x=150, y=0, w=100, h=100)

    inst1 = ledger.register_region(r1_cfg, wf)
    inst2 = ledger.register_region(r2_cfg, wf)

    inst1.start_workflow()
    inst2.start_workflow()

    # Candidate at (50, 50) belongs strictly to Region 1
    owner = ledger.associate_candidate(50, 50, "target_1")
    assert owner == "r1"

    # Candidate at (180, 50) belongs strictly to Region 2
    owner2 = ledger.associate_candidate(180, 50, "target_1")
    assert owner2 == "r2"

    # Candidate at (120, 50) (gap between regions) belongs to NO ONE
    assert ledger.associate_candidate(120, 50, "target_1") is None
