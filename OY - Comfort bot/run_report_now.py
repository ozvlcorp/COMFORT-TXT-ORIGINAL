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
                # Шлём уже собранный text напрямую (без повторной сборки) и
                # честно считаем доставку — run_for_today глушит ошибки внутри,
                # из-за чего раньше печаталось «отправлено» даже при сбое.
                ok = 0
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, text)
                        ok += 1
                    except Exception as e:  # noqa: BLE001
                        print(f"\n[НЕ доставлено {admin_id}: {repr(e)[:80]}]")
                print(f"\n[Telegram: доставлено {ok} из {len(ADMIN_IDS)} админам]")
            finally:
                await bot.session.close()
    finally:
        await ms.close_moysklad_http()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Сформировать дневной отчёт сейчас")
    p.add_argument("--send", action="store_true", help="отправить отчёт админам в Telegram")
    args = p.parse_args()
    asyncio.run(main(args.send))
