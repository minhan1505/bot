"""
tests/test_ui_workflow_and_dialogs.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit and integration tests for F06, F08, and F09 UI workflows and dialogs.
Tests:
  - WorkflowStepDialog configuration and target binding.
  - SurfaceVerificationDialog 2-stage verification flow.
  - MainWindow dynamic step reordering (Move Up, Move Down, Delete).
  - MainWindow fail-closed guard in Production mode when uncalibrated or surface unverified.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication
from bot.core.models import (
    Profile, Target, CalibrationProfile, RegionModel,
    Workflow, WorkflowStep, ActionType, SafetyConfig
)
from bot.action.manager import ActionManager
from bot.action.base import ActionDispatchResult, ActionDispatchStatus
from bot.ui.step_dialog import WorkflowStepDialog
from bot.ui.surface_dialog import SurfaceVerificationDialog
from bot.ui.main_window import MainWindow

# Ensure QApplication exists for UI tests
app = QApplication.instance() or QApplication([])


def test_workflow_step_dialog_configuration():
    target1 = Target(target_id="t1", name="Target 1")
    target2 = Target(target_id="t2", name="Target 2")
    profile = Profile(
        profile_id="p1",
        name="Test Profile",
        targets={"t1": target1, "t2": target2}
    )

    # Test creating new step
    dlg = WorkflowStepDialog(profile)
    assert dlg.combo_target.count() == 2
    dlg.combo_target.setCurrentIndex(1) # Target 2
    dlg.spin_timeout.setValue(3500)
    dlg.spin_cooldown.setValue(250)
    dlg.spin_retries.setValue(5)

    step = dlg.get_step(step_index=0)
    assert step.target_id == "t2"
    assert step.timeout_ms == 3500
    assert step.cooldown_ms == 250
    assert step.retry_limit == 5

    # Test editing existing step
    existing_step = WorkflowStep(
        step_index=1,
        target_id="t1",
        action_type=ActionType.DOUBLE_CLICK,
        timeout_ms=8000,
        cooldown_ms=1000,
        retry_limit=2
    )
    dlg_edit = WorkflowStepDialog(profile, step=existing_step)
    assert dlg_edit.combo_target.currentData() == "t1"
    assert dlg_edit.spin_timeout.value() == 8000
    assert dlg_edit.spin_cooldown.value() == 1000
    assert dlg_edit.spin_retries.value() == 2


def test_main_window_step_reordering():
    """Verifies move up, move down, and delete step reordering in MainWindow."""
    profile = Profile(
        profile_id="p_reorder",
        name="Reorder Profile",
        targets={"t1": Target(target_id="t1", name="T1"), "t2": Target(target_id="t2", name="T2"), "t3": Target(target_id="t3", name="T3")},
        workflows={
            "default_workflow": Workflow(
                workflow_id="default_workflow",
                name="Default",
                steps=[
                    WorkflowStep(step_index=0, target_id="t1"),
                    WorkflowStep(step_index=1, target_id="t2"),
                    WorkflowStep(step_index=2, target_id="t3")
                ]
            )
        }
    )

    ui = SimpleNamespace(
        active_profile=profile,
        table_steps=MagicMock(),
        db=MagicMock()
    )
    ui._get_or_create_default_workflow = lambda: profile.workflows["default_workflow"]
    ui._refresh_workflow_views = MagicMock()

    # Move row 1 ("t2") UP -> should swap with row 0 ("t1")
    ui.table_steps.currentRow.return_value = 1
    MainWindow._move_workflow_step_up(ui)
    wf_steps = profile.workflows["default_workflow"].steps
    assert wf_steps[0].target_id == "t2"
    assert wf_steps[0].step_index == 0
    assert wf_steps[1].target_id == "t1"
    assert wf_steps[1].step_index == 1

    # Move row 0 ("t2") DOWN -> should swap with row 1 ("t1")
    ui.table_steps.currentRow.return_value = 0
    MainWindow._move_workflow_step_down(ui)
    assert wf_steps[0].target_id == "t1"
    assert wf_steps[1].target_id == "t2"

    # Delete row 1 ("t2") -> row 2 ("t3") becomes index 1
    ui.table_steps.currentRow.return_value = 1
    MainWindow._delete_workflow_step(ui)
    assert len(wf_steps) == 2
    assert wf_steps[0].target_id == "t1"
    assert wf_steps[1].target_id == "t3"
    assert wf_steps[1].step_index == 1


def test_main_window_production_guard_rejects_uncalibrated_target():
    """Verifies that Production mode blocks execution if any workflow target lacks calibration."""
    profile = Profile(
        profile_id="p_prod",
        name="Prod Profile",
        targets={
            "t1": Target(target_id="t1", name="Uncalibrated Target", calibration=None)
        },
        regions={
            "r1": RegionModel(region_id="r1", name="R1", x=0, y=0, w=100, h=100)
        },
        workflows={
            "default_workflow": Workflow(
                workflow_id="default_workflow",
                name="WF",
                steps=[WorkflowStep(step_index=0, target_id="t1")]
            )
        }
    )

    action_mgr = MagicMock()
    action_mgr.is_supported = True
    action_mgr.is_surface_verified = True

    ui = SimpleNamespace(
        runner=None,
        combo_mode=MagicMock(),
        action_manager=action_mgr,
        active_profile=profile,
        capture_manager=MagicMock(),
        vision_engine=MagicMock(),
        decision_received_signal=MagicMock(),
        region_state_signal=MagicMock(),
        btn_start=MagicMock(),
        tray_manager=MagicMock()
    )
    ui.combo_mode.currentText.return_value = "Production Background Action"

    with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_msg:
        with patch("bot.ui.main_window.BotRuntimeRunner") as runner_class:
            MainWindow._toggle_bot(ui)
            # Must block execution before starting runner
            runner_class.assert_not_called()
            mock_msg.assert_called_once()
            assert "UNCALIBRATED" in mock_msg.call_args[0][2]


def test_main_window_production_guard_rejects_unverified_surface():
    """Verifies that Production mode blocks execution if surface is not verified."""
    calib = CalibrationProfile(
        model_sha256="test", precision="FP32",
        t_g=0.6, t_e=0.7, m_safe=0.05
    )
    profile = Profile(
        profile_id="p_prod",
        name="Prod Profile",
        targets={
            "t1": Target(target_id="t1", name="Calibrated Target", calibration=calib)
        },
        regions={
            "r1": RegionModel(region_id="r1", name="R1", x=0, y=0, w=100, h=100)
        },
        workflows={
            "default_workflow": Workflow(
                workflow_id="default_workflow",
                name="WF",
                steps=[WorkflowStep(step_index=0, target_id="t1")]
            )
        }
    )

    action_mgr = MagicMock()
    action_mgr.is_supported = True
    action_mgr.is_surface_verified = False # Surface unverified!

    ui = SimpleNamespace(
        runner=None,
        combo_mode=MagicMock(),
        action_manager=action_mgr,
        active_profile=profile,
        capture_manager=MagicMock(),
        vision_engine=MagicMock(),
        decision_received_signal=MagicMock(),
        region_state_signal=MagicMock(),
        btn_start=MagicMock(),
        tray_manager=MagicMock()
    )
    ui.combo_mode.currentText.return_value = "Production Background Action"

    with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_msg:
        with patch("bot.ui.main_window.BotRuntimeRunner") as runner_class:
            MainWindow._toggle_bot(ui)
            runner_class.assert_not_called()
            mock_msg.assert_called_once()
            assert "surface compatibility has not been verified" in mock_msg.call_args[0][2]


def test_main_window_production_guard_rejects_unassigned_workflow_for_region():
    """Verifies that Production mode blocks execution if any region has no assigned workflow."""
    calib = CalibrationProfile(model_sha256="test", precision="FP32", t_g=0.6, t_e=0.7, m_safe=0.05)
    profile = Profile(
        profile_id="p_prod",
        name="Prod Profile",
        targets={"t1": Target(target_id="t1", name="Calibrated Target", calibration=calib)},
        regions={"r1": RegionModel(region_id="r1", name="R1", workflow_id="", x=0, y=0, w=100, h=100)},
        workflows={"wf1": Workflow(workflow_id="wf1", name="WF1", steps=[WorkflowStep(step_index=0, target_id="t1")])}
    )

    action_mgr = MagicMock(is_supported=True, is_surface_verified=True)
    ui = SimpleNamespace(
        runner=None,
        combo_mode=MagicMock(),
        action_manager=action_mgr,
        active_profile=profile,
        capture_manager=MagicMock(),
        vision_engine=MagicMock(),
        decision_received_signal=MagicMock(),
        region_state_signal=MagicMock(),
        btn_start=MagicMock(),
        tray_manager=MagicMock()
    )
    ui.combo_mode.currentText.return_value = "Production Background Action"

    with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_msg:
        with patch("bot.ui.main_window.BotRuntimeRunner") as runner_class:
            MainWindow._toggle_bot(ui)
            runner_class.assert_not_called()
            mock_msg.assert_called_once()
            assert "has no assigned workflow" in mock_msg.call_args[0][2]


def test_main_window_production_guard_rejects_empty_workflow_steps():
    """Verifies that Production mode blocks execution if assigned workflow has 0 steps."""
    calib = CalibrationProfile(model_sha256="test", precision="FP32", t_g=0.6, t_e=0.7, m_safe=0.05)
    profile = Profile(
        profile_id="p_prod",
        name="Prod Profile",
        targets={"t1": Target(target_id="t1", name="Calibrated Target", calibration=calib)},
        regions={"r1": RegionModel(region_id="r1", name="R1", workflow_id="wf_empty", x=0, y=0, w=100, h=100)},
        workflows={"wf_empty": Workflow(workflow_id="wf_empty", name="Empty WF", steps=[])}
    )

    action_mgr = MagicMock(is_supported=True, is_surface_verified=True)
    ui = SimpleNamespace(
        runner=None,
        combo_mode=MagicMock(),
        action_manager=action_mgr,
        active_profile=profile,
        capture_manager=MagicMock(),
        vision_engine=MagicMock(),
        decision_received_signal=MagicMock(),
        region_state_signal=MagicMock(),
        btn_start=MagicMock(),
        tray_manager=MagicMock()
    )
    ui.combo_mode.currentText.return_value = "Production Background Action"

    with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_msg:
        with patch("bot.ui.main_window.BotRuntimeRunner") as runner_class:
            MainWindow._toggle_bot(ui)
            runner_class.assert_not_called()
            mock_msg.assert_called_once()
            assert "has 0 steps" in mock_msg.call_args[0][2]


def test_target_calibration_dialog_requires_dual_session_real_samples():
    """Verifies TargetCalibrationDialog blocks calibration if Session A or B lacks real samples."""
    from bot.ui.calibration_dialog import TargetCalibrationDialog

    target = Target(target_id="t_cal", name="Calib Target")
    profile = Profile(profile_id="p_test", name="P")
    onnx_v = MagicMock()
    geo_v = MagicMock()

    dlg = TargetCalibrationDialog(target, profile, onnx_v, geo_v)
    assert len(dlg.session_a_pos) == 0
    assert len(dlg.session_b_pos) == 0

    # Try calibrating with 0 samples
    with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
        dlg._run_calibration()
        mock_warn.assert_called_once()
        assert "independent capture sessions" in mock_warn.call_args[0][2]

    # Add sample only to Session A
    dlg.session_a_pos.append("fake/path/a.png")
    with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
        dlg._run_calibration()
        mock_warn.assert_called_once()
        assert "independent capture sessions" in mock_warn.call_args[0][2]


def test_cdp_backend_viewport_context_binding_and_production_guard():
    """Verifies CDPActionBackend rejects clicks in Production mode when ViewportContext is missing."""
    from bot.action.cdp_backend import CDPActionBackend
    import io
    import json

    backend = CDPActionBackend()

    # Test get_available_pages
    fake_json = json.dumps([
        {"id": "tab1", "type": "page", "title": "Game Window", "url": "https://game.com", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/tab1"},
        {"id": "bg1", "type": "background_page", "title": "Ext", "url": "chrome-ext://1"}
    ]).encode("utf-8")

    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_json
        mock_resp.__enter__.return_value = mock_resp
        mock_url.return_value = mock_resp

        pages = backend.get_available_pages()
        assert len(pages) == 1
        assert pages[0]["id"] == "tab1"
        assert pages[0]["title"] == "Game Window"

    # Test production fail-closed when viewport_context is None
    res = backend.dispatch_click(100, 100, context={"is_production": True})
    assert res.status == ActionDispatchStatus.NOT_SENT
    assert "ViewportContext" in res.reason


def test_target_calibration_dialog_rejects_content_leakage_between_sessions(tmp_path):
    """Verifies TargetCalibrationDialog blocks calibration when identical content is put in Session A and B."""
    import cv2
    import numpy as np
    from bot.ui.calibration_dialog import TargetCalibrationDialog

    # Create two files with identical pixel contents
    img = np.zeros((48, 48, 3), dtype=np.uint8)
    cv2.circle(img, (24, 24), 10, (255, 255, 255), -1)

    file_a = str(tmp_path / "img_session_a.png")
    file_b = str(tmp_path / "img_session_b_copy.png")
    cv2.imwrite(file_a, img)
    cv2.imwrite(file_b, img) # Identical pixel content!

    confuser_a = str(tmp_path / "conf_a.png")
    confuser_b = str(tmp_path / "conf_b.png")
    cv2.imwrite(confuser_a, np.ones((48, 48, 3), dtype=np.uint8) * 50)
    cv2.imwrite(confuser_b, np.ones((48, 48, 3), dtype=np.uint8) * 100)

    target = Target(target_id="t_leak", name="Target Leak Test")
    profile = Profile(profile_id="p_test", name="Profile")
    onnx_v = MagicMock()
    geo_v = MagicMock()

    dlg = TargetCalibrationDialog(target, profile, onnx_v, geo_v)
    dlg.session_a_pos = [file_a]
    dlg.session_b_pos = [file_b]
    dlg.session_a_neg = [confuser_a]
    dlg.session_b_neg = [confuser_b]

    with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_crit:
        dlg._run_calibration()
        mock_crit.assert_called_once()
        assert "Data leakage detected" in mock_crit.call_args[0][2]


def test_calibrate_target_from_samples_rejects_identical_samples():
    """Verifies calibrate_target_from_samples raises CalibrationOverlapError if positive samples are duplicated."""
    import numpy as np
    from bot.vision.calibration import calibrate_target_from_samples, CalibrationOverlapError

    img = np.zeros((48, 48, 3), dtype=np.uint8)
    # 2 identical positive images
    pos_samples = [img, img.copy()]
    neg_samples = [np.ones((48, 48, 3), dtype=np.uint8) * 50, np.ones((48, 48, 3), dtype=np.uint8) * 100]

    with pytest.raises(CalibrationOverlapError) as exc_info:
        calibrate_target_from_samples(
            target_id="t_dup",
            target_reference_img=img,
            positive_images=pos_samples,
            negative_images=neg_samples,
            alternative_identity_imgs={"alt1": neg_samples[0]},
            geo_verifier=MagicMock(),
            onnx_verifier=MagicMock()
        )
    assert "DATA_LEAKAGE_DETECTED" in str(exc_info.value)


def test_surface_dialog_cdp_probe_constructs_valid_viewport_context():
    """Verifies SurfaceVerificationDialog._probe_cdp creates ViewportContext with valid Rect without TypeError."""
    from bot.ui.surface_dialog import SurfaceVerificationDialog
    from bot.core.coordinates import ViewportContext, Rect
    import asyncio
    import json

    action_mgr = ActionManager()
    dlg = SurfaceVerificationDialog(action_mgr)
    dlg.combo_cdp_tabs.addItem("Test Tab", "ws://127.0.0.1:9222/devtools/page/test")
    dlg.combo_cdp_tabs.setCurrentIndex(dlg.combo_cdp_tabs.count() - 1)

    # Mock responses for CDP probe
    eval_resp_1 = {
        "result": {
            "result": {
                "value": {
                    "title": "Casino Table",
                    "innerWidth": 1920,
                    "innerHeight": 1080,
                    "devicePixelRatio": 1.25,
                    "screenX": 100,
                    "screenY": 50,
                    "outerWidth": 1920,
                    "outerHeight": 1150,
                    "scrollX": 0,
                    "scrollY": 0,
                    "targetElement": {"tagName": "CANVAS", "isCanvas": True, "pointerEvents": "auto"}
                }
            }
        }
    }
    eval_resp_2 = {"result": {"result": {}}}
    eval_resp_4 = {
        "result": {
            "result": {
                "value": {
                    "received": True,
                    "targetTagName": "CANVAS",
                    "eventTargetMatched": True,
                    "rafActive": True
                }
            }
        }
    }

    class MockWs:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, msg):
            pass
        async def recv(self):
            if not hasattr(self, "_idx"):
                self._idx = 0
            self._idx += 1
            if self._idx == 1:
                return json.dumps(eval_resp_1)
            elif self._idx == 2:
                return json.dumps(eval_resp_2)
            elif self._idx == 3:
                return json.dumps({})
            else:
                return json.dumps(eval_resp_4)

    with patch("bot.ui.surface_dialog.websockets.connect", return_value=MockWs()):
        with patch("bot.action.cdp_backend.CDPActionBackend.probe_capability", return_value=(True, "OK")):
            with patch("bot.ui.surface_dialog.get_physical_cursor_pos", return_value=(500, 500)):
                dlg._probe_cdp()

    assert dlg.probe_success is True
    assert action_mgr.is_surface_verified is True
    assert action_mgr.viewport_context is not None
    assert isinstance(action_mgr.viewport_context, ViewportContext)
    assert isinstance(action_mgr.viewport_context.window_rect, Rect)
    assert isinstance(action_mgr.viewport_context.client_rect, Rect)
    assert action_mgr.viewport_context.device_pixel_ratio == 1.25
    assert action_mgr.viewport_context.window_rect.x == 125
    assert action_mgr.viewport_context.client_rect.w == int(round(1920 * 1.25))


def test_surface_dialog_fails_when_matched_is_false():
    """Fail-Closed: If eventTargetMatched is False (overlay interception), probe MUST fail."""
    from bot.ui.surface_dialog import SurfaceVerificationDialog
    import json

    action_mgr = ActionManager()
    dlg = SurfaceVerificationDialog(action_mgr)
    dlg.combo_cdp_tabs.addItem("Test Tab", "ws://127.0.0.1:9222/devtools/page/test")
    dlg.combo_cdp_tabs.setCurrentIndex(dlg.combo_cdp_tabs.count() - 1)

    eval_resp_1 = {
        "result": {"result": {"value": {
            "title": "Casino Table", "innerWidth": 1000, "innerHeight": 800,
            "devicePixelRatio": 1.0, "screenX": 0, "screenY": 0,
            "outerWidth": 1000, "outerHeight": 800, "scrollX": 0, "scrollY": 0,
            "targetElement": {"tagName": "CANVAS"}
        }}}
    }
    eval_resp_4 = {
        "result": {"result": {"value": {
            "received": True,
            "targetTagName": "CANVAS",
            "eventTargetMatched": False, # Intercepted by overlay!
            "rafActive": True
        }}}
    }

    class MockWs:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, msg):
            pass
        async def recv(self):
            if not hasattr(self, "_idx"):
                self._idx = 0
            self._idx += 1
            if self._idx == 1:
                return json.dumps(eval_resp_1)
            elif self._idx == 2 or self._idx == 3:
                return json.dumps({})
            else:
                return json.dumps(eval_resp_4)

    with patch("bot.ui.surface_dialog.websockets.connect", return_value=MockWs()):
        with patch("bot.action.cdp_backend.CDPActionBackend.probe_capability", return_value=(True, "OK")):
            with patch("bot.ui.surface_dialog.get_physical_cursor_pos", return_value=(500, 500)):
                dlg._probe_cdp()

    assert dlg.probe_success is False
    assert action_mgr.is_surface_verified is False
    assert "SURFACE_PROBE_REJECTED" in dlg.lbl_status.text()


def test_surface_dialog_fails_when_raf_is_false():
    """Fail-Closed: If rafActive is False (render loop halted / tab throttled), probe MUST fail."""
    from bot.ui.surface_dialog import SurfaceVerificationDialog
    import json

    action_mgr = ActionManager()
    dlg = SurfaceVerificationDialog(action_mgr)
    dlg.combo_cdp_tabs.addItem("Test Tab", "ws://127.0.0.1:9222/devtools/page/test")
    dlg.combo_cdp_tabs.setCurrentIndex(dlg.combo_cdp_tabs.count() - 1)

    eval_resp_1 = {
        "result": {"result": {"value": {
            "title": "Casino Table", "innerWidth": 1000, "innerHeight": 800,
            "devicePixelRatio": 1.0, "screenX": 0, "screenY": 0,
            "outerWidth": 1000, "outerHeight": 800, "scrollX": 0, "scrollY": 0,
            "targetElement": {"tagName": "CANVAS"}
        }}}
    }
    eval_resp_4 = {
        "result": {"result": {"value": {
            "received": True,
            "targetTagName": "CANVAS",
            "eventTargetMatched": True,
            "rafActive": False # Frozen render loop!
        }}}
    }

    class MockWs:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, msg):
            pass
        async def recv(self):
            if not hasattr(self, "_idx"):
                self._idx = 0
            self._idx += 1
            if self._idx == 1:
                return json.dumps(eval_resp_1)
            elif self._idx == 2 or self._idx == 3:
                return json.dumps({})
            else:
                return json.dumps(eval_resp_4)

    with patch("bot.ui.surface_dialog.websockets.connect", return_value=MockWs()):
        with patch("bot.action.cdp_backend.CDPActionBackend.probe_capability", return_value=(True, "OK")):
            with patch("bot.ui.surface_dialog.get_physical_cursor_pos", return_value=(500, 500)):
                dlg._probe_cdp()

    assert dlg.probe_success is False
    assert action_mgr.is_surface_verified is False
    assert "SURFACE_PROBE_REJECTED" in dlg.lbl_status.text()


def test_target_calibration_dialog_rejects_overlapping_provenance_ids(tmp_path):
    """Verifies TargetCalibrationDialog blocks calibration when session_id or run_id overlap."""
    import cv2
    import numpy as np
    from bot.ui.calibration_dialog import TargetCalibrationDialog

    img1 = np.zeros((48, 48, 3), dtype=np.uint8)
    cv2.circle(img1, (24, 24), 8, (255, 255, 255), -1)
    img2 = np.zeros((48, 48, 3), dtype=np.uint8)
    cv2.rectangle(img2, (10, 10), (38, 38), (255, 255, 255), -1)

    file_a = str(tmp_path / "img_pos_a.png")
    file_b = str(tmp_path / "img_pos_b.png")
    cv2.imwrite(file_a, img1)
    cv2.imwrite(file_b, img2)

    conf_a = str(tmp_path / "conf_a.png")
    conf_b = str(tmp_path / "conf_b.png")
    cv2.imwrite(conf_a, np.ones((48, 48, 3), dtype=np.uint8) * 30)
    cv2.imwrite(conf_b, np.ones((48, 48, 3), dtype=np.uint8) * 80)

    target = Target(target_id="t_prov", name="Target Prov")
    profile = Profile(profile_id="p_test", name="Profile")

    dlg = TargetCalibrationDialog(target, profile, MagicMock(), MagicMock())
    dlg.session_a_pos = [file_a]
    dlg.session_b_pos = [file_b]
    dlg.session_a_neg = [conf_a]
    dlg.session_b_neg = [conf_b]

    # Force identical session IDs
    dlg.txt_session_a.setText("SAME_SESSION")
    dlg.txt_session_b.setText("SAME_SESSION")

    with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_crit:
        dlg._run_calibration()
        mock_crit.assert_called_once()
        assert "Session IDs must be distinct" in mock_crit.call_args[0][2]

    # Distinct sessions, but identical run IDs
    dlg.txt_session_a.setText("session_1")
    dlg.txt_session_b.setText("session_2")
    dlg.txt_run_a.setText("SAME_RUN")
    dlg.txt_run_b.setText("SAME_RUN")

    with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_crit:
        dlg._run_calibration()
        mock_crit.assert_called_once()
        assert "Run IDs must be distinct" in mock_crit.call_args[0][2]


def test_cdp_backend_freshness_check_invalidates_on_geometry_drift():
    """Verifies verify_viewport_freshness detects DPR drift and window resize, invalidating context."""
    from bot.action.cdp_backend import CDPActionBackend
    from bot.core.coordinates import ViewportContext, Rect
    import json

    ctx = ViewportContext(
        window_rect=Rect(0, 0, 1920, 1080),
        client_rect=Rect(0, 0, 1920, 1080),
        device_pixel_ratio=1.0,
        scroll_offset=(0, 0)
    )

    backend = CDPActionBackend()
    backend.ws_url = "ws://127.0.0.1:9222/test"
    backend.viewport_context = ctx

    class MockWs:
        def __init__(self, dpr, w, h):
            self.dpr = dpr
            self.w = w
            self.h = h
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, msg):
            pass
        async def recv(self):
            return json.dumps({
                "result": {"result": {"value": {
                    "dpr": self.dpr, "innerWidth": self.w, "innerHeight": self.h
                }}}
            })

    # 1. Fresh case
    with patch("bot.action.cdp_backend.websockets.connect", return_value=MockWs(1.0, 1920, 1080)):
        fresh, msg = backend.verify_viewport_freshness(ctx)
        assert fresh is True
        assert msg == "FRESH"
        assert backend.viewport_context is not None

    # 2. Resized case
    with patch("bot.action.cdp_backend.websockets.connect", return_value=MockWs(1.0, 1280, 720)):
        fresh, msg = backend.verify_viewport_freshness(ctx)
        assert fresh is False
        assert "VIEWPORT_RESIZED" in msg
        assert backend.viewport_context is None # Invalidated!

    # 3. DPR drift case
    backend.viewport_context = ctx
    with patch("bot.action.cdp_backend.websockets.connect", return_value=MockWs(1.5, 1920, 1080)):
        fresh, msg = backend.verify_viewport_freshness(ctx)
        assert fresh is False
        assert "DPR_DRIFT" in msg
        assert backend.viewport_context is None # Invalidated!


def test_action_manager_invalidate_binding():
    """Verifies ActionManager.invalidate_binding resets surface verification state."""
    from bot.action.cdp_backend import CDPActionBackend
    from bot.core.coordinates import ViewportContext, Rect

    backend = CDPActionBackend()
    backend.viewport_context = ViewportContext(
        window_rect=Rect(0, 0, 100, 100),
        client_rect=Rect(0, 0, 100, 100)
    )

    action_mgr = ActionManager()
    action_mgr.backend = backend
    action_mgr.is_supported = True
    action_mgr.is_surface_verified = True
    action_mgr.viewport_context = backend.viewport_context

    action_mgr.invalidate_binding("window_moved")

    assert action_mgr.is_surface_verified is False
    assert action_mgr.viewport_context is None
    assert "window_moved" in action_mgr.support_status_message
    assert backend.viewport_context is None



