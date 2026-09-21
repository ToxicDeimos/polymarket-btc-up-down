"""
coherence_5m15m.py — COHERENCIA 5m/15m (última idea estructural). Las ventanas de 15m arrancan en :00/:15/
:30/:45, y la 1ª ventana de 5m de cada una COMPARTE el mismo open (mismo ws). Durante esos primeros 5 min
ambos mercados valoran "¿arriba desde el MISMO precio?", a distinto horizonte → deben ser coherentes.

A t=ws+270 (30s antes de cerrar el 5m, cuando ya es casi decisivo sobre la ventaja actual) comparo el prob
implícito (mid) del 5m y del 15m. Estructura sana: mismo signo y |p15−0,5| ≤ |p5−0,5| (más horizonte → más
cerca de 0,5). EXPLOTABLE: cuando el 5m es DECISIVO (la ventaja de 4,5 min ya está clara), comprar el 15m en
esa dirección y aguantar a su resolución → ¿+EV? Sería el 15m infravalorando la persistencia de la ventaja
que el 5m ya cotiza. Prior BAJO (mercado eficiente), pero medible. Neto de fee taker, con corte train/test.

    cd ~/polymarket-btc-up-down/research && python3 coherence_5m15m.py
"""
import csv, os, sys, glob, json, time, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
T_OFF = 270                 # s desde el open del 15m (=30s antes de cerrar el 1er 5m)
TOL = 8
THRS = (0.70, 0.80, 0.90)   # umbral de "5m decisivo" (p5≥thr o ≤1−thr)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "coh/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.3)


def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if isinstance(d, dict):
        for t in d.get("tokens", []):
            if t.get("winner") is True: return t.get("outcome")
    return None


def fee(p): return 0.07 * p * (1 - p)


def load(prefix):
    """ws -> {cid, sides:{Up:[(ts,bid,ask)], Down:[...]}}  (una ventana por ws)"""
    W = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith(prefix) or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0])
                    bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                ws = int(row[1].split("-")[-1])
                w = W.setdefault(ws, {"cid": row[2], "sides": {"Up": [], "Down": []}})
                w["sides"][row[3]].append((ts, bid, ask))
    for w in W.values():
        for s in ("Up", "Down"): w["sides"][s].sort()
    return W


def row_le(rows, t, tol=TOL):
    """(bid, ask) del último snapshot con ts ≤ t dentro de tol."""
    best = None; bt = None
    for ts, b, a in rows:
        if ts <= t: best = (b, a); bt = ts
        else: break
    if bt is None or t - bt > tol: return None
    return best


def mid_up(w, t):
    r = row_le(w["sides"]["Up"], t)
    if r is None or r[0] is None or r[1] is None: return None
    return (r[0] + r[1]) / 2


def ask_side(w, side, t):
    r = row_le(w["sides"][side], t)
    return r[1] if (r and r[1] is not None) else None


def main():
    W5 = load("btc-updown-5m-"); W15 = load("btc-updown-15m-")
    print(f"ventanas: 5m {len(W5)} · 15m {len(W15)}")
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: reso[r["cid"]] = r["winner"]

    def resolve(cid):
        if cid in reso: return reso[cid]
        w = winner_clob(cid); time.sleep(0.1)
        if w:
            reso[cid] = w; nf = not os.path.exists(CACHE)
            with open(CACHE, "a", newline="", encoding="utf-8") as f:
                cw = csv.writer(f)
                if nf: cw.writerow(["cid", "winner"])
                cw.writerow([cid, w])
        return w

    recs = []   # (ws, p5, p15, win15up, ask15up, ask15dn)
    print(f"emparejando {len(W15)} ventanas de 15m con su 5m del mismo ws…")
    done = 0
    for ws, w15 in W15.items():
        w5 = W5.get(ws)
        done += 1
        if done % 1000 == 0: print(f"   … {done}/{len(W15)}")
        if w5 is None: continue
        t = ws + T_OFF
        p5 = mid_up(w5, t); p15 = mid_up(w15, t)
        if p5 is None or p15 is None: continue
        a_up = ask_side(w15, "Up", t); a_dn = ask_side(w15, "Down", t)
        if a_up is None or a_dn is None: continue
        win15 = resolve(w15["cid"])
        if win15 not in ("Up", "Down"): continue
        recs.append((ws, p5, p15, 1 if win15 == "Up" else 0, a_up, a_dn))
    n = len(recs)
    print(f"  emparejadas y resueltas: {n}")
    if not n: return

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    p5s = [r[1] for r in recs]; p15s = [r[2] for r in recs]
    m5, m15 = mean(p5s), mean(p15s)
    cov = mean([(a - m5) * (b - m15) for a, b in zip(p5s, p15s)])
    s5 = mean([(a - m5) ** 2 for a in p5s]) ** 0.5; s15 = mean([(b - m15) ** 2 for b in p15s]) ** 0.5
    corr = cov / (s5 * s15) if s5 and s15 else float("nan")
    disagree = 100 * mean([1 if (r[1] > 0.5) != (r[2] > 0.5) else 0 for r in recs])
    print("\n" + "=" * 84)
    print(f"  COHERENCIA 5m/15m a t=ws+{T_OFF}s (open compartido)  ·  n={n}")
    print("=" * 84)
    print(f"  correlación p5–p15: {corr:+.3f}   ·   % desacuerdo de signo: {disagree:.1f}%")
    print(f"  |p5−0,5| medio: {mean([abs(r[1]-0.5) for r in recs]):.3f}   |p15−0,5| medio: "
          f"{mean([abs(r[2]-0.5) for r in recs]):.3f}   (sano: 5m más extremo que 15m)")

    mid = sorted(r[0] for r in recs)[n // 2]
    print(f"\n  EXPLOTABLE — 5m decisivo → comprar 15m en esa dirección, aguantar a resolución (neto fee):")
    print(f"    {'thr_p5':>7}{'n':>7}{'ask15':>8}{'sigue%':>8}{'EV':>9}{'EV_tr':>9}{'EV_te':>9}")
    for thr in THRS:
        sub = []
        for r in recs:
            ws_, p5, p15, w15up, aup, adn = r
            if p5 >= thr:       d, ask, hit = "Up", aup, w15up
            elif p5 <= 1 - thr: d, ask, hit = "Down", adn, 1 - w15up
            else: continue
            sub.append((ws_, ask, hit))
        m = len(sub)
        if not m:
            print(f"    {thr:>7.2f}{0:>7}"); continue
        follow = 100 * mean([h for _, _, h in sub])
        ev = 100 * mean([h - a - fee(a) for _, a, h in sub])
        tr = [x for x in sub if x[0] < mid]; te = [x for x in sub if x[0] >= mid]
        evtr = 100 * mean([h - a - fee(a) for _, a, h in tr]) if tr else float("nan")
        evte = 100 * mean([h - a - fee(a) for _, a, h in te]) if te else float("nan")
        askm = mean([a for _, a, _ in sub])
        print(f"    {thr:>7.2f}{m:>7}{askm:>8.3f}{follow:>7.0f}%{ev:>+8.2f}{evtr:>+9.2f}{evte:>+9.2f}")
    print("\nLECTURA: 'sigue%' = veces que el 15m resuelve en la dirección del 5m decisivo. 'ask15' = lo que pagas")
    print("por el lado del 15m. EV = sigue − ask − fee. +EV ESTABLE en train Y test → el 15m infravalora la")
    print("ventaja → edge estructural real. Si ask15 ≈ sigue (EV≈0) → el 15m ya lo cotiza bien = eficiente.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
