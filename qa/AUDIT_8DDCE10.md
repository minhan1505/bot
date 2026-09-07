# QA follow-up — 8ddce10

**Kết luận: hai counterexamples S01/S02 trước đã pass. Còn một regression về tính đúng của kết quả action khi evidence thất bại. Chưa đạt Production.**

Base `8ddce10`, nhánh QA `qa/review-8ddce10`. Chỉ thêm báo cáo/test; không sửa production.

## Kiểm chứng

- Windows/Python 3.14. `python -m pytest tests/ qa/ -q -p no:cacheprovider --tb=short`: **64 passed in 5.39s** trước test bổ sung.
- `python -m pytest qa/test_8ddce10_outcome_truth.py -q -p no:cacheprovider --tb=short`: **3 failed in 0.75s**. Đây là ba trạng thái đầu vào của **một lỗi**, không phải ba root causes khác nhau.
- Cô lập capture/action/logger failure; chạy state machine và runner thật, không click hoặc network thật.

## T01 — P1: Outcome log thất bại ghi sai status action

`bot/workflow/runner.py`, nhánh `if not outcome_ok` (khoảng dòng 399–410) gọi `inst.on_action_dispatched()` vô điều kiện, rồi SAFE_PAUSE/continue trước đoạn phân giải status.

| Backend thực sự trả về | Lịch sử state hiện tại |
|---|---|
| NOT_SENT | VERIFIED → ACTION_PENDING, reason `Action dispatched` → SAFE_PAUSE |
| FAIL_CLOSED | VERIFIED → ACTION_PENDING, reason `Action dispatched` → SAFE_PAUSE |
| UNCERTAIN | VERIFIED → ACTION_PENDING, reason `Action dispatched` → SAFE_PAUSE |

Test `test_failed_outcome_does_not_record_unconfirmed_action_as_dispatched` tham số hóa ba trường hợp trên, cả ba fail. `last_action_at` cũng bị cập nhật dù action không được xác nhận dispatched. UNCERTAIN bị bỏ qua nhánh phân giải riêng và lý do thực tế không được lưu trong region state qua đường này.

**Ảnh hưởng và giới hạn:** SAFE_PAUSE đã chặn action tiếp theo, không kết luận bug này tự tạo thêm click. Lỗi là đánh mất/ghi sai bằng chứng trạng thái, vi phạm yêu cầu giữ đúng sự thật của action và A10. Nếu cần tiếp tục/đối soát sau pause, lịch sử hiện tại không đáng tin cậy. Trường hợp DISPATCHED + evidence fail trong test S02 cũ đã đúng, nhưng không đại diện cho các status khác.

**Cách sửa cần chứng minh:** bảo lưu result/status/reason của backend trước khi áp dụng pause do telemetry. Chỉ ghi nhận dispatched khi status thực sự DISPATCHED; NOT_SENT/FAIL_CLOSED không được ghi click đã gửi; UNCERTAIN phải còn thể hiện bất định, không xác nhận thành công. Evidence health/pause phải độc lập với sự thật về action. Không retry action vì lỗi ghi log. Test cả bốn status × outcome enqueue thành công/thất bại, giữ test S02 chặn action mới.

**Câu hỏi Gemini:** vì sao guard lỗi ghi evidence được phép gọi hàm ghi nhận action thành công trước khi đọc backend result? Sửa dựa trên result contract, không đổi tên `Action dispatched` để hợp thức hóa mọi status.

## Ghi nhận và phạm vi còn lại

- S01: send hoàn tất, mất press ACK đã trả UNCERTAIN; counterexample đạt. Chưa chứng nhận toàn bộ socket/recovery/window identity ngoài phạm vi test.
- S02: DISPATCHED nhưng outcome fail đã SAFE_PAUSE, không gửi bước tiếp; counterexample đạt. T01 là failure path khác trong cùng phần sửa.
- Các mục PARTIAL về model/calibration, surface/coordinate binding, UI workflow/ROI, monotonic lifetime, benchmark hardware vẫn chưa được nghiệm thu. Không có code thay đổi cho các mục đó trong commit này để xét đóng.

Không cần kế hoạch dài mới. Sửa T01 và bàn giao commit/test evidence. Báo cáo 64 tests xanh là đúng trong phạm vi suite cũ; không gọi nó là hoàn thành mọi failure path hoặc PASS Production.
