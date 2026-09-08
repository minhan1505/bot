"""
bot.core.database
~~~~~~~~~~~~~~~~~
SQLite persistence layer for Profiles, Targets, Workflows, and Audit Records.
"""

import sqlite3
import json
import os
from typing import Optional, List, Dict, Any, Tuple
from bot.core.models import Profile, DecisionResult, SCHEMA_VERSION
import logging
import time

logger = logging.getLogger(__name__)


def migrate_profile_data(data: Dict[str, Any]) -> Tuple[Profile, bool]:
    """
    Migrates legacy profile data (e.g. V2.3) to current schema version.
    Returns (Profile, was_migrated: bool).
    """
    migrated = False
    current_version = data.get("schema_version", 1)

    if current_version < SCHEMA_VERSION:
        migrated = True
        data["schema_version"] = SCHEMA_VERSION
        if "emergency_hotkey" not in data:
            data["emergency_hotkey"] = "F12"
        if "safety_config" in data and isinstance(data["safety_config"], dict):
            if "auto_stop_minutes" not in data["safety_config"]:
                data["safety_config"]["auto_stop_minutes"] = 0.0

        # Validate and patch targets
        if "targets" in data and isinstance(data["targets"], dict):
            for t_id, t_data in data["targets"].items():
                if isinstance(t_data, dict):
                    if "enabled" not in t_data:
                        t_data["enabled"] = True
                    if "reference_image_paths" not in t_data:
                        t_data["reference_image_paths"] = []
                    if "confuser_image_paths" not in t_data:
                        t_data["confuser_image_paths"] = []
                    # Invalidate legacy unhashed calibration (X04)
                    calib = t_data.get("calibration")
                    if isinstance(calib, dict) and not calib.get("target_content_hash"):
                        logger.warning(
                            f"Invalidating legacy unhashed calibration for target '{t_id}' in profile '{data.get('name')}'"
                        )
                        t_data["calibration"] = None

        logger.info(f"Migrated profile '{data.get('name')}' from schema v{current_version} to v{SCHEMA_VERSION}")

    # Also invalidate unhashed calibration for targets even if schema_version matches (X04)
    elif "targets" in data and isinstance(data["targets"], dict):
        for t_id, t_data in data["targets"].items():
            if isinstance(t_data, dict):
                calib = t_data.get("calibration")
                if isinstance(calib, dict) and not calib.get("target_content_hash"):
                    logger.warning(
                        f"Invalidating legacy unhashed calibration for target '{t_id}' in profile '{data.get('name')}'"
                    )
                    t_data["calibration"] = None
                    migrated = True

    profile = Profile.model_validate(data)
    return profile, migrated


class Database:
    def __init__(self, db_path: str = "bot_data.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Profiles table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    profile_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            # Snapshots table (FC-04)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            # Audit log table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    region_id TEXT,
                    target_id TEXT,
                    decision TEXT NOT NULL,
                    latency_ms REAL,
                    data_json TEXT NOT NULL
                )
            """)
            conn.commit()

    def save_profile(self, profile: Profile):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO profiles (profile_id, name, data_json, updated_at)
                VALUES (?, ?, ?, ?)
            """, (profile.profile_id, profile.name, profile.model_dump_json(), time.time()))
            conn.commit()

    def load_profile(self, profile_id: str) -> Optional[Profile]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data_json FROM profiles WHERE profile_id = ?", (profile_id,))
            row = cursor.fetchone()
            if row:
                data = json.loads(row["data_json"])
                profile, was_migrated = migrate_profile_data(data)
                if was_migrated:
                    self.save_profile(profile)
                return profile
        return None

    def list_profiles(self) -> List[Dict[str, str]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT profile_id, name FROM profiles ORDER BY updated_at DESC")
            return [{"profile_id": row["profile_id"], "name": row["name"]} for row in cursor.fetchall()]

    def delete_profile(self, profile_id: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM profiles WHERE profile_id = ?", (profile_id,))
            conn.commit()

    def rename_profile(self, profile_id: str, new_name: str) -> bool:
        prof = self.load_profile(profile_id)
        if not prof:
            return False
        prof.name = new_name
        self.save_profile(prof)
        return True

    def clone_profile(self, source_profile_id: str, new_profile_id: str, new_name: str) -> Optional[Profile]:
        source = self.load_profile(source_profile_id)
        if not source:
            return None
        data = source.model_dump()
        data["profile_id"] = new_profile_id
        data["name"] = new_name
        data["created_at"] = time.time()
        cloned = Profile.model_validate(data)
        self.save_profile(cloned)
        return cloned

    def create_snapshot(self, profile_id: str, label: str = "") -> Optional[str]:
        prof = self.load_profile(profile_id)
        if not prof:
            return None
        snapshot_id = f"snap_{profile_id}_{int(time.time() * 1000)}"
        snap_label = label.strip() or f"Snapshot of {prof.name}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO snapshots (snapshot_id, profile_id, label, data_json, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (snapshot_id, profile_id, snap_label, prof.model_dump_json(), time.time()))
            conn.commit()
        return snapshot_id

    def list_snapshots(self, profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if profile_id:
                cursor.execute("""
                    SELECT snapshot_id, profile_id, label, created_at
                    FROM snapshots WHERE profile_id = ? ORDER BY created_at DESC
                """, (profile_id,))
            else:
                cursor.execute("""
                    SELECT snapshot_id, profile_id, label, created_at
                    FROM snapshots ORDER BY created_at DESC
                """)
            return [
                {
                    "snapshot_id": row["snapshot_id"],
                    "profile_id": row["profile_id"],
                    "label": row["label"],
                    "created_at": row["created_at"]
                }
                for row in cursor.fetchall()
            ]

    def restore_snapshot(self, snapshot_id: str) -> Optional[Profile]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data_json FROM snapshots WHERE snapshot_id = ?", (snapshot_id,))
            row = cursor.fetchone()
            if row:
                data = json.loads(row["data_json"])
                profile, _ = migrate_profile_data(data)
                self.save_profile(profile)
                return profile
        return None

    def delete_snapshot(self, snapshot_id: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM snapshots WHERE snapshot_id = ?", (snapshot_id,))
            conn.commit()

    def log_decision(self, session_id: str, decision: DecisionResult, latency_ms: Optional[float] = None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO audit_logs (session_id, timestamp, region_id, target_id, decision, latency_ms, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                decision.timestamp,
                decision.region_id,
                decision.target_id,
                decision.decision.value,
                latency_ms,
                decision.model_dump_json()
            ))
            conn.commit()
