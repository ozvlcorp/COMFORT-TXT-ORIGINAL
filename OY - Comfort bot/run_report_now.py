"""Сформировать дневной отчёт прямо сейчас — проверка, не дожидаясь 20:00.

Запуск на сервере (где есть .env с токенами):

    python run_report_now.py          # печатает текст отчёта в консоль
    python run_report_now.py --send   # ещё и отправляет его в Telegram всем ADMIN_IDS

Период — сегодняшний день (Asia/Tashkent), как у штатного отчёта в 20:00.
Строки с «н/д» = у токена нет доступа к разделу (см. check_report_access.py).
"""
import argparse
import asyncio

import daily_report as dr
import moysklad_api as ms


async def main(send: bool) -> None:
    try:
        text = await dr.build_report_text("ru")
        print(text)

        if send:
            from aiogram import Bot
            from aiogram.client.default import DefaultBotProperties
            from aiogram.enums import ParseMode
            from config import ADMIN_IDS, BOT_TOKEN

            if not ADMIN_IDS:
                print("\n[--send пропущен: ADMIN_IDS пуст в .env]")
                return
            bot = Bot(
                token=BOT_TOKEN,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            try:
                await dr.run_for_today(bot)
                print(f"\n[отправлено в Telegram админам: {ADMIN_IDS}]")
            finally:
                await bot.session.close()
    finally:
        await ms.close_moysklad_http()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Сформировать дневной отчёт сейчас")
    p.add_argument("--send", action="store_true", help="отправить отчёт админам в Telegram")
    args = p.parse_args()
    asyncio.run(main(args.send))
