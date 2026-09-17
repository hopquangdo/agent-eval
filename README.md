# Agent Eval

Script đọc danh sách câu hỏi từ file `.txt`, gửi từng câu hỏi tới chatbot qua SSE và lưu câu trả lời tương ứng vào JSONL.

## Yêu cầu

- Python 3.10+
- Thư viện `requests`
- API chatbot đang hoạt động

Cài `requests` nếu môi trường chưa có:

```powershell
python -m pip install requests
```

## Chuẩn bị câu hỏi

Mỗi dòng không trống trong [data/question.txt](data/question.txt) là một câu hỏi. Script giữ nguyên thứ tự câu hỏi khi ghi kết quả.

## Chạy mặc định

Từ thư mục gốc repository:

```powershell
python agent-eval/test_answers.py
```

Hoặc chạy trong thư mục `agent-eval`:

```powershell
cd agent-eval
python test_answers.py
```

Mặc định:

- Input: `agent-eval/data/question.txt`
- API: `https://vietdemo.com/chat/api/v1`
- Output mặc định: mỗi lần chạy tạo thư mục `agent-eval/output/result-{question_name}-{timestamp}/`
- Trong thư mục output có `result-{question_name}.jsonl` và `result-{question_name}.md`
- Timeout mỗi câu: 120 giây
- Số luồng tối đa: 10; số luồng thực tế là `min(10, số câu hỏi)`


## Định dạng JSONL

Mỗi dòng trong file kết quả là một object JSON độc lập:

```json
[
{
    "id": 1,
    "question_name": "question",
    "timestamp": "20260917_143025",
    "question": "Hôm nay có gì bất thường không?",
    "answer": "...",
    "status": "OK",
    "latency_seconds": 12.34,
    "error": null
}
```

Ý nghĩa các trường:

- `id`: số thứ tự câu hỏi.
- `question_name`: tên file input không có đuôi, dùng trong tên output.
- `timestamp`: thời điểm bắt đầu lần chạy, format `YYYYMMDD_HHMMSS`.
- `question`: câu hỏi gửi tới chatbot.
- `answer`: câu trả lời nhận được; là `null` nếu gọi lỗi.
- `status`: `OK` hoặc `ERROR`.
- `latency_seconds`: thời gian xử lý câu hỏi.
- `error`: nội dung lỗi, hoặc `null` nếu thành công.

Một câu bị lỗi không làm dừng toàn bộ batch; lỗi được ghi vào dòng tương ứng trong JSONL và Markdown.

## File Markdown

Sau khi chạy xong, file `.md` trong cùng thư mục sẽ chứa từng cặp:

```markdown
## Câu hỏi 1

**Question:** Hôm nay có gì bất thường không?

**Answer:**

Nội dung câu trả lời của chatbot.
```

## Eval theo flow hội thoại (multi-turn)

Nhiều câu hỏi thực tế phụ thuộc ngữ cảnh của câu trước (vd: "Trạm này ai làm?" chỉ có nghĩa sau khi đã hỏi về một trạm cụ thể). Để test theo kịch bản hội thoại nhiều lượt:

```powershell
python test_answers.py --mode conversation --input data\conversations.txt
```

Định dạng input cho mode này: các dòng liên tiếp (không có dòng trống xen giữa) thuộc cùng một hội thoại; dòng trống ngăn cách giữa các hội thoại. Ví dụ `data/conversations.txt`:

```
Trạm BTS0123 đang ở bước nào?
Trạm này thuộc hợp đồng nào?
Trạm này vướng gì?

Khu vực nào đang chậm nhất?
Hợp đồng nào sắp hết hạn?
```

Khi chạy:

- Các turn trong **cùng một hội thoại** được gửi **tuần tự**, dùng chung một `session_id` để backend giữ ngữ cảnh.
- Các **hội thoại khác nhau** chạy **song song** (số luồng theo `--workers`, mỗi luồng xử lý trọn 1 hội thoại).
- Kết quả JSONL/Markdown có thêm `conversation_id` và `turn_index` để biết câu nào thuộc hội thoại nào, ở lượt thứ mấy.
- `--limit` ở mode này giới hạn theo **số hội thoại**, không phải số câu hỏi.
