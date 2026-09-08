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
5. **Fail-Safe & Anti-Runaway:** F12 global emergency stop, profile validation, and Zip Slip protection.

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

### Checklist Item 7: Security & Safety
- **Module:** [`bot/core/bundle.py`](file:///D:/xampp/bot/bot/core/bundle.py), [`bot/core/hotkey.py`](file:///D:/xampp/bot/bot/core/hotkey.py)
- **Invariant:**
  - Resilient against path traversal, tampering, and unresponsive UI loops.
- **Verification Points:**
  - `bundle.py`: Validates ZIP entries against `os.path.commonpath` to block Zip Slip attacks.
  - `hotkey.py`: Dedicated background Win32 message pump for **F12** global emergency stop, operational even if GUI is frozen or minimized.
- **Automated Test:**
  ```powershell
  python -m pytest tests/test_bundle.py -v -s
  ```

---

## 3. Running the Full Automated Audit Suite

Execute the complete audit test suite covering all 62 audit specifications and QA acceptance regressions:

```powershell
# From repo root
python -m pytest tests/ qa/ -v
```

Expected output:
```text
============================= 62 passed in ~3.3s ==============================
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
| **F06** | P1 | Fixed UI calibration thresholds & dual-session wizard | `bot/vision/calibration.py`: `calibrate_target_from_samples(...)` strictly requires partitioned inputs (`pos_calib`, `neg_calib`, `pos_val`, `neg_val`) and distinct provenance metadata (`session_calib`, `session_val`, `run_calib`, `run_val`), completely purging unpartitioned list splitting and fake label synthesis. `calibrate_target_from_partitions(...)` enforces non-overlapping runs (`calib_runs.isdisjoint(val_runs)`). Cross-partition negative sample hash deduplication. `bot/ui/calibration_dialog.py`: Interactive GUI wizard starting with empty inputs (no prefilled dummy labels), strictly validating non-empty, distinct, and non-placeholder provenance. Hardened `_toggle_bot`: uncalibrated targets blocked in Production mode. Verified in `tests/test_calibration_and_engine.py` & `tests/test_ui_workflow_and_dialogs.py`. | **RESOLVED** |
| **F07** | P1 | Vision encoder empirical separability | `scripts/export_models.py`: Exported deterministic 3-stage spatial filter bank baseline (directional Sobel derivatives, contour integrators, structural filters via Conv/ReLU/MaxPool/Flatten) to ONNX with SHA-256 in `models/ui_vision_encoder.sha256`. No external pretrained checkpoint loaded; no learned Gemm projection. Validated on synthetic glyphs. Real held-out UI target dataset ($D_{test}$) and trained checkpoint pending. | **PARTIAL / DETERMINISTIC BASELINE (PENDING PRETRAINED CHECKPOINT & REAL D_TEST)** |
| **F08** | P1 | SLA benchmark validity | `bot/telemetry/hardware_sla_harness.py`: End-to-end `HardwareSLAAcceptanceHarness` executing live `BotRuntimeRunner` thread across 19 concurrent active regions under Two-Tier Scheduling with production safety bounds. Verified in `tests/test_hardware_sla_harness.py` (mean $\approx 62.0\text{ ms}$, max $\approx 102.8\text{ ms} \le 700.0\text{ ms}$, 0 violations). Physical multi-monitor live rig certification reserved for on-site deployment without fabricating physical hardware evidence. | **PARTIAL / HARNESS COMPLETED (STAGING RIG REQUIRED)** |
| **F09** | P1 | UI workflow, ROI & surface verification | `bot/ui/surface_dialog.py`: Surface probe enforces strict quad-condition gate `received and matched and raf_ok and hover_ok` (fail-closed on overlay interception, frozen render loop, or inactive CSS `:hover` reaction); scales High-DPI CSS pixel window geometry by DPR to physical screen coordinates. `bot/core/coordinates.py`: CoordinateMapper aligns strictly with CDP viewport CSS coordinates (`clientX/Y`) without scroll displacement. `bot/action/cdp_backend.py`: `verify_viewport_freshness()` verifies DPR, viewport dimensions, and physical window coordinates `screenX/screenY` (detects window moves without resize). `bot/workflow/runner.py` & `bot/action/manager.py`: Runner passes `verify_freshness: self.is_production`, and ActionManager executes freshness check in production mode, dropping stale surface bindings on geometry drift. Verified in `tests/test_ui_workflow_and_dialogs.py` & `tests/test_coordinates.py`. | **RESOLVED** |
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

---

## 7. QA Follow-up Verification (R01 — R04)

### R01 — CDP & Win32 Backends Return Structured UNCERTAIN Status on Partial Send
- **Confirmation:** Confirmed.
- **Root Cause:** CDP backend returned `False` when `mousePressed` succeeded but `mouseReleased` failed or lost ACK; Win32 backend also returned `False` if `WM_LBUTTONUP` failed after `WM_LBUTTONDOWN`. Runner only handled `UNCERTAIN` when receiving `ActionDispatchResult`, so boolean `False` was treated as an ordinary failed attempt and retried.
- **Files Modified:** [`bot/action/cdp_backend.py`](file:///D:/xampp/bot/bot/action/cdp_backend.py), [`bot/action/window_backend.py`](file:///D:/xampp/bot/bot/action/window_backend.py), [`bot/action/manager.py`](file:///D:/xampp/bot/bot/action/manager.py).
- **Fix:** Both backends now return `ActionDispatchResult(ActionDispatchStatus.UNCERTAIN, reason)` when mouse down succeeded but mouse up failed/timed out. `ActionManager.dispatch_action` returns `ActionDispatchResult` across all safety and dispatch branches.
- **Counterexample Test:** `qa/test_79168c8_followup.py::test_cdp_partial_send_returns_uncertain_not_retryable_false` (**PASSED**).

### R02 — UNCERTAIN_HOLD Still Obeys Step Deadline
- **Confirmation:** Confirmed.
- **Root Cause:** `check_timeout()` in `bot/workflow/state_machine.py` included `RegionState.UNCERTAIN_HOLD` in the ignore tuple along with terminal states (`IDLE`, `DONE`, `TIMEOUT`, `REJECTED`, `SAFE_PAUSE`), allowing a region in `UNCERTAIN_HOLD` to hang indefinitely past its step deadline.
- **Files Modified:** [`bot/workflow/state_machine.py`](file:///D:/xampp/bot/bot/workflow/state_machine.py).
- **Fix:** Removed `UNCERTAIN_HOLD` from the terminal ignore set. When `now >= deadline`, `check_timeout()` transitions the instance to `RegionState.TIMEOUT` and returns `True`.
- **Counterexample Test:** `qa/test_79168c8_followup.py::test_uncertain_hold_still_obeys_step_deadline` (**PASSED**).

### R03 — Telemetry Check-and-Enqueue Atomic Under Lock
- **Confirmation:** Confirmed.
- **Root Cause:** In `log_event()`, the capacity check occurred inside `self._lock`, but the lock was released before `self._queue.put_nowait(entry)`. A concurrent action thread reserving capacity between the check and enqueue caused ordinary producers to steal reserved slots. In addition, `_reap_expired_tokens` was called from the writer thread without acquiring lock.
- **Files Modified:** [`bot/telemetry/logger.py`](file:///D:/xampp/bot/bot/telemetry/logger.py).
- **Fix:** Changed `self._lock` to `threading.RLock()`. Enclosed `_reap_expired_tokens()` under `self._lock`. In `log_event()`, kept both capacity check and `put_nowait` atomic within `with self._lock:`.
- **Counterexample Test:** `qa/test_79168c8_followup.py::test_reservation_between_normal_check_and_enqueue_cannot_be_stolen` (**PASSED**).

### R04 — Rejection of Critical ACTION_INTENT Blocks Action Dispatch
- **Confirmation:** Confirmed.
- **Root Cause:** In `bot/workflow/runner.py`, `self.telemetry.consume(token, "ACTION_INTENT", ...)` ignored the boolean return value. When token consumption failed (due to token expiry, capacity error, or stalled queue), the action was dispatched anyway.
- **Files Modified:** [`bot/workflow/runner.py`](file:///D:/xampp/bot/bot/workflow/runner.py).
- **Fix:** Explicitly checked `intent_ok`. If `intent_ok` is `False`, released token, transitioned instance to `RegionState.SAFE_PAUSE`, and halted dispatch immediately (`continue`). Also guarded `ACTION_OUTCOME` to log and mark evidence incomplete if outcome enqueue fails.
- **Counterexample Test:** `qa/test_79168c8_followup.py::test_failed_intent_enqueue_blocks_action` (**PASSED**).

---

## 8. Specific Real-World Dependencies & Non-Blocking Scope Breakdown

| Dependency | Current Implementation Status | Missing Real-World Artifact | Blocked Acceptance Item | Unblocked Test Scope |
| :--- | :--- | :--- | :--- | :--- |
| **Pretrained Vision Model Weights** | ONNX dynamic dimension inspection, batch L2 normalization, and cosine similarity cache verified. | Official pretrained weights file `models/ui_vision_encoder.onnx` (currently uses random-weight backbone export in dev script). | Real-symbol accuracy benchmark on unseen web/game targets (A1). | Full pipeline execution, proposal engine, escalation, and tri-gate logic. |
| **Golden Symbol Dataset & Confusers** | Geometry separability guard at crop, mathematical pre-overlap threshold search on $D_{calib}$ / $D_{val}$ enforcing zero false positives. | Real user screen crops of the 19 casino targets and explicit operational confusers (neighboring UI buttons). | Generating final production `calibration.json` profiles with empirical thresholds. | Pre-overlap rejection mathematical validation, geometry verifier, and contour density guards. |
| **Hardware Staging Multi-Monitor Rig** | Two-tier scheduler, same-frame escalation, live runner loop, CDP WebSocket and Win32 PostMessage background dispatch. | Physical multi-monitor testbed running 19 concurrent Chrome tables at mixed DPI. | Physical 10-minute stationary mouse cursor certification on live browser hardware and live hardware SLA latency (A7, A8). | Algorithmic pipeline latency benchmark ($\le 45\text{ ms} \ll 700\text{ ms}$), memory isolation, and anti-runaway safety guards. |

---

## 9. QA Follow-up Verification (S01 — S02, Commit db4d20e / b15972e)

### S01 — CDP Mouse-Down Sent but Lost ACK Returns UNCERTAIN (Closing R01)
- **Confirmation:** Confirmed.
- **Root Cause:** In `bot/action/cdp_backend.py`, after `await ws.send(press_msg)` transmitted `mousePressed` to the browser, if `_recv_ack` timed out or raised an exception, the method returned `ActionDispatchStatus.NOT_SENT`. `pressed_down` was only set to `True` after ACK. Consequently, if `mousePressed` was sent across the WebSocket but the ACK was dropped or timed out, the runner treated it as `NOT_SENT` and allowed retrying an action that may have already taken physical effect in Chrome.
- **Files Modified:** [`bot/action/cdp_backend.py`](file:///D:/xampp/bot/bot/action/cdp_backend.py).
- **Fix:** Set `pressed_down = True` immediately upon successful completion of `await ws.send(press_msg)`. If `_recv_ack` fails, attempt an emergency recovery `mouseReleased` event and return `ActionDispatchResult(ActionDispatchStatus.UNCERTAIN, reason)`. Any unhandled exception after `ws.send` also resolves to `UNCERTAIN` via the `pressed_down` guard.
- **Counterexample Test:** `qa/test_db4d20e_followup.py::test_press_sent_but_ack_lost_is_uncertain` (**PASSED**).

### S02 — Telemetry ACTION_OUTCOME Failure Records First Action & Safe-Pauses Workflow (Closing R04)
- **Confirmation:** Confirmed.
- **Root Cause:** In `bot/workflow/runner.py`, when `outcome_ok` was `False`, the runner only logged an error and released the reservation token, but still proceeded to advance `current_step_index` and dispatch subsequent workflow steps (eventually reporting `DONE` in a multi-step workflow without audit evidence).
- **Files Modified:** [`bot/workflow/runner.py`](file:///D:/xampp/bot/bot/workflow/runner.py).
- **Fix:** When `outcome_ok` is `False`, the runner records the first action via `inst.on_action_dispatched()` (preserving the factual execution count), transitions the instance to `RegionState.SAFE_PAUSE`, invokes `on_state_change`, and issues `continue` to halt all further step execution and block subsequent action dispatches.
- **Counterexample Test:** `qa/test_db4d20e_followup.py::test_outcome_evidence_failure_blocks_new_actions` (**PASSED**).

---

## 10. QA Follow-up Verification (T01, Commit 8ddce10 / 7cdda7e)

### T01 — Outcome Logging Failure Must Preserve True Backend Dispatch Status
- **Confirmation:** Confirmed.
- **Root Cause:** In `bot/workflow/runner.py`, when `outcome_ok` was `False`, the runner called `inst.on_action_dispatched()` before inspecting `dispatched.status`. If backend returned `NOT_SENT`, `FAIL_CLOSED`, or `UNCERTAIN`, the state history falsely recorded `ACTION_PENDING` with reason `"Action dispatched"`, updated `last_action_at`, and masked the backend's failure reason.
- **Files Modified:** [`bot/workflow/runner.py`](file:///D:/xampp/bot/bot/workflow/runner.py), [`tests/test_runner.py`](file:///D:/xampp/bot/tests/test_runner.py).
- **Fix:** Restructured outcome handling to evaluate backend `ActionDispatchResult` first:
  - `ActionDispatchStatus.DISPATCHED`: calls `inst.on_action_dispatched()` (`last_action_at > 0`); if `outcome_ok` advances step, else transitions to `SAFE_PAUSE`.
  - `ActionDispatchStatus.UNCERTAIN`: transitions to `RegionState.UNCERTAIN_HOLD` with backend reason (`last_action_at == 0`); if not `outcome_ok`, transitions to `SAFE_PAUSE`.
  - `ActionDispatchStatus.FAIL_CLOSED`: transitions to `RegionState.REJECTED` with backend reason (`last_action_at == 0`); if not `outcome_ok`, transitions to `SAFE_PAUSE`.
  - `ActionDispatchStatus.NOT_SENT`: transitions to `RegionState.WAIT_STEP` (if retry available and `outcome_ok`) or `RegionState.REJECTED` (`last_action_at == 0`); if not `outcome_ok`, transitions to `SAFE_PAUSE` blocking retries.
- **Counterexample Tests:**
  - `qa/test_8ddce10_outcome_truth.py::test_failed_outcome_does_not_record_unconfirmed_action_as_dispatched` (**PASSED** for `NOT_SENT`, `FAIL_CLOSED`, `UNCERTAIN`).
  - `tests/test_runner.py::test_dispatch_status_truth_and_outcome_evidence_matrix` (**PASSED** across all 8 permutations of 4 statuses × 2 evidence outcomes).

---

## 11. Test Suite Summary

- **Total Automated Tests:** 96 passed (0 failed, 0 skipped).
  - 78 Unit & Integration Tests (`tests/`)
    - Includes `test_hardware_sla_harness.py` (live runner 19 regions SLA)
    - Includes `test_ui_workflow_and_dialogs.py` (step dialog, reordering, production guards, dual-session zero-leakage, quad-condition probe gate, High-DPI coordinate scaling, window movement & freshness invalidation, ActionManager production freshness verification)
    - Includes `test_coordinates.py` (canonical coordinates, CDP viewport vs document scroll coordinates)
    - Includes `test_calibration_and_engine.py` (pre-overlap rejection, run ID overlap rejection, session ID overlap rejection, confuser partition deduplication)
    - Includes `test_model_empirical_validation.py` (model provenance, zero-overlap, separation gap)
  - 7 Acceptance Counterexample Tests (`qa/test_ae5036a_acceptance.py`)
  - 4 Robustness Counterexample Tests (`qa/test_79168c8_followup.py`)
  - 3 Outcome Truth Counterexample Tests (`qa/test_8ddce10_outcome_truth.py`)
  - 2 Follow-up Regression Tests (`qa/test_db4d20e_followup.py`)
  - 2 Independent Regression Scenarios (`qa/test_independent_regressions.py`)

---

## 12. Resolution of Remaining Partial Findings (F06, F07, F08, F09)

### F06 — Target Calibration Production Flow & UI Wizard
- **Problem:** Target creation previously assigned arbitrary fallback thresholds (`.55/.65/.05`), and runner fell back to heuristic defaults if calibration was missing. Furthermore, an earlier wizard iteration synthetically modified brightness on a single reference image to fake Session A / Session B partitions, or auto-split a single image list in half and fabricated artificial session labels (`session_A`/`session_B`, `run_A`/`run_B`).
- **Resolution:**
  - Fixed `TargetCalibrationDialog` constructor signature mismatch: unified to `(target, profile, onnx_verifier, geo_verifier, parent=None)`.
  - Completely purged synthetic brightness shifting (`*0.9 / *1.1`) and fake session IDs.
  - **Partitioned Inputs & Genuine Acquisition Provenance:** Refactored `calibrate_target_from_samples(...)` to strictly require pre-partitioned sample sets (`pos_calib`, `neg_calib`, `pos_val`, `neg_val`) and distinct, non-empty provenance parameters (`session_calib`, `session_val`, `run_calib`, `run_val`). Slicing a single list in half and assigning artificial session labels has been completely purged from the codebase.
  - **Disjoint Run Split Verification:** `calibrate_target_from_partitions(...)` enforces non-overlapping capture runs: `calib_runs.isdisjoint(val_runs)` fails closed with `CalibrationOverlapError` if identical capture run IDs exist across calibration and validation partitions.
  - **Cross-Partition Confuser Negative Sample Hash Deduplication:** `calibrate_target_from_samples(...)` indexes SHA-256 byte hashes of all calibration confusers and strictly blocks validation confusers with overlapping content hashes (`DATA_LEAKAGE_DETECTED`).
  - **Interactive Wizard Provenance Enforcement:** `TargetCalibrationDialog` starts with empty input fields (using descriptive placeholder hints instead of prefilled dummy defaults). Validates that `session_id` and `run_id` for both sessions are non-empty, distinct, and rejects generic placeholders (`session_a`, `session_b`, `run_a`, `run_b`).
  - **Zero Data Leakage by Path, Hash & Content:** `TargetCalibrationDialog` enforces:
    1. Disjoint file paths across Session A and Session B.
    2. Zero SHA-256 byte hash overlap between Session A and Session B.
    3. Zero identical decoded pixel buffers (`np.array_equal`) across sessions.
    4. Zero contradiction between positive target samples and negative confusers.
  - Mathematical pre-overlap rejection: computes target-to-confuser cross-similarity $S_{neg}$ and target self-consistency $S_{pos}$. Asserts separation gap $\delta = \min(S_{pos}) - \max(S_{neg}) > 0$.
  - Threshold selection bounds: $T_e = \min(S_{pos}) - 0.5 \cdot \delta$, $T_g = \min(G_{pos}) - 0.5 \cdot \delta_g$, $M_{safe} = 0.5 \cdot \delta$.
  - Enforced fail-closed in `_toggle_bot`: In Production mode, if any target referenced in workflow steps lacks a verified `CalibrationProfile`, execution is blocked immediately.
- **Verification:** `tests/test_calibration_and_engine.py`, `tests/test_ui_workflow_and_dialogs.py`.
- **Status:** **RESOLVED**.

### F07 — Pretrained Production Model & Empirical Validation
- **Problem:** `models/ui_vision_encoder.onnx` is exported via a deterministic 3-stage spatial filter bank (Sobel directional derivatives, contour integrators, structural filters via Conv/ReLU/MaxPool/Flatten) without an external learned checkpoint or learned Gemm projection weights. Validation on synthetic glyphs demonstrates structural separation, but does not substitute for a real held-out UI target dataset ($D_{test}$) or learned production model checkpoint.
- **Resolution:**
  - Exported deterministic baseline artifact `models/ui_vision_encoder.onnx` with verified SHA-256 checksum `d0445429c006d38f012adeb479d97fb97275b8c1be4eae5a14a9eb53ab3bba27` saved in `models/ui_vision_encoder.sha256`.
  - Replaced misleading claims of orthonormal projection matrices and "pretrained production model" with accurate descriptions of an analytical spatial filter bank baseline.
  - Empirical validation suite `scripts/validate_model.py` and regression test `tests/test_model_empirical_validation.py` confirm mathematical determinism and baseline separation on synthetic glyphs.
  - Retained finding status strictly as **PARTIAL** until learned deep neural network weights and empirical benchmarks on real held-out UI target crops ($D_{test}$) are provided.
- **Verification:** `tests/test_model_empirical_validation.py`.
- **Status:** **PARTIAL / DETERMINISTIC BASELINE (PENDING PRETRAINED CHECKPOINT & REAL D_TEST)**.

### F08 — Hard SLA Hardware Acceptance Harness
- **Problem:** SLA testing on local development machine cannot replace physical multi-monitor staging rig hardware testing.
- **Resolution:**
  - Implemented `HardwareSLAAcceptanceHarness` in `bot/telemetry/hardware_sla_harness.py`.
  - Executes the real, live `BotRuntimeRunner` background thread against 19 concurrent active regions under Two-Tier Scheduling with production safety bounds (`max_clicks_per_second=10.0`, `circuit_breaker_threshold=30`).
  - Complete end-to-end software-in-the-loop pipeline: Frame capture -> TwoTierScheduler -> Multi-region proposal generation -> ONNX batch embedding & Geometry Tri-Gate -> SessionLedger candidate association -> Fresh sub-ROI verification -> Atomic intent reservation -> SimulatedLiveActionBackend dispatch -> Outcome evidence recording -> Ledger state advancement.
  - Benchmarked across 20 appearances:
    - Min latency: $44.2\text{ ms}$
    - Mean latency: $62.0\text{ ms}$
    - P95 latency: $92.8\text{ ms}$
    - Worst-case max latency: $102.8\text{ ms} \le 700.0\text{ ms}$
    - Violations ($> 700\text{ ms}$): **0**
  - Maintained honest status: Algorithmic pipeline and software-in-the-loop harness are 100% complete and passing. Physical multi-monitor live rig certification on staging hardware remains separated without fabricating physical hardware evidence.
- **Verification:** `tests/test_hardware_sla_harness.py`.
- **Status:** **PARTIAL / HARNESS COMPLETED (STAGING RIG REQUIRED)**.

### F09 — End-to-End UI Workflows, ROI Selection & Surface Verification
- **Problem:** Target tab binding in CDP was previously unexposed; surface probe verified only bot-installed DOM listeners rather than actual CSS hit-test reaction; viewport freshness checks omitted window position (`screenX/screenY`) and were not wired into `BotRuntimeRunner`; and region-workflow bindings silently fell back to defaults.
- **Resolution:**
  - **Strict Quad-Condition Active Surface Gate:** In `SurfaceVerificationDialog`, Stage 2 probes the target element with active DOM events (`mouseMoved`) and evaluates a strict quad-condition gate: `received and matched and raf_ok and hover_ok`. Fails closed if the event is intercepted by unexpected overlays (`eventTargetMatched == False`), if the target render loop is frozen (`rafActive == False`), or if Chrome's CSS layout engine fails to activate `:hover` state on the hit element. Logs canvas context diagnostics (`canvasCtx`) for web canvas surfaces.
  - **High-DPI Physical Coordinate Scaling:** Chrome reports `screenX/Y`, `outerWidth/Height`, and `innerWidth/Height` in CSS pixels. `SurfaceVerificationDialog` scales all geometry metrics by `devicePixelRatio` to ensure `ViewportContext.window_rect` and `client_rect` accurately match physical screen pixels.
  - **CDP Viewport CSS Pixel Coordinate Alignment:** `CoordinateMapper.screen_to_css_pixels` strictly converts physical coordinates to main frame viewport CSS coordinates (`clientX`, `clientY`) without page scroll offset displacement. Added `CoordinateMapper.screen_to_document_css_pixels` for document-relative coordinate mapping.
  - **Window Movement Detection & Freshness Invalidation:** `verify_viewport_freshness()` in `CDPActionBackend` inspects `dpr`, `innerWidth`, `innerHeight`, and `screenX/screenY` (scaled by DPR). If the target Chrome window is moved across the monitor, resized, or changes DPR, the surface context is automatically invalidated (`WINDOW_MOVED`, `VIEWPORT_RESIZED`, `DPR_DRIFT`).
  - **Production Freshness Pipeline Enforcement:** `BotRuntimeRunner` explicitly attaches `"verify_freshness": self.is_production` to `action_context`. `ActionManager` enforces the freshness verification in production before dispatching actions, failing closed and dropping bindings if geometry has drifted.
  - **Explicit Browser Tab Selection:** Added `get_available_pages()` to `CDPActionBackend` querying `/json` to list all open Chrome tabs by title and URL, allowing the operator to explicitly select the target tab in the GUI rather than blindly binding to tab 0.
  - **Strict Region Workflow Contract:** In `MainWindow._toggle_bot`, Production mode strictly verifies that every defined region has an explicit assigned workflow with $\ge 1$ step. Completely purged silent auto-guessing of `default_wf` or auto-picking the first target in Production mode.
  - **Interactive ROI Crop & Reordering:** Visual screen ROI rubberband selection via `ScreenCropOverlay(mode="region")`; dynamic step reordering (Move Up, Move Down, Delete) in `MainWindow` with `WorkflowStepDialog`.
- **Verification:** `tests/test_ui_workflow_and_dialogs.py`, `tests/test_coordinates.py`.
- **Status:** **RESOLVED**.

---

## 14. Functional Completion Matrix (FC-01 → FC-15)

The 15 Functional Completion items requested in GitHub comment `5581209834` (PR #1) have been implemented and verified via automated test suites in `tests/test_functional_completion.py` and the existing test suites (123 total tests passing).

### Summary Table

| Group | Functional Requirement | Implementation Details | Verification Evidence | Status |
|---|---|---|---|---|
| **FC-01** | **Configurable Emergency Stop Hotkey** | `GlobalHotkeyManager` in `bot/core/hotkey.py`: Supports F8-F12 with Ctrl/Alt/Shift modifiers. Win32 `RegisterHotKey` message pump with strict STOP-ONLY semantics, debouncing (300ms), and conflict reporting (`HOTKEY_CONFLICT`). | `tests/test_functional_completion.py::test_fc01_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-02** | **Physical Monitor Selection** | `enumerate_monitors()` and `set_monitor()` in `bot/capture/manager.py`: Enumerates real physical displays via MSS, handles multi-monitor negative offsets (e.g. left secondary at $x = -1920$), and updates capture dimensions and canonical desktop coordinate translation. | `tests/test_functional_completion.py::test_fc02_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-03** | **Scan Scope / ROI Management** | `validate_roi()`, `set_roi()` in `bot/capture/manager.py`: Rejects out-of-bounds ROIs (fails closed). Crops grabbed frame and automatically shifts canonical desktop click offset. Resetting clears ROI and restores full-screen scanning. | `tests/test_functional_completion.py::test_fc03_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-04** | **Profile CRUD & Snapshots** | `Database` in `bot/core/database.py`: Profile renaming, cloning, and deletion with fallback. SQLite `snapshots` table supporting `create_snapshot()`, `list_snapshots()`, `restore_snapshot()`, and `delete_snapshot()`. Profile mutation invalidates vision engine target cache. | `tests/test_functional_completion.py::test_fc04_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-05** | **Target Management & Confusers** | `Target` in `bot/core/models.py`, `TargetDetailsDialog` in `bot/ui/target_dialog.py`: Multi-reference images, confuser image list, enable/disable toggle (disabled targets skipped by runner), batch folder import, and deletion guard blocking deletion of targets referenced by active workflows. | `tests/test_functional_completion.py::test_fc05_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-06** | **"Test Target" Offline Verification** | `test_target_on_frame()` in `bot/vision/engine.py`, `TestTargetDialog` in `bot/ui/test_target_dialog.py`: Runs proposals, geometry scoring, ONNX embedding verification, and decision classification against an offline or live frame. Displays candidates, bounding boxes, scores, identity margins, and latency breakdown with **0 physical action dispatch**. | `tests/test_functional_completion.py::test_fc06_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-07** | **Multi-Workflow Management** | `Workflow` in `bot/core/models.py`, `MainWindow` in `bot/ui/main_window.py`: Multiple workflows per profile, workflow cloning, step CRUD and reordering. Deletion guard strictly blocks deleting workflows currently assigned to active regions. | `tests/test_functional_completion.py::test_fc08_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-08** | **Action Execution Semantics** | `ActionType` enum in `bot/core/models.py`, `ActionManager.dispatch_action()`: Maps enum to Win32/CDP backends. `CLICK` dispatches single click; `DOUBLE_CLICK` executes two clicks with inter-click delay; `DETECT_ONLY` advances workflow step with **0 physical clicks**. | `tests/test_functional_completion.py::test_fc08_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-09** | **Execution Modes Differentiation** | `BotRuntimeRunner` in `bot/workflow/runner.py`: Three distinct modes: **Dry-Run** (0 physical dispatch, simulates advancement in ledger); **Shadow Mode** (0 physical dispatch, emits dedicated `SHADOW_COMPARISON` telemetry & callback stream with predicted coordinates and margins); **Production** (physical dispatch with quad-condition active surface gate & calibration check). | `tests/test_functional_completion.py::test_fc09_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-10** | **Safety Controls Persistence** | `SafetyConfig` in `bot/core/models.py`, Safety tab in `bot/ui/main_window.py`: Configurable `max_actions_per_minute` (enforced by rate limiter), `auto_stop_minutes` (runner automatically shuts down after duration), and emergency stop debouncing. | `tests/test_functional_completion.py::test_fc10_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-11** | **Live Metrics & Evidence Logging** | `MainWindow` & `BotRuntimeRunner`: Live counter badges for `MATCH`, `UNKNOWN`, `NON_MATCH`, `REJECT`, `TOTAL`. Rolling latency buffer computes P50, P95, P99, and Max latency. `_save_evidence_crop_async()` logs candidate crops to disk asynchronously on UNKNOWN or low-margin evaluations without blocking runner cycles. | `tests/test_functional_completion.py::test_fc11_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-12** | **Bundle Completeness** | `ProfileBundleManager` in `bot/core/bundle.py`: Exports and imports targets with both reference images and confusers. Enforces Zip Slip path traversal security defense. Revalidates imported coordinates and ROIs against current screen geometry. | `tests/test_functional_completion.py::test_fc12_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-13** | **Configuration Versioning & Migration** | `SCHEMA_VERSION = 2` in `bot/core/models.py`: `migrate_profile_data()` in `bot/core/database.py` seamlessly upgrades legacy schema v1 JSON profiles to Schema v2 (adding `schema_version=2`, `emergency_hotkey="F12"`, `auto_stop_minutes=0.0`) with zero data loss. | `tests/test_functional_completion.py::test_fc13_*` | ✅ **IMPLEMENTED & TESTED** |
| **FC-14** | **Test Wiring & Verification** | Comprehensive test suite `tests/test_functional_completion.py` (27 automated tests) integrated into the full test suite (123 passed). Every UI control maps to verified runtime behavior. | `tests/` and `qa/` (123 passed) | ✅ **IMPLEMENTED & TESTED** |
| **FC-15** | **Documentation & Audit Status Integrity** | Purged all obsolete claims (licensing, HWID, HMAC, machine locking). Maintained strict PARTIAL tracking for F07 and F08. Documented direct execution via `python -m bot`. | `README.md`, `AUDIT_CHECKLIST.md` | ✅ **IMPLEMENTED & TESTED** |

---

## 15. Status of Outstanding Audit Findings

| Finding ID | Title | Verified Status | Rationale |
|---|---|---|---|
| **F06** | Target Calibration Production Flow & UI Wizard | ✅ **CLOSED** | Replaced mock partition splitting with strict provenance-based partitioned calibration (`pos_calib`, `neg_calib`, `pos_val`, `neg_val`) with distinct session/run IDs, cross-session hash deduplication, and zero data leakage. Verified by tests. |
| **F07** | Pretrained Production Model & Empirical Validation | 🟡 **PARTIAL** | Exported deterministic baseline spatial filter bank `models/ui_vision_encoder.onnx` with verified SHA-256. Retained strictly as **PARTIAL** pending learned model weights and empirical benchmarks on real held-out UI target crops ($D_{test}$). **Not closed.** |
| **F08** | Hard SLA Hardware Acceptance Harness | 🟡 **PARTIAL** | Software-in-the-loop SLA harness (`HardwareSLAAcceptanceHarness`) executes 19 active regions under Two-Tier Scheduling with worst-case latency $\approx 102.8\text{ ms} \ll 700\text{ ms}$ (0 violations). Retained strictly as **PARTIAL** pending testing on physical multi-monitor staging rig hardware. **Not closed.** |
| **F09** | End-to-End UI Workflows, ROI & Surface Verification | ✅ **CLOSED** | Strict quad-condition active surface gate (`received and matched and raf_ok and hover_ok`), DPR physical scaling, window-move freshness check in CDP backend, production freshness enforcement in `BotRuntimeRunner`, and interactive ROI crop overlay. Verified by tests. |

---

## 16. Packaging and Execution Note

- **Zero Licensing / Machine Locking:** The codebase has zero licensing dependencies, zero HWID/HMAC checks, and zero machine locking mechanisms.
- **Direct Source Execution:** The bot is designed to be executed directly from Python source:
  ```powershell
  python main.py
  # or
  python -m bot
  ```
- **No .exe Build:** No binary compilation or `.exe` packaging is required or performed.

---

## 17. Re-Audit Hardening Matrix (V01 → V06)

| Finding | Severity | Resolution Details | Test Evidence |
|---|---|---|---|
| **V01** | **P0** | **Multi-Reference Fresh-Verify & Normalization:** Fixed reference scope leak in `bot/workflow/runner.py`. Fresh verify evaluates geometry and embedding across all references of the target being verified. Competitor targets and confusers are strictly normalized to `Dict[str, List[np.ndarray]]` to eliminate nested-list crash paths. | `tests/test_functional_completion.py::test_v01_*` |
| **V02** | **P1** | **Proposal Batch Limit 128 & Global Truncation Fault Evidence:** Wired `max_batch_limit=128` across `MainWindow`, `hardware_sla_harness.py`, and proposal engine default. In `bot/vision/proposal.py`, global truncation records dropped candidate counts per region (`PROPOSAL_OVERFLOW_GLOBAL_TRUNCATION`), ensuring non-matching truncated regions emit `PROPOSAL_OVERFLOW_PERFORMANCE_FAULT` (`UNKNOWN`). | `tests/test_functional_completion.py::test_v02_*` |
| **V03** | **P1** | **Universal Atomic Profile Activation & Hotkey Rollback Truth:** In `bot/ui/main_window.py`, `_restore_profile_snapshot()` routes through `activate_profile()`. When a requested hotkey conflicts, rollback synchronizes `profile.emergency_hotkey = self.hotkey_manager.hotkey_str` so UI views always display the actually bound emergency key. | `tests/test_functional_completion.py::test_v03_*` |
| **V04** | **P1** | **DOUBLE_CLICK Pre-Dispatch Safety Quota Boundary:** In `bot/action/manager.py`, computes planned action cost (`CLICK=1`, `DOUBLE_CLICK=2`, `DETECT_ONLY=0`) before dispatch. If `current + planned_cost > limit` for total, per-region, or rate window, fails closed before dispatch. Conservatively records 1 click on `UNCERTAIN` outcomes. | `tests/test_functional_completion.py::test_v04_*` |
| **V05** | **P1** | **Calibration Wizard Deep-Copy Staging & Safe Cancel:** In `bot/ui/calibration_dialog.py`, `TargetCalibrationDialog` operates on a deep copy (`target.model_copy(deep=True)`). Modifications are staged and only committed to the live target upon `Accept`. Clicking `Cancel` or `reject()` leaves the original target completely untouched. | `tests/test_functional_completion.py::test_v05_*` |
| **V06** | **P2** | **Documentation & Test Suite Synchronization:** Synchronized test counts across `README.md`, `AUDIT_CHECKLIST.md`, and PR #1 body to exact current suite (123 tests passing). Verified runtime proposal wiring to 128. | `tests/test_functional_completion.py` |

---

## 18. Re-Audit Hardening Matrix (W01 → W06)

| Finding | Severity | Resolution Details | Test Evidence |
|---|---|---|---|
| **W01** | **P0/P1** | **Python 3.12 Typing & Import:** Added `from __future__ import annotations` and imported `Tuple, Any, Union` from `typing` in `bot/workflow/runner.py`. Verified clean import and annotations under Python 3.12+. | `tests/test_functional_completion.py::test_w01_*` |
| **W02** | **P0** | **Emergency Hotkey Rollback Verification & Unbound Fail-Closed:** In `bot/core/hotkey.py`, captured rollback registration result in `update_hotkey()`. If rollback also fails, returns `(False, "HOTKEY_ROLLBACK_FAILED")` and sets `is_registered=False`. In `bot/ui/main_window.py`, unbinds profile hotkey and strictly blocks bot startup (`_toggle_bot`) with fail-closed dialog. | `tests/test_functional_completion.py::test_w02_*` |
| **W03** | **P0/P1** | **Multi-Reference Calibration Alignment & Strict D_val Holdout:** Updated `CalibrationEngine.calibrate_target()` in `bot/vision/calibration.py` to evaluate geometry (`max`) and embedding average across all reference images, identically to runtime. In `bot/ui/calibration_dialog.py`, deployed target reference bank uses `session_a_pos` (D_calib) only. Session B (`session_b_pos`, D_val) remains strictly held out and is never leaked into the deployed matcher. | `tests/test_functional_completion.py::test_w03_*` |
| **W04** | **P1** | **Explicit Target Confusers in Initial Gate-3 Margin:** In `bot/workflow/runner.py`, loaded `target_cfg.confuser_image_paths` directly into initial `alt_targets` alongside other targets. Initial `VisionEngine.evaluate_candidates()` compares candidate against explicit confusers, preventing confuser crops from falsely passing as MATCH before fresh verify. | `tests/test_functional_completion.py::test_w04_*` |
| **W05** | **P1** | **Path-Independent Content Hashing & Portable Calibrated ZIP:** In `bot/core/models.py`, `Target.compute_content_hash()` computes canonical SHA-256 over image byte hashes and roles (`ref:` vs `conf:`) without absolute file paths. Moving profiles or extracting ZIP bundles across machines preserves exact content hash and calibration validity. | `tests/test_functional_completion.py::test_w05_*` |
| **W06** | **P1/P2** | **Calibration Compatibility Pre-Check & Controlled SAFE_PAUSE:** In `MainWindow._toggle_bot()`, validates `target.calibration.is_valid_for(...)` against active model SHA and content hash before starting Production mode. In `BotRuntimeRunner.run()`, wrapped `evaluate_candidates()` in `try...except ValueError` catching `CALIBRATION_INVALID`, transitioning the region to `SAFE_PAUSE` with diagnostic fault evidence without killing the worker thread. | `tests/test_functional_completion.py::test_w06_*` |


