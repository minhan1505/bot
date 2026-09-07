"""
bot.telemetry.logger
~~~~~~~~~~~~~~~~~~~~
Async JSONL Structured Logger.
Writes audit and telemetry events on a background worker thread.
Guarantees zero blocking on the critical vision/action path.
"""

import json
import os
import time
import threading
import queue
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class AsyncTelemetryLogger:
    """Non-blocking background JSONL logger."""

    def __init__(self, log_filepath: str = "logs/telemetry.jsonl"):
        self.log_filepath = log_filepath
        os.makedirs(os.path.dirname(os.path.abspath(log_filepath)), exist_ok=True)
        self._queue = queue.Queue(maxsize=10000)
        self._stop_event = threading.Event()
        self._dropped_count = 0
        self._worker_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._worker_thread.start()

    def _writer_loop(self):
        with open(self.log_filepath, "a", encoding="utf-8") as f:
            while not self._stop_event.is_set() or not self._queue.empty():
                try:
                    entry = self._queue.get(timeout=0.1)
                    line = json.dumps(entry, ensure_ascii=False) + "\n"
                    f.write(line)
                    f.flush()
                    self._queue.task_done()
                except queue.Empty:
                    continue
                except Exception as exc:
                    logger.error(f"Error writing telemetry entry: {exc}")

    def log_event(self, event_type: str, data: Dict[str, Any]):
        """Non-blocking log submission. Drops event if queue is completely full."""
        entry = {
            "event_type": event_type,
            "wall_time": time.time(),
            "monotonic_time": time.perf_counter(),
            "data": data
        }
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            self._dropped_count += 1
            if self._dropped_count % 100 == 1:
                logger.warning(f"Telemetry queue full! Dropped {self._dropped_count} entries.")

    def close(self):
        self._stop_event.set()
        self._worker_thread.join(timeout=2.0)
