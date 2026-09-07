# BotAutoClick V2.3 - Code Audit & Verification Guide

> **Target Audience:** External AI Reviewers & Security Auditors (GPT-6-Astra, Claude Code, OpenAI Codex).  
> **Repository:** `https://github.com/minhan1505/bot`  
> **Branch:** `audit`  
> **Standard:** Conforming to Universal Senior Developer Protocol (`quytatchuan.txt`) & Specification Documents (Tech Specs v2.0, Functional Specs v1.2).

---

## 1. Executive Summary

BotAutoClick V2.3 is a clean-slate, production-grade automation system designed for zero-interference background operations, deterministic computer vision matching, and strict safety guarantees.

### Key Architectural Invariants
1. **Symbol/Geometry First Authority:** Appearance/Color is secondary. Geometry is a non-negotiable gate.
2. **Zero Heuristic Thresholds:** Data-driven calibration with Pre-Overlap Rejection on independent sets.
3. **Strict Mouse Independence:** No moving or locking the physical cursor; fail-closed on background action failure.
4. **Hard SLA Latency:** Strictly $\le 700\text{ ms}$ on Declared Supported Workload (up to 19 concurrent regions).
5. **Fail-Safe & Anti-Runaway:** F12 global emergency stop, hardware-bound licensing, and Zip Slip protection.

---

## 2. Auditor Verification Matrix

Please audit the implementation against the following 7 core modules and verification criteria:

### Checklist Item 1: Tri-Condition Decision Authority
- **Module:** [`bot/vision/engine.py`](file:///D:/xampp/bot/bot/vision/engine.py), [`bot/vision/geometry.py`](file:///D:/xampp/bot/bot/vision/geometry.py)
- **Invariant:**
  $$\text{MATCH} = (\text{Geometry PASS}) \land (\text{Embedding PASS}) \land (\text{Identity Margin PASS})$$
- **Verification Points:**
  - Geometry verification is an absolute pre-condition gate.
  - An embedding or color score cannot rescue a failing geometry match.
  - Border clipping artifacts on canonical crops are suppressed to prevent artificial distance inflation.
- **Automated Test:**
  ```powershell
  python -m pytest tests/test_calibration_and_engine.py::test_tri_condition_authority_rejects_different_symbol_with_same_color -v -s
  ```

---

### Checklist Item 2: Zero Heuristic Thresholds & Pre-Overlap Rejection
- **Module:** [`bot/vision/calibration.py`](file:///D:/xampp/bot/bot/vision/calibration.py)
- **Invariant:**
  - No arbitrary constants like $0.15$ or $0.50$.
  - Threshold search only proceeds if positive and negative sets are completely separable on $D_{calib}$.
- **Verification Points:**
  - If $\max(\text{neg}) \ge \min(\text{pos})$ on $D_{calib} \implies$ raises `CALIBRATION_REJECTED_OVERLAP`.
  - Independent held-out set $D_{val}$ verifies frozen thresholds with zero false positives.
  - Calibration is strictly bound to model SHA-256, precision, canonical input size, and preprocessing version.
- **Automated Test:**
  ```powershell
  python -m pytest tests/test_calibration_and_engine.py::test_calibration_pre_overlap_rejection -v -s
  ```

---

### Checklist Item 3: Target Geometry Separability Guard
- **Module:** [`bot/vision/geometry.py`](file:///D:/xampp/bot/bot/vision/geometry.py)
- **Invariant:**
  - Targets without sufficient edge/geometry structure must be rejected at registration/crop time.
- **Verification Points:**
  - `check_geometry_separability()` inspects edge density and standard deviation.
  - If a user uploads a solid color or gradient without contour $\implies$ raises `TARGET_NOT_GEOMETRICALLY_SEPARABLE`.
  - Never falls back to color or embedding-only mode.
- **Automated Test:**
  ```powershell
  python -m pytest tests/test_geometry.py::test_geometry_separability_guard_rejects_flat_targets -v -s
  python -m pytest tests/test_geometry.py::test_geometry_separability_guard_accepts_valid_symbol -v -s
  ```

---

### Checklist Item 4: Strict Mouse Independence & Fail-Closed Action
- **Module:** [`bot/action/manager.py`](file:///D:/xampp/bot/bot/action/manager.py), [`bot/action/cdp_backend.py`](file:///D:/xampp/bot/bot/action/cdp_backend.py), [`bot/action/window_backend.py`](file:///D:/xampp/bot/bot/action/window_backend.py)
- **Invariant:**
  - The physical Windows cursor is NEVER moved or hijacked (`pyautogui` and `SetCursorPos` are forbidden in production).
- **Verification Points:**
  - Actions dispatched via CDP (`Input.dispatchMouseEvent`) or Win32 `PostMessage`.
  - If background action fails or target is unsupported $\implies$ **Fail-Closed** (`BACKGROUND_ACTION_UNSUPPORTED`).
  - Rate Limiter enforces $\le 10\text{ clicks/sec}$.
  - Circuit Breaker trips if $> 30\text{ clicks}$ within 5 seconds.
- **Automated Test:**
  ```powershell
  python -m pytest tests/test_action_and_safety.py -v -s
  ```

---

### Checklist Item 5: Hard SLA Latency ($\le 700\text{ ms}$) on Supported Workload
- **Module:** [`bot/telemetry/benchmark.py`](file:///D:/xampp/bot/bot/telemetry/benchmark.py), [`tests/test_sla_benchmark.py`](file:///D:/xampp/bot/tests/test_sla_benchmark.py)
- **Invariant:**
  - Measured on Declared Supported Workload (19 concurrent active regions).
  - Strictly 0 samples $> 700.00\text{ ms}$.
- **Verification Points:**
  - Two-Tier Scheduler (`bot/workflow/scheduler.py`): Tier 1 processed every frame, Tier 2 interleaved round-robin.
  - Same-Frame Exhaustive Escalation (`bot/vision/proposal.py`): $K_{base}=4$ candidates, escalates up to 32 within the same frame without deferring to future frames.
  - End-to-end latency measured from first frame acquisition to action dispatch.
- **Automated Test:**
  ```powershell
  python -m pytest tests/test_sla_benchmark.py -v -s
  ```

---

### Checklist Item 6: Per-Monitor DPI Awareness V2 & Canonical Coordinates
- **Module:** [`bot/core/dpi.py`](file:///D:/xampp/bot/bot/core/dpi.py), [`bot/core/coordinates.py`](file:///D:/xampp/bot/bot/core/coordinates.py)
- **Invariant:**
  - Coordinates are unambiguously mapped across DPI scaling, window borders, and browser viewports.
- **Verification Points:**
  - `init_high_dpi_awareness()` executes `SetProcessDpiAwarenessContext(-4)` before Qt application initialization.
  - `CoordinateMapper` implements lossless, bijective conversions:
    - Physical Screen $\leftrightarrow$ Window Client
    - Window Client $\leftrightarrow$ Browser Viewport (CSS pixels with `devicePixelRatio`)
    - Absolute $\leftrightarrow$ Region-Local Normalized $[0.0, 1.0]$.
- **Automated Test:**
  ```powershell
  python -m pytest tests/test_coordinates.py -v -s
  ```

---

### Checklist Item 7: Security, Safety & Licensing
- **Module:** [`bot/core/bundle.py`](file:///D:/xampp/bot/bot/core/bundle.py), [`bot/core/licensing.py`](file:///D:/xampp/bot/bot/core/licensing.py), [`bot/core/hotkey.py`](file:///D:/xampp/bot/bot/core/hotkey.py)
- **Invariant:**
  - Resilient against path traversal, tampering, and unresponsive UI loops.
- **Verification Points:**
  - `bundle.py`: Validates ZIP entries against `os.path.commonpath` to block Zip Slip attacks.
  - `licensing.py`: Derives machine ID from Windows `MachineGuid` and verifies cryptographically signed HMAC-SHA256 licenses.
  - `hotkey.py`: Dedicated background Win32 message pump for **F12** global emergency stop, operational even if GUI is frozen or minimized.
- **Automated Test:**
  ```powershell
  python -m pytest tests/test_bundle.py tests/test_licensing.py -v -s
  ```

---

## 3. Running the Full Automated Audit Suite

Execute the full test suite covering all 31 audit specifications:

```powershell
# From repo root
python -m pytest tests/ -v -s
```

Expected output:
```text
============================= 31 passed in ~2.1s ==============================
```

---

## 4. Reproducing Hard SLA Benchmark Report

To run the standalone SLA benchmark on the 19 concurrent regions workload:

```powershell
python -m pytest tests/test_sla_benchmark.py -s
```

Output includes the comprehensive markdown benchmark report verifying 0 samples $> 700\text{ ms}$.
