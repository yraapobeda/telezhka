from __future__ import annotations

import logging
import sqlite3

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from .. import config
from ..config import LOCATIONS, PLANS, discounted, rub_to_stars, visible_plans
from ..cryptopay import CryptoPay
from ..db import DB
from ..service import apply_promo, check_order, finalize_payment, pay_from_balance, plan_locations
from ..views import (HELP_TEXT, balance_text, docs_kb, kb, loc_label, main_kb, main_text, plans_kb, plans_text,
                     ref_text, sub_view, terms_note)

log = logging.getLogger(__name__)
router = Router()


class Input(StatesGroup):
    promo = State()
    topup = State()


async def show(c: CallbackQuery, text: str, markup=None):
    """Редактирует сообщение, а если нельзя — шлёт новое."""
    try:
        await c.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            await c.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
    await c.answer()


async def user_of(db: DB, u) -> sqlite3.Row:
    await db.ensure_user(u.id, u.username)
    return await db.get_user(u.id)


def plan_price(plan_key: str, user) -> tuple[int, int]:
    """(рубли, звёзды) с учётом скидки юзера. На пробник скидка не действует."""
    plan = PLANS[plan_key]
    pct = 0 if plan.once else user["discount"]
    if not pct:
        return plan.rub, plan.stars
    return discounted(plan.rub, pct), discounted(plan.stars, pct)


# ── меню ──
@router.message(CommandStart())
async def cmd_start(m: Message, command: CommandObject, db: DB, state: FSMContext, bot: Bot):
    await state.clear()
    ref = None
    if command.args and command.args.startswith("ref_") and command.args[4:].isdigit():
        ref = int(command.args[4:])
    is_new = await db.ensure_user(m.from_user.id, m.from_user.username, referrer_id=ref)
    if is_new and ref and ref != m.from_user.id and await db.get_user(ref):
        try:
            await bot.send_message(ref, "👥 По твоей ссылке пришёл новый юзер. "
                                        f"С каждой его оплаты тебе {config.REF_PERCENT}% на баланс.")
        except Exception:
            pass
    await m.answer(main_text(), reply_markup=main_kb())


@router.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery, db: DB, state: FSMContext):
    await state.clear()
    await user_of(db, c.from_user)
    await show(c, main_text(), main_kb())


@router.callback_query(F.data == "docs")
async def cb_docs(c: CallbackQuery):
    await show(c, "📄 <b>Документы</b>", docs_kb())


@router.callback_query(F.data == "help")
async def cb_help(c: CallbackQuery):
    await show(c, HELP_TEXT, kb([("👤 Моя подписка", "my")], [("◀️ Меню", "menu")]))


@router.callback_query(F.data == "my")
async def cb_my(c: CallbackQuery, db: DB, xuis):
    await user_of(db, c.from_user)
    text, markup = await sub_view(db, xuis, c.from_user.id)
    await show(c, text, markup)


# ── покупка ──
@router.callback_query(F.data == "buy")
async def cb_buy(c: CallbackQuery, db: DB):
    user = await user_of(db, c.from_user)
    plans = visible_plans(bool(user["trial_used"]))
    await show(c, plans_text(plans, user), plans_kb(plans, user))


@router.callback_query(F.data.startswith("plan:"))
async def cb_plan(c: CallbackQuery, db: DB, crypto: CryptoPay | None):
    plan = PLANS.get(c.data.split(":", 1)[1])
    if not plan:
        return await c.answer("Тариф не найден", show_alert=True)
    if plan.all_locations:
        return await show_pay(c, db, crypto, plan.key, "all")
    if len(LOCATIONS) == 1:
        return await show_pay(c, db, crypto, plan.key, LOCATIONS[0].key)
    rows = [[(l.label, f"pay:{plan.key}:{l.key}")] for l in LOCATIONS]
    await show(c, f"<b>{plan.title}</b>\n\nВыбери страну:", kb(*rows, [("◀️ Назад", "buy")]))


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay(c: CallbackQuery, db: DB, crypto: CryptoPay | None):
    _, plan_key, loc = c.data.split(":")
    await show_pay(c, db, crypto, plan_key, loc)


async def show_pay(c: CallbackQuery, db: DB, crypto: CryptoPay | None, plan_key: str, loc: str):
    user = await user_of(db, c.from_user)
    err = check_order(plan_key, loc, bool(user["trial_used"]))
    if err:
        return await c.answer(err, show_alert=True)
    plan = PLANS[plan_key]
    rub, stars = plan_price(plan_key, user)

    note = ""
    sub = await db.get_sub(c.from_user.id)
    if sub and sub.active:
        same = sub.plan_key == plan_key and sorted(sub.locations) == sorted(plan_locations(plan_key, loc))
        if same:
            note = "\n\n➕ Дни добавятся к текущей подписке, счётчик трафика обнулится."
        else:
            note = ("\n\n⚠️ Текущая подписка заменится новой, оставшиеся дни не переносятся. "
                    "Ключ останется тем же.")

    price = f"{rub}₽ или {stars}⭐"
    if rub < plan.rub:
        price = f"<s>{plan.rub}₽</s> {rub}₽ или {stars}⭐  (скидка {user['discount']}%)"
    traffic = "безлимит" if not plan.traffic_gb else f"{plan.traffic_gb} ГБ"
    text = (f"<b>{plan.title}</b>\n"
            f"📍 {loc_label(loc)}\n"
            f"📦 {traffic} · {plan.days} дней\n"
            f"💵 {price}\n"
            f"💰 На балансе: {user['balance']}₽{note}\n\n"
            f"Как платишь?{terms_note()}")
    rows = []
    if user["balance"] >= rub:
        rows.append([(f"💰 С баланса — {rub}₽", f"bal:{plan_key}:{loc}")])
    rows.append([(f"⭐ Telegram Stars — {stars}⭐", f"stars:{plan_key}:{loc}")])
    if crypto:
        rows.append([(f"💎 Крипта (USDT/TON) — {rub}₽", f"crypto:{plan_key}:{loc}")])
    rows.append([("◀️ Назад", "buy")])
    await show(c, text, kb(*rows))


@router.callback_query(F.data.startswith("bal:"))
async def cb_bal_pay(c: CallbackQuery, db: DB, xuis, bot: Bot):
    _, plan_key, loc = c.data.split(":")
    user = await user_of(db, c.from_user)
    err = check_order(plan_key, loc, bool(user["trial_used"]))
    if err:
        return await c.answer(err, show_alert=True)
    rub, _ = plan_price(plan_key, user)
    await c.answer()
    if not await pay_from_balance(bot, db, xuis, c.from_user.id, plan_key, loc, rub):
        await c.message.answer("Не хватает денег на балансе", reply_markup=kb([("💰 Пополнить", "balance")]))


# ── Stars ──
@router.callback_query(F.data.startswith("stars:"))
async def cb_stars(c: CallbackQuery, db: DB):
    _, plan_key, loc = c.data.split(":")
    user = await user_of(db, c.from_user)
    err = check_order(plan_key, loc, bool(user["trial_used"]))
    if err:
        return await c.answer(err, show_alert=True)
    plan = PLANS[plan_key]
    rub, stars = plan_price(plan_key, user)
    await c.message.answer_invoice(
        title=f"{config.BRAND}: {plan.title}",
        description=f"Подписка на {plan.days} дней · {loc_label(loc)}",
        payload=f"p:{plan_key}:{loc}:{rub}",
        currency="XTR",
        prices=[LabeledPrice(label=plan.title, amount=stars)],
    )
    await c.answer()


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, db: DB):
    parts = q.invoice_payload.split(":")
    if parts[0] == "t" and len(parts) == 2:
        return await q.answer(ok=True)
    if parts[0] != "p" or len(parts) != 4:
        return await q.answer(ok=False, error_message="Битый счёт, создай новый")
    user = await user_of(db, q.from_user)
    err = check_order(parts[1], parts[2], bool(user["trial_used"]))
    await q.answer(ok=err is None, error_message=err)


@router.message(F.successful_payment)
async def on_paid(m: Message, db: DB, xuis, bot: Bot):
    sp = m.successful_payment
    parts = sp.invoice_payload.split(":")
    if parts[0] == "t":
        plan_key, loc, rub = "topup", "", int(parts[1])
    else:
        plan_key, loc, rub = parts[1], parts[2], int(parts[3])
    try:
        pid = await db.create_payment(m.from_user.id, plan_key, loc, "stars", sp.total_amount, "XTR",
                                      external_id=sp.telegram_payment_charge_id, rub=rub)
    except sqlite3.IntegrityError:
        return  # этот платёж уже проведён
    await finalize_payment(bot, db, xuis, pid)


# ── CryptoBot ──
async def crypto_invoice(c: CallbackQuery, db: DB, crypto: CryptoPay, plan_key: str, loc: str,
                         rub: int, title: str, back: str):
    pid = await db.create_payment(c.from_user.id, plan_key, loc, "crypto", rub, "RUB", rub=rub)
    try:
        inv = await crypto.create_invoice(rub, f"{config.BRAND}: {title}", str(pid))
    except Exception as e:
        log.warning("crypto invoice: %s", e)
        await db.mark_status(pid, "failed")
        return await c.answer("CryptoBot не отвечает, попробуй позже или оплати звёздами", show_alert=True)
    await db.set_external(pid, str(inv["invoice_id"]))
    await show(c,
               f"💎 Счёт на <b>{rub}₽</b> создан (оплата в USDT или TON по курсу).\n\n"
               f"После оплаты всё зачислится само в течение минуты, "
               f"либо нажми «Я оплатил». Счёт действует 1 час.",
               kb([("💎 Оплатить", f"url:{inv['url']}")],
                  [("✅ Я оплатил", f"check:{pid}")],
                  [("◀️ Назад", back)]))


@router.callback_query(F.data.startswith("crypto:"))
async def cb_crypto(c: CallbackQuery, db: DB, crypto: CryptoPay | None):
    if not crypto:
        return await c.answer("Оплата криптой сейчас выключена", show_alert=True)
    _, plan_key, loc = c.data.split(":")
    user = await user_of(db, c.from_user)
    err = check_order(plan_key, loc, bool(user["trial_used"]))
    if err:
        return await c.answer(err, show_alert=True)
    rub, _ = plan_price(plan_key, user)
    await crypto_invoice(c, db, crypto, plan_key, loc, rub,
                         f"{PLANS[plan_key].title} · {loc_label(loc)}", "buy")


@router.callback_query(F.data.startswith("check:"))
async def cb_check(c: CallbackQuery, db: DB, crypto: CryptoPay | None, xuis, bot: Bot):
    pid = int(c.data.split(":")[1])
    p = await db.get_payment(pid)
    if not p or p["user_id"] != c.from_user.id or not crypto:
        return await c.answer("Платёж не найден", show_alert=True)
    if p["status"] == "paid":
        return await c.answer("Уже оплачено ✅", show_alert=True)
    if p["status"] != "pending":
        return await c.answer("Этот счёт уже недействителен, создай новый", show_alert=True)

    try:
        items = await crypto.get_invoices([p["external_id"]])
    except Exception:
        return await c.answer("CryptoBot не отвечает, попробуй через минуту", show_alert=True)
    status = items[0]["status"] if items else "unknown"
    if status == "paid":
        await c.answer("Оплата найдена 🎉")
        await finalize_payment(bot, db, xuis, pid)
    elif status == "expired":
        await db.mark_status(pid, "expired")
        await c.answer("Счёт истёк, создай новый", show_alert=True)
    else:
        await c.answer("Оплата пока не пришла. Если уже заплатил, подожди минуту.", show_alert=True)


# ── баланс ──
@router.callback_query(F.data == "balance")
async def cb_balance(c: CallbackQuery, db: DB, state: FSMContext):
    await state.clear()
    user = await user_of(db, c.from_user)
    rows = []
    presets = config.TOPUP_PRESETS
    for i in range(0, len(presets), 2):
        rows.append([(f"+{r}₽", f"top:{r}") for r in presets[i:i + 2]])
    rows.append([("✏️ Своя сумма", "topcustom")])
    rows.append([("◀️ Меню", "menu")])
    await show(c, balance_text(user), kb(*rows))


@router.callback_query(F.data == "topcustom")
async def cb_top_custom(c: CallbackQuery, state: FSMContext):
    await state.set_state(Input.topup)
    await show(c, f"Напиши сумму пополнения в рублях (от {config.TOPUP_MIN} до {config.TOPUP_MAX}):",
               kb([("◀️ Отмена", "balance")]))


@router.message(StateFilter(Input.topup), F.text)
async def msg_top_custom(m: Message, state: FSMContext, crypto: CryptoPay | None):
    t = m.text.strip().replace("₽", "").replace(" ", "")
    if not t.isdigit() or not (config.TOPUP_MIN <= int(t) <= config.TOPUP_MAX):
        return await m.answer(f"Нужно число от {config.TOPUP_MIN} до {config.TOPUP_MAX}")
    await state.clear()
    text, markup = topup_method_view(int(t), crypto is not None)
    await m.answer(text, reply_markup=markup)


def topup_method_view(rub: int, crypto_on: bool = True):
    rows = [[(f"⭐ Telegram Stars — {rub_to_stars(rub)}⭐", f"tstars:{rub}")]]
    if crypto_on:
        rows.append([(f"💎 Крипта (USDT/TON) — {rub}₽", f"tcrypto:{rub}")])
    rows.append([("◀️ Назад", "balance")])
    return f"Пополнение на <b>{rub}₽</b>. Как платишь?{terms_note()}", kb(*rows)


@router.callback_query(F.data.startswith("top:"))
async def cb_top(c: CallbackQuery, crypto: CryptoPay | None):
    rub = int(c.data.split(":")[1])
    text, markup = topup_method_view(rub, crypto is not None)
    await show(c, text, markup)


def _topup_ok(rub: int) -> bool:
    return config.TOPUP_MIN <= rub <= config.TOPUP_MAX


@router.callback_query(F.data.startswith("tstars:"))
async def cb_top_stars(c: CallbackQuery):
    rub = int(c.data.split(":")[1])
    if not _topup_ok(rub):
        return await c.answer("Неверная сумма", show_alert=True)
    await c.message.answer_invoice(
        title=f"{config.BRAND}: пополнение баланса",
        description=f"+{rub}₽ на баланс",
        payload=f"t:{rub}",
        currency="XTR",
        prices=[LabeledPrice(label=f"+{rub}₽", amount=rub_to_stars(rub))],
    )
    await c.answer()


@router.callback_query(F.data.startswith("tcrypto:"))
async def cb_top_crypto(c: CallbackQuery, db: DB, crypto: CryptoPay | None):
    if not crypto:
        return await c.answer("Оплата криптой сейчас выключена", show_alert=True)
    rub = int(c.data.split(":")[1])
    if not _topup_ok(rub):
        return await c.answer("Неверная сумма", show_alert=True)
    await user_of(db, c.from_user)
    await crypto_invoice(c, db, crypto, "topup", "", rub, f"пополнение баланса +{rub}₽", "balance")


# ── промокод ──
@router.callback_query(F.data == "promo")
async def cb_promo(c: CallbackQuery, state: FSMContext):
    await state.set_state(Input.promo)
    await show(c, "🎁 Отправь промокод сообщением:", kb([("◀️ Отмена", "menu")]))


@router.message(StateFilter(Input.promo), F.text)
async def msg_promo(m: Message, db: DB, xuis, state: FSMContext):
    await state.clear()
    await user_of(db, m.from_user)
    text = await apply_promo(db, xuis, m.from_user.id, m.text)
    await m.answer(text, reply_markup=kb([("🛒 Купить", "buy"), ("👤 Подписка", "my")], [("◀️ Меню", "menu")]))


# ── рефералка ──
@router.callback_query(F.data == "ref")
async def cb_ref(c: CallbackQuery, db: DB, bot: Bot):
    await user_of(db, c.from_user)
    me = await bot.me()
    n, earned = await db.ref_stats(c.from_user.id)
    await show(c, ref_text(me.username, c.from_user.id, n, earned), kb([("◀️ Меню", "menu")]))
