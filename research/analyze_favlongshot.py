"""
Analiza markets_large.csv: ¿hay un favorito-longshot REAL y operable?

  1) Calibración por estimador (mid/q1/twap): precio Yes vs frecuencia real.
  2) Backtest de la estrategia "comprar el FAVORITO" por banda de precio, CON costes
     (spread/fee) — un edge que no sobrevive al coste no es edge.
  3) Split OUT-OF-SAMPLE por fecha de resolución (mitad antigua vs reciente):
     si el edge solo está en una mitad → artefacto, como el fade.

Uso: python analyze_favlongshot.py [estimador]   (estimador: mid|q1|twap|first, def=mid)
"""
import csv, os, sys, statistics
from datetime import datetime, timezone
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

EST = sys.argv[1] if len(sys.argv) > 1 else "mid"
CSVP = os.path.join(os.path.dirname(__file__), "markets_large.csv")

rows = []
with open(CSVP, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        try:
            rows.append({
                "ts": int(r["resolution_ts"]), "yes_won": int(r["yes_won"]),
                "vol": float(r["volume"]), "p": float(r[EST]),
            })
        except (ValueError, KeyError):
            pass
rows = [r for r in rows if 0.02 < r["p"] < 0.98]
print(f"Mercados (estimador='{EST}', precio en (0.02,0.98)): {len(rows)}\n")


def calib(data, title):
    print("="*62 + f"\n  {title}  (n={len(data)})\n" + "="*62)
    print(f"  {'precio Yes':>11} | {'gana':>5} | {'n':>4} | {'EV bruto':>8}")
    for lo, hi in [(0,.1),(.1,.2),(.2,.3),(.3,.4),(.4,.5),(.5,.6),(.6,.7),(.7,.8),(.8,.9),(.9,1.01)]:
        sel = [d for d in data if lo <= d["p"] < hi]
        if len(sel) < 15: continue
        n = len(sel); f = sum(d["yes_won"] for d in sel)/n; mp = sum(d["p"] for d in sel)/n
        ev = f/mp - 1
        print(f"  {lo:.1f}-{hi:<4.2f} | {f:>4.0%} | {n:>4} | {ev:>+7.0%}")


def strat(data, sh, title):
    """Comprar el FAVORITO (lado >0.5) con coste 'sh' (spread+fee, en $) por banda."""
    print("\n" + "-"*62 + f"\n  ESTRATEGIA: comprar favorito | coste entrada +{sh:.2f}$  [{title}]\n" + "-"*62)
    print(f"  {'banda fav':>10} | {'n':>4} | {'gana':>5} | {'EV neto':>8} | {'P&L/100':>8}")
    bands = [(.50,.60),(.60,.70),(.70,.80),(.80,.90),(.90,.98),(.50,.98)]
    for lo, hi in bands:
        ev_sum = wins = n = 0
        for d in data:
            fp = d["p"] if d["p"] >= 0.5 else 1 - d["p"]
            fw = d["yes_won"] if d["p"] >= 0.5 else (1 - d["yes_won"])
            if lo <= fp < hi:
                pe = fp + sh
                ev_sum += (fw / pe - 1); wins += fw; n += 1
        if n < 20: continue
        ev = ev_sum / n
        tag = "  <-- +EV" if ev > 0.01 else ""
        label = "TODOS 0.50-0.98" if (lo, hi) == (.50, .98) else f"{lo:.2f}-{hi:.2f}"
        print(f"  {label:>10} | {n:>4} | {wins/n:>4.0%} | {ev:>+7.1%} | {ev*100:>+7.1f}${tag}")


calib(rows, f"1) CALIBRACIÓN ({EST})")
print()
for sh in (0.0, 0.01, 0.02):
    strat(rows, sh, "in-sample completo")

# 3) OUT-OF-SAMPLE por fecha
rows_sorted = sorted(rows, key=lambda r: r["ts"])
half = len(rows_sorted)//2
old, new = rows_sorted[:half], rows_sorted[half:]
def dt(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
print("\n" + "#"*62)
print(f"  OUT-OF-SAMPLE  (split por fecha de resolución)")
print(f"  TRAIN: {dt(old[0]['ts'])}..{dt(old[-1]['ts'])} (n={len(old)})")
print(f"  TEST : {dt(new[0]['ts'])}..{dt(new[-1]['ts'])} (n={len(new)})")
print("#"*62)
strat(old, 0.01, "TRAIN (antiguo)")
strat(new, 0.01, "TEST (reciente) <-- el que cuenta")
