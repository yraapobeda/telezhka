"""Склейка подписок: одна ссылка → подписки со всех панелей разом.

GET /sub/{token} идёт во встроенный сервер подписок каждой панели
({SUB_URL}/{token}{loc}), берёт ключи как есть (с внешними адресами и
названиями из панели), склеивает и отдаёт одним списком + общий трафик/срок.
Базу бота не трогает, поэтому может работать и отдельно (см. sub_server.py).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re

import httpx
from aiohttp import web

log = logging.getLogger(__name__)
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _decode_body(text: str) -> list[str]:
    t = text.strip()
    if "://" not in t:
        try:
            t = base64.b64decode(t + "=" * (-len(t) % 4)).decode()
        except (binascii.Error, UnicodeDecodeError):
            return []
    return [l.strip() for l in t.splitlines() if "://" in l]


def _userinfo(h: str | None) -> dict[str, int]:
    out = {}
    for part in (h or "").split(";"):
        k, _, v = part.strip().partition("=")
        if v.strip().isdigit():
            out[k.strip()] = int(v)
    return out


class Merger:
    """sources: [(loc_key, sub_url, verify_ssl)]"""

    def __init__(self, sources: list[tuple[str, str, bool]], brand: str, support: str):
        self.sources = sources
        self.brand, self.support = brand, support
        self.clients = {v: httpx.AsyncClient(verify=v, timeout=10, headers={"User-Agent": "v2rayN/7.0"})
                        for v in {s[2] for s in sources}}

    async def close(self):
        for c in self.clients.values():
            await c.aclose()

    async def _one(self, key: str, url: str, verify: bool, token: str):
        try:
            r = await self.clients[verify].get(f"{url}/{token}{key}")
        except httpx.HTTPError as e:
            log.warning("sub %s: %s", key, e)
            return None
        if r.status_code != 200:
            return None
        links = _decode_body(r.text)
        return (links, _userinfo(r.headers.get("subscription-userinfo"))) if links else None

    async def merged(self, token: str):
        res = await asyncio.gather(*[self._one(k, u, v, token) for k, u, v in self.sources])
        res = [r for r in res if r]
        if not res:
            return None
        links = [l for r in res for l in r[0]]
        infos = [r[1] for r in res]
        up = sum(i.get("upload", 0) for i in infos)
        down = sum(i.get("download", 0) for i in infos)
        total = 0 if any(not i.get("total") for i in infos) else sum(i["total"] for i in infos)
        expire = max((i.get("expire", 0) for i in infos), default=0)
        return links, f"upload={up}; download={down}; total={total}; expire={expire}"

    def app(self) -> web.Application:
        async def sub(request: web.Request):
            token = request.match_info["token"]
            if not TOKEN_RE.match(token):
                raise web.HTTPNotFound()
            m = await self.merged(token)
            if not m:
                raise web.HTTPNotFound()
            links, info = m
            return web.Response(text=_b64("\n".join(links)), content_type="text/plain", headers={
                "Profile-Title": "base64:" + _b64(self.brand),
                "Subscription-Userinfo": info,
                "Profile-Update-Interval": "12",
                "Support-Url": f"https://t.me/{self.support.lstrip('@')}",
                "Content-Disposition": f'attachment; filename="{self.brand}"',
            })

        async def health(_):
            return web.Response(text="ok")

        a = web.Application()
        a.router.add_get("/sub/{token}", sub)
        a.router.add_get("/health", health)
        return a


async def start(host: str, port: int, merger: Merger) -> web.AppRunner:
    runner = web.AppRunner(merger.app())
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner


def merger_from_config() -> Merger:
    from . import config
    sources = [(l.key, l.sub_url, l.verify_ssl) for l in config.LOCATIONS if l.sub_url]
    return Merger(sources, config.BRAND, config.SUPPORT)
