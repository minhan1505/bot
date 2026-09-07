"""
tests/test_bundle.py
~~~~~~~~~~~~~~~~~~~~
Unit tests for Profile ZIP export and safe import with Zip Slip defense (FR-022).
"""

import os
import zipfile
import pytest
import numpy as np
import cv2

from bot.core.models import Profile, Target, RegionModel, Workflow, WorkflowStep
from bot.core.bundle import ProfileBundleManager


def test_profile_export_and_safe_import_roundtrip(tmp_path):
    # 1. Create target dummy image
    img_path = str(tmp_path / "sample_target.png")
    cv2.imwrite(img_path, np.full((32, 32, 3), 150, dtype=np.uint8))

    # 2. Construct profile
    prof = Profile(
        profile_id="prof_bundle_test",
        name="Bundle Test Profile",
        regions={"r1": RegionModel(region_id="r1", name="R1", x=10, y=10, w=100, h=100)},
        targets={"t1": Target(target_id="t1", name="T1", reference_image_paths=[img_path])},
        workflows={"w1": Workflow(workflow_id="w1", name="W1", steps=[WorkflowStep(step_index=0, target_id="t1")])}
    )

    zip_file = str(tmp_path / "exported_profile.zip")
    ProfileBundleManager.export_profile_to_zip(prof, zip_file)
    assert os.path.exists(zip_file)

    # 3. Import back into a different targets directory
    dest_dir = str(tmp_path / "imported_targets")
    imported_prof, msg = ProfileBundleManager.safe_import_profile_from_zip(zip_file, dest_targets_dir=dest_dir)

    assert imported_prof is not None
    assert msg == "IMPORT_SUCCESS"
    assert imported_prof.profile_id == prof.profile_id
    assert "t1" in imported_prof.targets
    # Verify local file was extracted and exists
    assert os.path.exists(imported_prof.targets["t1"].reference_image_paths[0])


def test_zip_slip_path_traversal_attack_rejected(tmp_path):
    # Construct malicious zip archive containing path traversal entry
    malicious_zip = str(tmp_path / "malicious.zip")
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("../../system32_payload.txt", "MALICIOUS CONTENT")
        zf.writestr("profile.json", "{}")

    imported_prof, msg = ProfileBundleManager.safe_import_profile_from_zip(malicious_zip)
    assert imported_prof is None
    assert "SECURITY_ERROR" in msg
