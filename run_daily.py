from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from radar.config import DEFAULTS
from radar.settings import load_active_keywords
from radar.db import init_db
from radar.pipeline import collect_official_announcements


def main() -> int:
    # Keep CLI output deterministic; on Windows, UTF-8 mode is enabled by the launcher
    # and normal Python 3 console streams already handle Unicode text.
    parser = argparse.ArgumentParser(description="中国上市公司东南亚投资建厂公告雷达：命令行定时采集")
    parser.add_argument("--days", type=int, default=7, help="回溯天数，默认7天")
    parser.add_argument("--start-date", type=str, default="", help="起始披露日期 YYYY-MM-DD（优先于--days）")
    parser.add_argument("--end-date", type=str, default="", help="结束披露日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--markets", nargs="+", choices=["sse", "szse", "bj", "third", "hke"], default=["sse", "szse", "bj", "third", "hke"], help="市场代码：sse / szse / bj / third / hke")
    parser.add_argument("--regions", "--keywords", dest="regions", nargs="+", default=load_active_keywords(), help="全文检索词，可按需传入一个或多个；默认读取界面当前启用的关键词")
    parser.add_argument("--pages", type=int, default=1, help="每个检索词每个市场的最大翻页数，默认1")
    parser.add_argument("--max-pdfs", type=int, default=120, help="本轮最多下载解析的PDF数")
    args = parser.parse_args()

    end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()
    start_date = date.fromisoformat(args.start_date) if args.start_date else end_date - timedelta(days=max(1, args.days) - 1)
    if start_date > end_date:
        parser.error("--start-date 不能晚于 --end-date")

    init_db()

    def progress(event: dict) -> None:
        print(f"[{event.get('event','')}] {event.get('message','')}", flush=True)

    stats = collect_official_announcements(
        start_date=start_date,
        end_date=end_date,
        markets=args.markets,
        regions=args.regions,
        page_size=DEFAULTS["page_size"],
        pages_per_keyword=args.pages,
        max_pdfs=args.max_pdfs,
        progress=progress,
    )
    print("\n=== 本轮汇总 ===")
    for key in ["run_id", "query_count", "candidate_count", "pdf_attempts", "pdf_success", "parsed_count", "identified_count", "new_project_count", "errors_count", "pending_count"]:
        print(f"{key}: {stats.get(key, 0)}")
    if stats.get("errors"):
        print("\n=== 异常明细 ===")
        for err in stats["errors"][:30]:
            print(err)
    return 0 if stats.get("errors_count", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
