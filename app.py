"""Web 应用：岗位列表与详情。

仅监听本机 127.0.0.1，不对外提供服务（见 docs/facts/技术方案.md 的访问控制）。
"""
from __future__ import annotations

import os
from datetime import datetime

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

import linkcheck
from store import db as db_module
from store import read

PAGE_SIZE = 10
CATEGORY_LABELS = {"intern": "实习", "campus": "校招", "fulltime": "就业"}
CATEGORY_ORDER = ("intern", "campus", "fulltime")
DEFAULT_CATEGORY = "intern"
# 手改 URL 时允许一次勾很多，但别让查询无限膨胀
MAX_FILTER_VALUES = 20
REMOTE_CITY = "远程"


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["DB_PATH"] = str(db_path or os.environ.get("JOBS_DB") or db_module.DEFAULT_DB_PATH)
    # 可达性检查要拉真实浏览器过 WAF，超时按浏览器尺度算（见 linkcheck.py）
    app.config["LINK_TIMEOUT"] = linkcheck.DEFAULT_TIMEOUT
    app.json.ensure_ascii = False

    app.jinja_env.filters["date_only"] = _date_only

    def conn():
        if "conn" not in g:
            g.conn = db_module.open_db(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def _close_conn(_exc):
        existing = g.pop("conn", None)
        if existing is not None:
            existing.close()

    @app.route("/")
    def index():
        job_type = request.args.get("type", DEFAULT_CATEGORY)
        if job_type not in CATEGORY_LABELS:
            job_type = DEFAULT_CATEGORY

        cities = _multi_arg("city")
        directions = _multi_arg("direction")
        page = _int_arg("page", 1)

        db = conn()
        library_total = read.total_jobs(db)
        counts = read.category_counts(db)
        total = read.count_jobs(db, job_type=job_type, cities=cities, directions=directions)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(1, page), pages)
        jobs = read.list_jobs(
            db, job_type=job_type, cities=cities, directions=directions, page=page, page_size=PAGE_SIZE
        )

        # 当前筛选状态，拼链接时统一从这儿取，避免某个链接漏带条件
        filters = {"type": job_type, "city": cities, "direction": directions}

        return render_template(
            "list.html",
            job_type=job_type,
            category_labels=CATEGORY_LABELS,
            category_order=CATEGORY_ORDER,
            counts=counts,
            library_total=library_total,
            total=total,
            page=page,
            pages=pages,
            page_size=PAGE_SIZE,
            jobs=jobs,
            filters=filters,
            cities=cities,
            directions=directions,
            filters_active=bool(cities or directions),
            city_facets=read.facet_counts(db, "city", job_type=job_type, directions=directions),
            direction_facets=read.facet_counts(db, "direction", job_type=job_type, cities=cities),
            remote_city=REMOTE_CITY,
        )

    @app.route("/job/<int:job_id>")
    def detail(job_id: int):
        db = conn()
        job = read.get_job(db, job_id)
        if job is None:
            return render_template("not_found.html", job_id=job_id), 404

        # 返回列表要回到点进来时的条件，而不是默认首页
        back = _back_args()
        return render_template(
            "detail.html",
            job=job,
            source=read.primary_source(job),
            category_label=CATEGORY_LABELS.get(job["job_type"], job["job_type"]),
            unreachable=request.args.get("unreachable") == "1",
            back_url=url_for("index", **back),
            back_args=back,
            remote_city=REMOTE_CITY,
        )

    @app.post("/job/<int:job_id>/check")
    def check_link(job_id: int):
        db = conn()
        job = read.get_job(db, job_id)
        if job is None:
            return jsonify(ok=False, error="not_found"), 404

        source = read.primary_source(job)
        if source is None:
            return jsonify(ok=False, error="no_source"), 200

        reachable = linkcheck.is_reachable(source["source_url"], timeout=app.config["LINK_TIMEOUT"])
        read.mark_source_reachability(db, source["id"], reachable, _now())
        return jsonify(ok=reachable, url=source["source_url"])

    @app.get("/job/<int:job_id>/go")
    def go_source(job_id: int):
        """无 JavaScript 时的兜底：可达就跳转，不可达回到详情页提示。"""
        db = conn()
        job = read.get_job(db, job_id)
        if job is None:
            return render_template("not_found.html", job_id=job_id), 404

        source = read.primary_source(job)
        if source is None:
            return redirect(url_for("detail", job_id=job_id, unreachable=1, **_back_args()))

        reachable = linkcheck.is_reachable(source["source_url"], timeout=app.config["LINK_TIMEOUT"])
        read.mark_source_reachability(db, source["id"], reachable, _now())
        if reachable:
            return redirect(source["source_url"])
        return redirect(url_for("detail", job_id=job_id, unreachable=1, **_back_args()))

    return app


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def _multi_arg(name: str) -> list[str]:
    """多选参数。`?city=北京&city=上海` 和 `?city=北京,上海` 都认。

    同值只留一个：勾选状态下表单重复提交同一个值没有意义。
    """
    values: list[str] = []
    for raw in request.args.getlist(name):
        for part in str(raw).replace("，", ",").split(","):
            value = part.strip()
            if value and value not in values:
                values.append(value)
    return values[:MAX_FILTER_VALUES]


def _back_args() -> dict:
    """详情页返回列表时要带上的筛选状态。

    默认值不写进链接，URL 短一点，也避免 `?page=1` 这类噪音。
    """
    job_type = request.args.get("type", DEFAULT_CATEGORY)
    if job_type not in CATEGORY_LABELS:
        job_type = DEFAULT_CATEGORY

    args: dict = {"type": job_type}
    cities = _multi_arg("city")
    directions = _multi_arg("direction")
    if cities:
        args["city"] = cities
    if directions:
        args["direction"] = directions
    page = _int_arg("page", 1)
    if page > 1:
        args["page"] = page
    return args


def _date_only(value: str | None) -> str:
    if not value:
        return "—"
    return str(value)[:10]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
