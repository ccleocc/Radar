from __future__ import annotations

import html
import re
from datetime import date, datetime
from typing import Any

from .config import (
    COUNTRY_ALIASES, LOCATION_ALIASES, INVESTMENT_TERMS, CONSTRUCTION_TERMS,
    EARLY_STAGE_TERMS, FORMAL_STAGE_TERMS, EXCLUSION_TERMS, ENGINEERING_TERMS,
    MANUFACTURING_TERMS, HARD_BUILD_TERMS, REGIONAL_ACTION_TERMS,
)

_SPACE_RE = re.compile(r"\s+")


def clean_announcement_title(title: str | None) -> str:
    """Remove CNINFO highlight markup such as <em> and decode HTML entities."""
    value = html.unescape(str(title or ""))
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def compact_text(text: str | None) -> str:
    value = normalize_text(text)
    # PDF text extractors often insert artificial spaces within Chinese words (e.g. 机 械).
    # Join CJK-to-CJK spaces while preserving spaces between Chinese and Latin/digits.
    value = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)
    return _SPACE_RE.sub(" ", value).strip()


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _find_aliases(text: str, mapping: dict[str, list[str]]) -> list[str]:
    found: list[str] = []
    for canonical, aliases in mapping.items():
        for alias in sorted(aliases, key=len, reverse=True):
            # Latin aliases need word boundaries to reduce false positives (e.g. "Thai" inside another word).
            if re.search(r"[A-Za-z]", alias):
                pattern = r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])"
            else:
                pattern = re.escape(alias)
            if re.search(pattern, text, re.IGNORECASE):
                found.append(canonical)
                break
    return _unique(found)


def _find_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term.lower() in text.lower()]


def _label_value(text: str, labels: list[str], max_len: int = 120) -> str:
    """Find a table-like label followed by a value, tolerating PDF line breaks."""
    flat = compact_text(text)
    for label in sorted(labels, key=len, reverse=True):
        pattern = re.escape(label) + r"\s*[:：]?\s*([^。；;]{2," + str(max_len) + r"})"
        m = re.search(pattern, flat)
        if m:
            value = m.group(1).strip(" ：:，,\t")
            # Stop at the next likely table label to avoid joining adjacent fields.
            value = re.split(r"(?:项目总投资金额|上市公司投资金额|项目建设期|预计开工时间|建设地点|项目主要内容|投资金额|项目进展情况)", value)[0]
            return value[:max_len].strip()
    return ""


def extract_project_name(title: str, text: str) -> str:
    """Use the official announcement title as the project-record label.

    Project names in disclosure body text are too inconsistent for dependable
    regex extraction. Keeping the exact title preserves the source wording and
    makes each project record traceable to its announcement.
    """
    value = clean_announcement_title(title)
    return value or "公告标题待核实"


def extract_countries_and_location(text: str) -> tuple[list[str], str]:
    # Prefer the explicit construction-site field. A subsidiary address or fund vehicle
    # may mention another Southeast Asian country, which should not become the project site.
    explicit_location = _label_value(text, ["建设地点", "项目建设地点", "项目所在地", "建设地址", "项目选址"], 100)
    explicit_location = re.split(r"(?:项目总投资金额|项目总投资|上市公司投资金额|项目建设期|预计开工时间|项目主要内容)", explicit_location)[0]
    explicit_location = re.sub(r"^[为：:、\s]+", "", explicit_location).strip(" ，,；;")
    if explicit_location and len(explicit_location) > 80:
        explicit_location = ""

    loc_hits = _find_aliases(explicit_location, LOCATION_ALIASES) if explicit_location else []
    all_countries = _find_aliases(text, COUNTRY_ALIASES)
    inferred = {
        "泰国罗勇府": "泰国", "泰国巴真府": "泰国", "泰国春武里府": "泰国", "泰国北柳府": "泰国", "泰国东部经济走廊": "泰国",
        "越南北宁省": "越南", "越南海防市": "越南", "越南同奈省": "越南", "越南平阳省": "越南",
        "印尼西爪哇省": "印度尼西亚", "印尼中爪哇省": "印度尼西亚", "印尼东爪哇省": "印度尼西亚", "印尼雅加达": "印度尼西亚", "印尼廖内群岛省": "印度尼西亚",
        "马来西亚柔佛州": "马来西亚", "马来西亚槟城州": "马来西亚",
    }
    if explicit_location:
        countries = []
        # Use the country appearing in the explicit site field and location aliases inferred from it.
        for country, aliases in COUNTRY_ALIASES.items():
            if any((re.search(r"(?<![A-Za-z])" + re.escape(a) + r"(?![A-Za-z])", explicit_location, re.I) if re.search(r"[A-Za-z]", a) else a in explicit_location) for a in aliases):
                if country not in countries:
                    countries.append(country)
        for loc in loc_hits:
            country = inferred.get(loc)
            if country and country not in countries:
                countries.append(country)
        # If a well-formed site field states e.g. "泰国罗勇府" but country alias matching missed it.
        if not countries:
            for country, loc_prefix in [("泰国", "泰国"), ("越南", "越南"), ("印度尼西亚", "印尼"), ("马来西亚", "马来西亚")]:
                if explicit_location.startswith(loc_prefix):
                    countries.append(country)
                    break
        location = "；".join(loc_hits) if loc_hits else explicit_location
        if countries == ["东南亚（国家未明确）"] and not loc_hits:
            location = "国家/地区待核实"
        if countries:
            return countries, location
    # If the公告没有明确施工地点，再保留全文中出现的多个国家作为待核实范围。
    locations = _find_aliases(text, LOCATION_ALIASES)
    for loc in locations:
        country = inferred.get(loc)
        if country and country not in all_countries:
            all_countries.append(country)
    location = "；".join(locations[:4])
    if not location:
        location = "项目具体地点待核实" if all_countries else ""
    # Put concrete named countries first; generic Southeast Asia should not dominate.
    all_countries = [c for c in all_countries if c != "东南亚（国家未明确）"] + (["东南亚（国家未明确）"] if "东南亚（国家未明确）" in all_countries and not any(c != "东南亚（国家未明确）" for c in all_countries) else [])
    return _unique(all_countries), location


def _amount_currency(unit: str, prefix: str = "", suffix: str = "") -> tuple[str, str]:
    around = f"{prefix} {unit} {suffix}"
    if any(token in around for token in ("美元", "美金", "USD", "US$", "$")):
        return "USD", unit or "（单位待核实）"
    if "泰铢" in around or "THB" in around:
        return "THB", unit or "（单位待核实）"
    if "越南盾" in around or "VND" in around:
        return "VND", unit or "（单位待核实）"
    if "新元" in around or "SGD" in around:
        return "SGD", unit or "（单位待核实）"
    if "人民币" in around or "CNY" in around or unit in {"万亿", "亿元", "亿", "万元", "万", "元", "亿元人民币", "万元人民币"}:
        return "CNY（公告口径/待核实）", unit or "（单位待核实）"
    return "", unit or "（单位待核实）"


def extract_amount_details(text: str) -> dict[str, Any]:
    """Extract likely project investment and company contribution amounts separately.

    Important safeguards:
    - Unit alternatives are longest-first, so 万美元/亿美元 are not truncated to 万/亿.
    - Explicit project-total labels outrank general investment labels and company contribution.
    - Registered capital, revenue, profit, financing proceeds and other unrelated figures
      are not used as project investment unless a supported investment label is nearby.
    - When a disclosure contains multiple amounts, the selected amount is accompanied by
      an evidence snippet and alternative candidates for manual verification.
    """
    flat = compact_text(text)
    unit_tokens = sorted([
        "万亿美元", "亿美元", "万美元", "万泰铢", "万越南盾", "万新元",
        "亿元人民币", "万元人民币", "人民币", "美金", "美元", "泰铢", "越南盾", "新元",
        "US$", "USD", "CNY", "THB", "VND", "SGD", "万亿", "亿元", "万元", "亿", "万", "元", "$",
    ], key=len, reverse=True)
    unit_pattern = "|".join(re.escape(token) for token in unit_tokens)
    number_pattern = r"(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?"
    amount_pattern = re.compile(rf"(?P<number>{number_pattern})\s*(?P<unit>{unit_pattern})?", re.I)

    # (score, bucket, display, currency, evidence, position)
    candidates: list[tuple[int, str, str, str, str, int]] = []
    label_groups: list[tuple[str, int, list[str]]] = [
        ("project_total", 120, [
            "项目总投资金额", "项目总投资额", "项目总投资", "项目投资总额", "项目总额",
            "总投资金额", "总投资额", "总投资", "投资总额", "预计总投资", "计划总投资",
        ]),
        ("general_investment", 85, [
            "本次投资金额", "拟投资金额", "投资预算", "投资金额", "对外投资金额",
            "投资规模", "预计投资额", "计划投资额", "拟投资额", "投资额", "投资约",
        ]),
        ("company_contribution", 55, [
            "上市公司拟出资金额", "上市公司出资金额", "上市公司出资额", "上市公司投资金额",
            "上市公司拟出资", "上市公司出资", "公司拟出资金额", "公司出资金额",
            "香港安井以公司自有资金出资", "公司以自有资金出资", "公司自有资金出资",
            "以公司自有资金出资", "本公司出资", "公司拟出资", "公司出资",
            "本次出资金额", "拟出资金额", "认缴出资额", "拟出资",
            "本次增资金额", "增资金额",
        ]),
    ]

    seen: set[tuple[int, str, str]] = set()
    for bucket, base_score, labels in label_groups:
        for label in sorted(labels, key=len, reverse=True):
            for label_match in re.finditer(re.escape(label), flat, re.I):
                # The value can contain explanatory text/percentages before the monetary
                # amount (e.g. ownership shares 50.5%/49.5%, followed by 7,425万美元).
                # Iterate until the first plausible monetary figure instead of blindly taking
                # the first number.
                value_window = flat[label_match.end():label_match.end() + 180]
                stop = re.search(r"[。；;\n]", value_window)
                if stop:
                    value_window = value_window[:stop.start()]
                amount_match = None
                gap = ""
                context_unit = ""
                equivalent_foreign_currency = False
                effective_unit = ""
                currency = ""
                display_unit = ""
                before_number = ""
                after_unit = ""
                for possible in amount_pattern.finditer(value_window):
                    possible_gap = value_window[:possible.start()]
                    if len(possible_gap) > 105:
                        continue
                    possible_unit = possible.group("unit") or ""
                    possible_after = value_window[possible.end():possible.end() + 16]
                    # Exclude ratios, headcount, area, production counts and other non-money figures.
                    if re.match(r"\s*[%％]", possible_after) or re.match(r"\s*(?:股|人|套|台|吨|件|个|家|户|亩|平方米|平米|公里|千瓦|MW|GW|年|个月|天)", possible_after, re.I):
                        continue
                    possible_before = value_window[max(0, possible.start() - 24):possible.start()]
                    possible_context_unit = ""
                    possible_equiv = False
                    if not possible_unit:
                        unit_alternation = "|".join(re.escape(x) for x in unit_tokens)
                        unit_context_patterns = [
                            rf"[（(]\s*(?P<unit>{unit_alternation})\s*(?P<equiv>或等值外币)?\s*[）)]",
                            rf"单位[：:]?\s*(?P<unit>{unit_alternation})(?P<equiv>或等值外币)?",
                        ]
                        for unit_context_pattern in unit_context_patterns:
                            unit_context = re.search(unit_context_pattern, possible_gap, re.I)
                            if unit_context:
                                possible_context_unit = unit_context.group("unit")
                                possible_equiv = bool(unit_context.groupdict().get("equiv"))
                                break
                    possible_effective_unit = possible_unit or possible_context_unit
                    possible_currency, possible_display_unit = _amount_currency(
                        possible_effective_unit, possible_before + " " + possible_gap, possible_after
                    )
                    explicit_currency = any(token in (possible_before + " " + possible_gap + " " + possible_after)
                                            for token in ("美元", "美金", "人民币", "US$", "USD", "CNY", "泰铢", "越南盾", "新元"))
                    # A bare number is accepted only where the nearby declared unit/currency is clear.
                    if not possible_effective_unit and not explicit_currency:
                        continue
                    amount_match = possible
                    gap = possible_gap
                    context_unit = possible_context_unit
                    equivalent_foreign_currency = possible_equiv
                    effective_unit = possible_effective_unit
                    currency = possible_currency
                    display_unit = possible_display_unit
                    before_number = possible_before
                    after_unit = possible_after
                    break
                if not amount_match:
                    continue
                raw_number = amount_match.group("number").replace("，", ",")
                unit = amount_match.group("unit") or ""
                absolute_start = label_match.end() + amount_match.start()
                after_unit = value_window[amount_match.end():amount_match.end() + 12]
                if not effective_unit:
                    display_unit = "（单位待核实）"
                # Preserve explicit currency even when it appears before the number,
                # e.g. “美元4,000万”; never render that as an implied RMB amount.
                if currency == "USD" and effective_unit in {"万", "万元"} and not any(x in effective_unit for x in ("美元", "美金", "USD", "US$")):
                    display_unit = "万美元"
                elif currency == "USD" and effective_unit in {"亿", "亿元"}:
                    display_unit = "亿美元"
                elif currency == "THB" and effective_unit in {"万", "万元"}:
                    display_unit = "万泰铢"
                elif currency == "VND" and effective_unit in {"万", "万元"}:
                    display_unit = "万越南盾"
                elif currency == "SGD" and effective_unit in {"万", "万元"}:
                    display_unit = "万新元"
                if equivalent_foreign_currency:
                    display_unit += "（或等值外币）"
                    if currency == "CNY（公告口径/待核实）":
                        currency = "CNY/等值外币（公告口径）"
                # Explicit currency in unit is authoritative. Do not translate USD into RMB.
                display = f"{raw_number}{display_unit}"
                evidence_start = max(0, label_match.start() - 35)
                evidence_end = min(len(flat), absolute_start + len(amount_match.group(0)) + 35)
                evidence = flat[evidence_start:evidence_end]
                # Small bonus for explicit currency and nearby construction/project context.
                effective_bucket = bucket
                # Do not let the generic substring “投资金额” steal a value explicitly
                # labeled as listed-company contribution.
                nearby_prefix = flat[max(0, label_match.start() - 20):label_match.start()]
                if bucket == "general_investment" and any(x in nearby_prefix for x in ("上市公司", "公司拟出资", "公司出资", "本次出资")):
                    effective_bucket = "company_contribution"
                score = base_score - (25 if effective_bucket == "company_contribution" and bucket != "company_contribution" else 0)
                if any(x in (before_number + unit + after_unit) for x in ("美元", "美金", "人民币", "US$", "USD", "CNY")):
                    score += 3
                if bucket == "project_total" and any(x in flat[max(0, label_match.start() - 60):label_match.end() + 100] for x in ("工厂", "厂房", "生产基地", "建设项目", "新建", "扩建")):
                    score += 2
                key = (absolute_start, raw_number, unit)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append((score, effective_bucket, display, currency, evidence, absolute_start))

    # Additional pattern for concise language without a formal amount label, e.g.
    # “拟投资约 4,000 万美元在泰国建设工厂”。
    fallback_labels = ["拟以", "投资建设", "拟投资", "计划投资", "投资约", "投资不超过", "不超过"]
    for label in fallback_labels:
        for label_match in re.finditer(re.escape(label), flat):
            value_window = flat[label_match.end():label_match.end() + 65]
            stop = re.search(r"[。；;\n]", value_window)
            if stop:
                value_window = value_window[:stop.start()]
            m = None
            for possible in amount_pattern.finditer(value_window):
                if len(value_window[:possible.start()]) > 45:
                    continue
                possible_after = value_window[possible.end():possible.end() + 16]
                if re.match(r"\s*[%％]", possible_after) or re.match(r"\s*(?:股|人|套|台|吨|件|个|家|户|亩|平方米|平米|公里|千瓦|MW|GW|年|个月|天)", possible_after, re.I):
                    continue
                possible_unit = possible.group("unit") or ""
                possible_before = value_window[max(0, possible.start() - 18):possible.start()]
                explicit_currency = any(token in (possible_before + " " + possible_after) for token in ("美元", "美金", "人民币", "US$", "USD", "CNY", "泰铢", "越南盾", "新元"))
                if not possible_unit and not explicit_currency:
                    continue
                m = possible
                break
            if not m:
                continue
            raw_number = m.group("number").replace("，", ",")
            unit = m.group("unit") or ""
            before_number = value_window[max(0, m.start() - 18):m.start()]
            after_unit = value_window[m.end():m.end() + 12]
            currency, display_unit = _amount_currency(unit, before_number, after_unit)
            if currency == "USD" and unit in {"万", "万元"}:
                display_unit = "万美元"
            elif currency == "USD" and unit in {"亿", "亿元"}:
                display_unit = "亿美元"
            elif currency == "THB" and unit in {"万", "万元"}:
                display_unit = "万泰铢"
            elif currency == "VND" and unit in {"万", "万元"}:
                display_unit = "万越南盾"
            elif currency == "SGD" and unit in {"万", "万元"}:
                display_unit = "万新元"
            display = f"{raw_number}{display_unit}"
            absolute_start = label_match.end() + m.start()
            evidence = flat[max(0, label_match.start() - 25):min(len(flat), absolute_start + len(m.group(0)) + 35)]
            key = (absolute_start, raw_number, unit)
            if key not in seen:
                seen.add(key)
                candidates.append((45, "general_investment", display, currency, evidence, absolute_start))

    if not candidates:
        return {
            "investment_amount": "", "currency": "", "project_total_investment": "",
            "listed_company_contribution": "", "amount_evidence": "", "amount_candidates": [],
        }

    # Select project-level total if present; otherwise use the best general investment amount.
    # Company contribution is a separate field and is only a last resort for the headline amount.
    project_totals = [x for x in candidates if x[1] == "project_total"]
    general = [x for x in candidates if x[1] == "general_investment"]
    company = [x for x in candidates if x[1] == "company_contribution"]
    ranked_main = sorted(project_totals or general or company, key=lambda x: (x[0], -x[5]), reverse=True)
    chosen = ranked_main[0]
    project_total = sorted(project_totals, key=lambda x: (x[0], -x[5]), reverse=True)[0][2] if project_totals else ""
    company_contribution = sorted(company, key=lambda x: (x[0], -x[5]), reverse=True)[0][2] if company else ""
    alternatives = []
    for score, bucket, display, currency, evidence, pos in sorted(candidates, key=lambda x: (x[0], -x[5]), reverse=True):
        alternatives.append({"类别": bucket, "金额": display, "币种": currency, "依据": evidence, "位置": pos})
    return {
        "investment_amount": chosen[2], "currency": chosen[3],
        "project_total_investment": project_total,
        "listed_company_contribution": company_contribution,
        "amount_evidence": chosen[4], "amount_candidates": alternatives[:12],
    }


def extract_investment_amount(text: str) -> tuple[str, str]:
    """Backward-compatible wrapper returning headline amount and currency."""
    details = extract_amount_details(text)
    return str(details.get("investment_amount") or ""), str(details.get("currency") or "")


def extract_expected_start(text: str) -> str:
    flat = compact_text(text)
    patterns = [
        r"(?:预计开工时间|计划开工时间|预计开工日期|计划开工日期|预计开工|计划开工)[：:\s]*(?:为|是)?\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
        r"(?:预计于|计划于|将于)(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)(?:开工|动工|建设)",
    ]
    for pattern in patterns:
        m = re.search(pattern, flat)
        if m:
            return normalize_date_string(m.group(1))
    return ""


def normalize_date_string(value: str) -> str:
    value = value.replace("年", "-").replace("月", "-").replace("日", "")
    value = value.replace("/", "-").replace(".", "-")
    parts = value.split("-")
    if len(parts) == 3:
        try:
            return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
        except ValueError:
            return value
    return value


def extract_construction_period(text: str) -> str:
    flat = compact_text(text)
    # In many disclosure tables the unit is in the label: "项目建设期（月）18".
    m = re.search(r"(?:项目建设期|建设周期|计划建设期|建设期限)[：:\s]*(?:为|约|预计)?\s*(?:[（(]?(个月|月|年|天|日)[）)]?\s*)?(\d{1,3})\s*(个月|月|年|天|日)?", flat)
    if m:
        unit = m.group(3) or m.group(1) or "月"
        return m.group(2) + unit
    return ""


def extract_project_subject(text: str, listed_company: str) -> str:
    flat = compact_text(text)
    subject = _label_value(text, ["本项目投资主体为", "项目投资主体", "项目实施主体为", "项目实施主体", "投资主体为"], 100)
    if subject:
        subject = re.split(r"(?:，|,|；|;|。|不涉及|本次增资)", subject)[0].strip()
        subject = re.sub(r"^(?:为|是|：|:)\s*", "", subject).strip()
        if subject and len(subject) <= 80:
            return subject
    return listed_company


def extract_partner(text: str) -> str:
    flat = compact_text(text)
    patterns = [
        r"与([^，。；;]{2,50}?)(?:共同投资|合资设立|合资建设|签署.*?协议|签订.*?协议|开展合作|合作建设)",
        r"(?:合作方|合资方|合作伙伴)[：:\s]*([^，。；;]{2,60})",
        r"与([^，。；;]{2,45}?)(?:成立合资公司|共同设立)",
    ]
    for pattern in patterns:
        m = re.search(pattern, flat)
        if m:
            partner = m.group(1).strip()
            if partner and partner != "公司":
                return partner
    return "公告未明确"


def classify_project_type(text: str) -> str:
    terms = {
        "工业厂房/制造基地": ["工厂", "厂房", "生产基地", "制造基地", "智能制造", "制造中心", "生产车间"],
        "产业园/工业园": ["产业园", "工业园", "园区", "产业基地"],
        "仓储物流设施": ["仓库", "仓储", "物流基地", "物流中心"],
        "新能源/电力设施": ["光伏", "风电", "储能", "电站", "电池基地", "电芯", "新能源基地"],
        "研发/办公设施": ["研发中心", "研发基地", "办公楼", "办公用房"],
        "其他实体投资项目": ["建设项目", "项目建设", "投资项目", "产能扩张"],
    }
    found = [label for label, keys in terms.items() if any(k in text for k in keys)]
    return "；".join(found[:3]) if found else "实体投资方向待核实"


def extract_region_context(text: str, countries: list[str], title: str = "", radius: int = 700) -> str:
    """Build a bounded context from occurrences of the detected SEA region/country.

    This prevents unrelated construction language in a long prospectus from boosting
    a Southeast Asian opportunity when the construction disclosure actually refers to
    a domestic project. Repeated local contexts are de-duplicated and kept in source order.
    """
    flat = compact_text(text)
    if not countries:
        return compact_text(title)

    aliases: list[str] = []
    for country in countries:
        aliases.extend(COUNTRY_ALIASES.get(country, []))
    aliases = sorted(_unique(aliases), key=len, reverse=True)
    positions: list[int] = []
    for alias in aliases:
        if re.search(r"[A-Za-z]", alias):
            pattern = re.compile(r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])", re.I)
        else:
            pattern = re.compile(re.escape(alias), re.I)
        positions.extend(m.start() for m in pattern.finditer(flat))
    positions = sorted(set(positions))

    chunks: list[tuple[int, int, str]] = []
    for pos in positions[:160]:
        start, end = max(0, pos - radius), min(len(flat), pos + radius)
        snippet = flat[start:end].strip()
        if not snippet:
            continue
        if chunks and start <= chunks[-1][1] + 45:
            old_start, old_end, old_text = chunks[-1]
            new_end = max(old_end, end)
            chunks[-1] = (old_start, new_end, flat[old_start:new_end].strip())
        else:
            chunks.append((start, end, snippet))
    body_context = "\n".join(chunk[2] for chunk in chunks[:60])
    title_clean = compact_text(title)
    if title_clean and title_clean not in body_context:
        body_context = title_clean + "\n" + body_context
    return body_context[:60000]


def _has_regional_project_link(countries: list[str], regional_text: str) -> tuple[bool, list[str]]:
    """Require a country-linked investment/manufacturing/action signal, not a stray country word."""
    if not countries:
        return False, []
    link_source = re.sub(
        r"(?:不涉及|未涉及|不含|不包括|没有|无|无需|不需要|不产生)(?:\s|[、，,及和与/]?)(?:工厂|厂房|生产线|生产基地|制造基地|建设项目|工程建设|厂区|产业园|工业园|生产设施|建设工程)[^。；;]{0,80}",
        " ", regional_text,
    )
    hits = _find_terms(link_source, REGIONAL_ACTION_TERMS)
    # Generic disclosure/risk language is not enough by itself: require a region-linked
    # action more specific than generic "计划" or "项目".
    strong_hits = [h for h in hits if h not in {"投资", "设立", "建设"}]
    return bool(strong_hits or any(h in link_source for h in ("投资", "设立", "建设"))), hits


def _has_production_setup(regional_text: str) -> tuple[bool, list[str]]:
    hits = _find_terms(regional_text, MANUFACTURING_TERMS)
    entity_terms = ("生产子公司", "生产型子公司", "制造子公司", "生产基地", "制造基地", "生产工厂")
    operation_terms = ("产品的生产和销售", "生产和销售", "生产、销售", "开展生产", "生产制造", "生产经营", "制造业务", "加工制造")
    has_entity = any(term in regional_text for term in entity_terms)
    has_operation = any(term in regional_text for term in operation_terms)
    # A production subsidiary plus a stated manufacturing/production activity is a
    # strong engineering lead even when greenfield construction vs. leasing is not stated.
    return bool(hits and (has_entity or has_operation)), hits


def analyze_announcement(
    title: str,
    text: str,
    stock_code: str = "",
    stock_name: str = "",
    pub_date: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    body = normalize_text(text)
    flat = compact_text(body)
    title = compact_text(clean_announcement_title(title))
    full = f"{title}\n{flat}"
    countries, location = extract_countries_and_location(full)
    regional_text = extract_region_context(flat, countries, title)
    has_regional_nexus, regional_action_hits = _has_regional_project_link(countries, regional_text)

    # Analyze project evidence only in contexts linked to the declared Southeast Asian
    # geography. This is the main guard against false positives from long bond prospectuses.
    signal_source = regional_text if has_regional_nexus else title
    signal_text = re.sub(
        r"(?:不涉及|未涉及|不含|不包括|没有|无|无需|不需要|不产生)(?:\s|[、，,及和与/]?)(?:工厂|厂房|生产线|生产基地|制造基地|建设项目|工程建设|厂区|产业园|工业园|生产设施|建设工程)[^。；;]{0,80}",
        " ", signal_source,
    )
    investment_hits = _find_terms(signal_source, INVESTMENT_TERMS)
    construction_hits = _find_terms(signal_text, CONSTRUCTION_TERMS)
    early_hits = _find_terms(signal_source, EARLY_STAGE_TERMS)
    formal_hits = _find_terms(signal_source, FORMAL_STAGE_TERMS)
    engineering_hits = _find_terms(signal_text, ENGINEERING_TERMS)
    manufacturing_setup, manufacturing_hits = _has_production_setup(signal_text)
    hard_build_hits = _find_terms(signal_text, HARD_BUILD_TERMS)
    exclusion_hits = _find_terms(signal_source, EXCLUSION_TERMS)

    # The project must be region-linked first. A local factory project inside a domestic
    # fundraising prospectus must never become a Southeast Asian engineering lead.
    intent_hits = investment_hits + construction_hits + [term for term in early_hits if term != "拟"]
    has_region = bool(countries)
    likely_candidate = has_region and has_regional_nexus and bool(intent_hits or manufacturing_setup)

    finance_or_sales_only = bool(exclusion_hits) or any(k in signal_source for k in ["收购", "并购", "销售网络", "渠道代理", "经销商"])
    physical_or_manufacturing = bool(construction_hits or engineering_hits or manufacturing_hits or hard_build_hits)
    finance_only_exclusion = finance_or_sales_only and not physical_or_manufacturing
    if finance_only_exclusion:
        likely_candidate = False

    # Amounts and construction scope are intentionally extracted from region-linked text,
    # not from unrelated parts of an entire prospectus.
    amount_source = regional_text if has_region and has_regional_nexus else ""
    amount_details = extract_amount_details(amount_source) if amount_source else {
        "investment_amount": "", "currency": "", "project_total_investment": "",
        "listed_company_contribution": "", "amount_evidence": "", "amount_candidates": [],
    }
    investment_amount = amount_details.get("investment_amount", "")
    currency = amount_details.get("currency", "")
    project_subject = extract_project_subject(regional_text if has_region else body, stock_name or title)
    partner = extract_partner(regional_text if has_region else body)
    expected_start = extract_expected_start(regional_text) if amount_source else ""
    construction_period = extract_construction_period(regional_text) if amount_source else ""
    project_type = classify_project_type(regional_text if has_region else title)

    construction_scope = _label_value(regional_text, ["项目主要内容", "主要建设内容", "项目建设内容", "建设内容"], 260) if amount_source else ""
    construction_scope = re.split(r"(?:建设地点|项目总投资金额|项目总投资|上市公司投资金额|项目建设期|预计开工时间|预计投资收益率)", construction_scope)[0]
    if not construction_scope or len(construction_scope) < 8:
        scope_snippets: list[str] = []
        for sentence in re.split(r"(?<=[。；;])|\n", regional_text if amount_source else ""):
            s = compact_text(sentence)
            if s and any(term in s for term in ENGINEERING_TERMS + MANUFACTURING_TERMS + HARD_BUILD_TERMS + ["新建", "扩建", "规划建设", "购置土地", "冷链基础设施"]):
                if len(s) > 12:
                    scope_snippets.append(s[:240])
        construction_scope = "；".join(_unique(scope_snippets)[:4])
    if not construction_scope and manufacturing_setup:
        construction_scope = "拟设立生产型子公司并开展产品生产；公告提及生产经营需求，但厂房新建、租赁或改造路径待核实"
    construction_scope = construction_scope or ("公告尚未明确具体工程范围" if likely_candidate else "未发现与东南亚项目相关联的实体建设范围")

    stage_source = regional_text if amount_source else title
    stage = detect_stage(stage_source, expected_start)
    if not likely_candidate or not has_region or not has_regional_nexus:
        certainty, certainty_score, certainty_basis = "不适用", 0, "未发现与东南亚国家/地区相绑定的合格投资/生产建设商机；不得使用公告中无关的国内建设内容推断海外商机。"
        opportunity_grade, opportunity_score = "— 不纳入", 0
        opportunity_basis = "未形成东南亚项目关联证据，或建设信号属于公告其他事项；不作为东南亚工程商机。"
    else:
        certainty, certainty_score, certainty_basis = score_certainty(
            regional_text, countries, location, investment_amount, expected_start, construction_period,
            investment_hits, construction_hits, formal_hits, early_hits,
        )
        opportunity_grade, opportunity_score, opportunity_basis = score_opportunity(
            regional_text, countries, location, investment_amount, expected_start, construction_period,
            construction_hits, engineering_hits, formal_hits, manufacturing_hits, hard_build_hits,
            manufacturing_setup, likely_candidate,
        )

    evidence = build_evidence(regional_text if has_region else title, countries, investment_hits, construction_hits, formal_hits, early_hits, engineering_hits)
    if manufacturing_hits:
        evidence = (evidence + "；生产经营信号：" + "、".join(manufacturing_hits[:6])).strip("；")
    if hard_build_hits:
        evidence = (evidence + "；明确建设信号：" + "、".join(hard_build_hits[:6])).strip("；")
    if not has_region:
        evidence = "未识别到东南亚国家/地区；正文中的国内厂房、募集资金或募投项目不作为本系统目标商机。"
    confidence = round(min(0.98, 0.38 + len(countries) * 0.08 + min(len(investment_hits), 4) * 0.06 + min(len(construction_hits), 5) * 0.07 + min(len(formal_hits), 3) * 0.05), 2)
    hit_signals = {
        "地域": countries,
        "地域关联动作": regional_action_hits,
        "投资/扩张": investment_hits,
        "建设/产能": construction_hits,
        "生产经营主体": manufacturing_hits,
        "明确工程建设": hard_build_hits,
        "阶段/规划": early_hits + formal_hits,
        "工程范围": engineering_hits,
        "排除信号": exclusion_hits,
        "东南亚项目关联": ["已关联" if has_regional_nexus else "未关联"],
    }

    return {
        "stock_code": stock_code.strip(),
        "stock_name": stock_name.strip(),
        "title": title,
        "pub_date": pub_date,
        "source_url": source_url,
        "project_name": extract_project_name(title, body),
        "countries": countries,
        "location": location,
        "investment_subject": project_subject or stock_name or "待核实",
        "partner": partner,
        "project_type": project_type,
        "investment_amount": investment_amount,
        "currency": currency,
        "project_total_investment": amount_details.get("project_total_investment", ""),
        "listed_company_contribution": amount_details.get("listed_company_contribution", ""),
        "amount_evidence": amount_details.get("amount_evidence", ""),
        "amount_candidates": amount_details.get("amount_candidates", []),
        "construction_scope": construction_scope,
        "project_stage": stage,
        "expected_start": expected_start,
        "construction_period": construction_period,
        "construction_certainty": certainty,
        "certainty_score": certainty_score,
        "certainty_basis": certainty_basis,
        "opportunity_grade": opportunity_grade,
        "opportunity_score": opportunity_score,
        "opportunity_basis": opportunity_basis,
        "confidence": confidence,
        "evidence": evidence,
        "hit_signals": hit_signals,
        "included_as_candidate": likely_candidate,
        "has_regional_nexus": has_regional_nexus,
        "regional_context_excerpt": regional_text[:2400],
        "excluded_reason": (
            "疑似纯销售/股权/金融事项，未发现实体建设或生产制造需求" if finance_only_exclusion
            else "未发现东南亚项目所在地或与东南亚投资/建设关联的证据" if not has_region or not has_regional_nexus
            else ""
        ),
        "body_chars": len(body),
    }

def detect_stage(text: str, expected_start: str = "") -> str:
    t = text
    # Use the project's own progress section when present; avoid mistaking subsidiary land
    # addresses or generic risk wording elsewhere in the announcement for current progress.
    m = re.search(r"项目目前进展情况.{0,15}?截至.{0,120}?(?:尚处于|处于|目前为|目前处于)([^。；;]{2,45}(?:阶段|状态|过程中))", t)
    if m:
        stage = compact_text(m.group(0))
        if "前期筹备" in stage:
            return "前期筹备阶段" + ("（已披露计划开工日期）" if expected_start else "")
        return compact_text(m.group(1)).strip(" ，,。；;")
    if any(k in t for k in ["尚处于前期筹备阶段", "目前处于前期筹备阶段", "前期筹备阶段"]):
        return "前期筹备阶段" + ("（已披露计划开工日期）" if expected_start else "")
    if any(k in t for k in ["已竣工", "已建成投产", "已正式投产", "已投入运营", "已实现投产", "已经投产"]):
        return "已建成/已投产（仍需判断是否有后续扩建）"
    if re.search(r"(?:已于|已在|目前已|现已|已经|已)(?:.{0,12})?(?:正式)?(?:开工建设|开工|动工|开始施工)", t):
        return "已开工建设"
    if any(k in t for k in ["施工招标", "工程招标", "EPC招标", "施工总承包招标", "招标公告"]):
        return "已进入招标/采购阶段"
    if any(k in t for k in ["取得施工许可证", "建设工程规划许可证", "施工许可"]):
        return "建设许可/开工准备"
    if any(k in t for k in ["正式签约", "签署协议", "签订协议", "签署合作协议", "合资协议"]):
        return "正式签约/合作落地"
    if any(k in t for k in ["董事会审议通过", "股东会审议通过", "董事会同意"]):
        return "董事会已审议/投资决策阶段" + ("（前期筹备，已披露计划开工日期）" if expected_start else "")
    if expected_start:
        return "已披露计划开工时间"
    if any(k in t for k in ["可行性研究", "可研", "正在选址", "选址", "前期筹备", "筹备阶段", "规划阶段"]):
        return "前期规划/筹备阶段"
    if any(k in t for k in ["拟投资", "意向投资", "计划投资", "拟建设", "计划建设", "框架协议", "合作意向"]):
        return "投资意向/规划阶段"
    return "阶段待核实"


def score_certainty(
    full: str, countries: list[str], location: str, amount: str, expected_start: str,
    period: str, investment_hits: list[str], construction_hits: list[str],
    formal_hits: list[str], early_hits: list[str],
) -> tuple[str, int, str]:
    score = 0
    factors: list[str] = []
    if countries:
        score += 1; factors.append("已识别东南亚国家/地区")
    if location and location not in {"国家/地区待核实", "项目具体地点待核实"}:
        score += 1; factors.append("披露较具体地点")
    if investment_hits:
        score += 1; factors.append("地域相关段落存在投资/扩张信号")
    if construction_hits:
        score += 2; factors.append("地域相关段落存在建设/产能信号")
    if amount:
        score += 1; factors.append("识别到地域相关投资金额")
    if expected_start:
        score += 1; factors.append("披露预计开工时间")
    if period:
        score += 1; factors.append("披露建设周期")
    if (("董事会" in full or "股东会" in full) and "审议通过" in full) or any(k in full for k in ["正式签约", "签署合同", "签署协议", "取得批复", "项目备案"]):
        score += 2; factors.append("地域相关段落存在正式决策/签约/审批信号")
    approvals_pending = bool(re.search(r"尚需(?:办理|获得|取得|履行|完成)|仍需(?:办理|获得|取得|履行)|尚待(?:审批|备案|许可)|审批(?:手续)?尚未完成|能否(?:顺利)?实施存在(?:一定的)?不确定性", full))
    if approvals_pending:
        score -= 2; factors.append("项目备案、许可或企业设立等前置程序尚待完成")
    if any(k in full for k in ["项目尚处于前期筹备", "前期筹备阶段", "仅为意向", "尚未确定", "正在论证", "根据市场需求和业务进展等具体情况实施"]):
        score -= 1; factors.append("项目处于早期或建设方案仍可调整阶段")
    if approvals_pending:
        label = "中" if score >= 2 else "低"
    elif score >= 8:
        label = "高"
    elif score >= 4:
        label = "中"
    else:
        label = "低"
    return label, score, "；".join(factors) if factors else "仅有初步地域/投资信号"


def score_opportunity(
    full: str, countries: list[str], location: str, amount: str, expected_start: str,
    period: str, construction_hits: list[str], engineering_hits: list[str], formal_hits: list[str],
    manufacturing_hits: list[str] | None = None, hard_build_hits: list[str] | None = None,
    manufacturing_setup: bool = False, candidate: bool = True,
) -> tuple[str, int, str]:
    manufacturing_hits = manufacturing_hits or []
    hard_build_hits = hard_build_hits or []
    if not countries or not candidate:
        return "— 不纳入", 0, "没有形成与东南亚实体投资/生产建设直接关联的证据。"

    score = 1  # region-linked project exists
    if construction_hits: score += 1
    if engineering_hits: score += 1
    if hard_build_hits: score += 3
    if manufacturing_setup and manufacturing_hits: score += 3
    if amount: score += 1
    if location and location not in {"国家/地区待核实", "项目具体地点待核实"}: score += 1
    if expected_start: score += 1
    if period: score += 1
    if formal_hits: score += 1

    if hard_build_hits:
        grade = "★★★ 高"
        basis = "地域相关原文明确披露实体建设/工程设施信号（" + "、".join(hard_build_hits[:5]) + "）；具备直接工程需求，具体工程边界仍需核实。"
    elif manufacturing_setup and manufacturing_hits:
        grade = "★★★ 高"
        basis = "地域相关原文同时显示生产型子公司/生产基地与实际生产制造活动（" + "、".join(manufacturing_hits[:5]) + "）；即使公告未明确新建或租赁路径，仍是值得跟踪的实体制造工程机会。需后续确认新建、租赁、改造及设备安装范围。"
    elif construction_hits or engineering_hits or manufacturing_hits:
        grade = "★★ 中"
        basis = "已出现与东南亚项目相关的部分建设/制造信号，但实体工程范围或实施路径尚不充分。"
    else:
        grade = "★ 观察"
        basis = "仅有东南亚投资/设立/考察等早期信号，尚无明确厂房、生产线或制造运营依据。保留线索，但不应直接视为高等级工程商机。"
    return grade, score, basis

def build_evidence(
    full: str,
    countries: list[str],
    investment_hits: list[str],
    construction_hits: list[str],
    formal_hits: list[str],
    early_hits: list[str],
    engineering_hits: list[str],
) -> str:
    pieces: list[str] = []
    if countries: pieces.append("地域：" + "、".join(countries))
    if investment_hits: pieces.append("投资信号：" + "、".join(investment_hits[:5]))
    if construction_hits: pieces.append("建设/产能：" + "、".join(construction_hits[:7]))
    if formal_hits: pieces.append("决策/签约：" + "、".join(formal_hits[:4]))
    if early_hits: pieces.append("规划阶段：" + "、".join(early_hits[:4]))
    if engineering_hits: pieces.append("工程范围信号：" + "、".join(engineering_hits[:5]))
    # Include concise contextual snippets as human-verifiable evidence.
    snippets = []
    flat = compact_text(full)
    for term in ["项目总投资", "购置土地", "新建", "生产基地", "预计开工", "董事会审议通过", "前期筹备"]:
        pos = flat.find(term)
        if pos >= 0:
            snippets.append(flat[max(0, pos - 35):min(len(flat), pos + 115)])
    if snippets:
        pieces.append("原文依据：" + " / ".join(_unique(snippets)[:3]))
    return "；".join(pieces)[:1800]


def make_project_key(analysis: dict[str, Any]) -> str:
    import hashlib
    company = str(analysis.get("stock_code") or analysis.get("stock_name") or "unknown").strip().lower()
    project = compact_text(str(analysis.get("project_name") or "")).lower()
    country = "|".join(analysis.get("countries") or [])
    # Project records are currently keyed by the exact announcement title for source traceability.
    seed = f"{company}|{country}|{project}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()
