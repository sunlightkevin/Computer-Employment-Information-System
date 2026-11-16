"""从列表行 + 详情页组装一条可写入的岗位记录。"""
from __future__ import annotations

from datetime import date

from .base import DetailData, ListRow
from .normalize import (
    build_dedupe_key,
    classify_directions,
    classify_job_type,
    clean_summary,
    extract_external_id,
    parse_cities,
    parse_date,
    split_title_and_company,
    strip_city_prefix,
)

UNKNOWN_CITY = "未知"


def is_cs_job(job: dict) -> bool:
    """是否属于计算机/IT 类岗位。

    产品定位限定采集计算机/IT 类信息（见 docs/facts/产品定位.md）。来源分类页
    本身比"计算机岗位"更宽，会混入销售、工艺、博士后等企业批量招聘，
    这类记录的方向只能落到「其他」。判定依据即方向是否全部为「其他」。
    """
    directions = job.get("directions") or []
    return bool(directions) and directions != ["其他"]


def build_job(
    row: ListRow,
    detail: DetailData,
    source_site: str,
    today: date | None = None,
) -> dict | None:
    """组装岗位记录。标题或企业都无法确定时返回 None（该条跳过）。"""
    title = (detail.title or "").strip()
    company = strip_city_prefix(detail.company)

    if not title or not company:
        fallback_title, fallback_company = split_title_and_company(row.raw_title)
        title = title or fallback_title
        company = company or fallback_company

    if not title or not company:
        return None

    cities = parse_cities(detail.city_raw) or parse_cities(row.city_raw) or [UNKNOWN_CITY]

    # 类型与方向的判断同时看详情页标题与列表标题：分类信息常只出现在列表标题里
    evidence_title = " ".join(part for part in (title, row.raw_title) if part)
    job_type = classify_job_type(detail.type_raw, evidence_title, detail.category)
    directions = classify_directions(detail.category, evidence_title)

    published_at = parse_date(detail.date_raw or row.date_raw, today=today)

    return {
        "dedupe_key": build_dedupe_key(company, title, cities),
        "title": title,
        "company": company,
        "job_type": job_type,
        "summary": clean_summary(detail.summary_raw),
        "apply_info": (detail.apply_info or "").strip() or None,
        "published_at": published_at,
        "source_site": source_site,
        "source_url": row.url,
        "external_id": extract_external_id(row.url),
        "cities": cities,
        "directions": directions,
    }
