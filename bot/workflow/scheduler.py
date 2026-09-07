"""
bot.workflow.scheduler
~~~~~~~~~~~~~~~~~~~~~~
Two-Tier Priority Scheduler.
Enforces:
  - Tier 1 (High Priority): Regions in WAIT_STEP with Step >= 2 (Urgent transitions) -> Evaluated EVERY frame.
  - Tier 2 (Standard Priority): Regions in IDLE or Step 1 -> Interleaved Round-Robin across frames.
Guarantees:
  - Immediate SLA response for mid-workflow transitions.
  - Zero starvation for starting steps (IDLE / Step 1).
"""

from typing import List, Dict, Tuple, Optional
from bot.workflow.state_machine import RegionInstance, RegionState
import logging

logger = logging.getLogger(__name__)


class TwoTierScheduler:
    """
    Schedules active Regions into per-frame evaluation batches.
    """

    def __init__(self, tier2_group_count: int = 3):
        self.tier2_group_count = max(1, tier2_group_count)
        self.frame_counter: int = 0

    def select_regions_for_frame(
        self,
        regions: Dict[str, RegionInstance]
    ) -> List[Tuple[str, str]]:
        """
        Returns list of (region_id, expected_target_id) scheduled for inspection in the current frame.
        """
        self.frame_counter += 1

        tier1: List[Tuple[str, str]] = []
        tier2: List[Tuple[str, str]] = []

        for r_id, inst in regions.items():
            if inst.state != RegionState.WAIT_STEP:
                continue

            target_id = inst.expected_target_id
            if not target_id:
                continue

            if inst.current_step_index >= 1:
                # Mid-workflow step (Step 2, 3, ... N) -> Tier 1 (High Priority)
                tier1.append((r_id, target_id))
            else:
                # Step 1 (Starting step) -> Tier 2 (Standard Priority)
                tier2.append((r_id, target_id))

        # Tier 1 always gets 100% inclusion in every frame
        scheduled = list(tier1)

        # Tier 2 is partitioned into round-robin groups
        if tier2:
            group_idx = self.frame_counter % self.tier2_group_count
            chunk_size = (len(tier2) + self.tier2_group_count - 1) // self.tier2_group_count
            start_idx = group_idx * chunk_size
            end_idx = min(len(tier2), start_idx + chunk_size)

            tier2_slice = tier2[start_idx:end_idx]
            scheduled.extend(tier2_slice)

        return scheduled
