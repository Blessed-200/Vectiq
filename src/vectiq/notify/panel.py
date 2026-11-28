"""Owner control panel for the channel: one pinned message with buttons; every button edits it in place.

The channel no longer carries bet alerts (those go to each person's private chat with HIS stake). It carries the view of
the system: today / last 7 days / by strategy / latest bets / health / users, a daily summary at 6 AM Cuba and a chart.
All numbers come from the three alert ledgers with the same filters as the web page (ST3 price >= 2.10; ST5 EV in
(0.15, 0.30] and <= 2 sets; ST0 all), so channel, page and bot always agree. Money = a $100 bank sized exactly like the bot
(0.7 % ST3, 1 % ST5, 0.65 % ST0, base recalculated hourly or after +-5 %)."""
from __future__ import annotations

import datetime as dt
import html
import sqlite3
import time
from typing import Callable

LEGS = ("ST3", "ST5", "ST0")
PCTS = (0.007, 0.01, 0.0065)
SPARK = "▁▂▃▄▅▆▇█"
HOME_BUTTONS = [[("📅 Hoy", "d:today"), ("📆 7 días", "d:week"), ("🎯 Estrategias", "d:strat")],
                [("🕒 Últimas", "d:last"), ("🩺 Salud", "d:health"), ("👥 Usuarios", "d:users")],
                [("📈 Gráfico", "d:chart"), ("🔄 Actualizar", "d:home")]]
BACK = [[("⬅ Menú", "d:home"), ("🔄", "d:refresh")]]
CUBA_OFFSET_S = 10 * 3600            # a "Cuba day" runs 6:00 AM -> 6:00 AM, i.e. 10:00 UTC -> 10:00 UTC


def day_of(ts_s: float) -> int:
    return int((ts_s - CUBA_OFFSET_S) // 86400)


def day_label(d: int) -> str:
    x = dt.datetime.fromtimestamp(d * 86400 + CUBA_OFFSET_S, dt.timezone.utc) - dt.timedelta(hours=4)
    return f"{x.day:02d}/{x.month:02d}"


def money(v: float) -> str:
    return f"${v:,.2f}"


def spark(vals: list[float]) -> str:
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return SPARK[3] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in vals)


def bar(v: float, scale: float, width: int = 10) -> str:
    n = min(width, int(round(abs(v) / scale * width))) if scale > 0 else 0
    return ("█" * n) if v >= 0 else ("▒" * n)


def load(dbs: dict[str, sqlite3.Connection | None]) -> list[dict]:
    """Every alert-eligible bet: ts (s), leg 0/1/2, u (unit result, None while open), price, line, players."""
    out: list[dict] = []
    for leg, key in enumerate(("st3", "st5", "st0")):
        db = dbs.get(key)
        if db is None:
            continue
        try:
            rows = db.execute("SELECT ts_ms, players, line, price, ev, sets, stake, pnl, result FROM bets").fetchall()
        except sqlite3.Error:
            continue
        for ts, pl, line, price, ev, sets, stake, pnl, res in rows:
            k = (sets or "").count("-")
            if leg == 0 and price < 2.10 - 1e-9:
                continue
            if leg == 1 and not (0.15 < (ev or 0) <= 0.30 + 1e-9 and k <= 2):
                continue
            u = pnl / stake if res in ("W", "L") and stake else None
            out.append({"t": ts / 1000.0, "leg": leg, "u": u, "price": price, "line": line, "pl": pl or "", "res": res})
    out.sort(key=lambda r: r["t"])
    return out


def simulate(bets: list[dict], b0: float = 100.0) -> tuple[float, float, float, list[float]]:
    """(final bank, peak, max drawdown, bank after each settlement) with the bot's stake rule."""
    ev: list[tuple[float, int, int]] = []
    s = [b for b in bets if b["u"] is not None]
    for i, b in enumerate(s):
        ev += [(b["t"], 0, i), (b["t"] + 600, 1, i)]
    ev.sort()
    bank = base = b0
    last, pk, mdd, stk, path = -1e18, b0, 0.0, {}, []
    for t, ty, i in ev:
        if ty == 0:
            if t - last >= 3600 or abs(bank / base - 1) >= 0.05:
                base, last = bank, t
            stk[i] = min(3000.0, max(1.0, PCTS[s[i]["leg"]] * base))
        else:
            bank = max(0.0, bank + stk[i] * s[i]["u"])
            pk = max(pk, bank)
            mdd = max(mdd, 1 - bank / pk)
            path.append(bank)
    return bank, pk, mdd, path


def stats(bets: list[dict]) -> dict:
    s = [b for b in bets if b["u"] is not None]
    w = sum(1 for b in s if b["u"] > 0)
    u = sum(b["u"] for b in s)
    return {"n": len(s), "open": len(bets) - len(s), "w": w, "l": len(s) - w, "u": u, "roi": u / len(s) if s else 0.0}


def _line(name: str, st: dict, w: int = 5) -> str:
    if not st["n"]:
        return f"{name:<{w}} sin liquidar" + (f" ({st['open']} abiertas)" if st["open"] else "")
    return f"{name:<{w}} {st['w']:>3}G {st['l']:>3}P {st['u']:+7.1f}u {st['roi'] * 100:+5.1f}%"


def _hdr(title: str, now_s: float) -> str:
    from vectiq.notify.telegram import cuba_clock
    return f"<b>VECTIQ</b> · {title}\n<i>actualizado {cuba_clock(now_s)} Cuba</i>\n"


def view_today(bets: list[dict], now_s: float) -> str:
    d = day_of(now_s)
    day = [b for b in bets if day_of(b["t"]) == d]
    tot = stats(day)
    bank, pk, mdd, path = simulate(day)
    rows = [_line(n, stats([b for b in day if b["leg"] == i])) for i, n in enumerate(LEGS)] + ["─" * 33, _line("TOTAL", tot)]
    big = "🟢" if bank >= 100 else "🔴"
    return (_hdr(f"HOY {day_label(d)} (desde las 6 AM)", now_s) + f"<pre>{html.escape(chr(10).join(rows))}</pre>"
            f"{big} Con $100: <b>{money(bank)}</b>  ({bank - 100:+.2f})\n"
            f"Pico {money(pk)} · ahora {((1 - bank / pk) * 100 if pk else 0):.1f} % bajo el pico · caída máx {mdd * 100:.1f} %\n"
            f"<code>{spark(path[-40:])}</code>\n"
            f"Abiertas ahora: {tot['open']}")


def view_week(bets: list[dict], now_s: float) -> str:
    d0 = day_of(now_s)
    lines, tot_u = [], 0.0
    ds = list(range(d0 - 6, d0 + 1))
    us = []
    for d in ds:
        s = stats([b for b in bets if day_of(b["t"]) == d])
        us.append(s["u"])
    sc = max([abs(x) for x in us] + [1.0])
    for d, u in zip(ds, us):
        s = stats([b for b in bets if day_of(b["t"]) == d])
        tot_u += u
        mark = " ← hoy" if d == d0 else ""
        lines.append(f"{day_label(d)} {s['n']:>3} {u:+6.1f}u {bar(u, sc)}{mark}")
    week = [b for b in bets if d0 - 6 <= day_of(b["t"]) <= d0]
    bank, pk, mdd, _ = simulate(week)
    st = stats(week)
    return (_hdr("ÚLTIMOS 7 DÍAS", now_s) + f"<pre>{html.escape(chr(10).join(lines))}</pre>"
            f"Total {st['n']} apuestas · <b>{tot_u:+.1f} u</b> · ROI {st['roi'] * 100:+.1f} %\n"
            f"Con $100 hace 7 días: <b>{money(bank)}</b> · caída máx {mdd * 100:.1f} %\n<i>(teórico: supone que ejecutaste todas)</i>")


def view_strat(bets: list[dict], now_s: float) -> str:
    d0 = day_of(now_s)
    rows = []
    for i, n in enumerate(LEGS):
        for lab, lo in (("14d", d0 - 13), ("hoy", d0)):
            rows.append(_line(f"{n} {lab}", stats([b for b in bets if b["leg"] == i and day_of(b["t"]) >= lo]), 8))
    return (_hdr("POR ESTRATEGIA", now_s) + f"<pre>{html.escape(chr(10).join(rows))}</pre>"
            "ST3 = Under tras set 1 · ST5 = modelo Markov · ST0 = prepartido.\n"
            "<i>u = unidades (1 u = lo apostado). Una racha mala de unos días es normal.</i>")


def view_last(bets: list[dict], now_s: float, n: int = 8) -> str:
    from vectiq.notify.telegram import cuba_clock
    done = [b for b in bets if b["u"] is not None][-n:][::-1]
    rows = [f"{LEGS[b['leg']]} {cuba_clock(b['t'])} {'✅' if b['u'] > 0 else '❌'} U{b['line']:g}@{b['price']:.2f} {b['u']:+.2f}u" for b in done]
    return _hdr("ÚLTIMAS LIQUIDADAS", now_s) + f"<pre>{html.escape(chr(10).join(rows) or 'sin datos')}</pre>"


def view_health(bets: list[dict], now_s: float, extra: dict | None = None) -> str:
    extra = extra or {}
    lines = []
    for i, n in enumerate(LEGS):
        mine = [b for b in bets if b["leg"] == i]
        if mine:
            age = (now_s - mine[-1]["t"]) / 60
            lines.append(f"{n}: última alerta hace {age:.0f} min" if age < 180 else f"{n}: ⚠ última hace {age / 60:.1f} h")
        else:
            lines.append(f"{n}: sin alertas")
    ex = [f"{k}: {v}" for k, v in extra.items()]
    return _hdr("SALUD", now_s) + "\n".join(lines) + ("\n" + "\n".join(ex) if ex else "")


def home_text(bets: list[dict], now_s: float) -> str:
    d = day_of(now_s)
    day = [b for b in bets if day_of(b["t"]) == d]
    bank, pk, mdd, path = simulate(day)
    st = stats(day)
    return (_hdr("PANEL", now_s) + f"Hoy: <b>{st['u']:+.1f} u</b> · {st['n']} liquidadas · con $100 → <b>{money(bank)}</b>\n"
            f"<code>{spark(path[-30:])}</code>\nToca un botón para ver más.")


def day_summary(bets: list[dict], d: int, now_s: float) -> str:
    day = [b for b in bets if day_of(b["t"]) == d]
    st = stats(day)
    bank, pk, mdd, path = simulate(day)
    rows = [_line(n, stats([b for b in day if b["leg"] == i])) for i, n in enumerate(LEGS)]
    emoji = "🟢" if st["u"] >= 0 else "🔴"
    return (_hdr(f"RESUMEN DEL DÍA {day_label(d)}", now_s) + f"{emoji} <b>{st['u']:+.1f} u</b> · ROI {st['roi'] * 100:+.1f} % · {st['n']} apuestas\n"
            f"<pre>{html.escape(chr(10).join(rows))}</pre>Con $100: <b>{money(bank)}</b> · caída máx {mdd * 100:.1f} %\n"
            f"<code>{spark(path[-40:])}</code>")


def view_users(users, banks: Callable[[dict], float]) -> str:
    rows = []
    for k, u in users.d["users"].items():
        st = "✅" if u.get("ok") else "⏳"
        b = banks(u) if u.get("bank") else None
        rows.append(f"{st} {html.escape(u['name'])} · " + (money(b) if b is not None else "sin banca") + (" · pausa" if u.get("paused") else "") + f"\n   id {k}")
    return "<b>VECTIQ</b> · USUARIOS\n" + ("\n".join(rows) if rows else "Nadie todavía.") + "\n<i>/aprobar ID · /quitar ID</i>"


def chart_png(bets: list[dict], now_s: float, path: str) -> bool:
    """Two panels: today's $100 path and the last 7 days' cumulative units. False if matplotlib is missing."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001
        return False
    d0 = day_of(now_s)
    day = [b for b in bets if day_of(b["t"]) == d0 and b["u"] is not None]
    bank, pk, mdd, path_ = simulate(day)
    fig, ax = plt.subplots(2, 1, figsize=(7.2, 6.4), dpi=150, facecolor="#0e1116")
    for a in ax:
        a.set_facecolor("#0e1116")
        a.tick_params(colors="#9aa4b2", labelsize=8)
        for sp in a.spines.values():
            sp.set_color("#2a313c")
        a.grid(color="#1c222b", lw=0.6)
    ax[0].plot([100] + path_, color="#34d399" if bank >= 100 else "#f87171", lw=2)
    ax[0].axhline(100, color="#475569", lw=0.8, ls="--")
    ax[0].set_title(f"Hoy {day_label(d0)} · \\$100 → \\{money(bank)}", color="#e6edf3", fontsize=11, loc="left")
    wk = [b for b in bets if d0 - 6 <= day_of(b["t"]) <= d0 and b["u"] is not None]
    cum, c = [], 0.0
    for b in wk:
        c += b["u"]
        cum.append(c)
    ax[1].plot(cum, color="#60a5fa", lw=1.8)
    ax[1].fill_between(range(len(cum)), cum, color="#60a5fa", alpha=0.12)
    ax[1].set_title(f"7 días · unidades acumuladas ({c:+.1f} u)", color="#e6edf3", fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return True


VIEWS = {"today": view_today, "week": view_week, "strat": view_strat, "last": view_last}


def render(view: str, bets: list[dict], now_s: float, extra: dict | None = None) -> tuple[str, list]:
    if view in VIEWS:
        return VIEWS[view](bets, now_s), BACK
    if view == "health":
        return view_health(bets, now_s, extra), BACK
    return home_text(bets, now_s), HOME_BUTTONS
