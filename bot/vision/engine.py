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
from typing import List, Tuple, Dict, Optional, Union
from bot.core.models import DecisionResult, DecisionClass, Target, CalibrationProfile
from bot.core.coordinates import Rect
from bot.vision.geometry import GeometryVerifier
from bot.vision.proposal import CandidateProposalEngine, CandidateProposal, nms
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
        target_reference_img: Union[np.ndarray, List[np.ndarray]],
        alternative_targets: Optional[Dict[str, Union[np.ndarray, List[np.ndarray]]]] = None
    ) -> List[DecisionResult]:
        """
        Evaluates candidate proposals against the target using the Tri-Condition Authority.
        Supports multi-reference targets (U05).
        """
        if not candidates:
            return []

        calibration = target.calibration
        if calibration is None:
            raise ValueError(f"Target '{target.target_id}' does not have a valid calibration profile.")

        # Invariant Guard #2: Check that calibration matches active model, precision, and dataset content (U06)
        curr_hash = target.compute_content_hash() if hasattr(target, "compute_content_hash") else None
        if not calibration.is_valid_for(
            self.onnx_verifier.model_sha256,
            self.onnx_verifier.precision,
            self.canonical_size,
            target_content_hash=curr_hash
        ):
            raise ValueError(
                f"CALIBRATION_INVALID: Target '{target.target_id}' was calibrated with a different model, precision, or modified dataset content. "
                f"Expected model SHA-256 {calibration.model_sha256[:8]}, active is {self.onnx_verifier.model_sha256[:8]}. Recalibration required."
            )

        # Normalize reference images to List[np.ndarray] (U05)
        if isinstance(target_reference_img, list):
            target_refs = [img for img in target_reference_img if img is not None]
        elif target_reference_img is not None:
            target_refs = [target_reference_img]
        else:
            target_refs = []

        if not target_refs:
            raise ValueError(f"Target '{target.target_id}' has no valid reference images.")

        # Ensure target embedding is cached with content validation across all references (U05)
        target_emb = self.onnx_verifier.get_cached_target_embedding(target.target_id, target_refs)
        if target_emb is None:
            self.onnx_verifier.cache_target_embedding(target.target_id, target_refs)
            target_emb = self.onnx_verifier.get_cached_target_embedding(target.target_id, target_refs)

        # Cache alternative identity embeddings with content validation
        alt_embs: Dict[str, np.ndarray] = {}
        if alternative_targets:
            for alt_id, alt_val in alternative_targets.items():
                if isinstance(alt_val, list):
                    alt_imgs = [img for img in alt_val if img is not None]
                elif alt_val is not None:
                    alt_imgs = [alt_val]
                else:
                    alt_imgs = []
                if not alt_imgs:
                    continue
                a_emb = self.onnx_verifier.get_cached_target_embedding(alt_id, alt_imgs)
                if a_emb is None:
                    self.onnx_verifier.cache_target_embedding(alt_id, alt_imgs)
                    a_emb = self.onnx_verifier.get_cached_target_embedding(alt_id, alt_imgs)
                if a_emb is not None:
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

            # --- GATE 1: Geometry / Symbol Topology Gate (U05: across all references) ---
            g_score = max(self.geo_verifier.compute_geometry_score(crop, ref) for ref in target_refs)
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

    def test_target_on_frame(
        self,
        frame: np.ndarray,
        target: Target,
        alternative_targets: Optional[Dict[str, Union[np.ndarray, List[np.ndarray]]]] = None,
        region_rect: Optional[Rect] = None
    ) -> List[DecisionResult]:
        """
        Tests a target against an offline frame or live capture frame (FC-06, U05).
        Returns list of DecisionResult reports for all candidate proposals.
        Guarantees ZERO action dispatch.
        """
        import cv2
        import os
        t_start = time.perf_counter()
        if not target.reference_image_paths:
            return []

        ref_imgs = []
        for p in target.reference_image_paths:
            img = cv2.imread(p)
            if img is not None:
                ref_imgs.append(img)
        if not ref_imgs:
            return []

        h_f, w_f = frame.shape[:2]
        if region_rect is None:
            region_rect = Rect(0, 0, w_f, h_f)

        rx1 = max(0, min(region_rect.x, w_f))
        ry1 = max(0, min(region_rect.y, h_f))
        rx2 = max(rx1, min(region_rect.right, w_f))
        ry2 = max(ry1, min(region_rect.bottom, h_f))

        if rx2 - rx1 < 4 or ry2 - ry1 < 4:
            return []

        region_crop = frame[ry1:ry2, rx1:rx2]
        proposals = []
        for ref_img in ref_imgs:
            sub_props = self.proposal_engine.generate_proposals_for_region(
                region_crop, region_rect, ref_img, region_id="test_region"
            )
            proposals.extend(sub_props)
        if len(ref_imgs) > 1:
            proposals = nms(proposals, iou_threshold=0.35)

        if not proposals:
            return []

        target_to_eval = target
        if target.calibration is None:
            # Temporary non-production calibration for offline inspection
            target_to_eval = target.model_copy()
            target_to_eval.calibration = CalibrationProfile(
                model_sha256=self.onnx_verifier.model_sha256,
                precision=self.onnx_verifier.precision,
                canonical_size=self.canonical_size,
                t_g=0.5,
                t_e=0.65,
                m_safe=0.05,
                target_content_hash=target.compute_content_hash()
            )

        decisions = self.evaluate_candidates(
            frame, proposals, target_to_eval, ref_imgs, alternative_targets
        )
        t_total_ms = (time.perf_counter() - t_start) * 1000.0
        for d in decisions:
            d.latency_ms = t_total_ms
        return decisions

    def clear_target_cache(self):
        """Invalidates all cached target embeddings."""
        self.onnx_verifier.clear_target_cache()
