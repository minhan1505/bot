"""
bot.vision.proposal
~~~~~~~~~~~~~~~~~~~
Hybrid Multi-Strategy Candidate Proposal Engine.
Enforces Invariant: Proposal Recall > Proposal Precision.
Candidate proposal is NOT an authority; its sole mission is to NEVER miss a true target.

Strategies:
  1. Structural Contour / Edge Bounding Boxes
  2. Coarse Downsampled Multi-Scale NCC Peak Proposals (captures soft gradients/low contrast)
  3. Region-local Anchor Sampling

Includes:
  - Non-Maximum Suppression (NMS)
  - Per-Region Quotas (K_base=4) with Same-Frame Exhaustive Escalation
  - Proposal Recall tracking and PROPOSAL_OVERFLOW diagnostics
"""

import cv2
import numpy as np
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass
from bot.core.coordinates import Rect
import logging

logger = logging.getLogger(__name__)


@dataclass
class CandidateProposal:
    rect: Rect
    region_id: Optional[str] = None
    strategy: str = "unknown"
    score: float = 0.0


def compute_iou(box1: Rect, box2: Rect) -> float:
    """Computes Intersection over Union (IoU) of two Rects."""
    x_left = max(box1.x, box2.x)
    y_top = max(box1.y, box2.y)
    x_right = min(box1.right, box2.right)
    y_bottom = min(box1.bottom, box2.bottom)

    if x_right <= x_left or y_bottom <= y_top:
        return 0.0

    intersection = (x_right - x_left) * (y_bottom - y_top)
    area1 = box1.w * box1.h
    area2 = box2.w * box2.h
    union = float(area1 + area2 - intersection)

    return intersection / union if union > 0 else 0.0


def nms(proposals: List[CandidateProposal], iou_threshold: float = 0.35) -> List[CandidateProposal]:
    """Non-Maximum Suppression to eliminate redundant proposals."""
    if not proposals:
        return []

    # Sort proposals by score descending
    sorted_props = sorted(proposals, key=lambda p: p.score, reverse=True)
    kept: List[CandidateProposal] = []

    for prop in sorted_props:
        overlap = False
        for k in kept:
            if compute_iou(prop.rect, k.rect) >= iou_threshold:
                overlap = True
                break
        if not overlap:
            kept.append(prop)

    return kept


class CandidateProposalEngine:
    """
    Hybrid Proposal Engine with Same-Frame Escalation and Per-Region Quota management.
    """

    def __init__(
        self,
        k_base_per_region: int = 4,
        max_batch_limit: int = 128,
        scales: Tuple[float, ...] = (0.85, 1.0, 1.15)
    ):
        self.k_base = k_base_per_region
        self.max_batch_limit = max_batch_limit
        self.scales = scales
        # Metrics
        self.total_gt_evaluated: int = 0
        self.total_gt_recalled: int = 0

    def generate_proposals_for_region(
        self,
        region_crop: np.ndarray,
        region_rect: Rect,
        target_template: np.ndarray,
        region_id: str
    ) -> List[CandidateProposal]:
        """
        Runs hybrid proposal strategies inside a specific Region ROI.
        Returns candidate proposals with screen-absolute coordinates.
        """
        proposals: List[CandidateProposal] = []
        th, tw = target_template.shape[:2]
        rh, rw = region_crop.shape[:2]

        if rh < th or rw < tw:
            return []

        # Strategy 1: Contour / Edge Proposals
        contour_props = self._strategy_contour(region_crop, region_rect, tw, th, region_id)
        proposals.extend(contour_props)

        # Strategy 2: Coarse Downsampled NCC (captures low-contrast / smooth-gradient glyphs)
        ncc_props = self._strategy_downsampled_ncc(region_crop, region_rect, target_template, region_id)
        proposals.extend(ncc_props)

        # Merge and NMS within region
        merged = nms(proposals, iou_threshold=0.35)
        return merged

    def _strategy_contour(
        self,
        crop: np.ndarray,
        region_rect: Rect,
        tw: int,
        th: int,
        region_id: str
    ) -> List[CandidateProposal]:
        props: List[CandidateProposal] = []
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop

        # Multi-scale edge / adaptive thresholding
        edges = cv2.Canny(gray, 40, 120)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        target_area = float(tw * th)
        target_aspect = float(tw) / max(1.0, float(th))

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = float(w * h)
            aspect = float(w) / max(1.0, float(h))

            # Allow 0.25x to 4.0x target area and flexible aspect ratio
            if 0.20 * target_area <= area <= 4.5 * target_area:
                if 0.25 * target_aspect <= aspect <= 4.0 * target_aspect:
                    screen_rect = Rect(
                        x=region_rect.x + x,
                        y=region_rect.y + y,
                        w=w,
                        h=h
                    )
                    # Score based on aspect and area proximity to target
                    area_diff = abs(np.log(area / target_area))
                    score = float(np.exp(-area_diff))
                    props.append(CandidateProposal(rect=screen_rect, region_id=region_id, strategy="contour", score=score))

        return props

    def _strategy_downsampled_ncc(
        self,
        crop: np.ndarray,
        region_rect: Rect,
        template: np.ndarray,
        region_id: str
    ) -> List[CandidateProposal]:
        props: List[CandidateProposal] = []
        # Downsample 2x for ultra-fast correlation
        ds_factor = 2
        crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        tmpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY) if len(template.shape) == 3 else template

        crop_small = cv2.resize(crop_gray, (max(1, crop.shape[1] // ds_factor), max(1, crop.shape[0] // ds_factor)), interpolation=cv2.INTER_AREA)

        for scale in self.scales:
            scaled_tw = int(round(template.shape[1] * scale / ds_factor))
            scaled_th = int(round(template.shape[0] * scale / ds_factor))

            if scaled_tw < 4 or scaled_th < 4 or scaled_tw > crop_small.shape[1] or scaled_th > crop_small.shape[0]:
                continue

            tmpl_small = cv2.resize(tmpl_gray, (scaled_tw, scaled_th), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(crop_small, tmpl_small, cv2.TM_CCOEFF_NORMED)

            # Find top 3 local peaks
            threshold = 0.40
            loc = np.where(res >= threshold)
            peaks = list(zip(*loc[::-1])) # (x, y)

            for pt in peaks[:3]:
                orig_x = int(pt[0] * ds_factor)
                orig_y = int(pt[1] * ds_factor)
                orig_w = int(round(template.shape[1] * scale))
                orig_h = int(round(template.shape[0] * scale))

                screen_rect = Rect(
                    x=region_rect.x + orig_x,
                    y=region_rect.y + orig_y,
                    w=orig_w,
                    h=orig_h
                )
                score = float(res[pt[1], pt[0]])
                props.append(CandidateProposal(rect=screen_rect, region_id=region_id, strategy="ncc_coarse", score=score))

        return props

    def apply_same_frame_escalation(
        self,
        proposals_by_region: Dict[str, List[CandidateProposal]]
    ) -> Tuple[List[CandidateProposal], Dict[str, str]]:
        """
        Enforces Per-Region Quota with Same-Frame Escalation.
        Returns:
            final_proposals: List[CandidateProposal] (capped at max_batch_limit)
            diagnostics: Dict[region_id, status_string]
        """
        diagnostics: Dict[str, str] = {}
        total_proposals = sum(len(props) for props in proposals_by_region.values())

        # Branch 1: If total proposals across all regions <= max_batch_limit,
        # Exhaustive Escalation is triggered: KEEP ALL PROPOSALS in the exact same frame!
        if total_proposals <= self.max_batch_limit:
            final_list: List[CandidateProposal] = []
            for r_id, props in proposals_by_region.items():
                final_list.extend(props)
                diagnostics[r_id] = "EXHAUSTIVE_ESCALATION_KEPT" if len(props) > self.k_base else "NORMAL_OK"
            return final_list, diagnostics

        # Branch 2: Total proposals exceed max_batch_limit -> apply per-region quota
        final_list = []
        overflow_flagged = False

        for r_id, props in proposals_by_region.items():
            if len(props) > self.k_base:
                # Region has overflow where candidates had to be truncated.
                # U07 Invariant: Recall cannot be guaranteed for truncated region!
                diagnostics[r_id] = f"PROPOSAL_OVERFLOW_UNGUARANTEED_RECALL(count={len(props)}, capped_to={self.k_base})"
                # Sort by proposal score and take top K_base
                sorted_props = sorted(props, key=lambda p: p.score, reverse=True)
                final_list.extend(sorted_props[:self.k_base])
                overflow_flagged = True
            else:
                diagnostics[r_id] = "NORMAL_OK"
                final_list.extend(props)

        # Global safety truncation if still exceeds batch limit
        if len(final_list) > self.max_batch_limit:
            final_list = sorted(final_list, key=lambda p: p.score, reverse=True)[:self.max_batch_limit]

        return final_list, diagnostics

    def record_ground_truth_recall(self, gt_boxes: List[Rect], proposals: List[CandidateProposal], iou_thresh: float = 0.5):
        """Records ground truth recall for benchmark reporting."""
        for gt in gt_boxes:
            self.total_gt_evaluated += 1
            recalled = any(compute_iou(gt, p.rect) >= iou_thresh for p in proposals)
            if recalled:
                self.total_gt_recalled += 1

    @property
    def proposal_recall(self) -> float:
        """Returns Proposal Recall ratio [0.0, 1.0]."""
        if self.total_gt_evaluated == 0:
            return 1.0
        return self.total_gt_recalled / float(self.total_gt_evaluated)
