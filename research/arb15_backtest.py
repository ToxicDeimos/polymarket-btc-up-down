"""
arb15_backtest.py — backtest de la estrategia del artículo HTX (BTC 15m up/down): NO predice, ARBITRA.
Fase 1: en los primeros 2 min, si un lado se desploma ≥CRASH% → compra ese lado hundido.
Fase 2: cuando Up_ask + Down_ask < HEDGE → compra el OTRO lado (cobertura) → tienes las dos caras por <$1,
        una paga $1 → beneficio bloqueado. Si nunca cubre, aguanta a resolución (riesgo direccional).
Sobre los libros REALES de 15m del lab (books_*.csv, filas btc-updown-15m-*), resuelto por CLOB, neto de fee
taker (0.07·p·(1−p) al comprar en el ask; el ask ya incluye el spread). Sin creerse el 86% del artículo.

    cd ~/polymarket-btc-up-down/research && python3 arb15_backtest.py
"""
import csv, os, sys, glob, json, time, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
WLEN = 900
ENTRY_WIN = 120          # primeros 2 min
CRASH = 0.15             # desplome mínimo para entrar
HEDGES = (0.99, 0.97, 0.95, 0.93)   # umbrales de suma a barrer


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "arb15/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.3)


def fee(p): return 0.07 * p * (1 - p)


def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if isinstance(d, dict):
        for t in d.get("tokens", []):
            if t.get("winner") is True: return t.get("outcome")
    return None


def main():
    HEDGE = float(sys.argv[1]) if len(sys.argv) > 1 else None    # opcional: umbral único
    hedge_list = [HEDGE] if HEDGE else HEDGES

    # cargar series 15m: por (slug) -> lista (ts, up_ask, down_ask) emparejadas por ts
    windows = {}   # slug -> {"cid","ws","rows":{ts:{"Up":ask,"Down":ask}}}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-15m-"): continue
                try:
                    ts = int(row[0]); ws = int(row[1].split("-")[-1]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                w = windows.setdefault(row[1], {"cid": row[2], "ws": ws, "rows": {}})
                w["rows"].setdefault(ts, {})[row[3]] = ask
    print(f"ventanas 15m en el libro: {len(windows)}")

    # cache de resolución
    reso = {}
    for fn in ("clob_reso_mmlogs.csv", "clob_reso_mw.csv", "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: reso[r["cid"]] = r["winner"]

    def resolve(cid):
        if cid in reso: return reso[cid]
        w = winner_clob(cid); reso[cid] = w; time.sleep(0.1); return w

    for HEDGE in hedge_list:
        trades = []; hedged = 0; stuck = 0
        for slug, w in windows.items():
            ws = w["ws"]
            series = sorted((ts, d) for ts, d in w["rows"].items() if "Up" in d and "Down" in d)
            if len(series) < 5: continue
            # FASE 1: desplome ≥CRASH en los primeros ENTRY_WIN s
            entry = None; prev = {}
            for ts, d in series:
                if ts - ws > ENTRY_WIN: break
                for side in ("Up", "Down"):
                    a = d[side]
                    if side in prev and prev[side] > 0.02 and a <= prev[side] * (1 - CRASH):
                        entry = (side, a, ts); break
                    prev[side] = a
                if entry: break
            if not entry: continue
            eside, eprice, ets = entry; oside = "Down" if eside == "Up" else "Up"
            # FASE 2: cubrir cuando suma < HEDGE, tras la entrada
            hedge = None
            for ts, d in series:
                if ts <= ets: continue
                if d["Up"] + d["Down"] < HEDGE:
                    hedge = (d[oside], ts); break
            win = resolve(w["cid"])
            if win not in ("Up", "Down"): continue
            if hedge:
                cost = eprice + hedge[0] + fee(eprice) + fee(hedge[0])
                pnl = 1 - cost; hedged += 1
            else:
                won = 1 if eside == win else 0
                pnl = won - eprice - fee(eprice); stuck += 1
            trades.append(pnl)
        n = len(trades)
        if not n:
            print(f"HEDGE {HEDGE}: 0 trades"); continue
        avg = sum(trades) / n * 100
        wins = sum(1 for p in trades if p > 0)
        print(f"HEDGE {HEDGE}: {n} trades · cubiertos {hedged} ({hedged/n*100:.0f}%) · atascados {stuck} · "
              f"PnL medio {avg:+.2f}pp/trade · ganadores {wins}/{n} ({wins/n*100:.0f}%)")
    print("\n→ si cubiertos% alto y PnL medio >0 → el arbitraje se cierra de verdad. Si atascados domina y")
    print("  el PnL es −, la cobertura no llega (mismo problema de liquidez/velocidad). CRASH=%.0f%%, entrada %ds." % (CRASH*100, ENTRY_WIN))


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
