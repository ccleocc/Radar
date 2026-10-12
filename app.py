from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import sys
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

# Streamlit Community Cloud executes from the repository root. Keep imports robust if
# the project folder was accidentally committed one level below app.py, but fail with
# a useful deployment hint when the radar package itself was not uploaded.
FILE_DIR = Path(__file__).resolve().parent

def _find_project_root(start: Path) -> Path | None:
    candidates = [start]
    try:
        children = [p for p in start.iterdir() if p.is_dir() and not p.name.startswith((".", "venv", "__pycache__"))]
    except OSError:
        children = []
    candidates.extend(children)
    for child in children:
        try:
            candidates.extend(p for p in child.iterdir() if p.is_dir() and not p.name.startswith((".", "venv", "__pycache__")))
        except OSError:
            pass
    for candidate in candidates:
        if (candidate / "radar" / "analyzer.py").is_file() and (candidate / "radar" / "__init__.py").is_file():
            return candidate
    return None

APP_DIR = _find_project_root(FILE_DIR)
st.set_page_config(page_title="中国上市公司东南亚投资建厂公告雷达", page_icon="🏭", layout="wide")
if APP_DIR is None:
    st.error("部署文件不完整：找不到 radar/analyzer.py。请将完整程序包中的 app.py、requirements.txt 和 radar/ 文件夹一起提交到 GitHub；不要只上传 app.py，也不要只上传 README。")
    st.markdown("Streamlit Cloud 的主文件路径应为仓库根目录下的 `app.py`。仓库根目录需要直接包含以下结构：")
    st.code("app.py\nrequirements.txt\nradar/\n  __init__.py\n  analyzer.py\n  cninfo.py\n  config.py\n  db.py\n  pipeline.py\n  settings.py\n  background.py")
    st.stop()
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

try:
    from radar.analyzer import analyze_announcement, make_project_key
    from radar.cninfo import CNInfoClient
    from radar.config import (
        APP_DIR as CONFIG_APP_DIR, CNINFO_HOME, CNINFO_COLUMNS, DEFAULTS,
        REGION_QUERIES, SAMPLE_DIR, DEFAULT_SEARCH_KEYWORDS,
    )
    from radar.settings import load_search_keywords, load_active_keywords, save_search_keywords, clean_keywords
    from radar.db import (
        connect, counts, delete_project, fetch_announcements, fetch_projects, fetch_runs, init_db,
        project_announcements, upsert_announcement, upsert_project, unlink_announcement_projects,
    )
    from radar.pipeline import import_pdf_bytes
    from radar.background import job_manager
except ModuleNotFoundError as exc:
    if exc.name == "radar" or (exc.name and exc.name.startswith("radar.")):
        st.error(f"部署包中的 radar 模块不完整：缺少 `{exc.name}`。请检查 GitHub 仓库是否上传了整个 radar 文件夹。")
        st.code("仓库根目录\n├── app.py\n├── requirements.txt\n└── radar/  # 必须包含 __init__.py、analyzer.py、cninfo.py、config.py、db.py、pipeline.py、settings.py、background.py")
        st.stop()
    raise

init_db()

st.markdown("""
<style>
.block-container {padding-top: 1.25rem; padding-bottom: 2rem;}
div[data-testid="stMetric"] {border: 1px solid rgba(120,120,120,.18); padding: 12px; border-radius: 10px;}
.small-muted {font-size: .85rem; color: #777;}
div[class*="st-key-kw_toggle_"] button, div[class*="st-key-kw_remove_"] button {
  min-height: 1.65rem !important; height: 1.65rem !important; padding: .08rem .28rem !important;
  font-size: .78rem !important; line-height: 1rem !important; border-radius: 7px !important;
}
div[class*="st-key-kw_remove_"] button { min-width: 1.45rem !important; width: 1.45rem !important; }
</style>
""", unsafe_allow_html=True)

st.title("🏭 中国上市公司东南亚投资建厂公告雷达")
st.caption("V2.3.2")

# Company logo at the top of the sidebar. Keep the official public image URL so the
# deployed app always uses the supplied logo; if the source image cannot be loaded,
# fall back to a clear company name instead of preventing the app from opening.
CSCEC_LOGO_URL = "https://8b2.cscec.com/gsgk/gsjj/images/P020210304415642434307.png"
try:
    st.sidebar.image(CSCEC_LOGO_URL, width=210)
except Exception:
    st.sidebar.markdown("**中国建筑第八工程局有限公司**")
st.sidebar.divider()

PAGE_LABELS = ["总览", "公告采集", "项目商机库", "公告复核", "数据导出", "系统诊断"]
page = st.sidebar.radio("导航", PAGE_LABELS, index=0)


def _configured_admin_password() -> str:
    """Read deployment secret without failing when no local secrets.toml exists."""
    value = os.getenv("RADAR_ADMIN_PASSWORD", "")
    try:
        value = st.secrets.get("RADAR_ADMIN_PASSWORD", value)
    except Exception:
        pass
    return str(value or "").strip()


ADMIN_PASSWORD = _configured_admin_password()
if ADMIN_PASSWORD:
    if "radar_admin_authenticated" not in st.session_state:
        st.session_state["radar_admin_authenticated"] = False
    with st.sidebar.expander("管理员登录", expanded=not st.session_state["radar_admin_authenticated"]):
        if st.session_state["radar_admin_authenticated"]:
            st.success("管理员已登录")
            if st.button("退出管理员", key="radar_admin_logout", use_container_width=True):
                st.session_state["radar_admin_authenticated"] = False
                st.rerun()
        else:
            with st.form("radar_admin_login_form"):
                entered_password = st.text_input("管理员密码", type="password", key="radar_admin_password_input")
                login_clicked = st.form_submit_button("登录并启用管理功能", use_container_width=True)
            if login_clicked:
                if hmac.compare_digest(entered_password or "", ADMIN_PASSWORD):
                    st.session_state["radar_admin_authenticated"] = True
                    st.rerun()
                else:
                    st.error("管理员密码不正确。")
    IS_ADMIN = bool(st.session_state.get("radar_admin_authenticated"))
    if not IS_ADMIN:
        st.sidebar.info("当前为公开只读模式：可以浏览和导出数据；采集、导入、重分析、关键词修改和删除需要管理员登录。")
else:
    # Keep local development convenient, but prominently warn if an open app is deployed.
    IS_ADMIN = True
    st.sidebar.warning("尚未配置管理员密码：当前管理操作对所有访问者开放。")

with st.sidebar.expander("系统边界与当前数据源", expanded=False):
    st.markdown("**主数据源：巨潮资讯网公告全文检索接口**")
    st.markdown(f"[巨潮资讯网]({CNINFO_HOME})")
    st.markdown("[上海证券交易所公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/)")
    st.markdown("[深圳证券交易所公告](https://www.szse.cn/disclosure/notice/company/index.html)")
    st.markdown("[北京证券交易所公告](https://www.bse.cn/disclosure/announcement.html)")
    st.caption("当前不采集普通新闻、公众号或 LPSE；交易所官网链接用于对照核验，自动检索通过巨潮公告接口执行。")


def render_project_table(projects: list[dict], columns: list[str] | None = None, *, selectable: bool = False, table_key: str = "project_table"):
    if not projects:
        st.info("目前没有符合条件的项目档案。可到“公告采集”检索官方公告，或在“公告复核”导入公告 PDF。")
        return None
    df = pd.DataFrame(projects)
    if "countries" in df.columns:
        df["国家/地区"] = df["countries"].apply(lambda x: "、".join(x) if isinstance(x, list) else str(x))
    mapping = {
        "stock_code": "股票代码", "stock_name": "上市公司", "project_name": "公告标题", "国家/地区": "国家/地区",
        "location": "具体地点", "project_type": "项目类型", "investment_subject": "项目投资主体", "partner": "合作方",
        "investment_amount": "公告投资金额", "project_total_investment": "项目总投资",
        "listed_company_contribution": "上市公司出资金额", "project_stage": "项目阶段", "construction_certainty": "建设确定性",
        "opportunity_grade": "工程机会等级", "expected_start": "预计开工", "construction_period": "建设周期",
        "latest_announcement_date": "公告发布日", "latest_title": "公告标题（最近）",
        "source_url": "原文/附件链接", "confidence": "识别置信度",
    }
    df = df.rename(columns=mapping)
    wanted = columns or ["公告发布日", "工程机会等级", "建设确定性", "上市公司", "股票代码", "公告标题", "国家/地区", "具体地点", "项目类型", "公告投资金额", "项目阶段", "预计开工", "建设周期"]
    wanted = [x for x in wanted if x in df.columns]
    table_df = df[wanted]
    if selectable:
        event = st.dataframe(
            table_df, use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row", key=table_key,
            column_config={
                "原文/附件链接": st.column_config.LinkColumn("原文/附件链接", display_text="打开公告"),
                "识别置信度": st.column_config.NumberColumn("识别置信度", format="%.0f%%"),
            },
        )
        try:
            selected_rows = list(event.selection.rows)
        except Exception:
            selected_rows = []
        if selected_rows and 0 <= selected_rows[0] < len(df):
            return str(df.iloc[selected_rows[0]].get("project_key") or "") or None
        return None
    st.dataframe(table_df, use_container_width=True, hide_index=True, column_config={
        "原文/附件链接": st.column_config.LinkColumn("原文/附件链接", display_text="打开公告"),
        "识别置信度": st.column_config.NumberColumn("识别置信度", format="%.0f%%"),
    })
    return None


def _render_progress_monitor(compact: bool = False) -> None:
    state = job_manager.snapshot()
    status = state.get("status", "idle")
    label = {
        "idle": "暂无后台采集任务", "running": "正在采集", "committing": "最终入库中",
        "completed": "采集完成", "cancelled": "已中断（未保存本轮公告）", "failed": "采集失败",
    }.get(status, status)
    if compact:
        st.markdown(f"**后台采集：{label}**")
        if status in {"running", "committing"} or state.get("finished_at"):
            st.progress(max(0.0, min(1.0, float(state.get("progress") or 0.0))), text=state.get("message", ""))
            if state.get("keyword"):
                st.caption(f"关键词：{state.get('keyword')} · 市场：{state.get('market')} · 页码：{state.get('page_num') or 0}/{state.get('page_total') or 0}")
            st.caption(f"候选公告 {state.get('candidate_count', 0)} · 已解析 {state.get('parsed_count', 0)} · 识别商机 {state.get('identified_count', 0)}")
        if status == "running":
            if st.button("⏹ 中断本次采集", key="cancel_collection_sidebar", use_container_width=True, disabled=not IS_ADMIN):
                ok, msg = job_manager.cancel()
                (st.warning if ok else st.info)(msg)
        elif status == "committing":
            st.caption("最终入库阶段，为避免产生不完整数据，暂不接受中断。")
        if state.get("error"):
            with st.expander("查看采集异常"):
                st.code(state["error"])
        return

    st.markdown("### 实时采集进度")
    header_color = {"running": "#2563eb", "committing": "#7c3aed", "completed": "#15803d", "cancelled": "#b45309", "failed": "#b91c1c"}.get(status, "#64748b")
    st.markdown(f"<div style='padding:14px 18px;border-radius:12px;border:1px solid #dbe3ee;background:linear-gradient(120deg,rgba(37,99,235,.07),rgba(124,58,237,.05));margin-bottom:10px'><div style='font-size:12px;color:#64748b'>任务状态</div><div style='font-size:22px;font-weight:700;color:{header_color}'>{label}</div><div style='font-size:13px;color:#64748b'>{state.get('message','')}</div></div>", unsafe_allow_html=True)
    st.progress(max(0.0, min(1.0, float(state.get("progress") or 0.0))), text=f"总进度 {float(state.get('progress') or 0.0):.0%}")
    if status in {"running", "committing"}:
        cols = st.columns([2, 2, 2, 2])
        if status == "committing":
            cols[0].metric("最终入库进度", f"{state.get('commit_index', 0)}/{state.get('commit_total', 0)} 项")
        else:
            cols[0].metric("检索组合", f"{state.get('query_index', 0)}/{state.get('query_total', 0)}")
        cols[1].metric("候选公告", state.get("candidate_count", 0))
        cols[2].metric("已解析正文", state.get("parsed_count", 0))
        cols[3].metric("识别商机", state.get("identified_count", 0))
        if status == "running" and state.get("keyword"):
            remain = max(0, int(state.get("keyword_remaining") or 0))
            page_num = int(state.get("page_num") or 0)
            page_total = int(state.get("page_total") or 0)
            st.markdown(f"**当前检索：** `{state.get('market')}` · **关键词：** `{state.get('keyword')}`  ")
            page_remaining = max(0, page_total - page_num)
            st.caption(f"当前关键词翻页：第 {page_num}/{page_total} 页；当前组合最多还需翻 {page_remaining} 页；后续待查市场/关键词组合约 {remain} 组。若接口提前返回最后一页，实际剩余页数会更少。")
        if status == "running":
            c1, c2 = st.columns([1, 5])
            with c1:
                if st.button("⏹ 中断采集", key="cancel_collection_main", type="secondary", use_container_width=True, disabled=not IS_ADMIN):
                    ok, msg = job_manager.cancel()
                    (st.warning if ok else st.info)(msg)
            with c2:
                st.caption("采集将在后台继续运行；切换左侧页面不会终止任务。中断会丢弃本轮临时结果，不写入公告和商机库。")
        else:
            st.info("正在最终写入数据库，为避免半批数据，请等待入库完成。")
    elif status in {"completed", "cancelled", "failed"}:
        cols = st.columns(5)
        cols[0].metric("候选公告", state.get("candidate_count", 0))
        cols[1].metric("成功提取正文", state.get("pdf_success", 0))
        cols[2].metric("已解析", state.get("parsed_count", 0))
        cols[3].metric("识别商机", state.get("identified_count", 0))
        cols[4].metric("新建档案", state.get("new_project_count", 0))
        if status == "cancelled":
            st.warning("本次采集已中断。临时公告、正文和识别结果均未写入公告/商机数据表；只保留一条中断运行记录用于诊断。")
        elif status == "completed":
            st.success("采集数据已完成统一入库。可以转到“项目商机库”查看，也可以在“公告复核”检查未匹配公告。")
        elif status == "failed":
            st.error(state.get("message", "采集失败"))
    latest = state.get("latest_results") or []
    if latest:
        if status == "completed":
            results_heading = "本轮已保存公告与识别结果（最近20条）"
        elif status == "cancelled":
            results_heading = "本轮临时结果（已中断、未保存，仅供查看）"
        else:
            results_heading = "本轮已处理公告（临时结果，待任务完成后统一保存；最近20条）"
        st.markdown(f"**{results_heading}**")
        show = [{"状态": r.get("status", ""), "公告发布日": r.get("pub_date", ""), "上市公司": r.get("stock_name", ""), "股票代码": r.get("stock_code", ""), "公告标题": r.get("title") or r.get("project_name", ""), "建设确定性": r.get("certainty", ""), "工程机会": r.get("grade", ""), "处理说明": r.get("fetch_status") or r.get("evidence", "")} for r in latest]
        st.dataframe(pd.DataFrame(show), use_container_width=True, hide_index=True)
    with st.expander("实时执行日志", expanded=status in {"running", "committing"}):
        logs = state.get("logs") or []
        st.code("\n".join(logs[-20:]) or "等待任务启动…", language="text")
    if state.get("error"):
        with st.expander("完整异常堆栈"):
            st.code(state["error"])


def render_sidebar_progress() -> None:
    # Render this compact summary during normal app runs, not in a fragment.
    # Streamlit fragments are deliberately kept inside the main content area;
    # this avoids fragment/sidebar restrictions and prevents repeated sidebar
    # elements from accumulating during periodic progress refreshes.
    with st.sidebar:
        st.divider()
        _render_progress_monitor(compact=True)


@st.fragment(run_every="1s")
def render_collection_progress() -> None:
    _render_progress_monitor(compact=False)


def render_keyword_tag_editor() -> tuple[list[str], list[str]]:
    """Editable/removable keyword chips plus a persistent per-run selection state."""
    if "keyword_inventory" not in st.session_state:
        st.session_state["keyword_inventory"] = load_search_keywords()
    if "active_search_keywords" not in st.session_state:
        st.session_state["active_search_keywords"] = load_active_keywords()
    inventory = list(st.session_state["keyword_inventory"])
    active = list(st.session_state["active_search_keywords"])
    active = [kw for kw in active if kw in inventory]
    st.session_state["active_search_keywords"] = active

    st.markdown("#### 全文检索关键词")
    st.caption("紧凑标签：点击词块切换本轮是否启用；点击 × 删除并同步保存到默认词表。管理员登录后可修改。" if IS_ADMIN else "当前为只读浏览模式；管理员登录后可修改关键词。")
    action_cols = st.columns([1, 1, 1, 1, 2.1])
    if action_cols[0].button("☑ 全选", key="kw_select_all", use_container_width=True, disabled=not IS_ADMIN):
        st.session_state["active_search_keywords"] = list(inventory)
        save_search_keywords(inventory, inventory)
        st.rerun()
    if action_cols[1].button("□ 全不选", key="kw_select_none", use_container_width=True, disabled=not IS_ADMIN):
        st.session_state["active_search_keywords"] = []
        save_search_keywords(inventory, [])
        st.rerun()
    if action_cols[2].button("⇄ 反选", key="kw_invert", use_container_width=True, disabled=not IS_ADMIN):
        active_set = set(active)
        st.session_state["active_search_keywords"] = [kw for kw in inventory if kw not in active_set]
        save_search_keywords(inventory, st.session_state["active_search_keywords"])
        st.rerun()
    if action_cols[3].button("↺ 恢复默认", key="kw_restore_defaults", use_container_width=True, disabled=not IS_ADMIN):
        st.session_state["keyword_inventory"] = list(DEFAULT_SEARCH_KEYWORDS)
        st.session_state["active_search_keywords"] = list(DEFAULT_SEARCH_KEYWORDS)
        save_search_keywords(DEFAULT_SEARCH_KEYWORDS, DEFAULT_SEARCH_KEYWORDS)
        st.rerun()
    action_cols[4].caption(f"已启用 {len(active)} / {len(inventory)} 个关键词")
    for start in range(0, len(inventory), 5):
        cols = st.columns(5, gap="small")
        for idx, keyword in enumerate(inventory[start:start + 5]):
            with cols[idx]:
                chip_cols = st.columns([5, 1], gap="small")
                chosen = keyword in active
                digest = hashlib.md5(keyword.encode("utf-8")).hexdigest()[:12]
                if chip_cols[0].button(("✓ " if chosen else "○ ") + keyword, key=f"kw_toggle_{digest}", type="primary" if chosen else "secondary", disabled=not IS_ADMIN):
                    current = list(st.session_state["active_search_keywords"])
                    if keyword in current:
                        current.remove(keyword)
                    else:
                        current.append(keyword)
                    st.session_state["active_search_keywords"] = [kw for kw in inventory if kw in current]
                    save_search_keywords(inventory, st.session_state["active_search_keywords"])
                    st.rerun()
                if chip_cols[1].button("×", key=f"kw_remove_{digest}", help="永久删除此关键词", disabled=not IS_ADMIN):
                    if len(inventory) <= 1:
                        st.error("至少保留一个关键词。")
                    else:
                        new_inventory = [kw for kw in inventory if kw != keyword]
                        new_active = [kw for kw in active if kw != keyword]
                        save_search_keywords(new_inventory, new_active)
                        st.session_state["keyword_inventory"] = new_inventory
                        st.session_state["active_search_keywords"] = new_active
                        st.rerun()

    with st.form("add_keyword_form", clear_on_submit=True):
        add_col, add_btn_col = st.columns([4, 1])
        with add_col:
            new_keyword = st.text_input("添加自定义关键词", placeholder="例如：产业园区、墨西哥、扩建项目", disabled=not IS_ADMIN)
        with add_btn_col:
            submitted = st.form_submit_button("＋ 添加并启用", use_container_width=True, disabled=not IS_ADMIN)
        if submitted:
            to_add = clean_keywords([new_keyword])
            if not to_add:
                st.warning("请输入至少一个关键词。")
            else:
                new_inventory = list(inventory)
                new_active = list(active)
                for word in to_add:
                    if word not in new_inventory:
                        new_inventory.append(word)
                    if word not in new_active:
                        new_active.append(word)
                save_search_keywords(new_inventory, new_active)
                st.session_state["keyword_inventory"] = new_inventory
                st.session_state["active_search_keywords"] = new_active
                st.rerun()
    return list(st.session_state["keyword_inventory"]), list(st.session_state["active_search_keywords"])


# Sidebar progress is a static snapshot per full app rerun. The collection-page
# monitor is the live 1-second fragment; the background worker continues even
# while the user navigates to other pages.
render_sidebar_progress()


if page == "总览":
    c = counts()
    projects = fetch_projects()
    announcements = fetch_announcements()
    runs = fetch_runs(10)
    high = sum(1 for p in projects if p.get("opportunity_grade") == "★★★ 高")
    medium = sum(1 for p in projects if p.get("opportunity_grade") == "★★ 中")
    today = date.today().isoformat()
    today_project = sum(1 for p in projects if p.get("latest_announcement_date") == today)
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("项目档案", c["projects"])
    col2.metric("已收录公告", c["announcements"])
    col3.metric("★★★ 高机会", high)
    col4.metric("★★ 中机会", medium)
    col5.metric("今日公告项目", today_project)

    st.subheader("项目阶段分布")
    if projects:
        stage_counts = pd.Series([p.get("project_stage", "阶段待核实") for p in projects]).value_counts().rename_axis("项目阶段").reset_index(name="项目数")
        st.bar_chart(stage_counts.set_index("项目阶段"))
    else:
        st.info("数据库暂无项目。建议先在“公告采集”选择较短日期范围进行首轮验证，或导入志邦家居案例 PDF。")

    st.subheader("最新工程机会")
    render_project_table(projects[:20])
    st.subheader("最近采集运行")
    if runs:
        run_df = pd.DataFrame([{k: r.get(k) for k in ["run_id", "started_at", "finished_at", "start_date", "end_date", "status", "candidate_count", "parsed_count", "identified_count", "new_project_count", "errors_count"]} for r in runs])
        run_df = run_df.rename(columns={"run_id":"运行编号", "started_at":"开始时间", "finished_at":"结束时间", "start_date":"起始日期", "end_date":"结束日期", "status":"状态", "candidate_count":"候选公告", "parsed_count":"已解析", "identified_count":"识别商机", "new_project_count":"新建项目", "errors_count":"异常数"})
        st.dataframe(run_df, use_container_width=True, hide_index=True)

elif page == "公告采集":
    st.subheader("官方公告检索与实时采集")
    st.markdown("通过巨潮资讯网公开公告查询接口执行 `fulltext` 全文检索。标题或正文只有在巨潮索引可检索到关键词时才会进入候选列表；命中后程序下载公告 PDF，再识别地域、投资金额和实体建设线索。")
    with st.expander("采集范围与关键词设置", expanded=True):
        left, right = st.columns(2)
        with left:
            default_start = date.today() - timedelta(days=90)
            start_date = st.date_input("公告披露起始日期", value=default_start, key="collect_start")
            end_date = st.date_input("公告披露结束日期", value=date.today(), key="collect_end")
            market_labels = st.multiselect("公告市场（可多选）", list(CNINFO_COLUMNS.keys()), default=list(CNINFO_COLUMNS.keys()), key="collection_markets")
            markets = [CNINFO_COLUMNS[x] for x in market_labels]
        with right:
            pages_per_keyword = st.select_slider("每个市场/关键词组合最多翻页", options=[1, 2, 3, 5, 8], value=1, key="pages_per_keyword")
            max_pdfs = st.select_slider("本轮最多解析公告 PDF 数", options=[30, 60, 120, 200, 300, 500], value=60, key="max_pdfs")
            include_unknown_dates = st.checkbox("包含无法识别公告发布日的公告", value=False, key="include_unknown_dates")
        inventory, active_keywords = render_keyword_tag_editor()
        st.caption(f"关键词总数：{len(inventory)} 个 · 本轮启用：{len(active_keywords)} 个。新增/删除词和启用状态均已写入本地默认配置。")
        st.caption("市场说明：沪深及北交所、新三板和港股分别检索；港股和新三板的代码格式与 A 股不同，程序不会将它们按 A 股代码规则过滤。")
        st.caption("建议首次选择最近30–90天并限制1页；如需扩大覆盖，再增加翻页数或延长日期范围。巨潮索引和接口仍可能造成漏检。")

    snapshot = job_manager.snapshot()
    job_active = snapshot.get("status") in {"running", "committing"}
    start_col, note_col = st.columns([1, 4])
    with start_col:
        run_clicked = st.button("▶ 开始采集", type="primary", use_container_width=True, disabled=(job_active or not IS_ADMIN))
    with note_col:
        st.caption("开始后会在后台执行；切换左侧其他页面不影响采集。只有在最终入库阶段开始前中断，才会丢弃全部本轮临时结果。" if IS_ADMIN else "只读模式不能启动采集。请在左侧管理员登录后操作。")

    if run_clicked:
        if not market_labels:
            st.error("请至少选择一个公告市场。")
        elif not active_keywords:
            st.error("请至少启用一个检索关键词方块。")
        elif start_date > end_date:
            st.error("开始日期不能晚于结束日期。")
        else:
            try:
                save_search_keywords(inventory, active_keywords)
                job_manager.start(
                    start_date=start_date, end_date=end_date, markets=markets, regions=active_keywords,
                    page_size=DEFAULTS["page_size"], pages_per_keyword=int(pages_per_keyword),
                    max_pdfs=int(max_pdfs), include_unknown_dates=include_unknown_dates,
                )
                st.toast("采集任务已启动；可以切换页面，任务会继续在后台运行。", icon="🚀")
                st.rerun()
            except Exception as exc:
                st.error(f"启动采集失败：{type(exc).__name__}: {exc}")

    st.divider()
    render_collection_progress()

    st.divider()
    with st.expander("采集逻辑与数据完整性说明"):
        st.markdown("""
        - **检索入口**：`https://www.cninfo.com.cn/new/hisAnnouncement/query`，POST 表单；日期参数采用 `YYYY-MM-DD~YYYY-MM-DD`。请求头包含公告查询页 Referer、User-Agent 和 X-Requested-With。
        - **全文检索**：`tabName=fulltext`、`searchkey=关键词`。这不等于巨潮一定索引了所有 PDF 正文，因此仍建议扩大日期范围、关键词和翻页数。
        - **中断策略**：公告元数据、正文和商机先暂存在内存中；用户在最终入库前中断，则本轮公告和商机不保存。最终入库开始后暂不接受中断，以免数据库只写入一部分。
        - **后台运行**：任务在线程中执行，切换本应用的其他页面不会终止；左侧栏展示当前任务状态快照，返回“公告采集”页面可查看每秒刷新的详细进度。
        """)

elif page == "项目商机库":
    st.subheader("项目级工程商机档案")
    projects = fetch_projects()
    if not projects:
        st.info("商机库目前为空。请先进入“公告采集”，或在“公告复核”导入公告 PDF / 志邦家居案例。")
    else:
        f1, f2, f3, f4 = st.columns(4)
        search_text = f1.text_input("搜索公司/公告标题", placeholder="股票代码、公司名、公告标题、建设关键词", key="opportunity_search")
        country_values = sorted({c for p in projects for c in (p.get("countries") or [])})
        selected_country = f2.multiselect("国家/地区", country_values, key="opportunity_country_filter")
        selected_certainty = f3.multiselect("建设确定性", ["高", "中", "低"], default=["高", "中", "低"], key="opportunity_certainty_filter")
        selected_grade = f4.multiselect("工程机会等级", ["★★★ 高", "★★ 中", "★ 观察"], default=["★★★ 高", "★★ 中", "★ 观察"], key="opportunity_grade_filter")
        filtered = []
        for p in projects:
            haystack = " ".join(str(p.get(k, "")) for k in ["stock_code", "stock_name", "project_name", "latest_title", "investment_subject", "location", "project_type"])
            if search_text and search_text.lower() not in haystack.lower():
                continue
            if selected_country and not any(c in (p.get("countries") or []) for c in selected_country):
                continue
            if selected_certainty and p.get("construction_certainty") not in selected_certainty:
                continue
            if selected_grade and p.get("opportunity_grade") not in selected_grade:
                continue
            filtered.append(p)

        st.caption(f"当前显示 {len(filtered)} / {len(projects)} 个商机档案。表格首列为公告发布日；点击表格中的一行，下方详情将同步切换到该档案。")
        clicked_project_key = render_project_table(filtered, selectable=True, table_key="opportunity_library_table")
        project_by_key = {str(p.get("project_key")): p for p in filtered}
        if clicked_project_key and clicked_project_key in project_by_key:
            # Only let a new table-row selection override the details selector. This avoids
            # a stale selected row overriding a user's later manual choice in the selector.
            if clicked_project_key != st.session_state.get("last_table_clicked_project_key"):
                st.session_state["project_detail_key"] = clicked_project_key
            st.session_state["last_table_clicked_project_key"] = clicked_project_key
        else:
            st.session_state["last_table_clicked_project_key"] = None

        filtered_keys = list(project_by_key.keys())
        if filtered_keys:
            if st.session_state.get("project_detail_key") not in project_by_key:
                st.session_state["project_detail_key"] = filtered_keys[0]
            label_by_key = {
                str(p.get("project_key")): f"{p.get('latest_announcement_date') or '日期待核实'} | {p.get('opportunity_grade')} | {p.get('stock_name')}（{p.get('stock_code')}） | {p.get('project_name')}"
                for p in filtered
            }
            st.divider()
            st.markdown("### 项目详情")
            chosen_key = st.selectbox(
                "选择项目查看详细信息",
                options=filtered_keys,
                format_func=lambda key: label_by_key.get(str(key), str(key)),
                key="project_detail_key",
            )
            project = project_by_key[chosen_key]

            col_date, col_grade, col_certainty, col_confidence = st.columns([1.2, 1.2, 1.2, 1])
            col_date.metric("公告发布日", project.get("latest_announcement_date") or "未识别")
            col_grade.metric(
                "工程机会等级 ⓘ", project.get("opportunity_grade", ""),
                help="不是简单累加‘投资/建设’关键词。出现地域相关的明确厂房、生产基地、土地购置、施工/EPC等实体工程信号，评为高；生产型子公司同时明确开展产品生产制造的，即使未说清新建或租赁路径，也评为高并提示工程方式待核实；仅有投资、设立或考察意向为观察/中。没有与东南亚项目关联的证据则不纳入。该等级表示工程跟踪价值，不等于中标概率。",
            )
            col_certainty.metric(
                "建设确定性 ⓘ", project.get("construction_certainty", ""),
                help="只对已确认与东南亚投资/生产建设直接关联的公告判断。参考项目决策、投资金额、地点、建设内容、开工/周期，以及境外投资备案、当地投资许可、企业登记等前置手续；尚待审批或建设方案仍可调整时会降低确定性。‘中’表示方向和经营/建设需求有依据，但落地手续或具体实施条件尚未完全确定；不是项目必然开工的保证。",
            )
            col_confidence.metric("识别置信度", f"{float(project.get('confidence', 0)):.0%}")

            def _shown(value, fallback="公告未披露"):
                if value is None or str(value).strip() == "":
                    return fallback
                return str(value)

            st.markdown(f"**公告标题：** {_shown(project.get('latest_title') or project.get('project_name'), '标题待核实')}  ")
            st.markdown(f"**上市公司 / 股票代码：** {_shown(project.get('stock_name'), '未识别')} / {_shown(project.get('stock_code'), '未识别')}  ")
            st.markdown(f"**国家/地区：** {_shown('、'.join(project.get('countries') or []), '待核实')}  ")
            st.markdown(f"**具体地点：** {_shown(project.get('location'))}  ")
            st.markdown(f"**投资主体：** {_shown(project.get('investment_subject'))}  ")
            st.markdown(f"**合作方：** {_shown(project.get('partner'))}  ")
            st.markdown(f"**项目类型：** {_shown(project.get('project_type'))}  ")
            st.markdown(f"**公告投资金额（优先显示项目层级口径）：** {_shown(project.get('investment_amount'))}  ")
            st.markdown(f"**项目总投资：** {_shown(project.get('project_total_investment'))}  ")
            st.markdown(f"**上市公司出资金额：** {_shown(project.get('listed_company_contribution'))}  ")
            st.markdown(f"**金额识别依据：** {_shown(project.get('amount_evidence'))}  ")
            amount_candidates = project.get("amount_candidates") or []
            if amount_candidates:
                with st.expander("查看公告中识别到的其他金额候选项", expanded=False):
                    st.caption("系统优先采用明确标注的项目总投资；上市公司出资金额单独列示。其他候选项保留供人工核对，不代表都属于项目投资。")
                    st.dataframe(pd.DataFrame(amount_candidates), use_container_width=True, hide_index=True)
            st.markdown(f"**项目阶段：** {_shown(project.get('project_stage'))}  ")
            st.markdown(f"**预计开工：** {_shown(project.get('expected_start'))}  ")
            st.markdown(f"**建设周期：** {_shown(project.get('construction_period'))}  ")
            st.markdown("**潜在工程范围（规则抽取，需人工确认）**")
            st.write(_shown(project.get("construction_scope")))
            st.markdown("**建设确定性依据**")
            st.write(_shown(project.get("certainty_basis"), "暂未生成明确依据"))
            st.markdown("**工程机会等级依据**")
            st.write(_shown(project.get("opportunity_basis"), "暂未生成明确依据"))
            st.markdown("**识别证据**")
            st.write(_shown(project.get("evidence"), "暂无可展示的文本证据"))
            if project.get("source_url", "").startswith("http"):
                st.markdown(f"[打开对应公告原文/PDF]({project['source_url']})")

            with st.expander("删除商机档案", expanded=False):
                st.warning("删除后，该项目档案将从商机库移除；原始公告仍保留在公告复核/公告记录中。此操作不可直接撤销。")
                confirm_delete = st.checkbox("我确认删除当前商机档案", key=f"confirm_delete_{chosen_key}")
                if st.button("🗑️ 永久删除此商机档案", key=f"delete_project_{chosen_key}", disabled=(not confirm_delete or not IS_ADMIN), type="secondary"):
                    deleted = delete_project(chosen_key)
                    if deleted:
                        st.session_state.pop("project_detail_key", None)
                        st.success("商机档案已删除；原始公告记录已保留。")
                    else:
                        st.error("未找到对应档案，可能已被删除。")
                    st.rerun()
        else:
            st.info("当前筛选条件下没有项目。请调整搜索词或筛选范围。")

elif page == "公告复核":
    st.subheader("公告复核与手工导入")
    st.markdown("由于正式公告的格式、表格和正文表述存在差异，系统保留已解析但未匹配为商机的公告，供业务人员复核。新版会先确认东南亚地域与项目投资/制造建设信号之间的关联，再评价工程机会等级。")
    with st.expander("升级规则后重新分析历史公告（可清理旧版误报）", expanded=False):
        st.caption("此操作会使用当前提取规则重算所有已保存公告正文；不重新联网下载 PDF。原始公告和正文保留。若某公告不再符合东南亚商机规则，其关联商机档案会被移除；符合规则的档案会重新生成。建议先备份 data/radar.db。")
        confirm_reanalyze = st.checkbox("我已备份数据库，并确认重新计算历史公告分析结果", key="confirm_reanalyze_cached")
        if st.button("重新分析所有已缓存公告正文", key="reanalyze_cached_announcements", type="primary", disabled=(not confirm_reanalyze or not IS_ADMIN)):
            cached = [a for a in fetch_announcements() if (a.get("body_text") or "").strip()]
            bar = st.progress(0.0, text="准备重新分析…")
            status_text = st.empty()
            included_count = excluded_count = removed_count = failed_count = 0
            total = len(cached)
            for idx, ann in enumerate(cached, start=1):
                try:
                    analysis = analyze_announcement(
                        title=ann.get("title", ""), text=ann.get("body_text", ""),
                        stock_code=ann.get("stock_code", ""), stock_name=ann.get("stock_name", ""),
                        pub_date=ann.get("pub_date", ""), source_url=ann.get("source_url", ""),
                    )
                    announcement_id = str(ann.get("announcement_id") or "")
                    removed_count += unlink_announcement_projects(announcement_id)
                    candidate = {
                        "announcement_id": announcement_id, "stock_code": ann.get("stock_code", ""),
                        "stock_name": ann.get("stock_name", ""), "title": ann.get("title", ""),
                        "pub_date": ann.get("pub_date", ""), "source_column": ann.get("market", ""),
                        "source_url": ann.get("source_url", ""), "pdf_url": ann.get("pdf_url", ""),
                        "search_keywords": ann.get("search_keywords", ""), "raw_json": ann.get("raw_json", "{}"),
                    }
                    is_candidate = bool(analysis.get("included_as_candidate"))
                    upsert_announcement(
                        candidate, body_text=ann.get("body_text", ""), analysis=analysis,
                        processing_status="已识别-待复核" if is_candidate else "已解析-未匹配商机",
                        fetch_status=ann.get("fetch_status", "规则重分析"), imported_from=ann.get("imported_from", ""),
                    )
                    if is_candidate:
                        upsert_project(analysis, announcement_id)
                        included_count += 1
                    else:
                        excluded_count += 1
                except Exception as exc:
                    failed_count += 1
                    status_text.warning(f"重分析失败：{ann.get('title', '')} — {type(exc).__name__}: {exc}")
                bar.progress(idx / max(1, total), text=f"已处理 {idx}/{total} 条")
                status_text.caption(f"当前：{ann.get('stock_name', '')}｜{ann.get('title', '')}")
            status_text.success(f"重分析完成：合格商机 {included_count} 条；排除/不纳入 {excluded_count} 条；清理旧商机档案 {removed_count} 个；失败 {failed_count} 条。")
    with st.expander("导入上市公司公告 PDF（适用于单篇公告或回溯样本）", expanded=True):
        files = st.file_uploader("选择一个或多个公告 PDF", type=["pdf"], accept_multiple_files=True, disabled=not IS_ADMIN)
        override_manual_date = st.checkbox("统一指定披露日期（仅在需要覆盖公告原日期时勾选）", value=False)
        manual_date = st.date_input("指定披露日期", value=date.today(), disabled=not override_manual_date)
        if st.button("解析并导入所选PDF", type="primary", disabled=(not files or not IS_ADMIN)):
            successes = 0; failures = []
            for uploaded in files or []:
                try:
                    result = import_pdf_bytes(uploaded.name, uploaded.getvalue(), manual_date.isoformat() if override_manual_date else "")
                    successes += 1
                    st.write({"文件": uploaded.name, "上市公司": result["analysis"].get("stock_name"), "公告标题": result["analysis"].get("project_name"), "是否候选商机": result["is_candidate"], "工程机会": result["analysis"].get("opportunity_grade"), "建设确定性": result["analysis"].get("construction_certainty"), "解析结果": result["parse_status"]})
                except Exception as exc:
                    failures.append({"文件": uploaded.name, "异常": str(exc)})
            st.success(f"处理完成：成功 {successes} 个，失败 {len(failures)} 个。")
            if failures: st.dataframe(pd.DataFrame(failures), use_container_width=True, hide_index=True)

    sample_path = SAMPLE_DIR / "志邦家居_新建泰国智能制造基地项目_公告样例.pdf"
    if sample_path.exists():
        if st.button("载入内置回归案例：志邦家居泰国智能制造基地", disabled=not IS_ADMIN):
            try:
                result = import_pdf_bytes(sample_path.name, sample_path.read_bytes())
                st.success("样例已载入数据库，可在项目商机库查看详细字段。")
                st.json({"公告标题": result["analysis"].get("project_name"), "公司": result["analysis"].get("stock_name"), "股票代码": result["analysis"].get("stock_code"), "国家/地区": result["analysis"].get("countries"), "地点": result["analysis"].get("location"), "金额": result["analysis"].get("investment_amount"), "预计开工": result["analysis"].get("expected_start"), "建设周期": result["analysis"].get("construction_period"), "建设确定性": result["analysis"].get("construction_certainty"), "工程机会": result["analysis"].get("opportunity_grade"), "是否候选商机": result["is_candidate"]})
            except Exception as exc:
                st.error(f"样例导入失败：{exc}")

    st.divider()
    status_filter = st.selectbox("公告状态", ["全部", "已识别-待复核", "已解析-未匹配商机", "正文抓取失败-待复核", "已解析-待复核"])
    announcements = fetch_announcements(None if status_filter == "全部" else status_filter)
    st.caption(f"共 {len(announcements)} 份公告记录。")
    if announcements:
        query = st.text_input("在已入库公告中搜索")
        filtered_ann = []
        for ann in announcements:
            if query and query.lower() not in (ann.get("title", "") + ann.get("stock_name", "") + ann.get("body_text", "")[:1500]).lower():
                continue
            filtered_ann.append(ann)
        for ann in filtered_ann[:100]:
            with st.expander(f"{ann.get('pub_date') or '日期未知'} | {ann.get('stock_name')}（{ann.get('stock_code')}） | {ann.get('title')} | {ann.get('processing_status')}"):
                analysis = ann.get("analysis_json_parsed") or {}
                if analysis:
                    st.write({"是否候选": analysis.get("included_as_candidate"), "公告标题": analysis.get("project_name"), "地区": analysis.get("countries"), "建设确定性": analysis.get("construction_certainty"), "工程机会": analysis.get("opportunity_grade"), "排除原因": analysis.get("excluded_reason")})
                    st.markdown("**命中信号**")
                    st.json(analysis.get("hit_signals", {}))
                    st.markdown("**原文依据**")
                    st.write(analysis.get("evidence", ""))
                st.markdown("**抓取状态**")
                st.write(ann.get("fetch_status", ""))
                with st.expander("查看公告正文片段"):
                    st.text((ann.get("body_text") or "")[:8000] or "未保存可用正文")
                if ann.get("source_url", "").startswith("http"):
                    st.markdown(f"[打开原文/PDF]({ann['source_url']})")

elif page == "数据导出":
    st.subheader("数据导出")
    projects = fetch_projects()
    announcements = fetch_announcements()
    if projects:
        project_rows = []
        export_fields = {
            "latest_announcement_date": "公告发布日", "stock_name": "上市公司", "stock_code": "股票代码",
            "project_name": "公告标题", "countries": "国家/地区", "location": "具体地点",
            "investment_subject": "项目投资主体", "partner": "合作方", "project_type": "项目类型",
            "investment_amount": "公告投资金额", "currency": "金额币种口径",
            "project_total_investment": "项目总投资", "listed_company_contribution": "上市公司出资金额",
            "amount_evidence": "金额原文依据", "amount_candidates": "其他金额候选项",
            "construction_scope": "潜在工程范围", "project_stage": "项目阶段",
            "expected_start": "预计开工日期", "construction_period": "建设周期",
            "construction_certainty": "建设确定性", "certainty_score": "建设确定性评分",
            "certainty_basis": "建设确定性依据", "opportunity_grade": "工程机会等级",
            "opportunity_score": "工程机会评分", "opportunity_basis": "工程机会判断依据", "confidence": "识别置信度",
            "evidence": "识别证据", "source_url": "公告原文/PDF链接",
        }
        for p in projects:
            row = {}
            for source, label in export_fields.items():
                value = p.get(source, "")
                if source == "countries":
                    value = "、".join(value or [])
                elif source in {"amount_candidates"}:
                    value = json.dumps(value or [], ensure_ascii=False)
                row[label] = value
            row["命中信号"] = json.dumps(p.get("hit_signals") or {}, ensure_ascii=False)
            project_rows.append(row)
        project_df = pd.DataFrame(project_rows)
        st.markdown("### 项目商机库")
        st.dataframe(project_df.head(20), use_container_width=True, hide_index=True)
        st.download_button("下载项目商机库 CSV", project_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"), file_name=f"sea_factory_projects_{date.today().isoformat()}.csv", mime="text/csv")
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            project_df.to_excel(writer, sheet_name="项目商机", index=False)
            pd.DataFrame([{k: a.get(k, "") for k in ["announcement_id", "stock_code", "stock_name", "title", "pub_date", "market", "source_url", "processing_status", "fetch_status", "search_keywords"]} for a in announcements]).to_excel(writer, sheet_name="公告档案", index=False)
        st.download_button("下载 Excel（项目+公告）", excel_buffer.getvalue(), file_name=f"sea_factory_radar_{date.today().isoformat()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("暂无项目商机可导出；如果已导入但未识别为商机的公告，仍可单独导出公告档案。")
    if announcements:
        ann_df = pd.DataFrame([{k: a.get(k, "") for k in ["announcement_id", "stock_code", "stock_name", "title", "pub_date", "market", "source_url", "processing_status", "fetch_status", "search_keywords"]} for a in announcements])
        st.download_button("单独下载公告档案 CSV", ann_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"), file_name=f"sea_factory_announcements_{date.today().isoformat()}.csv", mime="text/csv")
        if not projects:
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                ann_df.to_excel(writer, sheet_name="公告档案", index=False)
            st.download_button("下载公告档案 Excel", excel_buffer.getvalue(), file_name=f"sea_factory_announcements_{date.today().isoformat()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

elif page == "系统诊断":
    st.subheader("系统诊断与采集质量")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("测试巨潮接口", type="primary", disabled=not IS_ADMIN):
            with st.spinner("请求巨潮公开公告接口…"):
                health = CNInfoClient().test_connection()
            if health["ok"]: st.success(f"{health['message']}；样本数={health['records']}，命中总数={health['total']}")
            else: st.error(health["message"])
    with c2:
        if st.button("打开巨潮官网"):
            st.link_button("巨潮资讯网", CNINFO_HOME)
    st.markdown("### 官方平台入口")
    st.markdown(f"- [巨潮资讯网]({CNINFO_HOME})\n- [上交所上市公司公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/)\n- [深交所上市公司公告](https://www.szse.cn/disclosure/notice/company/index.html)\n- [北交所上市公司公告](https://www.bse.cn/disclosure/announcement.html)")
    st.markdown("### 数据库计数")
    st.json(counts())
    st.markdown("### 最近运行与异常")
    runs = fetch_runs(30)
    if runs:
        for run in runs:
            with st.expander(f"运行 #{run['run_id']} | {run['started_at']} | {run['status']} | 候选{run['candidate_count']} / 解析{run['parsed_count']} / 商机{run['identified_count']} / 异常{run['errors_count']}"):
                try: details = json.loads(run.get("details_json") or "{}")
                except json.JSONDecodeError: details = {}
                st.json({k: v for k, v in details.items() if k not in {"results", "query_stats"}})
                if details.get("errors"):
                    st.dataframe(pd.DataFrame(details["errors"]), use_container_width=True, hide_index=True)
                if details.get("query_stats"):
                    st.markdown("**按市场/地区检索情况**")
                    st.dataframe(pd.DataFrame(details["query_stats"]), use_container_width=True, hide_index=True)
    st.markdown("### 常见诊断")
    st.markdown("- **检索候选为0**：可能日期范围太窄、检索词无命中、接口变更或网络不可访问。\n- **有候选、PDF成功为0**：优先检查 PDF 地址、代理/网络和网站反爬返回。\n- **有正文、商机为0**：在“公告复核”检查文本和命中信号，后续可调整识别规则。\n- **已识别但新建项目为0**：多半为同一项目关联更新/去重，而不一定是采集失败。")

st.sidebar.divider()
st.sidebar.caption(f"数据库：{(APP_DIR / 'data' / 'radar.db').relative_to(APP_DIR)}")
st.sidebar.caption("建议先用样例验证字段，再用近30天公告做小批量联网测试。")
