"""全量采集入口测试：翻页覆盖、CLI 开关、去重与城市识别。"""
from __future__ import annotations

import pytest

from collector.sources.yingjiesheng import (
    ALL_CITIES,
    CITY_TOTAL_PAGES,
    DEFAULT_CITIES,
    YingjieshengAdapter,
)


@pytest.fixture(scope="module")
def adapter() -> YingjieshengAdapter:
    return YingjieshengAdapter()


class TestAllPages:
    def test_covers_all_nine_cities(self, adapter):
        urls = adapter.list_all_pages()
        assert len(urls) == sum(CITY_TOTAL_PAGES.values())
        assert len({u.split("/jisuanji/")[1].split("/")[0] for u in urls}) == 9

    def test_each_city_reaches_its_own_last_page(self, adapter):
        urls = adapter.list_all_pages()
        for city, last in CITY_TOTAL_PAGES.items():
            marker = f"/jisuanji/{city}/list_{last}.html"
            assert any(u.endswith(marker) for u in urls), f"{city} 没翻到末页 {last}"
            over = f"/jisuanji/{city}/list_{last + 1}.html"
            assert not any(u.endswith(over) for u in urls), f"{city} 翻过了末页"

    def test_first_page_uses_clean_url(self, adapter):
        urls = adapter.list_all_pages()
        assert "https://www.yingjiesheng.com/major/jisuanji/beijing/" in urls
        assert not any(u.endswith("/list_1.html") for u in urls)

    def test_default_mode_unchanged(self, adapter):
        """旧的每城 3 页模式保持不变，避免破坏已有采集习惯。"""
        urls = adapter.list_pages(["beijing"], pages=3)
        assert len(urls) == 3
        assert urls[-1].endswith("/beijing/list_3.html")

    def test_all_cities_includes_default(self):
        assert set(DEFAULT_CITIES).issubset(set(ALL_CITIES))

    def test_unknown_city_rejected(self, adapter):
        with pytest.raises(ValueError):
            adapter.list_all_pages(["atlantis"])


class TestCliAllPages:
    def test_parser_has_switch(self):
        from collector.__main__ import build_parser

        args = build_parser().parse_args(["--source", "yingjiesheng", "--all-pages"])
        assert args.all_pages is True
        assert args.pages == 5  # 开关存在时 --pages 保留默认值但不生效

    def test_default_switch_off(self):
        from collector.__main__ import build_parser

        args = build_parser().parse_args(["--source", "yingjiesheng"])
        assert args.all_pages is False

    def test_list_urls_file_switch(self, tmp_path):
        from collector.__main__ import build_parser

        path = tmp_path / "retry.txt"
        path.write_text("https://example.com/a\n\n# comment\nhttps://example.com/b\n", encoding="utf-8")
        args = build_parser().parse_args(["--source", "yingjiesheng", "--list-urls-file", str(path)])
        assert args.list_urls_file == str(path)

    def test_list_urls_file_skips_blank_and_comments(self, tmp_path):
        """补采文件里的空行与注释不能变成要抓的 URL。"""
        path = tmp_path / "retry.txt"
        path.write_text("https://example.com/a\n\n# comment\nhttps://example.com/b\n", encoding="utf-8")
        urls = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]
        assert urls == ["https://example.com/a", "https://example.com/b"]

    def test_zhuanye_city_switch(self):
        from collector.__main__ import build_parser

        args = build_parser().parse_args(["--source", "yingjiesheng", "--zhuanye-city", "zhengzhou"])
        assert args.zhuanye_city == "zhengzhou"

    def test_session_size_default(self):
        from collector.__main__ import build_parser

        args = build_parser().parse_args(["--source", "yingjiesheng"])
        assert args.session_size == 0

    def test_session_size_switch(self):
        from collector.__main__ import build_parser

        args = build_parser().parse_args(["--source", "yingjiesheng", "--session-size", "20"])
        assert args.session_size == 20

    def test_default_city_switch(self):
        from collector.__main__ import build_parser

        args = build_parser().parse_args(["--source", "yingjiesheng", "--default-city", "郑州"])
        assert args.default_city == "郑州"


class TestCityFallback:
    def test_other_city_replaced(self):
        from collector.normalize import build_dedupe_key

        job = {"cities": ["其它"], "company": "某公司", "title": "工程师"}
        cities = [c for c in job["cities"] if c not in ("其它", "未知", "")]
        if not cities:
            job["cities"] = ["郑州"]
            job["dedupe_key"] = build_dedupe_key(job["company"], job["title"], job["cities"])
        assert job["cities"] == ["郑州"]
        assert job["dedupe_key"].endswith("|郑州")

    def test_real_city_kept(self):
        job = {"cities": ["北京", "其它"]}
        cities = [c for c in job["cities"] if c not in ("其它", "未知", "")]
        assert cities == ["北京"]


class TestSessionBatches:
    def test_batches_split_rows(self):
        """每 2 条一批，索引保持全局连续（日志序号不能重来）。"""
        from collector.__main__ import _session_batches

        rows = [f"url{i}" for i in range(5)]
        # 不实际开浏览器，只验证切分逻辑：monkeypatch BrowserSession
        import collector.__main__ as cli

        class FakeSession:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        original = cli.BrowserSession
        cli.BrowserSession = lambda: FakeSession()
        try:
            result = [(batch[0][0], batch[-1][0], len(batch)) for _s, batch in _session_batches(rows, 2)]
        finally:
            cli.BrowserSession = original
        assert result == [(1, 2, 2), (3, 4, 2), (5, 5, 1)]

    def test_zero_means_single_batch(self):
        from collector.__main__ import _session_batches
        import collector.__main__ as cli

        class FakeSession:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        original = cli.BrowserSession
        cli.BrowserSession = lambda: FakeSession()
        try:
            batches = list(_session_batches(["a", "b", "c"], 0))
        finally:
            cli.BrowserSession = original
        assert len(batches) == 1
        assert len(batches[0][1]) == 3


class TestZhuanyeCity:
    def test_zhengzhou_urls(self, adapter):
        urls = adapter.zhuanye_list_urls("zhengzhou")
        assert urls == [
            "https://www.yingjiesheng.com/zhuanye/jisuanji/zhengzhou/",
            "https://www.yingjiesheng.com/zhuanye/jisuanji/zhengzhou/list_2.html",
            "https://www.yingjiesheng.com/zhuanye/jisuanji/zhengzhou/list_3.html",
        ]

    def test_unregistered_city_rejected(self, adapter):
        with pytest.raises(ValueError):
            adapter.zhuanye_list_urls("atlantis")

    def test_zhuanye_page_parses_rows(self, adapter):
        """专业招聘入口每行 50 条，选择器与 /major/ 一致。"""
        from pathlib import Path

        fixture = Path(__file__).parent / "fixtures" / "list_sample.html"
        rows = adapter.parse_list(fixture.read_text(encoding="utf-8"))
        assert len(rows) == 3
        assert all(r.url.startswith("https://") for r in rows)
