from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DB_PATH, DATA_DIR
from .analyzer import make_project_key, clean_announcement_title


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS announcements (
            announcement_id TEXT PRIMARY KEY,
            stock_code TEXT NOT NULL DEFAULT '',
            stock_name TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            pub_date TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            pdf_url TEXT NOT NULL DEFAULT '',
            search_keywords TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            body_text TEXT NOT NULL DEFAULT '',
            analysis_json TEXT NOT NULL DEFAULT '{}',
            processing_status TEXT NOT NULL DEFAULT '待处理',
            fetch_status TEXT NOT NULL DEFAULT '',
            imported_from TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL DEFAULT '',
            last_seen TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_ann_pub_date ON announcements(pub_date);
        CREATE INDEX IF NOT EXISTS idx_ann_stock_code ON announcements(stock_code);
        CREATE INDEX IF NOT EXISTS idx_ann_status ON announcements(processing_status);

        CREATE TABLE IF NOT EXISTS projects (
            project_key TEXT PRIMARY KEY,
            stock_code TEXT NOT NULL DEFAULT '',
            stock_name TEXT NOT NULL DEFAULT '',
            project_name TEXT NOT NULL DEFAULT '',
            countries_json TEXT NOT NULL DEFAULT '[]',
            location TEXT NOT NULL DEFAULT '',
            investment_subject TEXT NOT NULL DEFAULT '',
            partner TEXT NOT NULL DEFAULT '',
            project_type TEXT NOT NULL DEFAULT '',
            investment_amount TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT '',
            project_total_investment TEXT NOT NULL DEFAULT '',
            listed_company_contribution TEXT NOT NULL DEFAULT '',
            amount_evidence TEXT NOT NULL DEFAULT '',
            amount_candidates_json TEXT NOT NULL DEFAULT '[]',
            construction_scope TEXT NOT NULL DEFAULT '',
            project_stage TEXT NOT NULL DEFAULT '',
            expected_start TEXT NOT NULL DEFAULT '',
            construction_period TEXT NOT NULL DEFAULT '',
            construction_certainty TEXT NOT NULL DEFAULT '低',
            certainty_score INTEGER NOT NULL DEFAULT 0,
            certainty_basis TEXT NOT NULL DEFAULT '',
            opportunity_grade TEXT NOT NULL DEFAULT '★ 观察',
            opportunity_basis TEXT NOT NULL DEFAULT '',
            opportunity_score INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            evidence TEXT NOT NULL DEFAULT '',
            hit_signals_json TEXT NOT NULL DEFAULT '{}',
            latest_announcement_date TEXT NOT NULL DEFAULT '',
            latest_title TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            announcement_count INTEGER NOT NULL DEFAULT 0,
            last_updated TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_project_country ON projects(countries_json);
        CREATE INDEX IF NOT EXISTS idx_project_date ON projects(latest_announcement_date);
        CREATE INDEX IF NOT EXISTS idx_project_stock ON projects(stock_code);

        CREATE TABLE IF NOT EXISTS project_announcements (
            project_key TEXT NOT NULL,
            announcement_id TEXT NOT NULL,
            linked_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (project_key, announcement_id),
            FOREIGN KEY(project_key) REFERENCES projects(project_key) ON DELETE CASCADE,
            FOREIGN KEY(announcement_id) REFERENCES announcements(announcement_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS collection_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            start_date TEXT NOT NULL DEFAULT '',
            end_date TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '运行中',
            market_count INTEGER NOT NULL DEFAULT 0,
            query_count INTEGER NOT NULL DEFAULT 0,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            pdf_attempts INTEGER NOT NULL DEFAULT 0,
            pdf_success INTEGER NOT NULL DEFAULT 0,
            parsed_count INTEGER NOT NULL DEFAULT 0,
            identified_count INTEGER NOT NULL DEFAULT 0,
            new_project_count INTEGER NOT NULL DEFAULT 0,
            errors_count INTEGER NOT NULL DEFAULT 0,
            details_json TEXT NOT NULL DEFAULT '{}'
        );
        """)
        # Forward-compatible schema migration for databases created by earlier versions.
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
        for column, definition in [
            ("project_total_investment", "TEXT NOT NULL DEFAULT ''"),
            ("listed_company_contribution", "TEXT NOT NULL DEFAULT ''"),
            ("amount_evidence", "TEXT NOT NULL DEFAULT ''"),
            ("amount_candidates_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("opportunity_basis", "TEXT NOT NULL DEFAULT ''"),
        ]:
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {definition}")


def upsert_announcement(candidate: dict[str, Any], body_text: str = "", analysis: dict[str, Any] | None = None,
                        processing_status: str = "待处理", fetch_status: str = "", imported_from: str = "") -> bool:
    ann_id = str(candidate.get("announcement_id") or "").strip()
    if not ann_id:
        raise ValueError("公告缺少announcement_id")
    candidate = dict(candidate)
    # Clean official display attributes before storing. Older records are cleaned
    # again at read time below, so existing databases are also safe to display.
    for display_field in ("stock_name", "title"):
        candidate[display_field] = clean_announcement_title(candidate.get(display_field))
    raw_json = candidate.get("raw_json") or "{}"
    if isinstance(raw_json, str):
        try:
            raw_obj = json.loads(raw_json)
        except json.JSONDecodeError:
            raw_obj = None
        if isinstance(raw_obj, dict):
            for field in ("secName", "stockName", "announcementTitle", "title"):
                if field in raw_obj:
                    raw_obj[field] = clean_announcement_title(raw_obj.get(field))
            raw_json = json.dumps(raw_obj, ensure_ascii=False)
    if not isinstance(raw_json, str):
        raw_json = json.dumps(raw_json, ensure_ascii=False)
    clean_analysis = dict(analysis or {})
    for display_field in (
        "stock_name", "title", "project_name", "latest_title", "investment_subject", "partner",
        "location", "project_type", "investment_amount", "currency", "project_total_investment",
        "listed_company_contribution", "amount_evidence", "construction_scope", "project_stage",
    ):
        if display_field in clean_analysis:
            clean_analysis[display_field] = clean_announcement_title(clean_analysis.get(display_field))
    analysis_json = json.dumps(clean_analysis, ensure_ascii=False)
    now = now_iso()
    with connect() as conn:
        existing = conn.execute("SELECT announcement_id, body_text FROM announcements WHERE announcement_id=?", (ann_id,)).fetchone()
        is_new = existing is None
        old_text = existing["body_text"] if existing else ""
        final_text = body_text if body_text else old_text
        conn.execute("""
            INSERT INTO announcements(
                announcement_id, stock_code, stock_name, title, pub_date, market, source_url,
                pdf_url, search_keywords, raw_json, body_text, analysis_json, processing_status,
                fetch_status, imported_from, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(announcement_id) DO UPDATE SET
                stock_code=CASE WHEN excluded.stock_code<>'' THEN excluded.stock_code ELSE announcements.stock_code END,
                stock_name=CASE WHEN excluded.stock_name<>'' THEN excluded.stock_name ELSE announcements.stock_name END,
                title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE announcements.title END,
                pub_date=CASE WHEN excluded.pub_date<>'' THEN excluded.pub_date ELSE announcements.pub_date END,
                market=CASE WHEN excluded.market<>'' THEN excluded.market ELSE announcements.market END,
                source_url=CASE WHEN excluded.source_url<>'' THEN excluded.source_url ELSE announcements.source_url END,
                pdf_url=CASE WHEN excluded.pdf_url<>'' THEN excluded.pdf_url ELSE announcements.pdf_url END,
                search_keywords=CASE WHEN excluded.search_keywords<>'' THEN excluded.search_keywords ELSE announcements.search_keywords END,
                raw_json=CASE WHEN excluded.raw_json<>'{}' THEN excluded.raw_json ELSE announcements.raw_json END,
                body_text=CASE WHEN excluded.body_text<>'' THEN excluded.body_text ELSE announcements.body_text END,
                analysis_json=CASE WHEN excluded.analysis_json<>'{}' THEN excluded.analysis_json ELSE announcements.analysis_json END,
                processing_status=excluded.processing_status,
                fetch_status=CASE WHEN excluded.fetch_status<>'' THEN excluded.fetch_status ELSE announcements.fetch_status END,
                imported_from=CASE WHEN excluded.imported_from<>'' THEN excluded.imported_from ELSE announcements.imported_from END,
                last_seen=excluded.last_seen
        """, (
            ann_id, str(candidate.get("stock_code") or ""), str(candidate.get("stock_name") or ""),
            str(candidate.get("title") or ""), str(candidate.get("pub_date") or ""),
            str(candidate.get("source_column") or candidate.get("market") or ""),
            str(candidate.get("source_url") or ""), str(candidate.get("pdf_url") or ""),
            str(candidate.get("search_keywords") or ""), raw_json, final_text, analysis_json,
            processing_status, fetch_status, imported_from, now, now,
        ))
        return is_new


def upsert_project(analysis: dict[str, Any], announcement_id: str) -> tuple[str, bool]:
    analysis = dict(analysis or {})
    for display_field in (
        "stock_name", "project_name", "title", "latest_title", "investment_subject",
        "partner", "location", "project_type", "investment_amount", "currency",
        "project_total_investment", "listed_company_contribution", "amount_evidence",
        "construction_scope", "project_stage", "expected_start", "construction_period",
        "construction_certainty", "certainty_basis", "opportunity_grade", "opportunity_basis", "evidence",
    ):
        if display_field in analysis:
            analysis[display_field] = clean_announcement_title(analysis.get(display_field))
    key = make_project_key(analysis)
    now = now_iso()
    countries_json = json.dumps(analysis.get("countries") or [], ensure_ascii=False)
    hit_signals_json = json.dumps(analysis.get("hit_signals") or {}, ensure_ascii=False)
    with connect() as conn:
        existing = conn.execute("SELECT * FROM projects WHERE project_key=?", (key,)).fetchone()
        is_new = existing is None
        incoming_date = str(analysis.get("pub_date") or "")
        old_date = str(existing["latest_announcement_date"] or "") if existing else ""
        incoming_is_latest = (incoming_date or "") >= (old_date or "")
        if existing:
            # Latest announcement controls mutable status fields; never overwrite a better value with blanks.
            fields = dict(analysis)
            if not incoming_is_latest:
                for fld in ["project_stage", "expected_start", "construction_period", "construction_scope", "investment_amount", "currency", "project_total_investment", "listed_company_contribution", "amount_evidence", "location", "partner", "certainty_basis", "opportunity_basis", "evidence", "title", "source_url", "pub_date"]:
                    fields[fld] = existing[{
                        "project_stage": "project_stage", "expected_start": "expected_start", "construction_period": "construction_period",
                        "construction_scope": "construction_scope", "investment_amount": "investment_amount", "currency": "currency",
                        "project_total_investment": "project_total_investment", "listed_company_contribution": "listed_company_contribution", "amount_evidence": "amount_evidence",
                        "location": "location", "partner": "partner", "certainty_basis": "certainty_basis", "opportunity_basis": "opportunity_basis", "evidence": "evidence",
                        "title": "latest_title", "source_url": "source_url", "pub_date": "latest_announcement_date",
                    }[fld]]
                for fld in ["certainty_score", "construction_certainty", "opportunity_score", "opportunity_grade", "confidence"]:
                    fields[fld] = existing[fld]
        else:
            fields = dict(analysis)
        # Preserve prior non-empty fields when later announcements are incomplete.
        if existing:
            for fld in ["project_name", "investment_subject", "project_type", "location", "investment_amount", "currency", "project_total_investment", "listed_company_contribution", "amount_evidence", "construction_scope", "project_stage", "expected_start", "construction_period", "certainty_basis", "opportunity_basis", "evidence", "partner"]:
                if not fields.get(fld):
                    fields[fld] = existing[fld]

        conn.execute("""
            INSERT INTO projects(
                project_key, stock_code, stock_name, project_name, countries_json, location, investment_subject,
                partner, project_type, investment_amount, currency, project_total_investment, listed_company_contribution, amount_evidence, amount_candidates_json, construction_scope, project_stage,
                expected_start, construction_period, construction_certainty, certainty_score, certainty_basis,
                opportunity_grade, opportunity_basis, opportunity_score, confidence, evidence, hit_signals_json,
                latest_announcement_date, latest_title, source_url, announcement_count, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(project_key) DO UPDATE SET
                stock_name=CASE WHEN excluded.stock_name<>'' THEN excluded.stock_name ELSE projects.stock_name END,
                project_name=CASE WHEN excluded.project_name<>'' THEN excluded.project_name ELSE projects.project_name END,
                countries_json=CASE WHEN excluded.countries_json<>'[]' THEN excluded.countries_json ELSE projects.countries_json END,
                location=CASE WHEN excluded.location<>'' THEN excluded.location ELSE projects.location END,
                investment_subject=CASE WHEN excluded.investment_subject<>'' THEN excluded.investment_subject ELSE projects.investment_subject END,
                partner=CASE WHEN excluded.partner<>'公告未明确' AND excluded.partner<>'' THEN excluded.partner ELSE projects.partner END,
                project_type=CASE WHEN excluded.project_type<>'' THEN excluded.project_type ELSE projects.project_type END,
                investment_amount=CASE WHEN excluded.investment_amount<>'' THEN excluded.investment_amount ELSE projects.investment_amount END,
                currency=CASE WHEN excluded.currency<>'' THEN excluded.currency ELSE projects.currency END,
                project_total_investment=CASE WHEN excluded.project_total_investment<>'' THEN excluded.project_total_investment ELSE projects.project_total_investment END,
                listed_company_contribution=CASE WHEN excluded.listed_company_contribution<>'' THEN excluded.listed_company_contribution ELSE projects.listed_company_contribution END,
                amount_evidence=CASE WHEN excluded.amount_evidence<>'' THEN excluded.amount_evidence ELSE projects.amount_evidence END,
                amount_candidates_json=CASE WHEN excluded.amount_candidates_json<>'[]' THEN excluded.amount_candidates_json ELSE projects.amount_candidates_json END,
                construction_scope=CASE WHEN excluded.construction_scope<>'' AND excluded.construction_scope<>'公告尚未明确具体工程范围' THEN excluded.construction_scope ELSE projects.construction_scope END,
                project_stage=CASE WHEN excluded.project_stage<>'' THEN excluded.project_stage ELSE projects.project_stage END,
                expected_start=CASE WHEN excluded.expected_start<>'' THEN excluded.expected_start ELSE projects.expected_start END,
                construction_period=CASE WHEN excluded.construction_period<>'' THEN excluded.construction_period ELSE projects.construction_period END,
                construction_certainty=excluded.construction_certainty,
                certainty_score=excluded.certainty_score,
                certainty_basis=CASE WHEN excluded.certainty_basis<>'' THEN excluded.certainty_basis ELSE projects.certainty_basis END,
                opportunity_grade=excluded.opportunity_grade,
                opportunity_basis=CASE WHEN excluded.opportunity_basis<>'' THEN excluded.opportunity_basis ELSE projects.opportunity_basis END,
                opportunity_score=excluded.opportunity_score,
                confidence=excluded.confidence,
                evidence=CASE WHEN excluded.evidence<>'' THEN excluded.evidence ELSE projects.evidence END,
                hit_signals_json=CASE WHEN excluded.hit_signals_json<>'{}' THEN excluded.hit_signals_json ELSE projects.hit_signals_json END,
                latest_announcement_date=CASE WHEN excluded.latest_announcement_date>=projects.latest_announcement_date THEN excluded.latest_announcement_date ELSE projects.latest_announcement_date END,
                latest_title=CASE WHEN excluded.latest_announcement_date>=projects.latest_announcement_date THEN excluded.latest_title ELSE projects.latest_title END,
                source_url=CASE WHEN excluded.latest_announcement_date>=projects.latest_announcement_date THEN excluded.source_url ELSE projects.source_url END,
                last_updated=excluded.last_updated
        """, (
            key, str(fields.get("stock_code") or ""), str(fields.get("stock_name") or ""),
            str(fields.get("project_name") or "待核实项目名称"), countries_json, str(fields.get("location") or ""),
            str(fields.get("investment_subject") or ""), str(fields.get("partner") or "公告未明确"),
            str(fields.get("project_type") or ""), str(fields.get("investment_amount") or ""),
            str(fields.get("currency") or ""), str(fields.get("project_total_investment") or ""),
            str(fields.get("listed_company_contribution") or ""), str(fields.get("amount_evidence") or ""),
            json.dumps(fields.get("amount_candidates") or [], ensure_ascii=False),
            str(fields.get("construction_scope") or ""),
            str(fields.get("project_stage") or ""), str(fields.get("expected_start") or ""),
            str(fields.get("construction_period") or ""), str(fields.get("construction_certainty") or "低"),
            int(fields.get("certainty_score") or 0), str(fields.get("certainty_basis") or ""),
            str(fields.get("opportunity_grade") or "★ 观察"), str(fields.get("opportunity_basis") or ""),
            int(fields.get("opportunity_score") or 0), float(fields.get("confidence") or 0),
            str(fields.get("evidence") or ""), hit_signals_json,
            str(fields.get("pub_date") or ""), str(fields.get("title") or ""), str(fields.get("source_url") or ""), now,
        ))
        conn.execute("INSERT OR IGNORE INTO project_announcements(project_key, announcement_id, linked_at) VALUES (?, ?, ?)", (key, announcement_id, now))
        count = conn.execute("SELECT COUNT(*) FROM project_announcements WHERE project_key=?", (key,)).fetchone()[0]
        conn.execute("UPDATE projects SET announcement_count=? WHERE project_key=?", (count, key))
    return key, is_new


def delete_project(project_key: str) -> bool:
    """Delete only the selected business dossier; keep original announcement records for review."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM projects WHERE project_key=?", (project_key,))
        return cur.rowcount > 0


def unlink_announcement_projects(announcement_id: str) -> int:
    """Detach an announcement from old project dossiers during rule-based reanalysis.

    Projects with no remaining announcement links are removed; linked announcement records
    themselves are retained. Returns the number of orphaned project dossiers removed.
    """
    removed = 0
    with connect() as conn:
        old_keys = [r[0] for r in conn.execute(
            "SELECT project_key FROM project_announcements WHERE announcement_id=?",
            (str(announcement_id),),
        ).fetchall()]
        conn.execute("DELETE FROM project_announcements WHERE announcement_id=?", (str(announcement_id),))
        for project_key in old_keys:
            count = int(conn.execute(
                "SELECT COUNT(*) FROM project_announcements WHERE project_key=?", (project_key,)
            ).fetchone()[0])
            if count == 0:
                cur = conn.execute("DELETE FROM projects WHERE project_key=?", (project_key,))
                removed += int(cur.rowcount > 0)
            else:
                conn.execute("UPDATE projects SET announcement_count=? WHERE project_key=?", (count, project_key))
    return removed


def start_run(start_date: str, end_date: str, market_count: int) -> int:
    with connect() as conn:
        cur = conn.execute("INSERT INTO collection_runs(started_at,start_date,end_date,market_count,status) VALUES(?,?,?,?,?)",
                           (now_iso(), start_date, end_date, market_count, "运行中"))
        return int(cur.lastrowid)


def finish_run(run_id: int, status: str, stats: dict[str, Any]) -> None:
    details = json.dumps(stats, ensure_ascii=False, default=str)
    with connect() as conn:
        conn.execute("""UPDATE collection_runs SET finished_at=?, status=?, query_count=?, candidate_count=?, pdf_attempts=?, pdf_success=?, parsed_count=?, identified_count=?, new_project_count=?, errors_count=?, details_json=? WHERE run_id=?""",
                     (now_iso(), status, int(stats.get("query_count", 0)), int(stats.get("candidate_count", 0)),
                      int(stats.get("pdf_attempts", 0)), int(stats.get("pdf_success", 0)), int(stats.get("parsed_count", 0)),
                      int(stats.get("identified_count", 0)), int(stats.get("new_project_count", 0)), int(stats.get("errors_count", 0)), details, run_id))


def fetch_projects() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY CASE opportunity_grade WHEN '★★★ 高' THEN 3 WHEN '★★ 中' THEN 2 ELSE 1 END DESC, latest_announcement_date DESC").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["countries"] = json.loads(item.pop("countries_json") or "[]")
            item["hit_signals"] = json.loads(item.pop("hit_signals_json") or "{}")
            try:
                item["amount_candidates"] = json.loads(item.pop("amount_candidates_json", "[]") or "[]")
            except json.JSONDecodeError:
                item["amount_candidates"] = []
            # Clean historical HTML highlight markup across visible attributes,
            # not only announcement titles. This fixes already-stored <em> names.
            for field in (
                "stock_name", "latest_title", "project_name", "location", "investment_subject",
                "partner", "project_type", "investment_amount", "currency", "project_total_investment",
                "listed_company_contribution", "amount_evidence", "construction_scope", "project_stage",
                "expected_start", "construction_period", "construction_certainty", "certainty_basis",
                "opportunity_grade", "opportunity_basis", "evidence",
            ):
                item[field] = clean_announcement_title(item.get(field))
            # Backward-compatible display: the official latest announcement title wins.
            item["project_name"] = clean_announcement_title(item.get("latest_title") or item.get("project_name") or "公告标题待核实")
            result.append(item)
        return result


def fetch_announcements(status: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if status:
            rows = conn.execute("SELECT * FROM announcements WHERE processing_status=? ORDER BY pub_date DESC, last_seen DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM announcements ORDER BY pub_date DESC, last_seen DESC").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["stock_name"] = clean_announcement_title(item.get("stock_name"))
            item["title"] = clean_announcement_title(item.get("title"))
            for col in ("analysis_json", "raw_json"):
                try: item[col + "_parsed"] = json.loads(item.get(col) or "{}")
                except json.JSONDecodeError: item[col + "_parsed"] = {}
            # Normalize company/title fields in parsed JSON too, including old rows.
            raw_parsed = item.get("raw_json_parsed")
            if isinstance(raw_parsed, dict):
                for field in ("secName", "stockName", "announcementTitle", "title"):
                    if field in raw_parsed:
                        raw_parsed[field] = clean_announcement_title(raw_parsed.get(field))
                item["raw_json"] = json.dumps(raw_parsed, ensure_ascii=False)
            parsed_analysis = item.get("analysis_json_parsed")
            if isinstance(parsed_analysis, dict):
                for field in ("stock_name", "title", "project_name", "location", "investment_subject", "partner", "project_type"):
                    if field in parsed_analysis:
                        parsed_analysis[field] = clean_announcement_title(parsed_analysis.get(field))
                item["analysis_json"] = json.dumps(parsed_analysis, ensure_ascii=False)
            # Also normalize the display value for historical rows created before
            # V2.1, when project_name was inferred from body-text regexes.
            parsed_analysis = item.get("analysis_json_parsed")
            if isinstance(parsed_analysis, dict) and item.get("title"):
                item["title"] = clean_announcement_title(item.get("title"))
                parsed_analysis["project_name"] = item["title"]
            result.append(item)
        return result


def fetch_runs(limit: int = 30) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM collection_runs ORDER BY run_id DESC LIMIT ?", (limit,)).fetchall()]


def project_announcements(project_key: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("""
            SELECT a.* FROM announcements a JOIN project_announcements pa ON a.announcement_id=pa.announcement_id
            WHERE pa.project_key=? ORDER BY a.pub_date DESC
        """, (project_key,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for field in ("stock_name", "title"):
                item[field] = clean_announcement_title(item.get(field))
            result.append(item)
        return result


def counts() -> dict[str, int]:
    with connect() as conn:
        ann_count = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        pending_count = conn.execute("SELECT COUNT(*) FROM announcements WHERE processing_status LIKE '%待复核%' OR processing_status LIKE '%失败%'").fetchone()[0]
        latest_count = conn.execute("SELECT COUNT(*) FROM projects WHERE latest_announcement_date=?", (datetime.now().date().isoformat(),)).fetchone()[0]
        return {"announcements": ann_count, "projects": project_count, "pending": pending_count, "latest": latest_count}


def get_announcement(announcement_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM announcements WHERE announcement_id=?", (announcement_id,)).fetchone()
        return dict(row) if row else None
