# QA độc lập — ae5036a

**Kết luận: KHÔNG ĐẠT nghiệm thu Production. Không chấp nhận bảng F01–F13 đều RESOLVED.**

Commit cố định: `ae5036adda6800d44fa25cb2a3c4475af95e15b6`. Nhánh QA: `qa/review-ae5036a`. Chuẩn: yêu cầu Word v2.0 (A1–A12), các contract sửa lỗi R1.3 đã trao đổi; không coi mọi đề xuất trong các kế hoạch trước là yêu cầu được duyệt. Chỉ thêm báo cáo và test, không sửa source ứng dụng.

## Kết quả chạy

- Windows, Python 3.14, dependencies có sẵn.
- `python -m pytest tests/ qa/ -q -p no:cacheprovider --tb=short` trước khi thêm test mới: **49 passed in 5.12s**.
- `python -m pytest qa/test_ae5036a_acceptance.py -q -p no:cacheprovider --tb=short`: **7 failed in 1.01s**. Ba lỗi AttributeError từ code ứng dụng và bốn assertion vi phạm hành vi. Không phải lỗi setup/dependency.
- Tests dùng runner/state/logger/CDP adapter thật với đầu vào/capture/action cô lập. Không thao tác trình duyệt thật; không chứng nhận accuracy/DPI/SLA máy thật.

## Lỗi đã tái hiện

### N01 — P1: Runner crash khi dùng telemetry thật

`bot/workflow/runner.py:306,322` gọi `token.consume()`. `ReservationToken` chỉ là dataclass; API thật nằm ở `AsyncTelemetryLogger.consume(token, event_type, data)`.

Test `test_real_logger_integrates_with_runner_without_crash` đưa logger thật vào runner: **AttributeError: ReservationToken has no attribute consume**, trước action đầu tiên. MainWindow đã inject logger nên đây là đường tích hợp ứng dụng, không phải API không dùng. Lỗi này che các lỗi dispatch phía sau; sửa N01 không đồng nghĩa phần còn lại an toàn.

**Gemini giải trình:** tại sao test chỉ kiểm logger riêng mà không chạy runner với logger? Vì sao F11 được ghi RESOLVED khi caller và callee khác API? Cần dùng consume có token và kiểm tra kết quả, không sửa dataclass để tiếp tục enqueue ngoài reservation.

### N02 — P1: Queue đầy gây crash thay vì SAFE_PAUSE

`runner.py:303` truy cập `RegionState.SAFE_PAUSE`, nhưng enum trong `state_machine.py` không khai báo state này. Test `test_full_telemetry_enters_safe_pause_without_crash` tái hiện **AttributeError**, không vào pause hợp lệ.

**Gemini giải trình:** state, scheduler guard và UI notification của SAFE_PAUSE ở đâu? Test phải chứng minh dừng action và thông báo trạng thái, không chỉ không click do thread đã chết.

### N03 — P1: Producer thường chiếm slot đã reserve

`bot/telemetry/logger.py`, `log_event()` bỏ qua `_reserved_slots` và `_lock`. Reserve 2 slot trên queue capacity 2, gửi 2 heartbeat, rồi consume intent trả False. Test `test_reserved_evidence_cannot_be_stolen_by_ordinary_producer` chứng minh lỗi ngay bằng calls tuần tự, chưa cần race.

`consume()` còn giảm reservation trong lock rồi enqueue ngoài lock, để hở thêm race. Writer lỗi chỉ log, chưa đánh dấu evidence không đầy đủ/khóa action. **Giải trình:** invariant queue_size + reserved <= capacity được enforce ở mọi producer bằng cách nào? Cần token lifecycle, abort/release, writer failure và concurrency tests.

### N04 — P1: UNCERTAIN bị retry thành 4 action attempts

`bot/action/base.py` có enum/result mới, nhưng backends vẫn trả bool; runner tại dòng 331–341 chỉ `if dispatched`, còn false đưa về WAIT_STEP. `ActionDispatchResult.__bool__` biến UNCERTAIN thành False. Test `test_uncertain_result_never_retries_click` trả result UNCERTAIN: **4 attempts**, không vào UNCERTAIN_HOLD.

Backends CDP/Win32 cũng trả False khi đã gửi down nhưng up lỗi, nên nguyên nhân này tồn tại trong đường thật. **Giải trình:** tại sao khai báo structured result nhưng không nối qua backend → manager → runner? Cần xử lý từng status, hold/đối soát đúng bước, không thử click lại.

### N05 — P1: Truyền ViewportContext hợp lệ làm CDP backend crash

`bot/action/cdp_backend.py:124` đọc `inner_width/inner_height`; `ViewportContext` trong `bot/core/coordinates.py` không có hai trường này. Test `test_valid_viewport_context_does_not_crash_backend` tái hiện AttributeError trước cả network call.

Nếu không truyền context, code vẫn giả định screen = CSS. `_get_page_ws_url()` vẫn chọn page đầu tiên; chưa có HWND ↔ target/session binding/invalidation đã mô tả trong kế hoạch. **Giải trình:** nguồn/context schema và caller nào chứng minh tọa độ đúng? Cần test cả thiếu context lẫn context hợp lệ, rồi kiểm binding trên browser thật.

### N06 — P1: Hết deadline giữa fresh verify vẫn dispatch

Runner chỉ check timeout ở đầu cycle. `can_attempt_dispatch()` chỉ kiểm số attempts, không kiểm deadline. Test `test_deadline_expiring_during_fresh_verify_blocks_dispatch` đặt deadline hết trong capture fresh: vẫn ghi nhận action.

Ngoài ra deadline/state dùng `time.time()`, không phải monotonic như checklist F03 tuyên bố. **Giải trình:** deadline được kiểm ở dispatch boundary ở đâu? Cần test verify chậm và đồng hồ hệ thống đổi, không chỉ retry count.

### N07 — P1: Detection cũ vẫn dispatch sau khi generation đổi

Runner không snapshot generation trước fresh verify; sau đó chỉ chép generation hiện tại vào context. Test `test_generation_change_during_fresh_verify_blocks_stale_dispatch` restart workflow trong fresh capture: vẫn action từ decision cũ. `on_fresh_verified()` có thể không chuyển state nhưng runner không kiểm kết quả/state trước dispatch.

**Giải trình:** vì sao có trường generation được coi là đã kiểm generation? Cần so sánh snapshot và state tại action boundary, hủy request cũ, thử cả reload/reset/state thay đổi.

## Các mục chưa được triển khai/chưa đủ bằng chứng

### N08 — P1: F06/F07 vẫn mở — ngưỡng cố định và model random

- `bot/ui/main_window.py:444–446,482–484`: vẫn `.55/.65/.05` trong crop/upload; chưa nối calibrator vào luồng đăng ký.
- `scripts/export_models.py:29–32` vẫn weights `np.random.randn`; model files và exporter không đổi so với commit audit trước. Chưa có artifact pretrained theo kế hoạch, dataset/separation nghiệm thu.
- `bot/vision/engine.py:142` vẫn `identity_margin = e_score` khi không có competitor. Runner tự đưa mọi target khác vào alternatives, không theo explicit confuser binding và không nạp `confuser_image_paths` ở đường này.
- Fresh verify mặc định `g_fresh=1`, embedding/margin pass=True nếu verifier thiếu; có fallback threshold khi calibration thiếu. Đây là các nhánh fail-open trong runner, không phải chứng cứ ba cổng luôn hoạt động.

**Giải trình:** tại sao mô tả separability guard/L2 normalization được dùng để đóng finding về fixed thresholds và encoder provenance? Cần sửa đúng entry points, hoặc đổi trạng thái thành chưa hoàn thành; không đổi mô tả finding để đóng mục.

### N09 — P1: F08 chưa được chứng minh — benchmark vẫn khác luồng live

`tests/test_sla_benchmark.py` không chạy BotRuntimeRunner. Mỗi iteration chỉ xuất hiện **1 target**, xoay qua 19 vùng; backend giả lập ngủ 2ms. Fresh verify crop từ cùng screen_frame và chỉ geometry; limiter được nâng lên 1000/s. Không có capture thật, 19 target đồng thời, real CDP ACK hay telemetry runner.

Có cải thiện: reset region và assert số dispatch thành công bằng số target kỳ vọng. Nhưng con số ~45ms trong checklist chỉ mô tả benchmark tổng hợp này, không chứng minh A7. Cần benchmark live runner với workload/cấu hình safety production công bố, đủ pipeline và mọi target được tính cả miss/fault; tách regression giả lập khỏi nghiệm thu hardware.

### N10 — P1: Surface verification và cấu hình UI chưa thành luồng hoàn chỉnh

ActionManager có surface guard khi `context['is_production']` True; đó là cải thiện. Nhưng `probe_and_bind` chỉ lấy `surface_verified` từ dictionary; MainWindow probe truyền `{}`, không có luồng tương tác verify surface. Không thấy endpoint chọn/bind tab đúng. Chưa chứng minh Production hoạt động được với guard mới.

UI có combo workflow cho region, nhưng Add Step vẫn lấy target đầu tiên (`main_window.py:502–510`), region tọa độ công thức cố định (528); chưa nối sửa bảng/reorder/monitor/ROI thành cấu hình như yêu cầu. Callback `_on_region_workflow_changed` gọi `logger.info` nhưng module chưa khai báo logger. F09 không thể đóng chỉ bằng thêm combo.

### N11 — P2: SafetyConfig có schema nhưng chưa nối profile vào manager UI

MainWindow tạo `ActionManager()` mặc định, chưa truyền `active_profile.safety_config` hay cập nhật khi chuyển profile. Vì vậy giới hạn lưu trong profile chưa chắc là giới hạn được enforce. Quota region hiện đếm tích lũy, không phải sliding window như kế hoạch. Cần test profile → manager → dispatch. Các quota/rate limiter unit tests pass chỉ chứng minh manager được khởi tạo đúng bằng tay.

## Ghi nhận phần đã sửa đúng

- F01/F02: explicit identity không fallback; thiếu identity overlap trả None; đã bỏ caller introspection. Các test QA cũ tương ứng pass.
- Retry giới hạn 1+N và step_deadline tách state timestamp có tiến bộ; N06/N07 còn mở.
- CDP kiểm response ID/error và Win32 kiểm return code có cải thiện; N04/N05 còn mở.
- Crop đã có separability guard; content-aware cache và clear khi chuyển profile có tiến bộ. Chưa chứng minh multi-reference end-to-end.
- Không thấy lý do phải đổi từ ctypes sang pywin32 chỉ để đúng tên trong kế hoạch; điều quan trọng là wrapper contract, lỗi partial dispatch và bằng chứng.

## Yêu cầu bàn giao lại

Không cần viết lại kế hoạch. Với N01–N11, ghi: xác nhận/phản bác có evidence, root cause, file/caller sửa, test counterexample, commit. Giữ nguyên expectation nghiệm thu của test QA; nếu test sai requirement, giải thích trước khi đổi. Chuyển các dòng RESOLVED chưa có evidence trong AUDIT_CHECKLIST về OPEN/PARTIAL/UNVERIFIED.

Lần audit này đủ bằng chứng từ chối nghiệm thu, không phải chứng nhận đã tìm mọi lỗi. Chưa kiểm vận hành browser thật, mouse independence 10 phút, multi-monitor/DPI, vision accuracy trên dữ liệu người dùng, packaging và toàn bộ security/licensing. Không đồng nhất enum/schema tồn tại với tính năng đã được nối vào runtime.
