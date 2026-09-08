"""
bot.ui.crop_overlay
~~~~~~~~~~~~~~~~~~~
Direct Screen Crop Tool using Physical Pixel Frame.
Enforces:
  - 100% physical pixel fidelity: captures exact screen buffer before rendering overlay.
  - TARGET_NOT_GEOMETRICALLY_SEPARABLE Guard (Execution Guard #6):
    Validates crop immediately and alerts user if geometry features are insufficient.
"""

from PySide6.QtWidgets import QWidget, QRubberBand, QMessageBox, QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QPen
import cv2
import numpy as np
from typing import Optional, Tuple
from bot.vision.geometry import check_geometry_separability


def numpy_bgr_to_qpixmap(bgr_img: np.ndarray) -> QPixmap:
    """Converts numpy BGR image to QPixmap."""
    h, w, ch = bgr_img.shape
    bytes_per_line = ch * w
    rgb_img = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2RGB)
    qimg = QImage(rgb_img.data, w, h, bytes_per_line, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg)


class CropPreviewDialog(QDialog):
    """Preview dialog showing cropped target with geometry separability diagnostic."""

    def __init__(self, crop_bgr: np.ndarray, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crop Target Preview & Separability Check")
        self.crop_bgr = crop_bgr
        self.accepted_crop = False

        layout = QVBoxLayout(self)

        # Image preview
        pixmap = numpy_bgr_to_qpixmap(crop_bgr)
        self.img_label = QLabel()
        self.img_label.setPixmap(pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.img_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.img_label)

        # Dimension info
        h, w = crop_bgr.shape[:2]
        self.info_label = QLabel(f"Dimensions: {w}x{h} physical pixels")
        self.info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.info_label)

        # Guard #6: Geometry Separability Check
        is_separable, reason = check_geometry_separability(crop_bgr)
        self.diag_label = QLabel()
        if is_separable:
            self.diag_label.setText("✔ Geometry Separable: Structure & contours verified.")
            self.diag_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        else:
            self.diag_label.setText(f"✖ {reason}")
            self.diag_label.setStyleSheet("color: #c62828; font-weight: bold;")
        layout.addWidget(self.diag_label)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Use Target")
        self.btn_save.setEnabled(is_separable)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel = QPushButton("Cancel / Re-crop")
        self.btn_cancel.clicked.connect(self.reject)

        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def _on_save(self):
        self.accepted_crop = True
        self.accept()


class ScreenCropOverlay(QWidget):
    """
    Fullscreen overlay that displays the frozen physical-pixel screenshot
    and allows rubberband rectangle selection.
    """
    target_cropped = Signal(np.ndarray) # Emits cropped BGR numpy array
    region_selected = Signal(int, int, int, int) # Emits (x, y, w, h) for ROI selection

    def __init__(self, physical_frame: np.ndarray, desktop_offset: Tuple[int, int] = (0, 0), mode: str = "target"):
        super().__init__()
        self.physical_frame = physical_frame
        self.desktop_offset = desktop_offset
        self.mode = mode
        self.origin = QPoint()
        self.rubber_band = QRubberBand(QRubberBand.Rectangle, self)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # Move to physical desktop offset and resize
        h, w = physical_frame.shape[:2]
        self.setGeometry(desktop_offset[0], desktop_offset[1], w, h)
        self.pixmap = numpy_bgr_to_qpixmap(physical_frame)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.pixmap)
        # Draw subtle darkening tint
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.origin = event.pos()
            self.rubber_band.setGeometry(QRect(self.origin, self.origin))
            self.rubber_band.show()

    def mouseMoveEvent(self, event):
        if not self.origin.isNull():
            self.rubber_band.setGeometry(QRect(self.origin, event.pos()).normalized())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.rubber_band.hide()
            rect = self.rubber_band.geometry()
            self.close()

            if rect.width() >= 8 and rect.height() >= 8:
                x = max(0, rect.x())
                y = max(0, rect.y())
                w = min(rect.width(), self.physical_frame.shape[1] - x)
                h = min(rect.height(), self.physical_frame.shape[0] - y)

                if self.mode == "region":
                    self.region_selected.emit(x, y, w, h)
                else:
                    crop = self.physical_frame[y:y + h, x:x + w].copy()
                    preview = CropPreviewDialog(crop)
                    if preview.exec() == QDialog.Accepted and preview.accepted_crop:
                        self.target_cropped.emit(crop)
