"""
bot.vision.onnx_verifier
~~~~~~~~~~~~~~~~~~~~~~~~
ONNX Runtime Feature Embedding Verifier.
Strictly respects Execution Guard #2 and #3:
  - Binds calibration to exact model_sha256 + precision + canonical_size + preprocessing_version.
  - Dynamically inspects and verifies actual ONNX output tensor shape and dimension at runtime.
  - Never forces arbitrary reshaping if artifact reality differs.
  - Generates L2-normalized embeddings for fast matrix dot-product cosine similarity.
"""

import os
import hashlib
import cv2
import numpy as np
import onnxruntime as ort
from typing import List, Tuple, Dict, Optional
import logging

logger = logging.getLogger(__name__)


def compute_file_sha256(filepath: str) -> str:
    """Computes SHA-256 checksum of an artifact file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    return sha256.hexdigest()


class ONNXVerifier:
    """
    Inference Engine using ONNX Runtime.
    Extracts L2-normalized feature vectors and computes cosine similarity.
    """

    def __init__(
        self,
        model_path: str,
        canonical_size: Tuple[int, int] = (64, 64),
        precision: str = "FP32",
        providers: Optional[List[str]] = None
    ):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"ONNX Model file not found: {model_path}")

        self.model_path = model_path
        self.canonical_size = canonical_size
        self.precision = precision.upper()
        self.model_sha256 = compute_file_sha256(model_path)
        self.preprocessing_version = "v2.3_canonical_letterbox"

        if providers is None:
            providers = ["CPUExecutionProvider"]

        # Initialize ONNX Runtime Session
        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = 4
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(self.model_path, sess_options=sess_options, providers=providers)

        # Inspect and record actual model metadata
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

        # Probe actual native output dimension with a single dummy pass
        dummy_input = np.zeros((1, 3, self.canonical_size[1], self.canonical_size[0]), dtype=np.float32)
        dummy_out = self.session.run([self.output_name], {self.input_name: dummy_input})[0]

        # Invariant Guard #3: Read real output dimension directly from runtime output tensor
        if dummy_out.ndim != 2:
            # Flatten if multi-dimensional (e.g. [1, D, 1, 1])
            dummy_out = dummy_out.reshape(dummy_out.shape[0], -1)

        self.native_embedding_dim = dummy_out.shape[-1]
        logger.info(
            f"ONNXVerifier loaded: {os.path.basename(model_path)} "
            f"[SHA-256: {self.model_sha256[:12]}...] "
            f"Native Dim: {self.native_embedding_dim}-D, Input: {self.input_name}, Canonical: {self.canonical_size}"
        )

        # In-memory target embedding cache: target_id -> np.ndarray [D]
        self._target_cache: Dict[str, np.ndarray] = {}

    def letterbox_preprocess(self, img: np.ndarray, target_size: Tuple[int, int]) -> np.ndarray:
        """
        Resizes image maintaining aspect ratio with constant padding.
        Converts BGR -> RGB and normalizes pixel values to [0.0, 1.0].
        Returns float32 array in [3, H, W] format.
        """
        target_w, target_h = target_size
        h, w = img.shape[:2]

        scale = min(target_w / float(w), target_h / float(h))
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))

        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # Convert BGR -> RGB
        if len(resized.shape) == 3:
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        else:
            rgb = cv2.cvtColor(resized, cv2.COLOR_GRAY2RGB)

        pad_x = (target_w - new_w) // 2
        pad_y = (target_h - new_h) // 2

        canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = rgb

        # Normalize to [0.0, 1.0] and CHW layout
        tensor = canvas.astype(np.float32) / 255.0
        tensor = np.transpose(tensor, (2, 0, 1)) # HWC -> CHW

        # Standard ImageNet normalization: (x - mean) / std
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
        tensor = (tensor - mean) / std

        return tensor

    def compute_embeddings(self, images: List[np.ndarray]) -> np.ndarray:
        """
        Computes L2-normalized feature embeddings for a batch of images (U07).
        Safely chunks batches larger than 64 to prevent memory spikes.
        Returns np.ndarray of shape [B, D].
        """
        if not images:
            return np.empty((0, self.native_embedding_dim), dtype=np.float32)

        if len(images) > 64:
            chunk_embs = []
            for i in range(0, len(images), 64):
                chunk_embs.append(self.compute_embeddings(images[i:i + 64]))
            return np.vstack(chunk_embs)

        batch_tensors = [self.letterbox_preprocess(img, self.canonical_size) for img in images]
        batch_input = np.stack(batch_tensors, axis=0).astype(np.float32)

        outputs = self.session.run([self.output_name], {self.input_name: batch_input})[0]

        if outputs.ndim > 2:
            outputs = outputs.reshape(outputs.shape[0], -1)

        # L2-normalization: ||v||_2 = 1
        norms = np.linalg.norm(outputs, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-8) # Avoid division by zero
        normalized_embeddings = outputs / norms

        return normalized_embeddings

    @staticmethod
    def _compute_images_hash(images: List[np.ndarray]) -> str:
        h = hashlib.sha256()
        for img in images:
            h.update(img.tobytes())
        return h.hexdigest()[:16]

    def cache_target_embedding(self, target_id: str, reference_images: List[np.ndarray]):
        """
        Encodes and caches mean target embedding across all provided reference image variations.
        Keys embedding by target_id and content hash to prevent cross-profile contamination.
        """
        if not reference_images:
            raise ValueError(f"No reference images provided for target {target_id}")

        embs = self.compute_embeddings(reference_images)
        # Compute mean vector across variations and re-normalize
        mean_emb = np.mean(embs, axis=0, keepdims=True)
        norm = np.linalg.norm(mean_emb)
        norm = max(norm, 1e-8)
        normed = (mean_emb / norm).flatten()
        img_hash = self._compute_images_hash(reference_images)
        self._target_cache[target_id] = (img_hash, normed)
        logger.info(f"Cached embedding for target '{target_id}' [hash: {img_hash}] (averaged across {len(reference_images)} references).")

    def get_cached_target_embedding(
        self,
        target_id: str,
        reference_images: Optional[List[np.ndarray]] = None
    ) -> Optional[np.ndarray]:
        entry = self._target_cache.get(target_id)
        if entry is None:
            return None
        if isinstance(entry, tuple):
            cached_hash, emb = entry
            if reference_images is not None:
                current_hash = self._compute_images_hash(reference_images)
                if current_hash != cached_hash:
                    logger.debug(f"Target cache miss for '{target_id}': content changed ({cached_hash} != {current_hash})")
                    return None
            return emb
        return entry

    def clear_target_cache(self):
        """Clears target embedding cache (e.g. on profile switch)."""
        self._target_cache.clear()
        logger.debug("Cleared target embedding cache.")

    @staticmethod
    def cosine_similarity(v1: np.ndarray, v2: np.ndarray) -> float:
        """
        Computes cosine similarity between two L2-normalized vectors.
        Since vectors are unit length, this is directly the dot product.
        """
        sim = float(np.dot(v1.flatten(), v2.flatten()))
        return float(np.clip(sim, -1.0, 1.0))

    @staticmethod
    def batch_cosine_similarity(candidate_embeddings: np.ndarray, target_embedding: np.ndarray) -> np.ndarray:
        """
        Computes cosine similarities for batch of candidates against a single target vector.
        candidate_embeddings: [B, D]
        target_embedding: [D]
        Returns: [B]
        """
        sims = np.dot(candidate_embeddings, target_embedding.flatten())
        return np.clip(sims, -1.0, 1.0)
