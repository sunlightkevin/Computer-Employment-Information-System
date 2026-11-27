"""前程无忧 51job 适配器。

数据来源：搜索页调用的公开 GET 接口 `we.51job.com/api/job/search-pc`，
在浏览器页面上下文内 fetch 获取（复用页面 WAF 环境）。岗位详情页撞滑块，
但接口已返回摘要与职能类别，无需访问详情。

站点实测见 docs/Spec/recon-51job.md。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from ..normalize import clean_display, clean_summary, parse_date

BASE = "https://we.51job.com"
SEARCH_PATH = "/pc/search"
API_PATH = "/api/job/search-pc"
API_KEY = "51job"
JOB_AREA_HENAN = "170000"

# 互联网技术大类 code（含其下所有子孙 code 从字典动态加载）
IT_MAJOR_CODE = "9F02"
FUNTYPE_DICT_URL = "https://fecdn.51jobcdn.com/fe/mc/dict/dd_funtype_standard.json"

DEFAULT_KEYWORDS = (
    "软件测试工程师", "人工智能", "后端开发", "前端开发", "Java", "Python",
    "算法工程师", "数据分析师", "测试工程师", "运维工程师", "嵌入式工程师",
)

# 标题 IT 词表：只保留职能类别属互联网技术大类、且标题命中明确 IT 词的岗位。
# 大类口径过宽（游戏陪练、文员等会混入），标题词表用于收紧。
# 用完整短语而非泛词："测试"/"运维"/"开发" 单独命中会误收仪器测试、风电运维。
IT_TITLE_HINTS = (
    "软件", "后端", "前端", "全栈", "算法", "人工智能", "AI", "机器学习",
    "深度学习", "神经网络", "自然语言", "计算机视觉", "大模型", "视觉研发",
    "Java", "Python", "C++", "C#", "Golang", "goland", "PHP", "Ruby", "Swift",
    "Kotlin", ".NET", "Node.js", "React", "Vue", "嵌入式", "单片机", "固件",
    "驱动开发", "架构师", "测试工程师", "测试开发", "软件测试", "自动化测试",
    "5G测试", "运维工程师", "系统运维", "IT运维", "网络运维", "运维开发",
    "DevOps", "SRE", "IT技术支持", "数据开发", "数据分析", "数据仓库", "大数据",
    "数据挖掘", "数据处理", "图像处理", "数据采集", "数据治理", "数据库", "DBA",
    "MySQL", "Redis", "Oracle", "云计算", "云原生", "Kubernetes", "Docker",
    "信息安全", "网络安全", "渗透测试", "安全研发", "Web", "移动端", "Android",
    "iOS", "小程序", "游戏开发", "计算机", "信息技术", "程序员", "开发工程师",
    "研发工程师", "网络工程师", "系统实施", "系统维护", "网络维护", "GIS", "智能化",
)

# 黑名单优先：即便标题含 IT 词，这些明显非 IT 场景仍剔除（如热电厂运维工程师）。
NON_IT_TITLE_HINTS = (
    "新能源", "风电", "光伏", "电力", "热电厂", "火电", "设备运维", "设备维护",
    "仪器", "试验", "机械", "工艺", "游戏陪练", "文员", "销售", "资管",
    "客户经理", "数据标注", "数据采集员", "实验室", "制造",
)


@dataclass
class Job51Row:
    """接口返回的一条岗位，已提取入库所需字段。"""

    job_id: str
    title: str
    company: str
    summary: str | None
    city: str
    province: str
    published_at: str | None
    func_code: str
    source_url: str
    is_intern: bool
    term: str
    tags: list[str] = field(default_factory=list)


def search_page_url(keyword: str, job_area: str = JOB_AREA_HENAN) -> str:
    """搜索页 URL：用于让浏览器加载页面、生成合法 WAF 环境。"""
    return f"{BASE}{SEARCH_PATH}?keyword={quote(keyword)}&jobArea={job_area}"


def api_url(keyword: str, job_area: str, page_num: int, timestamp: int, page_size: int = 20) -> str:
    """构造公开搜索接口 URL。

    注意：直接 HTTP 请求该 URL 会触发 WAF；必须在搜索页的浏览器上下文内 fetch。
    """
    return (
        f"{BASE}{API_PATH}?api_key={API_KEY}&timestamp={timestamp}"
        f"&keyword={quote(keyword)}&searchType=2&function=&industry="
        f"&jobArea={job_area}&jobArea2=&landmark=&metro=&salary=&workYear="
        f"&degree=&companyType=&companySize=&jobType=&issueDate="
        f"&sortType=0&pageNum={page_num}&requestId=&pageSize={page_size}"
    )


def load_it_codes(dict_json: str | dict) -> set[str]:
    """从职能类别字典中提取「互联网技术」大类及其所有子孙 code。

    字典结构：顶层条目含 code/value/items，IT 大类 code 为 `9F02`。
    """
    data = json.loads(dict_json) if isinstance(dict_json, str) else dict_json
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("职能类别字典格式错误：缺少 items")

    def collect(node: dict, acc: set[str]) -> None:
        code = node.get("code")
        if code:
            acc.add(str(code))
        for child in node.get("items") or []:
            if isinstance(child, dict):
                collect(child, acc)

    result: set[str] = set()
    for node in items:
        if str(node.get("code")) == IT_MAJOR_CODE:
            collect(node, result)
            return result
    return result


def is_it_title(title: str) -> bool:
    """标题是否命中明确 IT 词（黑名单优先）。

    黑名单先于白名单：如「热电厂运维工程师」含「运维工程师」但不是 IT 岗。
    """
    lowered = (title or "").lower()
    if any(hint.lower() in lowered for hint in NON_IT_TITLE_HINTS):
        return False
    return any(hint.lower() in lowered for hint in IT_TITLE_HINTS)


def is_it_job(row: Job51Row, it_codes: set[str]) -> bool:
    """双重判定：职能类别属互联网技术大类，且标题命中明确 IT 词。"""
    return row.func_code in it_codes and is_it_title(row.title)


def parse_api_json(payload: str | dict) -> tuple[list[Job51Row], int]:
    """解析接口 JSON，返回 (岗位列表, totalCount)。

    纯函数，便于单测；不访问网络。
    """
    data = json.loads(payload) if isinstance(payload, str) else payload
    if str(data.get("status")) != "1":
        raise ValueError(f"接口返回异常状态：{data.get('status')} {data.get('message')}")
    body = data.get("resultbody") or {}
    job = body.get("job") or {}
    items = job.get("items") or []
    total = int(job.get("totalCount") or job.get("totalcount") or 0)

    rows: list[Job51Row] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = clean_display(item.get("jobName"))
        company = clean_display(item.get("companyName"))
        if not title or not company:
            continue

        area = item.get("jobAreaLevelDetail") or {}
        city = clean_display(area.get("cityString") or item.get("jobAreaString")) or "其它"
        province = clean_display(area.get("provinceString"))

        issue = clean_display(item.get("issueDateString"))
        published = parse_date(issue) if issue else None

        tags = [clean_display(t) for t in (item.get("jobTags") or []) if clean_display(t)]

        rows.append(
            Job51Row(
                job_id=str(item.get("jobId") or ""),
                title=title,
                company=company,
                summary=clean_summary(item.get("jobDescribe")),
                city=city,
                province=province,
                published_at=published,
                func_code=str(item.get("funcType1Code") or ""),
                source_url=clean_display(item.get("jobHref")),
                is_intern=bool(item.get("isIntern")),
                term=clean_display(item.get("termStr")),
                tags=tags,
            )
        )
    return rows, total


def classify_job_type(row: Job51Row) -> str:
    """按接口字段判定类型：实习 / 校招 / 就业。"""
    text = f"{row.title} {' '.join(row.tags)}"
    if row.is_intern or "实习" in text:
        return "intern"
    if re.search(r"校招|校园|应届|20\d{2}届", text):
        return "campus"
    return "fulltime"


def classify_directions(row: Job51Row) -> list[str]:
    """用现有关键词表按岗位名 + 摘要归类方向。"""
    from ..normalize import classify_directions as _classify

    return _classify(None, f"{row.title} {row.summary or ''}")
