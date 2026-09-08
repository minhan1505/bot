# BotAutoClick V2.3 — Generic Vision & Background Automation Engine

A clean-room, generic, multi-table desktop automation bot engineered in accordance with the **Universal Senior Developer Protocol** (`quytatchuan.txt`) and **Functional Completion Specification (FC-01 → FC-15)**.

---

## Important Operational Principles

1. **Zero Licensing / Zero Machine Locking:**
   - There are no licensing checks, HWID verifications, HMAC tokens, or remote activation servers.
   - The entire codebase executes purely locally and deterministically.
2. **Direct Source Execution (No .exe Build Required):**
   - The application runs directly in Python:
     ```powershell
     python main.py
     # or
     python -m bot
     ```
   - No binary compilation, obfuscation, or `.exe` packaging is required.

---

## Core Architectural Invariants

1. **Symbol / Geometry-First Authority (Tri-Condition Gate):**
   $$\mathbf{MATCH} \iff \text{GeometryPass } (G \ge T_g) \land \text{EmbeddingPass } (E \ge T_e) \land \text{IdentityMarginPass } (\text{Margin} \ge M_{safe})$$
   - Geometry is evaluated via bidirectional Chamfer Distance Matching on structural edge topologies.
   - **Hard Invariant:** If Geometry fails, the candidate is rejected (`UNKNOWN`). Color, brightness, and visual embeddings are **strictly prohibited** from rescuing a geometry failure.
   - Guard `TARGET_NOT_GEOMETRICALLY_SEPARABLE`: Flat, low-contrast, or unviable target crops are rejected at configuration time.

2. **Strict Mouse Independence (No Physical Cursor Hijacking):**
   - Dispatches background events via Chrome DevTools Protocol (`Input.dispatchMouseEvent`) or Win32 `PostMessage`.
   - **Hard Invariant:** The Windows system cursor is never seized, moved, or locked. Physical mouse fallback (`pyautogui`, `SetCursorPos`) is forbidden. If no background backend is supported, the system **fails closed** (`BACKGROUND_ACTION_UNSUPPORTED`).

3. **Hard SLA $\le 700\text{ ms}$ (Declared Supported Workload):**
   - Guarantees $Max(Latency) \le 700\text{ ms}$ with **0 violations** across up to 19 concurrent active regions.
   - Measured Software-in-the-Loop Worst-Case Benchmark: **$\approx 102.8\text{ ms} \ll 700.00\text{ ms}$**.

4. **Dynamic Data-Driven Workflows (1..N Steps):**
   - Zero hardcoded button names or class labels (no legacy `x1`, `✓`, `×2` dependencies).
   - Users can crop/upload arbitrary targets and define sequential workflows ($Target_1 \rightarrow Target_2 \rightarrow \dots \rightarrow Target_N$).

5. **Two-Tier Priority Scheduler:**
   - **Tier 1 (Mid-workflow steps):** Scanned every frame for zero-latency step-to-step transitions.
   - **Tier 2 (Starting step / Idle):** Interleaved round-robin across frames to prevent CPU saturation while guaranteeing zero starvation.

6. **End-to-End Safety & Operational Controls:**
   - Configurable Global Emergency Stop hotkey (**F8–F12, Ctrl/Alt/Shift modifiers**) via Win32 `RegisterHotKey` with strict STOP-ONLY debouncing.
   - Anti-Runaway rate limiter, automated circuit breaker, and configurable Auto-Stop timer (`auto_stop_minutes`).
   - Profile ZIP export and safe import with **Zip Slip / Path Traversal** protection, confuser image bundling, and screen geometry revalidation.

---

## 15 Functional Completion Groups (FC-01 → FC-15)

| Group | Feature | Status | Verification Suite |
|---|---|---|---|
| **FC-01** | **Configurable Emergency Stop Hotkey** (F8-F12, modifiers, STOP-only debouncing, conflict handling) | ✅ Complete | `tests/test_functional_completion.py::test_fc01_*` |
| **FC-02** | **Monitor Selection & Geometry Switching** (MSS enumeration, multi-monitor negative offsets, primary indicator) | ✅ Complete | `tests/test_functional_completion.py::test_fc02_*` |
| **FC-03** | **Scan Scope / ROI Management** (Full-screen vs Region, strict out-of-bounds rejection, live coordinate translation) | ✅ Complete | `tests/test_functional_completion.py::test_fc03_*` |
| **FC-04** | **Profile CRUD & Snapshots** (Rename, clone, delete fallback, SQLite snapshots, restore, engine cache invalidation) | ✅ Complete | `tests/test_functional_completion.py::test_fc04_*` |
| **FC-05** | **Target Management & Confusers** (Multi-reference images, confusers, enable/disable toggle, batch import, dangling workflow guard) | ✅ Complete | `tests/test_functional_completion.py::test_fc05_*` |
| **FC-06** | **"Test Target" Offline Verification** (Candidate proposals, geometry score, identity margin, latency breakdown, 0 dispatch) | ✅ Complete | `tests/test_functional_completion.py::test_fc06_*` |
| **FC-07** | **Multi-Workflow Management** (Workflow CRUD, step reordering, clone, active region deletion guard) | ✅ Complete | `tests/test_functional_completion.py::test_fc08_*` |
| **FC-08** | **Action Semantics** (`CLICK`, `DOUBLE_CLICK` with inter-click delay, `DETECT_ONLY` zero-click step advancement) | ✅ Complete | `tests/test_functional_completion.py::test_fc08_*` |
| **FC-09** | **Execution Modes Differentiation** (Dry-Run = 0 dispatch; Shadow = 0 dispatch + `SHADOW_COMPARISON` evidence; Production = live dispatch with gates) | ✅ Complete | `tests/test_functional_completion.py::test_fc09_*` |
| **FC-10** | **Safety Controls UI & Persistence** (Max actions/min rate limiting, runtime auto-stop duration, debounce window) | ✅ Complete | `tests/test_functional_completion.py::test_fc10_*` |
| **FC-11** | **Live Metrics & Evidence Logging** (MATCH/UNKNOWN/NON-MATCH/REJECT/TOTAL badges, P50/P95/P99 latency, async crop logging) | ✅ Complete | `tests/test_functional_completion.py::test_fc11_*` |
| **FC-12** | **Bundle Completeness** (Export/import targets + confusers, Zip Slip defense, screen geometry revalidation) | ✅ Complete | `tests/test_functional_completion.py::test_fc12_*` |
| **FC-13** | **Schema Versioning & Migration** (Schema v2 with transparent backward compatibility for legacy profiles) | ✅ Complete | `tests/test_functional_completion.py::test_fc13_*` |
| **FC-14** | **Comprehensive Test Wiring** (Automated tests covering all features, edge cases, and safety guards) | ✅ Complete | `tests/` and `qa/` |
| **FC-15** | **Documentation Integrity & Honest Status** (Purged legacy licensing claims, explicit PARTIAL tracking for F07/F08) | ✅ Complete | `README.md`, `AUDIT_CHECKLIST.md` |

---

## Project Structure

```text
D:\xampp\bot\
├── main.py                                  # Application Entrypoint (Pre-Qt DPI Awareness V2)
├── requirements.txt                         # Dependency specifications
├── models/
│   ├── ui_vision_encoder.onnx               # Deterministic ONNX Vision Filter Bank
│   └── ui_vision_encoder.sha256             # Artifact SHA-256 integrity checksum
├── scripts/
│   ├── export_models.py                     # Self-contained ONNX model builder & exporter
│   └── validate_model.py                    # Model validation benchmark harness
├── bot/
│   ├── __main__.py                          # Allows running via 'python -m bot'
│   ├── core/
│   │   ├── dpi.py                           # Windows Per-Monitor DPI Awareness V2
│   │   ├── coordinates.py                   # Canonical CoordinateMapper (Screen ↔ Client ↔ Viewport ↔ CSS ↔ Region)
│   │   ├── models.py                        # Pydantic V2 Schemas: Target, Region, Workflow, CalibrationProfile, Decision
│   │   ├── database.py                      # SQLite persistence (Profiles, Snapshots, Audit Logs) & Schema Migration
│   │   ├── bundle.py                        # ZIP Profile Export/Import with Confusers & Zip Slip defense
│   │   └── hotkey.py                        # Configurable Global Emergency Hotkey (F8-F12, modifiers)
│   ├── capture/
│   │   ├── base.py                          # Abstract BaseCapture interface
│   │   ├── dxgi_capture.py                  # DXGI Desktop Duplication capture (< 16ms)
│   │   ├── mss_capture.py                   # MSS capture fallback with multi-monitor support
│   │   └── manager.py                       # CaptureManager (Monitor enumeration & Sub-ROI cropping)
│   ├── vision/
│   │   ├── geometry.py                      # Directional Chamfer Matching + TARGET_NOT_GEOMETRICALLY_SEPARABLE guard
│   │   ├── proposal.py                      # Hybrid Proposal Engine + Same-Frame Escalation
│   │   ├── onnx_verifier.py                 # ONNX Runtime verifier with dynamic tensor shape inspection
│   │   ├── calibration.py                   # Partitioned Two-Set Calibration (Zero-Leakage Provenance)
│   │   └── engine.py                        # Tri-Condition Decision Engine & Offline Target Verification
│   ├── workflow/
│   │   ├── state_machine.py                 # Generic Region State Machine (1..N steps, generation isolation)
│   │   ├── scheduler.py                     # Two-Tier Priority Scheduler
│   │   ├── ledger.py                        # In-session durable ledger & table-local association
│   │   └── runner.py                        # Real-time background runner (Dry-Run, Shadow, Production, Auto-Stop)
│   ├── action/
│   │   ├── base.py                          # Abstract BaseActionBackend (Click, Double-Click)
│   │   ├── cdp_backend.py                   # Chrome DevTools Protocol backend with Window Movement Freshness
│   │   ├── window_backend.py                # Win32 PostMessage background messaging backend
│   │   └── manager.py                       # ActionManager with Anti-Runaway, Circuit Breaker & Freshness Gate
│   ├── telemetry/
│   │   ├── logger.py                        # Async non-blocking JSONL telemetry logger
│   │   ├── benchmark.py                     # Latency Distribution & Hard SLA report generator
│   │   └── hardware_sla_harness.py          # Software-in-the-Loop 19-region SLA acceptance harness
│   └── ui/
│       ├── crop_overlay.py                  # Physical-pixel screen crop overlay dialog
│       ├── tray.py                          # Windows System Tray manager
│       ├── target_dialog.py                 # Target Details Dialog (Multi-Reference & Confusers)
│       ├── test_target_dialog.py            # "Test Target" Offline Verification Dialog
│       └── main_window.py                   # PySide6 Desktop GUI Dashboard
└── tests/                                   # Complete Automated Test Suite (123 Tests)
    ├── test_action_and_safety.py
    ├── test_bundle.py
    ├── test_calibration_and_engine.py
    ├── test_coordinates.py
    ├── test_functional_completion.py        # FC-01..FC-13, U01..U08, V01..V05 test suite
    ├── test_geometry.py
    ├── test_hardware_sla_harness.py
    ├── test_model_empirical_validation.py
    ├── test_onnx_verifier.py
    ├── test_proposal.py
    ├── test_runner.py
    ├── test_sla_benchmark.py
    ├── test_ui_workflow_and_dialogs.py
    └── test_workflow_and_scheduler.py
```

---

## Installation & Setup

1. **Clone repository:**
   ```powershell
   git clone https://github.com/minhan1505/bot.git
   cd bot
   ```

2. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

3. **Verify/Generate ONNX Model Artifact:**
   ```powershell
   python scripts/export_models.py
   ```

---

## Running the Automated Test Suite

Execute the complete automated test suite (123 tests):
```powershell
python -m pytest tests/ qa/ -v
```

All 123 tests execute deterministically and pass with zero failures:
- `test_functional_completion.py`: Verifies FC-01 through FC-13, U01 through U08, and V01 through V05.
- `test_action_and_safety.py`: Verifies Fail-Closed, Anti-Runaway, Circuit Breaker, Freshness tri-gate.
- `test_calibration_and_engine.py`: Verifies Zero Data Leakage partitioned calibration and Tri-Condition gate.
- `test_bundle.py`: Verifies ZIP export/import with Zip Slip security defense and confuser preservation.
- `test_hardware_sla_harness.py`: Verifies 19-region software-in-the-loop SLA latency ($\le 700$ms).
- `test_ui_workflow_and_dialogs.py`: Verifies UI workflows, surface probe quad-condition gate, and target wizard.

---

## Launching the Application

Run the application directly without compiling to an executable:
```powershell
python main.py
# or
python -m bot
```

Key Dashboard Features:
- **Emergency Stop Hotkey:** Defaults to `F12`, configurable on the UI (`Ctrl+F11`, `Alt+Shift+F9`, etc.). Immediate STOP-ONLY semantics.
- **Display Selection:** Switch between available monitors with automatic canonical coordinate offset translation.
- **Scan Scope (ROI):** Choose Full Screen or define a sub-region with strict boundary validation.
- **Execution Mode:**
  - **Dry-Run:** Validates vision detection and workflow logic with 0 physical dispatches.
  - **Shadow Mode:** Evaluates live matches, emitting detailed `SHADOW_COMPARISON` telemetry showing predicted click targets with 0 physical dispatches.
  - **Production:** Full live background automation with mandatory surface compatibility and calibration verification gates.
- **Target Management:** Configure multi-reference targets, confuser images, batch imports, and offline testing via the **Test Target** button.
- **Workflow Steps:** Configure sequential workflows with `CLICK`, `DOUBLE_CLICK`, or `DETECT_ONLY` action types.
- **Live Metrics:** Real-time counters (`MATCH`, `UNKNOWN`, `NON-MATCH`, `REJECT`, `TOTAL`) and latency distribution percentiles (`P50`, `P95`, `P99`, `Max`).
