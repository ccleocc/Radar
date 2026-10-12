from __future__ import annotations

from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "radar.db"
SAMPLE_DIR = APP_DIR / "samples"

CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF_BASE = "https://static.cninfo.com.cn/"
CNINFO_HOME = "https://www.cninfo.com.cn/new/index?lang=zh"

# 巨潮公告全文检索默认关键词。关键词清单可在界面中通过标签增删和启停。
DEFAULT_SEARCH_KEYWORDS = [
    "投资", "募集", "购置", "基地", "产能", "设立", "工厂", "厂房",
    "印尼", "泰国", "印度尼西亚", "越南", "马来西亚", "菲律宾",
    "新加坡", "缅甸", "柬埔寨", "老挝", "文莱", "东南亚", "海外", "东盟",
]
# 向旧模块保留别名，降低升级对外部脚本的影响。
REGION_QUERIES = DEFAULT_SEARCH_KEYWORDS

# UI 显示名 -> 巨潮查询入口标识。北交所通过深市入口 + plate=bj 查询。
CNINFO_COLUMNS = {
    "沪市（上交所）": "sse",
    "深市（深交所）": "szse",
    "北交所": "bj",
    "新三板": "third",
    "港股": "hke",
}

DEFAULTS = {
    "page_size": 30,
    "pages_per_keyword": 1,
    "max_pdfs": 60,
    "timeout_seconds": 15,
    "request_delay_seconds": 0.25,
    "max_pdf_mb": 20,
}

# 地域和建设信号用简明配置集中管理，后续修改不需要改主程序。
COUNTRY_ALIASES = {
    "印度尼西亚": ["印度尼西亚", "印尼", "Indonesia", "Indonesian"],
    "泰国": ["泰国", "Thailand", "Thai"],
    "越南": ["越南", "Vietnam", "Vietnamese"],
    "马来西亚": ["马来西亚", "Malaysia", "Malaysian"],
    "菲律宾": ["菲律宾", "Philippines", "Filipino"],
    "新加坡": ["新加坡", "Singapore"],
    "柬埔寨": ["柬埔寨", "Cambodia", "Cambodian"],
    "老挝": ["老挝", "Laos", "Lao PDR"],
    "缅甸": ["缅甸", "Myanmar", "Burma"],
    "文莱": ["文莱", "Brunei", "Brunei Darussalam"],
    "东南亚（国家未明确）": ["东南亚", "东盟", "ASEAN", "Southeast Asia", "South-East Asia"],
}

LOCATION_ALIASES = {
    "泰国罗勇府": ["罗勇府", "罗勇", "Rayong"],
    "泰国巴真府": ["巴真府", "Prachin Buri", "Prachinburi"],
    "泰国春武里府": ["春武里府", "Chonburi", "Chon Buri"],
    "泰国北柳府": ["北柳府", "Chachoengsao"],
    "泰国东部经济走廊": ["东部经济走廊", "EEC", "Eastern Economic Corridor"],
    "越南北宁省": ["北宁省", "Bac Ninh"],
    "越南海防市": ["海防市", "Hai Phong"],
    "越南同奈省": ["同奈省", "Dong Nai"],
    "越南平阳省": ["平阳省", "Binh Duong"],
    "印尼西爪哇省": ["西爪哇省", "West Java", "Jawa Barat"],
    "印尼中爪哇省": ["中爪哇省", "Central Java", "Jawa Tengah"],
    "印尼东爪哇省": ["东爪哇省", "East Java", "Jawa Timur"],
    "印尼雅加达": ["雅加达", "Jakarta", "DKI Jakarta"],
    "印尼廖内群岛省": ["廖内群岛", "Riau Islands", "Kepulauan Riau"],
    "马来西亚柔佛州": ["柔佛州", "Johor"],
    "马来西亚槟城州": ["槟城", "Penang"],
}

INVESTMENT_TERMS = [
    "境外投资", "海外投资", "对外投资", "投资", "拟投资", "投资建设", "投资项目", "项目投资",
    "战略投资", "投资意向", "增资", "新设子公司", "设立子公司", "合资", "合作协议",
    "意向协议", "签署协议", "投资布局", "拟赴海外投资", "拟赴东南亚投资", "落地", "布局东南亚", "海外布局", "国际化布局",
    "投资计划", "项目规划", "拟建", "计划建设", "规划建设", "拟建设", "建设计划",
]

CONSTRUCTION_TERMS = [
    "生产基地", "制造基地", "智能制造基地", "工厂", "厂房", "生产车间", "生产线",
    "产线", "工业园", "产业园", "仓储中心", "物流基地", "加工基地", "制造中心",
    "扩建", "扩产", "新建", "建厂", "新厂", "改建", "技改", "产能扩张", "新增产能", "购置土地", "土地购置",
    "厂区", "建设项目", "项目开工", "建筑工程", "厂房建设", "配套设施", "办公用房",
    "生产设施", "研发制造基地", "产业基地", "制造业基地", "工业基地", "新生产基地", "投产", "一期项目", "二期项目", "规划产能",
]

EARLY_STAGE_TERMS = [
    "意向", "拟", "计划", "规划", "可行性研究", "可研", "选址", "筹备", "论证",
    "洽谈", "签署谅解备忘录", "框架协议", "合作备忘录", "考察", "调研", "评估中",
]

FORMAL_STAGE_TERMS = [
    "董事会审议通过", "股东会审议通过", "正式签约", "签署合同", "签署协议", "项目备案",
    "取得批复", "开工建设", "正式开工", "已开工", "施工建设", "投入运营",
]

EXCLUSION_TERMS = [
    "纯贸易", "经销协议", "销售代理", "产品销售", "渠道合作", "采购订单", "销售合同",
    "股权转让", "股权收购", "收购股权", "收购股份", "收购企业股权", "收购公司股权", "资产收购", "金融投资", "证券投资", "基金投资",
]

# More specific signals used to distinguish an actual Southeast Asian production project
# from unrelated domestic construction items inside a long prospectus.
MANUFACTURING_TERMS = [
    "生产子公司", "生产型子公司", "制造子公司", "生产基地", "制造基地",
    "本地化生产", "开展生产", "产品的生产和销售", "生产和销售", "生产、销售",
    "生产制造", "生产经营", "制造业务", "加工制造", "生产运营",
    "制造中心", "生产车间", "生产线", "产能布局", "生产设施",
]

HARD_BUILD_TERMS = [
    "建设生产基地", "新建生产基地", "新建工厂", "新建厂房", "厂房建设",
    "工厂建设", "厂区建设", "新建厂区", "购置土地", "购买土地", "土地购置",
    "购买土地和厂房", "厂房及配套设施", "生产线建设", "新建生产线",
    "工业厂房", "生产车间建设", "施工招标", "设计招标", "EPC招标",
    "施工总承包", "建筑工程", "土建工程", "公用工程", "机电安装",
    "钢结构", "冷链基础设施",
]

# A country/region mention must be linked to a real action in the same local context.
# Merely appearing elsewhere in a 200-page fundraising prospectus is not sufficient.
REGIONAL_ACTION_TERMS = [
    "境外投资", "海外投资", "对外投资", "投资", "设立", "新设", "合资",
    "生产", "制造", "建设", "工厂", "厂房", "基地", "产能", "产线",
    "购置土地", "购买土地", "扩建", "建厂", "项目选址", "项目建设",
    "投资意向", "考察", "洽谈", "拟建", "规划建设", "本地化生产",
]

ENGINEERING_TERMS = [
    "厂房", "厂区", "办公楼", "办公用房", "仓库", "仓储", "机电安装", "生产线安装",
    "配套设施", "公用工程", "道路", "管网", "污水处理", "变电站", "动力站", "空压站",
    "工程建设", "建筑工程", "EPC", "施工总承包", "设计施工", "钢结构", "土建", "装修",
]
