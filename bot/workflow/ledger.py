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
        target_id: str
    ) -> Optional[str]:
        """
        Associates a detected screen coordinate (screen_x, screen_y) with its owner Region.
        Candidate must strictly lie inside the region's physical rect and the region
        must be waiting for this exact target_id.
        """
        for r_id, cfg in self._regions_config.items():
            r_rect = Rect(cfg.x, cfg.y, cfg.w, cfg.h)
            if r_rect.contains(screen_x, screen_y):
                inst = self._instances.get(r_id)
                if inst and inst.state == RegionState.WAIT_STEP and inst.expected_target_id == target_id:
                    return r_id
                else:
                    logger.debug(f"Candidate at ({screen_x}, {screen_y}) is inside Region {r_id}, but region state is {inst.state if inst else 'NONE'}")
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
