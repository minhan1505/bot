# QA follow-up — db4d20e

**Kết luận: R02/R03 đạt các counterexamples đã kiểm; R01/R04 còn nhánh lỗi. Chưa đạt Production.**

Base `db4d20e`, nhánh `qa/review-db4d20e`. QA chỉ thêm báo cáo và test. Windows/Python 3.14, không click/network thật.

## Kết quả chạy

- Bộ hiện có: `python -m pytest tests/ qa/ -q -p no:cacheprovider --tb=short`: **62 passed in 4.54s**.
- Bổ sung: `python -m pytest qa/test_db4d20e_followup.py -q -p no:cacheprovider --tb=short`: **2 failed in 0.64s**. Assertion hành vi, không lỗi setup.

## S01 — P1: Mouse-down đã gửi, mất ACK lại bị gán NOT_SENT (R01 còn mở)

`bot/action/cdp_backend.py`, sau `await ws.send(press_msg)`, `_recv_ack` thất bại luôn trả NOT_SENT. `pressed_down` chỉ được set True sau ACK. Do đó transport đã gửi nhưng không biết ứng dụng nhận/xử lý chưa vẫn bị coi là chưa gửi. Runner cho phép retry NOT_SENT, trái uncertainty contract.

Test `test_press_sent_but_ack_lost_is_uncertain` dùng backend thật, socket giả: send ghi nhận mousePressed thành công, recv raise TimeoutError. Kết quả thực tế **NOT_SENT**, kỳ vọng **UNCERTAIN**. Không cần timeout 100ms thật để chứng minh phân loại sai.

Yêu cầu: phân biệt lỗi kết nối trước send, send có thể đã truyền, protocol error xác định, ACK timeout/disconnect sau send. Không dùng trạng thái ACK thành công để đại diện cho trạng thái đã gửi. Không retry click khi không chứng minh được chưa có side effect. Giữ testcase down-ACK thành công/up lỗi cũ; thêm cả lost press ACK. Đây là nhánh đã nêu trong uncertainty contract trước, không phải thêm yêu cầu.

## S02 — P1: Outcome không ghi được, runner vẫn tiếp tục phát action (R04 còn mở)

`bot/workflow/runner.py`, nhánh `if not outcome_ok` chỉ `logger.error("Marking evidence incomplete")` và release token. Không có cờ evidence-incomplete hoặc pause được thiết lập ở đây; sau đó tiếp tục xử lý DISPATCHED, advance bước và cho action mới.

Test `test_outcome_evidence_failure_blocks_new_actions`: workflow hai bước, cooldown 0; logger nhận intent nhưng từ chối outcome. Quan sát **2 action, state DONE**. Kỳ vọng chỉ action đầu đã xảy ra, chặn action mới do không bảo toàn evidence. Không được đổi kết quả của action đầu thành NOT_SENT hoặc retry lại chỉ vì ghi log thất bại.

Yêu cầu: giữ đúng kết quả hành động đã xảy ra, đặt trạng thái evidence không đầy đủ có thể quan sát/export, chặn action mới bằng SAFE_PAUSE/guard tương đương. Kiểm tra session/UI nhận trạng thái, outcome-token expiry và writer lỗi; không dùng một dòng console log thay cho trạng thái hệ thống. Policy này nằm trong contract bảo vệ A10 đã chốt.

## Ghi nhận các sửa đổi đạt trong phạm vi kiểm tra

- R01: CDP/Win32 đã trả structured result; down ACK thành công rồi up lỗi trả UNCERTAIN, test cũ pass. Chưa đạt lost press ACK S01.
- R02: UNCERTAIN_HOLD đã quay lại timeout theo deadline, test pass.
- R03: log_event kiểm capacity và enqueue cùng RLock; reaper được khóa. Các test slot stealing cũ pass. Không suy ra đã stress-test toàn bộ concurrency.
- R04: intent consume thất bại chặn action/release/pause, test pass. Outcome fail còn S02.

Các hạng mục PARTIAL đã ghi trước vẫn còn: pretrained model/calibration UI, surface/coordinate binding thực tế, UI workflow/ROI hoàn chỉnh, monotonic lifetime và benchmark live A7. Audit này không lặp lại toàn bộ source không đổi và không biến 62 unit/regression tests thành chứng nhận Production.

Gemini sửa trực tiếp S01/S02, cung cấp commit và bằng chứng. Không cần kế hoạch dài mới; không hạ expectation của test. Công nhận đúng phạm vi đã sửa, không tiếp tục ghi R01/R04 hoàn toàn khép kín trước khi hai nhánh trên đạt.
