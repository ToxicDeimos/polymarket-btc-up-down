"""
mm_paper.py — MARKET-MAKER en PAPER (no opera con dinero). Cotiza bid+ask en el token del favorito de la
ventana 5m activa, actualiza la cotización cada UPDATE s (latencia realista de la Pi), y SIMULA los fills
con la cinta de operaciones REAL (data-api /trades?market=cid): si una venta-taker pasa por mi bid → compro;
si una compra-taker pasa por mi ask → vendo. Gestiona inventario [0..MAX] (sin shorts). Al cerrar la ventana,
liquida el inventario por CLOB. Mide round-trips (spread capturado) vs inventario atascado (selección adversa
por quote rancio) — LO que el backtest idealizado no captura. Incluye rebate maker crypto.

CAVEAT: sobreestima el VOLUMEN de fills (asume que gano la cola); pero el % atascado / adverso con quote de
3s SÍ es realista y es la pregunta clave. Déjalo correr ~1-2h y analiza mm_paper_log.csv.

    cd ~/polymarket-btc-up-down/research && python3 mm_paper.py          # 24/7 (systemd) o a mano
Autónomo (stdlib). Log gitignored.
"""
import urllib.request, json, time, csv, os, sys

DIR = os.path.dirname(__file__)
LOG = os.path.join(DIR, "mm_paper_log.csv")
LO, HI = 0.62, 0.82
UPDATE = 3.0            # s entre actualizaciones de cotización (latencia realista Pi)
POLL = 1.0
MAXINV = 3
ENTRY_FROM = 60        # empezar a cotizar a los 60s de la ventana
REBATE = 0.20 * 0.07
HEADER = ["ws", "fav", "fav_ask", "buys", "sells", "roundtrips", "end_inv", "won", "pnl_pp", "spread_c"]


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mmpaper/1.0"})
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
    try:
        outs = json.loads(m.get("outcomes") or "[]"); tids = json.loads(m.get("clobTokenIds") or "[]")
    except Exception: return None
    if len(outs) != 2 or len(tids) != 2: return None
    return {"cid": m.get("conditionId"), "toks": dict(zip(outs, tids))}


def best(tok):
    b = get(f"https://clob.polymarket.com/book?token_id={tok}")
    if not isinstance(b, dict): return None, None
    bids = [float(x["price"]) for x in b.get("bids", [])]
    asks = [float(x["price"]) for x in b.get("asks", [])]
    return (max(bids) if bids else None), (min(asks) if asks else None)


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


def run_window(ws, mk):
    cid = mk["cid"]
    # esperar a 120s para fijar el favorito (mayor ask), luego cotizar el resto de la ventana (120-300s)
    while now() < ws + 120: time.sleep(2)
    bU, aU = best(mk["toks"]["Up"]); bD, aD = best(mk["toks"]["Down"])
    if None in (aU, aD): return
    fav = "Up" if aU > aD else "Down"; fav_ask = aU if fav == "Up" else aD
    if not (LO <= fav_ask <= HI):
        print(f"   {ws} favorito {fav}@{fav_ask:.2f} fuera de zona — no cotizo"); return
    tok = mk["toks"][fav]
    print(f"── MM ventana {ws}  favorito {fav} @ {fav_ask:.2f}")
    inv = 0; cash = 0.0; buys = sells = rts = 0; spreads = []
    my_bid = my_ask = None; last_q = 0; seen = set()
    while now() < ws + 300:
        t = now()
        if t - last_q >= UPDATE:                       # re-cotiza (join top of book)
            b, a = best(tok)
            if b is not None and a is not None and a - b >= 0.01:
                my_bid, my_ask = b, a; spreads.append(a - b)
            last_q = t
        # cinta real de la ventana → simular fills contra mi quote rancio
        d = get(f"https://data-api.polymarket.com/trades?market={cid}&limit=50")
        for x in (d or []):
            if x.get("outcome") != fav: continue
            h = x.get("transactionHash", "")
            if h in seen: continue
            seen.add(h)
            try: p = float(x["price"]); side = x.get("side")
            except Exception: continue
            if my_bid is None: continue
            if side == "SELL" and p <= my_bid and inv < MAXINV:      # venta-taker pica mi bid → compro
                inv += 1; cash -= my_bid; buys += 1
            elif side == "BUY" and p >= my_ask and inv > 0:          # compra-taker pica mi ask → vendo
                inv -= 1; cash += my_ask; sells += 1; rts += 1
        time.sleep(POLL)
    # liquidar inventario a resolución
    w = None; t0 = now()
    while now() < t0 + 300 and w is None:
        w = winner(cid)
        if w is None: time.sleep(15)
    won = 1 if w == fav else 0
    settle = cash + inv * won + buys * reb(my_bid or fav_ask) + sells * reb(my_ask or fav_ask)
    pnl_pp = settle * 100
    sp = sum(spreads) / len(spreads) * 100 if spreads else 0
    log([ws, fav, round(fav_ask, 3), buys, sells, rts, inv, "" if w is None else won,
         round(pnl_pp, 2), round(sp, 1)])
    print(f"   fin: buys {buys} sells {sells} round-trips {rts} inv {inv} won {won} → PnL {pnl_pp:+.2f}pp (spread {sp:.1f}¢)")


def main():
    print("=" * 60 + "\n  MM PAPER (DRY) — cotiza favorito 62-82¢, mide fills/atascado\n" + "=" * 60)
    seen = set()
    while True:
        try:
            t = now(); ws = t - t % 300
            if ws not in seen and t < ws + 120 - 5:
                mk = discover(ws)
                if mk:
                    seen.add(ws); run_window(ws, mk)
                    if len(seen) > 300: seen = set(list(seen)[-60:])
            time.sleep(5)
        except KeyboardInterrupt: print("\nparado."); break
        except Exception as ex: print("  err:", ex); time.sleep(10)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
