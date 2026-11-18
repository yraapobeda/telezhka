from __future__ import annotations

import asyncio
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .. import config
from ..config import LOCATIONS, PLANS
from ..db import DB
from ..service import activate
from ..views import fmt_date

router = Router()
router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))

ADMIN_HELP = (
    "<b>Админка</b>\n"
    "/stats — юзеры, подписки, выручка за 30 дней\n"
    "/locs — проверить панели\n"
    "/grant <code>user_id plan [loc] [days]</code> — выдать подписку\n"
    "   планы: " + ", ".join(PLANS) + "; loc: " + ", ".join(l.key for l in LOCATIONS) + " или all\n"
    "/user <code>user_id</code> — инфа о юзере\n"
    "/balance <code>user_id +100</code> — начислить (или <code>-100</code> списать)\n\n"
    "<b>Промокоды</b>\n"
    "/promo <code>КОД тип значение [активаций] [дней_действует]</code>\n"
    "   типы: <code>sale</code> — скидка %, <code>money</code> — ₽ на баланс, <code>days</code> — дни подписки\n"
    "   пример: <code>/promo NEWYEAR sale 20 100 14</code>\n"
    "/promos — список, /delpromo <code>КОД</code> — удалить\n"
    "/broadcast — ответь этой командой на сообщение, разошлю всем"
)


@router.message(Command("admin"))
async def cmd_admin(m: Message):
    await m.answer(ADMIN_HELP)


@router.message(Command("stats"))
async def cmd_stats(m: Message, db: DB):
    rev = await db.revenue(int(time.time()) - 30 * 86400)
    names = {"XTR": "⭐", "RUB": "₽ (крипта/баланс)"}
    rev_txt = "\n".join(f"  {r['s']} {names.get(r['currency'], r['currency'])} ({r['n']} шт.)" for r in rev) or "  пока пусто"
    await m.answer(f"👥 Юзеров: {await db.count_users()}\n"
                   f"✅ Активных подписок: {await db.count_active_subs()}\n"
                   f"💰 За 30 дней:\n{rev_txt}")


@router.message(Command("locs"))
async def cmd_locs(m: Message, xuis):
    lines = []
    for l in LOCATIONS:
        try:
            for ib in await xuis[l.key].get_inbounds(fresh=True):
                s = ib["stream"]
                lines.append(f"✅ {l.label}: inbound {ib['id']} «{ib.get('remark', '')}», порт {ib['port']}, "
                             f"{s.get('network')}+{s.get('security')}")
        except Exception as e:
            lines.append(f"❌ {l.label}: {e}")
    await m.answer("\n".join(lines))


@router.message(Command("grant"))
async def cmd_grant(m: Message, command: CommandObject, db: DB, xuis, bot: Bot):
    args = (command.args or "").split()
    if len(args) < 2 or args[1] not in PLANS:
        return await m.answer("Формат: /grant user_id plan [loc] [days]")
    uid, plan_key = int(args[0]), args[1]
    plan = PLANS[plan_key]
    loc = args[2] if len(args) > 2 else ("all" if plan.all_locations else LOCATIONS[0].key)
    days = int(args[3]) if len(args) > 3 else None
    await db.ensure_user(uid, None)
    try:
        sub = await activate(db, xuis, uid, plan_key, loc, days)
    except Exception as e:
        return await m.answer(f"❌ {e}")
    await m.answer(f"✅ Выдано {uid}: {plan.title}, до {fmt_date(sub.expires_at)}")
    try:
        await bot.send_message(uid, "🎁 Тебе выдали подписку! Открой /start → «Моя подписка».")
    except Exception:
        pass


@router.message(Command("user"))
async def cmd_user(m: Message, command: CommandObject, db: DB):
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("Формат: /user user_id")
    uid = int(command.args)
    u = await db.get_user(uid)
    if not u:
        return await m.answer("Не найден")
    s = await db.get_sub(uid)
    sub = f"{s.plan_key} {','.join(s.locations)} до {fmt_date(s.expires_at)}" if s else "нет"
    n, earned = await db.ref_stats(uid)
    await m.answer(f"ID {uid} @{u['username']}\n"
                   f"баланс: {u['balance']}₽, скидка: {u['discount']}%\n"
                   f"пробник: {'да' if u['trial_used'] else 'нет'}\n"
                   f"подписка: {sub}\n"
                   f"пригласил его: {u['referrer_id'] or '—'}; он пригласил: {n}, заработал {earned}₽")


@router.message(Command("balance"))
async def cmd_balance(m: Message, command: CommandObject, db: DB, bot: Bot):
    args = (command.args or "").split()
    if len(args) != 2 or not args[0].isdigit() or not args[1].lstrip("+-").isdigit():
        return await m.answer("Формат: /balance user_id +100  (или -100)")
    uid, delta = int(args[0]), int(args[1])
    if not await db.get_user(uid):
        return await m.answer("Юзер не найден (он должен хоть раз нажать /start)")
    await db.add_balance(uid, delta)
    u = await db.get_user(uid)
    await m.answer(f"✅ Баланс {uid}: {u['balance']}₽")
    if delta > 0:
        try:
            await bot.send_message(uid, f"💰 Тебе начислили {delta}₽ на баланс. Сейчас на счету: {u['balance']}₽")
        except Exception:
            pass


PROMO_KINDS = {"sale": "скидка", "money": "на баланс", "days": "дней"}


@router.message(Command("promo"))
async def cmd_promo(m: Message, command: CommandObject, db: DB):
    args = (command.args or "").split()
    if len(args) < 3 or args[1] not in PROMO_KINDS or not all(a.isdigit() for a in args[2:5]):
        return await m.answer("Формат: /promo КОД тип значение [активаций] [дней_действует]\n"
                              "типы: sale (скидка %), money (₽ на баланс), days (дни подписки)")
    code, kind, value = args[0].upper(), args[1], int(args[2])
    uses = int(args[3]) if len(args) > 3 else 0
    valid_days = int(args[4]) if len(args) > 4 else 0
    if kind == "sale" and not 1 <= value <= 99:
        return await m.answer("Скидка от 1 до 99%")
    if value <= 0:
        return await m.answer("Значение должно быть больше нуля")
    expires = int(time.time()) + valid_days * 86400 if valid_days else 0
    if not await db.create_promo(code, kind, value, uses, expires):
        return await m.answer("Такой код уже есть")
    await m.answer(f"✅ <code>{code}</code>: {_promo_desc(kind, value)}, "
                   f"активаций: {uses or '∞'}, действует: {fmt_date(expires) if expires else 'бессрочно'}")


def _promo_desc(kind: str, value: int) -> str:
    return {"sale": f"скидка {value}%", "money": f"+{value}₽ на баланс", "days": f"+{value} дн. подписки"}[kind]


@router.message(Command("promos"))
async def cmd_promos(m: Message, db: DB):
    rows = await db.list_promos()
    if not rows:
        return await m.answer("Промокодов нет")
    now = int(time.time())
    lines = []
    for p in rows:
        dead = (p["expires_at"] and p["expires_at"] < now) or (p["max_uses"] and p["uses"] >= p["max_uses"])
        lines.append(f"{'⛔' if dead else '✅'} <code>{p['code']}</code> — {_promo_desc(p['kind'], p['value'])}, "
                     f"{p['uses']}/{p['max_uses'] or '∞'}"
                     + (f", до {fmt_date(p['expires_at'])}" if p["expires_at"] else ""))
    await m.answer("\n".join(lines))


@router.message(Command("delpromo"))
async def cmd_delpromo(m: Message, command: CommandObject, db: DB):
    code = (command.args or "").strip().upper()
    await m.answer("✅ Удалён" if code and await db.delete_promo(code) else "Нет такого кода")


@router.message(Command("broadcast"))
async def cmd_broadcast(m: Message, db: DB, bot: Bot):
    if not m.reply_to_message:
        return await m.answer("Ответь /broadcast на сообщение, которое надо разослать")
    ids = await db.all_user_ids()
    ok = 0
    for uid in ids:
        try:
            await bot.copy_message(uid, m.chat.id, m.reply_to_message.message_id)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await m.answer(f"Разослано: {ok}/{len(ids)}")
