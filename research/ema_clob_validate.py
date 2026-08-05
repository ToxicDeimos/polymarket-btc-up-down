"""
ema_clob_validate.py — barre px>EMA{N} (familia de periodos) con RESOLUCIÓN REAL de Polymarket
(CLOB/Chainlink), no con el proxy de Binance. Responde honestamente: qué EMA rinde mejor y cuánto es el
win% / NETO de verdad. La parte cara (resolver ventanas por CLOB) es independiente de la EMA → se cachea
una vez y sirve para todos los periodos.

Diferencia con el barrido de klines: aquí cada ventana se resuelve por el flag `winner` del CLOB
(clob.polymarket.com/markets/{cid}) — la MISMA fuente con la que liquida Polymarket (Chainlink) y con
la que resuelve el bot en vivo. Los fills salen de las wallets GANADORAS (Data API), zona favorito 52-82¢,
5m, BUY, entrada 240s. px y EMA9 desde klines 1m de Binance (solo para el filtro alineado/contra).

Cachés en disco (re-correr es instantáneo, y la resolución es RESUMIBLE si se corta):
  lab/winner_fills.csv         fills de las wallets
  lab/clob_resolutions.csv     cid -> winner (lo lento; se rellena incrementalmente)
  lab/klines_1m_sweep.csv      klines 1m

    cd ~/polymarket-btc-up-down/research && python3 ema_clob_validate.py
    (--refresh re-fetchea fills/klines; la resolución CLOB siempre se cachea y reanuda)
"""
import csv, os, sys, time, json, bisect, urllib.request, datetime as dt

DIR   = os.path.join(os.path.dirname(__file__), "lab")
FILLS = os.path.join(DIR, "winner_fills.csv")
RESO  = os.path.join(DIR, "clob_resolutions.csv")
KL    = os.path.join(DIR, "klines_1m_sweep.csv")
LO, HI = 0.52, 0.82
EMAS_TEST = (5, 8, 9, 13, 21, 34, 50, 100)      # familia de periodos a barrer (px>EMA{N})
REFRESH = "--refresh" in sys.argv

WALLETS = {
    "izzyaussie":  "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2",
    "13mm-wrench": "0x57f2faf2eb75fd26bce0b5baf5ee7ffaadd66356",
    "zmbabwe":     "0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7",
    "w-f3a6":      "0xf3a6ef82d0904db48c0ad8016ca62c556fee8c6c",
    "w-9a2f":      "0x9a2f9100cd8accb9bb8ab1e3e025b042c0d5c62b",
    "w-0445":      "0x04454d6a686c5909724dc6a27555875eb86ebbf9",
}


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "emaclob/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(0.6 + i)


def fee(p):
    return 0.07 * p * (1 - p)


def load_fills():
    if os.path.exists(FILLS) and not REFRESH:
        rows = list(csv.DictReader(open(FILLS, encoding="utf-8")))
        print(f"fills en caché: {len(rows)}")
        return rows
    seen, out = set(), []
    for name, addr in WALLETS.items():
        off = 0
        while True:
            d = get(f"https://data-api.polymarket.com/trades?user={addr}&limit=100&offset={off}")
            if not d:
                break
            for x in d:
                slug = x.get("slug") or ""
                if not slug.startswith("btc-updown-5m-") or x.get("side") != "BUY":
                    continue
                cid = x.get("conditionId")
                try:
                    ws = int(slug.split("-")[-1]); p = float(x["price"]); ts = int(x.get("timestamp") or 0)
                except Exception:
                    continue
                if not cid:
                    continue
                key = (addr, ts, x.get("outcome"), round(p, 4))
                if key in seen:
                    continue
                seen.add(key)
                out.append({"ws": ws, "cid": cid, "price": p, "outcome": x.get("outcome"), "ts": ts})
            if len(d) < 100 or off > 40000:
                break
            off += 100
            time.sleep(0.1)
        print(f"  {name}: acumulado {len(out)}")
    os.makedirs(DIR, exist_ok=True)
    with open(FILLS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ws", "cid", "price", "outcome", "ts"]); w.writeheader(); w.writerows(out)
    print(f"fills fetcheados: {len(out)}")
    return out


def load_reso():
    d = {}
    if os.path.exists(RESO):
        for r in csv.DictReader(open(RESO, encoding="utf-8")):
            d[r["cid"]] = r["winner"]
    return d


def resolve_all(cids):
    """Resuelve por CLOB los cids que falten. Cachea incrementalmente (resumible)."""
    cache = load_reso()
    todo = [c for c in cids if c not in cache or cache[c] == ""]
    if not todo:
        print(f"resoluciones CLOB en caché: {len(cache)} (nada que resolver)")
        return cache
    print(f"resolviendo {len(todo)} ventanas únicas por CLOB (Chainlink real)… "
          f"[{len(cache)} ya en caché]")
    new = os.path.exists(RESO)
    f = open(RESO, "a", newline="", encoding="utf-8"); w = csv.writer(f)
    if not new:
        w.writerow(["cid", "winner"])
    done = 0
    for c in todo:
        d = get(f"https://clob.polymarket.com/markets/{c}")
        win = ""
        if isinstance(d, dict):
            for t in d.get("tokens", []):
                if t.get("winner") is True:
                    win = t.get("outcome"); break
        cache[c] = win
        w.writerow([c, win]); done += 1
        if done % 200 == 0:
            f.flush(); print(f"   … {done}/{len(todo)} resueltas")
        time.sleep(0.12)
    f.close()
    n_res = sum(1 for v in cache.values() if v)
    print(f"resoluciones CLOB: {n_res} resueltas, {len(cache)-n_res} sin ganador (pendientes/no liquidadas)")
    return cache


def load_klines(need_lo, need_hi):
    cache = {}
    if os.path.exists(KL) and not REFRESH:
        for ln in open(KL, encoding="utf-8"):
            a = ln.split(",")
            try: cache[int(a[0])] = float(a[1])
            except Exception: pass
    want = set(range((need_lo // 60) * 60, (need_hi // 60) * 60 + 60, 60))
    missing = sorted(want - set(cache))
    if missing:
        print(f"klines: faltan {len(missing)} velas, fetcheando de Binance…")
        t = missing[0]
        while t <= missing[-1]:
            d = get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m"
                    f"&startTime={t*1000}&limit=1000")
            if not d:
                break
            for c in d:
                cache[int(c[0] // 1000)] = float(c[4])
            t = int(d[-1][0] // 1000) + 60
            time.sleep(0.15)
        with open(KL, "w", newline="", encoding="utf-8") as fo:
            for k in sorted(cache):
                fo.write(f"{k},{cache[k]}\n")
    print(f"klines 1m disponibles: {len(cache)}")
    return cache


def main():
    fills = load_fills()
    fills = [f for f in fills if LO <= float(f["price"]) <= HI]
    print(f"fills 52-82¢: {len(fills)}  ·  ventanas únicas: {len(set(f['cid'] for f in fills))}")
    if not fills:
        return

    reso = resolve_all(sorted(set(f["cid"] for f in fills)))

    ws_all = [int(f["ws"]) for f in fills]
    kl = load_klines(min(ws_all) - 3600, max(ws_all) + 600)
    mins = sorted(kl); cls = kl
    def ema(N):
        k = 2 / (N + 1); out = {}; prev = None
        for m in mins:
            p = cls[m]; prev = p if prev is None else p * k + prev * (1 - k); out[m] = prev
        return out
    E = {N: ema(N) for N in EMAS_TEST}
    def at(dic, m):
        i = bisect.bisect_right(mins, m) - 1
        return dic[mins[i]] if i >= 0 else None

    # filas resueltas por CLOB con px/EMA disponibles: (fav, won, price, ts, px, {N: emaN})
    rows = []
    for f in fills:
        win = reso.get(f["cid"], "")
        if win not in ("Up", "Down"):
            continue
        ws = int(f["ws"]); e = ws + 240 - 60   # -60: SIN look-ahead. Klines keyed por openTime → el precio a
        #  240s = close de la vela [ws+180,ws+240) = kl[ws+180]. Antes usábamos kl[ws+240]=cierre de ventana (bug).
        px = at(cls, e); evs = {N: at(E[N], e) for N in EMAS_TEST}
        if px is None or any(v is None for v in evs.values()):
            continue
        fav = f["outcome"]
        won = 1 if fav == win else 0
        rows.append((fav, won, float(f["price"]), int(f["ts"]), px, evs))
    print(f"fills resueltos por CLOB con px/EMA: {len(rows)}\n")
    if len(rows) < 100:
        print("pocos fills resueltos"); return

    tss = sorted(r[3] for r in rows); mid = tss[len(tss) // 2]
    def net(seg): return sum(w - p - fee(p) for _, w, p, *_ in seg) / len(seg) * 100 if seg else None
    def wl(seg):  return sum(w for _, w, *_ in seg) / len(seg) * 100 if seg else None
    def aligned(r, N): return (r[4] > r[5][N]) == (r[0] == "Up")
    d = lambda t: dt.datetime.utcfromtimestamp(t).strftime("%m-%d")

    print("RESOLUCIÓN: CLOB / Chainlink (real de Polymarket)")
    print(f"periodo: {d(tss[0])} .. {d(tss[-1])}  ·  train<{d(mid)} / test>={d(mid)}")
    print(f"win base (favorito a ciegas 52-82¢): {wl(rows):.1f}%   NETO base: {net(rows):+.2f}pp\n")

    # ── BARRIDO de periodos EMA (px>EMA{N}), resuelto CLOB, rankeado por NETO_test ──
    print(f"{'variante':<11}{'n_alin':>8}{'fill%':>7}{'win':>6}{'NETO_train':>12}{'NETO_test':>12}{'contra_te':>11}  gen")
    print("-" * 80)
    res = []
    for N in EMAS_TEST:
        al = [r for r in rows if aligned(r, N)]; ct = [r for r in rows if not aligned(r, N)]
        tr_a = [r for r in al if r[3] < mid]; te_a = [r for r in al if r[3] >= mid]
        te_c = [r for r in ct if r[3] >= mid]
        nt, ne, nc = net(tr_a), net(te_a), net(te_c)
        gen = "✓" if (nt and ne and nt > 0 and ne > 0) else "✗"
        print(f"{'px>EMA'+str(N):<11}{len(al):>8}{len(al)/len(rows)*100:>6.0f}%{wl(al):>5.0f}%"
              f"{(nt or 0):>+11.2f}pp{(ne or 0):>+11.2f}pp{(nc or 0):>+10.2f}pp{gen:>5}")
        res.append((N, ne if ne is not None else -99, nt if nt is not None else -99, len(al)))

    res.sort(key=lambda x: x[1], reverse=True)
    bestN = res[0][0]
    print("\n" + "=" * 80)
    print(f"MEJOR EMA por NETO_test (CLOB real): px>EMA{bestN}  ({res[0][1]:+.2f}pp test, {res[0][2]:+.2f}pp train)")
    al = [r for r in rows if aligned(r, bestN)]
    print(f"desglose por banda (px>EMA{bestN} alineado, CLOB):")
    for blo, bhi in [(0.52, 0.62), (0.62, 0.72), (0.72, 0.821)]:
        seg = [r for r in al if blo <= r[2] < bhi]
        if seg:
            print(f"   {int(blo*100)}-{int(bhi*100)}¢: n={len(seg):>4}  win {wl(seg):>4.1f}%  NETO {net(seg):+.2f}pp")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
