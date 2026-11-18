"""Тексты и клавиатуры."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import config
from .config import LOC_BY_KEY, LOCATIONS, PLANS, Plan, discounted
from .db import DB
from .service import sub_id, sub_links, sub_traffic

MSK = timezone(timedelta(hours=3))


def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, MSK).strftime("%d.%m.%Y %H:%M") + " МСК"


def fmt_gb(b: int) -> str:
    return f"{b / 1024 ** 3:.1f} ГБ"


def loc_label(loc: str) -> str:
    if loc == "all":
        return "все страны"
    l = LOC_BY_KEY.get(loc)
    return l.label if l else loc


def kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Строки кнопок (текст, callback_data). Значение 'url:...' — кнопка-ссылка."""
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*[InlineKeyboardButton(text=t, url=v[4:]) if v.startswith("url:")
                else InlineKeyboardButton(text=t, callback_data=v) for t, v in row])
    return b.as_markup()


def main_text() -> str:
    locs = ", ".join(l.label for l in LOCATIONS)
    return (f"🚂 <b>{config.BRAND}</b> — ускоритель интернета\n\n"
            f"Ютуб без буферизации, соцсети и игры без тормозов.\n"
            f"Без рекламы, без логов, протокол VLESS Reality.\n\n"
            f"📍 Локации: {locs}")


def main_kb() -> InlineKeyboardMarkup:
    return kb([("🛒 Купить / продлить", "buy")],
              [("👤 Моя подписка", "my"), ("💰 Баланс", "balance")],
              [("🎁 Промокод", "promo"), ("👥 Пригласить друга", "ref")],
              [("📖 Как подключить", "help"), ("💬 Поддержка", f"url:https://t.me/{config.SUPPORT.lstrip('@')}")])


def _price(p: Plan, user) -> tuple[int, int]:
    pct = 0 if p.once else user["discount"]
    return discounted(p.rub, pct), discounted(p.stars, pct)


def plans_text(plans: list[Plan], user) -> str:
    lines = ["<b>Тарифы</b> (все на 30 дней)\n"]
    if user["discount"]:
        lines.append(f"🎁 Твоя скидка <b>{user['discount']}%</b> уже учтена (кроме пробного)\n")
    for p in plans:
        traffic = "безлимит" if not p.traffic_gb else f"{p.traffic_gb} ГБ"
        extra = " · только один раз" if p.once else ""
        rub, stars = _price(p, user)
        price = f"<s>{p.rub}₽</s> {rub}₽" if rub < p.rub else f"{rub}₽"
        lines.append(f"• <b>{p.title}</b> — {price} / {stars}⭐  <i>({traffic}{extra})</i>")
    lines.append(f"\n💰 На балансе: {user['balance']}₽")
    lines.append(f"\nНа одну подписку до {config.DEVICE_LIMIT} устройств." if config.DEVICE_LIMIT else "")
    return "\n".join(lines)


def plans_kb(plans: list[Plan], user) -> InlineKeyboardMarkup:
    return kb(*[[(f"{p.title} — {_price(p, user)[0]}₽", f"plan:{p.key}")] for p in plans], [("◀️ Назад", "menu")])


def balance_text(user) -> str:
    t = f"💰 <b>Баланс: {user['balance']}₽</b>\n\nС баланса можно оплатить любой тариф в один клик.\nНа сколько пополняем?"
    if user["discount"]:
        t += f"\n\n🎁 Активна скидка {user['discount']}% на следующую покупку"
    return t


def ref_text(bot_username: str, uid: int, n: int, earned: int) -> str:
    return (f"👥 <b>Приглашай друзей</b>\n\n"
            f"С каждой оплаты друга тебе <b>{config.REF_PERCENT}%</b> на баланс, навсегда.\n\n"
            f"Твоя ссылка (нажми, чтобы скопировать):\n"
            f"<code>https://t.me/{bot_username}?start=ref_{uid}</code>\n\n"
            f"Приглашено: {n}\nЗаработано: {earned}₽")


HELP_TEXT = (
    "<b>Как подключиться</b>\n\n"
    "1. Поставь приложение:\n"
    "   📱 Android — <b>Happ</b> или <b>v2RayTun</b> (Google Play)\n"
    "   🍏 iPhone — <b>Happ</b> или <b>v2RayTun</b> (App Store)\n"
    "   💻 Windows — <b>Happ</b>, <b>Hiddify</b> или <b>v2rayN</b>\n"
    "2. Открой «👤 Моя подписка» и скопируй ссылку (нажми на неё).\n"
    "3. В приложении нажми «+» → «Импорт из буфера обмена».\n"
    "4. Жми подключиться — готово 🚀\n\n"
    f"Что-то не работает? Пиши {config.SUPPORT}"
)


async def sub_view(db: DB, xuis, uid: int) -> tuple[str, InlineKeyboardMarkup]:
    sub = await db.get_sub(uid)
    if not sub:
        return ("У тебя пока нет подписки.\nВыбери тариф, чтобы начать 👇",
                kb([("🛒 Купить", "buy")], [("◀️ Меню", "menu")]))

    plan = PLANS.get(sub.plan_key)
    title = plan.title if plan else sub.plan_key
    status = "✅ активна" if sub.active else "⛔ истекла"
    up, down, total = await sub_traffic(xuis, sub)
    traffic = f"{fmt_gb(up + down)} из {fmt_gb(total)}" if total else f"{fmt_gb(up + down)} · безлимит"
    locs = ", ".join(loc_label(k) for k in sub.locations)

    text = (f"<b>Подписка:</b> {title}\n"
            f"<b>Статус:</b> {status}\n"
            f"<b>До:</b> {fmt_date(sub.expires_at)}\n"
            f"<b>Трафик:</b> {traffic}\n"
            f"<b>Локации:</b> {locs}\n\n")

    if config.SUB_PUBLIC_URL:
        user = await db.get_user(uid)
        text += ("🔗 <b>Ссылка-подписка</b> (нажми, чтобы скопировать):\n"
                 f"<code>{config.SUB_PUBLIC_URL}/sub/{user['sub_token']}</code>")
    elif any(LOC_BY_KEY[k].sub_url for k in sub.locations if k in LOC_BY_KEY):
        user = await db.get_user(uid)
        parts = []
        for k in sub.locations:
            loc = LOC_BY_KEY.get(k)
            if loc and loc.sub_url:
                url = f"{loc.sub_url}/{sub_id(user['sub_token'], k)}"
                prefix = f"{loc.label}:\n" if len(sub.locations) > 1 else ""
                parts.append(f"{prefix}<code>{url}</code>")
        text += "🔗 <b>Ссылка-подписка</b> (нажми, чтобы скопировать):\n" + "\n\n".join(parts)
    else:
        links = await sub_links(xuis, sub)
        if links:
            text += "🔑 <b>Ключи</b> (нажми, чтобы скопировать):\n" + "\n\n".join(f"<code>{l}</code>" for l in links)
        else:
            text += "⚠️ Не удалось получить ключи, сервер не отвечает. Попробуй через минуту."

    rows = []
    if plan and not plan.once:
        loc = "all" if plan.all_locations else sub.locations[0]
        rows.append([("🔄 Продлить", f"pay:{plan.key}:{loc}")])
    else:
        rows.append([("🛒 Купить тариф", "buy")])
    rows.append([("📖 Как подключить", "help"), ("◀️ Меню", "menu")])
    return text, kb(*rows)
