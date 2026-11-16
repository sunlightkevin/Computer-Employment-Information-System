"""站点注册表。新增站点只需在此登记。"""
from __future__ import annotations

from .yingjiesheng import YingjieshengAdapter

ADAPTERS = {
    "yingjiesheng": YingjieshengAdapter,
}


def get_adapter(name: str):
    try:
        return ADAPTERS[name]()
    except KeyError:
        raise SystemExit(f"未知站点：{name}（可选：{', '.join(sorted(ADAPTERS))}）")
