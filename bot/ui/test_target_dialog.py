"""
bot.ui.test_target_dialog
~~~~~~~~~~~~~~~~~~~~~~~~~
Offline and live-frame Target Verification Dialog (FC-06).
Allows verifying detection, geometry score, embedding similarity,
identity margin, and latency on a frozen or live frame with ZERO action dispatch.
"""

import os
import time
import cv2
import numpy as np
import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox,
    QHeaderView, QRadioButton, QButtonGroup, QScrollArea
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap, QColor

from bot.core.models import Target, Profile, DecisionClass, DecisionResult
from bot.vision.engine import VisionEngine
from bot.capture.manager import CaptureManager

logger = logging.getLogger(__name__)


class TestTargetDialog(QDialog):
    """Interactive Target Verification dialog without action dispatch."""

    def __init__(
        self,
        target: Target,
        profile: Profile,
        vision_engine: VisionEngine,
        capture_manager: CaptureManager,
        parent=None
    ):
        super().__init__(parent)
        self.target = target
        self.profile = profile
        self.vision_engine = vision_engine
        self.capture_manager = capture_manager
        self.current_frame: np.ndarray = None
        self.last_decisions = []

        self.setWindowTitle(f"Test Target — {target.name} ({target.target_id})")
        self.resize(1000, 750)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Source Selection Header
        header_box = QHBoxLayout()
        header_box.addWidget(QLabel("Test Image Source:"))

        self.radio_live = QRadioButton("Current Captured Frame")
        self.radio_live.setChecked(True)
        self.radio_file = QRadioButton("Offline Image File")

        self.bg_source = QButtonGroup(self)
        self.bg_source.addButton(self.radio_live)
        self.bg_source.addButton(self.radio_file)
        header_box.addWidget(self.radio_live)
        header_box.addWidget(self.radio_file)

        self.btn_browse = QPushButton("Browse Image...")
        self.btn_browse.setEnabled(False)
        self.btn_browse.clicked.connect(self._browse_image)
        header_box.addWidget(self.btn_browse)

        self.radio_file.toggled.connect(lambda checked: self.btn_browse.setEnabled(checked))

        self.btn_run_test = QPushButton("RUN TARGET TEST")
        self.btn_run_test.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;")
        self.btn_run_test.clicked.connect(self._run_test)
        header_box.addWidget(self.btn_run_test)

        header_box.addStretch()
        layout.addLayout(header_box)

        # Split area: Results Table & Visual Frame Preview
        mid_layout = QHBoxLayout()

        # Left: Results table
        table_vbox = QVBoxLayout()
        table_vbox.addWidget(QLabel("Evaluation Candidates:"))
        self.table_results = QTableWidget(0, 7)
        self.table_results.setHorizontalHeaderLabels([
            "Cand #", "Rect (X, Y, W, H)", "Geometry", "Embedding", "Margin", "Decision", "Latency"
        ])
        self.table_results.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_results.setSelectionBehavior(QTableWidget.SelectRows)
        table_vbox.addWidget(self.table_results)
        mid_layout.addLayout(table_vbox, 60)

        # Right: Frame Preview with bounding boxes
        preview_vbox = QVBoxLayout()
        preview_vbox.addWidget(QLabel("Frame Preview (Boxes drawn):"))
        self.scroll_preview = QScrollArea()
        self.lbl_image_preview = QLabel("No test run yet.")
        self.lbl_image_preview.setAlignment(Qt.AlignCenter)
        self.scroll_preview.setWidget(self.lbl_image_preview)
        self.scroll_preview.setWidgetResizable(True)
        preview_vbox.addWidget(self.scroll_preview)
        mid_layout.addLayout(preview_vbox, 40)

        layout.addLayout(mid_layout)

        # Status footer
        self.lbl_summary = QLabel("Ready to test target. 0 dispatch guaranteed.")
        self.lbl_summary.setStyleSheet("color: #555; font-style: italic;")
        layout.addWidget(self.lbl_summary)

        # Close button
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignRight)

    def _browse_image(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Test Image", "", "Images (*.png *.jpg *.bmp)")
        if file_path:
            img = cv2.imread(file_path)
            if img is not None:
                self.current_frame = img
                self._update_preview(img)
                self.lbl_summary.setText(f"Loaded image: {os.path.basename(file_path)} ({img.shape[1]}x{img.shape[0]})")
            else:
                QMessageBox.critical(self, "Error", "Failed to load selected image file.")

    def _run_test(self):
        if not self.target.reference_image_paths:
            QMessageBox.warning(self, "No References", "Target has no reference image paths configured.")
            return

        if self.radio_live.isChecked():
            frame, _ = self.capture_manager.grab()
            if frame is None:
                QMessageBox.critical(self, "Capture Error", "Failed to grab screen frame.")
                return
            self.current_frame = frame
        elif self.current_frame is None:
            QMessageBox.warning(self, "No Frame", "Please browse and select an image file first.")
            return

        frame = self.current_frame.copy()

        # Build competitor targets
        alt_targets = {}
        for o_id, o_target in self.profile.targets.items():
            if o_id != self.target.target_id and o_target.reference_image_paths:
                o_img = cv2.imread(o_target.reference_image_paths[0])
                if o_img is not None:
                    alt_targets[o_id] = o_img

        for c_idx, c_path in enumerate(self.target.confuser_image_paths):
            if os.path.exists(c_path):
                c_img = cv2.imread(c_path)
                if c_img is not None:
                    alt_targets[f"confuser_{c_idx}"] = c_img

        t_start = time.perf_counter()
        decisions = self.vision_engine.test_target_on_frame(
            frame=frame,
            target=self.target,
            alternative_targets=alt_targets
        )
        total_lat = (time.perf_counter() - t_start) * 1000.0
        self.last_decisions = decisions

        # Populate table
        self.table_results.setRowCount(0)
        matches = 0
        preview_img = frame.copy()

        for idx, d in enumerate(decisions):
            row = self.table_results.rowCount()
            self.table_results.insertRow(row)

            self.table_results.setItem(row, 0, QTableWidgetItem(f"#{idx + 1}"))
            rx, ry, rw, rh = d.candidate_rect
            self.table_results.setItem(row, 1, QTableWidgetItem(f"({rx}, {ry}, {rw}x{rh})"))
            self.table_results.setItem(row, 2, QTableWidgetItem(f"{d.geometry_score:.3f} (pass={d.geometry_pass})"))
            self.table_results.setItem(row, 3, QTableWidgetItem(f"{d.embedding_similarity:.3f} (pass={d.embedding_pass})"))
            self.table_results.setItem(row, 4, QTableWidgetItem(f"{d.identity_margin:.3f} (pass={d.margin_pass})"))

            dec_item = QTableWidgetItem(d.decision.value)
            if d.decision == DecisionClass.MATCH:
                matches += 1
                dec_item.setForeground(QColor("#2e7d32"))
                color = (0, 255, 0)
            elif d.decision == DecisionClass.NON_MATCH:
                dec_item.setForeground(QColor("#c62828"))
                color = (0, 0, 255)
            else:
                dec_item.setForeground(QColor("#e65100"))
                color = (0, 200, 255)

            self.table_results.setItem(row, 5, dec_item)
            self.table_results.setItem(row, 6, QTableWidgetItem(f"{d.latency_ms:.1f} ms"))

            # Draw box on preview
            cv2.rectangle(preview_img, (rx, ry), (rx + rw, ry + rh), color, 2)
            cv2.putText(preview_img, f"#{idx + 1}:{d.decision.value}", (rx, max(15, ry - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        self._update_preview(preview_img)
        self.lbl_summary.setText(
            f"Test Completed: {len(decisions)} candidates evaluated, {matches} MATCH found. Total Latency: {total_lat:.1f}ms. (0 dispatch)"
        )

    def _update_preview(self, img_bgr: np.ndarray):
        h, w = img_bgr.shape[:2]
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(400, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.lbl_image_preview.setPixmap(scaled)
