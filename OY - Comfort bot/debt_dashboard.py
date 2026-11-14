"""Веб-дашборд дебиторки (P&L под продажи в долг).

Отдаёт снимок дебиторки как самодостаточную HTML-страницу (без внешних
CDN/скриптов) — KPI, диаграмма зон старения, пончик «собрано vs долг» и
полная таблица открытых документов. Данные берутся из того же сборщика,
что и текстовый отчёт (debt_report.collect_data → aggregate_receivables).

Подключается к aiohttp-серверу бота маршрутом GET /debt-report и защищён
токеном DEBT_DASHBOARD_TOKEN (см. webhook_server.py).
"""
from __future__ import annotations

from datetime import date
from html import escape

import debt_report
from config import DEBT_REPORT_LOOKBACK_DAYS
from moysklad_api import AGING_BUCKET_KEYS

# Зона старения → (подпись, короткая подпись, цвет)
BUCKET_META: dict[str, tuple[str, str, str]] = {
    "current":  ("Текущая",         "В сроке",   "#16a34a"),
    "d1_7":     ("1–7 дней",        "1–7 дн.",   "#eab308"),
    "d8_30":    ("8–30 дней",       "8–30 дн.",  "#f97316"),
    "d31_90":   ("31–90 дней",      "31–90 дн.", "#dc2626"),
    "d90_plus": ("90+ дней (риск)", "90+ дн.",   "#7f1d1d"),
}


def _money(v: float) -> str:
    return f"{float(v):,.2f}"


def _fmt_date(iso: str) -> str:
    try:
        y, m, d = iso.split("-")
        return f"{d}.{m}.{y}"
    except (ValueError, AttributeError):
        return iso or "—"


def _status_badge(paid: float, total: float) -> tuple[str, str, str]:
    if paid <= 0:
        return ("Не оплачено", "#dc2626", "#fef2f2")
    if paid < total:
        return ("Частично", "#f97316", "#fff7ed")
    return ("Оплачено", "#16a34a", "#f0fdf4")


def _zone_of(overdue: int) -> str:
    if overdue <= 0:
        return "current"
    if overdue <= 7:
        return "d1_7"
    if overdue <= 30:
        return "d8_30"
    if overdue <= 90:
        return "d31_90"
    return "d90_plus"


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         background:#f1f5f9; color:#0f172a; padding:24px; }}
  .wrap {{ max-width:1040px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 2px; }}
  .sub {{ color:#64748b; font-size:13px; margin-bottom:20px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:18px; }}
  .card {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:16px 18px;
          box-shadow:0 1px 2px rgba(0,0,0,.04); }}
  .card .lbl {{ font-size:12px; color:#64748b; text-transform:uppercase; letter-spacing:.03em; }}
  .card .val {{ font-size:26px; font-weight:700; margin-top:6px; }}
  .accr .val {{ color:#0f172a; }} .coll .val {{ color:#16a34a; }} .recv .val {{ color:#dc2626; }}
  .card .hint {{ font-size:12px; color:#94a3b8; margin-top:4px; }}
  .panel {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:18px 20px; margin-bottom:18px; }}
  .panel h2 {{ font-size:15px; margin:0 0 14px; }}
  .split {{ display:grid; grid-template-columns:1.6fr 1fr; gap:20px; align-items:center; }}
  @media (max-width:720px) {{ .kpis {{ grid-template-columns:1fr; }} .split {{ grid-template-columns:1fr; }} }}
  .bar-row {{ display:grid; grid-template-columns:120px 1fr 120px; align-items:center; gap:10px; margin:9px 0; }}
  .bar-label {{ font-size:13px; font-weight:600; display:flex; flex-direction:column; }}
  .bar-label .cnt {{ font-size:11px; color:#94a3b8; font-weight:400; }}
  .bar-track {{ background:#f1f5f9; border-radius:6px; height:22px; overflow:hidden; }}
  .bar-fill {{ height:100%; border-radius:6px; min-width:2px; }}
  .bar-val {{ font-size:13px; font-weight:600; text-align:right; }}
  .bar-val .pct {{ display:block; font-size:11px; color:#94a3b8; font-weight:400; }}
  .donut-wrap {{ text-align:center; }}
  .donut-center {{ font-size:20px; font-weight:700; }}
  .donut-cap {{ font-size:12px; color:#64748b; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ padding:9px 10px; text-align:left; border-bottom:1px solid #eef2f6; }}
  th {{ font-size:11px; text-transform:uppercase; letter-spacing:.03em; color:#64748b; background:#f8fafc;
       position:sticky; top:0; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.cl {{ font-weight:600; }}
  td.rem {{ color:#dc2626; font-weight:700; }}
  td.paid {{ color:#16a34a; }}
  .od.ok {{ color:#16a34a; }}
  .badge {{ font-size:11px; font-weight:600; padding:3px 8px; border-radius:20px; white-space:nowrap; }}
  .foot {{ font-size:11px; color:#94a3b8; margin-top:10px; }}
  .empty {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:40px; text-align:center;
           font-size:16px; color:#16a34a; }}
</style></head><body><div class="wrap">{body}</div></body></html>"""


def render_html(data: dict, *, today: date, lookback: int) -> str:
    """Отрисовать снимок дебиторки (data из aggregate_receivables) в HTML."""
    date_label = today.strftime("%d.%m.%Y")
    header = (
        f'<h1>📉 Дебиторка — P&amp;L под продажи в долг</h1>'
        f'<div class="sub">Снимок на {date_label} · окно сканирования {lookback} дн. · '
        f'валюта — по умолчанию (USD)</div>'
    )

    if data.get("doc_count", 0) == 0:
        body = header + '<div class="empty">✅ Открытой дебиторки нет — все отгрузки оплачены.</div>'
        return _page("Дебиторка", body)

    accrued = data["accrued"]
    collected = data["collected"]
    receivable = data["receivable"]
    coll_pct = (collected / accrued * 100) if accrued else 0.0

    kpis = f"""
    <div class="kpis">
      <div class="card accr"><div class="lbl">Начислено (Σ отгрузок)</div>
        <div class="val">${_money(accrued)}</div><div class="hint">все продажи по документам</div></div>
      <div class="card coll"><div class="lbl">Собрано (Σ оплат)</div>
        <div class="val">${_money(collected)}</div><div class="hint">{coll_pct:.0f}% от начисленного</div></div>
      <div class="card recv"><div class="lbl">Дебиторка (остаток)</div>
        <div class="val">${_money(receivable)}</div>
        <div class="hint">{data['doc_count']} док. · {data['debtor_count']} должников</div></div>
    </div>"""

    max_total = max((data["buckets"][k]["total"] for k in AGING_BUCKET_KEYS), default=0) or 1
    bars = ""
    for k in AGING_BUCKET_KEYS:
        name, _, color = BUCKET_META[k]
        b = data["buckets"][k]
        w = b["total"] / max_total * 100
        pct = (b["total"] / receivable * 100) if receivable else 0
        bars += (
            f'<div class="bar-row">'
            f'<div class="bar-label">{name}<span class="cnt">{b["count"]} док.</span></div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{w:.1f}%;background:{color}"></div></div>'
            f'<div class="bar-val">${_money(b["total"])}<span class="pct">{pct:.0f}%</span></div>'
            f'</div>'
        )

    circ = 2 * 3.14159 * 52
    coll_len = circ * (collected / accrued if accrued else 0)
    aging = f"""
    <div class="panel"><h2>Старение долга по срокам просрочки</h2>
      <div class="split">
        <div class="bars">{bars}</div>
        <div class="donut-wrap">
          <svg width="150" height="150" viewBox="0 0 120 120">
            <circle cx="60" cy="60" r="52" fill="none" stroke="#fee2e2" stroke-width="14"/>
            <circle cx="60" cy="60" r="52" fill="none" stroke="#16a34a" stroke-width="14"
                    stroke-dasharray="{coll_len:.1f} {circ:.1f}" stroke-linecap="round"
                    transform="rotate(-90 60 60)"/>
            <text x="60" y="58" text-anchor="middle" class="donut-center">{coll_pct:.0f}%</text>
            <text x="60" y="74" text-anchor="middle" class="donut-cap">собрано</text>
          </svg>
          <div class="donut-cap" style="margin-top:6px">🟢 ${_money(collected)} собрано &nbsp; 🔴 ${_money(receivable)} долг</div>
        </div>
      </div>
    </div>"""

    trs = ""
    for r in data["rows"]:
        st_label, st_col, st_bg = _status_badge(r["paid"], r["sum"])
        _, _, zcol = BUCKET_META[_zone_of(r["overdue"])]
        od = r["overdue"]
        od_html = (f'<span class="od" style="color:{zcol}">{od} дн.</span>'
                   if od > 0 else '<span class="od ok">—</span>')
        trs += (
            f'<tr><td class="cl">{escape(str(r["client"]))}</td>'
            f'<td>№{escape(str(r["doc"]))}</td>'
            f'<td>{_fmt_date(r["date"])}</td>'
            f'<td class="num">${_money(r["sum"])}</td>'
            f'<td class="num paid">${_money(r["paid"])}</td>'
            f'<td class="num rem">${_money(r["remainder"])}</td>'
            f'<td class="num">{r["term"]} дн.</td>'
            f'<td class="num">{od_html}</td>'
            f'<td><span class="badge" style="color:{st_col};background:{st_bg}">{st_label}</span></td></tr>'
        )

    table = f"""
    <div class="panel"><h2>Открытые документы (по убыванию просрочки)</h2>
      <table><thead><tr>
        <th>Клиент</th><th>Документ</th><th>Дата</th><th>Сумма</th><th>Оплачено</th>
        <th>Остаток</th><th>Срок</th><th>Просрочка</th><th>Статус</th>
      </tr></thead><tbody>{trs}</tbody></table>
      <div class="foot">Срок оплаты берётся из доп. поля «Срок оплаты» на отгрузке, иначе дефолт.
        Полностью оплаченные документы в дебиторку не попадают. 90+ дней — зона высокого риска.</div>
    </div>"""

    return _page("Дебиторка — P&L под продажи в долг", header + kpis + aging + table)


async def build_html() -> str:
    """Собрать данные из MoySklad и вернуть готовую HTML-страницу дашборда."""
    data, today = await debt_report.collect_data()
    return render_html(data, today=today, lookback=DEBT_REPORT_LOOKBACK_DAYS)
