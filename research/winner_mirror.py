"""
winner_mirror.py — ¿El edge de los wallets GANADORES es TRANSFERIBLE? No copiamos su "indicador" (no hay, ya
visto en order-flow-mine) sino sus TRADES: cuando un ganador COMPRA un outcome, entramos nosotros AL PRECIO
QUE NOSOTROS conseguimos (su ask en el momento en que lo VEMOS, con el lag del colector) y aguantamos a
resolución. Su ventaja es de maker (compran ~4¢ bajo el mid); la pregunta exacta:

  ¿su DIRECCIÓN sobrevive al spread que pagamos de más al seguirlos tarde, o se lo come?

Datos del lab: fills_*.csv (fills de los 6 ganadores) + books_*.csv (nuestro ask al verlos) + resolución CLOB.
their_EV = hit − su_precio (maker) ·  our_EV = hit − nuestro_ask − fee_taker.  Por wallet y con corte train/test.

    cd ~/polymarket-btc-up-down/research && python3 winner_mirror.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 15                     # s para casar nuestro ask con el momento en que vemos el fill
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mirror/1.0"})
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


def load_books_asks():
    """slug -> {Up:([ts],[ask]), Down:([ts],[ask])}  (índices para bisect)"""
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, ask))
    B = {}
    for slug, sides in tmp.items():
        B[slug] = {}
        for s in ("Up", "Down"):
            rows = sorted(sides[s]); B[slug][s] = ([t for t, _ in rows], [a for _, a in rows])
    return B


def ask_at(idx_ask, t, tol=TOL):
    tss, asks = idx_ask
    if not tss: return None
    i = bisect.bisect_left(tss, t); best = None; bd = tol + 1
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(tss):
            d = abs(tss[j] - t)
            if d < bd: bd = d; best = asks[j]
    return best if bd <= tol else None


def load_fills():
    F = []
    for path in sorted(glob.glob(os.path.join(DIR, "fills_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("trade_side") != "BUY" or r.get("outcome") not in ("Up", "Down"): continue
                try:
                    ts_seen = int(float(r["ts_seen"])); price = float(r["price"])
                except Exception: continue
                F.append((r["wallet"], ts_seen, r["slug"], r["cid"], r["outcome"], price))
    return F


def main():
    B = load_books_asks(); F = load_fills()
    print(f"fills BUY de ganadores: {len(F)} · slugs con libro: {len(B)}")
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

    recs = []   # (wallet, ws, hit, our_ask, their_price)
    dedup = set()
    for wallet, ts_seen, slug, cid, outcome, price in F:
        if slug not in B: continue
        key = (cid, outcome, round(price, 3), wallet)
        if key in dedup: continue
        dedup.add(key)
        oa = ask_at(B[slug][outcome], ts_seen)
        if oa is None or not (0.0 < oa < 1.0): continue
        win = resolve(cid)
        if win not in ("Up", "Down"): continue
        ws = int(slug.split("-")[-1])
        recs.append((wallet, ws, 1 if win == outcome else 0, oa, price))
    n = len(recs)
    print(f"fills copiables (con ask y resolución): {n}")
    if not n: return

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    mid = sorted(r[1] for r in recs)[n // 2]

    def block(name, sub):
        m = len(sub)
        if not m: return
        wr = 100 * mean([r[2] for r in sub])
        tp = mean([r[4] for r in sub]); oa = mean([r[3] for r in sub])
        their = 100 * mean([r[2] - r[4] for r in sub])
        our = 100 * mean([r[2] - r[3] - fee(r[3]) for r in sub])
        tr = [r for r in sub if r[1] < mid]; te = [r for r in sub if r[1] >= mid]
        ourtr = 100 * mean([r[2] - r[3] - fee(r[3]) for r in tr]) if tr else float("nan")
        ourte = 100 * mean([r[2] - r[3] - fee(r[3]) for r in te]) if te else float("nan")
        print(f"  {name:>12}{m:>7}{wr:>7.0f}%{tp:>9.3f}{oa:>9.3f}{100*(oa-tp):>+8.1f}"
              f"{their:>+9.2f}{our:>+9.2f}{ourtr:>+9.2f}{ourte:>+9.2f}")

    print("\n" + "=" * 96)
    print("  MIRROR DE GANADORES — seguirlos al ask y aguantar a resolución (neto fee)  ·  PnL en pp")
    print("=" * 96)
    print(f"  {'wallet':>12}{'n':>7}{'gana%':>7}{'su_prec':>9}{'ntro_ask':>9}{'slip':>8}{'su_EV':>9}{'ntro_EV':>9}{'EV_tr':>9}{'EV_te':>9}")
    for wal in sorted(set(r[0] for r in recs)):
        block(wal, [r for r in recs if r[0] == wal])
    print("  " + "-" * 94)
    block("TODOS", recs)
    print("\nLECTURA: 'su_EV' + confirma que ganan. 'slip'=cuánto pagamos de más por seguirlos tarde. Si 'ntro_EV'")
    print("es + y ESTABLE (EV_tr y EV_te ambos +) → su dirección sobrevive el spread → edge transferible. Si")
    print("'su_EV'+ pero 'ntro_EV'− → su ventaja era 100% de fill de maker, no replicable. Mira wallet a wallet.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
