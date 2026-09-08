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

    alt_img = make_glyph_image("CROSS")

    with pytest.raises(CalibrationOverlapError) as exc_info:
        calib_engine.calibrate_target(
            "target_1", target_img, d_calib_overlap, d_val,
            alternative_identity_imgs={"alt_1": alt_img}
        )

    assert "CONFIGURATION_REJECTED" in str(exc_info.value)


def test_calibration_missing_confusers_rejected():
    geo_verifier = GeometryVerifier(canonical_size=(64, 64))
    onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))
    calib_engine = CalibrationEngine(geo_verifier, onnx_verifier, canonical_size=(64, 64))

    target_img = make_glyph_image("CHECK")
    alt_img = make_glyph_image("CROSS")
    d_calib = [
        EvaluationSample(image=target_img, is_positive=True, label="target_1", session_id="A", device_id="D1"),
        EvaluationSample(image=alt_img, is_positive=False, label="neg_1", session_id="A", device_id="D1"),
    ]
    d_val = [
        EvaluationSample(image=target_img, is_positive=True, label="target_1", session_id="B", device_id="D1"),
        EvaluationSample(image=alt_img, is_positive=False, label="neg_1", session_id="B", device_id="D1"),
    ]

    with pytest.raises(CalibrationOverlapError) as exc_info:
        calib_engine.calibrate_target("target_1", target_img, d_calib, d_val, alternative_identity_imgs=None)

    assert "CALIBRATION_BLOCKED_MISSING_CONFUSERS" in str(exc_info.value)


def test_calibration_data_leakage_rejected():
    geo_verifier = GeometryVerifier(canonical_size=(64, 64))
    onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))
    calib_engine = CalibrationEngine(geo_verifier, onnx_verifier, canonical_size=(64, 64))

    target_img = make_glyph_image("CHECK")
    alt_img = make_glyph_image("CROSS")
    # Same session "A" in both calib and val
    d_calib = [
        EvaluationSample(image=target_img, is_positive=True, label="target_1", session_id="A", device_id="D1"),
        EvaluationSample(image=alt_img, is_positive=False, label="neg_1", session_id="A", device_id="D1"),
    ]
    d_val = [
        EvaluationSample(image=target_img, is_positive=True, label="target_1", session_id="A", device_id="D1"),
        EvaluationSample(image=alt_img, is_positive=False, label="neg_1", session_id="A", device_id="D1"),
    ]

    with pytest.raises(CalibrationOverlapError) as exc_info:
        calib_engine.calibrate_target("target_1", target_img, d_calib, d_val, alternative_identity_imgs={"alt_1": alt_img})

    assert "DATA_LEAKAGE_DETECTED" in str(exc_info.value)


def test_calibration_run_id_overlap_rejected():
    geo_verifier = GeometryVerifier(canonical_size=(64, 64))
    onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))
    calib_engine = CalibrationEngine(geo_verifier, onnx_verifier, canonical_size=(64, 64))

    target_img = make_glyph_image("CHECK")
    alt_img = make_glyph_image("CROSS")
    # Distinct session IDs but overlapping run IDs ("run_shared")
    d_calib = [
        EvaluationSample(image=target_img, is_positive=True, label="target_1", session_id="session_A", device_id="D1", run_id="run_shared"),
        EvaluationSample(image=alt_img, is_positive=False, label="neg_1", session_id="session_A", device_id="D1", run_id="run_shared"),
    ]
    d_val = [
        EvaluationSample(image=target_img, is_positive=True, label="target_1", session_id="session_B", device_id="D1", run_id="run_shared"),
        EvaluationSample(image=alt_img, is_positive=False, label="neg_1", session_id="session_B", device_id="D1", run_id="run_shared"),
    ]

    with pytest.raises(CalibrationOverlapError) as exc_info:
        calib_engine.calibrate_target("target_1", target_img, d_calib, d_val, alternative_identity_imgs={"alt_1": alt_img})

    assert "DATA_LEAKAGE_DETECTED" in str(exc_info.value)
    assert "Capture runs" in str(exc_info.value)


def test_calibrate_target_from_samples_rejects_confuser_leakage():
    from bot.vision.calibration import calibrate_target_from_samples

    geo_verifier = GeometryVerifier(canonical_size=(64, 64))
    onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))

    pos_1 = make_glyph_image("CHECK")
    pos_2 = make_glyph_image("PLUS")
    shared_confuser = make_glyph_image("CROSS")

    # Confusers are identical across calib and val partitions: [shared_confuser] and [shared_confuser]
    with pytest.raises(CalibrationOverlapError) as exc_info:
        calibrate_target_from_samples(
            target_id="target_1",
            target_reference_img=pos_1,
            pos_calib=[pos_1],
            neg_calib=[shared_confuser],
            pos_val=[pos_2],
            neg_val=[shared_confuser],
            session_calib="siteA_session",
            session_val="siteB_session",
            run_calib="runA_01",
            run_val="runB_02",
            alternative_identity_imgs={"confuser": shared_confuser},
            geo_verifier=geo_verifier,
            onnx_verifier=onnx_verifier
        )

    assert "DATA_LEAKAGE_DETECTED" in str(exc_info.value)
    assert "Confuser validation partition" in str(exc_info.value)


def test_calibrate_target_from_samples_rejects_identical_session_or_run():
    from bot.vision.calibration import calibrate_target_from_samples

    geo_verifier = GeometryVerifier(canonical_size=(64, 64))
    onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))

    pos_1 = make_glyph_image("CHECK")
    pos_2 = make_glyph_image("PLUS")
    confuser_1 = make_glyph_image("CROSS")
    confuser_2 = make_glyph_image("SQUARE")

    # Identical session IDs
    with pytest.raises(CalibrationOverlapError) as exc_info:
        calibrate_target_from_samples(
            target_id="target_1",
            target_reference_img=pos_1,
            pos_calib=[pos_1],
            neg_calib=[confuser_1],
            pos_val=[pos_2],
            neg_val=[confuser_2],
            session_calib="same_session",
            session_val="same_session",
            run_calib="runA",
            run_val="runB",
            alternative_identity_imgs={"confuser": confuser_1},
            geo_verifier=geo_verifier,
            onnx_verifier=onnx_verifier
        )
    assert "DATA_LEAKAGE_DETECTED" in str(exc_info.value)
    assert "session ID" in str(exc_info.value)

    # Identical run IDs
    with pytest.raises(CalibrationOverlapError) as exc_info:
        calibrate_target_from_samples(
            target_id="target_1",
            target_reference_img=pos_1,
            pos_calib=[pos_1],
            neg_calib=[confuser_1],
            pos_val=[pos_2],
            neg_val=[confuser_2],
            session_calib="session_1",
            session_val="session_2",
            run_calib="same_run",
            run_val="same_run",
            alternative_identity_imgs={"confuser": confuser_1},
            geo_verifier=geo_verifier,
            onnx_verifier=onnx_verifier
        )
    assert "DATA_LEAKAGE_DETECTED" in str(exc_info.value)
    assert "capture run ID" in str(exc_info.value)




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
