"""
bot.vision.calibration
~~~~~~~~~~~~~~~~~~~~~~
Dataset Split & Data-Driven Calibration Engine.
Strictly implements:
  - Two-Set Calibration (D_calib for threshold search, D_val held-out for frozen verification).
  - Pre-Threshold Zero-Overlap Rejection on D_calib (Pre-Overlap Gate).
  - Data-Driven Threshold Midpoint calculation (Zero heuristic numbers).
  - Validation pass requiring:
      * 0 Observed False Positives on D_val
      * Separation Gap > 0
  - Produces frozen CalibrationProfile bound to exact model SHA-256 and preprocessing version.
"""

from __future__ import annotations
import numpy as np
import hashlib
from typing import List, Tuple, Dict, Optional, Union, Any
from dataclasses import dataclass
import logging
from bot.core.models import CalibrationProfile
from bot.vision.geometry import GeometryVerifier
from bot.vision.onnx_verifier import ONNXVerifier

logger = logging.getLogger(__name__)


class CalibrationOverlapError(Exception):
    """Raised when positive and negative distributions overlap on D_calib or D_val."""
    pass


@dataclass
class EvaluationSample:
    image: np.ndarray
    is_positive: bool
    label: str               # target_id or negative identifier
    session_id: str          # for group-wise split verification
    device_id: str           # device identifier
    run_id: str = ""         # capture run identifier


class CalibrationEngine:
    """
    Executes data-driven calibration following strict empirical methodology.
    """

    def __init__(
        self,
        geometry_verifier: GeometryVerifier,
        onnx_verifier: ONNXVerifier,
        canonical_size: Tuple[int, int] = (64, 64)
    ):
        self.geo_verifier = geometry_verifier
        self.onnx_verifier = onnx_verifier
        self.canonical_size = canonical_size

    def calibrate_target(
        self,
        target_id: str,
        target_reference_img: Union[np.ndarray, List[np.ndarray]],
        d_calib: List[EvaluationSample],
        d_val: List[EvaluationSample],
        alternative_identity_imgs: Optional[Dict[str, Union[np.ndarray, List[np.ndarray]]]] = None
    ) -> CalibrationProfile:
        """
        Runs rigorous calibration for a specific target.
        d_calib: Calibration set (Session A)
        d_val: Held-Out validation set (Session B)
        """
        if not d_calib:
            raise ValueError(f"Calibration set d_calib cannot be empty for target {target_id}")
        if not d_val:
            raise ValueError(f"Validation set d_val cannot be empty for target {target_id}")

        # Enforce disjoint session split between d_calib and d_val
        calib_sessions = {s.session_id for s in d_calib if s.session_id}
        val_sessions = {s.session_id for s in d_val if s.session_id}
        if calib_sessions and val_sessions and not calib_sessions.isdisjoint(val_sessions):
            overlap_sessions = calib_sessions.intersection(val_sessions)
            raise CalibrationOverlapError(
                f"DATA_LEAKAGE_DETECTED: Sessions {overlap_sessions} are present in both D_calib and D_val. "
                f"Calibration and validation sets must originate from distinct capture sessions."
            )

        # Enforce disjoint run split if run IDs provided
        calib_runs = {s.run_id for s in d_calib if s.run_id}
        val_runs = {s.run_id for s in d_val if s.run_id}
        if calib_runs and val_runs and not calib_runs.isdisjoint(val_runs):
            overlap_runs = calib_runs.intersection(val_runs)
            raise CalibrationOverlapError(
                f"DATA_LEAKAGE_DETECTED: Capture runs {overlap_runs} are present in both D_calib and D_val. "
                f"Calibration and validation sets must originate from distinct capture runs."
            )

        # Enforce non-empty competitor set for Identity Margin gate
        if not alternative_identity_imgs:
            raise CalibrationOverlapError(
                f"CALIBRATION_BLOCKED_MISSING_CONFUSERS: Target '{target_id}' has no competitor targets or confusers configured. "
                f"Identity Margin gate requires at least one competitor/confuser to establish M_safe."
            )

        # Normalize target reference images to List[np.ndarray] (W03: multi-reference alignment with runtime)
        if isinstance(target_reference_img, list):
            target_refs = [img for img in target_reference_img if img is not None]
        elif target_reference_img is not None:
            target_refs = [target_reference_img]
        else:
            target_refs = []

        if not target_refs:
            raise ValueError(f"Target '{target_id}' must have at least 1 valid reference image for calibration.")

        # Ensure target embedding is cached across all references
        self.onnx_verifier.cache_target_embedding(target_id, target_refs)
        target_emb = self.onnx_verifier.get_cached_target_embedding(target_id)

        # Cache alternative identity embeddings for margin calculation
        alt_embs: Dict[str, np.ndarray] = {}
        if alternative_identity_imgs:
            for alt_id, alt_val in alternative_identity_imgs.items():
                if isinstance(alt_val, list):
                    a_imgs = [img for img in alt_val if img is not None]
                elif alt_val is not None:
                    a_imgs = [alt_val]
                else:
                    a_imgs = []
                if a_imgs:
                    self.onnx_verifier.cache_target_embedding(alt_id, a_imgs)
                    a_emb = self.onnx_verifier.get_cached_target_embedding(alt_id)
                    if a_emb is not None:
                        alt_embs[alt_id] = a_emb

        # -------------------------------------------------------------
        # STEP 1: Compute scores on D_calib (Calibration Phase)
        # -------------------------------------------------------------
        calib_g_pos, calib_g_neg = [], []
        calib_e_pos, calib_e_neg = [], []
        calib_m_pos, calib_m_neg = [], []

        for sample in d_calib:
            # 1. Geometry Score (W03: max across all references)
            g_score = max(self.geo_verifier.compute_geometry_score(sample.image, ref) for ref in target_refs)
            # 2. Embedding Similarity
            cand_emb = self.onnx_verifier.compute_embeddings([sample.image])[0]
            e_score = self.onnx_verifier.cosine_similarity(cand_emb, target_emb)
            # 3. Alternative Identity Margin
            competitor_score = -1.0
            if alt_embs:
                competitor_score = max(self.onnx_verifier.cosine_similarity(cand_emb, a_emb) for a_emb in alt_embs.values())
            m_score = e_score - competitor_score if competitor_score > -1.0 else e_score

            if sample.is_positive:
                calib_g_pos.append(g_score)
                calib_e_pos.append(e_score)
                calib_m_pos.append(m_score)
            else:
                calib_g_neg.append(g_score)
                calib_e_neg.append(e_score)
                calib_m_neg.append(m_score)

        if not calib_g_pos or not calib_g_neg:
            raise ValueError("d_calib must contain at least one positive and one negative sample.")

        min_g_pos = min(calib_g_pos)
        max_g_neg = max(calib_g_neg)
        min_e_pos = min(calib_e_pos)
        max_e_neg = max(calib_e_neg)
        min_m_pos = min(calib_m_pos)
        max_m_neg = max(calib_m_neg)


        # -------------------------------------------------------------
        # STEP 2: Pre-Threshold Overlap Rejection (Pre-Overlap Gate)
        # -------------------------------------------------------------
        if min_g_pos <= max_g_neg:
            raise CalibrationOverlapError(
                f"CONFIGURATION_REJECTED: INSUFFICIENT_GEOMETRY_SEPARATION on D_calib. "
                f"min_positive ({min_g_pos:.4f}) <= max_negative ({max_g_neg:.4f}). "
                f"Target requires distinct geometric/contour features."
            )

        if min_e_pos <= max_e_neg:
            raise CalibrationOverlapError(
                f"CONFIGURATION_REJECTED: INSUFFICIENT_EMBEDDING_SEPARATION on D_calib. "
                f"min_positive ({min_e_pos:.4f}) <= max_negative ({max_e_neg:.4f})."
            )

        if min_m_pos <= max_m_neg:
            raise CalibrationOverlapError(
                f"CONFIGURATION_REJECTED: INSUFFICIENT_IDENTITY_MARGIN on D_calib. "
                f"min_positive_margin ({min_m_pos:.4f}) <= max_negative_margin ({max_m_neg:.4f}). "
                f"Target requires distinct margin against competitors."
            )

        # Calculate data-driven midpoint thresholds on D_calib
        t_g = float((min_g_pos + max_g_neg) / 2.0)
        t_e = float((min_e_pos + max_e_neg) / 2.0)
        m_safe = float((min_m_pos + max_m_neg) / 2.0)

        logger.info(
            f"D_calib Thresholds determined: T_g={t_g:.4f} (gap={min_g_pos - max_g_neg:.4f}), "
            f"T_e={t_e:.4f} (gap={min_e_pos - max_e_neg:.4f}), M_safe={m_safe:.4f}"
        )

        # -------------------------------------------------------------
        # STEP 3: Evaluation on Held-Out D_val (Frozen Threshold Check)
        # -------------------------------------------------------------
        val_e_pos, val_e_neg = [], []
        fp_observed = 0
        fn_observed = 0

        for sample in d_val:
            g_score = max(self.geo_verifier.compute_geometry_score(sample.image, ref) for ref in target_refs)
            cand_emb = self.onnx_verifier.compute_embeddings([sample.image])[0]
            e_score = self.onnx_verifier.cosine_similarity(cand_emb, target_emb)

            competitor_score = -1.0
            if alt_embs:
                competitor_score = max(self.onnx_verifier.cosine_similarity(cand_emb, a_emb) for a_emb in alt_embs.values())
            m_score = e_score - competitor_score if competitor_score > -1.0 else e_score

            passed_all = (g_score >= t_g) and (e_score >= t_e) and (m_score >= m_safe)

            if sample.is_positive:
                val_e_pos.append(e_score)
                if not passed_all:
                    fn_observed += 1
            else:
                val_e_neg.append(e_score)
                if passed_all:
                    fp_observed += 1

        val_min_pos = min(val_e_pos) if val_e_pos else 0.0
        val_max_neg = max(val_e_neg) if val_e_neg else 0.0
        separation_gap = val_min_pos - val_max_neg

        # Hard Acceptance on D_val
        if fp_observed > 0:
            raise CalibrationOverlapError(
                f"CONFIGURATION_REJECTED: {fp_observed} False Positives observed on Held-Out D_val. "
                f"Frozen thresholds failed to guarantee zero false positives."
            )

        if separation_gap <= 0.0:
            raise CalibrationOverlapError(
                f"CONFIGURATION_REJECTED: Separation gap on Held-Out D_val is non-positive ({separation_gap:.4f}). "
                f"Distributions overlap outside calibration set."
            )

        logger.info(
            f"Target '{target_id}' successfully calibrated! "
            f"Held-Out Separation Gap: {separation_gap:.4f}, FP: {fp_observed}, FN: {fn_observed}"
        )

        return CalibrationProfile(
            model_sha256=self.onnx_verifier.model_sha256,
            precision=self.onnx_verifier.precision,
            canonical_size=self.canonical_size,
            preprocessing_version=self.onnx_verifier.preprocessing_version,
            t_g=t_g,
            t_e=t_e,
            m_safe=m_safe,
            min_pos_sim=val_min_pos,
            max_neg_sim=val_max_neg,
            separation_gap=separation_gap,
            sample_count_pos=len(calib_g_pos) + len(val_e_pos),
            sample_count_neg=len(calib_g_neg) + len(val_e_neg)
        )


def calibrate_target_from_samples(
    target_id: str,
    target_reference_img: Union[np.ndarray, List[np.ndarray]],
    pos_calib: List[np.ndarray],
    neg_calib: List[np.ndarray],
    pos_val: List[np.ndarray],
    neg_val: List[np.ndarray],
    session_calib: str,
    session_val: str,
    run_calib: str,
    run_val: str,
    alternative_identity_imgs: Optional[Dict[str, Union[np.ndarray, List[np.ndarray]]]],
    geo_verifier: GeometryVerifier,
    onnx_verifier: ONNXVerifier,
    device_calib: str = "dev_calib",
    device_val: str = "dev_val",
    canonical_size: Tuple[int, int] = (64, 64)
) -> CalibrationProfile:
    """
    Executes calibration on explicitly partitioned sample sets provided by the caller,
    carrying verified group-wise provenance metadata (session_id, device_id, run_id).
    Strictly forbids splitting a single unpartitioned image list or synthesizing artificial session tags.
    """
    if not pos_calib or not pos_val:
        raise ValueError("Both calibration and validation partitions must contain at least 1 real positive sample.")
    if not neg_calib or not neg_val:
        raise ValueError("Both calibration and validation partitions must contain at least 1 confuser/negative sample.")
    if not alternative_identity_imgs:
        raise CalibrationOverlapError(
            f"CALIBRATION_BLOCKED_MISSING_CONFUSERS: Target '{target_id}' has no competitor targets or confusers configured."
        )

    # Validate provenance non-empty and disjoint
    s_calib = session_calib.strip()
    s_val = session_val.strip()
    r_calib = run_calib.strip()
    r_val = run_val.strip()

    if not s_calib or not s_val:
        raise ValueError("PROVENANCE_METADATA_REQUIRED: Non-empty session_calib and session_val must be provided.")
    if not r_calib or not r_val:
        raise ValueError("PROVENANCE_METADATA_REQUIRED: Non-empty run_calib and run_val must be provided.")

    if s_calib.lower() == s_val.lower():
        raise CalibrationOverlapError(
            f"DATA_LEAKAGE_DETECTED: Calibration and validation partitions share the same session ID ('{s_calib}'). "
            "Samples must originate from distinct physical capture sessions."
        )
    if r_calib.lower() == r_val.lower():
        raise CalibrationOverlapError(
            f"DATA_LEAKAGE_DETECTED: Calibration and validation partitions share the same capture run ID ('{r_calib}'). "
            "Samples must originate from distinct capture runs."
        )

    # Enforce zero content leakage between calibration and validation partitions
    calib_hashes = {hashlib.sha256(img.tobytes()).hexdigest() for img in pos_calib}
    for img in pos_val:
        if hashlib.sha256(img.tobytes()).hexdigest() in calib_hashes:
            raise CalibrationOverlapError(
                "DATA_LEAKAGE_DETECTED: Validation partition contains samples identical to calibration partition. "
                "Calibration requires genuinely independent capture samples."
            )

    neg_calib_hashes = {hashlib.sha256(img.tobytes()).hexdigest() for img in neg_calib}
    for img in neg_val:
        if hashlib.sha256(img.tobytes()).hexdigest() in neg_calib_hashes:
            raise CalibrationOverlapError(
                "DATA_LEAKAGE_DETECTED: Confuser validation partition contains samples identical to calibration partition."
            )

    # Check contradiction between positive and negative samples
    all_pos_hashes = calib_hashes.union({hashlib.sha256(img.tobytes()).hexdigest() for img in pos_val})
    for img in neg_calib + neg_val:
        if hashlib.sha256(img.tobytes()).hexdigest() in all_pos_hashes:
            raise CalibrationOverlapError(
                "DATA_CONTRADICTION_DETECTED: A negative confuser sample has identical pixel content to a positive target sample."
            )

    d_calib = [
        EvaluationSample(image=img, is_positive=True, label=target_id, session_id=s_calib, device_id=device_calib, run_id=r_calib)
        for img in pos_calib
    ] + [
        EvaluationSample(image=img, is_positive=False, label="negative", session_id=s_calib, device_id=device_calib, run_id=r_calib)
        for img in neg_calib
    ]

    d_val = [
        EvaluationSample(image=img, is_positive=True, label=target_id, session_id=s_val, device_id=device_val, run_id=r_val)
        for img in pos_val
    ] + [
        EvaluationSample(image=img, is_positive=False, label="negative", session_id=s_val, device_id=device_val, run_id=r_val)
        for img in neg_val
    ]

    return calibrate_target_from_partitions(
        target_id=target_id,
        target_reference_img=target_reference_img,
        d_calib=d_calib,
        d_val=d_val,
        alternative_identity_imgs=alternative_identity_imgs,
        geo_verifier=geo_verifier,
        onnx_verifier=onnx_verifier,
        canonical_size=canonical_size
    )


def calibrate_target_from_partitions(
    target_id: str,
    target_reference_img: Union[np.ndarray, List[np.ndarray]],
    d_calib: List[EvaluationSample],
    d_val: List[EvaluationSample],
    alternative_identity_imgs: Optional[Dict[str, Union[np.ndarray, List[np.ndarray]]]],
    geo_verifier: GeometryVerifier,
    onnx_verifier: ONNXVerifier,
    canonical_size: Tuple[int, int] = (64, 64)
) -> CalibrationProfile:
    """
    Executes calibration on explicitly partitioned D_calib and D_val with explicit provenance metadata.
    Enforces group-wise session/run independence without synthetic fabrication.
    """
    engine = CalibrationEngine(geometry_verifier=geo_verifier, onnx_verifier=onnx_verifier, canonical_size=canonical_size)
    return engine.calibrate_target(
        target_id=target_id,
        target_reference_img=target_reference_img,
        d_calib=d_calib,
        d_val=d_val,
        alternative_identity_imgs=alternative_identity_imgs
    )
