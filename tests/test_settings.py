import json

import pytest

from radar import settings


def test_clean_keywords_splits_deduplicates_and_preserves_order():
    assert settings.clean_keywords(["泰国，越南\n印尼", "泰国;马来西亚"]) == ["泰国", "越南", "印尼", "马来西亚"]


def test_saved_keywords_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(settings, "KEYWORDS_PATH", tmp_path / "search_keywords.json")
    saved = settings.save_search_keywords(["泰国", "数据中心", "泰国"])
    assert saved == ["泰国", "数据中心"]
    assert settings.load_search_keywords() == saved
    payload = json.loads((tmp_path / "search_keywords.json").read_text(encoding="utf-8"))
    assert payload["keywords"] == saved


def test_empty_keyword_list_cannot_be_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(settings, "KEYWORDS_PATH", tmp_path / "search_keywords.json")
    with pytest.raises(ValueError):
        settings.save_search_keywords(["", " "])


def test_requested_default_keyword_inventory():
    assert settings.DEFAULT_SEARCH_KEYWORDS == [
        "投资", "募集", "购置", "基地", "产能", "设立", "工厂", "厂房", "印尼", "泰国", "印度尼西亚",
        "越南", "马来西亚", "菲律宾", "新加坡", "缅甸", "柬埔寨", "老挝", "文莱", "东南亚", "海外", "东盟",
    ]


def test_active_keyword_selection_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(settings, "KEYWORDS_PATH", tmp_path / "search_keywords.json")
    settings.save_search_keywords(["泰国", "新加坡", "数据中心"], ["泰国", "数据中心"])
    assert settings.load_search_keywords() == ["泰国", "新加坡", "数据中心"]
    assert settings.load_active_keywords() == ["泰国", "数据中心"]
