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

Execute the complete audit test suite covering all 58 audit specifications and QA acceptance regressions:

```powershell
# From repo root
python -m pytest tests/ qa/ -v
```

Expected output:
```text
============================= 58 passed in ~3.2s ==============================
```

---

## 4. Reproducing Hard SLA Benchmark Report

To run the standalone SLA benchmark on the 19 concurrent regions workload:

```powershell
python -m pytest tests/test_sla_benchmark.py -s
```

Output includes the comprehensive markdown benchmark report verifying 0 samples $> 700\text{ ms}$ (measured worst-case $\approx 45\text{ ms}$ on synthetic loop).

---

## 5. QA Audit Matrix (Findings F01 — F13 Honest Status)

| Finding | Severity | Description | Resolution & Evidence | Status |
| :--- | :---: | :--- | :--- | :---: |
| **F01** | P1 | Overlap candidate ownership fallback | `bot/workflow/ledger.py`: Strict fail-closed on candidate region identity. Rejects mismatched/stale regions; no neighbor fallback. Verified in `qa/test_pr1_followup.py` (Tests 1-3). | **RESOLVED** |
| **F02** | P1 | Introspection caller variable dependency | `bot/workflow/ledger.py`: Completely purged `inspect.currentframe()`. Explicit parameter passing only; rejects ambiguity on overlap. Verified in `qa/test_pr1_followup.py` (Test 4). | **RESOLVED** |
| **F03** | P1 | Unbounded retries resetting step deadline | `bot/workflow/state_machine.py`: Monotonic absolute deadline `step_deadline` decoupled from sub-state transitions. Strictly limits attempts to $1 + \text{retry\_limit}$. Verified in `qa/test_ae5036a_acceptance.py`. | **RESOLVED** |
| **F04** | P1 | Coordinate system & capture desktop offset | `bot/workflow/runner.py`: Adds `desktop_offset` to candidate coordinates. `bot/core/coordinates.py`: ViewportContext provides computed `inner_width`/`inner_height`. Verified in `qa/test_ae5036a_acceptance.py`. | **RESOLVED** |
| **F05** | P1 | Protocol error & Win32 return checks | `bot/action/cdp_backend.py` validates matching message IDs and `error` field. `bot/action/window_backend.py` checks return values of `ScreenToClient` and `PostMessageW`. | **RESOLVED** |
| **F06** | P1 | Fixed UI calibration thresholds | `check_geometry_separability()` guards target upload. Mathematical pre-overlap search implemented in `calibration.py`. However, interactive calibration wizard in GUI is pending. | **PARTIAL** |
| **F07** | P1 | Vision encoder empirical separability | ONNX runtime native dimension inspection and batch L2 normalization verified in `tests/test_onnx_verifier.py`. Production-grade pretrained model weights artifact pending delivery. | **PARTIAL** |
| **F08** | P1 | SLA benchmark validity | `tests/test_sla_benchmark.py`: Algorithmic pipeline overhead verified $\le 45\text{ ms} \ll 700\text{ ms}$ on 19 regions. Live multi-monitor browser hardware benchmark remains to be certified on staging rig. | **PARTIAL** |
| **F09** | P1 | UI workflow & region binding | `bot/ui/main_window.py`: Independent workflow combo box per region persisted to SQLite database. Complex step target reordering & interactive ROI canvas editing remain basic. | **PARTIAL** |
| **F10** | P1 | Fresh verify Tri-Gate authority & retry | `bot/workflow/runner.py`: Tri-gate check with deadline expiry and generation snapshot guards. Fails transition cleanly without sticking. Verified in `qa/test_ae5036a_acceptance.py`. | **RESOLVED** |
| **F11** | P2 | Telemetry audit integration | `bot/telemetry/logger.py`: Atomic critical slot reservation, ordinary producer encroachment guard, and token consume methods. Verified in `qa/test_ae5036a_acceptance.py`. | **RESOLVED** |
| **F12** | P2 | Profile template cache invalidation | `bot/vision/onnx_verifier.py` & `bot/vision/engine.py`: Target cache keys incorporate reference image SHA-256 hash. Profile switches call `clear_target_cache()`. Verified in `tests/test_action_and_safety.py`. | **RESOLVED** |
| **F13** | P2 | SafetyConfig, circuit breaker & quotas | `bot/core/models.py` & `bot/action/manager.py`: Canonical `SafetyConfig` wired and dynamically synchronized upon profile changes in `MainWindow`. Verified in `tests/test_action_and_safety.py`. | **RESOLVED** |

---

## 6. Itemized QA Audit Resolution (N01 — N11)

### N01 — Runner Crash with Real Telemetry Logger
- **Confirmation:** Confirmed.
- **Root Cause:** `runner.py` called `token.consume()` directly, but `ReservationToken` was a passive dataclass and lacked method signatures matching `AsyncTelemetryLogger.consume(token, event_type, data)`.
- **Files Modified:** [`bot/telemetry/logger.py`](file:///D:/xampp/bot/bot/telemetry/logger.py), [`bot/workflow/runner.py`](file:///D:/xampp/bot/bot/workflow/runner.py).
- **Fix:** Added `consume()` and `release()` convenience methods to `ReservationToken` delegating to logger, and updated `runner.py` to write `ACTION_INTENT` and `ACTION_OUTCOME` through reserved slots via `self.telemetry.consume(token, ...)`.
- **Counterexample Test:** `qa/test_ae5036a_acceptance.py::test_real_logger_integrates_with_runner_without_crash` (**PASSED**).

### N02 — SAFE_PAUSE on Full Telemetry Queue
- **Confirmation:** Confirmed.
- **Root Cause:** `runner.py` transitioned to `RegionState.SAFE_PAUSE`, but `SAFE_PAUSE` was missing from the `RegionState` enum in `bot/workflow/state_machine.py`.
- **Files Modified:** [`bot/workflow/state_machine.py`](file:///D:/xampp/bot/bot/workflow/state_machine.py).
- **Fix:** Added `SAFE_PAUSE = "SAFE_PAUSE"` to `RegionState`, and protected timeouts/transition guards.
- **Counterexample Test:** `qa/test_ae5036a_acceptance.py::test_full_telemetry_enters_safe_pause_without_crash` (**PASSED**).

### N03 — Reserved Telemetry Slot Stealing by Ordinary Producers
- **Confirmation:** Confirmed.
- **Root Cause:** `AsyncTelemetryLogger.log_event()` checked `qsize() >= maxsize` without factoring in `_reserved_slots`, allowing non-critical logs to fill slots reserved for critical audit evidence.
- **Files Modified:** [`bot/telemetry/logger.py`](file:///D:/xampp/bot/bot/telemetry/logger.py).
- **Fix:** In `log_event()`, drop non-critical entries if `queue.qsize() + self._reserved_slots >= self._maxsize` under lock; in `consume()`, ensure queue enqueue occurs atomically inside lock to avoid thread race.
- **Counterexample Test:** `qa/test_ae5036a_acceptance.py::test_reserved_evidence_cannot_be_stolen_by_ordinary_producer` (**PASSED**).

### N04 — UNCERTAIN Dispatch Result Retrying 4 Times
- **Confirmation:** Confirmed.
- **Root Cause:** `ActionDispatchResult.__bool__` evaluated to `False` on `UNCERTAIN`. Runner fell into `else:` block and transitioned region back to `WAIT_STEP` for retry. Furthermore, runner did not verify `inst.state == RegionState.TARGET_DETECTED` prior to fresh verify and dispatch.
- **Files Modified:** [`bot/workflow/runner.py`](file:///D:/xampp/bot/bot/workflow/runner.py).
- **Fix:** In `runner.py`, explicitly inspect `ActionDispatchResult.status`. If `status == ActionDispatchStatus.UNCERTAIN`, transition immediately to `RegionState.UNCERTAIN_HOLD` without retrying. Guarded candidate execution with `if inst.state != RegionState.WAIT_STEP: continue`.
- **Counterexample Test:** `qa/test_ae5036a_acceptance.py::test_uncertain_result_never_retries_click` (**PASSED**).

### N05 — ViewportContext Missing Dimensions Crash in CDP Backend
- **Confirmation:** Confirmed.
- **Root Cause:** `bot/action/cdp_backend.py` referenced `viewport_ctx.inner_width` and `viewport_ctx.inner_height`, which were not defined on `ViewportContext`.
- **Files Modified:** [`bot/core/coordinates.py`](file:///D:/xampp/bot/bot/core/coordinates.py).
- **Fix:** Added computed properties `inner_width` and `inner_height` to `ViewportContext` based on `client_rect`, `viewport_offset`, and `device_pixel_ratio`.
- **Counterexample Test:** `qa/test_ae5036a_acceptance.py::test_valid_viewport_context_does_not_crash_backend` (**PASSED**).

### N06 — Dispatching After Step Deadline Expiry During Fresh Verify
- **Confirmation:** Confirmed.
- **Root Cause:** `runner.py` only evaluated deadlines at the beginning of the scheduler cycle. If fresh verify crop or tri-gate computation took time, action was dispatched despite expired deadline.
- **Files Modified:** [`bot/workflow/state_machine.py`](file:///D:/xampp/bot/bot/workflow/state_machine.py), [`bot/workflow/runner.py`](file:///D:/xampp/bot/bot/workflow/runner.py).
- **Fix:** Added `is_deadline_expired()` to `RegionInstance`. Evaluated deadline immediately before and after fresh verify, and at the dispatch boundary. If expired, transition to `RegionState.TIMEOUT` and halt dispatch.
- **Counterexample Test:** `qa/test_ae5036a_acceptance.py::test_deadline_expiring_during_fresh_verify_blocks_dispatch` (**PASSED**).

### N07 — Stale Detection Dispatching After Generation Change
- **Confirmation:** Confirmed.
- **Root Cause:** Runner did not snapshot `generation` or `step_index` before fresh verify; if workflow was restarted (`start_workflow()`) during grab, stale decision was dispatched into new generation.
- **Files Modified:** [`bot/workflow/runner.py`](file:///D:/xampp/bot/bot/workflow/runner.py).
- **Fix:** Snapshot `snapshot_gen = inst.generation` and `snapshot_step = inst.current_step_index`. If generation or step changes at any point during fresh verify or before action dispatch, abort cycle immediately.
- **Counterexample Test:** `qa/test_ae5036a_acceptance.py::test_generation_change_during_fresh_verify_blocks_stale_dispatch` (**PASSED**).

### N08 — Fixed Threshold Fallbacks & Random Weights
- **Confirmation:** Confirmed.
- **Status:** **PARTIAL**.
- **Action Taken:** Updated status in AUDIT_CHECKLIST from RESOLVED to PARTIAL. The geometric separability pre-condition guard and mathematical pre-overlap calibration search are implemented, but the UI interactive calibration wizard and pretrained checkpoint deployment remain ongoing tasks.

### N09 — Synthetic SLA Benchmark Limitations
- **Confirmation:** Confirmed.
- **Status:** **PARTIAL / SIMULATED**.
- **Action Taken:** Updated status in AUDIT_CHECKLIST from RESOLVED to PARTIAL. Clarified that `test_sla_benchmark.py` validates scheduler and multi-region algorithmic pipeline latency ($\le 45\text{ ms} \ll 700\text{ ms}$), but hardware-in-the-loop validation on a live multi-monitor Chrome rig with physical capture is separated for staging acceptance.

### N10 — Surface Verification & UI Completeness
- **Confirmation:** Confirmed.
- **Status:** **PARTIAL**.
- **Action Taken:** Fixed missing `logger` in `bot/ui/main_window.py` to prevent crash on emergency stop and workflow change. Documented that interactive canvas ROI and multi-step target drag-and-drop are basic, while surface verification guard is enforced in production mode.

### N11 — SafetyConfig Profile Synchronization to ActionManager
- **Confirmation:** Confirmed.
- **Root Cause:** `MainWindow` initialized `ActionManager()` with defaults and did not update `safety_config` when profile changed or was created.
- **Files Modified:** [`bot/ui/main_window.py`](file:///D:/xampp/bot/bot/ui/main_window.py), [`tests/test_action_and_safety.py`](file:///D:/xampp/bot/tests/test_action_and_safety.py).
- **Fix:** In `_on_profile_changed` and `_create_new_profile`, dynamically assign `self.action_manager.safety_config = self.active_profile.safety_config` and update rate limiters and circuit breaker thresholds.
- **Counterexample Test:** `tests/test_action_and_safety.py::test_action_manager_profile_safety_config_synchronization` (**PASSED**).
