import json
import os
import time
import uuid
import threading
import queue
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ReservationToken:
    token_id: str
    slots_reserved: int
    slots_consumed: int = 0
    created_at: float = field(default_factory=time.monotonic)
    expires_at: float = 0.0
    state: str = "RESERVED"  # "RESERVED" | "CONSUMED" | "RELEASED" | "EXPIRED"


class AsyncTelemetryLogger:
    """Non-blocking background JSONL logger with atomic slot reservation for critical evidence."""

    def __init__(self, log_filepath: str = "logs/telemetry.jsonl", maxsize: int = 10000):
        self.log_filepath = log_filepath
        os.makedirs(os.path.dirname(os.path.abspath(log_filepath)), exist_ok=True)
        self._maxsize = maxsize
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._stop_event = threading.Event()
        self._dropped_count = 0
        self._lock = threading.Lock()
        self._reserved_slots: int = 0
        self._active_tokens: Dict[str, ReservationToken] = {}
        self._worker_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._worker_thread.start()

    def _writer_loop(self):
        with open(self.log_filepath, "a", encoding="utf-8") as f:
            while not self._stop_event.is_set() or not self._queue.empty():
                try:
                    self._reap_expired_tokens()
                    entry = self._queue.get(timeout=0.1)
                    line = json.dumps(entry, ensure_ascii=False) + "\n"
                    f.write(line)
                    f.flush()
                    self._queue.task_done()
                except queue.Empty:
                    continue
                except Exception as exc:
                    logger.error(f"Error writing telemetry entry: {exc}")

    def _reap_expired_tokens(self):
        now = time.monotonic()
        with self._lock:
            expired_ids = [tid for tid, tok in self._active_tokens.items() if now >= tok.expires_at]
            for tid in expired_ids:
                tok = self._active_tokens.pop(tid)
                remaining = tok.slots_reserved - tok.slots_consumed
                self._reserved_slots = max(0, self._reserved_slots - remaining)
                tok.state = "EXPIRED"
                logger.warning(f"Reclaimed {remaining} slots from expired telemetry reservation {tid}")

    def reserve_critical_slots(self, count: int = 2, timeout_sec: float = 2.0) -> Optional[ReservationToken]:
        """
        Atomically reserves slots in the telemetry buffer before an action is attempted.
        Returns a ReservationToken if slots were secured, or None if buffer capacity is exhausted.
        """
        with self._lock:
            available = self._maxsize - (self._queue.qsize() + self._reserved_slots)
            if available < count:
                return None

            now = time.monotonic()
            tok = ReservationToken(
                token_id=f"res_{uuid.uuid4().hex[:8]}",
                slots_reserved=count,
                expires_at=now + timeout_sec,
                state="RESERVED"
            )
            self._reserved_slots += count
            self._active_tokens[tok.token_id] = tok
            return tok

    def consume(self, token: ReservationToken, event_type: str, data: Dict[str, Any]) -> bool:
        """
        Consumes one reserved slot to write a critical evidence entry.
        """
        now = time.monotonic()
        with self._lock:
            if token.state != "RESERVED" or token.token_id not in self._active_tokens:
                logger.error(f"Cannot consume slot: token {token.token_id} state is {token.state}")
                return False
            if now >= token.expires_at:
                token.state = "EXPIRED"
                remaining = token.slots_reserved - token.slots_consumed
                self._reserved_slots = max(0, self._reserved_slots - remaining)
                self._active_tokens.pop(token.token_id, None)
                return False
            if token.slots_consumed >= token.slots_reserved:
                return False

            token.slots_consumed += 1
            self._reserved_slots = max(0, self._reserved_slots - 1)
            if token.slots_consumed >= token.slots_reserved:
                token.state = "CONSUMED"
                self._active_tokens.pop(token.token_id, None)

        entry = {
            "event_type": event_type,
            "wall_time": time.time(),
            "monotonic_time": time.perf_counter(),
            "token_id": token.token_id,
            "data": data
        }
        try:
            self._queue.put_nowait(entry)
            return True
        except queue.Full:
            self._dropped_count += 1
            return False

    def release(self, token: ReservationToken):
        """
        Releases any unconsumed reserved slots back to the buffer when an action is aborted.
        """
        with self._lock:
            if token.state == "RESERVED" and token.token_id in self._active_tokens:
                remaining = token.slots_reserved - token.slots_consumed
                self._reserved_slots = max(0, self._reserved_slots - remaining)
                token.state = "RELEASED"
                self._active_tokens.pop(token.token_id, None)

    def log_event(self, event_type: str, data: Dict[str, Any]):
        """Non-blocking log submission for non-critical telemetry."""
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
