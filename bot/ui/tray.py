"""
bot.ui.tray
~~~~~~~~~~~
Windows System Tray Manager (FR-091).
Provides:
  - Background system tray icon with real-time status tooltip.
  - Quick Start / Stop and Emergency Stop actions.
  - Minimize-to-tray and restore functionality.
"""

from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PySide6.QtGui import QIcon, QPixmap, QColor, QPainter
from PySide6.QtCore import Signal, QObject
from typing import Callable, Optional


def create_tray_pixmap(is_running: bool) -> QPixmap:
    """Creates a clean dynamic tray icon pixmap."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0)) # Transparent
    painter = QPainter(pixmap)
    color = QColor(46, 125, 50) if is_running else QColor(198, 40, 40) # Green if running, Red if stopped
    painter.setBrush(color)
    painter.setPen(QColor(255, 255, 255))
    painter.drawEllipse(4, 4, 24, 24)
    painter.end()
    return pixmap


class SystemTrayManager(QObject):
    """Manages QSystemTrayIcon integration with main application."""
    toggle_bot_requested = Signal()
    emergency_stop_requested = Signal()

    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.parent_window = parent_window
        self.tray_icon = QSystemTrayIcon(parent_window)

        self._is_running = False
        self._init_tray()

    def _init_tray(self):
        # Create context menu
        self.menu = QMenu()

        self.action_show = self.menu.addAction("Show Dashboard")
        self.action_show.triggered.connect(self._on_show_triggered)

        self.action_toggle = self.menu.addAction("Start Bot")
        self.action_toggle.triggered.connect(self.toggle_bot_requested.emit)

        self.action_emergency = self.menu.addAction("EMERGENCY STOP (F12)")
        self.action_emergency.triggered.connect(self.emergency_stop_requested.emit)

        self.menu.addSeparator()

        self.action_quit = self.menu.addAction("Quit Application")
        self.action_quit.triggered.connect(QApplication.instance().quit)

        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._on_activated)

        self.update_status(is_running=False, profile_name="None", backend_status="NOT_PROBED")
        self.tray_icon.show()

    def update_status(self, is_running: bool, profile_name: str, backend_status: str):
        self._is_running = is_running
        self.tray_icon.setIcon(QIcon(create_tray_pixmap(is_running)))

        status_str = "RUNNING" if is_running else "STOPPED"
        self.tray_icon.setToolTip(f"BotAutoClick V2.3\nStatus: {status_str}\nProfile: {profile_name}\nBackend: {backend_status}")
        self.action_toggle.setText("Stop Bot" if is_running else "Start Bot")

    def _on_show_triggered(self):
        self.parent_window.showNormal()
        self.parent_window.activateWindow()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._on_show_triggered()
