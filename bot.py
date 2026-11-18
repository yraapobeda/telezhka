import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from app import config, scheduler, web
from app.cryptopay import CryptoPay
from app.db import DB
from app.handlers import admin, user
from app.middlewares import ChannelGate
from app.xui import XUI


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("telezhka")

    db = await DB.open(config.DB_PATH)
    xuis = {l.key: XUI(l) for l in config.LOCATIONS}
    crypto = CryptoPay(config.CRYPTOPAY_TOKEN, config.CRYPTOPAY_TESTNET, config.CRYPTOPAY_ASSETS) \
        if config.CRYPTOPAY_TOKEN else None

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(db=db, xuis=xuis, crypto=crypto)  # прокидываются в хендлеры по имени

    if config.REQUIRED_CHANNEL:
        gate = ChannelGate(config.REQUIRED_CHANNEL)
        user.router.message.middleware(gate)
        user.router.callback_query.middleware(gate)
    dp.include_routers(admin.router, user.router)

    runner = None
    if config.WEB_PORT:
        runner = await web.start(db, xuis)
        log.info("сервер подписок на %s:%s", config.WEB_HOST, config.WEB_PORT)

    for l in config.LOCATIONS:
        try:
            await xuis[l.key].get_inbounds(fresh=True)
            log.info("панель %s ок", l.key)
        except Exception as e:
            log.error("панель %s недоступна: %s", l.key, e)

    task = asyncio.create_task(scheduler.run(bot, db, xuis, crypto))
    log.info("%s запущен, локации: %s, крипта: %s", config.BRAND,
             ", ".join(l.key for l in config.LOCATIONS), "вкл" if crypto else "выкл")
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        if runner:
            await runner.cleanup()
        for x in xuis.values():
            await x.close()
        if crypto:
            await crypto.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
