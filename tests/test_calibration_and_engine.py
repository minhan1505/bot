"""
tests/test_calibration_and_engine.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for CalibrationEngine (Pre-overlap rejection, Two-set split)
and VisionEngine (Tri-Condition Decision Authority).
Verifies:
  - Overlap on D_calib raises CalibrationOverlapError (Pre-overlap Gate).
  - Geometry failure CANNOT be rescued by embedding (Guard #1).
"""

import os
import pytest
import numpy as np
import cv2

from bot.core.models import Target, CalibrationProfile, DecisionClass
from bot.core.coordinates import Rect
from bot.vision.geometry import GeometryVerifier
from bot.vision.onnx_verifier import ONNXVerifier
from bot.vision.calibration import CalibrationEngine, EvaluationSample, CalibrationOverlapError
from bot.vision.engine import VisionEngine
from bot.vision.proposal import CandidateProposal
from tests.test_geometry import make_glyph_image

MODEL_PATH = os.path.abspath("models/ui_vision_encoder.onnx")


def test_calibration_pre_overlap_rejection():
    geo_verifier = GeometryVerifier(canonical_size=(64, 64))
    onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))
    calib_engine = CalibrationEngine(geo_verifier, onnx_verifier, canonical_size=(64, 64))

    target_img = make_glyph_image("CHECK")

    # Construct overlapping dataset (negative sample identical to target)
    d_calib_overlap = [
        EvaluationSample(image=target_img, is_positive=True, label="target_1", session_id="A", device_id="D1"),
        EvaluationSample(image=target_img, is_positive=False, label="fake_neg", session_id="A", device_id="D1") # Overlapping!
    ]
    d_val = [
        EvaluationSample(image=target_img, is_positive=True, label="target_1", session_id="B", device_id="D1")
    ]

    with pytest.raises(CalibrationOverlapError) as exc_info:
        calib_engine.calibrate_target("target_1", target_img, d_calib_overlap, d_val)

    assert "CONFIGURATION_REJECTED" in str(exc_info.value)


def test_tri_condition_authority_rejects_different_symbol_with_same_color():
    """
    Core Invariant: Symbol/Geometry is the mandatory authority.
    An imposter with the same background color but a different symbol MUST FAIL
    because the Geometry Gate fails, and embedding/color cannot rescue it!
    """
    geo_verifier = GeometryVerifier(canonical_size=(64, 64))
    onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))
    engine = VisionEngine(onnx_verifier, geometry_verifier=geo_verifier)

    target_img = make_glyph_image("CHECK", bg_color=(220, 40, 40)) # Checkmark on red
    imposter_cross = make_glyph_image("CROSS", bg_color=(220, 40, 40)) # X on identical red!

    # Create target with strict calibration profile
    calib = CalibrationProfile(
        model_sha256=onnx_verifier.model_sha256,
        precision="FP32",
        canonical_size=(64, 64),
        t_g=0.65,      # High geometry threshold
        t_e=0.70,      # Embedding threshold
        m_safe=0.05
    )
    target = Target(target_id="target_check", name="Check Target", calibration=calib)

    # Frame containing both the true checkmark and the imposter cross
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    frame[10:58, 10:58] = target_img       # True target at (10, 10)
    frame[10:58, 100:148] = imposter_cross  # Imposter at (100, 10)

    candidates = [
        CandidateProposal(rect=Rect(10, 10, 48, 48), region_id="r1"),
        CandidateProposal(rect=Rect(100, 10, 48, 48), region_id="r1")
    ]

    results = engine.evaluate_candidates(frame, candidates, target, target_img)
    assert len(results) == 2

    res_true = results[0]
    res_imposter = results[1]

    # True target must MATCH
    assert res_true.geometry_pass
    assert res_true.decision == DecisionClass.MATCH

    # Imposter must FAIL (UNKNOWN) because Geometry Gate failed!
    assert not res_imposter.geometry_pass
    assert res_imposter.decision == DecisionClass.UNKNOWN
    assert "GEOMETRY_FAIL" in res_imposter.reason
