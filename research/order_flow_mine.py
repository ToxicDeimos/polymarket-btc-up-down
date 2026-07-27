"""
EXPERIMENTO #16 — MINERO DE ORDER-FLOW: ¿qué del LIBRO usan los ganadores para SELECCIONAR el favorito?

La última frontera del proyecto. selection_miner probó que la selección de los ganadores NO está en
features de precio (move/vol/accel salieron planos). Solo queda el LIBRO / flujo de órdenes, que el
lab-collector lleva grabando desde ~2026-07-16 (books_*.csv cada ~5s + tape_*.csv).

Este script CRUZA los fills COMPLETOS de los ganadores (wtrades_*.csv de winners_deep) en la zona favorito
con el estado del LIBRO a la entrada, y busca qué feature de order-flow separa los favoritos que GANAN del
montón. Con disciplina: puerta de potencia (no concluye sin datos), train/test por día, y línea base de
población (¿el favorito del montón también gana más ahí? = replicable, no skill).

Features de order-flow a la entrada (lado del favorito, desde el top-3 del libro + cinta):
  · spread = a1 − b1                          · imb1  = bs1/(bs1+as1)   (presión bid en el tope)
  · imb3  = Σbid/(Σbid+Σask) 3 niveles        · micro = microprice − mid (>0 = presión al alza)
  · aflow = neto BUY−SELL (taker) del favorito en los 30s previos (cinta, muestreada → ruidosa)
  · favask = a1 del favorito (control de precio)   · spot_mom = move spot 60s en bps (control)

    python order_flow_mine.py
Necesita lab/klines_1m_btc.csv (hist_backtest.py), lab/wtrades_*.csv (winners_deep.py) y lab/books_*.csv +
tape_*.csv + spot_*.csv (lab-collector, 24/7). Autónomo (stdlib). Procesa día a día (RAM acotada, Pi-friendly).

ESTADO (2026-07-27): esqueleto EN PAUSA. El lab tenía ~10 días → sin potencia. Correr cuando haya ~4-6
semanas (≈ mediados de agosto 2026). Ver memoria order-flow-mine-pending.md.
"""
import os, sys, csv, glob, math, bisect, time
from collections import defaultdict

DIR   = os.path.join(os.path.dirname(__file__), "lab")
KL    = os.path.join(DIR, "klines_1m_btc.csv")
FAV_LO, FAV_HI = 0.62, 0.95      # zona favorito (donde está el edge de los ganadores)
MAXAGE  = 12                     # s: antigüedad máxima de un snapshot de libro para valer como "a la entrada"
AFLOW_W = 30                     # s: ventana de flujo agresor previo
# Puerta de potencia (pre-registrada): sin esto NO se emite veredicto.
MIN_DAYS        = 28             # ~4 semanas de libro
MIN_WIN_FILLS   = 300           # n de fills-favorito-de-ganadores con libro (power ~3pp @ p~.9)


def day_of(ts): return time.strftime("%Y%m%d", time.gmtime(int(ts)))

def load_klines():
    if not os.path.exists(KL):
        print(f"falta {KL} — corre antes: python3 hist_backtest.py 365"); sys.exit(1)
    d = {}
    with open(KL, encoding="utf-8") as f:
        for ln in f:
            a = ln.split(",")
            try: d[int(a[0])] = float(a[1])
            except Exception: pass
    return d

def _f(x):
    try: return float(x)
    except Exception: return None

# ── índices por día (memoria acotada) ────────────────────────────────────────

def load_books_day(day):
    """(cid, side) -> lista ordenada [(ts, levels)]; levels = dict con b/bs/a/as por nivel."""
    idx = defaultdict(list)
    p = os.path.join(DIR, f"books_{day}.csv")
    if not os.path.exists(p): return idx
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try: ts = int(r["ts"])
        except Exception: continue
        lv = {k: _f(r.get(k)) for k in ("b1","bs1","b2","bs2","b3","bs3","a1","as1","a2","as2","a3","as3")}
        idx[(r.get("cid"), r.get("side"))].append((ts, lv))
    for k in idx: idx[k].sort(key=lambda x: x[0])
    return idx

def book_at(idx, cid, side, t):
    arr = idx.get((cid, side))
    if not arr: return None
    i = bisect.bisect_right([x[0] for x in arr], t) - 1
    if i < 0 or t - arr[i][0] > MAXAGE: return None
    return arr[i][1]

def load_tape_day(day):
    """(cid, outcome) -> [(ts_trade, trade_side, size, price)] y cid->ws. SOLO 5m (los ganadores son 5m)."""
    idx = defaultdict(list); cid_ws = {}
    p = os.path.join(DIR, f"tape_{day}.csv")
    if not os.path.exists(p): return idx, cid_ws
    for r in csv.DictReader(open(p, encoding="utf-8")):
        slug = r.get("slug", "") or ""
        if not slug.startswith("btc-updown-5m-"): continue
        try:
            tt = int(r["ts_trade"]); sz = float(r["size"]); pr = float(r["price"]); ws = int(slug.split("-")[-1])
        except Exception: continue
        idx[(r.get("cid"), r.get("outcome"))].append((tt, r.get("trade_side"), sz, pr))
        cid_ws[r.get("cid")] = ws
    for k in idx: idx[k].sort(key=lambda x: x[0])
    return idx, cid_ws

def load_spot_day(day):
    p = os.path.join(DIR, f"spot_{day}.csv"); ks = []; vs = []
    if not os.path.exists(p): return ks, vs
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try: ks.append(int(r["ts"])); vs.append(float(r["price"]))
        except Exception: pass
    return ks, vs

def spot_at(ks, vs, t, maxage=15):
    if not ks: return None
    i = bisect.bisect_right(ks, t) - 1
    return vs[i] if i >= 0 and t - ks[i] <= maxage else None

def aflow(tape_idx, cid, favside, t):
    """Neto BUY−SELL (tamaño) del favorito en [t−AFLOW_W, t] desde la cinta. Ruidoso (muestreada)."""
    arr = tape_idx.get((cid, favside))
    if not arr: return None
    net = 0.0
    for tt, bs, sz, _ in arr:
        if tt < t - AFLOW_W: continue
        if tt > t: break
        net += sz if bs == "BUY" else -sz
    return net

def features(bk, bk_other, aflw, spot_mom):
    """Features de order-flow del lado favorito. bk = levels favorito; bk_other = levels underdog."""
    b1, bs1, a1, as1 = bk["b1"], bk["bs1"], bk["a1"], bk["as1"]
    if None in (b1, bs1, a1, as1) or (bs1 + as1) <= 0: return None
    bids = [bk[k] for k in ("bs1","bs2","bs3") if bk[k]]
    asks = [bk[k] for k in ("as1","as2","as3") if bk[k]]
    sb, sa = sum(bids), sum(asks)
    mid   = (a1 + b1) / 2
    micro = (a1 * bs1 + b1 * as1) / (bs1 + as1)
    return {
        "spread":   round(a1 - b1, 4),
        "imb1":     round(bs1 / (bs1 + as1), 4),
        "imb3":     round(sb / (sb + sa), 4) if (sb + sa) else None,
        "micro":    round(micro - mid, 5),
        "favask":   round(a1, 4),
        "aflow":    aflw,
        "spot_mom": spot_mom,
    }


def collect():
    """Cruza wtrades (ganadores) + tape (montón) con el libro, día a día. Devuelve (winner_rows, pop_rows)."""
    kl = load_klines()
    def winner(ws):
        o, c = kl.get(ws), kl.get(ws + 300)
        return ("Up" if c >= o else "Down") if (o is not None and c is not None) else None

    # fills de ganadores (BUY, zona favorito) agrupados por día
    wfiles = glob.glob(os.path.join(DIR, "wtrades_*.csv"))
    if not wfiles:
        print("faltan lab/wtrades_*.csv — corre antes: python3 winners_deep.py"); sys.exit(1)
    win_by_day = defaultdict(list)
    for p in wfiles:
        who = os.path.basename(p)[8:-4]
        for r in csv.DictReader(open(p, encoding="utf-8")):
            try: ws = int(r["slug"].split("-")[-1]); pr = float(r["price"])
            except Exception: continue
            if r.get("side") != "BUY" or not (FAV_LO <= pr <= FAV_HI): continue
            win_by_day[day_of(r["ts"])].append(
                {"who": who, "ts": int(r["ts"]), "ws": ws, "cid": r.get("cid"), "out": r.get("outcome"), "pr": pr})

    book_days = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR, "books_*.csv"))})
    if not book_days:
        print("faltan lab/books_*.csv — el lab-collector aún no ha grabado libro"); sys.exit(1)

    winner_rows, pop_rows = [], []
    for day in book_days:
        bidx = load_books_day(day)
        tidx, cid_ws = load_tape_day(day)
        sks, svs = load_spot_day(day)
        if not bidx: continue

        def fav_side(cid, t):
            u = book_at(bidx, cid, "Up", t); d = book_at(bidx, cid, "Down", t)
            if not u or not d or u["a1"] is None or d["a1"] is None: return None, None, None
            return ("Up", u, d) if u["a1"] > d["a1"] else ("Down", d, u)

        def spot_mom_at(t):
            s0, s1 = spot_at(sks, svs, t - 60), spot_at(sks, svs, t)
            return round((s1 - s0) / s0 * 10000, 2) if (s0 and s1) else None

        # ganadores de ese día
        for fl in win_by_day.get(day, []):
            wn = winner(fl["ws"])
            if wn is None: continue
            fav, bk, bko = fav_side(fl["cid"], fl["ts"])
            if fav is None or fav != fl["out"]:      # solo compras de FAVORITO confirmadas por libro
                continue
            ft = features(bk, bko, aflow(tidx, fl["cid"], fav, fl["ts"]), spot_mom_at(fl["ts"]))
            if ft is None: continue
            ft.update({"won": 1 if fl["out"] == wn else 0, "day": day, "who": fl["who"]})
            winner_rows.append(ft)

        # población (montón): cinta 5m, BUY, zona favorito, lado favorito. ws desde el slug (cid_ws).
        for (cid, out), arr in tidx.items():
            ws = cid_ws.get(cid)
            if ws is None: continue
            wn = winner(ws)
            if wn is None: continue
            for tt, bs, sz, pr in arr:
                if bs != "BUY" or not (FAV_LO <= pr <= FAV_HI): continue
                fav, bk, bko = fav_side(cid, tt)
                if fav is None or fav != out: continue
                ft = features(bk, bko, aflow(tidx, cid, fav, tt), spot_mom_at(tt))
                if ft is None: continue
                ft.update({"won": 1 if out == wn else 0, "day": day})
                pop_rows.append(ft)
        del bidx, tidx
    return winner_rows, pop_rows, len(book_days)


# ── análisis ─────────────────────────────────────────────────────────────────

FEATS = ["spread", "imb1", "imb3", "micro", "aflow", "favask", "spot_mom"]

def wr(seg):
    seg = [x for x in seg if x is not None]
    if not seg: return None
    n = len(seg); w = sum(seg) / n; se = math.sqrt(w * (1 - w) / n)
    return n, w, max(0, w - 1.96 * se), min(1, w + 1.96 * se)

def quintiles(rows, key):
    vals = sorted(r[key] for r in rows if r.get(key) is not None)
    if len(vals) < 20: return None
    return [vals[int(len(vals) * f)] for f in (.2, .4, .6, .8)]

def split_by_feature(rows, key, label):
    q = quintiles(rows, key)
    if not q:
        print(f"    {key:<9} (pocos datos)"); return None
    edges = [(-1e18, q[0]), (q[0], q[1]), (q[1], q[2]), (q[2], q[3]), (q[3], 1e18)]
    names = ["Q1 bajo", "Q2", "Q3", "Q4", "Q5 alto"]
    print(f"    {key} ({label}):")
    lifts = []
    for (lo, hi), nm in zip(edges, names):
        s = wr([r["won"] for r in rows if r.get(key) is not None and lo <= r[key] < hi])
        if s: print(f"       {nm:<8} n={s[0]:>5}  win {s[1]:6.2%}  (IC {s[2]:.1%}-{s[3]:.1%})")
        lifts.append(s[1] if s else None)
    if lifts[0] is not None and lifts[-1] is not None:
        return (lifts[-1] - lifts[0]) * 100    # lift Q5−Q1 en pp
    return None

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    print("=" * 92)
    print("MINERO DE ORDER-FLOW #16 — ¿qué del libro separa los favoritos GANADORES?")
    print("=" * 92)
    winner_rows, pop_rows, ndays = collect()

    print(f"\ndías de libro: {ndays}  ·  fills-favorito de ganadores con libro: {len(winner_rows)}  ·  "
          f"población (montón): {len(pop_rows)}")

    # PUERTA DE POTENCIA (pre-registrada) — sin datos NO se concluye
    ready = ndays >= MIN_DAYS and len(winner_rows) >= MIN_WIN_FILLS
    if not ready:
        print("\n" + "!" * 92)
        print(f"POTENCIA INSUFICIENTE → NO se emite veredicto. Se exige ≥{MIN_DAYS} días de libro y "
              f"≥{MIN_WIN_FILLS} fills.")
        print(f"Ahora: {ndays} días, {len(winner_rows)} fills. El lab sigue acumulando (lab-collector 24/7).")
        print("Se muestran los cortes en modo PREVIEW (solo para verificar el pipeline; NO son conclusiones).")
        print("!" * 92)
    if not winner_rows:
        print("\nsin fills cruzables todavía — vuelve cuando el lab tenga solape con el historial de ganadores.")
        return

    base = wr([r["won"] for r in winner_rows])
    print(f"\nwin base de los favoritos de los GANADORES: {base[1]:.2%}  (n={base[0]})\n")

    # train/test por día (mitad cronológica) — disciplina anti-overfitting
    days = sorted({r["day"] for r in winner_rows})
    cut = days[len(days) // 2] if len(days) > 1 else None
    if cut:
        train = [r for r in winner_rows if r["day"] < cut]
        test  = [r for r in winner_rows if r["day"] >= cut]
        print(f"TRAIN {len(train)} fills (días < {cut})   ·   TEST {len(test)} fills (días ≥ {cut})\n")
    else:
        train, test = winner_rows, []
        print("sin split train/test (1 solo día) — se analiza todo junto (PREVIEW)\n")

    print("─" * 92)
    print("LIFT Q5−Q1 por feature en TRAIN (ganadores):")
    print("─" * 92)
    train_lift = {}
    for k in FEATS:
        lift = split_by_feature(train, k, "ganadores/train")
        if lift is not None: train_lift[k] = lift
        print()

    if not train_lift:
        print("sin features con datos suficientes en train."); return
    best = max(train_lift, key=lambda k: abs(train_lift[k]))
    print("=" * 92)
    print(f"FEATURE CANDIDATA (mayor |lift| en train): {best}  ({train_lift[best]:+.2f}pp Q5−Q1)")
    print("=" * 92)

    # confirmación en TEST (mismo signo) + POBLACIÓN (¿el montón también sube ahí? = replicable)
    if test:
        print("\nConfirmación en TEST (mismo corte de quintiles del feature candidato):")
        test_lift = split_by_feature(test, best, "ganadores/test")
        print(f"\n  lift TEST de {best}: {test_lift:+.2f}pp"
              if test_lift is not None else "\n  (test sin datos suficientes)")
    if pop_rows:
        print("\nLÍNEA BASE POBLACIÓN — el favorito del MONTÓN por el mismo feature (¿replicable?):")
        pop_lift = split_by_feature(pop_rows, best, "montón")

    print("\n" + "=" * 92)
    print("CÓMO LEER (pre-registrado)")
    print("=" * 92)
    print(f"· REPLICABLE si {best} da lift ≥3pp en TRAIN, mismo signo y ≥1.5pp en TEST, y el MONTÓN sube igual")
    print("  → regla: comprar el favorito solo cuando su order-flow está en el quintil bueno. Eso es EDGE nuevo.")
    print("· Si el lift NO generaliza a test o el montón NO lo confirma → es ruido/skill, no una regla replicable.")
    print("· aflow viene de la cinta MUESTREADA (incompleta) → tómalo como señal débil; imb/micro/spread son del")
    print("  libro directo (fiables). Con veredicto REAL: pasar la regla a favorite_paper como filtro y medir en vivo.")
    if not ready:
        print("\n⚠ RECORDATORIO: esto ha corrido en PREVIEW (potencia insuficiente). NO tomes los números como edge.")

if __name__ == "__main__":
    main()
