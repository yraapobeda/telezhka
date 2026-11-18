"""Обязательная подписка на канал (если REQUIRED_CHANNEL задан)."""
from __future__ import annotations

import logging
import time

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from . import config
from .views import kb

log = logging.getLogger(__name__)
OK_STATUSES = {"member", "administrator", "creator", "restricted"}


class ChannelGate(BaseMiddleware):
    def __init__(self, channel: str):
        self.channel = channel if channel.startswith("@") else "@" + channel
        self._ok: dict[int, float] = {}

    async def _is_member(self, bot, uid: int) -> bool:
        if time.time() - self._ok.get(uid, 0) < 300:
            return True
        try:
            m = await bot.get_chat_member(self.channel, uid)
        except Exception as e:  # бот не админ в канале и т.п. — продажи не блокируем
            log.warning("channel check failed: %s", e)
            return True
        ok = m.status in OK_STATUSES and getattr(m, "is_member", True) is not False
        if ok:
            self._ok[uid] = time.time()
        return ok

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.id in config.ADMIN_IDS:
            return await handler(event, data)
        if isinstance(event, Message) and event.successful_payment:
            return await handler(event, data)
        if await self._is_member(data["bot"], user.id):
            return await handler(event, data)

        text = f"Чтобы пользоваться ботом, подпишись на канал {self.channel} — там новости и статус серверов 👇"
        markup = kb([("📢 Подписаться", f"url:https://t.me/{self.channel[1:]}")], [("✅ Я подписался", "menu")])
        if isinstance(event, CallbackQuery):
            await event.answer("Сначала подпишись на канал", show_alert=True)
            await event.message.answer(text, reply_markup=markup)
        elif isinstance(event, Message):
            await event.answer(text, reply_markup=markup)
