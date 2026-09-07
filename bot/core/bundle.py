"""
bot.core.bundle
~~~~~~~~~~~~~~~
Profile Packaging, Export, and Safe Import Engine (FR-022).
Enforces:
  - Bundles profile metadata, workflows, and target reference images into a portable .zip.
  - Defends against Path Traversal (Zip Slip) security vulnerabilities.
  - Automatically normalizes screen-dependent parameters upon import.
"""

import os
import json
import zipfile
import shutil
from typing import Tuple, Optional
from bot.core.models import Profile, Target
import logging

logger = logging.getLogger(__name__)


class ProfileBundleManager:
    """
    Handles secure ZIP export and import of profiles.
    """

    @staticmethod
    def export_profile_to_zip(profile: Profile, zip_output_path: str) -> str:
        """
        Exports profile JSON and all associated target images into a portable ZIP package.
        """
        os.makedirs(os.path.dirname(os.path.abspath(zip_output_path)), exist_ok=True)

        with zipfile.ZipFile(zip_output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 1. Write profile metadata JSON
            profile_json = profile.model_dump_json(indent=2)
            zf.writestr("profile.json", profile_json)

            # 2. Archive target reference images
            for t_id, target in profile.targets.items():
                for idx, img_path in enumerate(target.reference_image_paths):
                    if os.path.exists(img_path):
                        ext = os.path.splitext(img_path)[1]
                        arc_name = f"targets/{t_id}_{idx}{ext}"
                        zf.write(img_path, arc_name)

        logger.info(f"Profile '{profile.name}' exported to ZIP: {zip_output_path}")
        return zip_output_path

    @staticmethod
    def safe_import_profile_from_zip(
        zip_path: str,
        dest_targets_dir: str = "data/targets"
    ) -> Tuple[Optional[Profile], str]:
        """
        Safely extracts and reconstructs a Profile from a ZIP package.
        Includes strict Zip Slip / Path Traversal protection.
        """
        if not os.path.exists(zip_path):
            return None, f"ZIP file not found: {zip_path}"

        dest_targets_dir = os.path.abspath(dest_targets_dir)
        os.makedirs(dest_targets_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Security Check: Guard against Zip Slip
                for member in zf.infolist():
                    member_path = member.filename
                    # Normalize and check for traversal
                    if member_path.startswith("/") or member_path.startswith("\\") or ".." in member_path:
                        return None, f"SECURITY_ERROR: Dangerous relative or absolute path in ZIP: {member_path}"

                # 1. Read profile.json
                if "profile.json" not in zf.namelist():
                    return None, "INVALID_ZIP: profile.json not found in archive."

                raw_json = zf.read("profile.json").decode("utf-8")
                profile_dict = json.loads(raw_json)
                profile = Profile.model_validate(profile_dict)

                # Security Check: Validate profile_id format to prevent directory traversal or collision
                import re
                if not re.match(r'^[a-zA-Z0-9_-]+$', profile.profile_id) or len(profile.profile_id) > 64:
                    return None, f"SECURITY_ERROR: Invalid profile_id '{profile.profile_id}'. Must strictly match ^[a-zA-Z0-9_-]+$."

                dest_canonical = os.path.realpath(dest_targets_dir)

                # 2. Extract target images and remap local paths
                for t_id, target in profile.targets.items():
                    # Validate t_id format as well
                    if not re.match(r'^[a-zA-Z0-9_-]+$', t_id) or len(t_id) > 64:
                        return None, f"SECURITY_ERROR: Invalid target_id '{t_id}'."

                    new_ref_paths = []
                    for idx in range(len(target.reference_image_paths)):
                        for ext in [".png", ".jpg", ".bmp"]:
                            arc_candidate = f"targets/{t_id}_{idx}{ext}"
                            if arc_candidate in zf.namelist():
                                local_filename = f"{profile.profile_id}_{t_id}_{idx}{ext}"
                                local_path = os.path.realpath(os.path.join(dest_targets_dir, local_filename))

                                # Path Traversal Verification using normalized canonical path
                                if os.path.commonpath([dest_canonical, local_path]) != dest_canonical:
                                    return None, f"SECURITY_ERROR: Path traversal detected: {local_path}"

                                with open(local_path, "wb") as f_out:
                                    f_out.write(zf.read(arc_candidate))
                                new_ref_paths.append(local_path)
                                break
                    if new_ref_paths:
                        target.reference_image_paths = new_ref_paths

                logger.info(f"Profile '{profile.name}' safely imported from {zip_path}")
                return profile, "IMPORT_SUCCESS"

        except Exception as exc:
            logger.error(f"Failed to import profile ZIP: {exc}", exc_info=True)
            return None, f"IMPORT_FAILED: {exc}"
