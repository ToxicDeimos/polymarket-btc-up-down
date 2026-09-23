"""
takeredge.py — QUÉ MIRAN LOS QUE CRUZAN Y GANAN. persist.py encontró por fin algo sólido:
  · PERSISTENCIA real y monótona — mejor cuartil de la 1ª mitad → z +0,50 en la 2ª (3,3 errores típicos
    sobre cero), peor cuartil → −0,08. Un z de +0,50 sobre 150 posiciones ≈ 2pp de ventaja por posición.
  · Y son TAKERS: 69% de sus entradas cruzan el spread. Caso claro: 0x0c7c520440 con z +3,26 en la 1ª mitad
    y +3,20 en la 2ª sobre 9.338 operaciones.
Eso contradice la investigación externa (ganadores = makers) y nuestra propia ley de eficiencia al taker,
confirmada 20 veces. Hay alguien que cruza, paga comisión y gana de forma sostenida ⇒ hay INFORMACIÓN
pública que no hemos sabido mirar, y eso sí es replicable desde la Pi.

 0) ROBUSTEZ DE LA CLASIFICACIÓN — clasificamos comparando su precio contra el libro, y un libro VIEJO empuja
    sistemáticamente hacia "taker" (si el precio subió, su compra queda por encima del ask antiguo). Se
    rehace exigiendo libro fresco y se comparan los dos números. Si el 69% se derrumba, no hay caso.
 1) QUIÉNES — wallets con z>0 en LAS DOS mitades y mayoría taker: los persistentes de verdad.
 2) QUÉ MIRAN — sus entradas frente a un CONTROL EMPAREJADO: otro instante de la MISMA ventana con el MISMO
    token a un precio parecido (±3¢). Emparejar por precio y ventana es obligatorio: sin eso, cualquier
    diferencia sería "compran favoritos", que ya sabemos. Rasgos (con el desfase de 3 s corregido):
    momento de la ventana · margen TWAP (regla D3 real) · movimiento de BTC a 15/30/60 s · spread ·
    desequilibrio del libro · distancia entre el precio de mercado y la probabilidad que implica el TWAP.
 3) FUERA DE MUESTRA — cada rasgo se mide por separado en la 1ª y la 2ª mitad. Solo vale lo que aparece en
    las dos.

    cd ~/polymarket-btc-up-down/research && python3 takeredge.py
"""
import csv, os, sys, glob, math, bisect, random
from array import array

DIR = os.path.join(os.path.dirname(__file__), "lab")
OFFSET = 3
T_TWAP = 1786060800
T_5M60 = 1786665600
FRESH = 4          # s: libro "fresco" para clasificar maker/taker
MINP = 150
PXTOL = 0.03       # emparejado por precio


def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def sd(xs):
    if len(xs) < 2: return 0.0
    m = mean(xs); return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
def regime_L(v, ws):
    if ws < T_TWAP: return 60
    return 30 if (v == "5m" and ws < T_5M60) else 60


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort()
    return array("i", [t for t, _ in out]), array("f", [p for _, p in out])


def main():
    rnd = random.Random(11)
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]

    # ---------- posiciones por wallet y mitad (igual que persist) ----------
    POS = {}; seen = set()
    for path in sorted(glob.glob(os.path.join(DIR, "tape_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                slug = r.get("slug") or ""
                if not slug.startswith("btc-updown-"): continue
                oc = r.get("outcome"); wal = r.get("proxy")
                if oc not in ("Up", "Down") or not wal: continue
                k = (r.get("tx", ""), oc, r.get("price"), r.get("trade_side"), r.get("ts_trade"))
                if k in seen: continue
                seen.add(k)
                try:
                    px = float(r["price"]); sz = float(r["size"] or 0); ws = int(slug.split("-")[-1])
                except Exception: continue
                if sz <= 0: continue
                buy = (r.get("trade_side") or "").upper().startswith("B")
                a = POS.setdefault((wal, r.get("cid"), oc), [0.0, 0.0, 0.0, 0.0, ws])
                a[0] += sz if buy else -sz
                a[1] += (-px * sz) if buy else (px * sz)
                a[2] += px * sz; a[3] += sz
    wss = sorted(set(v[4] for v in POS.values()))
    cut = wss[len(wss) // 2]
    A = {}
    for (wal, cid, oc), (sh, cash, pxsz, szs, ws) in POS.items():
        win = reso.get(cid)
        if win not in ("Up", "Down") or abs(sh) < 1e-6: continue
        p = min(max(pxsz / szs if szs > 0 else .5, 1e-4), 1 - 1e-4)
        pnl = cash + sh * (1.0 if win == oc else 0.0)
        h = 0 if ws < cut else 1
        a = A.setdefault(wal, [[0.0, 0.0, 0], [0.0, 0.0, 0]])
        a[h][0] += pnl - (cash + sh * p); a[h][1] += (sh ** 2) * p * (1 - p); a[h][2] += 1
    def z(h): return h[0] / math.sqrt(h[1]) if h[1] > 1e-9 else None
    cand = {}
    for wal, (h1, h2) in A.items():
        if h1[2] >= MINP and h2[2] >= MINP:
            z1, z2 = z(h1), z(h2)
            if z1 is not None and z2 is not None: cand[wal] = (z1, z2)
    print(f"wallets con ≥{MINP} posiciones en las dos mitades: {len(cand)}", flush=True)
    good = {w: v for w, v in cand.items() if v[0] > 0 and v[1] > 0}
    print(f"de ellas, con z>0 en LAS DOS: {len(good)}", flush=True)
    if not good: print("nada que caracterizar"); return

    need_cid = set(cid for (wal, cid, _) in POS if wal in good)
    print(f"cargando libro de {len(need_cid)} ventanas…", flush=True)
    BK = {}; SLUG = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 12 or row[2] not in need_cid or row[3] not in ("Up", "Down"): continue
                try:
                    ts = int(row[0]); b = float(row[4]); bs = float(row[5] or 0)
                    a = float(row[10]); asz = float(row[11] or 0)
                except Exception: continue
                if not (0 < b < a < 1): continue
                BK.setdefault((row[2], row[3]), []).append((ts, b, bs, a, asz))
                SLUG[row[2]] = row[1]
    for k in BK: BK[k].sort()
    print(f"series de libro: {len(BK)}", flush=True)
    sts, spx = load_spot()
    print(f"ticks spot: {len(sts)}", flush=True)

    def le(t, tol=12):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= tol) else None
    def avg(a, b):
        lo = bisect.bisect_left(sts, a); hi = bisect.bisect_right(sts, b)
        return (sum(spx[lo:hi]) / (hi - lo)) if hi - lo >= 2 else None

    # ---------- 0) robustez de la clasificación ----------
    CLS = {"todo": {"taker": 0, "maker": 0, "entre": 0}, "fresco": {"taker": 0, "maker": 0, "entre": 0}}
    ENT = []          # entradas TAKER de las buenas
    for path in sorted(glob.glob(os.path.join(DIR, "tape_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                wal = r.get("proxy")
                if wal not in good: continue
                oc = r.get("outcome"); cid = r.get("cid")
                rows = BK.get((cid, oc))
                if not rows or oc not in ("Up", "Down"): continue
                try: ts = int(float(r["ts_trade"])) - OFFSET; px = float(r["price"]); sz = float(r["size"] or 0)
                except Exception: continue
                i = bisect.bisect_right(rows, (ts, 9, 9, 9, 9)) - 1
                if i < 0: continue
                age = ts - rows[i][0]
                if age > 12: continue
                _, b, bs, a, asz = rows[i]
                buy = (r.get("trade_side") or "").upper().startswith("B")
                if buy: lab = "taker" if px >= a - 1e-9 else ("maker" if px <= b + 1e-9 else "entre")
                else:   lab = "taker" if px <= b + 1e-9 else ("maker" if px >= a - 1e-9 else "entre")
                CLS["todo"][lab] += 1
                if age <= FRESH:
                    CLS["fresco"][lab] += 1
                    if lab == "taker" and buy:
                        ENT.append({"wal": wal, "cid": cid, "oc": oc, "ts": ts, "px": px, "sz": sz,
                                    "b": b, "a": a, "bs": bs, "asz": asz})
    print("\n" + "=" * 92)
    print("  0) ROBUSTEZ: ¿siguen siendo takers con libro FRESCO?")
    print("=" * 92)
    print(f"  {'muestra':>18}{'n':>10}{'taker%':>9}{'maker%':>9}{'entre%':>9}")
    for k, c in CLS.items():
        n = sum(c.values())
        if n: print(f"  {('libro ≤12s' if k=='todo' else f'libro ≤{FRESH}s'):>18}{n:>10}"
                    f"{100*c['taker']/n:>8.0f}%{100*c['maker']/n:>8.0f}%{100*c['entre']/n:>8.0f}%")
    print(f"  entradas taker de COMPRA con libro fresco: {len(ENT)}")
    if len(ENT) < 300:
        print("  muestra corta para caracterizar — subir FRESH o acumular más cinta"); return

    # ---------- 2) rasgos: entrada vs control emparejado ----------
    def feats(cid, oc, t, px, b, a, bs, asz):
        slug = SLUG.get(cid, "")
        if not slug: return None
        v = "5m" if "-5m-" in slug else "15m"
        ws = int(slug.split("-")[-1]); wlen = 300 if v == "5m" else 900
        if not (ws < t < ws + wlen - 5): return None
        L = regime_L(v, ws)
        cur = le(t); ref = avg(ws - L, ws)
        if cur is None or ref is None: return None
        m = cur - ref
        if oc == "Down": m = -m                      # margen A FAVOR del token comprado
        d15 = (cur - le(t - 15)) if le(t - 15) is not None else None
        d60 = (cur - le(t - 60)) if le(t - 60) is not None else None
        if oc == "Down":
            d15 = -d15 if d15 is not None else None
            d60 = -d60 if d60 is not None else None
        tau = max((ws + wlen - t) - 2 * L / 3.0, 1.0)
        imb = (bs / (bs + asz)) if (bs + asz) > 0 else None
        return {"v": v, "ws": ws, "frac": (t - ws) / wlen, "margen": m, "d15": d15, "d60": d60,
                "spread": a - b, "imb": imb, "tau": tau, "px": px}

    F = []
    for e in ENT:
        f = feats(e["cid"], e["oc"], e["ts"], e["px"], e["b"], e["a"], e["bs"], e["asz"])
        if f is None: continue
        rows = BK[(e["cid"], e["oc"])]
        pool = [r for r in rows if abs(r[3] - e["px"]) <= PXTOL and abs(r[0] - e["ts"]) > 20]
        if not pool: continue
        c = rnd.choice(pool)
        # BK guarda (ts, bid, bid_size, ask, ask_size); feats espera (t, px, bid, ask, bid_size, ask_size).
        # La primera versión pasaba c[2] (TAMAÑO del bid) donde va el PRECIO del ask → spread de 31.769¢.
        g = feats(e["cid"], e["oc"], c[0], c[3], c[1], c[3], c[2], c[4])
        if g is None: continue
        F.append((f, g))
    print(f"  entradas emparejadas con control: {len(F)}")
    if len(F) < 200:
        print("  pocas parejas — bajar PXTOL o acumular más datos"); return

    print("\n" + "=" * 92)
    print("  2) QUÉ MIRAN: su entrada frente a otro instante de la MISMA ventana al MISMO precio (±3¢)")
    print("=" * 92)
    names = [("momento de la ventana", "frac"), ("margen TWAP a favor ($)", "margen"),
             ("BTC 15 s a favor ($)", "d15"), ("BTC 60 s a favor ($)", "d60"),
             ("spread (¢)", "spread"), ("desequilibrio del libro", "imb")]
    for half, lab in ((0, "1ª mitad"), (1, "2ª mitad")):
        sub = [(f, g) for f, g in F if (0 if f["ws"] < cut else 1) == half]
        if len(sub) < 80: continue
        print(f"\n  ── {lab} (n {len(sub)}) ──")
        print(f"  {'rasgo':>26}{'entrada':>12}{'control':>12}{'dif':>10}{'z':>8}")
        for nm, k in names:
            d = [(f[k] - g[k]) for f, g in sub if f.get(k) is not None and g.get(k) is not None]
            if len(d) < 50: continue
            s = sd(d) or 1e-12
            mul = 100 if k == "spread" else 1
            print(f"  {nm:>26}{mul*mean([f[k] for f,g in sub if f.get(k) is not None]):>12.3f}"
                  f"{mul*mean([g[k] for f,g in sub if g.get(k) is not None]):>12.3f}"
                  f"{mul*mean(d):>10.3f}{mean(d)/(s/math.sqrt(len(d))):>+8.2f}")
    print("\nLECTURA: solo cuenta el rasgo cuyo z sale con el MISMO signo y tamaño parecido en las DOS mitades.")
    print("El control está emparejado por ventana y por precio, así que 'compran favoritos' ya está descontado:")
    print("lo que salga es lo que miran ADEMÁS del precio. Si nada se separa, su edge no está en el instante de")
    print("entrada sino en otra parte (tamaño, qué ventanas eligen, o información que no tenemos).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
