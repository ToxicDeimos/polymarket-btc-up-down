"""
spread_mm.py — backtest del MARKET MAKING REAL (captura de spread), no direccional. La red + nuestros datos
coinciden: el edge es hacer mercado, no predecir. Modela: postear bid B y ask A en el token del favorito,
COMPRAR en B cuando el precio baja a B, VENDER en A cuando sube a A → capturas el spread (A−B) sin riesgo
direccional. Si compras pero no vendes antes del cierre → inventario, aguantas a resolución (won−B) = el
riesgo real. Fills por el PRECIO DE OPERACIONES real (columna `last` del libro), no por toque de ask/bid.
Incluye rebate maker crypto (20% del fee = 0.20·0.07·p·(1−p) por fill).

    cd ~/polymarket-btc-up-down/research && python3 spread_mm.py
Mide: spread medio, % round-trip vs inventario atascado, PnL neto (con rebate), train/test.
"""
import csv, os, sys, glob

DIR = os.path.join(os.path.dirname(__file__), "lab")
LO, HI = 0.62, 0.82; POST = 60; WLEN = 300
REBATE = 0.20 * 0.07     # 20% del fee taker crypto


def load_reso():
    d = {}
    for fn in ("clob_reso_mw.csv", "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: d[r["cid"]] = r["winner"]
    return d


def reb(p): return REBATE * p * (1 - p)


def main():
    reso = load_reso()
    res = []          # (kind, pnl, ws)   kind: 'rt' round-trip / 'stuck' / 'notrade'
    spreads = []
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        W = {}
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 17 or not row[1].startswith("btc-updown-5m-"): continue
                try:
                    ts = int(row[0]); ws = int(row[1].split("-")[-1])
                    b = float(row[4]) if row[4] else None; a = float(row[10]) if row[10] else None
                    last = float(row[16]) if row[16] else None
                except Exception: continue
                if b is None or a is None: continue
                W.setdefault(row[1], {"cid": row[2], "ws": ws, "Up": [], "Down": []})[row[3]].append((ts, b, a, last))
        for slug, w in W.items():
            win = reso.get(w["cid"])
            if win not in ("Up", "Down") or not w["Up"] or not w["Down"]: continue
            ws = w["ws"]
            # favorito por ask a 240s
            def nearest(series, t):
                best = None
                for row in series:
                    g = abs(row[0] - t)
                    if g <= 90 and (best is None or g < best[0]): best = (g,) + row[1:]
                return best
            n240U = nearest(w["Up"], ws + 240); n240D = nearest(w["Down"], ws + 240)
            if not n240U or not n240D: continue
            fav = "Up" if n240U[2] > n240D[2] else "Down"
            fav_ask = n240U[2] if fav == "Up" else n240D[2]
            if not (LO <= fav_ask <= HI): continue
            wonf = 1 if fav == win else 0
            series = sorted(w[fav])                    # (ts,bid,ask,last) del token favorito
            # cotización a POST s: B=bid, A=ask
            q = nearest(series, ws + POST)
            if not q: continue
            B, A = q[1], q[2]
            if A - B < 0.01: continue                  # sin spread que capturar
            spreads.append(A - B)
            # simular con el precio de operaciones (last)
            bought = sold = False
            for (ts, bid, ask, last) in series:
                if ts <= ws + POST or last is None: continue
                if not bought and last <= B: bought = True
                elif bought and not sold and last >= A: sold = True; break
            if not bought:
                res.append(("notrade", 0.0, ws))
            elif sold:
                res.append(("rt", (A - B) + reb(B) + reb(A), ws))       # capturó spread + 2 rebates
            else:
                res.append(("stuck", (wonf - B) + reb(B), ws))          # inventario a resolución + 1 rebate

    traded = [r for r in res if r[0] != "notrade"]
    rt = [r for r in traded if r[0] == "rt"]; stuck = [r for r in traded if r[0] == "stuck"]
    n = len(traded)
    if not n: print("sin trades"); return
    allws = sorted(r[2] for r in traded); mid = allws[len(allws) // 2]
    def avg(rs, i=1): return sum(r[i] for r in rs) / len(rs) * 100 if rs else float("nan")
    print(f"ventanas favorito 62-82¢ con cotización: {len(res)}  ·  con trade: {n}")
    print(f"spread medio cotizado: {sum(spreads)/len(spreads)*100:.1f}¢")
    print(f"  round-trip (capturó spread): {len(rt)} ({len(rt)/n*100:.0f}%)  ·  atascado (inventario): {len(stuck)} ({len(stuck)/n*100:.0f}%)")
    print(f"  PnL round-trips: {avg(rt):+.2f}pp/trade  ·  PnL atascados: {avg(stuck):+.2f}pp/trade")
    tr = [r for r in traded if r[2] < mid]; te = [r for r in traded if r[2] >= mid]
    print(f"\n  NETO MM (por trade, con rebate): {avg(traded):+.2f}pp  (train {avg(tr):+.1f}/test {avg(te):+.1f})")
    print("\n→ si NETO>0 y el % round-trip es alto → capturamos más spread del que perdemos en inventario = MM viable.")
    print("  si los 'atascados' hunden el neto → nos corren el inventario (selección adversa) = necesitamos más velocidad.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
