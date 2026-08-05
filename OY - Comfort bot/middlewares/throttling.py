"""Per-user anti-flood middleware.

Каждое нажатие кнопки может стоить очень дорого в запросах к МойСкладу:
💰 Баланс — до 8 запросов, а 📊 Отчёт «за всё время» запускает бэкфилл,
который постранично сканирует до 8000 документов с enrich'ем строк.
Без ограничения несколько пользователей, спамящих кнопками, выбирают
лимиты аккаунта МойСклад за секунды — и аккаунт получает ограничение
API_REMAP_12 (403 на все запросы).

Мидлварь ставит два барьера на каждого пользователя:

  1. Кулдаун — минимальный интервал между двумя принятыми действиями.
  2. Busy-guard — пока предыдущее действие пользователя ещё выполняется,
     новые его нажатия отбрасываются (иначе 10 нажатий = 10 параллельных
     обработчиков, каждый со своими запросами в МойСклад).

Отброшенные апдейты не обрабатываются вообще: обработчик не вызывается,
значит ни одного запроса в МойСклад по ним не уходит. Пользователю не
чаще раза в THROTTLE_WARN_COOLDOWN_SEC отправляется короткое
предупреждение, чтобы бот сам не спамил в ответ на спам.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from config import (
    THROTTLE_INTERVAL_SEC,
    THROTTLE_MAX_TRACKED_USERS,
    THROTTLE_WARN_COOLDOWN_SEC,
)
from locales import t

logger = logging.getLogger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    """Ограничивает частоту действий одного пользователя.

    Регистрируется как outer-middleware на Dispatcher.update, поэтому
    отсечка происходит до фильтров и до любого обработчика.
    """

    def __init__(
        self,
        interval_sec: float = THROTTLE_INTERVAL_SEC,
        warn_cooldown_sec: float = THROTTLE_WARN_COOLDOWN_SEC,
        max_tracked_users: int = THROTTLE_MAX_TRACKED_USERS,
    ) -> None:
        self._interval = interval_sec
        self._warn_cooldown = warn_cooldown_sec
        self._max_tracked = max_tracked_users
        # user_id -> monotonic timestamp последнего принятого действия.
        # OrderedDict, чтобы вытеснять самых старых и не течь по памяти.
        self._last_action: OrderedDict[int, float] = OrderedDict()
        # user_id -> monotonic timestamp последнего отправленного предупреждения
        self._last_warn: OrderedDict[int, float] = OrderedDict()
        # Пользователи, чей предыдущий апдейт ещё выполняется
        self._busy: set[int] = set()

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract(event: TelegramObject) -> tuple[int | None, Message | CallbackQuery | None]:
        """Достаёт user_id и объект, через который можно ответить."""
        inner: Any = event
        if isinstance(event, Update):
            inner = event.message or event.callback_query
        if isinstance(inner, (Message, CallbackQuery)) and inner.from_user:
            return inner.from_user.id, inner
        return None, None

    def _touch(self, store: OrderedDict[int, float], user_id: int, now: float) -> None:
        store[user_id] = now
        store.move_to_end(user_id)
        while len(store) > self._max_tracked:
            store.popitem(last=False)

    async def _warn_once(self, carrier: Message | CallbackQuery | None, user_id: int, now: float) -> None:
        """Предупреждает не чаще одного раза в _warn_cooldown секунд."""
        last_warn = self._last_warn.get(user_id)
        if last_warn is not None and (now - last_warn) < self._warn_cooldown:
            return
        self._touch(self._last_warn, user_id, now)

        if carrier is None:
            return
        # Язык берём из Telegram-профиля — бесплатно, без обращения к БД.
        # t() сам падает обратно на 'uz' для незнакомых значений.
        lang = "uz"
        from_user = getattr(carrier, "from_user", None)
        code = (getattr(from_user, "language_code", "") or "").lower()
        if code.startswith("ru"):
            lang = "ru"

        try:
            if isinstance(carrier, CallbackQuery):
                # Всплывающее уведомление — не засоряет чат
                await carrier.answer(t("too_fast", lang), show_alert=False)
            elif hasattr(carrier, "answer"):
                await carrier.answer(t("too_fast", lang))
        except Exception as exc:  # noqa: BLE001 — предупреждение не критично
            logger.debug("throttling: warn failed for user %s: %s", user_id, exc)

    # ── middleware entry point ───────────────────────────────────────────

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id, carrier = self._extract(event)
        if user_id is None:
            return await handler(event, data)

        now = time.monotonic()

        # Барьер 1: предыдущее действие пользователя ещё выполняется
        if user_id in self._busy:
            logger.info("throttling: dropped update from user %s (busy)", user_id)
            await self._warn_once(carrier, user_id, now)
            return None

        # Барьер 2: кулдаун между действиями
        last = self._last_action.get(user_id)
        if last is not None and (now - last) < self._interval:
            logger.info(
                "throttling: dropped update from user %s (%.2fs < %.2fs)",
                user_id, now - last, self._interval,
            )
            await self._warn_once(carrier, user_id, now)
            return None

        self._touch(self._last_action, user_id, now)
        self._busy.add(user_id)
        try:
            return await handler(event, data)
        finally:
            self._busy.discard(user_id)
            # Отсчёт кулдауна — от МОМЕНТА ЗАВЕРШЕНИЯ обработки. Иначе долгий
            # отчёт (десятки секунд) успевал бы «отстоять» кулдаун ещё во время
            # выполнения, и сразу после него проходило бы новое тяжёлое действие.
            self._touch(self._last_action, user_id, time.monotonic())
