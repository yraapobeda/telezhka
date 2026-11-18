"""Фон: автопроверка крипто-счетов, напоминания об окончании, чистка панели."""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot

from .config import PLANS
from .cryptopay import CryptoPay
from .db import DB
from .service import finalize_payment, remove_clients
from .views import fmt_date, kb

log = logging.getLogger(__name__)
CLEANUP_AFTER = 7 * 86400  # через сколько после окончания удалять клиента из панели


async def poll_crypto(bot: Bot, db: DB, xuis, crypto: CryptoPay):
    pending = await db.pending_crypto()
    if not pending:
        return
    by_ext = {p["external_id"]: p for p in pending}
    ids = list(by_ext)
    for i in range(0, len(ids), 100):
        for inv in await crypto.get_invoices(ids[i:i + 100]):
            p = by_ext.get(str(inv["invoice_id"]))
            if not p:
                continue
            if inv["status"] == "paid":
                await finalize_payment(bot, db, xuis, p["id"])
            elif inv["status"] == "expired":
                await db.mark_status(p["id"], "expired")


async def _send(bot: Bot, uid: int, text: str, markup=None):
    try:
        await bot.send_message(uid, text, reply_markup=markup)
    except Exception:
        pass  # юзер заблочил бота


async def notify(bot: Bot, db: DB):
    now = int(time.time())
    for s in await db.subs_expiring(now, 86400):
        plan = PLANS.get(s.plan_key)
        if plan and not plan.once:
            loc = "all" if plan.all_locations else s.locations[0]
            markup = kb([("🔄 Продлить", f"pay:{plan.key}:{loc}")])
        else:
            markup = kb([("🛒 Выбрать тариф", "buy")])
        await _send(bot, s.user_id, f"⏳ Подписка закончится {fmt_date(s.expires_at)}. Продлишь?", markup)
        await db.set_notified(s.user_id, 1)

    for s in await db.subs_just_expired(now):
        await _send(bot, s.user_id, "⛔ Подписка закончилась, ускоритель отключён.\nВключить обратно можно в пару кликов 👇",
                    kb([("🛒 Купить", "buy")]))
        await db.set_notified(s.user_id, 2)


async def cleanup(db: DB, xuis):
    for s in await db.subs_expired_before(int(time.time()) - CLEANUP_AFTER):
        if await remove_clients(xuis, s):
            await db.delete_sub(s.user_id)


async def run(bot: Bot, db: DB, xuis, crypto: CryptoPay | None):
    tick = 0
    while True:
        for name, coro, every in (
            ("crypto", (lambda: poll_crypto(bot, db, xuis, crypto)) if crypto else None, 1),
            ("notify", lambda: notify(bot, db), 10),
            ("cleanup", lambda: cleanup(db, xuis), 360),
        ):
            if coro and tick % every == 0:
                try:
                    await coro()
                except Exception:
                    log.exception("scheduler %s", name)
        tick += 1
        await asyncio.sleep(60)
