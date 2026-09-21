"""
entry_dynamics.py — DESCIFRAR el edge de los ganadores por su DINÁMICA (no el snapshot, que ya falló). 13mm-
wrench compra bajo mercado y el precio LUEGO sube = compra el SUELO de un bajón que REVIERTE. "El bid cayó" solo
es −EV (cazar cuchillos, ya visto); la clave sería "cayó Y ya rebota" (confirmación de suelo).

PARTE 1 — ¿sus entradas son de reversión? Por cada fill ganador: d60 = Δmid(X) en 60s previos, d15 = Δmid en
  15s previos. reversal = cayó (d60≤−REV) y sube/plano (d15≥0). ¿Qué % lo son y ganan más?
PARTE 2 — STANDALONE: en el universo, comprar X cuando se da esa "confirmación de suelo" (d60≤−D y d15≥0),
  aguantar a resolución. Barre D. Si +EV y estable (train/test) → edge PROPIO derivado, tradeable en vivo.

    cd ~/polymarket-btc-up-down/research && python3 entry_dynamics.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
REACT = 5; BUFFER = 5; TOL = 12; REV = 0.03
DS = (0.03, 0.05, 0.08)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dyn/1.0"})
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


def load_mids():
    """slug -> side -> ([ts],[mid],[ask])"""
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0])
                    bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                if bid is None or ask is None: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, (bid + ask) / 2, ask))
    S = {}
    for slug, sides in tmp.items():
        S[slug] = {}
        for s in ("Up", "Down"):
            r = sorted(sides[s]); S[slug][s] = ([x[0] for x in r], [x[1] for x in r], [x[2] for x in r])
    return S


def le(tss, arr, t, tol=TOL):
    if not tss: return None
    i = bisect.bisect_right(tss, t) - 1
    if i >= 0 and t - tss[i] <= tol: return arr[i]
    return None


def load_fills():
    F = []; dd = set()
    for path in sorted(glob.glob(os.path.join(DIR, "fills_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("trade_side") != "BUY" or r.get("outcome") not in ("Up", "Down"): continue
                try: ts = int(float(r["ts_trade"]))
                except Exception: continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"))
                if k in dd: continue
                dd.add(k)
                F.append((ts, r["slug"], r["cid"], r["outcome"]))
    return F


def main():
    M = load_mids(); F = load_fills()
    print(f"slugs libro: {len(M)} · fills: {len(F)}")
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: reso[r["cid"]] = r["winner"]
    slug_cid = {}
    for slug in M:
        pass

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

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")

    # PARTE 1 — ¿las entradas ganadoras son de reversión?
    rev = []; nonrev = []
    for ts, slug, cid, X in F:
        if slug not in M: continue
        wlen = 300 if "-5m-" in slug else 900; ws = int(slug.split("-")[-1]); close = ws + wlen
        te = ts + REACT
        if te > close - BUFFER: continue
        tss, mids, asks = M[slug][X]
        mnow = le(tss, mids, ts); m60 = le(tss, mids, ts - 60); m15 = le(tss, mids, ts - 15)
        ask = le(tss, asks, te)
        if None in (mnow, m60, m15, ask) or not (0.0 < ask < 1.0): continue
        win = resolve(cid)
        if win not in ("Up", "Down"): continue
        hit = 1 if win == X else 0
        rec = (ws, ask, hit)
        (rev if (mnow - m60 <= -REV and mnow - m15 >= 0) else nonrev).append(rec)

    def blk(name, sub):
        if len(sub) < 30: print(f"  {name:>16}: pocos ({len(sub)})"); return
        wr = 100 * mean([r[2] for r in sub]); ev = 100 * mean([r[2] - r[1] - fee(r[1]) for r in sub])
        print(f"  {name:>16}: n={len(sub):>6}  gana {wr:.0f}%  EV {ev:+.2f}pp  ask medio {mean([r[1] for r in sub]):.3f}")
    print("\n=== PARTE 1: entradas ganadoras — ¿reversión (cayó 60s, sube/plano 15s)? ===")
    tot = len(rev) + len(nonrev)
    print(f"  de {tot} fills: {len(rev)} reversión ({100*len(rev)/tot:.0f}%) · {len(nonrev)} no-reversión")
    blk("reversión", rev); blk("no-reversión", nonrev)

    # PARTE 2 — STANDALONE en el universo: 1er "suelo confirmado" por ventana/outcome
    print("\n=== PARTE 2: señal STANDALONE en el universo (comprar en suelo confirmado, aguantar) ===")
    print(f"  {'D_caída':>8}{'n':>8}{'ask':>7}{'gana%':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
    # cid por slug desde books
    scid = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) > 2 and row[1] not in scid: scid[row[1]] = row[2]
    for D in DS:
        recs = []
        for slug, sides in M.items():
            wlen = 300 if "-5m-" in slug else 900; ws = int(slug.split("-")[-1]); close = ws + wlen
            cid = scid.get(slug)
            if not cid: continue
            win = reso.get(cid) or resolve(cid)
            if win not in ("Up", "Down"): continue
            for X in ("Up", "Down"):
                tss, mids, asks = sides[X]
                for k in range(len(tss)):
                    t = tss[k]
                    if t < ws + 60 or t > close - BUFFER - REACT: continue
                    m60 = le(tss, mids, t - 60); m15 = le(tss, mids, t - 15)
                    if m60 is None or m15 is None: continue
                    if mids[k] - m60 <= -D and mids[k] - m15 >= 0:
                        a = le(tss, asks, t + REACT)
                        if a is not None and 0.0 < a < 1.0:
                            recs.append((ws, a, 1 if win == X else 0))
                        break   # 1 por ventana/outcome
        n = len(recs)
        if n < 30: print(f"  {int(D*100):>7}¢{n:>8}   (pocos)"); continue
        mid = sorted(r[0] for r in recs)[n // 2]
        ask = mean([r[1] for r in recs]); wr = 100 * mean([r[2] for r in recs])
        ev = 100 * mean([r[2] - r[1] - fee(r[1]) for r in recs])
        tr = [r for r in recs if r[0] < mid]; te = [r for r in recs if r[0] >= mid]
        evtr = 100 * mean([r[2] - r[1] - fee(r[1]) for r in tr]) if tr else float("nan")
        evte = 100 * mean([r[2] - r[1] - fee(r[1]) for r in te]) if te else float("nan")
        print(f"  {int(D*100):>7}¢{n:>8}{ask:>7.3f}{wr:>6.0f}%{ev:>+7.2f}{evtr:>+8.2f}{evte:>+8.2f}")
    print("\nLECTURA: P1 dice si su patrón ES reversión-de-suelo. P2 dice si ESE patrón es +EV standalone. Si P2")
    print("da +EV estable a algún D → edge PROPIO derivado (bottom-fishing confirmado), tradeable por WSS. Si −")
    print("→ tampoco la dinámica de suelo lo explica y su edge no deja huella replicable.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
