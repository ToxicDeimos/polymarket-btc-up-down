"""
dump_quoter.py — DETECTOR de dump grande por WSS + QUOTER selectivo en PAPER. Es la vía del edge grande: los
ganadores fadean los dumps agresores como MAKERS (postean bid, se llenan baratos en el pánico, montan la
reversión → +1,5-2,6pp en backtest). Aquí, en TIEMPO REAL: por cada ventana 5m escucho sus dos tokens; cuando
entra un SELL agresor GRANDE (size ≥ SIZE_THR = p99 del backtest) en un token, simulo POSTEAR un bid al
best_bid y me lleno ahí (cota superior: asumo ganar la cola), y aguanto a resolución. NO opera dinero — mide
fills y PnL simulados. La cuota de cola REAL solo la darán órdenes límite de $1 de verdad (las pones tú).

Modos:
  (sin args)   live: escucha WSS y loguea fills a dump_quoter_log.csv (won vacío)
  --resolve    rellena 'won' y 'pnl_pp' de las filas pendientes vía CLOB
  --analyze    resumen (fills, precio medio, win%, PnL medio, train/test)

    cd ~/polymarket-btc-up-down/research && python3 dump_quoter.py            # 24/7 (systemd)
    python3 dump_quoter.py --resolve && python3 dump_quoter.py --analyze
"""
import websocket, json, time, threading, csv, os, sys, urllib.request

DIR = os.path.dirname(__file__)
LOG = os.path.join(DIR, "dump_quoter_log.csv")
SIZE_THR = 390               # tamaño de SELL agresor para considerarlo dump (p99 5m del backtest)
DROP_THR = 0.04              # fallback: caída de best_bid (¢) que también cuenta como dump si no hay size
MAXINV = 2                   # fills máx por token y ventana
REBATE = 0.20 * 0.07         # rebate maker crypto
WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEADER = ["ws", "cid", "outcome", "ts_fill", "dump_size", "entry_bid", "won", "pnl_pp"]


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dq/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.3)


def now(): return int(time.time())
def reb(p): return REBATE * p * (1 - p)


def discover(ws):
    d = get(f"https://gamma-api.polymarket.com/markets?slug=btc-updown-5m-{ws}")
    if not (isinstance(d, list) and d): return None
    m = d[0]
    try: return {"cid": m.get("conditionId"), "toks": dict(zip(json.loads(m["outcomes"]), json.loads(m["clobTokenIds"])))}
    except Exception: return None


def winner(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if not isinstance(d, dict): return None
    for t in d.get("tokens", []):
        if t.get("winner") is True: return t.get("outcome")
    return None


def logrows(rows):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(HEADER)
        for r in rows: w.writerow(r)


def run_window(ws, mk):
    toks = mk["toks"]
    if "Up" not in toks or "Down" not in toks: return
    id2out = {toks["Up"]: "Up", toks["Down"]: "Down"}
    st = {"Up": {"bb": None, "ba": None, "hi": None}, "Down": {"bb": None, "ba": None, "hi": None}}
    inv = {"Up": 0, "Down": 0}; fills = []; seen = set()
    print(f"── DQ {ws}  escuchando Up+Down")

    def set_book(out, bb, ba):
        s = st[out]
        if bb is not None:
            s["bb"] = bb; s["hi"] = bb if s["hi"] is None else max(s["hi"], bb)
        if ba is not None: s["ba"] = ba

    def on_dump(out, size):
        s = st[out]
        if s["bb"] is None or inv[out] >= MAXINV: return
        entry = s["bb"]
        inv[out] += 1; s["hi"] = entry            # reset high tras el dump
        fills.append([ws, mk["cid"], out, now(), round(size, 1), round(entry, 3), "", ""])
        print(f"   DUMP {out} size~{size:.0f} → bid paper @ {entry:.3f} (inv {inv[out]})")

    def on_trade(out, price, side, size):
        if side != "SELL": return
        s = st[out]
        drop = (s["hi"] is not None and s["bb"] is not None and s["hi"] - s["bb"] >= DROP_THR)
        if size >= SIZE_THR or drop: on_dump(out, size)

    def on_open(w): w.send(json.dumps({"type": "market", "assets_ids": [toks["Up"], toks["Down"]]}))

    def on_message(w, msg):
        try: data = json.loads(msg)
        except Exception: return
        for d in (data if isinstance(data, list) else [data]):
            et = d.get("event_type"); aid = d.get("asset_id")
            if et == "book" and aid in id2out:
                bids = [float(x["price"]) for x in d.get("bids", [])]
                asks = [float(x["price"]) for x in d.get("asks", [])]
                set_book(id2out[aid], max(bids) if bids else None, min(asks) if asks else None)
            elif et == "price_change":
                for ch in d.get("price_changes", []):
                    a = ch.get("asset_id")
                    if a in id2out:
                        try: set_book(id2out[a], float(ch["best_bid"]), float(ch["best_ask"]))
                        except Exception: pass
            elif et == "last_trade_price" and aid in id2out:
                tx = d.get("transaction_hash", "")
                if tx and tx in seen: continue
                if tx: seen.add(tx)
                try: on_trade(id2out[aid], float(d["price"]), d.get("side"), float(d.get("size", 0) or 0))
                except Exception: pass

    wsapp = websocket.WebSocketApp(WSS, on_open=on_open, on_message=on_message, on_error=lambda w, e: None)
    threading.Thread(target=lambda: (time.sleep(max(0, ws + 300 - now())), wsapp.close()), daemon=True).start()
    wsapp.run_forever(ping_interval=20, ping_timeout=10)
    if fills:
        logrows(fills)
        print(f"   fin {ws}: {len(fills)} fills logueados (won se rellena con --resolve)")


def live():
    print("=" * 60 + "\n  DUMP QUOTER PAPER — WSS · fadear dumps grandes como maker\n" + "=" * 60)
    seen = set()
    while True:
        try:
            t = now(); ws = t - t % 300
            if ws not in seen and t < ws + 120:
                mk = discover(ws)
                if mk and "Up" in mk["toks"]:
                    seen.add(ws); run_window(ws, mk)
                    if len(seen) > 300: seen = set(list(seen)[-60:])
            time.sleep(3)
        except KeyboardInterrupt: print("\nparado."); break
        except Exception as ex: print("  err:", ex); time.sleep(10)


def resolve_mode():
    if not os.path.exists(LOG): print("sin log"); return
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    cache = {}
    done = 0
    for r in rows:
        if r["won"] in ("0", "1"): continue
        cid = r["cid"]
        if cid not in cache: cache[cid] = winner(cid); time.sleep(0.1)
        w = cache[cid]
        if w in ("Up", "Down"):
            won = 1 if w == r["outcome"] else 0; e = float(r["entry_bid"])
            r["won"] = str(won); r["pnl_pp"] = f"{(won - e + reb(e)) * 100:.2f}"; done += 1
    with open(LOG, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=HEADER); wr.writeheader(); wr.writerows(rows)
    print(f"resueltas {done} · total {len(rows)}")


def analyze():
    if not os.path.exists(LOG): print("sin log"); return
    R = [r for r in csv.DictReader(open(LOG, encoding="utf-8")) if r["won"] in ("0", "1")]
    n = len(R)
    if not n: print("sin fills resueltos aún — corre --resolve"); return
    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    pnl = [float(r["pnl_pp"]) for r in R]; ent = [float(r["entry_bid"]) for r in R]
    wr = 100 * mean([int(r["won"]) for r in R])
    ws = sorted(int(r["ws"]) for r in R); mid = ws[n // 2]
    tr = [float(r["pnl_pp"]) for r in R if int(r["ws"]) < mid]; te = [float(r["pnl_pp"]) for r in R if int(r["ws"]) >= mid]
    nw = len(set(r["ws"] for r in R))
    print(f"fills resueltos: {n} en {nw} ventanas ({n/nw:.1f}/vent) · entrada media {mean(ent):.3f} · gana {wr:.0f}%")
    print(f"PnL medio: {mean(pnl):+.2f}pp  (train {mean(tr):+.2f} / test {mean(te):+.2f})  · con rebate maker")
    print("⚠ COTA SUPERIOR: asume ganar la cola en cada fill. La cifra real la dan órdenes de $1 de verdad.")


def main():
    if "--resolve" in sys.argv: resolve_mode()
    elif "--analyze" in sys.argv: analyze()
    else: live()


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
