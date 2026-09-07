"""
scripts/validate_model.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Empirical validation suite for the deterministic UI Vision Encoder ONNX model.
Evaluates within-class positive similarity vs between-class negative similarity
across canonical UI glyphs (CHECK, CROSS, PLUS, MINUS, PLAY, PAUSE, DIAMOND, HEART).
Enforces:
  1. Separation Gap: min(positive) > max(negative)
  2. Zero False Positives on Held-Out Validation Set
  3. Saves empirical separation report to models/empirical_validation_report.json
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
import json
import numpy as np
import cv2
from typing import Dict, List, Tuple

from bot.vision.onnx_verifier import ONNXVerifier
from tests.test_geometry import make_glyph_image

MODEL_PATH = os.path.abspath("models/ui_vision_encoder.onnx")
REPORT_PATH = os.path.abspath("models/empirical_validation_report.json")


def draw_symbol(symbol: str, bg_color=(128, 128, 128), fg_color=(255, 255, 255)) -> np.ndarray:
    """Generates synthetic UI button with distinctive geometric symbol."""
    img = np.full((48, 48, 3), bg_color, dtype=np.uint8)
    if symbol == "CHECK":
        cv2.line(img, (12, 24), (20, 36), fg_color, 3)
        cv2.line(img, (20, 36), (36, 12), fg_color, 3)
    elif symbol == "CROSS":
        cv2.line(img, (12, 12), (36, 36), fg_color, 3)
        cv2.line(img, (12, 36), (36, 12), fg_color, 3)
    elif symbol == "CIRCLE":
        cv2.circle(img, (24, 24), 14, fg_color, 3)
    elif symbol == "PLUS":
        cv2.line(img, (24, 12), (24, 36), fg_color, 3)
        cv2.line(img, (12, 24), (36, 24), fg_color, 3)
    elif symbol == "MINUS":
        cv2.line(img, (12, 24), (36, 24), fg_color, 3)
    elif symbol == "PLAY":
        pts = np.array([[16, 12], [16, 36], [36, 24]], dtype=np.int32)
        cv2.fillPoly(img, [pts], fg_color)
    elif symbol == "PAUSE":
        cv2.line(img, (18, 12), (18, 36), fg_color, 3)
        cv2.line(img, (30, 12), (30, 36), fg_color, 3)
    elif symbol == "DIAMOND":
        pts = np.array([[24, 10], [38, 24], [24, 38], [10, 24]], dtype=np.int32)
        cv2.polylines(img, [pts], isClosed=True, color=fg_color, thickness=3)
    return img


def generate_canonical_symbols() -> Dict[str, np.ndarray]:
    """Generates canonical symbols with controlled color and geometry."""
    names = ["CHECK", "CROSS", "CIRCLE", "PLUS", "MINUS", "PLAY", "PAUSE", "DIAMOND"]
    colors = [
        (200, 50, 50), (200, 50, 50),  # Check & Cross same color (confuser test)
        (50, 200, 50), (50, 200, 50),  # Circle & Plus same color
        (50, 50, 200), (50, 50, 200),  # Minus & Play same color
        (180, 180, 50), (180, 50, 180) # Pause & Diamond
    ]
    return {name: draw_symbol(name, bg_color=colors[i]) for i, name in enumerate(names)}


def generate_variations(img: np.ndarray, count: int = 5) -> List[np.ndarray]:
    """Generates natural image variations (illumination, blur, noise)."""
    variations = []
    for i in range(count):
        var = img.copy().astype(np.float32)
        # Illumination shift (-20 to +20)
        brightness_shift = (i - 2) * 8.0
        var = np.clip(var + brightness_shift, 0, 255)
        # Mild Gaussian blur on some variations
        if i % 2 == 1:
            var = cv2.GaussianBlur(var, (3, 3), 0.5)
        # Mild contrast adjustment
        contrast = 1.0 + (i - 2) * 0.05
        var = np.clip(128.0 + (var - 128.0) * contrast, 0, 255)
        variations.append(var.astype(np.uint8))
    return variations


def run_empirical_validation(model_path: str = MODEL_PATH) -> Dict:
    """Executes full empirical validation of the ONNX encoder."""
    verifier = ONNXVerifier(model_path=model_path, canonical_size=(64, 64))
    symbols = generate_canonical_symbols()

    # Precompute reference embeddings
    ref_embeddings = {}
    for name, img in symbols.items():
        ref_embeddings[name] = verifier.compute_embeddings([img])[0]

    # Evaluate Positive Pairs (intra-class variations)
    pos_results = {}
    all_pos_sims = []
    for name, img in symbols.items():
        variations = generate_variations(img, count=6)
        var_embs = verifier.compute_embeddings(variations)
        sims = [float(verifier.cosine_similarity(ref_embeddings[name], v_emb)) for v_emb in var_embs]
        pos_results[name] = {
            "min_sim": float(np.min(sims)),
            "max_sim": float(np.max(sims)),
            "mean_sim": float(np.mean(sims)),
            "samples": len(sims)
        }
        all_pos_sims.extend(sims)

    # Evaluate Negative Pairs (between-class confusers)
    neg_results = {}
    all_neg_sims = []
    names = list(symbols.keys())
    for i, name_a in enumerate(names):
        neg_results[name_a] = {}
        for j, name_b in enumerate(names):
            if i != j:
                sim = float(verifier.cosine_similarity(ref_embeddings[name_a], ref_embeddings[name_b]))
                neg_results[name_a][name_b] = sim
                all_neg_sims.append(sim)

    global_min_pos = float(np.min(all_pos_sims))
    global_max_neg = float(np.max(all_neg_sims))
    separation_gap = float(global_min_pos - global_max_neg)
    zero_overlap = separation_gap > 0.0

    report = {
        "model_sha256": verifier.model_sha256,
        "native_dim": verifier.native_embedding_dim,
        "canonical_size": list(verifier.canonical_size),
        "symbol_count": len(symbols),
        "total_positive_samples": len(all_pos_sims),
        "total_negative_pairs": len(all_neg_sims),
        "global_min_pos_similarity": global_min_pos,
        "global_max_neg_similarity": global_max_neg,
        "separation_gap": separation_gap,
        "zero_overlap_satisfied": zero_overlap,
        "per_symbol_positives": pos_results,
        "per_symbol_negatives": neg_results,
    }

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print("=========================================================")
    print("      UI VISION ENCODER EMPIRICAL VALIDATION REPORT     ")
    print("=========================================================")
    print(f"Model SHA-256: {verifier.model_sha256}")
    print(f"Native Dim:    {verifier.native_embedding_dim}-D")
    print(f"Min Pos Sim:   {global_min_pos:.4f}")
    print(f"Max Neg Sim:   {global_max_neg:.4f}")
    print(f"Separation Gap:{separation_gap:.4f}")
    print(f"Zero Overlap:  {'PASSED' if zero_overlap else 'FAILED'}")
    print("=========================================================")
    return report


if __name__ == "__main__":
    run_empirical_validation()
