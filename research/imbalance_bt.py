"""
imbalance_bt.py — VALIDA STANDALONE la firma que salió en feature_mine: comprar un outcome cuando su IMBALANCE
de libro (fullimb) está en cierta banda es +EV, sin condicionar a los fills ganadores. Si en el UNIVERSO de
ventanas (a un instante fijo) el imbalance 0,55-0,70 da +EV estable → señal observable tradeable en vivo (el
WSS da el libro). Si solo funcionaba en los momentos de los ganadores → estaba entrelazado con su selección.

Por ventana y outcome, a t=ws+0,5·wlen y ws+0,75·wlen: fullimb(X) y ask(X); bucket por fullimb; EV=comprar X
al ask y aguantar a resolución, neto fee, train/test. 5m y 15m por separado.

    cd ~/polymarket-btc-up-down/research && python3 imbalance_bt.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 12
FRACS = (0.50, 0.75)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "imb/1.0"})
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


def load_series(prefix, col):
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, f"{prefix}_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) <= col or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); v = float(row[col]) if row[col] else None
                except Exception: continue
                if v is None: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, v))
    S = {}
    for slug, sides in tmp.items():
        S[slug] = {}
        for s in ("Up", "Down"):
            r = sorted(sides[s]); S[slug][s] = ([t for t, _ in r], [v for _, v in r])
    return S


def le(pair, t, tol=TOL):
    tss, vs = pair
    if not tss: return None
    i = bisect.bisect_right(tss, t) - 1
    if i >= 0 and t - tss[i] <= tol: return vs[i]
    return None


def main():
    ASK = load_series("books", 10)          # best ask
    IMB = load_series("bookdepth", 10)       # fullimb
    print(f"slugs ask: {len(ASK)} · slugs imb: {len(IMB)}")
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: reso[r["cid"]] = r["winner"]

    # cid por slug (de los books)
    slug_cid = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) > 2 and row[1].startswith("btc-updown-") and row[1] not in slug_cid:
                    slug_cid[row[1]] = row[2]

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

    recs = []   # (v, ws, ask, imb, hit)
    slugs = [s for s in IMB if s in ASK]
    print(f"resolviendo {len(slugs)} ventanas con imbalance…"); done = 0
    for slug in slugs:
        v = "5m" if "-5m-" in slug else "15m"; wlen = 300 if v == "5m" else 900
        ws = int(slug.split("-")[-1]); cid = slug_cid.get(slug)
        if not cid: continue
        win = resolve(cid); done += 1
        if done % 3000 == 0: print(f"   … {done}/{len(slugs)}")
        if win not in ("Up", "Down"): continue
        for fr in FRACS:
            t = ws + int(fr * wlen)
            for X in ("Up", "Down"):
                a = le(ASK[slug][X], t); im = le(IMB[slug][X], t)
                if a is None or im is None or not (0.0 < a < 1.0): continue
                recs.append((v, ws, a, im, 1 if win == X else 0))
    print(f"observaciones: {len(recs)}")
    if not recs: return

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    BINS = [("<0.40", 0, 0.40), ("0.40-0.55", 0.40, 0.55), ("0.55-0.70", 0.55, 0.70), (">=0.70", 0.70, 1.01)]
    for v in ("5m", "15m"):
        vv = [r for r in recs if r[0] == v]
        if not vv: continue
        mid = sorted(r[1] for r in vv)[len(vv) // 2]
        base = 100 * mean([r[4] - r[2] - fee(r[2]) for r in vv])
        print("\n" + "=" * 80)
        print(f"  IMBALANCE STANDALONE {v} (universo) · comprar X al ask por bucket de fullimb · base {base:+.2f}pp")
        print("=" * 80)
        print(f"    {'fullimb':>11}{'n':>8}{'ask':>7}{'gana%':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
        for lab, lo, hi in BINS:
            sub = [r for r in vv if lo <= r[3] < hi]
            m = len(sub)
            if m < 30: print(f"    {lab:>11}{m:>8}   (pocos)"); continue
            ask = mean([r[2] for r in sub]); wr = 100 * mean([r[4] for r in sub])
            ev = 100 * mean([r[4] - r[2] - fee(r[2]) for r in sub])
            tr = [r for r in sub if r[1] < mid]; te = [r for r in sub if r[1] >= mid]
            evtr = 100 * mean([r[4] - r[2] - fee(r[2]) for r in tr]) if tr else float("nan")
            evte = 100 * mean([r[4] - r[2] - fee(r[2]) for r in te]) if te else float("nan")
            print(f"    {lab:>11}{m:>8}{ask:>7.3f}{wr:>6.0f}%{ev:>+7.2f}{evtr:>+8.2f}{evte:>+8.2f}")
    print("\nLECTURA: si en el universo el bucket 0,55-0,70 (u otro) es +EV y estable (tr y te) → el imbalance es")
    print("señal STANDALONE observable → tradeable en vivo por WSS. Si todo ~0/− → el +7,4 de feature_mine venía")
    print("de la selección de los ganadores (no replicable solo con imbalance).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
