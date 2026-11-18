"""郑州作为独立筛选项的测试：固定展示、零计数、可选可筛。"""
from __future__ import annotations

import pytest

from app import PINNED_CITIES, create_app
from store import db as db_module
from store import queries


def job(city: str, direction: str, title: str) -> dict:
    return {
        "dedupe_key": f"公司-{title}|{city}",
        "title": title,
        "company": "郑州测试公司",
        "job_type": "fulltime",
        "summary": "摘要",
        "apply_info": "在线申请",
        "published_at": "2026-11-17",
        "source_site": "yingjiesheng",
        "source_url": f"https://www.yingjiesheng.com/job-{abs(hash(title)) % 100000}.html",
        "external_id": title,
        "cities": [city],
        "directions": [direction],
    }


@pytest.fixture()
def client_with_zhengzhou(tmp_path):
    path = tmp_path / "zhengzhou.db"
    conn = db_module.open_db(path)
    # 只给郑州 2 条，其余城市数量都更多——验证郑州不是靠数量排上来的
    for city, direction, n in (("上海", "后端", 30), ("深圳", "数据", 20), ("郑州", "后端", 2)):
        for i in range(n):
            queries.save_job(conn, job(city, direction, f"{city}{direction}{i}"))
    conn.close()

    application = create_app(str(path))
    application.config.update(TESTING=True)
    with application.test_client() as test_client:
        yield test_client


class TestPinnedZhengzhou:
    def test_order_is_pinned_not_by_count(self, client_with_zhengzhou):
        body = client_with_zhengzhou.get("/?type=fulltime").get_data(as_text=True)
        values = [line.split('value="')[1].split('"')[0] for line in body.splitlines() if 'name="city" value="' in line]
        assert values[:5] == list(PINNED_CITIES)

    def test_zhengzhou_visible_with_small_count(self, client_with_zhengzhou):
        body = client_with_zhengzhou.get("/?type=fulltime").get_data(as_text=True)
        beijing = body.split('value="郑州"')[1].split("</label>")[0]
        assert 'class="opt-count">2<' in beijing

    def test_zhengzhou_filterable(self, client_with_zhengzhou):
        body = client_with_zhengzhou.get("/?type=fulltime&city=%E9%83%91%E5%B7%9E").get_data(as_text=True)
        assert "筛选结果 <b>2</b>" in body
        assert 'value="郑州"' in body and "checked" in body.split('value="郑州"')[1].split(">")[0]

    def test_zhengzhou_shows_zero_in_other_category(self, client_with_zhengzhou):
        body = client_with_zhengzhou.get("/?type=intern").get_data(as_text=True)
        zz = body.split('value="郑州"')[1].split("</label>")[0]
        assert 'class="opt-count">0<' in zz

    def test_no_duplicate_city_option(self, client_with_zhengzhou):
        body = client_with_zhengzhou.get("/?type=fulltime").get_data(as_text=True)
        assert body.count('name="city" value="郑州"') == 1
