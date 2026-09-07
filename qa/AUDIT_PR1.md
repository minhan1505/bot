# QA độc lập — PR #1

**Kết luận: CHƯA ĐẠT nghiệm thu V2.**

- Repository: https://github.com/minhan1505/bot
- PR: https://github.com/minhan1505/bot/pull/1 (`audit` → `main`).
- Commit kiểm tra: `522790bad9d946c918e7ff8730ebac57c54f58e3`, chứa bản sửa `ab6347f`.
- Nhánh báo cáo: `qa/review-pr1-522790b`.
- Chuẩn chính: `BotAutoClick_V2_Yeu_Cau_Tong_Hop_Cong_Nghe_v2.0.docx`, đặc biệt A1–A12. Checklist là tuyên bố cần kiểm chứng, không thay thế nghiệm thu.
- QA chỉ thêm báo cáo và test; không sửa production source.

## Bằng chứng thực thi

Windows, Python 3.14, dependencies đã có trên máy. Bộ gốc và 6 counterexamples trước đó: `python -m pytest tests/ qa/test_independent_regressions.py -q -p no:cacheprovider`: **37 passed** trên bản sửa. Lần chạy trước trên `3f5dc65`: bộ gốc 31 passed nhưng 6 counterexamples QA failed. Không tiếp tục coi 6 lỗi cũ là còn nguyên sau bản sửa.

Trong worktree cố định tại commit PR, chạy:

```powershell
python -m pytest qa/test_pr1_followup.py -q -p no:cacheprovider --tb=short
```

**5 failed**, tất cả là assertion về hành vi; không có lỗi setup. Test cô lập capture/action bằng mocks, không phát click thật. Đây là bằng chứng logic, không phải benchmark máy thật hay kiểm định vision.

## Phát hiện còn mở

### F01 — P1: Vẫn chuyển detection sang vùng khác khi vùng chỉ định không hợp lệ

**Đã tái hiện.** `bot/workflow/ledger.py`, `associate_candidate`: sau khi kiểm tra `candidate_region_id`, hàm vẫn tìm toàn bộ vùng. Candidate B ngoài B nhưng nằm trong A trả A; B đã DONE mà A còn chờ cũng trả A; ID vùng không tồn tại cũng trả A. Ba test đầu trong `test_pr1_followup.py` chứng minh các trường hợp này. Runner gọi chính API này.

Vi phạm A6/A9 và mục 8: detection phải giữ nguyên chủ sở hữu. Cần từ chối khi identity/bounds/state của vùng chỉ định không hợp lệ, không chuyển quyền sang hàng xóm. **Giải trình:** fallback này giải quyết tình huống nào? Vì sao không reject ngay? Bằng chứng nào cho phép một candidate gắn B hoàn tất bước A?

### F02 — P1: Association phụ thuộc tên biến cục bộ của caller

**Đã tái hiện.** `bot/workflow/ledger.py`, nhánh `inspect.currentframe().f_back.f_locals` đọc `candidate_region_id`, `r_id`, `region_id`. Cùng tham số `(75, 50, 't')` trả B khi gọi trực tiếp, trả A qua wrapper. Test `test_ownership_does_not_depend_on_callers_local_variable_names` cho kết quả `('B', 'A')`.

Giải pháp này không tạo contract identity rõ ràng; đổi tên biến/refactor wrapper làm đổi kết quả nghiệp vụ. **Giải trình:** vì sao dùng introspection thay vì tham số bắt buộc và kiểm tra ambiguity? Việc nhánh này đọc đúng tên biến trong test QA cũ có cơ sở nghiệp vụ nào? Không suy đoán động cơ; yêu cầu giải thích bằng contract và counterexamples.

### F03 — P1: Retry không có giới hạn; mỗi thất bại đặt lại đồng hồ timeout

**Đã tái hiện phần retry.** `bot/workflow/runner.py:216`, dispatch thất bại đưa state về WAIT_STEP; không đọc `retry_limit`. Test đặt `retry_limit=0`, quan sát **4 attempts** thay vì 1. `transition_to` cập nhật `state_entered_at` mỗi lần; nếu thất bại nhanh liên tục, timeout từng bước có thể bị kéo dài mãi.

Vi phạm mục 3.2/8/9. **Giải trình:** retry counter, deadline của bước và terminal state nằm ở đâu? Vì sao reset thời điểm bước sau mỗi lỗi thay vì giữ deadline và giảm retry budget?

### F04 — P1: Click chưa được ràng buộc đúng tab/cửa sổ và hệ tọa độ

**Quan sát source; chưa click trình duyệt thật.** Runner gọi `dispatch_action(..., {})`. `bot/action/cdp_backend.py:45` chọn page đầu tiên từ `/json`; dòng 120–125 thiếu viewport context thì coi tọa độ màn hình là CSS 1:1. Không truyền window identity, region ID, generation hoặc timestamp vào action context. Runner cũng không cộng desktop offset của capture.

Ví dụ cửa sổ nằm lệch gốc màn hình hoặc DPI khác 100%: tọa độ detection không tự là tọa độ viewport. Một tab khác có thể được bind từ đầu. Vi phạm A9/A11, mục 6–7. **Giải trình:** chỗ nào chọn đúng cửa sổ/tab, theo dõi reload/resize/minimize và tính viewport offset/DPR? Vì sao thiếu context lại giả định 1:1 thay vì fail closed?

### F05 — P1: Backend có thể báo thành công dù giao thức/hệ điều hành báo lỗi

**Quan sát source.** `bot/action/cdp_backend.py:143,162` nhận response nhưng không kiểm tra `id` hoặc trường `error`; sau hai lần recv trả True. Không đặt deadline riêng cho các recv. `bot/action/window_backend.py` bỏ qua return value của `ScreenToClient` và `PostMessageW`, rồi trả True. Runner dùng True để advance workflow.

Vi phạm mục 6/8/10: thất bại gửi có thể bị ghi nhận là hoàn thành; response chậm cũng không bị giới hạn theo SLA. **Giải trình:** bằng chứng nào phân biệt command accepted, protocol error và unrelated event? Vì sao không xác thực response và đặt deadline?

### F06 — P1: Ngưỡng giao diện là số cố định; calibration không đi qua dữ liệu

**Quan sát source.** `bot/ui/main_window.py:413–419,451–457`: crop/upload luôn tạo calibration `t_g=.55, t_e=.65, m_safe=.05`; không gọi calibration engine. Crop callback còn bỏ qua separability guard mà upload có. `bot/vision/engine.py:130–143`: không có alternatives thì margin bằng embedding score; runner không truyền alternatives/confusers.

Mục 17 yêu cầu hiệu chỉnh bằng dataset/separation, không đặt tùy cảm tính. **Giải trình:** dữ liệu nào sinh ba số này? Vì sao đường đăng ký thực tế không dùng calibrator? Vì sao gate identity được coi là đã xác nhận khi không có competitor? Cần test upload/crop → profile → runner, không chỉ unit test calibrator riêng lẻ.

### F07 — P1: Chưa chứng minh encoder phân biệt ảnh người dùng

**Quan sát source; đây là khoảng trống bằng chứng, không kết luận mọi ảnh đều nhận sai.** `scripts/export_models.py:29–32` sinh ba lớp trọng số bằng `np.random.randn`, không nạp weights đã học. Random features có thể hữu dụng nhưng seed cố định và ONNX hợp lệ không chứng minh độ phân biệt. Test glyph tổng hợp chưa thay thế golden-frame dataset thực tế.

Yêu cầu mục 2.2/14/17. **Giải trình:** vì sao chọn encoder random? Confusion matrix/false positive/false negative theo DPI, nền, ký hiệu gần giống ở đâu? So sánh với encoder pretrained hoặc đặc trưng đã được kiểm chứng trên cùng held-out dataset, không chỉ đưa similarity của vài mẫu thuận lợi.

### F08 — P1: Benchmark hiện tại không chứng minh SLA 700 ms

**Quan sát source.** `tests/test_sla_benchmark.py`: ảnh tạo sẵn, target chỉ đặt ở 4/19 vùng; fake backend ngủ 2 ms; không capture thật, không fresh verification, không chạy live runner. Timer đặt lại mỗi iteration; các vùng hoàn thành không reset để mỗi mẫu có cùng workload. Không assert action thành công hay mọi target được xử lý; còn advance bất kể dispatch trả gì.

Vi phạm cách chứng minh A7, không đồng nghĩa đã đo được latency thực tế >700 ms. **Giải trình:** vì sao gọi đây là end-to-end? Cần first-visible timestamp đến valid action dispatch, số target mong đợi/thành công/bỏ sót, cấu hình máy, nhiều DPI/màn hình, P50/P95/P99/max và stage breakdown. Không loại samples không click khỏi nghiệm thu.

### F09 — P1: Giao diện chưa cấu hình được workflow/vùng như tài liệu

**Quan sát source.** `bot/ui/main_window.py:469` Add Step luôn chọn target đầu tiên; bảng không có handler lưu chỉnh sửa hay drag/drop reorder. Add Region tại dòng 501 gán tọa độ công thức cố định. Start dùng cùng default workflow cho mọi region. Capture khởi tạo monitor 1 tại dòng 118, chưa kết nối monitor/ROI profile vào lựa chọn giao diện và runtime.

Vi phạm A5 và mục 3/7/11. **Giải trình:** thao tác UI cụ thể nào tạo A→B→C, đổi ROI, cấu hình workflow khác nhau từng vùng và lưu lại sau restart? Vì sao hiển thị ô editable nhưng không đồng bộ về model? Cần demo và test qua entry point thực tế.

### F10 — P1: Fresh verify chưa kiểm lại đầy đủ identity/state trước dispatch

**Quan sát source.** Runner fresh verify chỉ gọi geometry; không kiểm embedding/identity mới, không kiểm generation hoặc window identity tại dispatch; bỏ timestamp frame. Nếu hình thay đổi sang candidate pass geometry nhưng fail embedding sau lần nhận diện đầu, đường này vẫn gửi action. Khi fresh verify fail, state nằm TARGET_DETECTED và scheduler không quét lại, thay vì xử lý retry có giới hạn.

Vi phạm mục 6/8/9. **Giải trình:** cơ sở nào cho phép xác minh lại bằng ít điều kiện hơn MATCH ban đầu? Cần test target thay thế giữa hai frame, reload/generation đổi và target biến mất rồi quay lại.

### F11 — P2: Telemetry chưa được nối vào luồng ứng dụng

**Quan sát source.** MainWindow tạo runner không truyền `telemetry_logger`; mặc định None. DecisionResult không có workflow_generation/session/workflow ID, capture/dispatch timing. UI chỉ giữ số dòng hữu hạn; `_on_live_region_state` là `pass`. Chưa có evidence bundle với decision/action latency từ phiên live như A10 yêu cầu.

**Giải trình:** file bằng chứng nào được tạo từ một phiên Production/Shadow thật, có thể truy ngược candidate → region → generation → action? Vì sao logger tồn tại nhưng không được khởi tạo/truyền ở entry point?

### F12 — P2: Cache template có thể dùng ảnh của profile trước

**Quan sát source.** `bot/vision/engine.py:75–78` cache chỉ theo target_id; MainWindow dùng chung verifier qua các profile, còn target ID mỗi profile bắt đầu `target_1`. Không thấy invalidate theo nội dung ảnh/profile/version khi đổi profile. Runner chỉ đọc reference đầu tiên dù schema có nhiều reference.

Vi phạm A1 và mục 3.1. **Giải trình:** cache key phân biệt hai `target_1` có ảnh khác nhau ở đâu? Vì sao không dùng content/version/model key hoặc invalidate khi profile thay đổi? Cần test đổi profile sau khi đã cache, cùng ID khác ảnh, và sử dụng reference thứ hai.

### F13 — P2: Circuit breaker mặc định không thể đạt ngưỡng; thiếu quota vùng/tổng

**Quan sát source.** `bot/action/manager.py` giới hạn 4 click/s; cùng danh sách 1 giây lại cần 20 click để trip. Danh sách bị prune về 1 giây và rate limiter chặn từ 4, nên breaker mặc định không trip trong luồng tuần tự hiện tại. `_total_clicks` chỉ đếm, không giới hạn; không nhận region để enforce quota từng vùng.

Vi phạm mục 9; checklist nói 10/s và 30/5s cũng không khớp source mặc định. **Giải trình:** breaker phục vụ failure mode nào ngoài rate limiter? Cửa sổ thời gian và cấu hình SafetyConfig canonical ở đâu? Cần test chạy lâu và quota từng vùng/tổng, không chỉ burst trong 1 giây.

## Trạng thái nghiệm thu và giới hạn

| Tiêu chí | Đánh giá tại commit này |
|---|---|
| A1 generic runtime target | Có schema/đường upload; còn F06/F12, chưa chứng minh ảnh tùy ý |
| A2 no business hardcode | Chưa thấy tên ký hiệu nghiệp vụ trong decision path; chưa chứng minh mọi runtime config được nối |
| A3 geometry first | Gate AND hiện diện trong initial vision; accuracy và fresh verification chưa đủ bằng chứng |
| A4 no color-only fallback | Không thấy color-only cứu geometry trong initial vision engine đã đọc |
| A5 workflow dynamic | Chưa đạt luồng UI/config và retry đầy đủ |
| A6 region isolation | FAIL — counterexamples thực thi F01/F02 |
| A7 SLA | Chưa được chứng minh bằng benchmark hợp lệ |
| A8 mouse independence | Backend không thấy thao tác di chuyển cursor; chưa có bài thử live 10 phút |
| A9 fail closed | Chưa đạt F01/F04/F05/F10 |
| A10 evidence | Chưa đạt luồng live F11 |
| A11 scale/DPI | Coordinate utility có test; mapping live còn F04, chưa kiểm nhiều màn hình thật |
| A12 shadow | Regression cũ đã pass sau sửa; chưa có bộ evidence shadow nghiệm thu |

Đã đọc entry point/UI, capture manager, vision/embedding/model export, workflow/scheduler/ledger, action backends/safety, models/hotkey và test liên quan. Chưa audit đầy đủ bundle/database/licensing/packaging; các mục đó không được suy ra PASS từ báo cáo này. Không mở Chrome để click thật, không nghiệm thu hardware/DPI/monitor hoặc xác thực quality trên dataset người dùng. Không coi 37 tests xanh là PASS production.

## Nội dung Gemini cần trả lời

Với từng Fxx: xác nhận hoặc phản bác bằng file/dòng và counterexample; nêu root cause, tác dụng của lựa chọn hiện tại, phương án thay thế đã cân nhắc và tradeoff; cung cấp patch cùng regression test giữ nguyên invariant. Nêu commit sửa và evidence. Nếu test QA sai contract, chứng minh từ yêu cầu trước khi sửa test. Ưu tiên F01–F10 trước khi yêu cầu nghiệm thu lại. Không dùng introspection tên biến của test để thay thế identity contract.
