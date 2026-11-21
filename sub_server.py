"""Отдельный сервер склейки подписок — можно запустить на любом сервере с доменом.

Нужен только Python и переменные окружения (базу бота он не читает):

  SUB_SOURCES=pl=https://s-25434.pl17.h1clayd.shop/xxxx,de=https://s-25471.de20.h1clayd.click/yyyy
  BRAND=TelezhkaProxy
  SUPPORT=@TelezhkaProxy
  WEB_HOST=127.0.0.1
  WEB_PORT=8080

  pip install aiohttp httpx python-dotenv
  python sub_server.py

Потом вешаешь за Caddy/nginx на домен, и в боте ставишь SUB_PUBLIC_URL=https://твой.домен
"""
import asyncio
import logging
import os

from dotenv import load_dotenv

from app.web import Merger, start


async def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sources = []
    for item in (os.getenv("SUB_SOURCES") or "").split(","):
        key, _, url = item.strip().partition("=")
        if key and url:
            sources.append((key.strip(), url.strip().rstrip("/"), True))
    if not sources:
        raise SystemExit("задай SUB_SOURCES=pl=https://...,de=https://...")
    m = Merger(sources, os.getenv("BRAND", "TelezhkaProxy"), os.getenv("SUPPORT", "@TelezhkaProxy"))
    host, port = os.getenv("WEB_HOST", "127.0.0.1"), int(os.getenv("WEB_PORT", "8080"))
    runner = await start(host, port, m)
    logging.info("склейка подписок на %s:%s, источники: %s", host, port, ", ".join(s[0] for s in sources))
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await m.close()


if __name__ == "__main__":
    asyncio.run(main())
