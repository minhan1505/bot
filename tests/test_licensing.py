"""
tests/test_licensing.py
~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for Hardware Machine ID (HWID) and cryptographic license verification (FR-002).
"""

import os
import time
import json
import pytest
from bot.core.licensing import get_machine_id, generate_license_data, verify_license_file


def test_machine_id_generation():
    mid = get_machine_id()
    assert isinstance(mid, str)
    assert len(mid) == 16
    # Same machine ID must be consistent across calls
    assert mid == get_machine_id()


def test_license_creation_and_verification_success(tmp_path):
    mid = get_machine_id()
    lic_str = generate_license_data(mid, days_valid=30, tier="TEST_COMMERCIAL")

    lic_file = tmp_path / "license.dat"
    lic_file.write_text(lic_str, encoding="utf-8")

    valid, reason, payload = verify_license_file(str(lic_file), is_dev_mode=False)
    assert valid
    assert "LICENSE_VALID" in reason
    assert payload["machine_id"] == mid
    assert payload["tier"] == "TEST_COMMERCIAL"


def test_license_tampered_signature_rejected(tmp_path):
    mid = get_machine_id()
    lic_str = generate_license_data(mid, days_valid=30)
    data = json.loads(lic_str)

    # Tamper with tier without updating signature
    data["payload"]["tier"] = "HACKED_UNLIMITED"

    lic_file = tmp_path / "tampered.dat"
    lic_file.write_text(json.dumps(data), encoding="utf-8")

    valid, reason, _ = verify_license_file(str(lic_file), is_dev_mode=False)
    assert not valid
    assert "LICENSE_TAMPERED" in reason


def test_license_machine_mismatch_rejected(tmp_path):
    # License signed for a completely different machine ID
    fake_mid = "FFFF0000AAAA1111"
    lic_str = generate_license_data(fake_mid, days_valid=30)

    lic_file = tmp_path / "wrong_machine.dat"
    lic_file.write_text(lic_str, encoding="utf-8")

    valid, reason, _ = verify_license_file(str(lic_file), is_dev_mode=False)
    assert not valid
    assert "LICENSE_MACHINE_MISMATCH" in reason


def test_license_expired_rejected(tmp_path):
    mid = get_machine_id()
    # Days valid = -1 (already expired)
    lic_str = generate_license_data(mid, days_valid=-1)

    lic_file = tmp_path / "expired.dat"
    lic_file.write_text(lic_str, encoding="utf-8")

    valid, reason, _ = verify_license_file(str(lic_file), is_dev_mode=False)
    assert not valid
    assert "LICENSE_EXPIRED" in reason
