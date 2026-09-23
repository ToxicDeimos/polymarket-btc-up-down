"""
volskew.py — Dieciocho pruebas dicen "acierto ≈ precio": el mercado acierta la DIRECCIÓN. Pero el precio de
este mercado es, matemáticamente, Φ(margen / (σ·√τ)). El margen lo vemos los dos (precio actual − media de los
L s previos a la apertura, la referencia D3). La σ es el único parámetro libre = la VOLATILIDAD IMPLÍCITA que
el mercado está asumiendo. Se despeja del precio como en una opción y se compara con la realizada.

  σ_impl = margen / (√τ_ef · Φ⁻¹(precio))      τ_ef = (cierre − t) − 2L/3   [varianza de la media final]
  p_modelo = Φ(margen / (σ_real · √τ_ef))      σ_real = vol realizada de los últimos 30 min (rejilla 10 s)

TEST 1 — INFORMACIÓN: dentro de bandas ESTRECHAS de precio de mercado, ¿cambia el acierto según el modelo?
  Si dentro de la banda 0,55-0,65 el cajón bajo del modelo acierta 52% y el alto 68%, el modelo sabe algo que
  el precio no. Es la prueba decisiva y no la puede pasar el azar del precio.
TEST 2 — DINERO: comprar el lado que el modelo dice infravalorado, al ask fresco, neto de comisión, por umbral.
TEST 3 — VOLATILIDAD: por cajón de σ_impl/σ_real, EV de comprar al favorito y al no-favorito. Si el mercado
  asume DEMASIADA volatilidad, los favoritos están baratos; si asume poca, los caros son los favoritos.
  Desglose por zona de precio: en los extremos la comisión (0,07·p·(1−p)) es barata → el peaje no se lo come.

Puertas: train/test por mediana temporal, semanas positivas, EV sin el 1% mejor.

    cd ~/polymarket-btc-up-down/research && python3 volskew.py
"""
import csv, os, sys, glob, json, time, math, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
T_TWAP = 1786060800      # 2026-08-07 00:00 UTC
T_5M60 = 1786665600      # 2026-08-14 00:00 UTC
FRACS = {"5m": (0.10, 0.30, 0.50, 0.70), "15m": (0.10, 0.30, 0.50, 0.70)}
GRID = 10                # rejilla de la vol realizada, s
LOOK = 180               # 180 muestras · 10 s = 30 min
RB = [("<0,70", 0, 0.70), ("0,70-0,90", 0.70, 0.90), ("0,90-1,10", 0.90, 1.10),
      ("1,10-1,40", 1.10, 1.40), (">1,40", 1.40, 9e9)]
ZB = [("0,50-0,70", 0.50, 0.70), ("0,70-0,85", 0.70, 0.85), ("0,85-0,97", 0.85, 0.97)]
EB = [("0-2pp", 0.00, 0.02), ("2-5pp", 0.02, 0.05), ("5-10pp", 0.05, 0.10), (">10pp", 0.10, 9e9)]


def regime(v, ws):
    if ws < T_TWAP: return "puntual", 60
    if v == "5m" and ws < T_5M60: return "TWAP30", 30
    return "TWAP60", 60


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vskew/1.0"})
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
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))
def cdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def ppf(p):
    """Φ⁻¹ (Acklam)."""
    if not (0 < p < 1): return float("nan")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    pl = 0.02425
    if p < pl or p > 1 - pl:
        q = math.sqrt(-2 * math.log(p if p < pl else 1 - p))
        r = ((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]
        r /= (((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1
        return r if p < pl else -r
    q = p - 0.5; r = q * q
    return ((((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q /
            (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1))


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def load_books():
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
                if ask is None or bid is None: continue
                w = tmp.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})
                w[row[3]].append((ts, ask, bid))
    W = {}
    for slug, w in tmp.items():
        if not w["Up"] or not w["Down"]: continue
        W[slug] = {"cid": w["cid"], "ws": int(slug.split("-")[-1]), "v": "5m" if "-5m-" in slug else "15m"}
        for s in ("Up", "Down"):
            r = sorted(w[s]); W[slug][s] = ([x[0] for x in r], [x[1] for x in r], [x[2] for x in r])
    return W


def stats(rows, minn=40):
    if len(rows) < minn: return None
    def pnl(r): return r["won"] - r["ask"] - fee(r["ask"])
    def ev(s): return 100 * mean([pnl(x) for x in s])
    mid = sorted(r["ws"] for r in rows)[len(rows) // 2]
    tr = [r for r in rows if r["ws"] < mid]; te = [r for r in rows if r["ws"] >= mid]
    ps = sorted(rows, key=pnl); k = max(1, int(len(rows) * 0.01))
    byw = {}
    for r in rows: byw.setdefault(week(r["ws"]), []).append(r)
    wt = [s for s in byw.values() if len(s) >= 10]
    wp = sum(1 for s in wt if ev(s) > 0)
    return (len(rows), mean([r["ask"] for r in rows]), 100 * mean([r["won"] for r in rows]), ev(rows),
            ev(tr) if len(tr) >= 10 else float("nan"), ev(te) if len(te) >= 10 else float("nan"),
            100 * mean([pnl(x) for x in ps[:-k]]), f"{wp}/{len(wt)}")


HDR = f"  {'caso':>26}{'n':>7}{'ask':>7}{'gana%':>7}{'EV':>8}{'tr':>7}{'te':>7}{'-top1%':>8}{'sem+':>7}"


def show(lab, s):
    if s is None: print(f"  {lab:>26}{'(pocos)':>9}"); return
    n, ask, g, ev, tr, te, ev1, wk = s
    print(f"  {lab:>26}{n:>7}{ask:>7.3f}{g:>6.0f}%{ev:>+8.2f}{tr:>+7.2f}{te:>+7.2f}{ev1:>+8.2f}{wk:>7}")


def main():
    sts, spx = load_spot(); W = load_books()
    print(f"ventanas con libro: {len(W)} · ticks spot: {len(sts)}")

    # ---------- rejilla de volatilidad realizada (sumas acumuladas, O(1) por consulta) ----------
    g0 = (sts[0] // GRID) * GRID; g1 = (sts[-1] // GRID) * GRID
    ng = (g1 - g0) // GRID + 1
    gpx = [None] * ng
    for i in range(ng):
        t = g0 + i * GRID
        j = bisect.bisect_right(sts, t) - 1
        if j >= 0 and t - sts[j] <= GRID: gpx[i] = spx[j]
    S = [0.0] * (ng + 1); C = [0] * (ng + 1)
    for i in range(1, ng):
        d2 = 0.0; c = 0
        if gpx[i] is not None and gpx[i - 1] is not None:
            d2 = (gpx[i] - gpx[i - 1]) ** 2; c = 1
        S[i + 1] = S[i] + d2; C[i + 1] = C[i] + c
    print(f"rejilla vol: {ng} puntos de {GRID}s · cobertura {100*C[ng]/max(1,ng-1):.0f}%")

    def sigma(t):
        """desviación por √s, con la vol realizada de los últimos 30 min ANTES de t."""
        i = int((t - g0) // GRID)
        if i < 2: return None
        lo = max(1, i - LOOK)
        c = C[i] - C[lo]
        if c < LOOK * 0.6: return None
        v = (S[i] - S[lo]) / (c * GRID)
        return math.sqrt(v) if v > 0 else None

    def avg(a, b):
        lo = bisect.bisect_left(sts, a); hi = bisect.bisect_right(sts, b)
        if hi - lo < 2: return None
        seg = spx[lo:hi]; return sum(seg) / len(seg)

    def le(t, tol):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= tol) else None

    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]

    def resolve(cid):
        if cid in reso: return reso[cid]
        w = winner_clob(cid); time.sleep(0.1)
        if w:
            reso[cid] = w; nf = not os.path.exists(CACHE)
            with open(CACHE, "a", newline="", encoding="utf-8") as fo:
                cw = csv.writer(fo)
                if nf: cw.writerow(["cid", "winner"])
                cw.writerow([cid, w])
        return w

    # ---------- observaciones ----------
    OBS = {}
    done = 0
    for slug, w in W.items():
        v = w["v"]; ws = w["ws"]; wlen = 300 if v == "5m" else 900; close = ws + wlen
        reg, L = regime(v, ws)
        if reg == "puntual": continue
        ref = avg(ws - L, ws)
        if ref is None: continue
        done += 1
        if done % 3000 == 0: print(f"   … {done}")
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        wup = 1 if win == "Up" else 0
        for f in FRACS[v]:
            t = ws + int(f * wlen)
            cur = le(t, 12)
            if cur is None: continue
            tau = (close - t) - 2 * L / 3.0
            if tau <= 5: continue
            sg = sigma(t)
            if sg is None or sg <= 0: continue
            m = cur - ref

            def fresh(X):
                tss, asks, bids = w[X]; k = bisect.bisect_left(tss, t)
                if k < len(tss) and tss[k] <= t + 15 and tss[k] < close - 20:
                    a, b = asks[k], bids[k]
                    if 0 < b < a < 1: return a, b
                return None, None
            aU, bU = fresh("Up"); aD, bD = fresh("Down")
            if aU is None or aD is None: continue
            pm = ((bU + aU) / 2 + (1 - (bD + aD) / 2)) / 2      # precio medio de "Up", los dos lados
            if not (0.02 < pm < 0.98): continue
            pmod = cdf(m / (sg * math.sqrt(tau)))
            z = ppf(pm)
            simp = (m / (math.sqrt(tau) * z)) if (abs(z) > 1e-6 and m * z > 0) else None
            OBS.setdefault((v, reg, f), []).append(
                {"ws": ws, "pm": pm, "pmod": pmod, "sg": sg, "simp": simp, "wup": wup,
                 "aU": aU, "aD": aD})

    def side(o, up):
        """fila lista para stats(): comprar Up (up=True) o Down."""
        return {"ws": o["ws"], "ask": o["aU"] if up else o["aD"],
                "won": (o["wup"] if up else 1 - o["wup"])}

    # ================= TEST 1 — ¿SABE EL MODELO ALGO QUE EL PRECIO NO? =================
    print("\n" + "=" * 100)
    print("  TEST 1 — INFORMACIÓN: dentro de bandas estrechas de PRECIO, ¿varía el acierto con el MODELO?")
    print("=" * 100)
    for key in sorted(OBS):
        rows = OBS[key]
        if len(rows) < 400: continue
        print(f"\n  {key[0]}·{key[1]}·t+{int(key[2]*100)}%   (n {len(rows)})")
        print(f"  {'banda precio':>14}{'cajón modelo':>16}{'n':>7}{'precio':>8}{'modelo':>8}{'gana%':>8}{'dif':>8}")
        for bl, blo, bhi in (("0,45-0,55", .45, .55), ("0,55-0,70", .55, .70), ("0,70-0,85", .70, .85),
                             ("0,85-0,95", .85, .95)):
            band = [o for o in rows if blo <= o["pm"] < bhi]
            if len(band) < 120: continue
            band.sort(key=lambda o: o["pmod"])
            k = len(band) // 3
            for nm, sub in (("modelo bajo", band[:k]), ("modelo medio", band[k:2*k]), ("modelo alto", band[2*k:])):
                if len(sub) < 40: continue
                gp = 100 * mean([o["wup"] for o in sub]); pp = 100 * mean([o["pm"] for o in sub])
                print(f"  {bl:>14}{nm:>16}{len(sub):>7}{pp:>7.1f}%{100*mean([o['pmod'] for o in sub]):>7.1f}%"
                      f"{gp:>7.1f}%{gp-pp:>+8.1f}")

    # ================= TEST 2 — DINERO: comprar lo que el modelo dice barato =================
    print("\n" + "=" * 100)
    print("  TEST 2 — COMPRAR EL LADO INFRAVALORADO SEGÚN EL MODELO (ask fresco, aguantar, neto comisión)")
    print("=" * 100)
    for key in sorted(OBS):
        rows = OBS[key]
        if len(rows) < 400: continue
        print(f"\n  {key[0]}·{key[1]}·t+{int(key[2]*100)}%   (n {len(rows)})")
        print(HDR)
        for nm, lo, hi in EB:
            up = [side(o, True) for o in rows if lo <= o["pmod"] - o["pm"] < hi]
            dn = [side(o, False) for o in rows if lo <= (1 - o["pmod"]) - (1 - o["pm"]) < hi]
            show(f"modelo dice barato {nm}", stats(up + dn))
        # control: comprar el lado que el modelo dice CARO
        car = ([side(o, True) for o in rows if o["pmod"] - o["pm"] < -0.05] +
               [side(o, False) for o in rows if o["pm"] - o["pmod"] < -0.05])
        show("control: lado CARO >5pp", stats(car))

    # ================= TEST 3 — VOLATILIDAD IMPLÍCITA vs REALIZADA =================
    print("\n" + "=" * 100)
    print("  TEST 3 — σ_IMPLÍCITA / σ_REALIZADA: ¿el mercado asume demasiada o poca volatilidad?")
    print("=" * 100)
    for key in sorted(OBS):
        rows = [o for o in OBS[key] if o["simp"] is not None and abs(o["pm"] - 0.5) > 0.03]
        if len(rows) < 400: continue
        rt = [o["simp"] / o["sg"] for o in rows]
        rt.sort()
        print(f"\n  {key[0]}·{key[1]}·t+{int(key[2]*100)}%   (n {len(rows)} · ratio mediano "
              f"{rt[len(rt)//2]:.2f})")
        print(HDR)
        for nm, lo, hi in RB:
            sub = [o for o in rows if lo <= o["simp"] / o["sg"] < hi]
            fav = [side(o, o["pm"] > 0.5) for o in sub]
            und = [side(o, o["pm"] <= 0.5) for o in sub]
            show(f"σi/σr {nm} · favorito", stats(fav))
            show(f"σi/σr {nm} · no-favorito", stats(und))
        print(f"  {'— por zona de precio (favorito, solo σi/σr > 1,10) —':>60}")
        for zn, zlo, zhi in ZB:
            sub = [o for o in rows if o["simp"] / o["sg"] >= 1.10 and zlo <= max(o["pm"], 1-o["pm"]) < zhi]
            show(f"zona {zn}", stats([side(o, o["pm"] > 0.5) for o in sub]))

    print("\nLECTURA: TEST 1 es el decisivo — si dentro de una banda estrecha de precio el 'modelo alto' acierta")
    print("bastante más que el 'modelo bajo', el modelo tiene información que el precio no tiene y hay edge que")
    print("ninguna prueba direccional podía ver. Si el acierto es plano dentro de la banda, el precio ya lo sabe")
    print("todo y esta vía muere. TEST 2 lo traduce a dinero (exijo EV+ en tr Y te, control CARO negativo y")
    print("-top1% positivo). TEST 3 dice DÓNDE: si con σ implícita alta el favorito gana más de lo que cuesta,")
    print("el mercado se pasa de prudente y los favoritos están baratos — y en los extremos la comisión es barata.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
