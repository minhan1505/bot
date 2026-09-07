"""
bot.core.licensing
~~~~~~~~~~~~~~~~~~
Licensing & Hardware ID (HWID) Engine (FR-002).
Enforces:
  - Generates unique machine ID from Windows MachineGuid / Motherboard UUID.
  - Cryptographically verifies license.dat via HMAC-SHA256 signature.
  - Validates authority, device binding, and expiration date.
  - Rejects tampered, corrupt, expired, or wrong-machine license files.
  - Provides Development Mode bypass for internal maintenance.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import winreg
from typing import Tuple, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Master Authority Secret (in production, loaded from environment or secure vault)
AUTHORITY_SECRET = b"BotAutoClick_V2_Master_Secret_Key_2026_Secure"


def get_machine_id() -> str:
    """
    Derives unique hardware machine identifier from Windows registry MachineGuid.
    """
    if sys.platform != "win32":
        return "NON_WINDOWS_DEV_MACHINE"

    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        )
        guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        # Hash guid to produce clean 16-char machine ID
        return hashlib.sha256(guid.encode("utf-8")).hexdigest()[:16].upper()
    except Exception as exc:
        logger.warning(f"Failed to query Windows MachineGuid: {exc}. Using fallback.")
        fallback = f"{os.environ.get('COMPUTERNAME', 'HOST')}_{os.environ.get('USERNAME', 'USER')}"
        return hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:16].upper()


def generate_license_data(machine_id: str, days_valid: int = 30, tier: str = "COMMERCIAL_PRO") -> str:
    """
    Generates cryptographically signed license data string.
    """
    expiry_timestamp = time.time() + (days_valid * 86400)
    payload = {
        "machine_id": machine_id.upper(),
        "tier": tier,
        "authority": "BotAutoClickV2_Authority",
        "expires_at": expiry_timestamp,
        "issued_at": time.time()
    }
    payload_str = json.dumps(payload, sort_keys=True)
    sig = hmac.new(AUTHORITY_SECRET, payload_str.encode("utf-8"), hashlib.sha256).hexdigest()

    license_envelope = {
        "payload": payload,
        "signature": sig
    }
    return json.dumps(license_envelope, indent=2)


def verify_license_file(license_path: str, is_dev_mode: bool = False) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Verifies license.dat file.
    Returns:
      (is_valid, reason_string, payload_dict)
    """
    if is_dev_mode:
        return True, "DEV_MODE_ACTIVE (License check bypassed for development)", None

    if not os.path.exists(license_path):
        return False, f"LICENSE_FILE_NOT_FOUND: {license_path}", None

    try:
        with open(license_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        payload = data.get("payload")
        signature = data.get("signature")

        if not payload or not signature:
            return False, "LICENSE_CORRUPTED: Missing payload or signature", None

        # 1. Verify cryptographic signature
        payload_str = json.dumps(payload, sort_keys=True)
        expected_sig = hmac.new(AUTHORITY_SECRET, payload_str.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return False, "LICENSE_TAMPERED: Cryptographic signature mismatch!", None

        # 2. Verify authority
        if payload.get("authority") != "BotAutoClickV2_Authority":
            return False, "LICENSE_INVALID_AUTHORITY: Unrecognized license authority.", None

        # 3. Verify machine ID
        current_machine = get_machine_id()
        if payload.get("machine_id") != current_machine:
            return False, f"LICENSE_MACHINE_MISMATCH: License bound to {payload.get('machine_id')}, current machine is {current_machine}", None

        # 4. Verify expiration date
        now = time.time()
        expires_at = payload.get("expires_at", 0)
        if now > expires_at:
            expiry_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires_at))
            return False, f"LICENSE_EXPIRED: License expired on {expiry_str}", None

        days_remaining = (expires_at - now) / 86400.0
        return True, f"LICENSE_VALID (Tier: {payload.get('tier')}, {days_remaining:.1f} days remaining)", payload

    except Exception as exc:
        return False, f"LICENSE_VERIFICATION_ERROR: {exc}", None
