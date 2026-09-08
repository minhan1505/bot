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

            # 2. Archive target reference and confuser images (FC-12)
            for t_id, target in profile.targets.items():
                for idx, img_path in enumerate(target.reference_image_paths):
                    if os.path.exists(img_path):
                        ext = os.path.splitext(img_path)[1]
                        arc_name = f"targets/{t_id}_ref_{idx}{ext}"
                        zf.write(img_path, arc_name)

                for idx, img_path in enumerate(target.confuser_image_paths):
                    if os.path.exists(img_path):
                        ext = os.path.splitext(img_path)[1]
                        arc_name = f"confusers/{t_id}_conf_{idx}{ext}"
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
        Includes strict Zip Slip / Path Traversal protection and geometry revalidation (FC-12).
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

                from bot.core.database import migrate_profile_data
                profile, _ = migrate_profile_data(profile_dict)

                # Security Check: Validate profile_id format to prevent directory traversal or collision
                import re
                if not re.match(r'^[a-zA-Z0-9_-]+$', profile.profile_id) or len(profile.profile_id) > 64:
                    return None, f"SECURITY_ERROR: Invalid profile_id '{profile.profile_id}'. Must strictly match ^[a-zA-Z0-9_-]+$."

                dest_canonical = os.path.realpath(dest_targets_dir)

                # 2. Extract target reference and confuser images and remap local paths
                for t_id, target in profile.targets.items():
                    if not re.match(r'^[a-zA-Z0-9_-]+$', t_id) or len(t_id) > 64:
                        return None, f"SECURITY_ERROR: Invalid target_id '{t_id}'."

                    # Reference images
                    new_ref_paths = []
                    for idx in range(len(target.reference_image_paths)):
                        for ext in [".png", ".jpg", ".bmp"]:
                            arc_candidates = [
                                f"targets/{t_id}_ref_{idx}{ext}",
                                f"targets/{t_id}_{idx}{ext}"  # Backwards compatibility
                            ]
                            for arc_candidate in arc_candidates:
                                if arc_candidate in zf.namelist():
                                    local_filename = f"{profile.profile_id}_{t_id}_ref_{idx}{ext}"
                                    local_path = os.path.realpath(os.path.join(dest_targets_dir, local_filename))

                                    if os.path.commonpath([dest_canonical, local_path]) != dest_canonical:
                                        return None, f"SECURITY_ERROR: Path traversal detected: {local_path}"

                                    with open(local_path, "wb") as f_out:
                                        f_out.write(zf.read(arc_candidate))
                                    new_ref_paths.append(local_path)
                                    break
                    if new_ref_paths:
                        target.reference_image_paths = new_ref_paths

                    # Confuser images (FC-12)
                    new_conf_paths = []
                    for idx in range(len(target.confuser_image_paths)):
                        for ext in [".png", ".jpg", ".bmp"]:
                            arc_candidate = f"confusers/{t_id}_conf_{idx}{ext}"
                            if arc_candidate in zf.namelist():
                                local_filename = f"{profile.profile_id}_{t_id}_conf_{idx}{ext}"
                                local_path = os.path.realpath(os.path.join(dest_targets_dir, local_filename))

                                if os.path.commonpath([dest_canonical, local_path]) != dest_canonical:
                                    return None, f"SECURITY_ERROR: Path traversal detected: {local_path}"

                                with open(local_path, "wb") as f_out:
                                    f_out.write(zf.read(arc_candidate))
                                new_conf_paths.append(local_path)
                                break
                    if new_conf_paths:
                        target.confuser_image_paths = new_conf_paths

                # 3. Geometry Revalidation & Safe Re-resolution (FC-12, U08)
                geometry_rebound = False
                try:
                    import mss
                    mss_cls = getattr(mss, "MSS", mss.mss)
                    with mss_cls() as sct:
                        num_mons = len(sct.monitors) - 1
                        # Rebind monitor_index if it doesn't exist on this hardware
                        if profile.monitor_index < 0 or profile.monitor_index > num_mons:
                            logger.warning(
                                f"Imported monitor index {profile.monitor_index} does not exist on this machine "
                                f"(available: 0..{num_mons}). Rebinding to primary monitor 1."
                            )
                            profile.monitor_index = 1
                            geometry_rebound = True

                        target_mon = sct.monitors[profile.monitor_index]
                        m_w, m_h = target_mon["width"], target_mon["height"]

                        # Revalidate and clamp/reset ROI
                        if profile.roi:
                            rx, ry, rw, rh = profile.roi
                            if rx < 0 or ry < 0 or rw <= 0 or rh <= 0 or rx + rw > m_w or ry + rh > m_h:
                                logger.warning(
                                    f"Imported ROI {profile.roi} exceeds Monitor {profile.monitor_index} bounds ({m_w}x{m_h}). "
                                    f"Resetting ROI to full-screen capture."
                                )
                                profile.roi = None
                                geometry_rebound = True

                        # Revalidate and clamp all region coordinates
                        for r_id, reg in profile.regions.items():
                            orig_box = (reg.x, reg.y, reg.w, reg.h)
                            if reg.x < 0 or reg.y < 0 or reg.w <= 0 or reg.h <= 0 or reg.x + reg.w > m_w or reg.y + reg.h > m_h:
                                reg.x = max(0, min(reg.x, m_w - 32))
                                reg.y = max(0, min(reg.y, m_h - 32))
                                reg.w = max(16, min(reg.w, m_w - reg.x))
                                reg.h = max(16, min(reg.h, m_h - reg.y))
                                logger.warning(
                                    f"Imported region '{r_id}' coordinates {orig_box} were out of bounds for "
                                    f"Monitor {profile.monitor_index} ({m_w}x{m_h}). Clamped to ({reg.x}, {reg.y}, {reg.w}, {reg.h})."
                                )
                                geometry_rebound = True

                except Exception as geo_err:
                    logger.warning(f"Geometry revalidation check encountered warning: {geo_err}")

                logger.info(f"Profile '{profile.name}' safely imported from {zip_path}")
                if geometry_rebound:
                    return profile, "IMPORT_SUCCESS_GEOMETRY_REBOUND"
                return profile, "IMPORT_SUCCESS"

        except Exception as exc:
            logger.error(f"Failed to import profile ZIP: {exc}", exc_info=True)
            return None, f"IMPORT_FAILED: {exc}"
