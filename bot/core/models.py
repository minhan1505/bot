"""
bot.core.models
~~~~~~~~~~~~~~~
Pydantic / dataclass schemas for Target, Region, Workflow, Calibration, and Decision.
Enforces strict schema validation, type safety, and zero hidden assumptions.
"""

from enum import Enum
from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel, Field
import time


class DecisionClass(str, Enum):
    MATCH = "MATCH"
    NON_MATCH = "NON_MATCH"
    UNKNOWN = "UNKNOWN"


class ActionType(str, Enum):
    CLICK = "CLICK"
    DOUBLE_CLICK = "DOUBLE_CLICK"
    DETECT_ONLY = "DETECT_ONLY"


class CalibrationProfile(BaseModel):
    """
    Tightly bounds threshold parameters to the exact model artifact and preprocessing.
    If model_sha256, precision, canonical_size, or preprocessing_version changes,
    this profile is rendered INVALID and recalibration is required.
    """
    model_sha256: str
    precision: str = "FP32"  # 'FP32' or 'INT8'
    canonical_size: Tuple[int, int] = (64, 64)
    preprocessing_version: str = "v2.3_canonical_letterbox"
    t_g: float = Field(..., description="Geometry threshold")
    t_e: float = Field(..., description="Embedding similarity threshold")
    m_safe: float = Field(..., description="Alternative identity margin threshold")
    min_pos_sim: float = 0.0
    max_neg_sim: float = 0.0
    separation_gap: float = 0.0
    sample_count_pos: int = 0
    sample_count_neg: int = 0
    target_content_hash: Optional[str] = None
    calibrated_at: float = Field(default_factory=time.time)

    def is_valid_for(
        self,
        model_sha256: str,
        precision: str,
        canonical_size: Tuple[int, int],
        preprocessing_version: str = "v2.3_canonical_letterbox",
        target_content_hash: Optional[str] = None
    ) -> bool:
        base_valid = (
            self.model_sha256 == model_sha256
            and self.precision == precision
            and self.canonical_size == canonical_size
            and self.preprocessing_version == preprocessing_version
        )
        if not base_valid:
            return False
        if self.target_content_hash is not None and target_content_hash is not None:
            return self.target_content_hash == target_content_hash
        return True


class Target(BaseModel):
    """
    Dynamic User Target.
    Contains no hardcoded class semantics.
    """
    target_id: str
    name: str
    reference_image_paths: List[str] = Field(default_factory=list)
    confuser_image_paths: List[str] = Field(default_factory=list)
    enabled: bool = True
    calibration: Optional[CalibrationProfile] = None
    created_at: float = Field(default_factory=time.time)

    def compute_content_hash(self) -> str:
        """Computes deterministic, path-independent SHA-256 hash across target reference and confuser image contents (W05)."""
        import hashlib
        import os
        h = hashlib.sha256()

        ref_hashes = []
        for p in self.reference_image_paths:
            if os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        ref_hashes.append(hashlib.sha256(f.read()).hexdigest())
                except Exception:
                    pass
        for r_hash in sorted(ref_hashes):
            h.update(f"ref:{r_hash}".encode("utf-8"))

        conf_hashes = []
        for p in self.confuser_image_paths:
            if os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        conf_hashes.append(hashlib.sha256(f.read()).hexdigest())
                except Exception:
                    pass
        for c_hash in sorted(conf_hashes):
            h.update(f"conf:{c_hash}".encode("utf-8"))

        return h.hexdigest()


class RegionModel(BaseModel):
    """
    Generic Region / Table representation (1..N).
    Supports arbitrary layouts, grids, or floating windows.
    """
    region_id: str
    name: str
    x: int
    y: int
    w: int
    h: int
    enabled: bool = True
    workflow_id: Optional[str] = None


SCHEMA_VERSION: int = 2


class SafetyConfig(BaseModel):
    """
    Canonical anti-runaway and quota safety bounds.
    """
    max_clicks_per_second: float = 10.0
    circuit_breaker_threshold: int = 30
    circuit_breaker_window_sec: float = 5.0
    max_clicks_per_region: int = 100
    max_total_clicks: int = 1000
    auto_stop_minutes: float = 0.0


class WorkflowStep(BaseModel):
    """
    A single step in a 1..N dynamic workflow sequence.
    """
    step_index: int
    target_id: str
    action_type: ActionType = ActionType.CLICK
    timeout_ms: int = 5000
    cooldown_ms: int = 500
    retry_limit: int = 3


class Workflow(BaseModel):
    """
    Dynamic 1..N Workflow configured by the user.
    """
    workflow_id: str
    name: str
    steps: List[WorkflowStep] = Field(default_factory=list)


class Profile(BaseModel):
    """
    Independent profile encapsulating monitor, ROI, regions, targets, and workflows.
    """
    profile_id: str
    name: str
    schema_version: int = SCHEMA_VERSION
    emergency_hotkey: str = "F12"
    monitor_index: int = 0
    roi: Optional[Tuple[int, int, int, int]] = None # (x, y, w, h)
    regions: Dict[str, RegionModel] = Field(default_factory=dict)
    targets: Dict[str, Target] = Field(default_factory=dict)
    workflows: Dict[str, Workflow] = Field(default_factory=dict)
    safety_config: SafetyConfig = Field(default_factory=SafetyConfig)
    scan_interval_ms: int = 16 # ~60 FPS
    created_at: float = Field(default_factory=time.time)


class DecisionResult(BaseModel):
    """
    Per-candidate decision audit report.
    Logs every score, threshold check, and gate pass/fail reason.
    """
    target_id: Optional[str] = None
    region_id: Optional[str] = None
    workflow_id: Optional[str] = None
    generation: int = 0
    candidate_rect: Tuple[int, int, int, int] # (x, y, w, h)
    geometry_score: float
    geometry_pass: bool
    embedding_similarity: float
    embedding_pass: bool
    identity_margin: float
    margin_pass: bool
    decision: DecisionClass
    reason: str
    capture_timestamp: float = 0.0
    dispatch_timestamp: float = 0.0
    latency_ms: float = 0.0
    timestamp: float = Field(default_factory=time.time)
