"""Свой сервер подписок: одна ссылка → ключи со всех локаций + трафик и срок в приложении."""
from __future__ import annotations

import base64

from aiohttp import web

from . import config
from .db import DB
from .service import sub_links, sub_traffic


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def make_app(db: DB, xuis) -> web.Application:
    async def sub(request: web.Request):
        user = await db.user_by_token(request.match_info["token"])
        s = await db.get_sub(user["id"]) if user else None
        if not s:
            raise web.HTTPNotFound()
        links = await sub_links(xuis, s)
        up, down, total = await sub_traffic(xuis, s)
        headers = {
            "Profile-Title": "base64:" + _b64(config.BRAND),
            "Subscription-Userinfo": f"upload={up}; download={down}; total={total}; expire={s.expires_at}",
            "Profile-Update-Interval": "12",
            "Support-Url": f"https://t.me/{config.SUPPORT.lstrip('@')}",
        }
        return web.Response(text=_b64("\n".join(links)), content_type="text/plain", headers=headers)

    async def health(_):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/sub/{token}", sub)
    app.router.add_get("/health", health)
    return app


async def start(db: DB, xuis) -> web.AppRunner:
    runner = web.AppRunner(make_app(db, xuis))
    await runner.setup()
    await web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT).start()
    return runner
