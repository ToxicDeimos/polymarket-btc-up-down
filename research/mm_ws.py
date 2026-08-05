"""
mm_ws.py — MARKET-MAKER sofisticado en PAPER, sobre WebSocket (tiempo real, ms) + SKEW de inventario.
Mejora al maker ingenuo (que se quedaba atascado largo y perdía −20pp): cotiza a dos caras y, según el
inventario, DESPLAZA las cotizaciones para deshacerlo (Avellaneda-style skew) → evita selección adversa.

Por ventana 5m: a 120s fija el favorito 62-82¢, se suscribe al WSS de ese token, y hace mercado hasta el
cierre. Fills SIMULADOS con las operaciones REALES del feed (last_trade_price): venta-taker ≤ mi bid → compro;
compra-taker ≥ mi ask → vendo. Skew: my_bid=best_bid−k·inv, my_ask=best_ask−k·inv (si largo, ambos bajan →
vendo más, compro menos). Liquida inventario a resolución (CLOB). Rebate maker crypto incluido. NO opera dinero.

    cd ~/polymarket-btc-up-down/research && python3 mm_ws.py            # 24/7 (systemd)
"""
import websocket, json, time, threading, csv, os, sys, urllib.request

DIR = os.path.dirname(__file__)
LOG = os.path.join(DIR, "mm_ws_log.csv")
LO, HI = 0.62, 0.82
MAXINV = 3
SKEW = 0.01                  # desplazamiento por unidad de inventario (1¢)
REBATE = 0.20 * 0.07
WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEADER = ["ws", "fav", "fav_ask", "buys", "sells", "roundtrips", "end_inv", "won", "pnl_pp", "maxinv_seen"]


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mmws/1.0"})
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


def best(tok):
    b = get(f"https://clob.polymarket.com/book?token_id={tok}")
    if not isinstance(b, dict): return None
    a = [float(x["price"]) for x in b.get("asks", [])]
    return min(a) if a else None


def winner(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if not isinstance(d, dict): return None
    for t in d.get("tokens", []):
        if t.get("winner") is True: return t.get("outcome")
    return None


def log(row):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(HEADER)
        w.writerow(row)


class MM:
    def __init__(self):
        self.bid = self.ask = None
        self.inv = 0; self.cash = 0.0; self.buys = self.sells = self.rt = 0; self.maxinv = 0
        self.seen = set()

    def requote(self):
        if self.bid is None or self.ask is None: return
        self.my_bid = round(self.bid - SKEW * self.inv, 2)
        self.my_ask = round(self.ask - SKEW * self.inv, 2)

    def on_book(self, d):
        bids = [float(x["price"]) for x in d.get("bids", [])]
        asks = [float(x["price"]) for x in d.get("asks", [])]
        if bids: self.bid = max(bids)
        if asks: self.ask = min(asks)
        self.requote()

    def on_change(self, ch):
        try:
            self.bid = float(ch["best_bid"]); self.ask = float(ch["best_ask"]); self.requote()
        except Exception: pass

    def on_trade(self, d):
        tx = d.get("transaction_hash", "")
        if tx in self.seen: return
        self.seen.add(tx)
        try: p = float(d["price"]); side = d.get("side")
        except Exception: return
        if getattr(self, "my_bid", None) is None: return
        if side == "SELL" and p <= self.my_bid and self.inv < MAXINV:
            self.inv += 1; self.cash -= self.my_bid; self.buys += 1; self.maxinv = max(self.maxinv, self.inv)
            self.requote()
        elif side == "BUY" and p >= self.my_ask and self.inv > 0:
            self.inv -= 1; self.cash += self.my_ask; self.sells += 1; self.rt += 1
            self.requote()


def run_window(ws, mk):
    while now() < ws + 120: time.sleep(1)
    aU, aD = best(mk["toks"]["Up"]), best(mk["toks"]["Down"])
    if None in (aU, aD): return
    fav = "Up" if aU > aD else "Down"; fav_ask = max(aU, aD)
    if not (LO <= fav_ask <= HI):
        print(f"   {ws} favorito @ {fav_ask:.2f} fuera de zona"); return
    tok = mk["toks"][fav]; print(f"── MM-WS {ws}  favorito {fav} @ {fav_ask:.2f}")
    mm = MM()
    def on_open(w): w.send(json.dumps({"type": "market", "assets_ids": [tok]}))
    def on_message(w, msg):
        try: data = json.loads(msg)
        except Exception: return
        for d in (data if isinstance(data, list) else [data]):
            et = d.get("event_type")
            if et == "book": mm.on_book(d)
            elif et == "price_change":
                for ch in d.get("price_changes", []):
                    if ch.get("asset_id") == tok: mm.on_change(ch)
            elif et == "last_trade_price" and d.get("asset_id") == tok: mm.on_trade(d)
    wsapp = websocket.WebSocketApp(WSS, on_open=on_open, on_message=on_message, on_error=lambda w, e: None)
    threading.Thread(target=lambda: (time.sleep(max(0, ws + 300 - now())), wsapp.close()), daemon=True).start()
    wsapp.run_forever(ping_interval=20, ping_timeout=10)
    # liquidar inventario a resolución
    w = None; t0 = now()
    while now() < t0 + 300 and w is None:
        w = winner(mk["cid"])
        if w is None: time.sleep(15)
    won = 1 if w == fav else 0
    pnl = (mm.cash + mm.inv * won + mm.buys * reb(fav_ask) + mm.sells * reb(fav_ask)) * 100
    log([ws, fav, round(fav_ask, 3), mm.buys, mm.sells, mm.rt, mm.inv, "" if w is None else won,
         round(pnl, 2), mm.maxinv])
    print(f"   fin: buys {mm.buys} sells {mm.sells} RT {mm.rt} inv-fin {mm.inv} (máx {mm.maxinv}) won {won} → PnL {pnl:+.2f}pp")


def main():
    print("=" * 60 + "\n  MM-WS PAPER (DRY) — WebSocket + skew de inventario\n" + "=" * 60)
    seen = set()
    while True:
        try:
            t = now(); ws = t - t % 300
            if ws not in seen and t < ws + 120 - 5:
                mk = discover(ws)
                if mk and "Up" in mk["toks"]:
                    seen.add(ws); run_window(ws, mk)
                    if len(seen) > 300: seen = set(list(seen)[-60:])
            time.sleep(3)
        except KeyboardInterrupt: print("\nparado."); break
        except Exception as ex: print("  err:", ex); time.sleep(10)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
