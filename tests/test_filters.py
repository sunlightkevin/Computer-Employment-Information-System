"""M3 条件筛选测试：城市/方向多选、与分类叠加、无结果、条件保留、远程标识。"""
from __future__ import annotations

import re

import pytest

from app import create_app
from store import db as db_module
from store import queries

# (岗位类型, 城市, 方向, 条数)
SEED_PLAN = [
    ("intern", "北京", "后端", 6),
    ("intern", "北京", "前端", 6),
    ("intern", "上海", "后端", 5),
    ("intern", "远程", "后端", 2),
    ("campus", "北京", "后端", 3),
    ("campus", "上海", "数据", 2),
    ("fulltime", "深圳", "运维", 3),
    ("fulltime", "远程", "测试", 1),
]

TOTALS = {
    "intern": 19,
    "campus": 5,
    "fulltime": 4,
}


def build_jobs() -> list[dict]:
    jobs: list[dict] = []
    day = 0
    for job_type, city, direction, count in SEED_PLAN:
        for _ in range(count):
            day += 1
            title = f"{job_type}-{city}-{direction}-{day:02d}"
            jobs.append(
                {
                    "dedupe_key": f"公司{day}|{title}|{city}",
                    "title": title,
                    "company": f"公司{day}",
                    "job_type": job_type,
                    "summary": "摘要",
                    "apply_info": "在线申请",
                    "published_at": f"2026-01-{day:02d}",
                    "source_site": "yingjiesheng",
                    "source_url": f"https://www.yingjiesheng.com/job-{day:03d}.html",
                    "external_id": f"{day:03d}",
                    "cities": [city],
                    "directions": [direction],
                }
            )
    return jobs


@pytest.fixture()
def seeded_client(tmp_path):
    path = tmp_path / "filters.db"
    conn = db_module.open_db(path)
    for job in build_jobs():
        queries.save_job(conn, job)
    conn.close()

    application = create_app(str(path))
    application.config.update(TESTING=True)
    with application.test_client() as test_client:
        yield test_client


def total_of(client, url: str) -> int:
    body = client.get(url).get_data(as_text=True)
    marker = "筛选结果 <b>"
    if marker not in body:
        return 0
    return int(body.split(marker)[1].split("</b>")[0])


def tab_hrefs(html: str) -> list[str]:
    return re.findall(r'<a class="tab[^"]*"\s+href="([^"]+)"', html)


class TestMultiSelect:
    def test_single_city(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&city=上海") == 5

    def test_multi_city_is_union(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&city=北京&city=上海") == 17

    def test_comma_separated_city_is_accepted(self, seeded_client):
        """手改 URL 时容易写成逗号形式，不该静默失效。"""
        assert total_of(seeded_client, "/?type=intern&city=北京,上海") == 17

    def test_duplicate_values_do_not_double_count(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&city=北京&city=北京") == 12

    def test_single_direction(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&direction=前端") == 6

    def test_multi_direction_is_union(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&direction=后端&direction=前端") == 19

    def test_unknown_value_yields_nothing(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&city=火星") == 0


class TestIntersection:
    def test_city_and_direction_intersect(self, seeded_client):
        """"或"在同一维度内，"且"在维度之间。"""
        assert total_of(seeded_client, "/?type=intern&city=北京&direction=前端") == 6

    def test_multi_city_with_direction(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&city=北京&city=上海&direction=后端") == 11

    def test_no_duplicate_cards(self, seeded_client):
        """多值表若用 JOIN 会出重复行，分页也会跟着错位。"""
        body = seeded_client.get("/?type=intern&city=北京&city=上海&direction=后端").get_data(as_text=True)
        cards = body.count('<a class="card"')
        titles = [segment.split("</span>")[0] for segment in body.split('<span class="card-title">')[1:]]
        assert cards == 10  # 11 条里的第一页
        assert len(set(titles)) == len(titles)

    def test_pagination_stays_inside_filter(self, seeded_client):
        page2 = seeded_client.get("/?type=intern&city=北京&page=2").get_data(as_text=True)
        assert "intern-北京-后端" in page2 or "intern-北京-前端" in page2
        assert "intern-上海-后端" not in page2


class TestCategoryWithFilters:
    def test_filter_applies_per_category(self, seeded_client):
        assert total_of(seeded_client, "/?type=campus&city=北京") == 3
        assert total_of(seeded_client, "/?type=fulltime&city=北京") == 0

    def test_filter_does_not_cross_categories(self, seeded_client):
        body = seeded_client.get("/?type=fulltime&city=深圳").get_data(as_text=True)
        assert "fulltime-深圳-运维" in body
        assert "intern-北京-后端" not in body


class TestFilterPersistence:
    def test_tabs_carry_current_filters(self, seeded_client):
        body = seeded_client.get("/?type=intern&city=北京&direction=后端").get_data(as_text=True)
        hrefs = tab_hrefs(body)
        assert len(hrefs) == 3

        by_type = {href.split("type=")[1].split("&")[0]: href for href in hrefs}
        for job_type in ("intern", "campus", "fulltime"):
            href = by_type[job_type]
            assert "city=%E5%8C%97%E4%BA%AC" in href  # 北京
            assert "direction=%E5%90%8E%E7%AB%AF" in href  # 后端

    def test_switching_category_keeps_result_non_zero(self, seeded_client):
        """带上条件切分类，落到 campus 应只剩北京的 3 条，而不是回到 5 条。"""
        body = seeded_client.get("/?type=campus&city=北京").get_data(as_text=True)
        assert "筛选结果 <b>3</b> 条" in body

    def test_pagination_carries_filters(self, seeded_client):
        body = seeded_client.get("/?type=intern&city=北京").get_data(as_text=True)
        assert "page=2" in body
        page_link = body.split('page=2"')[0].split('href="')[-1]
        assert "city=%E5%8C%97%E4%BA%AC" in page_link

    def test_detail_back_link_keeps_filters(self, seeded_client):
        conn = db_module.open_db(seeded_client.application.config["DB_PATH"])
        job_id = conn.execute("SELECT id FROM jobs ORDER BY id LIMIT 1").fetchone()[0]
        conn.close()

        body = seeded_client.get(
            f"/job/{job_id}?type=intern&city=%E5%8C%97%E4%BA%AC&direction=%E5%90%8E%E7%AB%AF&page=2"
        ).get_data(as_text=True)
        assert "city=%E5%8C%97%E4%BA%AC" in body
        assert "direction=%E5%90%8E%E7%AB%AF" in body
        assert "page=2" in body

    def test_detail_back_link_without_filters_is_plain(self, seeded_client):
        conn = db_module.open_db(seeded_client.application.config["DB_PATH"])
        job_id = conn.execute("SELECT id FROM jobs ORDER BY id LIMIT 1").fetchone()[0]
        conn.close()

        body = seeded_client.get(f"/job/{job_id}").get_data(as_text=True)
        assert 'href="/?type=intern"' in body

    def test_filter_bar_shows_selected_chips(self, seeded_client):
        body = seeded_client.get("/?type=intern&city=北京&direction=后端").get_data(as_text=True)
        assert "已选" in body
        assert '<span class="chip">北京</span>' in body
        assert '<span class="chip">后端</span>' in body

    def test_clear_link_drops_filters_but_keeps_category(self, seeded_client):
        body = seeded_client.get("/?type=campus&city=北京").get_data(as_text=True)
        assert '<a class="filter-clear" href="/?type=campus">清除筛选</a>' in body

    def test_no_filter_bar_when_idle(self, seeded_client):
        body = seeded_client.get("/?type=intern").get_data(as_text=True)
        assert "已选" not in body


class TestNoResult:
    def test_shows_dedicated_empty_state(self, seeded_client):
        body = seeded_client.get("/?type=intern&city=深圳").get_data(as_text=True)
        assert "没有符合当前条件的岗位" in body
        assert "清除筛选" in body

    def test_does_not_render_cards(self, seeded_client):
        body = seeded_client.get("/?type=intern&city=深圳").get_data(as_text=True)
        assert '<a class="card"' not in body

    def test_suggests_other_categories(self, seeded_client):
        body = seeded_client.get("/?type=intern&city=深圳").get_data(as_text=True)
        assert "查看就业（4）" in body

    def test_empty_library_hint_not_shown(self, seeded_client):
        """库里有数据、只是筛不出来，不能提示"去采集"。"""
        body = seeded_client.get("/?type=intern&city=深圳").get_data(as_text=True)
        assert "本地库中还没有岗位" not in body


class TestRemoteCity:
    def test_remote_badge_rendered(self, seeded_client):
        body = seeded_client.get("/?type=intern&direction=后端&city=远程").get_data(as_text=True)
        assert '<span class="remote-badge">远程</span>' in body

    def test_remote_is_selectable(self, seeded_client):
        body = seeded_client.get("/?type=intern").get_data(as_text=True)
        assert 'value="远程"' in body

    def test_remote_filter_works(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&city=远程") == 2

    def test_remote_badge_absent_for_normal_city(self, seeded_client):
        body = seeded_client.get("/?type=intern&city=上海").get_data(as_text=True)
        assert '<span class="remote-badge">远程</span>' not in body


class TestFacetCounts:
    def test_city_counts_ignore_city_selection(self, seeded_client):
        """勾了北京之后，上海的数量不能被清零，否则没法再切过去。"""
        body = seeded_client.get("/?type=intern&city=北京").get_data(as_text=True)
        shanghai = body.split('value="上海"')[1].split("</label>")[0]
        assert ">5<" in shanghai

    def test_direction_counts_follow_city_selection(self, seeded_client):
        body = seeded_client.get("/?type=intern&city=上海").get_data(as_text=True)
        assert 'value="后端"' in body
        assert 'value="前端"' not in body  # 上海没有前端岗位

    def test_selected_value_stays_checked(self, seeded_client):
        body = seeded_client.get("/?type=intern&city=北京").get_data(as_text=True)
        beijing = body.split('value="北京"')[1].split(">")[0]
        assert "checked" in beijing

    def test_facet_counts_match_category(self, seeded_client):
        body = seeded_client.get("/?type=fulltime").get_data(as_text=True)
        assert 'value="深圳"' in body
        assert 'value="北京"' not in body  # 北京没有就业类岗位


class TestParamParsing:
    def test_invalid_type_falls_back_to_intern(self, seeded_client):
        assert total_of(seeded_client, "/?type=unknown&city=北京") == 12

    def test_blank_value_ignored(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&city=&city=北京") == 12

    def test_whitespace_trimmed(self, seeded_client):
        assert total_of(seeded_client, "/?type=intern&city=%20北京%20") == 12

    def test_totals_baseline(self, seeded_client):
        for job_type, expected in TOTALS.items():
            assert total_of(seeded_client, f"/?type={job_type}") == expected
