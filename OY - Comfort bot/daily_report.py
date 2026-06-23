"""Дневной отчёт для админов: агрегирует МойСклад + локальную БД и шлёт в TG.

Запускается по расписанию (см. scheduler.py) каждый день в 20:00 Asia/Tashkent.
Период отчёта — 00:00:00–23:59:59 текущего дня в Asia/Tashkent (МойСклад
фильтр конвертируется в МСК — Europe/Moscow).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import database as db
import moysklad_api as ms
from config import ADMIN_IDS
from locales import t
from time_utils import LOCAL_TZ, local_today

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_UTC = ZoneInfo("UTC")


def _fmt_money_ru(v: float) -> str:
    """8639.78 → '8 639,78' (NBSP thousand sep, comma decimal)."""
    s = f"{float(v):,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", " ")


def _today_bounds() -> tuple[str, str, str, str, str]:
    """Returns (msk_from, msk_to, utc_from, utc_to, ddmmyyyy_local)."""
    today = local_today()
    start_local = datetime.combine(today, datetime.min.time(), tzinfo=LOCAL_TZ)
    end_local = start_local + timedelta(days=1) - timedelta(seconds=1)

    fmt = "%Y-%m-%d %H:%M:%S"
    msk_from = start_local.astimezone(_MSK).strftime(fmt)
    msk_to = end_local.astimezone(_MSK).strftime(fmt)
    utc_from = start_local.astimezone(_UTC).strftime(fmt)
    utc_to = end_local.astimezone(_UTC).strftime(fmt)
    return msk_from, msk_to, utc_from, utc_to, today.strftime("%d.%m.%Y")


async def build_report_text(lang: str = "ru") -> str:
    msk_from, msk_to, utc_from, utc_to, date_label = _today_bounds()

    entity_types = (
        "customerorder",
        "demand",
        "paymentin",
        "cashin",
        "paymentout",
        "cashout",
        "supply",
    )
    results = await asyncio.gather(
        *(
            ms.aggregate_documents(
                e, moment_from_msk=msk_from, moment_to_msk=msk_to
            )
            for e in entity_types
        ),
        ms.count_new_counterparties(
            created_from_msk=msk_from, created_to_msk=msk_to
        ),
        db.count_users_registered_between(utc_from, utc_to),
        return_exceptions=True,
    )

    # «н/д» = запрос к МойСклад по этому типу документа не прошёл (часто 403:
    # у API-токена нет прав на раздел). Показываем маркер вместо фейкового 0,
    # чтобы «приёмка была, а в отчёте 0» больше не вводило в заблуждение.
    NA = "н/д"

    def _disp(r) -> tuple[str, str]:
        if isinstance(r, Exception):
            logger.error("daily_report aggregate piece failed: %s", r)
            return NA, NA
        count, total = r
        return str(count), _fmt_money_ru(total)

    def _ok_int(r) -> int:
        if isinstance(r, Exception):
            logger.error("daily_report counter failed: %s", r)
            return 0
        return int(r)

    orders_count, orders_total = _disp(results[0])
    ship_count, ship_total = _disp(results[1])
    paymin_count, paymin_total = _disp(results[2])
    cashin_count, cashin_total = _disp(results[3])
    paymout_count, paymout_total = _disp(results[4])
    cashout_count, cashout_total = _disp(results[5])
    supply_count, supply_total = _disp(results[6])
    new_cp_ms = _ok_int(results[7])
    new_cp_bot = _ok_int(results[8])

    return t(
        "daily_admin_report", lang,
        date=date_label,
        orders_count=orders_count, orders_total=orders_total,
        ship_count=ship_count, ship_total=ship_total,
        paymentin_count=paymin_count, paymentin_total=paymin_total,
        cashin_count=cashin_count, cashin_total=cashin_total,
        paymentout_count=paymout_count, paymentout_total=paymout_total,
        cashout_count=cashout_count, cashout_total=cashout_total,
        supply_count=supply_count, supply_total=supply_total,
        new_cp_ms=new_cp_ms,
        new_cp_bot=new_cp_bot,
    )


async def run_for_today(bot) -> None:
    """Сформировать и отправить отчёт всем ADMIN_IDS."""
    if not ADMIN_IDS:
        logger.warning("daily_report: ADMIN_IDS пуст, отчёт не отправлен")
        return
    try:
        text = await build_report_text("ru")
    except Exception as e:
        logger.exception("daily_report: build failed: %s", e)
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:
            logger.error("daily_report: send to %s failed: %s", admin_id, e)
    logger.info("daily_report: sent to %d admin(s)", len(ADMIN_IDS))
