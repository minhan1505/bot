# BotAutoClick V2.3 — Generic Vision & Background Automation Engine

A clean-room, generic, multi-table desktop automation bot engineered in accordance with the **Universal Senior Developer Protocol** (`quytatchuan.txt`) and the **Technical Specification V2.3**.

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
   - Measured Worst-Case Benchmark: **$\approx 35.64\text{ ms} \ll 700.00\text{ ms}$**.

4. **Dynamic Data-Driven Workflow (1..N Steps):**
   - Zero hardcoded button names or class labels (no legacy `x1`, `✓`, `×2` dependencies).
   - Users can crop/upload arbitrary targets and define sequential workflows ($Target_1 \rightarrow Target_2 \rightarrow \dots \rightarrow Target_N$).

5. **Two-Tier Priority Scheduler:**
   - **Tier 1 (Mid-workflow steps):** Scanned every frame for zero-latency step-to-step transitions.
   - **Tier 2 (Starting step / Idle):** Interleaved round-robin across frames to prevent CPU saturation while guaranteeing zero starvation.

6. **End-to-End Safety & Operational Guards:**
   - Global Emergency Stop hotkey (**F12**) via Win32 `RegisterHotKey`.
   - Anti-Runaway rate limiter and automated circuit breaker.
   - Profile ZIP export and safe import with **Zip Slip / Path Traversal** protection.

---

## Project Structure

```text
D:\xampp\bot\
├── main.py                                  # Application Entrypoint (Pre-Qt DPI Awareness V2)
├── requirements.txt                         # Dependency specifications
├── models/
│   ├── ui_vision_encoder.onnx               # Pre-trained/exported ONNX Vision Encoder
│   └── ui_vision_encoder.sha256             # Artifact SHA-256 integrity checksum
├── scripts/
│   └── export_models.py                     # Self-contained ONNX model builder & exporter
├── bot/
│   ├── core/
│   │   ├── dpi.py                           # Windows Per-Monitor DPI Awareness V2
│   │   ├── coordinates.py                   # Canonical CoordinateMapper (Screen ↔ Client ↔ Viewport ↔ CSS ↔ Region)
│   │   ├── models.py                        # Pydantic Schemas: Target, Region, Workflow, CalibrationProfile, Decision
│   │   ├── database.py                      # SQLite persistence (Profiles, Audit Logs)
│   │   ├── bundle.py                        # ZIP Profile Export/Import with Zip Slip defense
│   │   └── hotkey.py                        # Global Emergency Hotkey (F12) message pump
│   ├── capture/
│   │   ├── base.py                          # Abstract BaseCapture interface
│   │   ├── dxgi_capture.py                  # DXGI Desktop Duplication capture (< 16ms)
│   │   ├── mss_capture.py                   # MSS capture fallback
│   │   └── manager.py                       # CaptureManager with automatic failover & sub-ROI cropping
│   ├── vision/
│   │   ├── geometry.py                      # Directional Chamfer Matching + TARGET_NOT_GEOMETRICALLY_SEPARABLE guard
│   │   ├── proposal.py                      # Hybrid Proposal Engine + Same-Frame Escalation
│   │   ├── onnx_verifier.py                 # ONNX Runtime verifier with dynamic tensor shape inspection
│   │   ├── calibration.py                   # Two-Set Calibration (Pre-Overlap Rejection on D_calib, frozen D_val)
│   │   └── engine.py                        # Tri-Condition Decision Engine
│   ├── workflow/
│   │   ├── state_machine.py                 # Generic Region State Machine (1..N steps, generation isolation)
│   │   ├── scheduler.py                     # Two-Tier Priority Scheduler
│   │   ├── ledger.py                        # In-session durable ledger & table-local association
│   │   └── runner.py                        # Real-time background execution runner thread
│   ├── action/
│   │   ├── base.py                          # Abstract BaseActionBackend interface
│   │   ├── cdp_backend.py                   # Chrome DevTools Protocol backend
│   │   ├── window_backend.py                # Win32 PostMessage background messaging backend
│   │   └── manager.py                       # ActionManager with Anti-Runaway & circuit breaker
│   ├── telemetry/
│   │   ├── logger.py                        # Async non-blocking JSONL telemetry logger
│   │   └── benchmark.py                     # Latency Distribution & Hard SLA report generator
│   └── ui/
│       ├── crop_overlay.py                  # Physical-pixel screen crop overlay dialog
│       ├── tray.py                          # Windows System Tray manager
│       └── main_window.py                   # PySide6 Desktop GUI Dashboard
└── tests/                                   # Complete Automated Test Suite
    ├── test_action_and_safety.py
    ├── test_bundle.py
    ├── test_calibration_and_engine.py
    ├── test_coordinates.py
    ├── test_geometry.py
    ├── test_onnx_verifier.py
    ├── test_proposal.py
    ├── test_runner.py
    ├── test_sla_benchmark.py
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

Execute the complete automated test suite:
```powershell
pytest -v -s
```

All test suites run in under 30 seconds with zero failures:
- `test_action_and_safety.py` (Fail-Closed, Anti-Runaway, Circuit Breaker, Hotkey)
- `test_bundle.py` (ZIP export/import, Zip Slip security defense)
- `test_calibration_and_engine.py` (Pre-overlap rejection, Tri-Condition gate)
- `test_coordinates.py` (CoordinateMapper multidimensional scaling)
- `test_geometry.py` (Chamfer score, separability guard)
- `test_onnx_verifier.py` (Dynamic tensor inspection, L2 norm, Cosine similarity)
- `test_proposal.py` (Contour + Downsampled NCC, Same-Frame Escalation)
- `test_runner.py` (Live background runner execution cycle)
- `test_sla_benchmark.py` (Hard SLA $\le 700$ms verified on 19 regions workload)
- `test_workflow_and_scheduler.py` (State transitions, two-tier scheduling, ledger association)

---

## Launching the Application

Start the Desktop GUI dashboard:
```powershell
python main.py
```
- **F12:** Global emergency stop hotkey.
- **Crop Target From Screen:** Direct physical-pixel crop with instant geometry separability check.
- **Probe Action:** Tests CDP / Win32 capability before enabling production dispatch.
- **System Tray:** Minimizes to taskbar tray with live status tooltip.
