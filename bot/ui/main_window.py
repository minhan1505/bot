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
from typing import Optional, Dict, Tuple, List

logger = logging.getLogger(__name__)

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QLabel, QComboBox, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QGroupBox, QLineEdit, QSpinBox, QDoubleSpinBox,
    QHeaderView, QInputDialog, QDialog, QSplitter, QCheckBox, QRadioButton, QButtonGroup
)
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QIcon, QColor

from bot.core.models import Profile, Target, RegionModel, Workflow, WorkflowStep, DecisionResult, DecisionClass, CalibrationProfile, SafetyConfig
from bot.core.database import Database
from bot.core.bundle import ProfileBundleManager
from bot.core.hotkey import GlobalHotkeyManager
from bot.capture.manager import CaptureManager
from bot.ui.crop_overlay import ScreenCropOverlay
from bot.ui.tray import SystemTrayManager
from bot.ui.calibration_dialog import TargetCalibrationDialog
from bot.ui.step_dialog import WorkflowStepDialog
from bot.ui.surface_dialog import SurfaceVerificationDialog
from bot.ui.test_target_dialog import TestTargetDialog
from bot.ui.target_dialog import TargetDetailsDialog
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
    """PySide6 Desktop Application MainWindow with complete V2.3 functional pipeline."""
    decision_received_signal = Signal(object)
    region_state_signal = Signal(str, str, int)
    shadow_evidence_signal = Signal(dict)

    def __init__(self, db_path: str = "bot_data.db"):
        super().__init__()
        self.setWindowTitle("BotAutoClick V2.3 — Vision Automation Suite")
        self.resize(1200, 850)

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
        self.active_workflow_id: str = "default_workflow"

        # Telemetry & Runtime Statistics (FC-11)
        self.counter_match = 0
        self.counter_unknown = 0
        self.counter_non_match = 0
        self.counter_reject = 0
        self.counter_total = 0
        self.latency_buffer = []

        # Thread signals
        self.decision_received_signal.connect(self._on_live_decision)
        self.region_state_signal.connect(self._on_live_region_state)
        self.shadow_evidence_signal.connect(self._on_shadow_evidence)

        # Global Hotkey (FC-01)
        self.hotkey_manager = GlobalHotkeyManager(hotkey_str="F12", on_triggered=self._on_emergency_hotkey)
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

        # Row 1: Profile CRUD & Snapshots (FC-04, FC-12)
        prof_bar = QHBoxLayout()
        prof_bar.addWidget(QLabel("<b>Profile:</b>"))
        self.combo_profiles = QComboBox()
        self.combo_profiles.currentIndexChanged.connect(self._on_profile_changed)
        prof_bar.addWidget(self.combo_profiles)

        self.btn_new_profile = QPushButton("New")
        self.btn_new_profile.clicked.connect(self._create_new_profile)
        prof_bar.addWidget(self.btn_new_profile)

        self.btn_rename_profile = QPushButton("Rename")
        self.btn_rename_profile.clicked.connect(self._rename_active_profile)
        prof_bar.addWidget(self.btn_rename_profile)

        self.btn_clone_profile = QPushButton("Clone")
        self.btn_clone_profile.clicked.connect(self._clone_active_profile)
        prof_bar.addWidget(self.btn_clone_profile)

        self.btn_delete_profile = QPushButton("Delete")
        self.btn_delete_profile.clicked.connect(self._delete_active_profile)
        prof_bar.addWidget(self.btn_delete_profile)

        prof_bar.addSpacing(10)

        self.btn_snapshot = QPushButton("Snapshot")
        self.btn_snapshot.clicked.connect(self._create_profile_snapshot)
        prof_bar.addWidget(self.btn_snapshot)

        self.btn_restore = QPushButton("Restore...")
        self.btn_restore.clicked.connect(self._restore_profile_snapshot)
        prof_bar.addWidget(self.btn_restore)

        prof_bar.addSpacing(10)

        self.btn_export_zip = QPushButton("Export ZIP")
        self.btn_export_zip.clicked.connect(self._export_profile_zip)
        prof_bar.addWidget(self.btn_export_zip)

        self.btn_import_zip = QPushButton("Import ZIP")
        self.btn_import_zip.clicked.connect(self._import_profile_zip)
        prof_bar.addWidget(self.btn_import_zip)

        prof_bar.addStretch()
        main_layout.addLayout(prof_bar)

        # Row 2: Hardware & Runtime Control (FC-01, FC-02, FC-09)
        ctl_bar = QHBoxLayout()

        # Monitor enumeration (FC-02)
        ctl_bar.addWidget(QLabel("Monitor:"))
        self.combo_monitor = QComboBox()
        self._populate_monitors()
        self.combo_monitor.currentIndexChanged.connect(self._on_monitor_changed)
        ctl_bar.addWidget(self.combo_monitor)

        # Emergency Stop Hotkey (FC-01)
        ctl_bar.addWidget(QLabel("Stop Hotkey:"))
        self.combo_hotkey = QComboBox()
        self.combo_hotkey.addItems(["F12", "F11", "F10", "F9", "F8", "Ctrl+F12", "Ctrl+F10"])
        self.combo_hotkey.setCurrentText("F12")
        self.combo_hotkey.currentTextChanged.connect(self._on_hotkey_changed)
        ctl_bar.addWidget(self.combo_hotkey)

        ctl_bar.addSpacing(10)

        # Backend Status
        ctl_bar.addWidget(QLabel("Action Backend:"))
        self.lbl_backend_status = QLabel("NOT_PROBED")
        self.lbl_backend_status.setStyleSheet("color: #e65100; font-weight: bold; background: #ffe0b2; padding: 4px; border-radius: 4px;")
        ctl_bar.addWidget(self.lbl_backend_status)

        self.btn_probe_backend = QPushButton("Probe Action...")
        self.btn_probe_backend.clicked.connect(self._probe_action_backend)
        ctl_bar.addWidget(self.btn_probe_backend)

        ctl_bar.addStretch()

        # Run Mode (FC-09)
        ctl_bar.addWidget(QLabel("Mode:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Dry-Run (0 Click)", "Shadow Mode (Audit Only)", "Production Background Action"])
        ctl_bar.addWidget(self.combo_mode)

        # Start / Stop Buttons
        self.btn_start = QPushButton("START BOT")
        self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;")
        self.btn_start.clicked.connect(self._toggle_bot)
        ctl_bar.addWidget(self.btn_start)

        main_layout.addLayout(ctl_bar)

        # Tab Widget
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_targets_tab(), "1. Target Management")
        self.tabs.addTab(self._create_workflow_tab(), "2. Dynamic Workflow (1..N)")
        self.tabs.addTab(self._create_regions_tab(), "3. Regions / Tables")
        self.tabs.addTab(self._create_telemetry_tab(), "4. Telemetry & Decision Audit")
        self.tabs.addTab(self._create_safety_tab(), "5. Safety Controls & Settings")
        main_layout.addWidget(self.tabs)

    def _create_targets_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        btn_bar = QHBoxLayout()
        self.btn_crop_screen = QPushButton("Crop From Screen (Overlay)")
        self.btn_crop_screen.clicked.connect(self._crop_target_from_screen)
        btn_bar.addWidget(self.btn_crop_screen)

        self.btn_upload_target = QPushButton("Upload Image File...")
        self.btn_upload_target.clicked.connect(self._upload_target_file)
        btn_bar.addWidget(self.btn_upload_target)

        self.btn_batch_import = QPushButton("Batch Import Folder...")
        self.btn_batch_import.clicked.connect(self._batch_import_targets)
        btn_bar.addWidget(self.btn_batch_import)

        btn_bar.addSpacing(10)

        self.btn_target_details = QPushButton("Details / Preview...")
        self.btn_target_details.clicked.connect(self._open_target_details)
        btn_bar.addWidget(self.btn_target_details)

        self.btn_test_target = QPushButton("Test Target...")
        self.btn_test_target.setStyleSheet("background-color: #00796b; color: white; font-weight: bold;")
        self.btn_test_target.clicked.connect(self._test_selected_target)
        btn_bar.addWidget(self.btn_test_target)

        self.btn_calibrate_target = QPushButton("Calibrate...")
        self.btn_calibrate_target.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold;")
        self.btn_calibrate_target.clicked.connect(self._calibrate_selected_target)
        btn_bar.addWidget(self.btn_calibrate_target)

        btn_bar.addSpacing(10)

        self.btn_toggle_enabled = QPushButton("Toggle Enable")
        self.btn_toggle_enabled.clicked.connect(self._toggle_target_enabled)
        btn_bar.addWidget(self.btn_toggle_enabled)

        self.btn_delete_target = QPushButton("Delete Target")
        self.btn_delete_target.clicked.connect(self._delete_selected_target)
        btn_bar.addWidget(self.btn_delete_target)

        btn_bar.addStretch()
        layout.addLayout(btn_bar)

        self.table_targets = QTableWidget(0, 6)
        self.table_targets.setHorizontalHeaderLabels(["Target ID", "Enabled", "Name", "References", "Confusers", "Calibration Status"])
        self.table_targets.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_targets.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_targets.doubleClicked.connect(self._open_target_details)
        layout.addWidget(self.table_targets)

        return widget

    def _create_workflow_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        wf_selector_bar = QHBoxLayout()
        wf_selector_bar.addWidget(QLabel("<b>Active Workflow:</b>"))
        self.combo_workflows = QComboBox()
        self.combo_workflows.currentIndexChanged.connect(self._on_active_workflow_changed)
        wf_selector_bar.addWidget(self.combo_workflows)

        self.btn_new_workflow = QPushButton("New Workflow")
        self.btn_new_workflow.clicked.connect(self._create_new_workflow)
        wf_selector_bar.addWidget(self.btn_new_workflow)

        self.btn_rename_workflow = QPushButton("Rename")
        self.btn_rename_workflow.clicked.connect(self._rename_active_workflow)
        wf_selector_bar.addWidget(self.btn_rename_workflow)

        self.btn_clone_workflow = QPushButton("Clone")
        self.btn_clone_workflow.clicked.connect(self._clone_active_workflow)
        wf_selector_bar.addWidget(self.btn_clone_workflow)

        self.btn_delete_workflow = QPushButton("Delete")
        self.btn_delete_workflow.clicked.connect(self._delete_active_workflow)
        wf_selector_bar.addWidget(self.btn_delete_workflow)

        wf_selector_bar.addStretch()
        layout.addLayout(wf_selector_bar)

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

        # Scan Scope / ROI Configuration Group (FC-03)
        roi_group = QGroupBox("Scan Scope / Screen ROI")
        roi_layout = QHBoxLayout(roi_group)

        self.radio_roi_full = QRadioButton("Full Screen / Monitor")
        self.radio_roi_custom = QRadioButton("Custom ROI Scope")
        self.radio_roi_full.setChecked(True)
        self.bg_roi = QButtonGroup(self)
        self.bg_roi.addButton(self.radio_roi_full)
        self.bg_roi.addButton(self.radio_roi_custom)
        roi_layout.addWidget(self.radio_roi_full)
        roi_layout.addWidget(self.radio_roi_custom)

        roi_layout.addWidget(QLabel("X:"))
        self.spin_roi_x = QSpinBox()
        self.spin_roi_x.setRange(-10000, 10000)
        self.spin_roi_x.setEnabled(False)
        roi_layout.addWidget(self.spin_roi_x)

        roi_layout.addWidget(QLabel("Y:"))
        self.spin_roi_y = QSpinBox()
        self.spin_roi_y.setRange(-10000, 10000)
        self.spin_roi_y.setEnabled(False)
        roi_layout.addWidget(self.spin_roi_y)

        roi_layout.addWidget(QLabel("W:"))
        self.spin_roi_w = QSpinBox()
        self.spin_roi_w.setRange(10, 20000)
        self.spin_roi_w.setValue(1920)
        self.spin_roi_w.setEnabled(False)
        roi_layout.addWidget(self.spin_roi_w)

        roi_layout.addWidget(QLabel("H:"))
        self.spin_roi_h = QSpinBox()
        self.spin_roi_h.setRange(10, 20000)
        self.spin_roi_h.setValue(1080)
        self.spin_roi_h.setEnabled(False)
        roi_layout.addWidget(self.spin_roi_h)

        self.radio_roi_custom.toggled.connect(lambda chk: self._on_roi_mode_toggled(chk))

        self.btn_roi_screen_overlay = QPushButton("Set ROI from Screen")
        self.btn_roi_screen_overlay.clicked.connect(self._define_roi_from_screen)
        roi_layout.addWidget(self.btn_roi_screen_overlay)

        self.btn_apply_roi = QPushButton("Apply ROI")
        self.btn_apply_roi.clicked.connect(self._apply_roi_scope)
        roi_layout.addWidget(self.btn_apply_roi)

        self.btn_reset_roi = QPushButton("Reset to Full Scan")
        self.btn_reset_roi.clicked.connect(self._reset_roi_to_full)
        roi_layout.addWidget(self.btn_reset_roi)

        roi_layout.addStretch()
        layout.addWidget(roi_group)

        # Regions / Tables (1..N)
        layout.addWidget(QLabel("<b>Configured Regions / Tables:</b>"))
        self.table_regions = QTableWidget(0, 6)
        self.table_regions.setHorizontalHeaderLabels(["Region ID", "Name", "X", "Y", "Dimensions (WxH)", "Assigned Workflow"])
        self.table_regions.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_regions.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table_regions)

        btn_bar = QHBoxLayout()
        self.btn_reg_screen = QPushButton("Define Region from Screen (Overlay)")
        self.btn_reg_screen.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold;")
        self.btn_reg_screen.clicked.connect(self._define_region_from_screen)
        btn_bar.addWidget(self.btn_reg_screen)

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

        # Metrics Counter Badges Bar (FC-11)
        badge_bar = QHBoxLayout()
        self.lbl_badge_match = QLabel("MATCH: 0")
        self.lbl_badge_match.setStyleSheet("background: #c8e6c9; color: #1b5e20; font-weight: bold; padding: 4px 8px; border-radius: 4px;")
        badge_bar.addWidget(self.lbl_badge_match)

        self.lbl_badge_unknown = QLabel("UNKNOWN: 0")
        self.lbl_badge_unknown.setStyleSheet("background: #ffe0b2; color: #e65100; font-weight: bold; padding: 4px 8px; border-radius: 4px;")
        badge_bar.addWidget(self.lbl_badge_unknown)

        self.lbl_badge_non_match = QLabel("NON-MATCH: 0")
        self.lbl_badge_non_match.setStyleSheet("background: #ffcdd2; color: #b71c1c; font-weight: bold; padding: 4px 8px; border-radius: 4px;")
        badge_bar.addWidget(self.lbl_badge_non_match)

        self.lbl_badge_reject = QLabel("REJECT: 0")
        self.lbl_badge_reject.setStyleSheet("background: #e1bee7; color: #4a148c; font-weight: bold; padding: 4px 8px; border-radius: 4px;")
        badge_bar.addWidget(self.lbl_badge_reject)

        self.lbl_badge_total = QLabel("TOTAL: 0")
        self.lbl_badge_total.setStyleSheet("background: #cfd8dc; color: #263238; font-weight: bold; padding: 4px 8px; border-radius: 4px;")
        badge_bar.addWidget(self.lbl_badge_total)

        badge_bar.addSpacing(20)

        self.lbl_latency_summary = QLabel("Latency: P50: 0.0ms | P95: 0.0ms | P99: 0.0ms | Max: 0.0ms")
        self.lbl_latency_summary.setStyleSheet("font-weight: bold; color: #0d47a1;")
        badge_bar.addWidget(self.lbl_latency_summary)

        badge_bar.addStretch()
        layout.addLayout(badge_bar)

        self.table_telemetry = QTableWidget(0, 7)
        self.table_telemetry.setHorizontalHeaderLabels(["Time", "Region", "Target", "Geometry", "Embedding", "Decision", "Reason"])
        self.table_telemetry.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table_telemetry)

        return widget

    def _create_safety_tab(self) -> QWidget:
        """Safety Bounds & Anti-Runaway Controls Panel (FC-10)."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("Anti-Runaway & Safety Bounds (Persisted per Profile)")
        form_layout = QVBoxLayout(group)

        # Max Clicks Per Second
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Max Clicks Per Second (Rate Limit):"))
        self.spin_max_cps = QDoubleSpinBox()
        self.spin_max_cps.setRange(0.1, 100.0)
        self.spin_max_cps.setValue(10.0)
        self.spin_max_cps.setSingleStep(1.0)
        row1.addWidget(self.spin_max_cps)
        row1.addStretch()
        form_layout.addLayout(row1)

        # Circuit Breaker Threshold & Window
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Circuit Breaker Action Threshold:"))
        self.spin_cb_thresh = QSpinBox()
        self.spin_cb_thresh.setRange(1, 500)
        self.spin_cb_thresh.setValue(30)
        row2.addWidget(self.spin_cb_thresh)

        row2.addWidget(QLabel("Window Duration (sec):"))
        self.spin_cb_window = QDoubleSpinBox()
        self.spin_cb_window.setRange(0.5, 60.0)
        self.spin_cb_window.setValue(5.0)
        row2.addWidget(self.spin_cb_window)
        row2.addStretch()
        form_layout.addLayout(row2)

        # Max Clicks Per Region & Max Total Clicks
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Max Clicks Per Region Quota:"))
        self.spin_max_per_reg = QSpinBox()
        self.spin_max_per_reg.setRange(1, 100000)
        self.spin_max_per_reg.setValue(100)
        row3.addWidget(self.spin_max_per_reg)

        row3.addWidget(QLabel("Max Total Clicks Session Quota:"))
        self.spin_max_total = QSpinBox()
        self.spin_max_total.setRange(1, 1000000)
        self.spin_max_total.setValue(1000)
        row3.addWidget(self.spin_max_total)
        row3.addStretch()
        form_layout.addLayout(row3)

        # Auto-Stop Runtime Duration (FC-10)
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Auto-Stop After Runtime (Minutes, 0 = Disabled):"))
        self.spin_auto_stop = QDoubleSpinBox()
        self.spin_auto_stop.setRange(0.0, 1440.0)
        self.spin_auto_stop.setValue(0.0)
        self.spin_auto_stop.setSingleStep(5.0)
        row4.addWidget(self.spin_auto_stop)
        row4.addStretch()
        form_layout.addLayout(row4)

        form_layout.addSpacing(15)

        self.btn_save_safety = QPushButton("Save Safety Settings to Profile")
        self.btn_save_safety.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;")
        self.btn_save_safety.clicked.connect(self._save_safety_settings)
        form_layout.addWidget(self.btn_save_safety, alignment=Qt.AlignLeft)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def _load_initial_profile(self):
        profiles = self.db.list_profiles()
        self.combo_profiles.blockSignals(True)
        self.combo_profiles.clear()
        if profiles:
            for p in profiles:
                self.combo_profiles.addItem(p["name"], p["profile_id"])
            self.combo_profiles.blockSignals(False)
            self._on_profile_changed(0)
        else:
            self.combo_profiles.blockSignals(False)
            self._create_new_profile()

    def _create_new_profile(self):
        prof_id = f"profile_{int(time.time())}"
        new_prof = Profile(
            profile_id=prof_id,
            name=f"Profile {prof_id[-4:]}"
        )
        self.db.save_profile(new_prof)
        self.combo_profiles.blockSignals(True)
        self.combo_profiles.addItem(new_prof.name, new_prof.profile_id)
        self.combo_profiles.setCurrentIndex(self.combo_profiles.count() - 1)
        self.combo_profiles.blockSignals(False)
        self.activate_profile(new_prof)

    def _rename_active_profile(self):
        if not self.active_profile:
            return
        new_name, ok = QInputDialog.getText(self, "Rename Profile", "Enter new profile name:", text=self.active_profile.name)
        if ok and new_name.strip():
            self.active_profile.name = new_name.strip()
            self.db.rename_profile(self.active_profile.profile_id, new_name.strip())
            curr_idx = self.combo_profiles.currentIndex()
            self.combo_profiles.setItemText(curr_idx, new_name.strip())
            QMessageBox.information(self, "Profile Renamed", f"Profile renamed to '{new_name.strip()}'.")

    def _clone_active_profile(self):
        if not self.active_profile:
            return
        clone_name, ok = QInputDialog.getText(self, "Clone Profile", "Enter cloned profile name:", text=f"{self.active_profile.name} (Copy)")
        if ok and clone_name.strip():
            new_id = f"profile_{int(time.time())}"
            cloned = self.db.clone_profile(self.active_profile.profile_id, new_id, clone_name.strip())
            if cloned:
                self.combo_profiles.blockSignals(True)
                self.combo_profiles.addItem(cloned.name, cloned.profile_id)
                self.combo_profiles.setCurrentIndex(self.combo_profiles.count() - 1)
                self.combo_profiles.blockSignals(False)
                self.activate_profile(cloned)
                QMessageBox.information(self, "Profile Cloned", f"Cloned into new profile '{cloned.name}'.")

    def _delete_active_profile(self):
        if not self.active_profile:
            return
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to delete profile '{self.active_profile.name}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            old_id = self.active_profile.profile_id
            self.db.delete_profile(old_id)
            profiles = self.db.list_profiles()
            if profiles:
                self._load_initial_profile()
            else:
                self._create_new_profile()
            QMessageBox.information(self, "Profile Deleted", "Profile deleted successfully.")

    def _create_profile_snapshot(self):
        if not self.active_profile:
            return
        label, ok = QInputDialog.getText(self, "Create Snapshot", "Enter snapshot label (optional):", text=f"Backup {time.strftime('%H:%M:%S')}")
        if ok:
            snap_id = self.db.create_snapshot(self.active_profile.profile_id, label)
            if snap_id:
                QMessageBox.information(self, "Snapshot Created", f"Snapshot saved successfully:\nID: {snap_id}")
            else:
                QMessageBox.critical(self, "Error", "Failed to create snapshot.")

    def _restore_profile_snapshot(self):
        if not self.active_profile:
            return
        snapshots = self.db.list_snapshots(self.active_profile.profile_id)
        if not snapshots:
            snapshots = self.db.list_snapshots()
        if not snapshots:
            QMessageBox.information(self, "No Snapshots", "No snapshots found for this profile or database.")
            return

        items = [f"{s['snapshot_id']} — {s['label']} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(s['created_at']))})" for s in snapshots]
        item, ok = QInputDialog.getItem(self, "Restore Snapshot", "Select snapshot to restore:", items, 0, False)
        if ok and item:
            snap_id = item.split(" — ")[0]
            restored = self.db.restore_snapshot(snap_id)
            if restored:
                self.vision_engine.clear_target_cache()
                self.active_profile = restored
                self._refresh_profile_views()
                QMessageBox.information(self, "Snapshot Restored", f"Profile successfully restored from snapshot:\n{snap_id}")
            else:
                QMessageBox.critical(self, "Restore Failed", "Failed to restore snapshot.")

    def _populate_monitors(self):
        monitors = CaptureManager.enumerate_monitors()
        self.combo_monitor.blockSignals(True)
        self.combo_monitor.clear()
        for m in monitors:
            label = f"{m['name']} ({m['width']}x{m['height']} @ ({m['left']},{m['top']}))"
            self.combo_monitor.addItem(label, m["index"])
        self.combo_monitor.blockSignals(False)

    def _on_monitor_changed(self, idx: int):
        if idx < 0 or not self.active_profile:
            return
        mon_index = self.combo_monitor.currentData()
        if mon_index is not None:
            self.active_profile.monitor_index = mon_index
            self.capture_manager.set_monitor(mon_index)
            self.db.save_profile(self.active_profile)
            logger.info(f"Scan monitor changed to {mon_index}")

    def _on_hotkey_changed(self, hotkey_str: str):
        if not hotkey_str or not self.active_profile:
            return
        success, err = self.hotkey_manager.update_hotkey(hotkey_str)
        if not success:
            QMessageBox.warning(
                self, "HOTKEY_CONFLICT",
                f"Failed to register global hotkey '{hotkey_str}': {err}.\nAnother application may be using this hotkey."
            )
            # Revert combo to current active
            self.combo_hotkey.blockSignals(True)
            self.combo_hotkey.setCurrentText(self.active_profile.emergency_hotkey or "F12")
            self.combo_hotkey.blockSignals(False)
            return

        self.active_profile.emergency_hotkey = hotkey_str
        self.db.save_profile(self.active_profile)
        logger.info(f"Emergency stop hotkey updated to '{hotkey_str}'")

    def _on_roi_mode_toggled(self, custom_checked: bool):
        self.spin_roi_x.setEnabled(custom_checked)
        self.spin_roi_y.setEnabled(custom_checked)
        self.spin_roi_w.setEnabled(custom_checked)
        self.spin_roi_h.setEnabled(custom_checked)

    def _define_roi_from_screen(self):
        frame, _ = self.capture_manager.grab()
        if frame is None:
            QMessageBox.critical(self, "Capture Error", "Failed to grab screen frame for ROI definition.")
            return

        desktop_offset = self.capture_manager.get_desktop_offset()
        self.roi_overlay = ScreenCropOverlay(frame, desktop_offset=desktop_offset, mode="region")
        self.roi_overlay.region_selected.connect(self._on_roi_overlay_selected)
        self.roi_overlay.show()

    def _on_roi_overlay_selected(self, x: int, y: int, w: int, h: int):
        self.radio_roi_custom.setChecked(True)
        self.spin_roi_x.setValue(x)
        self.spin_roi_y.setValue(y)
        self.spin_roi_w.setValue(w)
        self.spin_roi_h.setValue(h)
        self._apply_roi_scope()

    def _apply_roi_scope(self):
        if not self.active_profile:
            return
        if self.radio_roi_full.isChecked():
            self._reset_roi_to_full()
            return

        roi = (
            self.spin_roi_x.value(),
            self.spin_roi_y.value(),
            self.spin_roi_w.value(),
            self.spin_roi_h.value()
        )
        valid, err = self.capture_manager.set_roi(roi)
        if not valid:
            QMessageBox.critical(
                self, "ROI_OUT_OF_BOUNDS",
                f"Failed to apply ROI:\n{err}\nPlease verify coordinates fit within display bounds."
            )
            return

        self.active_profile.roi = roi
        self.db.save_profile(self.active_profile)
        QMessageBox.information(self, "ROI Applied", f"Scan scope ROI set to ({roi[0]}, {roi[1]}, {roi[2]}x{roi[3]}).")

    def _reset_roi_to_full(self):
        if not self.active_profile:
            return
        self.capture_manager.set_roi(None)
        self.active_profile.roi = None
        self.radio_roi_full.setChecked(True)
        self.db.save_profile(self.active_profile)
        QMessageBox.information(self, "Scan Scope Reset", "Scan scope reset to full screen capture.")

    def activate_profile(self, profile: Profile) -> Tuple[bool, str]:
        """
        Atomic profile-activation path (U03).
        Atomically applies:
          1. Monitor binding to CaptureManager (with fallback/validation)
          2. ROI scope to CaptureManager (validated against monitor bounds)
          3. Emergency hotkey to GlobalHotkeyManager (with conflict rollback)
          4. SafetyConfig parameters to ActionManager
          5. Clears VisionEngine target cache
          6. Refreshes UI controls and views
        Enforces fail-closed error handling if critical components fail to bind.
        """
        if profile is None:
            return False, "PROFILE_NONE"

        self.active_profile = profile

        # 1. Apply monitor to CaptureManager
        target_mon = getattr(profile, "monitor_index", 1)
        if hasattr(self, "capture_manager"):
            try:
                self.capture_manager.set_monitor(target_mon)
            except Exception as exc:
                logger.error(f"Failed to bind monitor {target_mon} on profile activation: {exc}. Failing closed to monitor 1.")
                self.capture_manager.set_monitor(1)
                profile.monitor_index = 1

        # 2. Apply ROI to CaptureManager
        roi = getattr(profile, "roi", None)
        if hasattr(self, "capture_manager"):
            if roi:
                valid_roi, roi_err = self.capture_manager.set_roi(roi)
                if not valid_roi:
                    logger.warning(f"Profile ROI {roi} invalid for active monitor ({roi_err}). Resetting ROI to None.")
                    self.capture_manager.set_roi(None)
                    profile.roi = None
            else:
                self.capture_manager.set_roi(None)

        # 3. Apply emergency hotkey to GlobalHotkeyManager
        hk_str = getattr(profile, "emergency_hotkey", "F12") or "F12"
        if hasattr(self, "hotkey_manager"):
            hk_ok, hk_msg = self.hotkey_manager.update_hotkey(hk_str)
            if not hk_ok:
                logger.warning(f"Emergency hotkey '{hk_str}' failed to register ({hk_msg}). Rolled back to previous working hotkey.")

        # 4. Apply Safety Config to ActionManager
        if hasattr(self, "action_manager") and profile.safety_config:
            self.action_manager.safety_config = profile.safety_config
            self.action_manager.max_clicks_per_sec = profile.safety_config.max_clicks_per_second
            self.action_manager.circuit_breaker_threshold = profile.safety_config.circuit_breaker_threshold

        # 5. Clear target embedding caches
        if hasattr(self, "vision_engine"):
            self.vision_engine.clear_target_cache()

        # 6. Refresh UI views
        self._refresh_profile_views()
        self._update_backend_status_ui()

        return True, "PROFILE_ACTIVATED"

    def _on_profile_changed(self, index: int):
        if index < 0:
            return
        prof_id = self.combo_profiles.currentData()
        if prof_id:
            prof = self.db.load_profile(prof_id)
            if prof:
                self.activate_profile(prof)

    def _refresh_profile_views(self):
        if not self.active_profile:
            return

        # 0. Sync hardware & settings controls
        self.combo_monitor.blockSignals(True)
        for i in range(self.combo_monitor.count()):
            if self.combo_monitor.itemData(i) == self.active_profile.monitor_index:
                self.combo_monitor.setCurrentIndex(i)
                break
        self.combo_monitor.blockSignals(False)

        self.combo_hotkey.blockSignals(True)
        self.combo_hotkey.setCurrentText(self.active_profile.emergency_hotkey or "F12")
        self.combo_hotkey.blockSignals(False)

        # Sync ROI controls
        if self.active_profile.roi:
            self.radio_roi_custom.setChecked(True)
            rx, ry, rw, rh = self.active_profile.roi
            self.spin_roi_x.setValue(rx)
            self.spin_roi_y.setValue(ry)
            self.spin_roi_w.setValue(rw)
            self.spin_roi_h.setValue(rh)
        else:
            self.radio_roi_full.setChecked(True)

        # Sync Safety Settings (FC-10)
        sc = self.active_profile.safety_config
        if sc:
            self.spin_max_cps.setValue(sc.max_clicks_per_second)
            self.spin_cb_thresh.setValue(sc.circuit_breaker_threshold)
            self.spin_cb_window.setValue(sc.circuit_breaker_window_sec)
            self.spin_max_per_reg.setValue(sc.max_clicks_per_region)
            self.spin_max_total.setValue(sc.max_total_clicks)
            self.spin_auto_stop.setValue(getattr(sc, "auto_stop_minutes", 0.0))

        # 1. Refresh target table (FC-05)
        self.table_targets.setRowCount(0)
        for t_id, target in self.active_profile.targets.items():
            row = self.table_targets.rowCount()
            self.table_targets.insertRow(row)
            self.table_targets.setItem(row, 0, QTableWidgetItem(target.target_id))

            is_en = getattr(target, "enabled", True)
            en_item = QTableWidgetItem("YES" if is_en else "DISABLED")
            en_item.setForeground(QColor("#2e7d32" if is_en else "#c62828"))
            self.table_targets.setItem(row, 1, en_item)

            self.table_targets.setItem(row, 2, QTableWidgetItem(target.name))
            self.table_targets.setItem(row, 3, QTableWidgetItem(str(len(target.reference_image_paths))))
            self.table_targets.setItem(row, 4, QTableWidgetItem(str(len(target.confuser_image_paths))))

            status_item = QTableWidgetItem()
            if target.calibration:
                status_item.setText(f"CALIBRATED (Tg={target.calibration.t_g:.2f}, Te={target.calibration.t_e:.2f})")
                status_item.setForeground(QColor("#2e7d32"))
            else:
                status_item.setText("UNCALIBRATED (Action Blocked in Prod)")
                status_item.setForeground(QColor("#c62828"))
            self.table_targets.setItem(row, 5, status_item)

        # 2. Refresh workflow selector (FC-07)
        self.combo_workflows.blockSignals(True)
        self.combo_workflows.clear()
        if not self.active_profile.workflows:
            self._get_or_create_default_workflow()
        for wf_id, wf in self.active_profile.workflows.items():
            self.combo_workflows.addItem(f"{wf.name} ({wf_id})", wf_id)
        if self.active_workflow_id in self.active_profile.workflows:
            idx = self.combo_workflows.findData(self.active_workflow_id)
            if idx >= 0:
                self.combo_workflows.setCurrentIndex(idx)
        else:
            self.active_workflow_id = next(iter(self.active_profile.workflows.keys()))
            self.combo_workflows.setCurrentIndex(0)
        self.combo_workflows.blockSignals(False)

        # 3. Refresh region table with workflow binding
        self.table_regions.setRowCount(0)
        available_wfs = list(self.active_profile.workflows.keys())
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

        # 4. Refresh workflow steps
        self._refresh_workflow_views()

    def _refresh_workflow_views(self):
        self.table_steps.setRowCount(0)
        if not self.active_profile:
            return
        wf = self.active_profile.workflows.get(self.active_workflow_id)
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

    def _on_active_workflow_changed(self, index: int):
        if index < 0:
            return
        wf_id = self.combo_workflows.currentData()
        if wf_id:
            self.active_workflow_id = wf_id
            self._refresh_workflow_views()

    def _create_new_workflow(self):
        if not self.active_profile:
            return
        wf_num = len(self.active_profile.workflows) + 1
        name, ok = QInputDialog.getText(self, "New Workflow", "Enter Workflow Name:", text=f"Workflow {wf_num}")
        if ok and name.strip():
            wf_id = f"wf_{int(time.time())}"
            wf = Workflow(workflow_id=wf_id, name=name.strip())
            self.active_profile.workflows[wf_id] = wf
            self.active_workflow_id = wf_id
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()

    def _rename_active_workflow(self):
        if not self.active_profile:
            return
        wf = self.active_profile.workflows.get(self.active_workflow_id)
        if not wf:
            return
        new_name, ok = QInputDialog.getText(self, "Rename Workflow", "Enter new workflow name:", text=wf.name)
        if ok and new_name.strip():
            wf.name = new_name.strip()
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()

    def _clone_active_workflow(self):
        if not self.active_profile:
            return
        wf = self.active_profile.workflows.get(self.active_workflow_id)
        if not wf:
            return
        clone_name, ok = QInputDialog.getText(self, "Clone Workflow", "Enter clone workflow name:", text=f"{wf.name} (Clone)")
        if ok and clone_name.strip():
            cloned_id = f"wf_{int(time.time())}"
            cloned_wf = wf.model_copy(deep=True)
            cloned_wf.workflow_id = cloned_id
            cloned_wf.name = clone_name.strip()
            self.active_profile.workflows[cloned_id] = cloned_wf
            self.active_workflow_id = cloned_id
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()

    def _delete_active_workflow(self):
        if not self.active_profile:
            return
        if len(self.active_profile.workflows) <= 1:
            QMessageBox.warning(self, "Cannot Delete", "Profile must have at least one workflow.")
            return

        # Check if any region is currently bound to this workflow (FC-07)
        bound_regions = [r_id for r_id, reg in self.active_profile.regions.items() if reg.workflow_id == self.active_workflow_id]
        if bound_regions:
            QMessageBox.critical(
                self, "Workflow In Use",
                f"Cannot delete workflow '{self.active_workflow_id}': It is currently assigned to region(s): {', '.join(bound_regions)}.\n"
                "Reassign these regions before deleting."
            )
            return

        reply = QMessageBox.question(self, "Confirm Delete", f"Delete workflow '{self.active_workflow_id}'?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.active_profile.workflows.pop(self.active_workflow_id, None)
            self.active_workflow_id = next(iter(self.active_profile.workflows.keys()))
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()

    def _on_region_workflow_changed(self, region_id: str, workflow_id: str):
        if self.active_profile and region_id in self.active_profile.regions:
            self.active_profile.regions[region_id].workflow_id = workflow_id
            self.db.save_profile(self.active_profile)
            logger.info(f"Region '{region_id}' bound to workflow '{workflow_id}'")

    def _get_or_create_default_workflow(self) -> Workflow:
        if not self.active_profile.workflows:
            wf = Workflow(workflow_id="default_workflow", name="Default Workflow")
            self.active_profile.workflows["default_workflow"] = wf
            return wf
        if self.active_workflow_id in self.active_profile.workflows:
            return self.active_profile.workflows[self.active_workflow_id]
        return next(iter(self.active_profile.workflows.values()))

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

    def _open_target_details(self):
        if not self.active_profile:
            return
        row = self.table_targets.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No Target Selected", "Please select a target from the table.")
            return
        t_id = self.table_targets.item(row, 0).text()
        target = self.active_profile.targets.get(t_id)
        if not target:
            return

        dlg = TargetDetailsDialog(target, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.vision_engine.clear_target_cache()
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()

    def _test_selected_target(self):
        """Opens interactive Test Target verification dialog (FC-06)."""
        if not self.active_profile:
            return
        row = self.table_targets.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No Target Selected", "Please select a target from the table to test.")
            return
        t_id = self.table_targets.item(row, 0).text()
        target = self.active_profile.targets.get(t_id)
        if not target:
            return

        dlg = TestTargetDialog(
            target=target,
            profile=self.active_profile,
            vision_engine=self.vision_engine,
            capture_manager=self.capture_manager,
            parent=self
        )
        dlg.exec()

    def _toggle_target_enabled(self):
        """Enables/disables selected target with workflow dependency check (FC-05)."""
        if not self.active_profile:
            return
        row = self.table_targets.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No Target Selected", "Please select a target from the table.")
            return
        t_id = self.table_targets.item(row, 0).text()
        target = self.active_profile.targets.get(t_id)
        if not target:
            return

        new_state = not getattr(target, "enabled", True)
        if not new_state:
            # Check if used in workflows
            used_in = []
            for wf_id, wf in self.active_profile.workflows.items():
                for step in wf.steps:
                    if step.target_id == t_id:
                        used_in.append(f"'{wf.name}' step {step.step_index + 1}")
            if used_in:
                reply = QMessageBox.question(
                    self, "Target In Active Workflows",
                    f"Target '{t_id}' is referenced in:\n" + "\n".join(used_in) +
                    "\n\nDisabling it will cause the runner to bypass detection for this target. Proceed?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return

        target.enabled = new_state
        self.db.save_profile(self.active_profile)
        self._refresh_profile_views()

    def _batch_import_targets(self):
        """Batch imports target images from a directory (FC-05)."""
        if not self.active_profile:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select Folder for Batch Target Import")
        if not folder:
            return

        supported_exts = {".png", ".jpg", ".jpeg", ".bmp"}
        imported_count = 0
        skipped_count = 0

        for fname in os.listdir(folder):
            ext = os.path.splitext(fname)[1].lower()
            if ext in supported_exts:
                fpath = os.path.abspath(os.path.join(folder, fname))
                img = cv2.imread(fpath)
                if img is None:
                    skipped_count += 1
                    continue

                is_sep, _ = check_geometry_separability(img)
                if not is_sep:
                    skipped_count += 1
                    continue

                t_num = len(self.active_profile.targets) + 1
                t_id = f"target_{t_num}"
                target_name = os.path.splitext(fname)[0]
                new_target = Target(
                    target_id=t_id,
                    name=target_name,
                    reference_image_paths=[fpath],
                    enabled=True,
                    calibration=None
                )
                self.active_profile.targets[t_id] = new_target
                imported_count += 1

        self.db.save_profile(self.active_profile)
        self._refresh_profile_views()
        QMessageBox.information(
            self, "Batch Import Complete",
            f"Batch import finished:\n{imported_count} targets added.\n{skipped_count} images skipped (unreadable or weak separability)."
        )

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
                self.combo_profiles.addItem(imported_prof.name, imported_prof.profile_id)
                if status == "IMPORT_SUCCESS_GEOMETRY_REBOUND":
                    QMessageBox.warning(
                        self, "Import Successful (Geometry Rebound)",
                        f"Profile '{imported_prof.name}' imported successfully.\n\n"
                        f"Target monitor or region coordinates were out of display bounds and were "
                        f"automatically clamped/rebound to Monitor {imported_prof.monitor_index}.\n\n"
                        f"Please verify Region coordinates before running in Production."
                    )
                else:
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
            f"Target '{target.name}' saved.\nStatus: UNCALIBRATED.\nPlease click 'Calibrate...' before running in Production."
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
                f"Target '{target.name}' added.\nStatus: UNCALIBRATED.\nPlease click 'Calibrate...' before running in Production."
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

        # Guard against dangling steps in workflows (FC-05)
        used_in = []
        for wf_id, wf in self.active_profile.workflows.items():
            for s in wf.steps:
                if s.target_id == t_id:
                    used_in.append(f"Workflow '{wf.name}' step {s.step_index + 1}")
        if used_in:
            QMessageBox.critical(
                self, "Cannot Delete Target",
                f"Target '{t_id}' is currently in use by:\n" + "\n".join(used_in) +
                "\n\nPlease remove or edit those workflow steps first to avoid dangling references."
            )
            return

        reply = QMessageBox.question(self, "Confirm Delete", f"Delete target '{t_id}'?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.active_profile.targets.pop(t_id, None)
            self.db.save_profile(self.active_profile)
            self._refresh_profile_views()

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
        wf_id = self.active_workflow_id or "default_workflow"
        reg = RegionModel(region_id=r_id, name=name.strip(), x=x, y=y, w=w, h=h, workflow_id=wf_id)
        self.active_profile.regions[r_id] = reg
        self.db.save_profile(self.active_profile)
        self._refresh_profile_views()
        QMessageBox.information(self, "Region Defined", f"Region '{reg.name}' defined at screen coordinates ({x}, {y}, {w}x{h}).")

    def _add_region(self):
        if not self.active_profile:
            return
        r_num = len(self.active_profile.regions) + 1
        r_id = f"table_{r_num}"
        wf_id = self.active_workflow_id or "default_workflow"
        reg = RegionModel(region_id=r_id, name=f"Table {r_num}", x=100 + (r_num * 50), y=100, w=200, h=200, workflow_id=wf_id)
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

    def _save_safety_settings(self):
        """Saves safety bounds from UI to active profile (FC-10)."""
        if not self.active_profile:
            return
        cfg = SafetyConfig(
            max_clicks_per_second=self.spin_max_cps.value(),
            circuit_breaker_threshold=self.spin_cb_thresh.value(),
            circuit_breaker_window_sec=self.spin_cb_window.value(),
            max_clicks_per_region=self.spin_max_per_reg.value(),
            max_total_clicks=self.spin_max_total.value(),
            auto_stop_minutes=self.spin_auto_stop.value()
        )
        self.active_profile.safety_config = cfg
        self.action_manager.safety_config = cfg
        self.action_manager.max_clicks_per_sec = cfg.max_clicks_per_second
        self.action_manager.circuit_breaker_threshold = cfg.circuit_breaker_threshold
        self.db.save_profile(self.active_profile)
        QMessageBox.information(self, "Safety Settings Saved", "Safety bounds updated and persisted to profile.")

    def _on_emergency_hotkey(self):
        """Triggered immediately when user presses emergency stop hotkey system-wide (STOP ONLY)."""
        self.action_manager.trigger_emergency_stop()
        if self.runner and self.runner.is_running:
            self.runner.stop()
        self._set_ui_running_state(False)
        self.tray_manager.update_status(
            is_running=False,
            profile_name=self.active_profile.name if self.active_profile else "None",
            backend_status="EMERGENCY_STOPPED"
        )
        logger.critical("Emergency Stop executed (STOP ONLY semantics).")

    def _set_ui_running_state(self, running: bool):
        """Locks hardware/safety controls while running (FC-02, FC-10)."""
        if running:
            self.btn_start.setText("STOP BOT")
            self.btn_start.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; padding: 6px 16px;")
        else:
            self.btn_start.setText("START BOT")
            self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;")

        self.combo_monitor.setEnabled(not running)
        self.combo_profiles.setEnabled(not running)
        self.btn_new_profile.setEnabled(not running)
        self.btn_rename_profile.setEnabled(not running)
        self.btn_clone_profile.setEnabled(not running)
        self.btn_delete_profile.setEnabled(not running)
        self.btn_apply_roi.setEnabled(not running)
        self.btn_reset_roi.setEnabled(not running)
        self.btn_save_safety.setEnabled(not running)

    def _toggle_bot(self):
        """Starts or stops the live bot execution thread (FC-08, FC-09)."""
        if not self.runner or not self.runner.is_running:
            mode = self.combo_mode.currentText()
            is_shadow = "Shadow" in mode
            is_dry_run = ("Dry-Run" in mode) or is_shadow
            is_production = "Production" in mode and not is_shadow

            if is_production:
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
                                    "Production mode requires verified CalibrationProfile. Run 'Calibrate...' first."
                                )
                                return

                    for r_id, reg in self.active_profile.regions.items():
                        if not reg.workflow_id:
                            QMessageBox.critical(
                                self,
                                "Execution Blocked (Fail-Closed)",
                                f"Cannot start in Production mode: Region '{reg.name or r_id}' has no assigned workflow."
                            )
                            return
                        if reg.workflow_id not in self.active_profile.workflows:
                            QMessageBox.critical(
                                self,
                                "Execution Blocked (Fail-Closed)",
                                f"Cannot start in Production mode: Region '{reg.name or r_id}' assigned workflow '{reg.workflow_id}' not found."
                            )
                            return
                        wf = self.active_profile.workflows[reg.workflow_id]
                        if not wf.steps:
                            QMessageBox.critical(
                                self,
                                "Execution Blocked (Fail-Closed)",
                                f"Cannot start in Production mode: Workflow '{wf.name or reg.workflow_id}' has 0 steps."
                            )
                            return

            if not self.active_profile or not self.active_profile.regions:
                QMessageBox.warning(self, "No Regions", "Configure at least 1 region before starting bot.")
                return

            # Register regions with their designated workflows
            self.ledger = SessionLedger()
            if hasattr(self, "_get_or_create_default_workflow"):
                default_wf = self._get_or_create_default_workflow()
            else:
                default_wf = self.active_profile.workflows.get("default_workflow") or Workflow(workflow_id="default_workflow", name="Default Workflow")

            for r_id, reg in self.active_profile.regions.items():
                wf_to_use = None
                if reg.workflow_id and reg.workflow_id in self.active_profile.workflows:
                    wf_to_use = self.active_profile.workflows[reg.workflow_id]
                else:
                    wf_to_use = default_wf
                inst = self.ledger.register_region(reg, wf_to_use)
                inst.start_workflow()

            # Start Runner Thread
            self.action_manager.reset_emergency_stop()
            shadow_cb = self.shadow_evidence_signal.emit if hasattr(self, "shadow_evidence_signal") else None
            self.runner = BotRuntimeRunner(
                profile=self.active_profile,
                capture_manager=self.capture_manager,
                vision_engine=self.vision_engine,
                action_manager=self.action_manager,
                ledger=self.ledger,
                telemetry_logger=getattr(self, "telemetry_logger", None),
                is_dry_run=is_dry_run,
                is_production=is_production,
                is_shadow=is_shadow,
                on_decision_callback=self.decision_received_signal.emit,
                on_state_callback=self.region_state_signal.emit,
                on_shadow_evidence_callback=shadow_cb
            )
            self.runner.start()

            if hasattr(self, "_set_ui_running_state"):
                self._set_ui_running_state(True)
            elif hasattr(self, "btn_start"):
                self.btn_start.setText("STOP BOT")

            status_text = "SHADOW_MODE" if is_shadow else ("DRY_RUN" if is_dry_run else "PRODUCTION_ACTIVE")
            self.tray_manager.update_status(
                is_running=True,
                profile_name=self.active_profile.name,
                backend_status=status_text
            )
        else:
            self.runner.stop()
            if hasattr(self, "_set_ui_running_state"):
                self._set_ui_running_state(False)
            elif hasattr(self, "btn_start"):
                self.btn_start.setText("START BOT")

            self.tray_manager.update_status(
                is_running=False,
                profile_name=self.active_profile.name if self.active_profile else "None",
                backend_status=self.action_manager.support_status_message
            )

    @Slot(object)
    def _on_live_decision(self, res: DecisionResult):
        # Update metrics counters (FC-11)
        self.counter_total += 1
        if res.decision == DecisionClass.MATCH:
            self.counter_match += 1
        elif res.decision == DecisionClass.NON_MATCH:
            self.counter_non_match += 1
        elif res.decision == DecisionClass.UNKNOWN:
            self.counter_unknown += 1

        if res.latency_ms > 0:
            self.latency_buffer.append(res.latency_ms)
            if len(self.latency_buffer) > 200:
                self.latency_buffer.pop(0)

        # Update counter badges
        self.lbl_badge_match.setText(f"MATCH: {self.counter_match}")
        self.lbl_badge_unknown.setText(f"UNKNOWN: {self.counter_unknown}")
        self.lbl_badge_non_match.setText(f"NON-MATCH: {self.counter_non_match}")
        self.lbl_badge_reject.setText(f"REJECT: {self.counter_reject}")
        self.lbl_badge_total.setText(f"TOTAL: {self.counter_total}")

        if self.latency_buffer:
            p50 = float(np.percentile(self.latency_buffer, 50))
            p95 = float(np.percentile(self.latency_buffer, 95))
            p99 = float(np.percentile(self.latency_buffer, 99))
            mx = float(np.max(self.latency_buffer))
            self.lbl_latency_summary.setText(f"Latency: P50: {p50:.1f}ms | P95: {p95:.1f}ms | P99: {p99:.1f}ms | Max: {mx:.1f}ms")

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

    @Slot(dict)
    def _on_shadow_evidence(self, shadow_data: dict):
        """Dedicated comparison evidence stream for Shadow Mode (FC-09)."""
        row = self.table_telemetry.rowCount()
        if row > 200:
            self.table_telemetry.removeRow(0)
            row -= 1

        self.table_telemetry.insertRow(row)
        t_str = time.strftime('%H:%M:%S', time.localtime(shadow_data.get("timestamp", time.time())))
        self.table_telemetry.setItem(row, 0, QTableWidgetItem(t_str))
        self.table_telemetry.setItem(row, 1, QTableWidgetItem(shadow_data.get("region_id", "-")))
        self.table_telemetry.setItem(row, 2, QTableWidgetItem(shadow_data.get("target_id", "-")))
        self.table_telemetry.setItem(row, 3, QTableWidgetItem(f"{shadow_data.get('geometry_score', 0.0):.3f}"))
        self.table_telemetry.setItem(row, 4, QTableWidgetItem(f"{shadow_data.get('embedding_similarity', 0.0):.3f}"))

        dec_item = QTableWidgetItem(f"SHADOW_{shadow_data.get('decision', 'MATCH')}")
        dec_item.setForeground(QColor("#6a1b9a"))
        self.table_telemetry.setItem(row, 5, dec_item)
        self.table_telemetry.setItem(row, 6, QTableWidgetItem(f"Pos: {shadow_data.get('screen_pos')} (0 Dispatch)"))

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
