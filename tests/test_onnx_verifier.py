"""
tests/test_onnx_verifier.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for ONNX Runtime Feature Verifier (Guard #2 & #3).
"""

import os
import pytest
import numpy as np
from bot.vision.onnx_verifier import ONNXVerifier

MODEL_PATH = os.path.abspath("models/ui_vision_encoder.onnx")


def test_onnx_verifier_loads_and_inspects_actual_dimension():
    verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64), precision="FP32")

    # Invariant Guard #3: Model dimension is inspected dynamically, not assumed
    assert verifier.native_embedding_dim == 128
    assert len(verifier.model_sha256) == 64
    assert verifier.precision == "FP32"


def test_onnx_verifier_batch_l2_normalization():
    verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))

    # Generate 5 random test images
    test_imgs = [np.random.randint(0, 256, (40, 50, 3), dtype=np.uint8) for _ in range(5)]
    embeddings = verifier.compute_embeddings(test_imgs)

    assert embeddings.shape == (5, 128)

    # Verify L2 norm of each row is 1.0
    norms = np.linalg.norm(embeddings, axis=1)
    for n in norms:
        assert n == pytest.approx(1.0, abs=1e-5)


def test_onnx_verifier_cosine_similarity():
    verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))

    img1 = np.full((48, 48, 3), 100, dtype=np.uint8)
    img2 = img1.copy()

    emb1 = verifier.compute_embeddings([img1])[0]
    emb2 = verifier.compute_embeddings([img2])[0]

    # Identical image -> cosine similarity = 1.0
    sim = verifier.cosine_similarity(emb1, emb2)
    assert sim == pytest.approx(1.0, abs=1e-4)
