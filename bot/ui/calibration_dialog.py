"""
bot.ui.calibration_dialog
~~~~~~~~~~~~~~~~~~~~~~~~~
Interactive Target Calibration Wizard Dialog.
Implements F06 Production Calibration Flow:
  - Enforces two-set split on independent capture sessions: Session A (D_calib) and Session B (D_val).
  - Strictly zero synthetic data generation or brightness shift fabrication.
  - Enforces Pre-Overlap Rejection on D_calib and Zero False Positives on D_val.
  - Computes empirical T_g, T_e, M_safe and separation gap.
  - Updates Target.calibration and saves profile.
"""

import os
import hashlib
import cv2
import numpy as np
from typing import List, Dict, Optional, Tuple
import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QGroupBox, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QTabWidget, QWidget, QLineEdit
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage, QColor

from bot.core.models import Target, Profile, CalibrationProfile
from bot.vision.geometry import GeometryVerifier
from bot.vision.onnx_verifier import ONNXVerifier
from bot.vision.calibration import CalibrationEngine, EvaluationSample, CalibrationOverlapError

logger = logging.getLogger(__name__)


def np_to_qpixmap(img: np.ndarray, max_size: int = 128) -> QPixmap:
    """Converts a BGR numpy image to a scaled QPixmap for display."""
    if img is None or img.size == 0:
        return QPixmap()
    h, w = img.shape[:2]
    if len(img.shape) == 3:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    else:
        rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    bytes_per_line = 3 * w
    qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
    pix = QPixmap.fromImage(qimg)
    return pix.scaled(max_size, max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class TargetCalibrationDialog(QDialog):
    """
    Production Calibration Wizard Dialog for a specific target.
    Requires genuine, partitioned capture sessions without synthetic data fabrication.
    """

    def __init__(
        self,
        target: Target,
        profile: Profile,
        onnx_verifier: ONNXVerifier,
        geo_verifier: GeometryVerifier,
        parent=None
    ):
        super().__init__(parent)
        self.target = target
        self.profile = profile
        self.onnx_verifier = onnx_verifier
        self.geo_verifier = geo_verifier
        self.calibrated_profile: Optional[CalibrationProfile] = None

        # Separate genuine samples by session: Session A (calib) and Session B (val)
        # Seed Session A with existing reference image
        self.session_a_pos: List[str] = list(self.target.reference_image_paths[:1]) if self.target.reference_image_paths else []
        self.session_b_pos: List[str] = list(self.target.reference_image_paths[1:]) if len(self.target.reference_image_paths) > 1 else []

        self.session_a_neg: List[str] = list(self.target.confuser_image_paths[:1]) if self.target.confuser_image_paths else []
        self.session_b_neg: List[str] = list(self.target.confuser_image_paths[1:]) if len(self.target.confuser_image_paths) > 1 else []

        self.setWindowTitle(f"Target Calibration Wizard - {target.name} [{target.target_id}]")
        self.resize(700, 600)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 1. Target Preview Group
        grp_target = QGroupBox("Target Information")
        target_layout = QHBoxLayout(grp_target)

        self.lbl_preview = QLabel()
        self.lbl_preview.setFixedSize(100, 100)
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setStyleSheet("border: 1px solid #555; background: #222;")

        if self.target.reference_image_paths and os.path.exists(self.target.reference_image_paths[0]):
            ref_img = cv2.imread(self.target.reference_image_paths[0])
            if ref_img is not None:
                self.lbl_preview.setPixmap(np_to_qpixmap(ref_img, max_size=96))

        info_layout = QFormLayout()
        info_layout.addRow("Target ID:", QLabel(self.target.target_id))
        info_layout.addRow("Target Name:", QLabel(self.target.name))
        current_status = "Calibrated" if self.target.calibration else "UNCALIBRATED (Production Action Locked)"
        status_color = "#2e7d32" if self.target.calibration else "#c62828"
        lbl_status = QLabel(current_status)
        lbl_status.setStyleSheet(f"font-weight: bold; color: {status_color};")
        info_layout.addRow("Current Status:", lbl_status)

        target_layout.addWidget(self.lbl_preview)
        target_layout.addLayout(info_layout)
        layout.addWidget(grp_target)

        # 2. Tabs for Session A (D_calib) and Session B (D_val)
        tabs_session = QTabWidget()
        tabs_session.addTab(self._create_session_tab("A"), "Session A — Calibration Set (D_calib)")
        tabs_session.addTab(self._create_session_tab("B"), "Session B — Validation Set (D_val)")
        layout.addWidget(tabs_session)

        # 3. Diagnostic Readout
        grp_diag = QGroupBox("Empirical Thresholds & Separation Diagnostics")
        self.diag_layout = QFormLayout(grp_diag)
        self.lbl_tg = QLabel("T_g (Geometry): Not Calibrated")
        self.lbl_te = QLabel("T_e (Embedding): Not Calibrated")
        self.lbl_msafe = QLabel("M_safe (Competitor Margin): Not Calibrated")
        self.lbl_gap = QLabel("Separation Gap: Not Calibrated")

        self.diag_layout.addRow(self.lbl_tg)
        self.diag_layout.addRow(self.lbl_te)
        self.diag_layout.addRow(self.lbl_msafe)
        self.diag_layout.addRow(self.lbl_gap)
        layout.addWidget(grp_diag)

        # 4. Action Buttons
        actions_layout = QHBoxLayout()
        self.btn_run_calib = QPushButton("Run Empirical Calibration")
        self.btn_run_calib.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold; padding: 8px 16px;")
        self.btn_run_calib.clicked.connect(self._run_calibration)

        self.btn_save = QPushButton("Accept & Save Profile")
        self.btn_save.setEnabled(False)
        self.btn_save.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 8px 16px;")
        self.btn_save.clicked.connect(self.accept)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        actions_layout.addWidget(self.btn_run_calib)
        actions_layout.addWidget(self.btn_save)
        actions_layout.addWidget(btn_cancel)
        layout.addLayout(actions_layout)

        self._refresh_tables()

    def _create_session_tab(self, session_key: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Provenance Metadata
        grp_prov = QGroupBox(f"Capture Provenance Metadata (Session {session_key})")
        prov_layout = QFormLayout(grp_prov)
        txt_session = QLineEdit()
        txt_session.setPlaceholderText(f"e.g. 2026-09-08_site_{session_key.lower()}")
        txt_device = QLineEdit()
        txt_device.setPlaceholderText(f"e.g. rig_{'primary' if session_key == 'A' else 'secondary'}")
        txt_run = QLineEdit()
        txt_run.setPlaceholderText(f"e.g. capture_run_{session_key.lower()}_01")
        setattr(self, f"txt_session_{session_key.lower()}", txt_session)
        setattr(self, f"txt_device_{session_key.lower()}", txt_device)
        setattr(self, f"txt_run_{session_key.lower()}", txt_run)
        prov_layout.addRow("Session ID:", txt_session)
        prov_layout.addRow("Device ID:", txt_device)
        prov_layout.addRow("Run ID:", txt_run)
        layout.addWidget(grp_prov)

        # Positive Samples
        lbl_pos = QLabel(f"Real Positive Samples for Session {session_key} (Must be real captures of this target):")
        layout.addWidget(lbl_pos)

        table_pos = QTableWidget(0, 1)
        table_pos.setHorizontalHeaderLabels(["Sample Image Path"])
        table_pos.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        setattr(self, f"table_pos_{session_key.lower()}", table_pos)
        layout.addWidget(table_pos)

        btn_bar_pos = QHBoxLayout()
        btn_add_pos = QPushButton(f"Add Positive Sample(s) to Session {session_key}...")
        btn_add_pos.clicked.connect(lambda _, s=session_key: self._add_positive_sample(s))
        btn_bar_pos.addWidget(btn_add_pos)

        btn_del_pos = QPushButton(f"Remove Selected")
        btn_del_pos.clicked.connect(lambda _, s=session_key: self._remove_positive_sample(s))
        btn_bar_pos.addWidget(btn_del_pos)
        btn_bar_pos.addStretch()
        layout.addLayout(btn_bar_pos)

        # Confuser / Negative Samples
        lbl_neg = QLabel(f"Real Confuser / Negative Samples for Session {session_key}:")
        layout.addWidget(lbl_neg)

        table_neg = QTableWidget(0, 1)
        table_neg.setHorizontalHeaderLabels(["Confuser Image Path"])
        table_neg.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        setattr(self, f"table_neg_{session_key.lower()}", table_neg)
        layout.addWidget(table_neg)

        btn_bar_neg = QHBoxLayout()
        btn_add_neg = QPushButton(f"Add Confuser(s) to Session {session_key}...")
        btn_add_neg.clicked.connect(lambda _, s=session_key: self._add_confuser_sample(s))
        btn_bar_neg.addWidget(btn_add_neg)

        btn_del_neg = QPushButton(f"Remove Selected")
        btn_del_neg.clicked.connect(lambda _, s=session_key: self._remove_confuser_sample(s))
        btn_bar_neg.addWidget(btn_del_neg)
        btn_bar_neg.addStretch()
        layout.addLayout(btn_bar_neg)

        return widget

    def _refresh_tables(self):
        # Refresh Session A
        t_pos_a = getattr(self, "table_pos_a")
        t_pos_a.setRowCount(0)
        for p in self.session_a_pos:
            r = t_pos_a.rowCount()
            t_pos_a.insertRow(r)
            t_pos_a.setItem(r, 0, QTableWidgetItem(p))

        t_neg_a = getattr(self, "table_neg_a")
        t_neg_a.setRowCount(0)
        for p in self.session_a_neg:
            r = t_neg_a.rowCount()
            t_neg_a.insertRow(r)
            t_neg_a.setItem(r, 0, QTableWidgetItem(p))

        # Refresh Session B
        t_pos_b = getattr(self, "table_pos_b")
        t_pos_b.setRowCount(0)
        for p in self.session_b_pos:
            r = t_pos_b.rowCount()
            t_pos_b.insertRow(r)
            t_pos_b.setItem(r, 0, QTableWidgetItem(p))

        t_neg_b = getattr(self, "table_neg_b")
        t_neg_b.setRowCount(0)
        for p in self.session_b_neg:
            r = t_neg_b.rowCount()
            t_neg_b.insertRow(r)
            t_neg_b.setItem(r, 0, QTableWidgetItem(p))

    def _add_positive_sample(self, session_key: str):
        files, _ = QFileDialog.getOpenFileNames(self, f"Select Positive Sample(s) for Session {session_key}", "", "Images (*.png *.jpg *.bmp)")
        if files:
            target_list = self.session_a_pos if session_key == "A" else self.session_b_pos
            other_list = self.session_b_pos if session_key == "A" else self.session_a_pos
            for f in files:
                abs_f = os.path.abspath(f)
                if any(os.path.abspath(other) == abs_f for other in other_list):
                    QMessageBox.warning(
                        self,
                        "DATA_LEAKAGE_REJECTED",
                        f"Cannot add '{os.path.basename(f)}': File is already present in Session {'B' if session_key == 'A' else 'A'}.\n"
                        "Calibration and validation require strictly independent capture files."
                    )
                    continue
                if f not in target_list:
                    target_list.append(f)
            self._refresh_tables()

    def _remove_positive_sample(self, session_key: str):
        table = getattr(self, f"table_pos_{session_key.lower()}")
        row = table.currentRow()
        if row >= 0:
            target_list = self.session_a_pos if session_key == "A" else self.session_b_pos
            if row < len(target_list):
                target_list.pop(row)
            self._refresh_tables()

    def _add_confuser_sample(self, session_key: str):
        files, _ = QFileDialog.getOpenFileNames(self, f"Select Confuser(s) for Session {session_key}", "", "Images (*.png *.jpg *.bmp)")
        if files:
            target_list = self.session_a_neg if session_key == "A" else self.session_b_neg
            other_list = self.session_b_neg if session_key == "A" else self.session_a_neg
            for f in files:
                abs_f = os.path.abspath(f)
                if any(os.path.abspath(other) == abs_f for other in other_list):
                    QMessageBox.warning(
                        self,
                        "DATA_LEAKAGE_REJECTED",
                        f"Cannot add '{os.path.basename(f)}': Confuser file is already present in Session {'B' if session_key == 'A' else 'A'}."
                    )
                    continue
                if f not in target_list:
                    target_list.append(f)
            self._refresh_tables()

    def _remove_confuser_sample(self, session_key: str):
        table = getattr(self, f"table_neg_{session_key.lower()}")
        row = table.currentRow()
        if row >= 0:
            target_list = self.session_a_neg if session_key == "A" else self.session_b_neg
            if row < len(target_list):
                target_list.pop(row)
            self._refresh_tables()

    def _run_calibration(self):
        # Strict Precondition Check: Zero synthetic fabrication
        if len(self.session_a_pos) < 1 or len(self.session_b_pos) < 1:
            QMessageBox.warning(
                self,
                "MISSING_SESSION_POS_SAMPLES",
                "Data-driven calibration requires independent capture sessions.\n"
                "Please provide at least 1 real positive sample for Session A (D_calib) "
                "and at least 1 real positive sample for Session B (D_val).\n"
                "Synthetic duplication or brightness shifts are strictly prohibited."
            )
            return

        # Check file path overlap between Session A and Session B
        pos_a_paths = {os.path.abspath(p) for p in self.session_a_pos}
        for p in self.session_b_pos:
            if os.path.abspath(p) in pos_a_paths:
                QMessageBox.critical(
                    self,
                    "DATA_LEAKAGE_DETECTED",
                    f"Data leakage detected: File '{os.path.basename(p)}' is present in both Session A and Session B.\n"
                    "Calibration (D_calib) and Validation (D_val) must originate from distinct capture files."
                )
                return

        if len(self.session_a_neg) < 1 or len(self.session_b_neg) < 1:
            # Check if profile has distinct competitor targets to populate negatives
            competitor_imgs = []
            for oid, ot in self.profile.targets.items():
                if oid != self.target.target_id and ot.reference_image_paths and os.path.exists(ot.reference_image_paths[0]):
                    competitor_imgs.append(ot.reference_image_paths[0])

            if len(self.session_a_neg) < 1 and len(competitor_imgs) >= 1:
                self.session_a_neg.append(competitor_imgs[0])
            if len(self.session_b_neg) < 1 and len(competitor_imgs) >= 2:
                self.session_b_neg.append(competitor_imgs[1])

            self._refresh_tables()

            if len(self.session_a_neg) < 1 or len(self.session_b_neg) < 1:
                QMessageBox.warning(
                    self,
                    "MISSING_SESSION_CONFUSERS",
                    "Data-driven calibration requires at least 1 confuser/competitor sample for Session A "
                    "and 1 distinct sample for Session B to establish competitor margins.\n"
                    "Add distinct confuser image files to both sessions before running calibration."
                )
                return

        # Check confuser path overlap between Session A and Session B
        neg_a_paths = {os.path.abspath(p) for p in self.session_a_neg}
        for p in self.session_b_neg:
            if os.path.abspath(p) in neg_a_paths:
                QMessageBox.critical(
                    self,
                    "DATA_LEAKAGE_DETECTED",
                    f"Data leakage detected: Confuser file '{os.path.basename(p)}' is present in both Session A and Session B."
                )
                return

        # Load real images
        pos_a_imgs = [cv2.imread(p) for p in self.session_a_pos if os.path.exists(p)]
        pos_b_imgs = [cv2.imread(p) for p in self.session_b_pos if os.path.exists(p)]
        neg_a_imgs = [cv2.imread(p) for p in self.session_a_neg if os.path.exists(p)]
        neg_b_imgs = [cv2.imread(p) for p in self.session_b_neg if os.path.exists(p)]

        pos_a_imgs = [img for img in pos_a_imgs if img is not None]
        pos_b_imgs = [img for img in pos_b_imgs if img is not None]
        neg_a_imgs = [img for img in neg_a_imgs if img is not None]
        neg_b_imgs = [img for img in neg_b_imgs if img is not None]

        if not pos_a_imgs or not pos_b_imgs or not neg_a_imgs or not neg_b_imgs:
            QMessageBox.critical(self, "Image Read Error", "Failed to load image files from disk. Verify file paths.")
            return

        # Check pixel content hashes across sessions (Zero Content Leakage)
        pos_a_hashes = {hashlib.sha256(img.tobytes()).hexdigest() for img in pos_a_imgs}
        for img in pos_b_imgs:
            h = hashlib.sha256(img.tobytes()).hexdigest()
            if h in pos_a_hashes:
                QMessageBox.critical(
                    self,
                    "DATA_LEAKAGE_DETECTED",
                    "Data leakage detected: A positive sample in Session B has identical pixel content "
                    "to a sample in Session A.\nZero data leakage requires truly distinct capture sessions."
                )
                return

        neg_a_hashes = {hashlib.sha256(img.tobytes()).hexdigest() for img in neg_a_imgs}
        for img in neg_b_imgs:
            h = hashlib.sha256(img.tobytes()).hexdigest()
            if h in neg_a_hashes:
                QMessageBox.critical(
                    self,
                    "DATA_LEAKAGE_DETECTED",
                    "Data leakage detected: A confuser sample in Session B has identical pixel content "
                    "to a confuser in Session A.\nBoth sessions require independent samples."
                )
                return

        # Check contradiction between positive samples and confuser samples
        all_pos_hashes = pos_a_hashes.union({hashlib.sha256(img.tobytes()).hexdigest() for img in pos_b_imgs})
        for img in neg_a_imgs + neg_b_imgs:
            h = hashlib.sha256(img.tobytes()).hexdigest()
            if h in all_pos_hashes:
                QMessageBox.critical(
                    self,
                    "DATA_CONTRADICTION_DETECTED",
                    "Contradiction detected: A negative confuser sample has identical pixel content to a positive target sample.\n"
                    "A sample cannot be both positive and negative."
                )
                return

        # Validate group-wise provenance metadata
        txt_sess_a = getattr(self, "txt_session_a", None)
        txt_sess_b = getattr(self, "txt_session_b", None)
        txt_dev_a = getattr(self, "txt_device_a", None)
        txt_dev_b = getattr(self, "txt_device_b", None)
        txt_r_a = getattr(self, "txt_run_a", None)
        txt_r_b = getattr(self, "txt_run_b", None)

        session_a_id = txt_sess_a.text().strip() if txt_sess_a else ""
        session_b_id = txt_sess_b.text().strip() if txt_sess_b else ""
        device_a_id = txt_dev_a.text().strip() if txt_dev_a else ""
        device_b_id = txt_dev_b.text().strip() if txt_dev_b else ""
        run_a_id = txt_r_a.text().strip() if txt_r_a else ""
        run_b_id = txt_r_b.text().strip() if txt_r_b else ""

        if not session_a_id or not session_b_id:
            QMessageBox.warning(
                self,
                "PROVENANCE_REQUIRED",
                "Explicit, non-empty Session IDs are required for both Session A and Session B.\n"
                "Please enter verified capture session identifiers."
            )
            return

        if not run_a_id or not run_b_id:
            QMessageBox.warning(
                self,
                "PROVENANCE_REQUIRED",
                "Explicit, non-empty Capture Run IDs are required for both Session A and Session B.\n"
                "Please enter verified capture run identifiers."
            )
            return

        if session_a_id.lower() == session_b_id.lower():
            QMessageBox.critical(
                self,
                "DATA_LEAKAGE_DETECTED",
                f"Data leakage detected: Session IDs must be distinct for group-wise independence.\n"
                f"Session A: '{session_a_id}', Session B: '{session_b_id}'"
            )
            return

        if run_a_id.lower() == run_b_id.lower():
            QMessageBox.critical(
                self,
                "DATA_LEAKAGE_DETECTED",
                f"Data leakage detected: Run IDs must be distinct across calibration and validation partitions.\n"
                f"Run A: '{run_a_id}', Run B: '{run_b_id}'"
            )
            return

        # Check for generic dummy labels that evade true provenance
        generic_dummies = {"session_a", "session_b", "run_a", "run_b", "session1", "session2", "run1", "run2"}
        if session_a_id.lower() in generic_dummies or session_b_id.lower() in generic_dummies:
            QMessageBox.warning(
                self,
                "GENERIC_PLACEHOLDER_REJECTED",
                "Generic dummy session labels ('session_a', 'session_b', etc.) are rejected.\n"
                "Please provide meaningful capture session provenance from your acquisition records."
            )
            return
        if run_a_id.lower() in generic_dummies or run_b_id.lower() in generic_dummies:
            QMessageBox.warning(
                self,
                "GENERIC_PLACEHOLDER_REJECTED",
                "Generic dummy run labels ('run_a', 'run_b', etc.) are rejected.\n"
                "Please provide meaningful capture run provenance from your acquisition records."
            )
            return

        ref_img = pos_a_imgs[0]

        # Construct competitor alternative targets dictionary
        alt_imgs: Dict[str, np.ndarray] = {}
        for idx, img in enumerate(neg_a_imgs + neg_b_imgs):
            alt_imgs[f"confuser_{idx}"] = img

        # Construct genuine, partitioned evaluation samples with verified provenance
        d_calib = [
            EvaluationSample(image=img, is_positive=True, label=self.target.target_id, session_id=session_a_id, device_id=device_a_id, run_id=run_a_id)
            for img in pos_a_imgs
        ] + [
            EvaluationSample(image=img, is_positive=False, label="confuser", session_id=session_a_id, device_id=device_a_id, run_id=run_a_id)
            for img in neg_a_imgs
        ]

        d_val = [
            EvaluationSample(image=img, is_positive=True, label=self.target.target_id, session_id=session_b_id, device_id=device_b_id, run_id=run_b_id)
            for img in pos_b_imgs
        ] + [
            EvaluationSample(image=img, is_positive=False, label="confuser", session_id=session_b_id, device_id=device_b_id, run_id=run_b_id)
            for img in neg_b_imgs
        ]

        try:
            from bot.vision.calibration import calibrate_target_from_partitions
            calib_result = calibrate_target_from_partitions(
                target_id=self.target.target_id,
                target_reference_img=ref_img,
                d_calib=d_calib,
                d_val=d_val,
                alternative_identity_imgs=alt_imgs,
                geo_verifier=self.geo_verifier,
                onnx_verifier=self.onnx_verifier,
                canonical_size=(64, 64)
            )

            self.calibrated_profile = calib_result
            self.lbl_tg.setText(f"T_g (Geometry): {calib_result.t_g:.4f}")
            self.lbl_te.setText(f"T_e (Embedding): {calib_result.t_e:.4f}")
            self.lbl_msafe.setText(f"M_safe (Competitor Margin): {calib_result.m_safe:.4f}")
            self.lbl_gap.setText(f"Separation Gap: +{calib_result.separation_gap:.4f} (PASSED Zero-Overlap)")
            self.lbl_gap.setStyleSheet("color: #2e7d32; font-weight: bold;")

            # Persist real sample paths into target
            all_pos = list(dict.fromkeys(self.session_a_pos + self.session_b_pos))
            all_neg = list(dict.fromkeys(self.session_a_neg + self.session_b_neg))
            self.target.reference_image_paths = all_pos
            self.target.confuser_image_paths = all_neg
            calib_result.target_content_hash = self.target.compute_content_hash()
            self.target.calibration = calib_result

            self.btn_save.setEnabled(True)
            QMessageBox.information(
                self,
                "Calibration Succeeded",
                f"Data-driven calibration passed with zero false positives!\n"
                f"T_g={calib_result.t_g:.3f}, T_e={calib_result.t_e:.3f}, M_safe={calib_result.m_safe:.3f}\n"
                f"Held-Out Separation Gap: +{calib_result.separation_gap:.3f}"
            )

        except CalibrationOverlapError as exc:
            self.lbl_gap.setText(f"FAILED: {exc}")
            self.lbl_gap.setStyleSheet("color: #c62828; font-weight: bold;")
            QMessageBox.critical(
                self,
                "Calibration Rejected",
                f"Empirical calibration failed safety acceptance criteria:\n{exc}\n"
                "Target distributions overlap or produce false positives. Add more distinct samples or adjust confusers."
            )
