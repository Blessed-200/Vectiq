import sqlite3
import time

from test_masters import NOW

from vectiq.notify import panel
from vectiq.signals.paper import PaperLedger


def _db(rows):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE bets(ts_ms INTEGER, players TEXT, line REAL, price REAL, ev REAL, sets TEXT, stake REAL, pnl REAL, result TEXT)")
    db.executemany("INSERT INTO bets VALUES(?,?,?,?,?,?,?,?,?)", rows)
    return db


def test_views_use_the_page_filters_and_render():
    now = 1_795_381_000.0
    t = int((now - 3600) * 1000)
    st3 = _db([(t, "a vs b", 100.5, 2.18, 0.1, "11-5", 1.0, 1.18, "W"), (t, "c vs d", 100.5, 2.05, 0.1, "11-5", 1.0, -1.0, "L")])
    st5 = _db([(t, "e vs f", 103.5, 1.8, 0.2, "11-5", 1.0, 0.8, "W"), (t, "g vs h", 103.5, 1.8, 0.4, "11-5", 1.0, -1.0, "L")])
    bets = panel.load({"st3": st3, "st5": st5, "st0": None})
    assert len(bets) == 2                       # ST3 price<2.10 and ST5 ev>0.30 are filtered out, like the web page
    for v in ("home", "today", "week", "strat", "last", "health"):
        text, buttons = panel.render(v, bets, now)
        assert "VECTIQ" in text and buttons
    assert "+1.2u" in panel.render("today", bets, now)[0] or "+2.0u" in panel.render("today", bets, now)[0]


def test_collector_channel_silent_but_private_gets_alert_and_panel_button(tmp_path):
    from test_masters import FakeClient
    from test_st0 import _pre

    from vectiq.collector import Collector
    from vectiq.notify.users import Users
    from vectiq.signals import masters
    from vectiq.store.tape import TapeWriter

    chan, priv, edits = [], [], []
    us = Users()
    us.register("9", "Ana")
    us.approve("9", True)
    us.get("9").update(bank=100.0, bank_ts=0.0)
    col = Collector(FakeClient(), TapeWriter(tmp_path / "t"), None, mledger=PaperLedger(tmp_path / "m.sqlite", 100.0),
                    book=masters.MastersBook.from_seed(), st3_ledger=PaperLedger(tmp_path / "s3.sqlite", 100.0),
                    st0_ledger=PaperLedger(tmp_path / "s0.sqlite", 100.0), alerts="st0",
                    notify=lambda m: True, notify_alert=lambda m, b=None, silent=False: chan.append(m) or 7,
                    users=us, send_to=lambda cid, t, b=None, silent=False: priv.append(t) or 1, alert_chat="1",
                    channel_alerts=False, edit_msg=lambda *a: edits.append(a) or True,
                    ack_callback=lambda *a: None)
    col._masters(_pre(), NOW)
    assert chan == [] and len(priv) == 1 and "PREPARTIDO" in priv[0]          # channel stays quiet, Ana is served
    cq = {"id": "c", "data": "d:week", "message": {"message_id": 5, "chat": {"id": -100, "type": "channel"}, "text": "x"}}
    col._panel_press(cq, "week", NOW)
    assert edits and "7 DÍAS" in edits[0][2] and edits[0][1] == 5
    assert time.time() > 0
