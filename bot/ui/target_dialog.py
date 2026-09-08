"""
bot.ui.target_dialog
~~~~~~~~~~~~~~~~~~~~
Complete Target Management Dialog (FC-05).
Supports:
  - Target rename & enabled/disabled toggle
  - Multiple reference images with thumbnail & pixel dimensions (WxH)
  - Confuser images management with thumbnail & dimensions
  - Add / remove reference and confuser images
"""

import os
import cv2
import numpy as np
import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox,
    QHeaderView, QLineEdit, QCheckBox, QGroupBox, QSplitter
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from bot.core.models import Target
from bot.vision.geometry import check_geometry_separability

logger = logging.getLogger(__name__)


class TargetDetailsDialog(QDialog):
    """Dialog for inspecting, editing, and managing a Target's references and confusers."""

    def __init__(self, target: Target, parent=None):
        super().__init__(parent)
        self.original_target = target
        self.target = target.model_copy(deep=True)
        self.setWindowTitle(f"Target Details — {target.name} ({target.target_id})")
        self.resize(850, 600)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Meta info
        meta_box = QHBoxLayout()
        meta_box.addWidget(QLabel("Target ID:"))
        lbl_id = QLabel(self.target.target_id)
        lbl_id.setStyleSheet("font-weight: bold;")
        meta_box.addWidget(lbl_id)

        meta_box.addWidget(QLabel("Name:"))
        self.txt_name = QLineEdit(self.target.name)
        meta_box.addWidget(self.txt_name)

        self.chk_enabled = QCheckBox("Enabled")
        self.chk_enabled.setChecked(getattr(self.target, "enabled", True))
        meta_box.addWidget(self.chk_enabled)

        layout.addLayout(meta_box)

        # Splitter: Left References, Right Confusers
        splitter = QSplitter(Qt.Horizontal)

        # Left: Reference Images
        ref_group = QGroupBox("Reference Images (Positive Samples)")
        ref_layout = QVBoxLayout(ref_group)

        self.table_refs = QTableWidget(0, 3)
        self.table_refs.setHorizontalHeaderLabels(["#", "Path / Dimensions", "Preview"])
        self.table_refs.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_refs.setSelectionBehavior(QTableWidget.SelectRows)
        ref_layout.addWidget(self.table_refs)

        ref_btn_bar = QHBoxLayout()
        self.btn_add_ref = QPushButton("Add Reference Image...")
        self.btn_add_ref.clicked.connect(self._add_ref_image)
        ref_btn_bar.addWidget(self.btn_add_ref)

        self.btn_del_ref = QPushButton("Remove Selected")
        self.btn_del_ref.clicked.connect(self._del_ref_image)
        ref_btn_bar.addWidget(self.btn_del_ref)
        ref_layout.addLayout(ref_btn_bar)

        splitter.addWidget(ref_group)

        # Right: Confuser Images
        conf_group = QGroupBox("Confuser Images (Hard Negatives / Competitors)")
        conf_layout = QVBoxLayout(conf_group)

        self.table_confs = QTableWidget(0, 3)
        self.table_confs.setHorizontalHeaderLabels(["#", "Path / Dimensions", "Preview"])
        self.table_confs.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_confs.setSelectionBehavior(QTableWidget.SelectRows)
        conf_layout.addWidget(self.table_confs)

        conf_btn_bar = QHBoxLayout()
        self.btn_add_conf = QPushButton("Add Confuser Image...")
        self.btn_add_conf.clicked.connect(self._add_conf_image)
        conf_btn_bar.addWidget(self.btn_add_conf)

        self.btn_del_conf = QPushButton("Remove Selected")
        self.btn_del_conf.clicked.connect(self._del_conf_image)
        conf_btn_bar.addWidget(self.btn_del_conf)
        conf_layout.addLayout(conf_btn_bar)

        splitter.addWidget(conf_group)

        layout.addWidget(splitter)

        # Bottom Bar: Save / Cancel
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()

        btn_save = QPushButton("Save Changes")
        btn_save.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;")
        btn_save.clicked.connect(self._save_and_close)
        bottom_bar.addWidget(btn_save)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        bottom_bar.addWidget(btn_cancel)

        layout.addLayout(bottom_bar)

        self._refresh_tables()

    def _refresh_tables(self):
        # Refresh references
        self.table_refs.setRowCount(0)
        for idx, path in enumerate(self.target.reference_image_paths):
            row = self.table_refs.rowCount()
            self.table_refs.insertRow(row)
            self.table_refs.setItem(row, 0, QTableWidgetItem(str(idx + 1)))

            dim_str = "File not found"
            if os.path.exists(path):
                img = cv2.imread(path)
                if img is not None:
                    h, w = img.shape[:2]
                    dim_str = f"{os.path.basename(path)} ({w}x{h})"
                    self._set_table_thumbnail(self.table_refs, row, 2, img)

            self.table_refs.setItem(row, 1, QTableWidgetItem(dim_str))

        # Refresh confusers
        self.table_confs.setRowCount(0)
        for idx, path in enumerate(self.target.confuser_image_paths):
            row = self.table_confs.rowCount()
            self.table_confs.insertRow(row)
            self.table_confs.setItem(row, 0, QTableWidgetItem(str(idx + 1)))

            dim_str = "File not found"
            if os.path.exists(path):
                img = cv2.imread(path)
                if img is not None:
                    h, w = img.shape[:2]
                    dim_str = f"{os.path.basename(path)} ({w}x{h})"
                    self._set_table_thumbnail(self.table_confs, row, 2, img)

            self.table_confs.setItem(row, 1, QTableWidgetItem(dim_str))

    def _set_table_thumbnail(self, table: QTableWidget, row: int, col: int, img_bgr: np.ndarray):
        h, w = img_bgr.shape[:2]
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        lbl = QLabel()
        lbl.setPixmap(scaled)
        lbl.setAlignment(Qt.AlignCenter)
        table.setCellWidget(row, col, lbl)
        table.setRowHeight(row, 70)

    def _add_ref_image(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Reference Image", "", "Images (*.png *.jpg *.bmp)")
        if file_path:
            img = cv2.imread(file_path)
            if img is None:
                QMessageBox.critical(self, "Invalid Image", "Cannot read image file.")
                return
            is_sep, reason = check_geometry_separability(img)
            if not is_sep:
                QMessageBox.warning(self, "Separability Warning", f"Image has weak geometric separability:\n{reason}")
            self.target.reference_image_paths.append(file_path)
            self._refresh_tables()

    def _del_ref_image(self):
        row = self.table_refs.currentRow()
        if row >= 0 and row < len(self.target.reference_image_paths):
            if len(self.target.reference_image_paths) <= 1:
                QMessageBox.warning(self, "Cannot Remove", "Target must have at least one reference image.")
                return
            self.target.reference_image_paths.pop(row)
            self._refresh_tables()

    def _add_conf_image(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Confuser Image", "", "Images (*.png *.jpg *.bmp)")
        if file_path:
            img = cv2.imread(file_path)
            if img is None:
                QMessageBox.critical(self, "Invalid Image", "Cannot read image file.")
                return
            self.target.confuser_image_paths.append(file_path)
            self._refresh_tables()

    def _del_conf_image(self):
        row = self.table_confs.currentRow()
        if row >= 0 and row < len(self.target.confuser_image_paths):
            self.target.confuser_image_paths.pop(row)
            self._refresh_tables()

    def _save_and_close(self):
        new_name = self.txt_name.text().strip()
        if new_name:
            self.target.name = new_name
        self.target.enabled = self.chk_enabled.isChecked()

        # Check if reference or confuser images changed (U06)
        refs_changed = (self.target.reference_image_paths != self.original_target.reference_image_paths)
        confs_changed = (self.target.confuser_image_paths != self.original_target.confuser_image_paths)

        if refs_changed or confs_changed:
            # Identity-defining target dataset content changed!
            # U06 Vision Safety: Force recalibration to prevent stale calibration validity
            self.target.calibration = None
            logger.info(f"Target '{self.target.target_id}' references/confusers modified. Invalidation of CalibrationProfile enforced.")
            QMessageBox.information(
                self,
                "Calibration Invalidated",
                f"Target sample images were modified.\n\n"
                f"The existing CalibrationProfile has been invalidated (calibration=None).\n"
                f"Recalibration is required before running this target in production."
            )

        # Commit deep-copy changes back to original target
        self.original_target.name = self.target.name
        self.original_target.enabled = self.target.enabled
        self.original_target.reference_image_paths = list(self.target.reference_image_paths)
        self.original_target.confuser_image_paths = list(self.target.confuser_image_paths)
        self.original_target.calibration = self.target.calibration

        self.accept()
