"""Root factory測試共用的最小production SPA artifact authority。"""

from __future__ import annotations

from pathlib import Path


def 建立ProductionDist(根: Path) -> Path:
    """建立最小合法hashed production dist；參數為測試根，返回absolute dist Path。"""
    Dist根 = 根 / "web-dist"
    Assets根 = Dist根 / "assets"
    Assets根.mkdir(parents=True)
    (Assets根 / "app-ABCDEFGH.js").write_bytes(b"console.log('production')")
    (Dist根 / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div>'
        '<script type="module" src="/assets/app-ABCDEFGH.js"></script>'
        '</body></html>',
        encoding="utf-8",
    )
    return Dist根.resolve()


__all__ = ("建立ProductionDist",)
