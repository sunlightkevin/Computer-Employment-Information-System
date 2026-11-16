"""M4 关键词搜索测试：命中三个字段、与分类和筛选叠加、无结果、条件保留。"""
from __future__ import annotations

import re

import pytest

from app import MAX_KEYWORD_LEN, create_app
from store import db as db_module
from store import queries

# (标题, 公司, 城市, 方向, 类型)
SEED = [
    ("Java后端开发", "字节跳动", "北京", "后端", "fulltime"),
    ("前端开发工程师", "腾讯科技", "深圳", "前端", "fulltime"),
    ("算法工程师", "商汤科技", "北京", "算法", "campus"),
    ("机器学习研究员", "旷视科技", "北京", "算法", "fulltime"),
    ("数据分析实习生", "美团", "上海", "数据", "intern"),
    ("测试开发工程师", "阿里巴巴", "杭州", "测试", "fulltime"),
    ("运维工程师", "京东", "北京", "运维", "fulltime"),
    ("嵌入式软件工程师", "华为", "深圳", "嵌入式", "fulltime"),
    ("后端开发实习生", "百度", "北京", "后端", "intern"),
]


@pytest.fixture()
def client(tmp_path):
    path = tmp_path / "search.db"
    conn = db_module.open_db(path)
    for index, (title, company, city, direction, job_type) in enumerate(SEED, start=1):
        queries.save_job(
            conn,
            {
                "dedupe_key": f"{company}|{title}|{city}",
                "title": title,
                "company": company,
                "job_type": job_type,
                "summary": "摘要",
                "apply_info": "在线申请",
                "published_at": f"2026-02-{index:02d}",
                "source_site": "yingjiesheng",
                "source_url": f"https://www.yingjiesheng.com/job-{index:03d}.html",
                "external_id": f"{index:03d}",
                "cities": [city],
                "directions": [direction],
            },
        )
    conn.close()

    application = create_app(str(path))
    application.config.update(TESTING=True)
    with application.test_client() as test_client:
        yield test_client


def body_of(client, url: str) -> str:
    return client.get(url).get_data(as_text=True)


def total_of(client, url: str) -> int:
    body = body_of(client, url)
    marker = "筛选结果 <b>"
    if marker not in body:
        return 0
    return int(body.split(marker)[1].split("</b>")[0])


class TestMatchFields:
    def test_matches_title(self, client):
        assert total_of(client, "/?type=fulltime&q=Java") == 1

    def test_matches_company(self, client):
        body = body_of(client, "/?type=fulltime&q=腾讯")
        assert "前端开发工程师" in body

    def test_matches_direction(self, client):
        """「机器学习研究员」标题里没有「算法」，靠方向命中。"""
        body = body_of(client, "/?type=fulltime&q=算法")
        assert "机器学习研究员" in body

    def test_direction_match_does_not_leak_other_directions(self, client):
        assert total_of(client, "/?type=fulltime&q=算法") == 1

    def test_partial_match(self, client):
        assert total_of(client, "/?type=fulltime&q=工程") == 4

    def test_ascii_case_insensitive(self, client):
        assert total_of(client, "/?type=fulltime&q=java") == 1
        assert total_of(client, "/?type=fulltime&q=JAVA") == 1

    def test_trims_whitespace(self, client):
        assert total_of(client, "/?type=fulltime&q=%20Java%20") == 1


class TestCombine:
    def test_with_category(self, client):
        assert total_of(client, "/?q=后端") == 1  # 默认 intern，只有「后端开发实习生」
        assert total_of(client, "/?type=fulltime&q=后端") == 1  # 「Java后端开发」

    def test_with_city(self, client):
        """深圳有「前端开发工程师」和「嵌入式软件工程师」，搜索不该越过城市条件。"""
        assert total_of(client, "/?type=fulltime&q=工程师&city=深圳") == 2
        assert total_of(client, "/?type=fulltime&q=工程师") == 4

    def test_with_city_and_direction(self, client):
        assert total_of(client, "/?type=fulltime&q=开发&direction=后端") == 1

    def test_intersection_can_be_empty(self, client):
        assert total_of(client, "/?type=fulltime&q=工程师&direction=后端") == 0

    def test_narrows_facet_counts(self, client):
        """搜索「算法」后，侧栏不应还列着跟算法无关的城市。"""
        body = body_of(client, "/?type=fulltime&q=算法")
        assert 'value="北京"' in body
        assert 'value="杭州"' not in body


class TestNoResult:
    def test_empty_state_mentions_keyword(self, client):
        body = body_of(client, "/?type=fulltime&q=不存在的岗位")
        assert "没有符合当前条件的岗位" in body
        assert "关键词「不存在的岗位」" in body

    def test_gives_clear_entry(self, client):
        body = body_of(client, "/?type=fulltime&q=不存在的岗位")
        assert "清除筛选" in body

    def test_no_cards(self, client):
        body = body_of(client, "/?type=fulltime&q=不存在的岗位")
        assert '<a class="card"' not in body


class TestWildcardSafety:
    def test_percent_is_literal_not_wildcard(self, client):
        """原文来自搜索框，% 若当通配符会「匹配一切」。"""
        assert total_of(client, "/?type=fulltime&q=%25") == 0

    def test_underscore_is_literal(self, client):
        assert total_of(client, "/?type=fulltime&q=_") == 0

    def test_backslash_does_not_break_query(self, client):
        assert total_of(client, "/?type=fulltime&q=%5C") == 0


class TestParamHandling:
    def test_blank_keyword_means_no_search(self, client):
        assert total_of(client, "/?type=fulltime&q=%20%20") == 6

    def test_missing_keyword_means_no_search(self, client):
        assert total_of(client, "/?type=fulltime") == 6

    def test_long_keyword_is_truncated(self, client):
        response = client.get("/?type=fulltime&q=" + "长" * (MAX_KEYWORD_LEN + 40))
        assert response.status_code == 200

    def test_keyword_echoed_into_input(self, client):
        body = body_of(client, "/?type=fulltime&q=Java")
        assert re.search(r'<input[^>]*name="q"[^>]*value="Java"', body)

    def test_empty_input_when_idle(self, client):
        body = body_of(client, "/?type=fulltime")
        assert re.search(r'<input[^>]*name="q"[^>]*value=""', body)

    def test_html_in_keyword_is_escaped(self, client):
        body = body_of(client, "/?type=fulltime&q=%3Cscript%3E")
        assert "<script>alert" not in body
        assert "&lt;script&gt;" in body


class TestPersistence:
    def test_tabs_keep_keyword(self, client):
        body = body_of(client, "/?type=fulltime&q=Java")
        hrefs = re.findall(r'<a class="tab[^"]*"\s+href="([^"]+)"', body)
        assert hrefs
        assert all("q=Java" in href for href in hrefs)

    def test_facet_clear_keeps_keyword(self, client):
        body = body_of(client, "/?type=fulltime&q=工程师&city=北京")
        clear_links = re.findall(r'class="facet-clear" href="([^"]+)"', body)
        assert clear_links
        assert any("q=%E5%B7%A5%E7%A8%8B%E5%B8%88" in link for link in clear_links)

    def test_clear_keyword_keeps_other_conditions(self, client):
        body = body_of(client, "/?type=fulltime&q=Java&city=北京")
        clear = re.search(r'class="search-clear"[^>]*href="([^"]+)"', body)
        assert clear
        assert "city=" in clear.group(1)
        assert "q=" not in clear.group(1)

    def test_detail_back_keeps_keyword(self, client):
        conn = db_module.open_db(client.application.config["DB_PATH"])
        job_id = conn.execute("SELECT id FROM jobs ORDER BY id LIMIT 1").fetchone()[0]
        conn.close()

        body = body_of(client, f"/job/{job_id}?type=fulltime&q=Java")
        assert "q=Java" in body

    def test_chip_shows_keyword(self, client):
        body = body_of(client, "/?type=fulltime&q=Java")
        assert '<span class="chip chip-keyword">“Java”</span>' in body

    def test_search_form_keeps_filters(self, client):
        """搜索不能把已选的城市/方向丢掉。"""
        body = body_of(client, "/?type=fulltime&q=工程师&city=北京&direction=后端")
        form = body.split('<form class="search"')[1].split("</form>")[0]
        assert 'name="city" value="北京"' in form
        assert 'name="direction" value="后端"' in form
        assert 'name="type" value="fulltime"' in form
