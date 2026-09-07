"""
tests/test_model_empirical_validation.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Automated test suite verifying the empirical validation of the UI Vision Encoder.
Tests that positive pairs maintain separation over confuser pairs and zero false positives.
"""

import os
import pytest
from scripts.validate_model import run_empirical_validation, MODEL_PATH, REPORT_PATH
from bot.vision.onnx_verifier import compute_file_sha256


def test_model_artifact_provenance_and_checksum():
    """Asserts that the exported ONNX model matches its recorded SHA-256 hash."""
    assert os.path.exists(MODEL_PATH), "ONNX model artifact must exist"
    hash_path = os.path.abspath("models/ui_vision_encoder.sha256")
    assert os.path.exists(hash_path), "Model SHA-256 file must exist"

    with open(hash_path, "r") as f:
        expected_hash = f.read().strip()

    actual_hash = compute_file_sha256(MODEL_PATH)
    assert actual_hash == expected_hash, f"Model SHA-256 mismatch: {actual_hash} != {expected_hash}"


def test_model_empirical_zero_overlap_separation():
    """Asserts that empirical validation achieves zero overlap and positive separation gap."""
    report = run_empirical_validation()
    assert report["zero_overlap_satisfied"] is True
    assert report["separation_gap"] > 0.0
    assert report["global_min_pos_similarity"] > report["global_max_neg_similarity"]
    assert report["native_dim"] == 128
    assert os.path.exists(REPORT_PATH)
