"""Static regression tests for Streamlit layout issues and UTF-8 launch settings."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _attribute_is_st_sidebar(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "sidebar"
        and isinstance(node.value, ast.Name)
        and node.value.id == "st"
    )


def test_sidebar_progress_is_not_wrapped_in_fragment():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    sidebar_func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "render_sidebar_progress"
    )
    assert not any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and isinstance(dec.func.value, ast.Name)
        and dec.func.value.id == "st"
        and dec.func.attr == "fragment"
        for dec in sidebar_func.decorator_list
    ), "Sidebar summary should not be a fragment; Streamlit rejects st.sidebar calls inside fragments."
    assert any(_attribute_is_st_sidebar(n) for n in ast.walk(sidebar_func)), (
        "The non-fragment sidebar renderer should write into st.sidebar."
    )
    assert "render_sidebar_progress()" in source

    # The live progress fragment remains in the main body, not in a sidebar renderer.
    collection_func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "render_collection_progress"
    )
    assert any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and isinstance(dec.func.value, ast.Name)
        and dec.func.value.id == "st"
        and dec.func.attr == "fragment"
        for dec in collection_func.decorator_list
    )


def test_windows_launcher_enables_utf8_mode_and_has_current_version():
    launcher = (ROOT / "启动系统.bat").read_text(encoding="utf-8")
    assert 'set "PYTHONUTF8=1"' in launcher
    assert 'set "PYTHONIOENCODING=utf-8"' in launcher
    assert "V2.3" in launcher


def test_main_text_files_are_valid_utf8_without_replacement_chars():
    files = [ROOT / "app.py", ROOT / "run_daily.py", ROOT / "README.md", ROOT / "启动系统.bat"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "\ufffd" not in text, f"Unexpected replacement character in {path.name}"


def test_keyword_selector_has_bulk_actions_and_compact_layout():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'key="kw_select_all"' in source
    assert 'key="kw_select_none"' in source
    assert 'key="kw_invert"' in source
    assert 'key="kw_restore_defaults"' in source
    assert 'range(0, len(inventory), 5)' in source
    assert 'st-key-kw_toggle_' in source


def test_cached_announcement_reanalysis_is_exposed_in_review_page():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "升级规则后重新分析历史公告" in source
    assert "重新分析所有已缓存公告正文" in source
    assert "unlink_announcement_projects(announcement_id)" in source
    assert "工程机会等级依据" in source


def test_streamlit_cloud_import_path_and_admin_readonly_mode_are_documented_in_app():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "def _find_project_root" in source
    assert "找不到 radar/analyzer.py" in source
    assert "RADAR_ADMIN_PASSWORD" in source
    assert "公开只读模式" in source
    assert "disabled=(job_active or not IS_ADMIN)" in source


def test_cscec_company_logo_is_at_top_of_sidebar():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    logo_url = "https://8b2.cscec.com/gsgk/gsjj/images/P020210304415642434307.png"
    assert logo_url in source
    assert 'st.sidebar.image(CSCEC_LOGO_URL, width=210)' in source
    assert source.index('st.sidebar.image(CSCEC_LOGO_URL, width=210)') < source.index('page = st.sidebar.radio')
    assert 'V2.3.2' in source
