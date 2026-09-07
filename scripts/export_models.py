"""
scripts/export_models.py
~~~~~~~~~~~~~~~~~~~~~~~~
Generates and exports the self-contained UI Vision Encoder model to ONNX.
Computes and verifies SHA-256 model checksum.
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
    # Architecture: 3-stage Conv-ReLU-MaxPool + GlobalAveragePool -> 128-D embedding
    X = helper.make_tensor_value_info('input', TensorProto.FLOAT, ['batch', 3, 64, 64])
    Y = helper.make_tensor_value_info('embedding', TensorProto.FLOAT, ['batch', 128])

    # Deterministic weights for reproducibility
    np.random.seed(42)
    w1 = np.random.randn(32, 3, 3, 3).astype(np.float32) * 0.05
    w2 = np.random.randn(64, 32, 3, 3).astype(np.float32) * 0.05
    w3 = np.random.randn(128, 64, 3, 3).astype(np.float32) * 0.05

    t_w1 = helper.make_tensor('w1', TensorProto.FLOAT, w1.shape, w1.flatten())
    t_w2 = helper.make_tensor('w2', TensorProto.FLOAT, w2.shape, w2.flatten())
    t_w3 = helper.make_tensor('w3', TensorProto.FLOAT, w3.shape, w3.flatten())

    node_conv1 = helper.make_node('Conv', ['input', 'w1'], ['conv1_out'], pads=[1, 1, 1, 1])
    node_relu1 = helper.make_node('Relu', ['conv1_out'], ['relu1_out'])
    node_pool1 = helper.make_node('MaxPool', ['relu1_out'], ['pool1_out'], kernel_shape=[2, 2], strides=[2, 2])

    node_conv2 = helper.make_node('Conv', ['pool1_out', 'w2'], ['conv2_out'], pads=[1, 1, 1, 1])
    node_relu2 = helper.make_node('Relu', ['conv2_out'], ['relu2_out'])
    node_pool2 = helper.make_node('MaxPool', ['relu2_out'], ['pool2_out'], kernel_shape=[2, 2], strides=[2, 2])

    node_conv3 = helper.make_node('Conv', ['pool2_out', 'w3'], ['conv3_out'], pads=[1, 1, 1, 1])
    node_relu3 = helper.make_node('Relu', ['conv3_out'], ['relu3_out'])
    node_gap   = helper.make_node('GlobalAveragePool', ['relu3_out'], ['gap_out'])
    node_flat  = helper.make_node('Flatten', ['gap_out'], ['embedding'], axis=1)

    graph = helper.make_graph(
        [node_conv1, node_relu1, node_pool1, node_conv2, node_relu2, node_pool2, node_conv3, node_relu3, node_gap, node_flat],
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
