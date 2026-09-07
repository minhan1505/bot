"""
bot.core.database
~~~~~~~~~~~~~~~~~
SQLite persistence layer for Profiles, Targets, Workflows, and Audit Records.
"""

import sqlite3
import json
import os
from typing import Optional, List, Dict
from bot.core.models import Profile, DecisionResult
import logging

logger = logging.getLogger(__name__)


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
            """, (profile.profile_id, profile.name, profile.model_dump_json(), profile.created_at))
            conn.commit()

    def load_profile(self, profile_id: str) -> Optional[Profile]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data_json FROM profiles WHERE profile_id = ?", (profile_id,))
            row = cursor.fetchone()
            if row:
                data = json.loads(row["data_json"])
                return Profile.model_validate(data)
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
