"""
favorite_sizing.py — dimensiona el favorite bot desde su DRAWDOWN REAL (bootstrap Monte Carlo).

El máx drawdown que ves hoy NO es el peor que verás: crece con los fills. Este script resamplea con
reemplazo TUS fills reales (ask, won) para estimar cómo de grande se pondrá el drawdown a distintos
horizontes, y de ahí saca el STAKE (% del capital por ventana, plano) para que el peor acantilado
plausible (p95) no pase de tu tolerancia. Escribe una caché JSON que el dashboard lee.

    python3 favorite_sizing.py                # BTC
    FAV_ASSET=eth python3 favorite_sizing.py  # ETH
Autónomo. Usa numpy si está (rápido, por lotes para no comerse la RAM de la Pi); si no, stdlib.
"""
import os, sys, csv, json, time, random

ASSET = (os.environ.get("FAV_ASSET") or "btc").lower()
DIR = os.path.dirname(__file__)
LOG = os.path.join(DIR, "favorite_paper_log.csv" if ASSET == "btc" else f"favorite_paper_{ASSET}_log.csv")
CACHE = os.path.join(DIR, f"favorite_sizing_{ASSET}.json")
PATHS   = 8000
HORIZONS = [None, 1000, 2000, 5000]      # None = n real (sanity: su mediana ≈ el drawdown observado)
TOLS    = [0.10, 0.15, 0.20]
REC_TOL, REC_H = 0.15, 5000              # recomendación por defecto: tol 15%, horizonte 5000 (~2 meses)


def load_pnls():
    if not os.path.exists(LOG):
        print(f"sin log: {LOG}"); sys.exit(1)
    pnls = []
    for r in csv.DictReader(open(LOG, encoding="utf-8")):
        if r.get("status") != "bought" or r.get("won") not in ("0", "1"): continue
        try: a = float(r["ask"])
        except Exception: continue
        pnls.append((1 / a - 1) if r["won"] == "1" else -1.0)   # P&L plano $1/ventana
    return pnls


def sim(pnls, L, paths=PATHS, batch=2000):
    """Máx drawdown por camino (en 'ventanas de stake'), bootstrap. Devuelve lista ORDENADA."""
    try:
        import numpy as np
        arr = np.asarray(pnls); out = []
        done = 0
        while done < paths:
            b = min(batch, paths - done)
            eq = np.cumsum(arr[np.random.randint(0, len(arr), size=(b, L))], axis=1)
            out.extend((np.maximum.accumulate(eq, axis=1) - eq).max(axis=1).tolist())
            done += b
        return sorted(out)
    except ImportError:
        out = []
        for _ in range(min(paths, 3000)):                       # stdlib: menos caminos por velocidad
            eq = peak = mdd = 0.0
            for x in random.choices(pnls, k=L):
                eq += x
                if eq > peak: peak = eq
                if peak - eq > mdd: mdd = peak - eq
            out.append(mdd)
        return sorted(out)


def pct(s, p): return s[min(len(s) - 1, int(len(s) * p / 100))]


def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    pnls = load_pnls(); n = len(pnls)
    if n < 30:
        print(f"[{ASSET.upper()}] solo {n} fills resueltos — muy pocos para dimensionar, espera a más."); return
    wr = sum(1 for x in pnls if x > 0) / n
    print(f"[{ASSET.upper()}]  {n} fills resueltos · win {wr:.1%} · edge medio ${sum(pnls)/n:+.4f}/fill\n")

    print("MÁX DRAWDOWN (bootstrap de TUS fills, en 'ventanas de stake'):")
    print(f"{'horizonte':>10} | {'mediana':>8} {'p90':>6} {'p95':>6} {'p99':>7}")
    dd95 = {}
    for L in HORIZONS:
        LL = n if L is None else L
        s = sim(pnls, LL); dd95[LL] = pct(s, 95)
        print(f"{LL:>10} | {pct(s,50):>8.1f} {pct(s,90):>6.1f} {pct(s,95):>6.1f} {pct(s,99):>7.1f}"
              + ("  (n real → su mediana ≈ tu drawdown observado)" if L is None else ""))

    H = REC_H if REC_H in dd95 else max(dd95)
    print(f"\nSTAKE sugerido (PLANO, % del capital/ventana) — peor drawdown p95 a {H} fills = {dd95[H]:.1f} stakes:")
    for tol in TOLS:
        print(f"   tolerancia {int(tol*100)}% de drawdown  →  {tol/dd95[H]*100:.2f}% del capital/ventana")
    rec = REC_TOL / dd95[H] * 100
    print(f"\n→ RECOMENDADO: ~{rec:.2f}% del capital/ventana (tol {int(REC_TOL*100)}%). Con $100 → ~${rec:.2f}/ventana.")
    print("  PLANO, no Kelly. Condicionado a que el edge sea real. Recalcula al crecer los fills.")

    json.dump({"asset": ASSET, "n": n, "win": round(wr, 4), "dd95": round(dd95[H], 1),
               "horizon": H, "rec_pct": round(rec, 3), "tol": REC_TOL,
               "computed": time.strftime("%Y-%m-%d %H:%M")},
              open(CACHE, "w", encoding="utf-8"))
    print(f"\ncaché → {os.path.basename(CACHE)} (la lee el dashboard)")


if __name__ == "__main__":
    main()
