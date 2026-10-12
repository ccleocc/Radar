from __future__ import annotations

import json
import html
import re
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

import requests

from .config import CNINFO_QUERY_URL, CNINFO_PDF_BASE, CNINFO_COLUMNS, DEFAULTS, REGION_QUERIES

ProgressFn = Callable[[dict[str, Any]], None]


@dataclass
class AnnouncementCandidate:
    announcement_id: str
    stock_code: str
    stock_name: str
    title: str
    pub_date: str
    source_column: str
    pdf_url: str
    source_url: str
    raw_json: str
    search_keywords: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CNInfoClient:
    """Client for CNINFO's public announcement search interface.

    No login credentials are used. The endpoint is a public web interface and may
    change; errors are surfaced instead of being silently treated as zero results.
    """

    def __init__(self, timeout: int | None = None, delay: float | None = None):
        self.timeout = timeout or DEFAULTS["timeout_seconds"]
        self.delay = DEFAULTS["request_delay_seconds"] if delay is None else delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.cninfo.com.cn",
            "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "X-Requested-With": "XMLHttpRequest",
        })

    @staticmethod
    def _parse_pub_date(item: dict[str, Any]) -> str:
        for key in ("announcementTime", "announcementTimeString", "publishDate", "noticeDate"):
            value = item.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, (int, float)):
                # CNINFO commonly returns epoch milliseconds.
                timestamp = float(value)
                if timestamp > 10_000_000_000:
                    timestamp /= 1000
                try:
                    return datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai")).date().isoformat()
                except (ValueError, OSError, OverflowError):
                    continue
            value = str(value).strip()
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                try:
                    return datetime.strptime(value[:19], fmt).date().isoformat()
                except ValueError:
                    pass
            if len(value) >= 10 and value[:4].isdigit():
                return value[:10].replace("/", "-")
        return ""

    @staticmethod
    def _decode_response_json(response: Any) -> Any:
        """Decode CNINFO JSON explicitly as UTF-8 first to avoid mojibake caused by bad charset headers.

        Some HTTP responses omit or misstate their charset. Prefer UTF-8 (CNINFO's JSON encoding),
        then GB18030 only as a compatibility fallback. If no raw bytes are exposed by a mock or
        non-requests response, fall back to its JSON parser.
        """
        content = getattr(response, "content", None)
        if isinstance(content, bytes) and content:
            last_error: Exception | None = None
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    text = content.decode(encoding)
                    return json.loads(text)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    last_error = exc
            try:
                return response.json()
            except Exception as exc:
                raise ValueError(f"无法按 UTF-8/GB18030 解析巨潮 JSON：{last_error or exc}") from exc
        return response.json()

    @staticmethod
    def _extract_announcements(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
        if not isinstance(payload, dict):
            return [], None
        records = payload.get("announcements") or payload.get("data") or payload.get("results") or []
        total = payload.get("totalRecordNum") or payload.get("total") or payload.get("totalCount")
        try:
            total = int(total) if total is not None else None
        except (ValueError, TypeError):
            total = None
        if not isinstance(records, list):
            records = []
        return records, total

    @staticmethod
    def clean_title(value: Any) -> str:
        """Remove CNINFO highlight markup such as <em> and decode HTML entities."""
        text = html.unescape(str(value or ""))
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def query_params_for_market(market: str) -> tuple[str, str]:
        # CNINFO's public query uses szse + plate=bj for BSE; third/hke are
        # the commonly used columns for NEEQ and Hong Kong announcements.
        if market == "bj":
            return "szse", "bj"
        return market, ""

    def search(
        self,
        keyword: str,
        start_date: date,
        end_date: date,
        column: str,
        page_num: int = 1,
        page_size: int = 30,
    ) -> tuple[list[AnnouncementCandidate], int | None]:
        query_column, plate = self.query_params_for_market(column)
        data = {
            "pageNum": str(page_num),
            "pageSize": str(page_size),
            "tabName": "fulltext",
            "column": query_column,
            "plate": plate,
            "stock": "",
            "searchkey": keyword,
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": f"{start_date.strftime('%Y-%m-%d')}~{end_date.strftime('%Y-%m-%d')}",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        # Session-level headers include the required Referer/User-Agent/X-Requested-With.
        response = self.session.post(CNINFO_QUERY_URL, data=data, timeout=self.timeout)
        if response.status_code != 200:
            body_hint = str(getattr(response, "text", ""))[:120].replace("\n", " ")
            raise RuntimeError(f"CNINFO HTTP {response.status_code}，关键词：{keyword}，市场：{column}；响应片段：{body_hint}")
        try:
            payload = self._decode_response_json(response)
        except (json.JSONDecodeError, ValueError) as exc:
            sample = str(getattr(response, "text", ""))[:120].replace("\n", " ")
            raise RuntimeError(f"CNINFO 返回内容不是 JSON，关键词：{keyword}，响应片段：{sample}") from exc
        records, total = self._extract_announcements(payload)
        results: list[AnnouncementCandidate] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            title = self.clean_title(item.get("announcementTitle") or item.get("title") or "")
            code = str(item.get("secCode") or item.get("stockCode") or "").strip()
            name = self.clean_title(item.get("secName") or item.get("stockName") or "")
            pub_date = self._parse_pub_date(item)
            ann_id = str(item.get("announcementId") or item.get("announcementID") or "").strip()
            adjunct = str(item.get("adjunctUrl") or item.get("pdfUrl") or "").lstrip("/")
            if not ann_id:
                ann_id = f"{code}-{pub_date}-{title}"
            pdf_url = urljoin(CNINFO_PDF_BASE, adjunct) if adjunct else ""
            # Browser URL remains useful when a PDF link is not returned.
            source_url = pdf_url or f"https://www.cninfo.com.cn/new/fulltextSearch?keyWord={requests.utils.quote(keyword)}"
            # Store clean display fields in raw JSON as well; the untouched HTML
            # highlight tags (e.g. <em>) are not useful business attributes.
            sanitized_item = dict(item)
            for display_key in ("secName", "stockName", "announcementTitle", "title"):
                if display_key in sanitized_item:
                    sanitized_item[display_key] = self.clean_title(sanitized_item.get(display_key))
            results.append(AnnouncementCandidate(
                announcement_id=ann_id,
                stock_code=code,
                stock_name=name,
                title=title,
                pub_date=pub_date,
                source_column=column,
                pdf_url=pdf_url,
                source_url=source_url,
                raw_json=json.dumps(sanitized_item, ensure_ascii=False),
                search_keywords=keyword,
            ))
        if self.delay:
            time.sleep(self.delay)
        return results, total

    def download_pdf_text(self, candidate: AnnouncementCandidate, max_pdf_mb: int | None = None) -> tuple[str, str]:
        """Download/extract a CNINFO PDF. Returns (text, status)."""
        if not candidate.pdf_url:
            return "", "缺少PDF地址"
        max_bytes = int((max_pdf_mb or DEFAULTS["max_pdf_mb"]) * 1024 * 1024)
        response = self.session.get(candidate.pdf_url, timeout=self.timeout, stream=True)
        if response.status_code != 200:
            return "", f"PDF下载HTTP {response.status_code}"
        content_type = response.headers.get("Content-Type", "").lower()
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                response.close()
                return "", f"PDF超过{max_pdf_mb or DEFAULTS['max_pdf_mb']}MB上限"
            chunks.append(chunk)
        content = b"".join(chunks)
        if not content.startswith(b"%PDF") and "pdf" not in content_type:
            return "", "响应不是PDF文件，可能被反爬或链接已变化"
        try:
            from io import BytesIO
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if text.strip():
                return text, f"PDF文本提取成功（{len(reader.pages)}页）"
            return "", "PDF无可提取文本，可能为扫描件（当前未启用OCR）"
        except Exception as exc:  # Robustly report parsing errors per item.
            return "", f"PDF解析失败：{type(exc).__name__}: {exc}"

    def test_connection(self) -> dict[str, Any]:
        """Small low-cost health test against a recent-date query."""
        today = date.today()
        try:
            results, total = self.search("泰国", today, today, "szse", page_num=1, page_size=1)
            return {"ok": True, "message": "CNINFO接口返回正常", "records": len(results), "total": total}
        except Exception as exc:
            return {"ok": False, "message": str(exc), "records": 0, "total": None}


def is_a_share_code(code: str) -> bool:
    """Conservative prefix whitelist for Shanghai/Shenzhen/Beijing A shares."""
    code = (code or "").strip()
    if len(code) != 6 or not code.isdigit():
        return False
    prefixes = (
        "000", "001", "002", "003", "300", "301",  # Shenzhen
        "600", "601", "603", "605", "688", "689",  # Shanghai
        "430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839",
        "870", "871", "872", "873", "874", "875", "876", "920",  # Beijing
    )
    return code.startswith(prefixes)


def discover_candidates(
    client: CNInfoClient,
    start_date: date,
    end_date: date,
    markets: list[str] | None = None,
    regions: list[str] | None = None,
    page_size: int = 30,
    pages_per_keyword: int = 3,
    progress: ProgressFn | None = None,
    cancel_event: Any = None,
) -> tuple[list[AnnouncementCandidate], dict[str, Any]]:
    """Search selected disclosure markets and deduplicate announcements."""
    markets = markets or ["sse", "szse", "bj"]
    regions = regions or REGION_QUERIES
    all_candidates: dict[str, AnnouncementCandidate] = {}
    errors: list[dict[str, str]] = []
    query_stats: list[dict[str, Any]] = []
    query_total = max(1, len(markets) * len(regions))
    query_index = 0
    consecutive_failures = 0
    stop_due_to_connection = False
    cancelled = False
    for market in markets:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        for keyword in regions:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            query_index += 1
            if stop_due_to_connection:
                stat = {"market": market, "keyword": keyword, "pages": 0, "hits": 0, "total": None, "error": "因前序连续接口异常，为避免长时间等待，本项已跳过"}
                query_stats.append(stat)
                if progress:
                    progress({"event": "query_done", "message": f"跳过 {market} / {keyword}：接口连续异常", "query_index": query_index, "query_total": query_total, "candidate_count": len(all_candidates), "stat": stat})
                continue
            stat = {"market": market, "keyword": keyword, "pages": 0, "hits": 0, "total": None, "error": ""}
            for page in range(1, max(1, pages_per_keyword) + 1):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                if progress:
                    progress({"event": "query", "message": f"检索{market}：{keyword}（第{page}页）", "query_index": query_index, "query_total": query_total, "candidate_count": len(all_candidates), "keyword": keyword, "market": market, "page_num": page, "page_total": max(1, pages_per_keyword), "keyword_remaining": max(0, query_total - query_index)})
                try:
                    candidates, total = client.search(keyword, start_date, end_date, market, page_num=page, page_size=page_size)
                    consecutive_failures = 0
                    stat["pages"] = page
                    stat["hits"] += len(candidates)
                    if total is not None:
                        stat["total"] = total
                    for candidate in candidates:
                        # Restrict only the mainland A-share venues to A-share-looking codes.
                        # NEEQ and Hong Kong symbols follow different code conventions.
                        code = candidate.stock_code
                        if market in {"sse", "szse", "bj"} and not is_a_share_code(code):
                            continue
                        key = candidate.announcement_id or f"{candidate.stock_code}|{candidate.pub_date}|{candidate.title}"
                        if key in all_candidates:
                            old = all_candidates[key]
                            old.search_keywords = ",".join(sorted(set((old.search_keywords + "," + keyword).strip(",").split(","))))
                        else:
                            all_candidates[key] = candidate
                    if not candidates or (total is not None and page * page_size >= total):
                        break
                except Exception as exc:
                    stat["error"] = str(exc)
                    errors.append({"market": market, "keyword": keyword, "error": str(exc)})
                    consecutive_failures += 1
                    # Continue through occasional per-query errors, but stop after repeated connection/API failures.
                    if consecutive_failures >= 3:
                        stop_due_to_connection = True
                    break
                if cancelled:
                    break
            query_stats.append(stat)
            if progress:
                configured_page_cap = max(1, pages_per_keyword)
                if stat.get("total") is not None and page_size > 0:
                    actual_page_count = max(1, (int(stat["total"]) + page_size - 1) // page_size)
                    page_total = min(configured_page_cap, actual_page_count)
                else:
                    page_total = configured_page_cap
                progress({
                    "event": "query_done",
                    "message": f"检索完成：{market} / {keyword}，命中{stat['hits']}条",
                    "query_index": query_index, "query_total": query_total,
                    "candidate_count": len(all_candidates), "keyword": keyword,
                    "market": market, "page_num": max(1, int(stat.get("pages") or 1)),
                    "page_total": page_total, "keyword_remaining": max(0, query_total - query_index),
                    "stat": stat,
                })
            if cancelled:
                break
        if cancelled:
            break
    candidates = list(all_candidates.values())
    # Prioritize likely project announcements without hard-excluding generic titles; full text is decisive.
    priority_terms = ["对外投资", "境外投资", "海外投资", "投资项目", "投资建设", "生产基地", "智能制造", "新建", "扩建", "工厂", "项目建设", "增资", "项目投资", "产能", "厂房", "工业园", "产业园"]
    candidates.sort(key=lambda x: (sum(term in x.title for term in priority_terms), x.pub_date), reverse=True)
    stats = {
        "query_count": query_total,
        "unique_candidates": len(candidates),
        "query_stats": query_stats,
        "errors": errors,
        "markets": markets,
        "regions": regions,
        "candidate_by_market": {m: sum(1 for c in candidates if c.source_column == m) for m in markets},
        "cancelled": cancelled or bool(cancel_event is not None and cancel_event.is_set()),
    }
    return candidates, stats
