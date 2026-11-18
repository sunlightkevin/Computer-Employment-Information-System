"""大数据量下的分页控件测试：页码不能全部平铺。"""
from __future__ import annotations

import pytest

from app import create_app
from store import db as db_module
from store import queries


@pytest.fixture(scope="module")
def big_client(tmp_path_factory):
    """造 35 页数据（350 条），足够暴露分页平铺问题。"""
    path = tmp_path_factory.mktemp("big") / "big.db"
    conn = db_module.open_db(path)
    for i in range(350):
        queries.save_job(
            conn,
            {
                "dedupe_key": f"c{i}|job{i}|北京",
                "title": f"岗位{i}",
                "company": "公司",
                "job_type": "fulltime",
                "summary": "摘要",
                "apply_info": "",
                "published_at": f"2026-11-{i % 28 + 1:02d}",
                "source_site": "yingjiesheng",
                "source_url": f"https://www.yingjiesheng.com/job-{i:05d}.html",
                "external_id": f"{i:05d}",
                "cities": ["北京"],
                "directions": ["后端"],
            },
        )
    conn.close()

    application = create_app(str(path))
    application.config.update(TESTING=True)
    return application.test_client()


class TestPaginationWindow:
    def test_first_page_does_not_list_all_pages(self, big_client):
        body = big_client.get("/?type=fulltime").get_data(as_text=True)
        pager = body.split('<nav class="pager">')[1].split("</nav>")[0]
        assert pager.count("<a ") + pager.count('<span class="on">') < 20

    def test_first_page_has_next_and_last(self, big_client):
        body = big_client.get("/?type=fulltime").get_data(as_text=True)
        pager = body.split('<nav class="pager">')[1].split("</nav>")[0]
        assert "›" in pager
        assert ">35<" in pager  # 末页可见

    def test_middle_page_keeps_first_last_and_neighbors(self, big_client):
        body = big_client.get("/?type=fulltime&page=18").get_data(as_text=True)
        pager = body.split('<nav class="pager">')[1].split("</nav>")[0]
        assert ">1<" in pager
        assert ">35<" in pager
        assert '<span class="on">18</span>' in pager
        assert ">17<" in pager and ">19<" in pager

    def test_ellipsis_shown(self, big_client):
        body = big_client.get("/?type=fulltime&page=18").get_data(as_text=True)
        pager = body.split('<nav class="pager">')[1].split("</nav>")[0]
        assert "…" in pager

    def test_last_page_no_next(self, big_client):
        body = big_client.get("/?type=fulltime&page=35").get_data(as_text=True)
        pager = body.split('<nav class="pager">')[1].split("</nav>")[0]
        assert '<span class="pg-disabled">›</span>' in pager
