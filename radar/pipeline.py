from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader

from .analyzer import analyze_announcement, normalize_text
from .cninfo import CNInfoClient, discover_candidates
from .config import DEFAULTS, REGION_QUERIES
from .db import (
    finish_run, get_announcement, start_run, upsert_announcement, upsert_project,
)

ProgressFn = Callable[[dict[str, Any]], None]


def _in_date_range(pub_date: str, start_date: date, end_date: date, include_unknown: bool) -> bool:
    if not pub_date:
        return include_unknown
    try:
        parsed = date.fromisoformat(pub_date[:10])
        return start_date <= parsed <= end_date
    except ValueError:
        return include_unknown


def collect_official_announcements(
    start_date: date,
    end_date: date,
    markets: list[str] | None = None,
    regions: list[str] | None = None,
    page_size: int = DEFAULTS["page_size"],
    pages_per_keyword: int = DEFAULTS["pages_per_keyword"],
    max_pdfs: int = DEFAULTS["max_pdfs"],
    include_unknown_dates: bool = False,
    progress: ProgressFn | None = None,
    client: CNInfoClient | None = None,
    cancel_event: Any = None,
) -> dict[str, Any]:
    if start_date > end_date:
        raise ValueError("起始日期不能晚于结束日期")
    if (end_date - start_date).days > 730:
        raise ValueError("单次查询日期范围不建议超过两年，请缩小日期范围后采集")
    client = client or CNInfoClient()
    markets = markets or ["sse", "szse", "bj"]
    regions = regions or REGION_QUERIES
    run_id = start_run(start_date.isoformat(), end_date.isoformat(), len(markets))
    stats: dict[str, Any] = {
        "run_id": run_id, "query_count": 0, "candidate_count": 0, "pdf_attempts": 0,
        "pdf_success": 0, "parsed_count": 0, "identified_count": 0, "new_project_count": 0,
        "new_announcement_count": 0, "errors_count": 0, "errors": [], "results": [],
        "query_stats": [], "date_filtered": 0, "cache_reused": 0, "outside_date_count": 0,
        "unknown_date_count": 0, "not_candidates_count": 0, "pending_count": 0,
        "cancelled": False,
    }
    # Staged in memory and committed only if the entire collection isn't interrupted.
    staged_updates: dict[str, dict[str, Any]] = {}
    staged_project_ids: list[str] = []
    result_by_ann_id: dict[str, dict[str, Any]] = {}

    def emit(event: dict[str, Any]) -> None:
        if progress:
            snapshot = {k: stats.get(k) for k in ["candidate_count", "pdf_attempts", "pdf_success", "parsed_count", "identified_count", "new_project_count", "errors_count"]}
            # Search callbacks carry the live unique-candidate count before the PDF phase starts.
            for key in snapshot:
                if key in event:
                    snapshot[key] = event[key]
            progress({"run_id": run_id, **event, "stats": snapshot})

    try:
        emit({"event": "start", "message": "正在连接巨潮资讯网并检索东南亚地区公告"})
        candidates, discover_stats = discover_candidates(
            client, start_date, end_date, markets=markets, regions=regions,
            page_size=page_size, pages_per_keyword=pages_per_keyword, progress=emit, cancel_event=cancel_event,
        )
        stats["query_count"] = len(discover_stats.get("query_stats", []))
        stats["query_stats"] = discover_stats.get("query_stats", [])
        stats["errors"].extend(discover_stats.get("errors", []))
        stats["errors_count"] = len(stats["errors"])
        if (cancel_event is not None and cancel_event.is_set()) or discover_stats.get("cancelled"):
            stats["cancelled"] = True
            safe_stats = dict(stats); safe_stats["results"] = []
            safe_stats["errors"] = [{k: v for k, v in e.items() if k in {"market", "keyword", "error"}} for e in stats["errors"]]
            finish_run(run_id, "已中断（本轮未保存公告）", safe_stats)
            emit({"event": "cancelled", "message": "用户中断采集；临时检索结果已丢弃，未写入公告和商机库", "finished": True})
            return stats

        eligible: list[Any] = []
        for candidate in candidates:
            if not _in_date_range(candidate.pub_date, start_date, end_date, include_unknown_dates):
                stats["outside_date_count"] += 1
                continue
            if not candidate.pub_date:
                stats["unknown_date_count"] += 1
            eligible.append(candidate)
            staged_updates[candidate.announcement_id] = {
                "candidate": candidate.to_dict(), "body_text": "", "analysis": None,
                "processing_status": "发现候选", "fetch_status": "本轮尚未解析正文",
            }
            # Keep one row per discovered announcement so the progress panel can show
            # all announcement metadata that will be saved, including candidates beyond
            # the current PDF parsing limit.
            result = {
                "status": "候选公告（正文待解析，临时）", "announcement_id": candidate.announcement_id,
                "stock_code": candidate.stock_code, "stock_name": candidate.stock_name,
                "title": candidate.title, "pub_date": candidate.pub_date,
                "project_name": candidate.title, "source_url": candidate.source_url,
                "fetch_status": "本轮尚未解析正文",
            }
            stats["results"].append(result)
            result_by_ann_id[candidate.announcement_id] = result
        stats["candidate_count"] = len(eligible)
        process_list = eligible[:max(1, max_pdfs)]
        stats["pending_count"] = max(0, len(eligible) - len(process_list))
        emit({"event": "discovered", "message": f"发现候选{len(eligible)}条；本轮正文解析上限{len(process_list)}条；待后续解析{stats['pending_count']}条；目前仅暂存，不会逐条写库"})

        for idx, candidate in enumerate(process_list, start=1):
            if cancel_event is not None and cancel_event.is_set():
                break
            emit({"event": "item_start", "message": f"正在解析 {idx}/{len(process_list)}：{candidate.stock_name}｜{candidate.title}", "item_index": idx, "item_total": len(process_list), "announcement_title": candidate.title})
            existing = get_announcement(candidate.announcement_id)
            body = (existing or {}).get("body_text", "")
            fetch_status = "复用本地已缓存正文" if body else ""
            if body:
                stats["cache_reused"] += 1
            else:
                stats["pdf_attempts"] += 1
                body, fetch_status = client.download_pdf_text(candidate)
                if body:
                    stats["pdf_success"] += 1
                else:
                    stats["errors_count"] += 1
                    stats["errors"].append({"announcement_id": candidate.announcement_id, "title": candidate.title, "error": fetch_status or "正文不可用"})
            if body:
                analysis = analyze_announcement(
                    title=candidate.title, text=body, stock_code=candidate.stock_code,
                    stock_name=candidate.stock_name, pub_date=candidate.pub_date,
                    source_url=candidate.source_url,
                )
                status = "已识别-待复核" if analysis["included_as_candidate"] else "已解析-未匹配商机"
                staged_updates[candidate.announcement_id] = {"candidate": candidate.to_dict(), "body_text": body, "analysis": analysis,
                    "processing_status": status, "fetch_status": fetch_status}
                stats["parsed_count"] += 1
                if analysis["included_as_candidate"]:
                    stats["identified_count"] += 1
                    staged_project_ids.append(candidate.announcement_id)
                    result = result_by_ann_id[candidate.announcement_id]
                    result.update({
                        "status": "候选商机（待最终保存）", "project_name": analysis["project_name"],
                        "countries": analysis["countries"], "location": analysis["location"],
                        "certainty": analysis["construction_certainty"], "grade": analysis["opportunity_grade"],
                        "fetch_status": fetch_status,
                    })
                else:
                    stats["not_candidates_count"] += 1
                    result = result_by_ann_id[candidate.announcement_id]
                    result.update({
                        "status": "未识别为商机（待最终保存）", "fetch_status": fetch_status,
                        "evidence": analysis.get("excluded_reason") or "未同时满足地域与投资/建设意图规则",
                    })
                emit({"event": "item_done", "message": f"正文解析完成：{candidate.title} → {'识别为候选商机' if analysis['included_as_candidate'] else '未达到候选条件'}（待本轮结束后统一保存）", "item_index": idx, "item_total": len(process_list), "result": result})
            else:
                staged_updates[candidate.announcement_id] = {"candidate": candidate.to_dict(), "body_text": "", "analysis": None,
                    "processing_status": "正文抓取失败-待复核", "fetch_status": fetch_status}
                result = result_by_ann_id[candidate.announcement_id]
                result.update({"status": "正文抓取失败（待最终保存）", "fetch_status": fetch_status})
                emit({"event": "item_error", "message": f"正文抓取失败：{candidate.title}（{fetch_status}）", "item_index": idx, "item_total": len(process_list), "result": result})
            # Give the hosting site a small pause between PDF pulls.
            time.sleep(0.08)

        if cancel_event is not None and cancel_event.is_set():
            stats["cancelled"] = True
            safe_stats = dict(stats); safe_stats["results"] = []
            safe_stats["errors"] = [{k: v for k, v in e.items() if k in {"market", "keyword", "error"}} for e in stats["errors"]]
            finish_run(run_id, "已中断（本轮未保存公告）", safe_stats)
            emit({"event": "cancelled", "message": "用户中断采集；本轮临时结果已全部丢弃，未写入公告和商机库", "finished": True})
            return stats

        # Final commit: cancellation is disabled by the background controller from here onward.
        total_commit = len(staged_updates) + len(staged_project_ids)
        completed_commit = 0
        emit({"event": "commit_start", "message": f"检索与解析完成，正在统一保存{len(staged_updates)}份公告和{len(staged_project_ids)}个商机档案；最终入库阶段暂不接受中断", "commit_index": 0, "commit_total": total_commit})
        for ann_id, row in staged_updates.items():
            was_new = upsert_announcement(row["candidate"], body_text=row["body_text"], analysis=row["analysis"],
                processing_status=row["processing_status"], fetch_status=row["fetch_status"])
            if was_new:
                stats["new_announcement_count"] += 1
            result = result_by_ann_id.get(ann_id)
            if result is not None:
                analysis = row.get("analysis") or {}
                if analysis.get("included_as_candidate"):
                    # The project-key/status is assigned just below after creating the dossier.
                    result["status"] = "候选商机（已保存公告）"
                elif analysis:
                    result["status"] = "公告已保存（未识别为商机）"
                elif row.get("fetch_status") == "本轮尚未解析正文":
                    result["status"] = "公告元数据已保存（正文待后续解析）"
                else:
                    result["status"] = "公告已保存（正文抓取失败）"
            completed_commit += 1
            if completed_commit % 5 == 0 or completed_commit == total_commit:
                emit({"event": "commit_progress", "message": f"最终入库中：已完成{completed_commit}/{total_commit}项写入", "commit_index": completed_commit, "commit_total": total_commit})
        for ann_id in staged_project_ids:
            row = staged_updates.get(ann_id) or {}
            analysis = row.get("analysis") or {}
            project_key, is_new_project = upsert_project(analysis, ann_id)
            if is_new_project:
                stats["new_project_count"] += 1
            result = result_by_ann_id.get(ann_id)
            if result is not None:
                result["project_key"] = project_key
                result["status"] = "新建商机档案" if is_new_project else "已存在同标题档案"
            completed_commit += 1
            if completed_commit % 5 == 0 or completed_commit == total_commit:
                emit({"event": "commit_progress", "message": f"最终入库中：已完成{completed_commit}/{total_commit}项写入", "commit_index": completed_commit, "commit_total": total_commit})

        status = "完成" if stats["errors_count"] == 0 else "完成（部分数据源/正文异常）"
        finish_run(run_id, status, stats)
        emit({"event": "finish", "message": _summary_message(stats), "finished": True})
        return stats
    except Exception as exc:
        stats["errors_count"] += 1
        stats["errors"].append({"error": f"采集流程异常：{type(exc).__name__}: {exc}"})
        finish_run(run_id, "失败", stats)
        emit({"event": "fatal", "message": f"采集未正常完成：{type(exc).__name__}: {exc}", "finished": True})
        raise


def _summary_message(stats: dict[str, Any]) -> str:
    if stats.get("candidate_count", 0) == 0:
        if stats.get("errors_count", 0):
            return "本轮未形成候选公告，且存在请求/解析异常；请查看数据源诊断，不应把0条直接理解为市场没有项目。"
        return "本轮没有找到候选公告。请检查日期范围、市场选择和巨潮接口诊断；0条不等于没有投资项目。"
    if stats.get("parsed_count", 0) == 0:
        return "发现了公告候选，但本轮正文均未成功解析；请查看PDF地址、网络状态和异常明细。"
    if stats.get("identified_count", 0) == 0:
        return f"已解析{stats['parsed_count']}份公告，但没有达到宽口径线索规则；请在“公告复核”查看已解析公告，调整规则后可重新分析。"
    return f"完成：已解析{stats['parsed_count']}份公告，识别候选线索{stats['identified_count']}条，新建项目档案{stats['new_project_count']}个，待后续解析{stats['pending_count']}条。"


def extract_pdf_text_bytes(pdf_bytes: bytes) -> tuple[str, str]:
    from io import BytesIO
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if not text.strip():
            return "", "PDF无可提取文本，可能是扫描件；当前版本未自动执行OCR"
        return text, f"文本提取成功（{len(reader.pages)}页）"
    except Exception as exc:
        return "", f"PDF解析失败：{type(exc).__name__}: {exc}"


def import_pdf_bytes(filename: str, pdf_bytes: bytes, pub_date: str = "") -> dict[str, Any]:
    text, parse_status = extract_pdf_text_bytes(pdf_bytes)
    if not text:
        raise ValueError(parse_status)
    flat = normalize_text(text)
    code_match = re.search(r"证券代码[：:\s]*([0-9]{6})", flat)
    stock_code = code_match.group(1) if code_match else ""
    short_match = re.search(r"证券简称[：:\s]*([^\s\n]+)", flat)
    stock_name = short_match.group(1).strip("，,；;") if short_match else ""
    if not stock_name:
        m = re.search(r"([\u4e00-\u9fff]{2,12}股份有限公司)", flat)
        if m:
            stock_name = re.sub(r"股份有限公司$", "", m.group(1))
    if not pub_date:
        # The disclosure date is commonly printed at the end near "特此公告"; use the last
        # date occurrence rather than the first meeting/financial-period date in the body.
        date_matches = list(re.finditer(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", flat))
        if date_matches:
            m = date_matches[-1]
            try: pub_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError: pub_date = ""
    title = re.sub(r"\.pdf$", "", Path(filename).name, flags=re.I)
    # Prefer announcement heading (commonly several lines after stock name).
    title_match = re.search(r"(?:关于.{2,70}?的公告|关于.{2,70}?项目的公告)", compact_for_regex(flat))
    if title_match:
        title = title_match.group(0)
    ann_id = "manual-" + hashlib.sha1(pdf_bytes).hexdigest()[:24]
    analysis = analyze_announcement(title, text, stock_code, stock_name, pub_date, f"本地导入：{Path(filename).name}")
    candidate = {
        "announcement_id": ann_id, "stock_code": stock_code, "stock_name": stock_name,
        "title": title, "pub_date": pub_date, "source_column": "本地PDF导入",
        "source_url": f"本地导入：{Path(filename).name}", "pdf_url": "", "search_keywords": "本地PDF导入",
        "raw_json": json.dumps({"filename": filename, "kind": "manual_pdf_import"}, ensure_ascii=False),
    }
    status = "已识别-待复核" if analysis["included_as_candidate"] else "已解析-未匹配商机"
    is_new_announcement = upsert_announcement(candidate, body_text=text, analysis=analysis,
                                               processing_status=status, fetch_status=parse_status, imported_from=filename)
    project_key = ""
    is_new_project = False
    if analysis["included_as_candidate"]:
        project_key, is_new_project = upsert_project(analysis, ann_id)
    return {"announcement_id": ann_id, "is_new_announcement": is_new_announcement,
            "is_candidate": analysis["included_as_candidate"], "is_new_project": is_new_project,
            "project_key": project_key, "analysis": analysis, "parse_status": parse_status,
            "body_text": text}


def compact_for_regex(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
