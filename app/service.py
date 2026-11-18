"""Бизнес-логика: выдача/продление подписки, ключи, трафик, проведение оплаты."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid as uuidlib

from aiogram import Bot

from . import config
from .config import LOC_BY_KEY, LOCATIONS, PLANS
from .db import DB, Sub
from .links import build_vless, client_flow, transport_label
from .xui import XUI

log = logging.getLogger(__name__)
GB = 1024 ** 3
_locks: dict[int, asyncio.Lock] = {}


def _lock(uid: int) -> asyncio.Lock:
    return _locks.setdefault(uid, asyncio.Lock())


def email_for(uid: int, loc: str) -> str:
    return f"tg{uid}-{loc}"


def sub_id(sub_token: str, loc: str) -> str:
    return f"{sub_token}{loc}"


def plan_locations(plan_key: str, loc: str) -> list[str]:
    plan = PLANS[plan_key]
    return [l.key for l in LOCATIONS] if plan.all_locations else [loc]


def check_order(plan_key: str, loc: str, trial_used: bool) -> str | None:
    """None если заказ валиден, иначе текст ошибки."""
    plan = PLANS.get(plan_key)
    if not plan:
        return "Такого тарифа нет"
    if plan.all_locations != (loc == "all"):
        return "Неверная локация"
    if loc != "all" and loc not in LOC_BY_KEY:
        return "Эта локация сейчас недоступна"
    if plan.once and trial_used:
        return "Пробный тариф можно взять только один раз"
    return None


async def activate(db: DB, xuis: dict[str, XUI], uid: int, plan_key: str, loc: str,
                   days: int | None = None) -> Sub:
    plan = PLANS[plan_key]
    days = days or plan.days
    locs = plan_locations(plan_key, loc)

    async with _lock(uid):
        user = await db.get_user(uid)
        old = await db.get_sub(uid)
        now = int(time.time())
        same = bool(old) and old.plan_key == plan_key and sorted(old.locations) == sorted(locs)

        # UUID у юзера постоянный — меньше мороки с дублями email в панели
        client_uuid = old.uuid if old else str(uuidlib.uuid4())
        base = max(now, old.expires_at) if same else now
        expires = base + days * 86400

        if old:  # убираем клиента с локаций, которых нет в новом тарифе
            for k in set(old.locations) - set(locs):
                if k in xuis:
                    try:
                        await xuis[k].del_client(email_for(uid, k))
                    except Exception as e:
                        log.warning("del_client %s %s: %s", uid, k, e)

        for k in locs:
            x = xuis[k]
            inbounds = await x.get_inbounds()
            await x.upsert_client({
                "id": client_uuid,
                # панель сама уберёт flow на inbound'ах, где он не нужен
                "flow": "xtls-rprx-vision" if any(client_flow(i) for i in inbounds) else "",
                "email": email_for(uid, k),
                "limitIp": config.DEVICE_LIMIT,
                "totalGB": plan.traffic_gb * GB,
                "expiryTime": expires * 1000,
                "enable": True,
                "subId": sub_id(user["sub_token"], k),
                "tgId": uid,
                "reset": 0,
                "comment": f"tg {uid}",
            })
            try:
                await x.reset_traffic(email_for(uid, k))
            except Exception as e:
                log.warning("reset_traffic %s %s: %s", uid, k, e)

        await db.save_sub(uid, plan_key, locs, client_uuid, expires)
        if plan.once:
            await db.set_trial_used(uid)
        return await db.get_sub(uid)


async def remove_clients(xuis: dict[str, XUI], sub: Sub) -> bool:
    ok = True
    for k in sub.locations:
        if k not in xuis:
            continue
        try:
            await xuis[k].del_client(email_for(sub.user_id, k))
        except Exception as e:
            log.warning("cleanup %s %s: %s", sub.user_id, k, e)
            ok = False
    return ok


async def sub_links(xuis: dict[str, XUI], sub: Sub) -> list[str]:
    out = []
    for k in sub.locations:
        loc, x = LOC_BY_KEY.get(k), xuis.get(k)
        if not loc or not x:
            continue
        try:
            inbounds = await x.get_inbounds()
        except Exception as e:
            log.warning("get_inbounds %s: %s", k, e)
            continue
        for ib in inbounds:
            name = f"{loc.flag} {loc.name}"
            if len(inbounds) > 1:
                name += f" · {transport_label(ib)}"
            out.append(build_vless(ib, sub.uuid, loc.host, f"{name} | {config.BRAND}"))
    return out


async def sub_traffic(xuis: dict[str, XUI], sub: Sub) -> tuple[int, int, int]:
    up = down = 0
    for k in sub.locations:
        if k in xuis:
            t = await xuis[k].get_traffic(email_for(sub.user_id, k))
            if t:
                up += t.get("up", 0)
                down += t.get("down", 0)
    plan = PLANS.get(sub.plan_key)
    total = plan.traffic_gb * GB if plan else 0
    return up, down, total


async def _notify_admins(bot: Bot, text: str):
    for a in config.ADMIN_IDS:
        try:
            await bot.send_message(a, text)
        except Exception:
            pass


async def _ref_bonus(bot: Bot, db: DB, uid: int, rub: int):
    """Процент пригласившему с реальной оплаты (не с баланса)."""
    user = await db.get_user(uid)
    ref = user["referrer_id"] if user else None
    bonus = rub * config.REF_PERCENT // 100
    if not ref or bonus <= 0 or not await db.get_user(ref):
        return
    await db.add_ref_earned(ref, bonus)
    try:
        await bot.send_message(ref, f"👥 Твой реферал сделал оплату, тебе +{bonus}₽ на баланс")
    except Exception:
        pass


async def finalize_payment(bot: Bot, db: DB, xuis: dict[str, XUI], pid: int) -> bool:
    """Проводит оплату ровно один раз: пополнение баланса или выдача подписки."""
    if not await db.mark_paid(pid):
        return False
    p = await db.get_payment(pid)
    uid, rub = p["user_id"], p["rub"]

    if p["method"] != "balance":
        await _ref_bonus(bot, db, uid, rub)

    if p["plan_key"] == "topup":
        await db.add_balance(uid, rub)
        user = await db.get_user(uid)
        await bot.send_message(uid, f"💰 Баланс пополнен на <b>{rub}₽</b>\nТеперь на счету: <b>{user['balance']}₽</b>",
                               reply_markup=_kb_after_topup())
        await _notify_admins(bot, f"💰 #{pid}: пополнение {rub}₽ ({p['amount']} {p['currency']}) · {uid}")
        return True

    plan = PLANS.get(p["plan_key"])
    if plan and rub < plan.rub:  # оплатил со скидкой → скидка израсходована
        await db.set_discount(uid, 0, None)

    try:
        await activate(db, xuis, uid, p["plan_key"], p["location"])
    except Exception:
        log.exception("activate failed for payment %s", pid)
        if p["method"] == "balance":
            await db.add_balance(uid, rub)
            await bot.send_message(uid, f"Не получилось выдать ключ из-за сбоя сервера, {rub}₽ вернулись на баланс. "
                                        f"Попробуй чуть позже или напиши {config.SUPPORT}.")
        else:
            await bot.send_message(uid, f"Оплата прошла ✅, но выдать ключ не получилось из-за сбоя сервера.\n"
                                        f"Напиши в поддержку {config.SUPPORT} и укажи номер платежа <b>#{pid}</b>, всё починим.")
        await _notify_admins(bot, f"⚠️ Платёж #{pid} ({p['method']}) прошёл, но activate упал. Юзер {uid}, "
                                  f"тариф {p['plan_key']}/{p['location']}."
                                  + ("" if p["method"] == "balance" else " Выдай через /grant."))
        return True

    from .views import sub_view  # локальный импорт, чтобы не было циклов
    text, kb = await sub_view(db, xuis, uid)
    await bot.send_message(uid, "🎉 <b>Оплата прошла, подписка активна!</b>\n\n" + text, reply_markup=kb)
    await _notify_admins(bot, f"💰 #{pid}: {p['amount']} {p['currency']} ({rub}₽) · {p['plan_key']}/{p['location']} · {uid}")
    return True


def _kb_after_topup():
    from .views import kb
    return kb([("🛒 Купить тариф", "buy")], [("◀️ Меню", "menu")])


async def pay_from_balance(bot: Bot, db: DB, xuis, uid: int, plan_key: str, loc: str, price: int) -> bool:
    if not await db.take_balance(uid, price):
        return False
    pid = await db.create_payment(uid, plan_key, loc, "balance", price, "RUB", rub=price)
    await finalize_payment(bot, db, xuis, pid)
    return True


async def apply_promo(db: DB, xuis, uid: int, code: str) -> str:
    """Активирует промокод и возвращает текст для юзера."""
    code = code.strip().upper()
    err = await db.use_promo(code, uid)
    if err:
        return "❌ " + err
    promo = await db.get_promo(code)
    kind, value = promo["kind"], promo["value"]
    try:
        if kind == "sale":
            await db.set_discount(uid, value, code)
            return f"✅ Скидка <b>{value}%</b> на следующую покупку тарифа. Цены в «🛒 Купить» уже пересчитаны."
        if kind == "money":
            await db.add_balance(uid, value)
            user = await db.get_user(uid)
            return f"✅ +{value}₽ на баланс. Теперь на счету: <b>{user['balance']}₽</b>"
        if kind == "days":
            sub = await db.get_sub(uid)
            plan = PLANS.get(sub.plan_key) if sub and sub.active else None
            if plan and not plan.once:  # продлеваем текущий тариф
                loc = "all" if plan.all_locations else sub.locations[0]
                sub = await activate(db, xuis, uid, plan.key, loc, value)
            else:
                sub = await activate(db, xuis, uid, "unlim", LOCATIONS[0].key, value)
            from .views import fmt_date
            return f"✅ +{value} дн. подписки, теперь до {fmt_date(sub.expires_at)}. Ключ в «👤 Моя подписка»."
    except Exception:
        log.exception("apply_promo %s %s", code, uid)
        await db.unuse_promo(code, uid)
        return f"❌ Сервер сейчас не отвечает, промокод не потрачен. Попробуй позже."
    await db.unuse_promo(code, uid)
    return "❌ Битый промокод"
