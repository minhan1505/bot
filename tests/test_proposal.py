"""
tests/test_proposal.py
~~~~~~~~~~~~~~~~~~~~~~
Unit tests for Candidate Proposal Engine, NMS, Same-Frame Escalation, and Recall tracking.
"""

import pytest
import numpy as np
import cv2
from bot.core.coordinates import Rect
from bot.vision.proposal import CandidateProposalEngine, CandidateProposal, compute_iou, nms


def test_iou_computation():
    box1 = Rect(10, 10, 20, 20) # area 400
    box2 = Rect(20, 10, 20, 20) # intersection: 10 * 20 = 200, union: 400 + 400 - 200 = 600
    assert compute_iou(box1, box2) == pytest.approx(200.0 / 600.0, abs=1e-4)

    # Completely disjoint
    box3 = Rect(100, 100, 20, 20)
    assert compute_iou(box1, box3) == 0.0


def test_nms_deduplication():
    # Two heavily overlapping proposals
    p1 = CandidateProposal(rect=Rect(10, 10, 30, 30), score=0.9)
    p2 = CandidateProposal(rect=Rect(12, 12, 30, 30), score=0.8) # overlap > 0.7
    p3 = CandidateProposal(rect=Rect(100, 100, 30, 30), score=0.85)

    kept = nms([p1, p2, p3], iou_threshold=0.35)
    assert len(kept) == 2
    assert kept[0].score == 0.9
    assert kept[1].score == 0.85


def test_same_frame_escalation_under_batch_limit():
    engine = CandidateProposalEngine(k_base_per_region=4, max_batch_limit=32)

    # Region 1 has 6 proposals (overflows K=4), Region 2 has 3 proposals
    # Total = 9 <= 32 -> Exhaustive Escalation must KEEP ALL 9 in the SAME FRAME!
    proposals_by_region = {
        "r1": [CandidateProposal(rect=Rect(i*10, 10, 20, 20), score=0.5) for i in range(6)],
        "r2": [CandidateProposal(rect=Rect(i*10, 50, 20, 20), score=0.6) for i in range(3)]
    }

    final_props, diag = engine.apply_same_frame_escalation(proposals_by_region)
    assert len(final_props) == 9
    assert diag["r1"] == "EXHAUSTIVE_ESCALATION_KEPT"
    assert diag["r2"] == "NORMAL_OK"


def test_same_frame_escalation_over_batch_limit():
    engine = CandidateProposalEngine(k_base_per_region=4, max_batch_limit=10)

    # Region 1 has 8 proposals, Region 2 has 8 proposals -> Total = 16 > 10
    # Must cap each region to K=4 and flag PROPOSAL_OVERFLOW
    proposals_by_region = {
        "r1": [CandidateProposal(rect=Rect(i*10, 10, 20, 20), score=float(i)) for i in range(8)],
        "r2": [CandidateProposal(rect=Rect(i*10, 50, 20, 20), score=float(i)) for i in range(8)]
    }

    final_props, diag = engine.apply_same_frame_escalation(proposals_by_region)
    assert "PROPOSAL_OVERFLOW" in diag["r1"]
    assert "PROPOSAL_OVERFLOW" in diag["r2"]
    # Final proposals cannot exceed max_batch_limit
    assert len(final_props) <= 10
