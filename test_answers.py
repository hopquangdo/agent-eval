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


def ask_chat(
    base_url: str, question: str, timeout: float, session_id: str | None = None
) -> tuple[str, float]:
    started = time.perf_counter()
    session_id = session_id or str(uuid.uuid4())
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


def parse_conversations(path: Path) -> list[list[str]]:
    """Đọc file input; các dòng liên tiếp (không cách nhau bởi dòng trống)
    thuộc cùng một hội thoại (nhiều turn, giữ chung session_id)."""
    conversations: list[list[str]] = []
    current: list[str] = []
    with path.open(encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line:
                current.append(line)
            elif current:
                conversations.append(current)
                current = []
    if current:
        conversations.append(current)
    return conversations


def run_one(
    index: int,
    question: str,
    question_name: str,
    timestamp: str,
    base_url: str,
    timeout: float,
    session_id: str | None = None,
    conversation_id: int | None = None,
    turn_index: int | None = None,
) -> dict:
    result = {
        "id": index,
        "question_name": question_name,
        "timestamp": timestamp,
        "question": question,
    }
    if conversation_id is not None:
        result["conversation_id"] = conversation_id
        result["turn_index"] = turn_index
    try:
        answer, elapsed = ask_chat(base_url, question, timeout, session_id=session_id)
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


def run_conversation(
    conversation_id: int,
    turns: list[str],
    start_index: int,
    question_name: str,
    timestamp: str,
    base_url: str,
    timeout: float,
) -> list[dict]:
    """Chạy tuần tự các turn trong một hội thoại, dùng chung session_id
    để backend giữ ngữ cảnh qua các lượt (vd: 'trạm này', 'hợp đồng này')."""
    session_id = str(uuid.uuid4())
    results = []
    for turn_offset, question in enumerate(turns):
        results.append(
            run_one(
                start_index + turn_offset,
                question,
                question_name,
                timestamp,
                base_url,
                timeout,
                session_id=session_id,
                conversation_id=conversation_id,
                turn_index=turn_offset + 1,
            )
        )
    return results


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
    parser.add_argument("--limit", type=int, default=None, help="Giới hạn số hội thoại chạy")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers phải lớn hơn hoặc bằng 1")

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

    conversations = parse_conversations(args.input)
    if args.limit is not None:
        conversations = conversations[: args.limit]
    worker_count = min(args.workers, len(conversations)) if conversations else 1
    total_turns = sum(len(turns) for turns in conversations)
    multi_turn = any(len(turns) > 1 for turns in conversations)
    if multi_turn:
        print(f"Phát hiện hội thoại nhiều lượt. Chạy {len(conversations)} hội thoại ({total_turns} lượt hỏi) với {worker_count} luồng...")
    else:
        print(f"Chạy {total_turns} câu hỏi với {worker_count} luồng...")

    results: list[list[dict]] = [None] * len(conversations)
    start_indices: list[int] = []
    next_index = 1
    for turns in conversations:
        start_indices.append(next_index)
        next_index += len(turns)

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(
                run_conversation,
                conv_id + 1,
                turns,
                start_indices[conv_id],
                question_name,
                timestamp,
                args.base_url,
                args.timeout,
            ): conv_id
            for conv_id, turns in enumerate(conversations)
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            results[futures[future]] = future.result()
            print(f"[{completed}/{len(conversations)}] hoàn tất", flush=True)

    flat_results = [turn for conv_results in results for turn in conv_results]

    output_dir.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for result in flat_results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    with markdown_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# Kết quả kiểm thử: {question_name}\n\n")
        handle.write(f"- Timestamp: `{timestamp}`\n")
        handle.write(f"- Số hội thoại: {len(conversations)}\n")
        handle.write(f"- Tổng số lượt hỏi: {total_turns}\n\n")
        for conv_results in results:
            if multi_turn:
                handle.write(f"## Hội thoại {conv_results[0]['conversation_id']}\n\n")
            for result in conv_results:
                heading = f"### Lượt {result['turn_index']}" if multi_turn else f"## Câu hỏi {result['id']}"
                handle.write(f"{heading}\n\n")
                handle.write(f"**Question:** {result['question']}\n\n")
                handle.write("**Answer:**\n\n")
                handle.write(f"{result['answer'] or '(Không có câu trả lời)'}\n\n")
                handle.write(f"- Status: `{result['status']}`\n")
                handle.write(f"- Latency: `{result['latency_seconds']}` giây\n")
                if result["error"]:
                    handle.write(f"- Error: `{result['error']}`\n")
                handle.write("\n")
            handle.write("---\n\n")

    print(f"Đã lưu JSONL: {jsonl_path}")
    print(f"Đã lưu Markdown: {markdown_path}")


if __name__ == "__main__":
    main()