"""
bot.workflow.ledger
~~~~~~~~~~~~~~~~~~~
In-Session Durable Ledger and Table Association Engine.
Enforces:
  - Per-table ownership: Candidate must fall inside region bounds.
  - Generational separation: Past rounds do not contaminate current execution.
  - Zero cross-region association.
"""

from typing import Dict, Optional, List, Tuple
from bot.core.models import RegionModel, Workflow
from bot.core.coordinates import Rect
from bot.workflow.state_machine import RegionInstance, RegionState
import logging

logger = logging.getLogger(__name__)


class SessionLedger:
    """
    Manages active region states and candidate ownership association.
    """

    def __init__(self):
        self._regions_config: Dict[str, RegionModel] = {}
        self._instances: Dict[str, RegionInstance] = {}

    def register_region(self, region: RegionModel, workflow: Workflow) -> RegionInstance:
        """Registers a region with its assigned workflow."""
        self._regions_config[region.region_id] = region
        inst = RegionInstance(region_id=region.region_id, workflow=workflow)
        self._instances[region.region_id] = inst
        return inst

    def get_instance(self, region_id: str) -> Optional[RegionInstance]:
        return self._instances.get(region_id)

    def get_all_instances(self) -> Dict[str, RegionInstance]:
        return self._instances

    def associate_candidate(
        self,
        screen_x: int,
        screen_y: int,
        target_id: str,
        candidate_region_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Associates a detected screen coordinate (screen_x, screen_y) with its owner Region.
        Enforces strict fail-closed identity:
        - If candidate_region_id is specified: only validates that exact region. Never falls back.
        - If candidate_region_id is None: searches active regions; rejects if ambiguous (>1 overlap).
        """
        # Strict explicit identity branch
        if candidate_region_id is not None:
            cfg = self._regions_config.get(candidate_region_id)
            if not cfg:
                logger.debug(f"Candidate region '{candidate_region_id}' does not exist in ledger")
                return None

            r_rect = Rect(cfg.x, cfg.y, cfg.w, cfg.h)
            if not r_rect.contains(screen_x, screen_y):
                logger.debug(f"Coordinate ({screen_x}, {screen_y}) is outside Region '{candidate_region_id}' rect")
                return None

            inst = self._instances.get(candidate_region_id)
            if not inst or inst.state != RegionState.WAIT_STEP or inst.expected_target_id != target_id:
                logger.debug(f"Region '{candidate_region_id}' state is not WAIT_STEP for target '{target_id}'")
                return None

            return candidate_region_id

        # Global unassigned scan branch: reject if ambiguous
        matching_regions = []
        for r_id, cfg in self._regions_config.items():
            r_rect = Rect(cfg.x, cfg.y, cfg.w, cfg.h)
            if r_rect.contains(screen_x, screen_y):
                inst = self._instances.get(r_id)
                if inst and inst.state == RegionState.WAIT_STEP and inst.expected_target_id == target_id:
                    matching_regions.append(r_id)

        if len(matching_regions) == 1:
            return matching_regions[0]
        elif len(matching_regions) > 1:
            logger.warning(f"Ambiguity conflict: candidate at ({screen_x}, {screen_y}) overlaps multiple regions: {matching_regions} -> REJECT")
            return None

        return None

    def advance_region(self, region_id: str):
        inst = self._instances.get(region_id)
        if inst:
            inst.advance_step()

    def timeout_stuck_regions(self, now: float) -> List[str]:
        timed_out = []
        for r_id, inst in self._instances.items():
            if inst.check_timeout(now):
                timed_out.append(r_id)
        return timed_out
