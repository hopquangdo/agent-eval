"""Chạy danh sách câu hỏi trong file TXT qua chatbot và lưu kết quả JSON.

Ví dụ:
    python test_answers.py
    python test_answers.py --input data\question.txt --out output\answers.json --limit 10
    python test_answers.py --base-url http://localhost:8000/chat/api/v1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def unwrap(payload: dict) -> dict:
    if payload.get("success") is False:
        error = payload.get("error") or {}
        raise RuntimeError(error.get("message", "Chatbot trả về lỗi"))
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def ask_chat(base_url: str, question: str, timeout: float) -> tuple[str, float]:
    started = time.perf_counter()
    session_id = str(uuid.uuid4())
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/stream",
        json={"message": question, "session_id": session_id},
        timeout=timeout,
    )
    response.raise_for_status()
    stream_id = unwrap(response.json())["stream_id"]

    reply = ""
    token_parts: list[str] = []
    with requests.get(
        f"{base_url.rstrip('/')}/chat/stream/{stream_id}",
        headers={"Accept": "text/event-stream"},
        stream=True,
        timeout=timeout,
    ) as stream:
        stream.raise_for_status()
        event_name = "message"
        data_lines: list[str] = []
        for raw_line in stream.iter_lines(decode_unicode=True):
            line = raw_line or ""
            if line == "":
                if data_lines:
                    raw_data = "".join(data_lines)
                    _consume_event(event_name, raw_data, token_parts)
                    if event_name == "done":
                        try:
                            reply = json.loads(raw_data).get("reply") or ""
                        except json.JSONDecodeError:
                            pass
                    data_lines = []
                event_name = "message"
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())

    if not reply:
        reply = "".join(token_parts).strip()
    if not reply:
        raise RuntimeError("Stream kết thúc nhưng không có câu trả lời")
    return reply, time.perf_counter() - started


def _consume_event(event_name: str, raw_data: str, token_parts: list[str]) -> None:
    if event_name != "token":
        return
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError:
        return
    content = payload.get("content")
    if isinstance(content, str):
        token_parts.append(content)


def run_one(
    index: int,
    question: str,
    question_name: str,
    timestamp: str,
    base_url: str,
    timeout: float,
) -> dict:
    result = {
        "id": index,
        "question_name": question_name,
        "timestamp": timestamp,
        "question": question,
    }
    try:
        answer, elapsed = ask_chat(base_url, question, timeout)
        result.update({
                "answer": answer,
            "status": "OK",
            "latency_seconds": round(elapsed, 2),
            "error": None,
        })
    except Exception as exc:  # noqa: BLE001 - lỗi một câu không dừng cả batch
        result.update({
            "answer": None,
            "status": "ERROR",
            "latency_seconds": 0,
            "error": str(exc),
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Test câu trả lời chatbot từ file TXT")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).parent / "data" / "question.txt",
        help="Mỗi dòng không trống là một câu hỏi",
    )
    parser.add_argument("--base-url", default="https://vietdemo.com/chat/api/v1")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Thư mục chứa output; mặc định tạo một thư mục riêng cho mỗi lần chạy",
    )
    parser.add_argument("--out", type=Path, default=None, help="Tên file JSONL cũ, không khuyến nghị")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers phải lớn hơn hoặc bằng 1")
    with args.input.open(encoding="utf-8-sig") as handle:
        questions = [line.strip() for line in handle if line.strip()]
    if args.limit is not None:
        questions = questions[:args.limit]

    worker_count = min(args.workers, len(questions)) if questions else 1
    question_name = re.sub(r"[^\w.-]+", "_", args.input.stem).strip("._") or "questions"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.out:
        output_dir = args.out.parent
        jsonl_path = args.out
    else:
        output_dir = args.out_dir or (
            Path(__file__).parent / "output" / f"result-{question_name}-{timestamp}"
        )
        jsonl_path = output_dir / f"result-{question_name}.jsonl"
    markdown_path = output_dir / f"result-{question_name}.md"

    print(f"Chạy {len(questions)} câu hỏi với {worker_count} luồng...")
    results: list[dict] = [{} for _ in questions]
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(
                run_one,
                index + 1,
                question,
                question_name,
                timestamp,
                args.base_url,
                args.timeout,
            ): index
            for index, question in enumerate(questions)
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            results[futures[future]] = future.result()
            print(f"[{completed}/{len(questions)}] hoàn tất", flush=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    with markdown_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# Kết quả kiểm thử: {question_name}\n\n")
        handle.write(f"- Timestamp: `{timestamp}`\n")
        handle.write(f"- Số câu hỏi: {len(results)}\n\n")
        for result in results:
            handle.write(f"## Câu hỏi {result['id']}\n\n")
            handle.write(f"**Question:** {result['question']}\n\n")
            handle.write("**Answer:**\n\n")
            handle.write(f"{result['answer'] or '(Không có câu trả lời)'}\n\n")
            handle.write(f"- Status: `{result['status']}`\n")
            handle.write(f"- Latency: `{result['latency_seconds']}` giây\n")
            if result["error"]:
                handle.write(f"- Error: `{result['error']}`\n")
            handle.write("\n---\n\n")

    print(f"Đã lưu JSONL: {jsonl_path}")
    print(f"Đã lưu Markdown: {markdown_path}")


if __name__ == "__main__":
    main()