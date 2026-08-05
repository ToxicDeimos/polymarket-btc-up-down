"""
margin_marketwide.py — ¿trend-strength (margin) predice el win en el UNIVERSO ENTERO de ventanas,
o era survivorship de las wallets ganadoras?

El minado sobre fills de ganadores dio: dentro de "favorito 52-82¢ alineado px>EMA9", el margin (cuánto
supera px a la EMA9) sube el win de ~76% (débil) a ~98% (fuerte), con ask PLANO. Pero eso está confundido
con la selección de los ganadores. Aquí lo probamos SIN esa contaminación: usamos el libro de TODAS las
ventanas que grabó el lab-collector (books_*.csv), no solo las que operaron los ganadores.

Método (procesa books día a día, RAM acotada):
  1) por cada ventana 5m, el mejor ask de Up y Down en el snapshot más cercano a 240s → favorito = mayor
     ask; filtra favorito en 52-82¢.
  2) px, EMA9, margin (bps, signo a favor del favorito), mom_open desde klines 1m de Binance (caché).
  3) resultado por el flag `winner` del CLOB (Chainlink real, caché).
  4) DENTRO de alineado (margin>0): win% por quintil de margin/mom_open, train/test. Si sube 76→98% también
     aquí = edge mecánico REAL y replicable. Si sale plano = era survivorship.

    cd ~/polymarket-btc-up-down/research && python3 margin_marketwide.py
"""
import csv, os, sys, glob, bisect, time, json, statistics, urllib.request, datetime as dt

DIR   = os.path.join(os.path.dirname(__file__), "lab")
KL    = os.path.join(DIR, "klines_mw.csv")
RESO  = os.path.join(DIR, "clob_reso_mw.csv")
LO, HI = 0.52, 0.82
ENTRY = 240
TOL   = 75            # s de tolerancia al buscar el snapshot de libro cercano a 240s


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mw/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(0.6 + i)


def fee(p): return 0.07 * p * (1 - p)


# ── 1) favorito + ask a ~240s por ventana, desde books (día a día) ────────────
def extract_windows():
    best = {}   # slug -> {"cid","ws","Up":(gap,ask),"Down":(gap,ask)}
    files = sorted(glob.glob(os.path.join(DIR, "books_*.csv")))
    print(f"leyendo {len(files)} ficheros de books…")
    for path in files:
        with open(path, encoding="utf-8") as f:
            rd = csv.reader(f); next(rd, None)
            for row in rd:
                if len(row) < 11: continue
                slug = row[1]
                if not slug.startswith("btc-updown-5m-"): continue
                a1 = row[10]
                if not a1: continue
                try:
                    ts = int(row[0]); ws = int(slug.split("-")[-1]); ask = float(a1)
                except Exception:
                    continue
                gap = abs(ts - (ws + ENTRY))
                if gap > TOL: continue
                side = row[3]
                e = best.setdefault(slug, {"cid": row[2], "ws": ws})
                cur = e.get(side)
                if cur is None or gap < cur[0]:
                    e[side] = (gap, ask)
    # ventanas con ambos lados y favorito en zona
    wins = []
    for slug, e in best.items():
        if "Up" not in e or "Down" not in e: continue
        au = e["Up"][1]; ad = e["Down"][1]
        fav = "Up" if au > ad else "Down"; ask = max(au, ad)
        if not (LO <= ask <= HI): continue
        wins.append({"slug": slug, "cid": e["cid"], "ws": e["ws"], "fav": fav, "ask": ask})
    print(f"ventanas 5m con favorito 52-82¢ a ~240s: {len(wins)}  (universo, sin filtro de wallet)")
    return wins


# ── 2) klines 1m (caché, se extiende) ─────────────────────────────────────────
def load_klines(need_lo, need_hi):
    cache = {}
    if os.path.exists(KL):
        for ln in open(KL, encoding="utf-8"):
            a = ln.split(",")
            try: cache[int(a[0])] = float(a[1])
            except Exception: pass
    want = set(range((need_lo // 60) * 60, (need_hi // 60) * 60 + 60, 60))
    missing = sorted(want - set(cache))
    if missing:
        print(f"klines: faltan {len(missing)} velas, fetcheando…")
        t = missing[0]
        while t <= missing[-1]:
            d = get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m"
                    f"&startTime={t*1000}&limit=1000")
            if not d: break
            for c in d: cache[int(c[0] // 1000)] = float(c[4])
            t = int(d[-1][0] // 1000) + 60
            time.sleep(0.15)
        with open(KL, "w", newline="", encoding="utf-8") as fo:
            for k in sorted(cache): fo.write(f"{k},{cache[k]}\n")
    print(f"klines 1m: {len(cache)}")
    return cache


# ── 3) resolución CLOB (caché, resumible) ─────────────────────────────────────
def resolve_all(cids):
    cache = {}
    if os.path.exists(RESO):
        for r in csv.DictReader(open(RESO, encoding="utf-8")): cache[r["cid"]] = r["winner"]
    todo = [c for c in cids if c not in cache or cache[c] == ""]
    if todo:
        print(f"resolviendo {len(todo)} ventanas por CLOB… [{len(cache)} en caché]")
        first = not os.path.exists(RESO)
        f = open(RESO, "a", newline="", encoding="utf-8"); w = csv.writer(f)
        if first: w.writerow(["cid", "winner"])
        for i, c in enumerate(todo, 1):
            d = get(f"https://clob.polymarket.com/markets/{c}")
            win = ""
            if isinstance(d, dict):
                for t in d.get("tokens", []):
                    if t.get("winner") is True: win = t.get("outcome"); break
            cache[c] = win; w.writerow([c, win])
            if i % 200 == 0: f.flush(); print(f"   … {i}/{len(todo)}")
            time.sleep(0.12)
        f.close()
    return cache


def main():
    wins = extract_windows()
    if len(wins) < 300:
        print("pocas ventanas"); return
    ws_all = [w["ws"] for w in wins]
    kl = load_klines(min(ws_all) - 3600, max(ws_all) + 600)
    reso = resolve_all(sorted(set(w["cid"] for w in wins)))

    mins = sorted(kl)
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
    for w in wins:
        win = reso.get(w["cid"], "")
        if win not in ("Up", "Down"): continue
        ws = w["ws"]
        # SIN look-ahead: precio a tiempo T = close de la vela que TERMINA en T = kl[T-60]. Klines keyed por
        # openTime → kl[T]=close de [T,T+60)=precio a T+60=FUTURO. Bug corregido 2026-08-05 (e=ws+240→ws+180).
        e = ws + ENTRY - 60
        px = at(kl, e); e9 = at(E9, e); opn = at(kl, ws - 60)
        if None in (px, e9, opn): continue
        fav = w["fav"]; sgn = 1 if fav == "Up" else -1
        margin = sgn * (px - e9) / e9 * 1e4
        mom = sgn * (px - opn) / opn * 1e4
        won = 1 if fav == win else 0
        rows.append({"won": won, "ask": w["ask"], "ws": ws, "margin": margin, "mom": mom})

    n = len(rows)
    print(f"\nventanas resueltas con px/EMA: {n}")
    al = [r for r in rows if r["margin"] > 0]      # ALINEADO (px del lado del favorito)
    print(f"favorito ALINEADO (px>EMA9): {len(al)}  ({len(al)/n*100:.0f}%)  ·  "
          f"win base alineado {sum(r['won'] for r in al)/len(al)*100:.1f}%  "
          f"(sin look-ahead; el 90% de antes era el bug)\n")
    if len(al) < 200:
        print("pocos alineados"); return

    tss = sorted(r["ws"] for r in al); mid = tss[len(tss) // 2]
    def wr(rs): return sum(x["won"] for x in rs) / len(rs) * 100 if rs else float("nan")
    def net(rs): return sum(x["won"] - x["ask"] - fee(x["ask"]) for x in rs) / len(rs) * 100 if rs else float("nan")

    def quint(feat):
        vals = sorted(r[feat] for r in al); L = len(al)
        qs = [vals[int(L * q)] for q in (0.2, 0.4, 0.6, 0.8)]
        buck = lambda v: sum(1 for q in qs if v >= q)
        print(f"── {feat} (Q0 débil … Q4 fuerte; cortes {[round(q,2) for q in qs]}) ──")
        for b in range(5):
            seg = [r for r in al if buck(r[feat]) == b]
            ask = statistics.mean(r["ask"] for r in seg) * 100 if seg else float("nan")
            nt = net([r for r in seg if r["ws"] < mid]); ne = net([r for r in seg if r["ws"] >= mid])
            print(f"   Q{b}: n={len(seg):>4}  win {wr(seg):>5.1f}%  ask {ask:>4.0f}%  "
                  f"NETO {net(seg):>+6.1f}pp  (tr {nt:>+6.1f}/te {ne:>+6.1f})")
        hi = [r for r in al if buck(r[feat]) == 4]; lo = [r for r in al if buck(r[feat]) == 0]
        g_tr = wr([r for r in hi if r["ws"] < mid]) - wr([r for r in lo if r["ws"] < mid])
        g_te = wr([r for r in hi if r["ws"] >= mid]) - wr([r for r in lo if r["ws"] >= mid])
        v = "✓ GENERALIZA (edge REAL, no survivorship)" if (g_tr > 5 and g_te > 5) else \
            ("~ solo train" if g_tr > 5 else "✗ PLANO → era survivorship")
        print(f"   gap Q4−Q0:  train {g_tr:+.1f}pp / test {g_te:+.1f}pp  → {v}\n")

    for feat in ("margin", "mom"):
        quint(feat)

    # ── LO QUE COMPRA EL BOT: margin≥1.5 (strong), desglose por BANDA (sin look-ahead) ──
    strong = [r for r in al if r["margin"] >= 1.5]
    print(f"=== STRONG (margin≥1.5 = lo que compra el bot): {len(strong)} fills, win {wr(strong):.1f}%, "
          f"NETO {net(strong):+.2f}pp — por BANDA (survivorship-free, SIN look-ahead) ===")
    for blo, bhi, lab in [(0.52, 0.62, "52-62"), (0.62, 0.72, "62-72"), (0.72, 0.821, "72-82")]:
        seg = [r for r in strong if blo <= r["ask"] < bhi]
        if seg:
            nt = net([r for r in seg if r["ws"] < mid]); ne = net([r for r in seg if r["ws"] >= mid])
            print(f"   {lab}¢: n={len(seg):>4}  win {wr(seg):>5.1f}%  NETO {net(seg):>+6.1f}pp  (tr {nt:>+6.1f}/te {ne:>+6.1f})")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
