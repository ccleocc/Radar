from pathlib import Path

from pypdf import PdfReader

from radar.analyzer import analyze_announcement

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "志邦家居_新建泰国智能制造基地项目_公告样例.pdf"


def pdf_text(path: Path) -> str:
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_zbom_thailand_factory_case():
    text = pdf_text(SAMPLE)
    result = analyze_announcement(
        title="关于向全资子公司、孙公司增资并对外投资项目的公告",
        text=text,
        stock_code="603801",
        stock_name="志邦家居",
        pub_date="2026-10-08",
    )
    assert result["included_as_candidate"] is True
    assert result["project_name"] == "关于向全资子公司、孙公司增资并对外投资项目的公告"
    assert result["countries"] == ["泰国"]
    assert result["location"] == "泰国罗勇府"
    assert result["investment_subject"] == "泰国德志"
    assert result["investment_amount"].startswith("20,000.00")
    assert "万元人民币" in result["investment_amount"]
    assert "等值外币" in result["investment_amount"]
    assert result["project_total_investment"] == result["investment_amount"]
    assert result["listed_company_contribution"] == result["investment_amount"]
    assert result["expected_start"] == "2026-12-15"
    assert result["construction_period"] == "18月"
    assert "新建厂房" in result["construction_scope"]
    assert result["opportunity_grade"] == "★★★ 高"
    assert result["construction_certainty"] == "中"  # regulatory approvals still pending


def test_wide_capture_investment_intent_without_final_factory_plan():
    text = "公司拟在越南投资布局，进一步拓展海外业务，目前处于项目论证阶段，投资金额、地点和建设内容尚未确定。"
    result = analyze_announcement("关于拟在越南开展境外投资的公告", text, "300001", "示例科技", "2026-10-08")
    assert result["included_as_candidate"] is True
    assert "越南" in result["countries"]
    assert result["construction_certainty"] == "低"
    assert result["opportunity_grade"] in {"★ 观察", "★★ 中"}


def test_factory_plan_in_indonesia_is_captured():
    text = "董事会审议通过境外投资计划，公司拟在印度尼西亚西爪哇省新建电池材料生产基地，项目计划分期建设，正在办理投资备案。"
    result = analyze_announcement("关于对外投资建设印尼生产基地的公告", text, "002001", "示例新材", "2026-10-08")
    assert result["included_as_candidate"] is True
    assert "印度尼西亚" in result["countries"]
    assert result["location"] == "印尼西爪哇省"
    assert "工业厂房/制造基地" in result["project_type"]


def test_pure_share_acquisition_without_build_signals_is_excluded():
    text = "公司拟收购泰国当地经销公司100%股权，以扩大当地销售网络。本次交易为股权收购，不涉及工厂、厂房、生产线或建设项目。"
    result = analyze_announcement("关于收购泰国公司股权的公告", text, "600001", "示例股份", "2026-10-08")
    assert result["included_as_candidate"] is False
    assert result["excluded_reason"]


def test_planned_start_date_is_not_misclassified_as_already_started():
    text = "公司董事会已审议通过在泰国新建工厂项目，预计开工时间为2027年3月1日，项目计划建设厂房和生产线，尚需办理投资备案。"
    result = analyze_announcement("关于在泰国新建工厂项目的公告", text, "603801", "示例家居", "2026-10-08")
    assert result["included_as_candidate"] is True
    assert result["expected_start"] == "2027-03-01"
    assert result["project_stage"] != "已开工建设"


def test_usd_amount_is_not_misclassified_as_rmb_and_company_contribution_is_separate():
    from radar.analyzer import extract_amount_details

    details = extract_amount_details(
        "项目总投资额为4000万美元，上市公司拟出资2000万美元，项目建设地点为泰国。"
    )
    assert details["investment_amount"] == "4000万美元"
    assert details["currency"] == "USD"
    assert details["project_total_investment"] == "4000万美元"
    assert details["listed_company_contribution"] == "2000万美元"
    assert "4000万元人民币" not in details["investment_amount"]
    assert details["amount_candidates"]


def test_project_total_takes_priority_over_multiple_amounts():
    from radar.analyzer import extract_amount_details

    details = extract_amount_details(
        "项目总投资金额：4000万美元；上市公司拟出资金额为人民币1.2亿元；公司上一年度营业收入为50亿元。"
    )
    assert details["investment_amount"] == "4000万美元"
    assert details["project_total_investment"] == "4000万美元"
    assert details["listed_company_contribution"] == "1.2亿元"


def test_anjoy_indonesia_production_subsidiary_is_high_engineering_lead_medium_certainty():
    # Regression fixture based on Anjoy's 2026-09-10 announcement: the disclosure sets up
    # an Indonesian production subsidiary, names products to be produced, discusses cold-chain
    # infrastructure, commits a USD funding amount, and still awaits multiple approvals.
    text = (
        "为推进公司国际化战略，公司拟通过全资子公司香港安井与合作方在香港设立路径公司。"
        "路径公司设立后，将分别在印度尼西亚设立印尼生产子公司及印尼贸易子公司。"
        "投资金额：香港安井与合作方承诺按照各自50.5%和49.5%的持股比例，"
        "向香港路径公司以现金形式出资合计不少于7,425万美元。按最低出资金额计算，"
        "其中香港安井以公司自有资金出资3,749.625万美元，合作方出资3,675.375万美元。"
        "印尼子公司设立后，将开展速冻鱼糜制品、速冻烤肠、速冻面米制品等产品的生产和销售。"
        "双方还应促使印尼子公司建立并维护符合适用法律和行业标准的冷链基础设施。"
        "董事会审议通过本次投资方案。本次对外投资尚需办理境外投资备案或审批、"
        "企业设立登记以及项目建设和运营所需的相关许可。"
    )
    result = analyze_announcement(
        title="关于对外投资暨设立印度尼西亚合资公司并签署股东协议的公告",
        text=text, stock_code="603345", stock_name="安井食品", pub_date="2026-09-10",
    )
    assert result["included_as_candidate"] is True
    assert result["countries"] == ["印度尼西亚"]
    assert result["opportunity_grade"] == "★★★ 高"
    assert result["construction_certainty"] == "中"
    assert result["investment_amount"] == "7,425万美元"
    assert result["currency"] == "USD"
    assert result["listed_company_contribution"] == "3,749.625万美元"
    assert "冷链基础设施" in result["construction_scope"]
    assert "生产型子公司" in result["opportunity_basis"] or "实体建设" in result["opportunity_basis"]


def test_taijing_vietnam_factory_explicit_build_is_high_engineering_lead_medium_certainty():
    # Regression fixture based on the 2026-10-09 Taijing Technology announcement.
    text = (
        "公司拟在越南设立全资子公司并建设生产基地。本次总投资额不超过4,000万美元，"
        "投资款主要用于设立及运营境外公司、购买土地和厂房、厂房建设、设备采购、流动资金等。"
        "公司董事会审议通过。本次投资尚需获得商务、发改、外汇管理等部门的境内对外投资备案或审批，"
        "以及越南当地投资许可和企业登记等审批或登记程序。"
    )
    result = analyze_announcement(
        title="关于对外投资设立境外全资子公司并建设生产基地的公告",
        text=text, stock_code="603738", stock_name="泰晶科技", pub_date="2026-10-09",
    )
    assert result["included_as_candidate"] is True
    assert result["countries"] == ["越南"]
    assert result["opportunity_grade"] == "★★★ 高"
    assert result["construction_certainty"] == "中"
    assert result["investment_amount"] == "4,000万美元"
    assert "购买土地和厂房" in result["opportunity_basis"] or "建设生产基地" in result["opportunity_basis"]


def test_fuda_domestic_bond_prospectus_is_not_sea_opportunity_despite_factory_terms():
    # Regression fixture distilled from the 2026-10-10 Fuda convertible-bond prospectus.
    # It mentions domestic manufacturing construction projects in Jiaxing, Zhejiang, but no
    # Southeast Asian project/country. Those domestic build words must not create a SEA lead.
    text = (
        "桂林福达股份有限公司向不特定对象发行可转换公司债券募集说明书（申报稿）。"
        "本次募投项目包括新能源汽车电驱齿轮系统数智化制造建设项目和精密减速器数智化制造建设项目。"
        "项目实施主体为公司全资子公司，项目选址于浙江省嘉兴市，在已有土地上建设，不涉及新增土地。"
        "项目已取得当地企业投资项目备案信息表及建设项目环评审查意见。"
        "公司拟使用本次募集资金补充流动资金，用于日常运营和生产制造。"
    )
    result = analyze_announcement(
        title="向不特定对象发行可转换公司债券募集说明书（申报稿）",
        text=text, stock_code="603166", stock_name="福达股份", pub_date="2026-10-10",
    )
    assert result["included_as_candidate"] is False
    assert result["has_regional_nexus"] is False
    assert result["construction_certainty"] == "不适用"
    assert result["opportunity_grade"] == "— 不纳入"
    assert not result["investment_amount"]
    assert "国内建设内容" in result["certainty_basis"]


def test_financial_amount_parser_skips_percentages_before_actual_usd_amount():
    from radar.analyzer import extract_amount_details

    details = extract_amount_details(
        "投资金额：合作方按照各自50.5%和49.5%的持股比例出资合计不少于7,425万美元。"
        "其中上市公司以自有资金出资3,749.625万美元。"
    )
    assert details["investment_amount"] == "7,425万美元"
    assert details["currency"] == "USD"
    assert details["listed_company_contribution"] == "3,749.625万美元"
