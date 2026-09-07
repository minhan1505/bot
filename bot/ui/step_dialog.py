"""
bot.ui.step_dialog
~~~~~~~~~~~~~~~~~~
Dialog for adding and editing dynamic workflow steps.
Enforces:
  - Explicit target selection from current profile (no hardcoded symbols).
  - Explicit action configuration: ActionType, timeout, cooldown, retry limit.
"""

from typing import Optional, Dict
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QSpinBox, QPushButton, QLabel, QMessageBox
)
from PySide6.QtCore import Qt

from bot.core.models import Profile, WorkflowStep, ActionType


class WorkflowStepDialog(QDialog):
    """Interactive modal dialog for configuring a dynamic workflow step."""

    def __init__(self, profile: Profile, step: Optional[WorkflowStep] = None, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.step = step
        self.setWindowTitle("Edit Workflow Step" if step else "Add Workflow Step")
        self.resize(400, 260)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Target dropdown
        self.combo_target = QComboBox()
        self._target_keys = []
        if not self.profile.targets:
            self.combo_target.addItem("(No targets available)", "")
        else:
            for t_id, t in self.profile.targets.items():
                label = f"{t.name} [{t_id}]"
                self.combo_target.addItem(label, t_id)
                self._target_keys.append(t_id)

        if step and step.target_id in self._target_keys:
            idx = self._target_keys.index(step.target_id)
            self.combo_target.setCurrentIndex(idx)

        form.addRow("Target:", self.combo_target)

        # Action Type dropdown
        self.combo_action = QComboBox()
        for at in ActionType:
            self.combo_action.addItem(at.value, at)
        if step:
            idx = self.combo_action.findText(step.action_type.value)
            if idx >= 0:
                self.combo_action.setCurrentIndex(idx)
        form.addRow("Action Type:", self.combo_action)

        # Timeout (ms)
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(100, 60000)
        self.spin_timeout.setSingleStep(500)
        self.spin_timeout.setValue(step.timeout_ms if step else 5000)
        self.spin_timeout.setSuffix(" ms")
        form.addRow("Detection Timeout:", self.spin_timeout)

        # Cooldown (ms)
        self.spin_cooldown = QSpinBox()
        self.spin_cooldown.setRange(50, 10000)
        self.spin_cooldown.setSingleStep(50)
        self.spin_cooldown.setValue(step.cooldown_ms if step else 500)
        self.spin_cooldown.setSuffix(" ms")
        form.addRow("Action Cooldown:", self.spin_cooldown)

        # Max Retries
        self.spin_retries = QSpinBox()
        self.spin_retries.setRange(1, 10)
        self.spin_retries.setValue(step.retry_limit if step else 3)
        form.addRow("Max Retries:", self.spin_retries)

        layout.addLayout(form)

        # Button Bar
        btn_bar = QHBoxLayout()
        self.btn_save = QPushButton("Save Step" if step else "Add Step")
        self.btn_save.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 14px;")
        self.btn_save.clicked.connect(self._on_save)
        btn_bar.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(self.btn_cancel)

        layout.addLayout(btn_bar)

    def _on_save(self):
        t_id = self.combo_target.currentData()
        if not t_id:
            QMessageBox.warning(self, "Invalid Target", "Please select a valid target for this step.")
            return
        self.accept()

    def get_step(self, step_index: int = 0) -> WorkflowStep:
        """Returns a configured WorkflowStep."""
        t_id = self.combo_target.currentData()
        act = self.combo_action.currentData()
        return WorkflowStep(
            step_index=step_index,
            target_id=t_id,
            action_type=act,
            timeout_ms=self.spin_timeout.value(),
            cooldown_ms=self.spin_cooldown.value(),
            retry_limit=self.spin_retries.value()
        )
