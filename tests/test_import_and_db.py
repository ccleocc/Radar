from pathlib import Path

from radar import db
from radar.pipeline import import_pdf_bytes

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "志邦家居_新建泰国智能制造基地项目_公告样例.pdf"


def test_import_sample_pdf_into_temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db(tmp_path / "test.db")
    result = import_pdf_bytes(SAMPLE.name, SAMPLE.read_bytes())
    assert result["is_candidate"] is True
    assert result["is_new_project"] is True
    assert db.counts()["projects"] == 1
    projects = db.fetch_projects()
    assert projects[0]["project_name"] == "关于向全资子公司、孙公司增资并对外投资项目的公告"
    assert projects[0]["stock_code"] == "603801"


def test_pipeline_collection_with_fake_cninfo_client(monkeypatch, tmp_path):
    from datetime import date
    from radar.cninfo import AnnouncementCandidate
    from radar.pipeline import collect_official_announcements
    from pypdf import PdfReader

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "pipeline.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db(tmp_path / "pipeline.db")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(SAMPLE).pages)

    class FakeClient:
        def search(self, keyword, start_date, end_date, column, page_num=1, page_size=30):
            if keyword != "泰国" or column != "sse" or page_num > 1:
                return [], 0
            return [AnnouncementCandidate(
                announcement_id="fake-ann-001", stock_code="603801", stock_name="志邦家居",
                title="关于向全资子公司、孙公司增资并对外投资项目的公告", pub_date="2026-10-08",
                source_column="sse", pdf_url="https://static.cninfo.com.cn/finalpage/test.pdf",
                source_url="https://static.cninfo.com.cn/finalpage/test.pdf", raw_json="{}", search_keywords="泰国",
            )], 1

        def download_pdf_text(self, candidate, max_pdf_mb=None):
            return text, "Mock PDF文本提取成功"

    events = []
    stats = collect_official_announcements(
        start_date=date(2026, 10, 1), end_date=date(2026, 10, 10),
        markets=["sse"], regions=["泰国"], pages_per_keyword=1, max_pdfs=10,
        progress=events.append, client=FakeClient(),
    )
    assert stats["candidate_count"] == 1
    assert stats["parsed_count"] == 1
    assert stats["identified_count"] == 1
    assert stats["new_project_count"] == 1
    assert any(e.get("event") == "finish" for e in events)
    assert db.counts()["projects"] == 1


def test_announcement_review_displays_title_instead_of_old_regex_name(monkeypatch, tmp_path):
    from radar import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "review.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db(tmp_path / "review.db")
    db.upsert_announcement(
        {"announcement_id": "legacy-ann", "stock_code": "603801", "stock_name": "志邦家居",
         "title": "关于向全资子公司、孙公司增资并对外投资项目的公告", "pub_date": "2026-10-08",
         "source_column": "sse", "source_url": "https://example.invalid/a.pdf", "raw_json": "{}"},
        body_text="泰国建设厂房",
        analysis={"project_name": "旧版正则提取的新建泰国智能制造基地项目"},
        processing_status="已识别-待复核",
    )
    rows = db.fetch_announcements()
    assert rows[0]["analysis_json_parsed"]["project_name"] == rows[0]["title"]


def test_delete_project_dossier_keeps_announcement(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "delete.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db(tmp_path / "delete.db")
    import_pdf_bytes(SAMPLE.name, SAMPLE.read_bytes())
    project = db.fetch_projects()[0]
    assert db.delete_project(project["project_key"]) is True
    assert db.counts()["projects"] == 0
    assert db.counts()["announcements"] == 1


def test_interrupted_pipeline_does_not_save_current_batch(monkeypatch, tmp_path):
    from datetime import date
    import threading
    from pypdf import PdfReader
    from radar.cninfo import AnnouncementCandidate
    from radar.pipeline import collect_official_announcements

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cancel.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db(tmp_path / "cancel.db")
    text = "\\n".join(page.extract_text() or "" for page in PdfReader(SAMPLE).pages)
    cancel_event = threading.Event()

    class CancelClient:
        def search(self, keyword, start_date, end_date, column, page_num=1, page_size=30):
            if keyword != "泰国" or column != "sse":
                return [], 0
            return [AnnouncementCandidate(
                announcement_id="cancel-ann-001", stock_code="603801", stock_name="志邦家居",
                title="关于向全资子公司、孙公司增资并对外投资项目的公告", pub_date="2026-10-08",
                source_column="sse", pdf_url="https://example.invalid/test.pdf",
                source_url="https://example.invalid/test.pdf", raw_json="{}", search_keywords="泰国",
            )], 1

        def download_pdf_text(self, candidate, max_pdf_mb=None):
            cancel_event.set()
            return text, "Mock PDF文本提取成功"

    events = []
    stats = collect_official_announcements(
        start_date=date(2026, 10, 1), end_date=date(2026, 10, 10),
        markets=["sse"], regions=["泰国"], pages_per_keyword=1, max_pdfs=10,
        progress=events.append, client=CancelClient(), cancel_event=cancel_event,
    )
    assert stats["cancelled"] is True
    assert db.counts()["projects"] == 0
    assert db.counts()["announcements"] == 0
    assert any(event.get("event") == "cancelled" for event in events)


def test_unlink_announcement_projects_removes_orphan_dossier_but_keeps_announcement(monkeypatch, tmp_path):
    from radar import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "unlink.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db(tmp_path / "unlink.db")
    result = import_pdf_bytes(SAMPLE.name, SAMPLE.read_bytes())
    assert result["is_candidate"] is True
    removed = db.unlink_announcement_projects(result["announcement_id"])
    assert removed == 1
    assert db.counts()["projects"] == 0
    assert db.counts()["announcements"] == 1


def test_html_highlights_are_removed_from_company_and_project_attributes(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "html-clean.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db(tmp_path / "html-clean.db")

    db.upsert_announcement(
        {"announcement_id": "html-ann", "stock_code": "603801", "stock_name": "<em>志邦</em>家居",
         "title": "关于<em>泰国</em>投资的公告", "pub_date": "2026-10-08", "source_column": "sse",
         "source_url": "https://example.invalid/a.pdf", "raw_json": '{"secName":"<em>志邦</em>家居"}'},
        body_text="泰国建设厂房",
    )
    ann = db.fetch_announcements()[0]
    assert ann["stock_name"] == "志邦家居"
    assert ann["title"] == "关于泰国投资的公告"
    assert "<em>" not in ann["raw_json"]

    analysis = {
        "stock_code": "603801", "stock_name": "<em>志邦</em>家居",
        "project_name": "关于<em>泰国</em>投资的公告", "title": "关于<em>泰国</em>投资的公告",
        "countries": ["泰国"], "location": "泰国<em>罗勇</em>府", "investment_subject": "PT <em>ZBOM</em> Thailand",
        "partner": "合作方待确认", "project_type": "生产基地", "investment_amount": "20000万元人民币",
        "currency": "人民币", "project_stage": "拟投资", "construction_certainty": "中",
        "opportunity_grade": "★★★ 高", "pub_date": "2026-10-08",
        "source_url": "https://example.invalid/a.pdf", "certainty_basis": "境外审批仍待完成",
        "opportunity_basis": "地域相关建设信号明确", "evidence": "泰国设立生产子公司并建设厂房",
    }
    db.upsert_project(analysis, "html-ann")
    project = db.fetch_projects()[0]
    for field in ("stock_name", "project_name", "latest_title", "location", "investment_subject"):
        assert "<em>" not in str(project.get(field, "")), (field, project.get(field))
        assert "</em>" not in str(project.get(field, "")), (field, project.get(field))
