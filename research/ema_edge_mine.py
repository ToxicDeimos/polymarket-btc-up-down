"""
ema_edge_mine.py — ¿DÓNDE está el edge dentro de "favorito alineado con EMA9"?

Contexto: el backtest sobre fills de ganadores da 90% win para px>EMA9 alineado, pero nuestro forward
mecánico (entra en TODAS las ventanas alineadas 52-82¢) da ~64%. El `aligned` se calcula igual (240s) en
ambos → el gap es SELECCIÓN DE VENTANA: los ganadores eligen un subconjunto. Este script mina los fills de
los ganadores (ya resueltos por CLOB) buscando sub-features computables a 240s que separen sus ganadas de
sus perdidas DENTRO de alineado — si alguna generaliza train/test, es un filtro extra aplicable al bot.

Features a 240s (todas sign-ajustadas a la dirección del favorito, >0 = a favor):
  · margin9   : (px − EMA9)/EMA9 en bps        → fuerza de la alineación (¿solo tendencias fuertes ganan?)
  · mom_open  : (px − open_ventana)/open en bps → momentum desde la apertura de la ventana 5m
  · ema_slope : (EMA9@240 − EMA9@180)/EMA9 bps  → pendiente de la EMA (¿acelerando?)
  · phase     : ts − ws (cuándo entró el ganador) → comportamiento, ¿entran tarde?
Todo sobre datos CACHEADOS (winner_fills + clob_resolutions + klines_1m_sweep). No fetchea nada.

    cd ~/polymarket-btc-up-down/research && python3 ema_edge_mine.py
"""
import csv, os, sys, bisect, statistics, datetime as dt

DIR   = os.path.join(os.path.dirname(__file__), "lab")
FILLS = os.path.join(DIR, "winner_fills.csv")
RESO  = os.path.join(DIR, "clob_resolutions.csv")
KL    = os.path.join(DIR, "klines_1m_sweep.csv")
LO, HI = 0.52, 0.82


def load_kl():
    d = {}
    for ln in open(KL, encoding="utf-8"):
        a = ln.split(",")
        try: d[int(a[0])] = float(a[1])
        except Exception: pass
    return d


def main():
    for p in (FILLS, RESO, KL):
        if not os.path.exists(p):
            print(f"falta {p} — corre antes ema_clob_validate.py"); return
    reso = {r["cid"]: r["winner"] for r in csv.DictReader(open(RESO, encoding="utf-8"))}
    kl = load_kl(); mins = sorted(kl)
    def ema(N):
        k = 2 / (N + 1); out = {}; prev = None
        for m in mins:
            p = kl[m]; prev = p if prev is None else p * k + prev * (1 - k); out[m] = prev
        return out
    E9 = ema(9)
    def at(dic, m):
        i = bisect.bisect_right(mins, m) - 1
        return dic[mins[i]] if i >= 0 else None

    rows = []
    for f in csv.DictReader(open(FILLS, encoding="utf-8")):
        try: p = float(f["price"]); ws = int(f["ws"]); ts = int(f["ts"])
        except Exception: continue
        if not (LO <= p <= HI): continue
        win = reso.get(f["cid"], "")
        if win not in ("Up", "Down"): continue
        e = ws + 240 - 60   # -60: SIN look-ahead (precio a 240s = close de vela [ws+180,ws+240) = kl[ws+180])
        px = at(kl, e); e9 = at(E9, e); e9b = at(E9, e - 60); opn = at(kl, ws - 60)
        if None in (px, e9, e9b, opn): continue
        fav = f["outcome"]; sgn = 1 if fav == "Up" else -1
        aligned = (px > e9) == (fav == "Up")
        if not aligned:            # minamos SOLO dentro de alineado (donde el bot compra)
            continue
        won = 1 if fav == win else 0
        margin9  = sgn * (px - e9) / e9 * 1e4
        mom_open = sgn * (px - opn) / opn * 1e4
        slope    = sgn * (e9 - e9b) / e9 * 1e4
        phase    = ts - ws
        rows.append({"won": won, "ts": ts, "p": p, "day": dt.datetime.utcfromtimestamp(ws).strftime("%m-%d"),
                     "margin9": margin9, "mom_open": mom_open, "slope": slope, "phase": phase})

    n = len(rows)
    if n < 200:
        print(f"pocos fills alineados ({n})"); return
    base = sum(r["won"] for r in rows) / n * 100
    tss = sorted(r["ts"] for r in rows); mid = tss[n // 2]
    def fee(p): return 0.07 * p * (1 - p)
    def wr(rs): return sum(x["won"] for x in rs) / len(rs) * 100 if rs else float("nan")
    def net(rs): return sum(x["won"] - x["p"] - fee(x["p"]) for x in rs) / len(rs) * 100 if rs else float("nan")
    print(f"fills GANADORES alineados (px>EMA9, CLOB): {n}   win base {base:.1f}%")
    print(f"(nuestro forward mecánico da ~64% → buscamos qué sube por encima del {base:.0f}% base)\n")

    def quint(feat):
        vals = sorted(r[feat] for r in rows)
        qs = [vals[int(n * q)] for q in (0.2, 0.4, 0.6, 0.8)]
        def bucket(v):
            return sum(1 for q in qs if v >= q)   # 0..4
        print(f"── {feat}  (Q0=bajo … Q4=alto; cortes {[round(q,2) for q in qs]}) ──")
        gens = []
        for b in range(5):
            seg = [r for r in rows if bucket(r[feat]) == b]
            ask = statistics.mean(r["p"] for r in seg) * 100 if seg else float("nan")
            nt = net([r for r in seg if r["ts"] < mid]); ne = net([r for r in seg if r["ts"] >= mid])
            print(f"   Q{b}: n={len(seg):>4}  win {wr(seg):>5.1f}%  ask {ask:>4.0f}%  "
                  f"NETO {net(seg):>+6.1f}pp  (train {nt:>+6.1f} / test {ne:>+6.1f})")
        # ¿el quintil alto (Q4) bate al bajo (Q0) en train Y test?
        hi = [r for r in rows if bucket(r[feat]) == 4]; lo = [r for r in rows if bucket(r[feat]) == 0]
        g_tr = wr([r for r in hi if r["ts"] < mid]) - wr([r for r in lo if r["ts"] < mid])
        g_te = wr([r for r in hi if r["ts"] >= mid]) - wr([r for r in lo if r["ts"] >= mid])
        verdict = "✓ GENERALIZA (Q4>>Q0 train Y test)" if (g_tr > 5 and g_te > 5) else \
                  ("~ solo train" if g_tr > 5 else "✗ plano/ruido")
        print(f"   gap Q4−Q0:  train {g_tr:+.1f}pp / test {g_te:+.1f}pp  → {verdict}\n")

    for feat in ("margin9", "mom_open", "slope", "phase"):
        quint(feat)

    # comportamiento de entrada: ¿en qué fase entran los ganadores?
    ph = [r["phase"] for r in rows if 0 <= r["phase"] <= 300]
    if ph:
        print(f"fase de entrada de los ganadores (ts−ws): mediana {statistics.median(ph):.0f}s  "
              f"p25 {sorted(ph)[len(ph)//4]:.0f}s  p75 {sorted(ph)[len(ph)*3//4]:.0f}s")
        for lo2, hi2 in [(0, 120), (120, 200), (200, 260), (260, 300)]:
            seg = [r for r in rows if lo2 <= r["phase"] < hi2]
            if seg: print(f"   fase {lo2}-{hi2}s: n={len(seg):>4}  win {wr(seg):>5.1f}%")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
