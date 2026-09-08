"""
bot.ui.main_window
~~~~~~~~~~~~~~~~~~
PySide6 Main Dashboard for BotAutoClick V2.3.
Fully integrates:
  - 100% Core Requirements: Target Management, 1..N Dynamic Workflows, Regions.
  - Runtime Runner Thread: Live real-time capture, detection, and action loop.
  - Global Hotkey Manager (F12) for system-wide emergency stop.
  - Windows System Tray Manager with background status and quick controls.
  - Profile Packaging: ZIP Export and Safe Import.
  - Data-Driven Target Calibration: Interactive Empirical Calibration Wizard.
  - Visual Screen ROI Selection: Define region coordinates directly from desktop overlay.
  - Dynamic Workflow Editor: Target selection, action parameters, step reordering.
  - 2-Stage Action Surface Verification: Protocol & render surface validation with fail-closed guard.
"""

import sys
import os
import time
import cv2
import numpy as np
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QLabel, QComboBox, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QGroupBox, QLineEdit, QSpinBox, QHeaderView,
    QInputDialog, QDialog
)
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QIcon, QColor

from bot.core.models import Profile, Target, RegionModel, Workflow, WorkflowStep, DecisionResult, DecisionClass, CalibrationProfile
from bot.core.database import Database
from bot.core.bundle import ProfileBundleManager
from bot.core.hotkey import GlobalHotkeyManager
from bot.capture.manager import CaptureManager
from bot.ui.crop_overlay import ScreenCropOverlay
from bot.ui.tray import SystemTrayManager
from bot.ui.calibration_dialog import TargetCalibrationDialog
from bot.ui.step_dialog import WorkflowStepDialog
from bot.ui.surface_dialog import SurfaceVerificationDialog
from bot.action.manager import ActionManager
from bot.action.cdp_backend import CDPActionBackend
from bot.action.window_backend import WindowActionBackend
from bot.vision.geometry import check_geometry_separability, GeometryVerifier
from bot.vision.onnx_verifier import ONNXVerifier
from bot.vision.proposal import CandidateProposalEngine
from bot.vision.engine import VisionEngine
from bot.workflow.ledger import SessionLedger
from bot.workflow.runner import BotRuntimeRunner
from bot.telemetry.logger import AsyncTelemetryLogger

MODEL_PATH = os.path.abspath("models/ui_vision_encoder.onnx")



class MainWindow(QMainWindow):
    """PySide6 Desktop Application MainWindow with complete V2.3 pipeline."""
    decision_received_signal = Signal(object)
    region_state_signal = Signal(str, str, int)

    def __init__(self, db_path: str = "bot_data.db"):
        super().__init__()
        self.setWindowTitle("BotAutoClick V2.3 — Generic Vision Automation")
        self.resize(1150, 800)

        self.db = Database(db_path)
        self.capture_manager = CaptureManager(monitor_index=1, prefer_dxgi=True)
        self.action_manager = ActionManager()
        self.telemetry_logger = AsyncTelemetryLogger(log_filepath="logs/telemetry.jsonl")

        # Vision Subsystem
        self.geo_verifier = GeometryVerifier(canonical_size=(64, 64))
        self.onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))
        self.proposal_engine = CandidateProposalEngine(k_base_per_region=4, max_batch_limit=32)
        self.vision_engine = VisionEngine(self.onnx_verifier, self.geo_verifier, self.proposal_engine)

        self.ledger = SessionLedger()
        self.runner: Optional[BotRuntimeRunner] = None
        self.active_profile: Optional[Profile] = None

        # Thread signals
        self.decision_received_signal.connect(self._on_live_decision)
        self.region_state_signal.connect(self._on_live_region_state)

        # Global Hotkey F12
        self.hotkey_manager = GlobalHotkeyManager(on_triggered=self._on_emergency_hotkey)
        self.hotkey_manager.start()

        # UI Setup
        self._init_ui()

        # System Tray
        self.tray_manager = SystemTrayManager(self)
        self.tray_manager.toggle_bot_requested.connect(self._toggle_bot)
        self.tray_manager.emergency_stop_requested.connect(self._on_emergency_hotkey)

        self._load_initial_profile()

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Top Bar: Profile, Backend Status, Mode, Start/Stop
        top_bar = QHBoxLayout()

        top_bar.addWidget(QLabel("Profile:"))
        self.combo_profiles = QComboBox()
        self.combo_profiles.currentIndexChanged.connect(self._on_profile_changed)
        top_bar.addWidget(self.combo_profiles)

        self.btn_new_profile = QPushButton("New")
        self.btn_new_profile.clicked.connect(self._create_new_profile)
        top_bar.addWidget(self.btn_new_profile)

        self.btn_export_zip = QPushButton("Export ZIP")
        self.btn_export_zip.clicked.connect(self._export_profile_zip)
        top_bar.addWidget(self.btn_export_zip)

        self.btn_import_zip = QPushButton("Import ZIP")
        self.btn_import_zip.clicked.connect(self._import_profile_zip)
        top_bar.addWidget(self.btn_import_zip)

        top_bar.addSpacing(15)

        # Backend Status
        top_bar.addWidget(QLabel("Action Backend:"))
        self.lbl_backend_status = QLabel("NOT_PROBED")
        self.lbl_backend_status.setStyleSheet("color: #e65100; font-weight: bold; background: #ffe0b2; padding: 4px; border-radius: 4px;")
        top_bar.addWidget(self.lbl_backend_status)

        self.btn_probe_backend = QPushButton("Probe Action...")
        self.btn_probe_backend.clicked.connect(self._probe_action_backend)
        top_bar.addWidget(self.btn_probe_backend)


        top_bar.addStretch()

        # Run Mode
        top_bar.addWidget(QLabel("Mode:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Dry-Run (0 Click)", "Shadow Mode", "Production Background Action"])
        top_bar.addWidget(self.combo_mode)

        # Start / Stop Buttons
        self.btn_start = QPushButton("START BOT")
        self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;")
        self.btn_start.clicked.connect(self._toggle_bot)
        top_bar.addWidget(self.btn_start)

        main_layout.addLayout(top_bar)

        # Tab Widget
        tabs = QTabWidget()
        tabs.addTab(self._create_targets_tab(), "1. Target Management")
        tabs.addTab(self._create_workflow_tab(), "2. Dynamic Workflow (1..N)")
        tabs.addTab(self._create_regions_tab(), "3. Regions / Tables")
        tabs.addTab(self._create_telemetry_tab(), "4. Telemetry & Decision Audit")
        main_layout.addWidget(tabs)

    def _create_targets_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        btn_bar = QHBoxLayout()
        self.btn_crop_screen = QPushButton("Crop Target From Screen (Overlay)")
        self.btn_crop_screen.clicked.connect(self._crop_target_from_screen)
        btn_bar.addWidget(self.btn_crop_screen)

        self.btn_upload_target = QPushButton("Upload Target Image File")
        self.btn_upload_target.clicked.connect(self._upload_target_file)
        btn_bar.addWidget(self.btn_upload_target)

        self.btn_calibrate_target = QPushButton("Calibrate Target...")
        self.btn_calibrate_target.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold;")
        self.btn_calibrate_target.clicked.connect(self._calibrate_selected_target)
        btn_bar.addWidget(self.btn_calibrate_target)

        self.btn_delete_target = QPushButton("Delete Target")
        self.btn_delete_target.clicked.connect(self._delete_selected_target)
        btn_bar.addWidget(self.btn_delete_target)

        btn_bar.addStretch()
        layout.addLayout(btn_bar)

        self.table_targets = QTableWidget(0, 5)
        self.table_targets.setHorizontalHeaderLabels(["Target ID", "Name", "References", "Confusers", "Calibration Status"])
        self.table_targets.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_targets.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table_targets)

        return widget

    def _create_workflow_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel("Configure dynamic step sequence: Step 1 -> Step 2 -> ... -> Step N. No hardcoded symbols.")
        layout.addWidget(info)

        self.table_steps = QTableWidget(0, 6)
        self.table_steps.setHorizontalHeaderLabels(["Step #", "Target ID", "Action", "Timeout (ms)", "Cooldown (ms)", "Max Retries"])
        self.table_steps.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_steps.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table_steps)

        btn_bar = QHBoxLayout()
        self.btn_add_step = QPushButton("Add Step")
        self.btn_add_step.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        self.btn_add_step.clicked.connect(self._add_workflow_step)
        btn_bar.addWidget(self.btn_add_step)

        self.btn_edit_step = QPushButton("Edit Step")
        self.btn_edit_step.clicked.connect(self._edit_workflow_step)
        btn_bar.addWidget(self.btn_edit_step)

        self.btn_delete_step = QPushButton("Delete Step")
        self.btn_delete_step.clicked.connect(self._delete_workflow_step)
        btn_bar.addWidget(self.btn_delete_step)

        btn_bar.addSpacing(20)

        self.btn_move_step_up = QPushButton("Move Up ▲")
        self.btn_move_step_up.clicked.connect(self._move_workflow_step_up)
        btn_bar.addWidget(self.btn_move_step_up)

        self.btn_move_step_down = QPushButton("Move Down ▼")
        self.btn_move_step_down.clicked.connect(self._move_workflow_step_down)
        btn_bar.addWidget(self.btn_move_step_down)

        btn_bar.addStretch()
        layout.addLayout(btn_bar)

        return widget

    def _create_regions_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel("Configure independent Regions/Tables (supports 1..N arbitrary layout).")
        layout.addWidget(info)

        self.table_regions = QTableWidget(0, 6)
        self.table_regions.setHorizontalHeaderLabels(["Region ID", "Name", "X", "Y", "Dimensions (WxH)", "Assigned Workflow"])
        self.table_regions.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_regions.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table_regions)

        btn_bar = QHBoxLayout()
        self.btn_roi_screen = QPushButton("Define ROI from Screen (Overlay)")
        self.btn_roi_screen.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold;")
        self.btn_roi_screen.clicked.connect(self._define_region_from_screen)
        btn_bar.addWidget(self.btn_roi_screen)

        self.btn_add_reg = QPushButton("Add Region (Manual)")
        self.btn_add_reg.clicked.connect(self._add_region)
        btn_bar.addWidget(self.btn_add_reg)

        self.btn_delete_reg = QPushButton("Delete Region")
        self.btn_delete_reg.clicked.connect(self._delete_selected_region)
        btn_bar.addWidget(self.btn_delete_reg)

        btn_bar.addStretch()
        layout.addLayout(btn_bar)

        return widget

    def _create_telemetry_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.table_telemetry = QTableWidget(0, 7)
        self.table_telemetry.setHorizontalHeaderLabels(["Time", "Region", "Target", "Geometry", "Embedding", "Decision", "Reason"])
        self.table_telemetry.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table_telemetry)

        return widget

    def _load_initial_profile(self):
        profiles = self.db.list_profiles()
        self.combo_profiles.clear()
        if profiles:
            for p in profiles:
                self.combo_profiles.addItem(p["name"], p["profile_id"])
        else:
            self._create_new_profile()

    def _create_new_profile(self):
        prof_id = f"profile_{int(time.time())}"
        new_prof = Profile(
            profile_id=prof_id,
            name=f"Profile {prof_id[-4:]}"
        )
        self.db.save_profile(new_prof)
        self.active_profile = new_prof
        if new_prof.safety_config:
            self.action_manager.safety_config = new_prof.safety_config
            self.action_manager.max_clicks_per_sec = new_prof.safety_config.max_clicks_per_second
            self.action_manager.circuit_breaker_threshold = new_prof.safety_config.circuit_breaker_threshold
        self.combo_profiles.addItem(new_prof.name, new_prof.profile_id)

    def _on_profile_changed(self, index: int):
        if index < 0:
            return
        prof_id = self.combo_profiles.currentData()
        if prof_id:
            self.vision_engine.clear_target_cache()
            self.active_profile = self.db.load_profile(prof_id)
            if self.active_profile and self.active_profile.safety_config:
                self.action_manager.safety_config = self.active_profile.safety_config
                self.action_manager.max_clicks_per_sec = self.active_profile.safety_config.max_clicks_per_second
                self.action_manager.circuit_breaker_threshold = self.active_profile.safety_config.circuit_breaker_threshold
            self._refresh_profile_views()
            self._update_backend_status_ui()

    def _refresh_profile_views(self):
        if not self.active_profile:
            return

        # 1. Refresh target table
        self.table_targets.setRowCount(0)
        for t_id, target in self.active_profile.targets.items():
            row = self.table_targets.rowCount()
            self.table_targets.insertRow(row)
            self.table_targets.setItem(row, 0, QTableWidgetItem(target.target_id))
            self.table_targets.setItem(row, 1, QTableWidgetItem(target.name))
            self.table_targets.setItem(row, 2, QTableWidgetItem(str(len(target.reference_image_paths))))
            self.table_targets.setItem(row, 3, QTableWidgetItem(str(len(target.confuser_image_paths))))

            status_item = QTableWidgetItem()
            if target.calibration:
                status_item.setText(f"CALIBRATED (Tg={target.calibration.t_g:.2f}, Te={target.calibration.t_e:.2f})")
                status_item.setForeground(QColor("#2e7d32"))
            else:
                status_item.setText("UNCALIBRATED (Action Blocked in Prod)")
                status_item.setForeground(QColor("#c62828"))
            self.table_targets.setItem(row, 4, status_item)

        # 2. Refresh region table with workflow binding
        self.table_regions.setRowCount(0)
        available_wfs = list(self.active_profile.workflows.keys()) if self.active_profile.workflows else ["default_workflow"]
        for r_id, reg in self.active_profile.regions.items():
            row = self.table_regions.rowCount()
            self.table_regions.insertRow(row)
            self.table_regions.setItem(row, 0, QTableWidgetItem(reg.region_id))
            self.table_regions.setItem(row, 1, QTableWidgetItem(reg.name))
            self.table_regions.setItem(row, 2, QTableWidgetItem(str(reg.x)))
            self.table_regions.setItem(row, 3, QTableWidgetItem(str(reg.y)))
            self.table_regions.setItem(row, 4, QTableWidgetItem(f"{reg.w}x{reg.h}"))

            combo_wf = QComboBox()
            for wf_id in available_wfs:
                combo_wf.addItem(wf_id, wf_id)
            if reg.workflow_id and reg.workflow_id in available_wfs:
                combo_wf.setCurrentText(reg.workflow_id)
            combo_wf.currentTextChanged.connect(lambda text, r=reg.region_id: self._on_region_workflow_changed(r, text))
            self.table_regions.setCellWidget(row, 5, combo_wf)

        # 3. Refresh workflow steps
        self._refresh_workflow_views()

    def _refresh_workflow_views(self):
        self.table_steps.setRowCount(0)
        if not self.active_profile:
            return
        wf = self.active_profile.workflows.get("default_workflow")
        if not wf:
            return
        for i, step in enumerate(wf.steps):
            row = self.table_steps.rowCount()
            self.table_steps.insertRow(row)
            self.table_steps.setItem(row, 0, QTableWidgetItem(f"Step {i + 1}"))
            self.table_steps.setItem(row, 1, QTableWidgetItem(step.target_id))
            self.table_steps.setItem(row, 2, QTableWidgetItem(step.action_type.value if hasattr(step.action_type, 'value') else str(step.action_type)))
            self.table_steps.setItem(row, 3, QTableWidgetItem(str(step.timeout_ms)))
            self.table_steps.setItem(row, 4, QTableWidgetItem(str(step.cooldown_ms)))
            self.table_steps.setItem(row, 5, QTableWidgetItem(str(step.retry_limit)))

    def _on_region_workflow_changed(self, region_id: str, workflow_id: str):
        if self.active_profile and region_id in self.active_profile.regions:
            self.active_profile.regions[region_id].workflow_id = workflow_id
            self.db.save_profile(self.active_profile)
            logger.info(f"Region '{region_id}' bound to workflow '{workflow_id}'")

    def _probe_action_backend(self):
        """Runs interactive 2-stage capability probe for background action."""
        dlg = SurfaceVerificationDialog(self.action_manager, self)
        dlg.exec()
        self._update_backend_status_ui()

    def _update_backend_status_ui(self):
        is_supp = getattr(self.action_manager, "is_supported", False)
        is_surf = getattr(self.action_manager, "is_surface_verified", False)

        if is_supp and is_surf:
            self.lbl_backend_status.setText("SURFACE_VERIFIED (READY)")
            self.lbl_backend_status.setStyleSheet("color: #1b5e20; font-weight: bold; background: #c8e6c9; padding: 4px; border-radius: 4px;")
        elif is_supp:
            self.lbl_backend_status.setText("PROTOCOL_VERIFIED (SURFACE_UNVERIFIED)")
            self.lbl_backend_status.setStyleSheet("color: #e65100; font-weight: bold; background: #ffe0b2; padding: 4px; border-radius: 4px;")
        else:
            self.lbl_backend_status.setText("BACKGROUND_ACTION_UNSUPPORTED")
            self.lbl_backend_status.setStyleSheet("color: #b71c1c; font-weight: bold; background: #ffcdd2; padding: 4px; border-radius: 4px;")

        self.tray_manager.update_status(
            is_running=self.runner.is_running if self.runner else False,
            profile_name=self.active_profile.name if self.active_profile else "None",
            backend_status=self.action_manager.support_status_message
        )


    def _export_profile_zip(self):
        if not self.active_profile:
            return
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Profile ZIP", f"{self.active_profile.profile_id}.zip", "ZIP Archives (*.zip)")
        if file_path:
            ProfileBundleManager.export_profile_to_zip(self.active_profile, file_path)
            QMessageBox.information(self, "Export Successful", f"Profile exported to {file_path}")

    def _import_profile_zip(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Profile ZIP", "", "ZIP Archives (*.zip)")
        if file_path:
            imported_prof, status = ProfileBundleManager.safe_import_profile_from_zip(file_path)
            if imported_prof:
                self.db.save_profile(imported_prof)
                self.active_profile = imported_prof
                self.combo_profiles.addItem(imported_prof.name, imported_prof.profile_id)
                self.combo_profiles.setCurrentIndex(self.combo_profiles.count() - 1)
                QMessageBox.information(self, "Import Successful", f"Profile '{imported_prof.name}' imported successfully.")
            else:
                QMessageBox.critical(self, "Import Failed", status)

    def _crop_target_from_screen(self):
        frame, _ = self.capture_manager.grab()
        if frame is None:
            QMessageBox.critical(self, "Capture Error", "Failed to grab screen frame for crop overlay.")
            return

        desktop_offset = self.capture_manager.get_desktop_offset()
        self.overlay = ScreenCropOverlay(frame, desktop_offset=desktop_offset, mode="target")
        self.overlay.target_cropped.connect(self._on_target_cropped)
        self.overlay.show()

    def _on_target_cropped(self, crop: np.ndarray):
        is_separable, reason = check_geometry_separability(crop)
        if not is_separable:
            QMessageBox.warning(
                self,
                "TARGET_NOT_GEOMETRICALLY_SEPARABLE",
                f"Cropped region rejected:\n{reason}\nTarget must have distinct contour/edge structure."
            )
            return

        os.makedirs("data/targets", exist_ok=True)
        t_id = f"target_{len(self.active_profile.targets) + 1}"
        save_path = os.path.abspath(f"data/targets/{t_id}.png")
        cv2.imwrite(save_path, crop)

        target = Target(
            target_id=t_id,
            name=f"Cropped Target {len(self.active_profile.targets) + 1}",
            reference_image_paths=[save_path],
            calibration=None
        )
        self.active_profile.targets[t_id] = target
        self.db.save_profile(self.active_profile)
        self._refresh_profile_views()
        QMessageBox.information(
            self,
            "Target Saved (Uncalibrated)",
            f"Target '{target.name}' saved.\nStatus: UNCALIBRATED.\nPlease click 'Calibrate Target...' to run empirical calibration before running in Production."
        )

    def _upload_target_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Target Image", "", "Images (*.png *.jpg *.bmp)")
        if file_path:
            img = cv2.imread(file_path)
            if img is None:
                QMessageBox.critical(self, "Invalid Image", "Cannot read image file.")
                return

            is_separable, reason = check_geometry_separability(img)
            if not is_separable:
                QMessageBox.warning(
                    self,
                    "TARGET_NOT_GEOMETRICALLY_SEPARABLE",
                    f"Uploaded image rejected:\n{reason}\nTarget must have distinct contour/edge structure."
                )
                return

            t_id = f"target_{len(self.active_profile.targets) + 1}"
            target = Target(
                target_id=t_id,
                name=os.path.basename(file_path),
                reference_image_paths=[file_path],
                calibration=None
            )
            self.active_profile.targets[t_id] = target
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()
            QMessageBox.information(
                self,
                "Target Added (Uncalibrated)",
                f"Target '{target.name}' added.\nStatus: UNCALIBRATED.\nPlease click 'Calibrate Target...' to run empirical calibration before running in Production."
            )

    def _calibrate_selected_target(self):
        if not self.active_profile:
            return
        row = self.table_targets.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No Target Selected", "Please select a target from the table to calibrate.")
            return
        t_id = self.table_targets.item(row, 0).text()
        target = self.active_profile.targets.get(t_id)
        if not target:
            return

        dlg = TargetCalibrationDialog(
            target=target,
            profile=self.active_profile,
            onnx_verifier=self.onnx_verifier,
            geo_verifier=self.geo_verifier,
            parent=self
        )
        if dlg.exec() == QDialog.Accepted and dlg.calibrated_profile:
            target.calibration = dlg.calibrated_profile
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()
            QMessageBox.information(
                self,
                "Calibration Saved",
                f"Empirical calibration profile applied for target '{target.name}'.\n"
                f"Tg={dlg.calibrated_profile.t_g:.3f}, Te={dlg.calibrated_profile.t_e:.3f}, Msafe={dlg.calibrated_profile.m_safe:.3f}\n"
                f"Separation Gap: +{dlg.calibrated_profile.separation_gap:.3f}"
            )

    def _delete_selected_target(self):
        if not self.active_profile:
            return
        row = self.table_targets.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No Target Selected", "Please select a target from the table to delete.")
            return
        t_id = self.table_targets.item(row, 0).text()
        reply = QMessageBox.question(self, "Confirm Delete", f"Delete target '{t_id}'?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.active_profile.targets.pop(t_id, None)
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()

    def _get_or_create_default_workflow(self) -> Workflow:
        wf = self.active_profile.workflows.get("default_workflow")
        if not wf:
            wf = Workflow(workflow_id="default_workflow", name="Default Workflow")
            self.active_profile.workflows["default_workflow"] = wf
        return wf

    def _add_workflow_step(self):
        if not self.active_profile or not self.active_profile.targets:
            QMessageBox.warning(self, "No Targets", "Add at least one target before creating workflow steps.")
            return

        dlg = WorkflowStepDialog(self.active_profile, parent=self)
        if dlg.exec() == QDialog.Accepted:
            wf = self._get_or_create_default_workflow()
            new_step = dlg.get_step(step_index=len(wf.steps))
            wf.steps.append(new_step)
            self.db.save_profile(self.active_profile)
            self._refresh_workflow_views()

    def _edit_workflow_step(self):
        if not self.active_profile:
            return
        row = self.table_steps.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No Step Selected", "Please select a step to edit.")
            return
        wf = self._get_or_create_default_workflow()
        if row >= len(wf.steps):
            return
        step = wf.steps[row]
        dlg = WorkflowStepDialog(self.active_profile, step=step, parent=self)
        if dlg.exec() == QDialog.Accepted:
            updated_step = dlg.get_step(step_index=row)
            wf.steps[row] = updated_step
            self.db.save_profile(self.active_profile)
            self._refresh_workflow_views()

    def _delete_workflow_step(self):
        if not self.active_profile:
            return
        row = self.table_steps.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No Step Selected", "Please select a step to delete.")
            return
        wf = self._get_or_create_default_workflow()
        if row < len(wf.steps):
            wf.steps.pop(row)
            for i, s in enumerate(wf.steps):
                s.step_index = i
            self.db.save_profile(self.active_profile)
            self._refresh_workflow_views()

    def _move_workflow_step_up(self):
        if not self.active_profile:
            return
        row = self.table_steps.currentRow()
        if row <= 0:
            return
        wf = self._get_or_create_default_workflow()
        if row < len(wf.steps):
            wf.steps[row - 1], wf.steps[row] = wf.steps[row], wf.steps[row - 1]
            for i, s in enumerate(wf.steps):
                s.step_index = i
            self.db.save_profile(self.active_profile)
            self._refresh_workflow_views()
            self.table_steps.selectRow(row - 1)

    def _move_workflow_step_down(self):
        if not self.active_profile:
            return
        row = self.table_steps.currentRow()
        wf = self._get_or_create_default_workflow()
        if row < 0 or row >= len(wf.steps) - 1:
            return
        wf.steps[row], wf.steps[row + 1] = wf.steps[row + 1], wf.steps[row]
        for i, s in enumerate(wf.steps):
            s.step_index = i
        self.db.save_profile(self.active_profile)
        self._refresh_workflow_views()
        self.table_steps.selectRow(row + 1)

    def _define_region_from_screen(self):
        frame, _ = self.capture_manager.grab()
        if frame is None:
            QMessageBox.critical(self, "Capture Error", "Failed to grab screen frame for ROI definition.")
            return

        desktop_offset = self.capture_manager.get_desktop_offset()
        self.region_overlay = ScreenCropOverlay(frame, desktop_offset=desktop_offset, mode="region")
        self.region_overlay.region_selected.connect(self._on_screen_region_selected)
        self.region_overlay.show()

    def _on_screen_region_selected(self, x: int, y: int, w: int, h: int):
        if not self.active_profile:
            return
        name, ok = QInputDialog.getText(self, "New Region ROI", "Enter Region/Table Name:", text=f"Table {len(self.active_profile.regions) + 1}")
        if not ok or not name.strip():
            return
        r_num = len(self.active_profile.regions) + 1
        r_id = f"table_{r_num}"
        reg = RegionModel(region_id=r_id, name=name.strip(), x=x, y=y, w=w, h=h)
        self.active_profile.regions[r_id] = reg
        self.db.save_profile(self.active_profile)
        self._refresh_profile_views()
        QMessageBox.information(self, "Region Defined", f"Region '{reg.name}' defined at screen coordinates ({x}, {y}, {w}x{h}).")

    def _add_region(self):
        if not self.active_profile:
            return
        r_num = len(self.active_profile.regions) + 1
        r_id = f"table_{r_num}"
        reg = RegionModel(region_id=r_id, name=f"Table {r_num}", x=100 + (r_num * 50), y=100, w=200, h=200)
        self.active_profile.regions[r_id] = reg
        self.db.save_profile(self.active_profile)
        self._refresh_profile_views()

    def _delete_selected_region(self):
        if not self.active_profile:
            return
        row = self.table_regions.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No Region Selected", "Please select a region from the table to delete.")
            return
        r_id = self.table_regions.item(row, 0).text()
        reply = QMessageBox.question(self, "Confirm Delete", f"Delete region '{r_id}'?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.active_profile.regions.pop(r_id, None)
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()

    def _on_emergency_hotkey(self):
        """Triggered immediately when user presses F12 system-wide."""
        self.action_manager.trigger_emergency_stop()
        if self.runner and self.runner.is_running:
            self.runner.stop()
        self.btn_start.setText("START BOT")
        self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;")
        self.tray_manager.update_status(
            is_running=False,
            profile_name=self.active_profile.name if self.active_profile else "None",
            backend_status="EMERGENCY_STOPPED"
        )
        logger.critical("F12 Hotkey Emergency Stop executed.")

    def _toggle_bot(self):
        """Starts or stops the live bot execution thread."""
        if not self.runner or not self.runner.is_running:
            mode = self.combo_mode.currentText()
            is_dry_run = "Production" not in mode

            if "Production" in mode:
                if not getattr(self.action_manager, "is_supported", False) or not getattr(self.action_manager, "is_surface_verified", False):
                    QMessageBox.critical(
                        self,
                        "Execution Blocked (Fail-Closed)",
                        "Cannot start in Production mode: Background action surface compatibility has not been verified.\n"
                        "Run 'Probe Action...' -> 2-Stage Surface Verification Probe first or use Dry-Run / Shadow mode."
                    )
                    return

                if self.active_profile:
                    for wf in self.active_profile.workflows.values():
                        for step in wf.steps:
                            target = self.active_profile.targets.get(step.target_id)
                            if not target or not target.calibration:
                                QMessageBox.critical(
                                    self,
                                    "Execution Blocked (Fail-Closed)",
                                    f"Cannot start in Production mode: Target '{step.target_id}' is UNCALIBRATED.\n"
                                    "Production mode requires verified CalibrationProfile. Run 'Calibrate Target...' first."
                                )
                                return

                    # Contract requirement: Every region must have an explicit assigned workflow with >= 1 steps
                    for r_id, reg in self.active_profile.regions.items():
                        if not reg.workflow_id:
                            QMessageBox.critical(
                                self,
                                "Execution Blocked (Fail-Closed)",
                                f"Cannot start in Production mode: Region '{reg.name or r_id}' has no assigned workflow.\n"
                                "Assign an explicit workflow with valid steps before starting in Production."
                            )
                            return
                        if reg.workflow_id not in self.active_profile.workflows:
                            QMessageBox.critical(
                                self,
                                "Execution Blocked (Fail-Closed)",
                                f"Cannot start in Production mode: Region '{reg.name or r_id}' assigned workflow '{reg.workflow_id}' not found in profile."
                            )
                            return
                        wf = self.active_profile.workflows[reg.workflow_id]
                        if not wf.steps:
                            QMessageBox.critical(
                                self,
                                "Execution Blocked (Fail-Closed)",
                                f"Cannot start in Production mode: Workflow '{wf.name or reg.workflow_id}' assigned to region '{reg.name or r_id}' has 0 steps."
                            )
                            return

            if not self.active_profile or not self.active_profile.regions:
                QMessageBox.warning(self, "No Regions", "Configure at least 1 region before starting bot.")
                return

            # Register regions with their designated workflows
            self.ledger = SessionLedger()
            default_wf = self.active_profile.workflows.get("default_workflow")

            for r_id, reg in self.active_profile.regions.items():
                wf_to_use = None
                if reg.workflow_id and reg.workflow_id in self.active_profile.workflows:
                    wf_to_use = self.active_profile.workflows[reg.workflow_id]
                elif default_wf:
                    wf_to_use = default_wf
                else:
                    wf_to_use = Workflow(workflow_id=f"wf_{r_id}", name=f"WF {reg.name}")
                inst = self.ledger.register_region(reg, wf_to_use)
                inst.start_workflow()

            # Start Runner Thread
            self.action_manager.reset_emergency_stop()
            self.runner = BotRuntimeRunner(
                profile=self.active_profile,
                capture_manager=self.capture_manager,
                vision_engine=self.vision_engine,
                action_manager=self.action_manager,
                ledger=self.ledger,
                telemetry_logger=getattr(self, "telemetry_logger", None),
                is_dry_run=is_dry_run,
                is_production="Production" in mode,
                on_decision_callback=self.decision_received_signal.emit,
                on_state_callback=self.region_state_signal.emit
            )
            self.runner.start()

            self.btn_start.setText("STOP BOT")
            self.btn_start.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; padding: 6px 16px;")
            self.tray_manager.update_status(
                is_running=True,
                profile_name=self.active_profile.name,
                backend_status="DRY_RUN" if is_dry_run else "BACKGROUND_ACTIVE"
            )
        else:
            self.runner.stop()
            self.btn_start.setText("START BOT")
            self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;")
            self.tray_manager.update_status(
                is_running=False,
                profile_name=self.active_profile.name if self.active_profile else "None",
                backend_status=self.action_manager.support_status_message
            )

    @Slot(object)
    def _on_live_decision(self, res: DecisionResult):
        # Insert row in telemetry tab
        row = self.table_telemetry.rowCount()
        if row > 200:
            self.table_telemetry.removeRow(0)
            row -= 1

        self.table_telemetry.insertRow(row)
        t_str = time.strftime('%H:%M:%S', time.localtime(res.timestamp))
        self.table_telemetry.setItem(row, 0, QTableWidgetItem(t_str))
        self.table_telemetry.setItem(row, 1, QTableWidgetItem(res.region_id or "-"))
        self.table_telemetry.setItem(row, 2, QTableWidgetItem(res.target_id or "-"))
        self.table_telemetry.setItem(row, 3, QTableWidgetItem(f"{res.geometry_score:.3f}"))
        self.table_telemetry.setItem(row, 4, QTableWidgetItem(f"{res.embedding_similarity:.3f}"))

        dec_item = QTableWidgetItem(res.decision.value)
        if res.decision == DecisionClass.MATCH:
            dec_item.setForeground(QColor("#2e7d32"))
        elif res.decision == DecisionClass.NON_MATCH:
            dec_item.setForeground(QColor("#c62828"))
        self.table_telemetry.setItem(row, 5, dec_item)
        self.table_telemetry.setItem(row, 6, QTableWidgetItem(res.reason))

    @Slot(str, str, int)
    def _on_live_region_state(self, region_id: str, state_str: str, step_idx: int):
        for row in range(self.table_regions.rowCount()):
            item = self.table_regions.item(row, 0)
            if item and item.text() == region_id:
                name_item = self.table_regions.item(row, 1)
                if name_item:
                    name_item.setToolTip(f"Live State: {state_str} (Step {step_idx + 1})")
                break

    def closeEvent(self, event):
        """Ensures background threads and hooks are cleanly stopped on window exit."""
        if self.runner and self.runner.is_running:
            self.runner.stop()
        if self.telemetry_logger:
            self.telemetry_logger.close()
        self.hotkey_manager.stop()
        self.capture_manager.close()
        event.accept()
