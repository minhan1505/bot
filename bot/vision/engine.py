"""
bot.vision.engine
~~~~~~~~~~~~~~~~~
Core Vision Decision Engine V2.
Orchestrates the Tri-Condition Decision Contract:
  MATCH = GeometryPass (G >= T_g)
          AND EmbeddingPass (E >= T_e)
          AND MarginPass (Margin >= M_safe)

Enforces Hard Invariant:
  - If Geometry fails, candidate is REJECTED / UNKNOWN.
  - Color, brightness, and Embedding are STRICTLY PROHIBITED from rescuing a geometry failure.
"""

import time
import numpy as np
from typing import List, Tuple, Dict, Optional
from bot.core.models import DecisionResult, DecisionClass, Target, CalibrationProfile
from bot.core.coordinates import Rect
from bot.vision.geometry import GeometryVerifier
from bot.vision.proposal import CandidateProposalEngine, CandidateProposal
from bot.vision.onnx_verifier import ONNXVerifier
import logging

logger = logging.getLogger(__name__)


class VisionEngine:
    """
    Unified Vision Engine implementing Tri-Condition Verification.
    """

    def __init__(
        self,
        onnx_verifier: ONNXVerifier,
        geometry_verifier: Optional[GeometryVerifier] = None,
        proposal_engine: Optional[CandidateProposalEngine] = None,
        canonical_size: Tuple[int, int] = (64, 64)
    ):
        self.onnx_verifier = onnx_verifier
        self.canonical_size = canonical_size
        self.geo_verifier = geometry_verifier or GeometryVerifier(canonical_size=canonical_size)
        self.proposal_engine = proposal_engine or CandidateProposalEngine()

    def evaluate_candidates(
        self,
        frame: np.ndarray,
        candidates: List[CandidateProposal],
        target: Target,
        target_reference_img: np.ndarray,
        alternative_targets: Optional[Dict[str, np.ndarray]] = None
    ) -> List[DecisionResult]:
        """
        Evaluates candidate proposals against the target using the Tri-Condition Authority.
        """
        if not candidates:
            return []

        calibration = target.calibration
        if calibration is None:
            raise ValueError(f"Target '{target.target_id}' does not have a valid calibration profile.")

        # Invariant Guard #2: Check that calibration matches the exact active model and precision
        if not calibration.is_valid_for(
            self.onnx_verifier.model_sha256,
            self.onnx_verifier.precision,
            self.canonical_size
        ):
            raise ValueError(
                f"CALIBRATION_INVALID: Target '{target.target_id}' was calibrated with a different model or artifact. "
                f"Expected model SHA-256 {calibration.model_sha256[:8]}, active is {self.onnx_verifier.model_sha256[:8]}. Recalibration required."
            )

        # Ensure target embedding is cached
        target_emb = self.onnx_verifier.get_cached_target_embedding(target.target_id)
        if target_emb is None:
            self.onnx_verifier.cache_target_embedding(target.target_id, [target_reference_img])
            target_emb = self.onnx_verifier.get_cached_target_embedding(target.target_id)

        # Cache alternative identity embeddings
        alt_embs: Dict[str, np.ndarray] = {}
        if alternative_targets:
            for alt_id, alt_img in alternative_targets.items():
                a_emb = self.onnx_verifier.get_cached_target_embedding(alt_id)
                if a_emb is None:
                    self.onnx_verifier.cache_target_embedding(alt_id, [alt_img])
                    a_emb = self.onnx_verifier.get_cached_target_embedding(alt_id)
                alt_embs[alt_id] = a_emb

        # Step 1: Crop all candidate regions from frame
        h_frame, w_frame = frame.shape[:2]
        valid_crops: List[np.ndarray] = []
        valid_indices: List[int] = []

        for idx, cand in enumerate(candidates):
            rect = cand.rect
            x1 = max(0, min(rect.x, w_frame))
            y1 = max(0, min(rect.y, h_frame))
            x2 = max(x1, min(rect.right, w_frame))
            y2 = max(y1, min(rect.bottom, h_frame))

            if x2 - x1 >= 4 and y2 - y1 >= 4:
                crop = frame[y1:y2, x1:x2]
                valid_crops.append(crop)
                valid_indices.append(idx)

        if not valid_crops:
            return []

        # Step 2: Batch ONNX Inference (High throughput CPU / DirectML)
        batch_embeddings = self.onnx_verifier.compute_embeddings(valid_crops)

        results: List[DecisionResult] = []
        t_now = time.time()

        # Step 3: Tri-Condition Authority Evaluation for each candidate
        for i, crop in enumerate(valid_crops):
            cand_prop = candidates[valid_indices[i]]
            cand_emb = batch_embeddings[i]

            # --- GATE 1: Geometry / Symbol Topology Gate ---
            g_score = self.geo_verifier.compute_geometry_score(crop, target_reference_img)
            geo_pass = (g_score >= calibration.t_g)

            # --- GATE 2: Embedding Similarity Gate ---
            e_score = self.onnx_verifier.cosine_similarity(cand_emb, target_emb)
            emb_pass = (e_score >= calibration.t_e)

            # --- GATE 3: Alternative Identity Margin Gate ---
            competitor_score = -1.0
            best_competitor_id = None
            if alt_embs:
                for alt_id, a_emb in alt_embs.items():
                    sim_alt = self.onnx_verifier.cosine_similarity(cand_emb, a_emb)
                    if sim_alt > competitor_score:
                        competitor_score = sim_alt
                        best_competitor_id = alt_id

            if competitor_score > -1.0:
                identity_margin = e_score - competitor_score
            else:
                identity_margin = e_score  # If no other competitor configured, margin equals embedding score
            margin_pass = (identity_margin >= calibration.m_safe)

            # --- FINAL DECISION SYNTHESIS ---
            if geo_pass and emb_pass and margin_pass:
                decision = DecisionClass.MATCH
                reason = f"PASS_ALL_GATES (G={g_score:.3f}>={calibration.t_g:.3f}, E={e_score:.3f}>={calibration.t_e:.3f}, M={identity_margin:.3f}>={calibration.m_safe:.3f})"
            elif not geo_pass:
                # Execution Guard #1: Geometry fail -> CANNOT be rescued by embedding
                decision = DecisionClass.UNKNOWN
                reason = f"GEOMETRY_FAIL: G={g_score:.3f} < {calibration.t_g:.3f}. Structural/Symbol signature mismatch."
            elif not emb_pass:
                decision = DecisionClass.UNKNOWN
                reason = f"EMBEDDING_FAIL: E={e_score:.3f} < {calibration.t_e:.3f}. Feature vector divergence."
            elif not margin_pass:
                decision = DecisionClass.UNKNOWN
                reason = f"MARGIN_FAIL: Margin={identity_margin:.3f} < {calibration.m_safe:.3f}. Ambiguous with competitor '{best_competitor_id}' (sim={competitor_score:.3f})."
            else:
                decision = DecisionClass.UNKNOWN
                reason = "UNKNOWN_REASON"

            # Check if competitor clearly won (NON_MATCH)
            if competitor_score > e_score and competitor_score >= calibration.t_e:
                decision = DecisionClass.NON_MATCH
                reason = f"COMPETITOR_WON: '{best_competitor_id}' similarity ({competitor_score:.3f}) > target ({e_score:.3f})"

            res = DecisionResult(
                target_id=target.target_id,
                region_id=cand_prop.region_id,
                candidate_rect=(cand_prop.rect.x, cand_prop.rect.y, cand_prop.rect.w, cand_prop.rect.h),
                geometry_score=g_score,
                geometry_pass=geo_pass,
                embedding_similarity=e_score,
                embedding_pass=emb_pass,
                identity_margin=identity_margin,
                margin_pass=margin_pass,
                decision=decision,
                reason=reason,
                timestamp=t_now
            )
            results.append(res)

        return results
