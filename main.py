"""
main.py
~~~~~~~
BotAutoClick V2.3 Main Application Entrypoint.
Enforces FR-001: Enables Windows Per-Monitor DPI Awareness V2
BEFORE initializing QApplication.
"""

import sys
import logging
from PySide6.QtWidgets import QApplication

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BotAutoClick_V2")

# Step 1: Enforce DPI Awareness BEFORE QApplication creation
from bot.core.dpi import enable_dpi_awareness_v2
enable_dpi_awareness_v2()

# Step 2: Import MainWindow and initialize Qt
from bot.ui.main_window import MainWindow


def main():
    logger.info("Starting BotAutoClick V2.3 Desktop Application...")
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
