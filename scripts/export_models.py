"""
scripts/export_models.py
~~~~~~~~~~~~~~~~~~~~~~~~
Generates and exports the deterministic UI Vision Encoder baseline model to ONNX.
Computes and verifies SHA-256 model checksum.

NOTE: This baseline model uses analytical spatial filter banks (directional edge kernels,
contour integrators, and structural filters) exported via ONNX Conv/ReLU/MaxPool/Flatten.
It serves as a deterministic baseline and does NOT require external PyTorch checkpoint weights.
Production pre-trained deep models with empirical held-out benchmark datasets (D_test)
remain marked PARTIAL in AUDIT_CHECKLIST.md until trained weights and real held-out UI datasets
are provided.
"""

import os
import hashlib
import numpy as np
import onnx
from onnx import helper, TensorProto
import onnxruntime as ort

MODELS_DIR = os.path.abspath("models")
MODEL_PATH = os.path.join(MODELS_DIR, "ui_vision_encoder.onnx")
HASH_PATH = os.path.join(MODELS_DIR, "ui_vision_encoder.sha256")


def build_and_export_encoder(output_path: str = MODEL_PATH):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Input: [batch, 3, 64, 64]
    # Architecture: 3-stage Conv-ReLU-MaxPool + Flatten -> 128-D spatial feature embedding
    X = helper.make_tensor_value_info('input', TensorProto.FLOAT, ['batch', 3, 64, 64])
    Y = helper.make_tensor_value_info('embedding', TensorProto.FLOAT, ['batch', 128])

    # Deterministic spatial filter bank (Sobel directional & contour structural filters)
    # Stage 1: 16 Spatial Directional & Edge Filters
    w1 = np.zeros((16, 3, 3, 3), dtype=np.float32)
    for idx in range(8):
        theta = idx * np.pi / 4.0
        dx, dy = np.cos(theta), np.sin(theta)
        kernel = np.array([
            [-dx - dy, -dy, dx - dy],
            [-dx,      0.0, dx],
            [-dx + dy,  dy, dx + dy]
        ], dtype=np.float32)
        kernel -= kernel.mean()
        norm = np.linalg.norm(kernel) + 1e-6
        for c in range(3):
            w1[idx, c] = (kernel / norm) * (1.0 / np.sqrt(3.0))

    for idx in range(8):
        theta = idx * np.pi / 4.0
        kernel = np.array([
            [np.cos(2 * theta),  np.sin(2 * theta), -np.cos(2 * theta)],
            [-np.sin(2 * theta), 0.0,                np.sin(2 * theta)],
            [-np.cos(2 * theta), -np.sin(2 * theta), np.cos(2 * theta)]
        ], dtype=np.float32)
        kernel -= kernel.mean()
        norm = np.linalg.norm(kernel) + 1e-6
        for c in range(3):
            w1[8 + idx, c] = (kernel / norm) * (1.0 / np.sqrt(3.0))

    # Stage 2: 32 Contour Integrator Filters (Combining directional features)
    w2 = np.zeros((32, 16, 3, 3), dtype=np.float32)
    for out_c in range(32):
        in_c = out_c % 16
        theta = (out_c // 4) * np.pi / 4.0
        kernel = np.array([
            [np.cos(theta), 0.0, -np.cos(theta)],
            [np.sin(theta), 1.0, -np.sin(theta)],
            [0.0,          -1.0, 0.0]
        ], dtype=np.float32)
        kernel -= kernel.mean()
        norm = np.linalg.norm(kernel) + 1e-6
        w2[out_c, in_c] = kernel / norm

    # Stage 3: 32 High-Level Spatial Structural Filters
    w3 = np.zeros((32, 32, 3, 3), dtype=np.float32)
    for out_c in range(32):
        in_c = out_c % 32
        shift = out_c % 4
        kernel = np.zeros((3, 3), dtype=np.float32)
        if shift == 0:
            kernel[0, :] = 1.0; kernel[2, :] = -1.0
        elif shift == 1:
            kernel[:, 0] = 1.0; kernel[:, 2] = -1.0
        elif shift == 2:
            kernel[0, 0] = 1.0; kernel[2, 2] = -1.0
        else:
            kernel[0, 2] = 1.0; kernel[2, 0] = -1.0
        kernel -= kernel.mean()
        norm = np.linalg.norm(kernel) + 1e-6
        w3[out_c, in_c] = kernel / norm

    t_w1 = helper.make_tensor('w1', TensorProto.FLOAT, w1.shape, w1.flatten())
    t_w2 = helper.make_tensor('w2', TensorProto.FLOAT, w2.shape, w2.flatten())
    t_w3 = helper.make_tensor('w3', TensorProto.FLOAT, w3.shape, w3.flatten())

    # Layer dimensions:
    # Input: [batch, 3, 64, 64]
    # Conv1: [batch, 16, 64, 64] -> MaxPool 2x2 -> [batch, 16, 32, 32]
    # Conv2: [batch, 32, 32, 32] -> MaxPool 2x2 -> [batch, 32, 16, 16]
    # Conv3: [batch, 32, 16, 16] -> MaxPool 8x8 (stride 8) -> [batch, 32, 2, 2] = 128 elements!
    # Flatten -> [batch, 128]
    node_conv1 = helper.make_node('Conv', ['input', 'w1'], ['conv1_out'], pads=[1, 1, 1, 1])
    node_relu1 = helper.make_node('Relu', ['conv1_out'], ['relu1_out'])
    node_pool1 = helper.make_node('MaxPool', ['relu1_out'], ['pool1_out'], kernel_shape=[2, 2], strides=[2, 2])

    node_conv2 = helper.make_node('Conv', ['pool1_out', 'w2'], ['conv2_out'], pads=[1, 1, 1, 1])
    node_relu2 = helper.make_node('Relu', ['conv2_out'], ['relu2_out'])
    node_pool2 = helper.make_node('MaxPool', ['relu2_out'], ['pool2_out'], kernel_shape=[2, 2], strides=[2, 2])

    node_conv3 = helper.make_node('Conv', ['pool2_out', 'w3'], ['conv3_out'], pads=[1, 1, 1, 1])
    node_relu3 = helper.make_node('Relu', ['conv3_out'], ['relu3_out'])
    node_pool3 = helper.make_node('MaxPool', ['relu3_out'], ['pool3_out'], kernel_shape=[8, 8], strides=[8, 8])
    node_flat  = helper.make_node('Flatten', ['pool3_out'], ['embedding'], axis=1)

    graph = helper.make_graph(
        [node_conv1, node_relu1, node_pool1, node_conv2, node_relu2, node_pool2, node_conv3, node_relu3, node_pool3, node_flat],
        'ui_symbol_encoder',
        [X],
        [Y],
        [t_w1, t_w2, t_w3]
    )

    model = helper.make_model(graph, producer_name='bot_v2_encoder', opset_imports=[helper.make_opsetid('', 17)])
    onnx.checker.check_model(model)
    onnx.save(model, output_path)

    # Compute SHA-256
    with open(output_path, "rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()

    with open(HASH_PATH, "w") as f:
        f.write(file_hash)

    print(f"Exported model to: {output_path}")
    print(f"SHA-256 Checksum: {file_hash}")

    # Verify session run
    sess = ort.InferenceSession(output_path, providers=['CPUExecutionProvider'])
    dummy = np.zeros((1, 3, 64, 64), dtype=np.float32)
    out = sess.run(['embedding'], {'input': dummy})[0]
    print(f"Verified Runtime Output Shape: {out.shape} (Dimension: {out.shape[-1]}-D)")
    return file_hash


if __name__ == "__main__":
    build_and_export_encoder()
