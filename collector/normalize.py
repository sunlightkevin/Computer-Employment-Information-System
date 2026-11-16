"""字段归一化：方向归类、类型判定、去重键、日期、摘要。

规则依据 docs/facts/筛选与检索.md、database-schema.md、数据来源与合规.md
与 docs/Spec/M1-采集与入库.md 第 5 节。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

SUMMARY_LIMIT = 300
MAX_DIRECTIONS = 3

DIRECTIONS = ("后端", "前端", "算法", "数据", "嵌入式", "测试", "运维", "其他")

# 强关键词：能唯一指向某个方向，命中即采用。
# 英文关键词按单词边界匹配，避免 java 命中 javascript。
DIRECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "后端": (
        "后端", "服务端", "服务开发", "java", "c++", "golang", "go开发",
        "python", "软件工程师", "后端开发", "架构师", "全栈", "中间件",
    ),
    "前端": (
        "前端", "web前端", "h5", "react", "vue", "小程序", "web开发",
        "页面开发", "ui开发", "javascript", "typescript",
    ),
    "算法": (
        "算法", "机器学习", "深度学习", "计算机视觉", "cv工程", "nlp",
        "自然语言", "推荐", "搜索", "大模型", "人工智能", "ai工程", "ai算法",
        "感知", "具身", "ai",
    ),
    "数据": (
        "数据", "大数据", "数仓", "数据分析", "数据开发", "数据挖掘",
        "bi工程", "hadoop", "spark", "etl", "报表",
    ),
    "嵌入式": (
        "嵌入式", "单片机", "驱动", "固件", "fpga", "硬件", "电子工程",
        "电路", "arm", "射频", "电气", "器件", "芯片", "微电子", "集成电路",
    ),
    "测试": ("测试", "qa", "质量保障", "测开", "质量工程", "质量管理"),
    "运维": (
        "运维", "sre", "devops", "系统管理", "网络工程", "系统工程师", "云平台",
    ),
}

# 弱关键词：通用表述，多个方向都可能出现。只有在强关键词全部未命中时才采用，
# 否则「前端开发工程师」会被误判为后端。
WEAK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "后端": ("开发工程师", "研发工程师", "技术开发", "系统开发", "研发类", "工程师"),
}

INTERN_KEYS = ("实习",)
CAMPUS_KEYS = ("校招", "校园", "应届", "届别", "毕业生")
CAMPUS_PATTERN = re.compile(r"(20\d{2}\s*届|校招|校园招聘|应届)")
SOCIAL_KEYS = ("社招", "社会招聘")

_CITY_SPLIT = re.compile(r"[,，、/|;；\s]+")
_WS = re.compile(r"\s+")


def norm_text(value: str | None) -> str:
    """全角转半角、去首尾空白、压缩连续空白、转小写。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u3000", " ")
    text = _WS.sub(" ", text).strip()
    return text.lower()


def clean_display(value: str | None) -> str:
    """展示用：只做空白与全角处理，不转小写。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u3000", " ")
    return _WS.sub(" ", text).strip()


def parse_cities(raw: str | None) -> list[str]:
    """解析城市串，支持多分隔符，去掉省略号与「市」后缀，远程统一为「远程」。"""
    text = clean_display(raw)
    if not text:
        return []

    cities: list[str] = []
    for token in _CITY_SPLIT.split(text):
        token = token.strip().strip(".．…·")
        token = token.rstrip("等")
        token = token.strip()
        if not token or not re.search(r"[\u4e00-\u9fa5A-Za-z]", token):
            continue
        if token in ("远程", "线上", "remote"):
            token = "远程"
        elif token.endswith("市") and len(token) > 2:
            token = token[:-1]
        if token not in cities:
            cities.append(token)
    return cities


def parse_date(raw: str | None, today: date | None = None) -> str | None:
    """解析为 YYYY-MM-DD。仅给月日时按「不晚于今天」补年份。"""
    text = clean_display(raw)
    if not text:
        return None
    today = today or date.today()

    m = re.search(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})", text)
    if m:
        year, month, day = (int(g) for g in m.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    m = re.search(r"^(\d{1,2})\s*[-/月.]\s*(\d{1,2})", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = today.year
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate > today:
            try:
                candidate = date(year - 1, month, day)
            except ValueError:
                return None
        return candidate.isoformat()

    return None


def _match_any(keywords: tuple[str, ...], haystack: str) -> bool:
    for keyword in keywords:
        if keyword.isascii():
            pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
            if re.search(pattern, haystack):
                return True
        elif keyword in haystack:
            return True
    return False


def classify_job_type(type_raw: str | None, title: str | None, category: str | None = None) -> str:
    """intern / campus / fulltime，按 SPEC 5.1 的顺序判定。"""
    raw = norm_text(type_raw)
    text = norm_text(f"{title or ''} {category or ''}")

    if _match_any(INTERN_KEYS, raw):
        return "intern"
    if _match_any(INTERN_KEYS, text):
        return "intern"
    if _match_any(SOCIAL_KEYS, text):
        return "fulltime"
    if CAMPUS_PATTERN.search(text):
        return "campus"
    return "fulltime"


def classify_directions(category: str | None, title: str | None) -> list[str]:
    """按关键词表归类，允许多值，最多 3 个；无命中兜底「其他」。

    强关键词优先；全部未命中时才看通用词，避免「前端开发工程师」被判成后端。
    """
    haystack = norm_text(f"{category or ''} {title or ''}")

    found = [name for name, keys in DIRECTION_KEYWORDS.items() if _match_any(keys, haystack)]
    if not found:
        found = [name for name, keys in WEAK_KEYWORDS.items() if _match_any(keys, haystack)]
    if not found:
        return ["其他"]
    return found[:MAX_DIRECTIONS]


def clean_summary(raw: str | None, limit: int = SUMMARY_LIMIT) -> str | None:
    """压缩空白，超出上限截断并加省略号。"""
    if raw is None:
        return None
    text = clean_display(raw)
    if not text:
        return None
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def build_dedupe_key(company: str | None, title: str | None, cities: list[str]) -> str:
    """公司 | 岗位名 | 首个城市（排序后）。"""
    city_key = sorted(cities)[0] if cities else "未知"
    return "|".join((norm_text(company), norm_text(title), norm_text(city_key)))


def extract_external_id(url: str) -> str | None:
    """从 job-008-150-254.html 提取 008-150-254。"""
    m = re.search(r"/job-([0-9\-]+)\.html", url or "")
    return m.group(1) if m else None


def strip_city_prefix(text: str | None) -> str:
    """去掉标题开头的 [北京] 前缀。"""
    cleaned = clean_display(text)
    return re.sub(r"^\[[^\]]*\]\s*", "", cleaned).strip()


def split_title_and_company(row_title: str | None) -> tuple[str, str]:
    """列表行标题形如「[北京]公司名 岗位名」，尽力拆出公司与岗位名。

    仅作详情页缺字段时的兜底，无法可靠拆分的部分留空。
    """
    body = strip_city_prefix(row_title)
    if not body:
        return "", ""
    parts = body.split(" ", 1)
    if len(parts) == 2:
        return parts[1].strip(), parts[0].strip()
    return body, ""
