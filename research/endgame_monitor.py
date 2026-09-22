"""
endgame_monitor.py — Desempate EN VIVO del "ask barato del líder al cierre". tape_check mostró que los asks ~0,50
del líder en los últimos segundos (con BTC ganando $60+) casi nunca se negocian (0-1% de disparos con alguna
operación después), y que el +EV del sniper salía entero de ahí. Dos lecturas: (A) libro REST viejo/de reinicio o
mercado que deja de aceptar órdenes = artefacto; (B) órdenes reales olvidadas que nadie levanta = dinero real.
Solo se distingue en vivo. En cada ventana 5m, de T-40 a cierre+15s, cada ~2s registra:
  · best ask/bid de ambos tokens según el WSS (libro que EMPUJA el exchange, tiempo real)
  · best ask de ambos tokens según REST /book (lo que graba el lab)
  · estado del mercado en CLOB (accepting_orders / closed), cada ~6s
  · spot Binance y apertura (ventaja)
y todas las operaciones del WSS (last_trade_price). NO opera.

    cd ~/polymarket-btc-up-down/research && python3 endgame_monitor.py            # vivo (1-2 días)
    python3 endgame_monitor.py --analyze                                          # veredicto
"""
import websocket, json, time, threading, csv, os, sys, urllib.request

DIR = os.path.dirname(__file__)
LOG = os.path.join(DIR, "endgame_log.csv")
TLOG = os.path.join(DIR, "endgame_trades.csv")
WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
H = ["ts", "ws", "ttc", "spot", "open", "wss_ask_up", "wss_bid_up", "wss_ask_dn", "wss_bid_dn",
     "rest_ask_up", "rest_ask_dn", "accepting", "closed"]
TH = ["ts", "ws", "outcome", "side", "price", "size"]
LOCK = threading.Lock()


def get(url, tries=2, timeout=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "endgame/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.2)


def now(): return time.time()


def spot():
    d = get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=4)
    try: return float(d["price"])
    except Exception: return None


def discover(ws):
    d = get(f"https://gamma-api.polymarket.com/markets?slug=btc-updown-5m-{ws}")
    if not (isinstance(d, list) and d): return None
    m = d[0]
    try: return {"cid": m.get("conditionId"), "toks": dict(zip(json.loads(m["outcomes"]), json.loads(m["clobTokenIds"])))}
    except Exception: return None


def rest_ask(tok):
    b = get(f"https://clob.polymarket.com/book?token_id={tok}", timeout=4)
    if not isinstance(b, dict): return None
    a = [float(x["price"]) for x in b.get("asks", [])]
    return min(a) if a else None


def mkt_state(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}", timeout=4)
    if not isinstance(d, dict): return None, None
    return d.get("accepting_orders"), d.get("closed")


def append(path, header, row):
    with LOCK:
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new: w.writerow(header)
            w.writerow(row)


def run_window(ws, mk, open_px):
    toks = mk["toks"]; id2 = {toks["Up"]: "Up", toks["Down"]: "Down"}
    st = {"Up": {"a": None, "b": None}, "Down": {"a": None, "b": None}}

    def on_open(w): w.send(json.dumps({"type": "market", "assets_ids": [toks["Up"], toks["Down"]]}))

    def on_message(w, msg):
        try: data = json.loads(msg)
        except Exception: return
        for d in (data if isinstance(data, list) else [data]):
            et = d.get("event_type"); aid = d.get("asset_id")
            if et == "book" and aid in id2:
                bids = [float(x["price"]) for x in d.get("bids", [])]; asks = [float(x["price"]) for x in d.get("asks", [])]
                st[id2[aid]]["b"] = max(bids) if bids else None; st[id2[aid]]["a"] = min(asks) if asks else None
            elif et == "price_change":
                for ch in d.get("price_changes", []):
                    a = ch.get("asset_id")
                    if a in id2:
                        try:
                            st[id2[a]]["b"] = float(ch["best_bid"]) if ch.get("best_bid") not in (None, "") else None
                            st[id2[a]]["a"] = float(ch["best_ask"]) if ch.get("best_ask") not in (None, "") else None
                        except Exception: pass
            elif et == "last_trade_price" and aid in id2:
                try: append(TLOG, TH, [round(now(), 1), ws, id2[aid], d.get("side"), float(d["price"]), d.get("size")])
                except Exception: pass

    wsapp = websocket.WebSocketApp(WSS, on_open=on_open, on_message=on_message, on_error=lambda w, e: None)
    th = threading.Thread(target=lambda: wsapp.run_forever(ping_interval=20, ping_timeout=10), daemon=True)
    th.start()
    close = ws + 300; acc = cl = None; last_state = 0
    print(f"── {ws} monitorizando cierre (open {open_px})")
    while now() < close + 15:
        t = now()
        if t - last_state >= 6: acc, cl = mkt_state(mk["cid"]); last_state = t
        s = spot(); ru = rest_ask(toks["Up"]); rd = rest_ask(toks["Down"])
        append(LOG, H, [round(t, 1), ws, round(close - t, 1), s, open_px, st["Up"]["a"], st["Up"]["b"],
                        st["Down"]["a"], st["Down"]["b"], ru, rd, acc, cl])
        time.sleep(max(0.0, 2.0 - (now() - t)))
    wsapp.close()


def live():
    print("=" * 60 + "\n  ENDGAME MONITOR — WSS vs REST en los últimos 40s (no opera)\n" + "=" * 60)
    opens = {}; done = set()

    def recorder():
        while True:
            t = now(); ws = int(t - t % 300)
            if ws not in opens and t - ws < 5:
                s = spot()
                if s: opens[ws] = s
                if len(opens) > 400:
                    for k in sorted(opens)[:200]: opens.pop(k, None)
            time.sleep(0.5)
    threading.Thread(target=recorder, daemon=True).start()
    while True:
        try:
            t = now(); ws = int(t - t % 300)
            if ws in opens and ws not in done and t >= ws + 255:
                mk = discover(ws)
                done.add(ws)
                if mk and "Up" in mk["toks"]: run_window(ws, mk, opens[ws])
            time.sleep(1)
        except KeyboardInterrupt: print("\nparado."); break
        except Exception as ex: print("  err:", ex); time.sleep(5)


def fnum(x):
    try: return float(x)
    except Exception: return None


def analyze():
    if not os.path.exists(LOG): print("sin log"); return
    R = list(csv.DictReader(open(LOG, encoding="utf-8")))
    T = list(csv.DictReader(open(TLOG, encoding="utf-8"))) if os.path.exists(TLOG) else []
    nw = len(set(r["ws"] for r in R))
    print(f"filas: {len(R)} · ventanas: {nw} · trades WSS: {len(T)}")
    rows = []
    for r in R:
        s, o, ttc = fnum(r["spot"]), fnum(r["open"]), fnum(r["ttc"])
        if s is None or o is None or ttc is None: continue
        lead = s - o; X = "Up" if lead > 0 else "Down"; k = "up" if X == "Up" else "dn"
        rows.append({"ws": r["ws"], "ts": fnum(r["ts"]), "ttc": ttc, "lead": abs(lead), "X": X,
                     "wa": fnum(r[f"wss_ask_{k}"]), "ra": fnum(r[f"rest_ask_{k}"]),
                     "acc": r["accepting"], "cl": r["closed"]})

    def med(xs):
        xs = sorted(x for x in xs if x is not None); return xs[len(xs) // 2] if xs else float("nan")
    print("\n  LÍDER con |ventaja| ≥ $30 · por tramo hasta el cierre")
    print(f"  {'tramo':>12}{'n':>6}{'ask_REST':>9}{'ask_WSS':>9}{'REST≤0,70':>10}{'WSS≤0,70':>10}{'REST≤0,7 y WSS≥0,9':>20}{'accept=False':>13}")
    for lo, hi in ((30, 40), (20, 30), (10, 20), (0, 10), (-15, 0)):
        sub = [x for x in rows if lo < x["ttc"] <= hi and x["lead"] >= 30]
        if not sub: continue
        both = [x for x in sub if x["ra"] is not None and x["wa"] is not None]
        rc = [x for x in both if x["ra"] <= 0.70]
        stale = 100 * sum(1 for x in rc if x["wa"] >= 0.90) / len(rc) if rc else float("nan")
        lab = f"T-{hi}..T-{lo}" if lo >= 0 else "tras cierre"
        print(f"  {lab:>12}{len(sub):>6}{med([x['ra'] for x in sub]):>9.3f}{med([x['wa'] for x in sub]):>9.3f}"
              f"{100*sum(1 for x in both if x['ra']<=0.70)/max(1,len(both)):>9.0f}%"
              f"{100*sum(1 for x in both if x['wa']<=0.70)/max(1,len(both)):>9.0f}%{stale:>19.0f}%"
              f"{100*sum(1 for x in sub if x['acc']=='False')/len(sub):>12.0f}%")
    # ¿se puede ejecutar el ask barato REAL (visible en WSS)?
    cheap = [x for x in rows if 0 < x["ttc"] <= 30 and x["lead"] >= 30 and x["wa"] is not None and x["wa"] <= 0.70
             and x["acc"] != "False"]
    fills = 0
    for x in cheap:
        for t in T:
            if t["ws"] == x["ws"] and t["outcome"] == x["X"] and x["ts"] <= fnum(t["ts"]) <= x["ts"] + 10 \
                    and fnum(t["price"]) is not None and fnum(t["price"]) <= x["wa"] + 0.02:
                fills += 1; break
    print(f"\n  casos con ask del líder ≤0,70 VISIBLE EN WSS, mercado aceptando órdenes, ventaja ≥$30, ≤30s: {len(cheap)} "
          f"(ventanas {len(set(x['ws'] for x in cheap))}) · con operación a ese precio en ≤10s: {fills}")
    print("\nLECTURA: si REST≤0,70 pero WSS≥0,9 casi siempre → REST devolvía un libro viejo = ARTEFACTO. Si accept=False")
    print("sube al final → el mercado deja de aceptar órdenes = no ejecutable. Si hay casos con ask barato VISIBLE en WSS")
    print("y aceptando órdenes → orden REAL: probar un fill de $1 a mano en uno de esos momentos para confirmarlo.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    if "--analyze" in sys.argv: analyze()
    else: live()
