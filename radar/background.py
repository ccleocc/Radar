from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from datetime import date
from typing import Any


class CollectionJobManager:
    """Process-local background worker that survives Streamlit script reruns/page changes."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._state: dict[str, Any] = self._blank_state()

    @staticmethod
    def _blank_state() -> dict[str, Any]:
        return {
            "status": "idle", "message": "暂无后台采集任务", "event": "idle",
            "started_at": "", "finished_at": "", "progress": 0.0,
            "query_index": 0, "query_total": 0, "keyword": "", "market": "",
            "page_num": 0, "page_total": 0, "keyword_remaining": 0,
            "commit_index": 0, "commit_total": 0,
            "candidate_count": 0, "pdf_attempts": 0, "pdf_success": 0,
            "parsed_count": 0, "identified_count": 0, "new_project_count": 0,
            "new_announcement_count": 0, "errors_count": 0,
            "logs": [], "latest_results": [], "stats": None, "error": "",
        }

    def start(self, *, start_date: date, end_date: date, markets: list[str], regions: list[str],
              page_size: int, pages_per_keyword: int, max_pdfs: int,
              include_unknown_dates: bool = False) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("已有采集任务正在后台运行，请等待完成或先中断当前任务。")
            self._cancel_event = threading.Event()
            self._state = self._blank_state()
            self._state.update({
                "status": "running", "event": "start", "message": "任务启动中…",
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "progress": 0.01,
                "query_total": max(1, len(markets) * len(regions)),
            })
            cancel_event = self._cancel_event
            kwargs = {
                "start_date": start_date, "end_date": end_date, "markets": list(markets),
                "regions": list(regions), "page_size": int(page_size),
                "pages_per_keyword": int(pages_per_keyword), "max_pdfs": int(max_pdfs),
                "include_unknown_dates": bool(include_unknown_dates),
            }
            self._thread = threading.Thread(target=self._run, args=(kwargs, cancel_event), daemon=True,
                                            name="sea-factory-radar-collector")
            self._thread.start()

    def _run(self, kwargs: dict[str, Any], cancel_event: threading.Event) -> None:
        from .pipeline import collect_official_announcements
        try:
            stats = collect_official_announcements(**kwargs, progress=self.update, cancel_event=cancel_event)
            with self._lock:
                self._state["stats"] = stats
                if isinstance(stats.get("results"), list):
                    # Refresh with final post-commit statuses and include metadata-only
                    # candidates whose PDF parsing exceeded this run's configured limit.
                    self._state["latest_results"] = [dict(x) for x in reversed(stats["results"][-20:]) if isinstance(x, dict)]
                self._state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if stats.get("cancelled"):
                    self._state["status"] = "cancelled"
                elif self._state.get("status") != "failed":
                    self._state["status"] = "completed"
                self._state["progress"] = 1.0
        except Exception as exc:
            with self._lock:
                self._state["status"] = "failed"
                self._state["event"] = "fatal"
                self._state["message"] = f"采集失败：{type(exc).__name__}: {exc}"
                self._state["error"] = traceback.format_exc()
                self._state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._append_log_locked(self._state["message"])

    def update(self, event: dict[str, Any]) -> None:
        with self._lock:
            event_type = str(event.get("event") or "")
            self._state["event"] = event_type
            self._state["message"] = str(event.get("message") or "")
            for key in ("query_index", "query_total", "keyword", "market", "page_num", "page_total",
                        "keyword_remaining", "candidate_count", "announcement_title", "commit_index", "commit_total"):
                if key in event:
                    self._state[key] = event[key]
            stats = event.get("stats") or {}
            for key in ("candidate_count", "pdf_attempts", "pdf_success", "parsed_count", "identified_count",
                        "new_project_count", "new_announcement_count", "errors_count"):
                if key in stats:
                    self._state[key] = stats[key]
            if event_type == "commit_start":
                self._state["status"] = "committing"
                self._state["progress"] = 0.95
            elif event_type == "commit_progress":
                idx = int(event.get("commit_index", 0)); total = max(1, int(event.get("commit_total", 1)))
                self._state["progress"] = min(0.99, 0.95 + 0.04 * idx / total)
            elif event_type in {"query", "query_done"}:
                idx = int(event.get("query_index", 0)); total = max(1, int(event.get("query_total", 1)))
                page = int(event.get("page_num", 1)); pages = max(1, int(event.get("page_total", 1)))
                self._state["progress"] = min(0.72, 0.72 * ((max(0, idx - 1) + page / pages) / total))
            elif event_type in {"discovered", "item_start", "item_done", "item_error"}:
                idx = int(event.get("item_index", 0)); total = max(1, int(event.get("item_total", 1)))
                self._state["progress"] = min(0.94, 0.72 + 0.22 * idx / total)
            elif event_type in {"finish", "cancelled", "fatal"}:
                self._state["progress"] = 1.0
                self._state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._state["status"] = {"finish": "completed", "cancelled": "cancelled", "fatal": "failed"}[event_type]
            if event_type in {"query", "query_done", "discovered", "item_start", "item_done", "item_error", "commit_start", "finish", "cancelled", "fatal"}:
                self._append_log_locked(self._state["message"])
            elif event_type == "commit_progress":
                idx = int(event.get("commit_index", 0)); total = max(1, int(event.get("commit_total", 1)))
                if idx % 20 == 0 or idx >= total:
                    self._append_log_locked(self._state["message"])
            result = event.get("result")
            if isinstance(result, dict):
                recent = self._state["latest_results"]
                recent.insert(0, result)
                self._state["latest_results"] = recent[:20]

    def _append_log_locked(self, message: str) -> None:
        self._state["logs"].append(f"{time.strftime('%H:%M:%S')}  {message}")
        self._state["logs"] = self._state["logs"][-30:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            state["logs"] = list(self._state.get("logs", []))
            state["latest_results"] = [dict(x) for x in self._state.get("latest_results", [])]
            stats = self._state.get("stats")
            state["stats"] = dict(stats) if isinstance(stats, dict) else stats
            return state

    def cancel(self) -> tuple[bool, str]:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return False, "当前没有正在运行的采集任务。"
            if self._state.get("status") == "committing":
                return False, "任务正在最终入库，为避免只保存一部分，当前阶段不再接受中断。"
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._state["message"] = "已收到中断请求，正在等待当前网络请求结束；本轮临时结果不会保存。"
            self._append_log_locked(self._state["message"])
            return True, self._state["message"]


job_manager = CollectionJobManager()
