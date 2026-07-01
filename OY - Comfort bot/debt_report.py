"""Отчёт по дебиторке — «P&L под продажи в долг».

Готовый отчёт «Прибыли и убытки» в МойСкладе считает по начислению (все
продажи), а для узбекских продаж в долг нужно видеть *денежную* картину:
сколько отгружено (начислено), сколько реально собрано и какой остаток
завис по срокам. У каждой отгрузки (demand) есть поля `sum` и `payedSum`,
из которых и собирается дебиторка с разбивкой по зонам старения
(текущая / 1–7 / 8–30 / 31–90 / 90+ дней).

Отчёт — снимок на «сегодня»: сканируются отгрузки за окно
DEBT_REPORT_LOOKBACK_DAYS назад, берутся только неоплаченные остатки.
Отправляется админам вместе с дневным отчётом (см. scheduler.py) и по
запросу командой /debt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import moysklad_api as ms
from config import (
    ADMIN_IDS,
    DEBT_DEFAULT_TERM_DAYS,
    DEBT_REPORT_LOOKBACK_DAYS,
    DEBT_REPORT_TOP_N,
)
from formatting import fmt_date_dd_mm_yyyy, fmt_usd
from locales import t
from time_utils import LOCAL_TZ, local_today

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")


def _scan_bounds_msk() -> tuple[str, str]:
    """Границы сканирования [сегодня−lookback 00:00, сегодня 23:59] в МСК."""
    today = local_today()
    start_local = datetime.combine(
        today - timedelta(days=DEBT_REPORT_LOOKBACK_DAYS),
        datetime.min.time(),
        tzinfo=LOCAL_TZ,
    )
    end_local = datetime.combine(today, datetime.max.time().replace(microsecond=0), tzinfo=LOCAL_TZ)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start_local.astimezone(_MSK).strftime(fmt), end_local.astimezone(_MSK).strftime(fmt)


async def build_report_text(lang: str = "ru") -> str | None:
    """Собрать текст отчёта по дебиторке. None — если открытой дебиторки нет."""
    msk_from, msk_to = _scan_bounds_msk()
    today = local_today()

    data = await ms.aggregate_receivables(
        moment_from_msk=msk_from,
        moment_to_msk=msk_to,
        today_local=today,
        default_term_days=DEBT_DEFAULT_TERM_DAYS,
    )

    if data["doc_count"] == 0:
        return None

    buckets = data["buckets"]

    def _bcount(key: str) -> int:
        return buckets.get(key, {}).get("count", 0)

    def _btotal(key: str) -> str:
        return fmt_usd(buckets.get(key, {}).get("total", 0.0))

    text = t(
        "debt_report_header", lang,
        date=today.strftime("%d.%m.%Y"),
        lookback=DEBT_REPORT_LOOKBACK_DAYS,
        accrued=fmt_usd(data["accrued"]),
        collected=fmt_usd(data["collected"]),
        receivable=fmt_usd(data["receivable"]),
        doc_count=data["doc_count"],
        debtor_count=data["debtor_count"],
        b_current_count=_bcount("current"), b_current_total=_btotal("current"),
        b_d1_7_count=_bcount("d1_7"), b_d1_7_total=_btotal("d1_7"),
        b_d8_30_count=_bcount("d8_30"), b_d8_30_total=_btotal("d8_30"),
        b_d31_90_count=_bcount("d31_90"), b_d31_90_total=_btotal("d31_90"),
        b_d90_plus_count=_bcount("d90_plus"), b_d90_plus_total=_btotal("d90_plus"),
    )

    rows = data["rows"]
    if rows:
        parts = [t("debt_report_rows_header", lang)]
        for r in rows[:DEBT_REPORT_TOP_N]:
            parts.append(
                t(
                    "debt_report_row", lang,
                    client=_escape(r["client"]),
                    doc=_escape(str(r["doc"])),
                    date=fmt_date_dd_mm_yyyy(r["date"]),
                    remainder=fmt_usd(r["remainder"]),
                    sum=fmt_usd(r["sum"]),
                    paid=fmt_usd(r["paid"]),
                    term=r["term"],
                    overdue=r["overdue"],
                )
            )
        rest = len(rows) - DEBT_REPORT_TOP_N
        if rest > 0:
            parts.append(t("debt_report_more", lang, n=rest))
        text += "\n" + "\n".join(parts)

    return text


def _escape(s: str) -> str:
    from html import escape
    return escape(str(s or ""))


async def run_for_today(bot) -> None:
    """Сформировать снимок дебиторки и отправить всем ADMIN_IDS."""
    if not ADMIN_IDS:
        logger.warning("debt_report: ADMIN_IDS пуст, отчёт не отправлен")
        return
    try:
        text = await build_report_text("ru")
    except Exception as e:
        logger.exception("debt_report: build failed: %s", e)
        return
    if text is None:
        logger.info("debt_report: открытой дебиторки нет, отчёт не отправлен")
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:
            logger.error("debt_report: send to %s failed: %s", admin_id, e)
    logger.info("debt_report: sent to %d admin(s)", len(ADMIN_IDS))
