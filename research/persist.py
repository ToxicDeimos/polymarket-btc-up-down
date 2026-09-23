"""
persist.py — ¿HABILIDAD O SUPERVIVENCIA? whowins (con el z ya calibrado: media +0,04, desviación 0,99) dejó
esto: en el corte de ≥50 posiciones no hay nada (observadas ≈ azar, cola izquierda 77 ≈ derecha 87), pero
entre las 37 wallets con ≥2.000 posiciones hay 7 por encima de z=+2 (tocaban 0,8) y CERO por debajo de −2.

Esa asimetría parece habilidad, pero el corte alto es EXACTAMENTE el de la supervivencia: una wallet solo
llega a 2.000 posiciones si lleva semanas operando, y la que perdía paró. "Cero por debajo de −2" puede
significar que los perdedores ya no están en la muestra, no que no existan. Al subir el mínimo para ganar
potencia me colé por la puerta de atrás.

 A) PERSISTENCIA (la prueba que lo separa) — partir el tiempo por la mitad y medir cada wallet en las dos.
    Todas las que sobrevivieron a la primera mitad están en la segunda, ganen o pierdan. Si los buenos de la
    primera siguen siendo buenos en la segunda, la habilidad es REAL y reproducible. Si su z de la segunda
    mitad es ~0, era suerte más supervivencia. Se reporta también el z medio de la segunda mitad de TODAS
    las que llegan, que es la línea base libre de supervivencia.

 B) ¿MAKER O TAKER? — con el desfase de 3 s corregido ([[polymarket-desfase-3s-cinta-libro]]), comparar su
    precio contra el libro: comprar al ASK = cruzaron (taker); comprar al BID = su orden estaba puesta
    (maker). Lo mismo al revés vendiendo. Decide si lo que hacen es copiable desde una Pi: si son takers,
    su edge es información y se puede replicar; si son makers, es cola y ya sabemos que exige <10 ms.

    cd ~/polymarket-btc-up-down/research && python3 persist.py
"""
import csv, os, sys, glob, math, bisect
from array import array

DIR = os.path.join(os.path.dirname(__file__), "lab")
OFFSET = 3          # la cinta va ~3 s por delante del reloj del libro
MINP = 150          # posiciones mínimas EN CADA MITAD
TOPN = 25           # wallets que se clasifican maker/taker


def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def cdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def main():
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]
    print(f"resoluciones: {len(reso)}", flush=True)

    POS = {}          # (wallet, cid, outcome) -> [sh, cash, Σp·sz, Σsz, ws]
    seen = set()
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
                    px = float(r["price"]); sz = float(r["size"] or 0)
                    ws = int(slug.split("-")[-1])
                except Exception: continue
                if sz <= 0: continue
                buy = (r.get("trade_side") or "").upper().startswith("B")
                a = POS.setdefault((wal, r.get("cid"), oc), [0.0, 0.0, 0.0, 0.0, ws])
                a[0] += sz if buy else -sz
                a[1] += (-px * sz) if buy else (px * sz)
                a[2] += px * sz; a[3] += sz
    print(f"posiciones: {len(POS)}", flush=True)
    wss = sorted(set(v[4] for v in POS.values()))
    if not wss: print("sin datos"); return
    cut = wss[len(wss) // 2]
    print(f"corte temporal: {cut}  ({len(wss)} ventanas; mitad 1 < corte ≤ mitad 2)", flush=True)

    # acumuladores por wallet y mitad: [exceso, varianza, n posiciones con riesgo]
    A = {}
    for (wal, cid, oc), (sh, cash, pxsz, szs, ws) in POS.items():
        win = reso.get(cid)
        if win not in ("Up", "Down") or abs(sh) < 1e-6: continue
        p = min(max(pxsz / szs if szs > 0 else .5, 1e-4), 1 - 1e-4)
        pnl = cash + sh * (1.0 if win == oc else 0.0)
        h = 0 if ws < cut else 1
        a = A.setdefault(wal, [[0.0, 0.0, 0], [0.0, 0.0, 0]])
        a[h][0] += pnl - (cash + sh * p)
        a[h][1] += (sh ** 2) * p * (1 - p)
        a[h][2] += 1

    def z(h): return h[0] / math.sqrt(h[1]) if h[1] > 1e-9 else None

    cand = []
    for wal, (h1, h2) in A.items():
        if h1[2] >= MINP and h2[2] >= MINP:
            z1, z2 = z(h1), z(h2)
            if z1 is not None and z2 is not None: cand.append((wal, z1, z2, h1[2], h2[2]))
    print(f"wallets con ≥{MINP} posiciones en LAS DOS mitades: {len(cand)}", flush=True)
    if len(cand) < 15:
        print("muestra insuficiente para la prueba de persistencia — bajar MINP o acumular más datos"); return

    z1s = [c[1] for c in cand]; z2s = [c[2] for c in cand]
    print("\n" + "=" * 100)
    print("  A) PERSISTENCIA: lo que hicieron en la PRIMERA mitad frente a lo que hicieron en la SEGUNDA")
    print("=" * 100)
    print(f"  línea base libre de supervivencia — z medio de la 2ª mitad de TODAS: {mean(z2s):+.3f}")
    print(f"  (si la habilidad no existe esto es 0; ojo: estas wallets sobrevivieron a la 1ª mitad)")
    m1 = mean(z1s); m2 = mean(z2s)
    sd1 = math.sqrt(mean([(x - m1) ** 2 for x in z1s])) or 1e-12
    sd2 = math.sqrt(mean([(x - m2) ** 2 for x in z2s])) or 1e-12
    rho = mean([(a - m1) * (b - m2) for a, b in zip(z1s, z2s)]) / (sd1 * sd2)
    print(f"  correlación entre el z de la 1ª mitad y el de la 2ª: {rho:+.3f}  "
          f"(0 = la primera mitad no predice nada)")

    order = sorted(cand, key=lambda c: -c[1])
    q = max(3, len(order) // 4)
    print(f"\n  {'grupo por la 1ª mitad':>26}{'n':>6}{'z medio 1ª':>13}{'z medio 2ª':>13}"
          f"{'2ª > 0':>9}{'2ª > +2':>10}")
    for nm, g in (("mejor cuartil", order[:q]), ("cuartil 2", order[q:2*q]),
                  ("cuartil 3", order[2*q:3*q]), ("peor cuartil", order[3*q:])):
        if not g: continue
        print(f"  {nm:>26}{len(g):>6}{mean([c[1] for c in g]):>+13.2f}{mean([c[2] for c in g]):>+13.2f}"
              f"{100*mean([1 if c[2] > 0 else 0 for c in g]):>8.0f}%"
              f"{100*mean([1 if c[2] > 2 else 0 for c in g]):>9.0f}%")
    hot = [c for c in cand if c[1] > 2.0]
    if hot:
        print(f"\n  las {len(hot)} que superaron z=+2 en la 1ª mitad → z medio en la 2ª: "
              f"{mean([c[2] for c in hot]):+.2f} · siguen por encima de +2: "
              f"{sum(1 for c in hot if c[2] > 2)}/{len(hot)}")
    print("  → si el mejor cuartil repite z claramente positivo en la 2ª mitad, la habilidad es REAL.")
    print("    Si su z de la 2ª ronda 0 como el de los demás, era suerte más supervivencia.")

    # ---------------- B) maker o taker ----------------
    top = [c[0] for c in sorted(cand, key=lambda c: -(c[1] + c[2]))[:TOPN]]
    tset = set(top)
    need = set(cid for (wal, cid, _) in POS if wal in tset)
    print(f"\n  cargando libro para {len(need)} ventanas de las {len(top)} mejores…", flush=True)
    BK = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or row[2] not in need or row[3] not in ("Up", "Down"): continue
                try:
                    ts = int(row[0]); b = float(row[4]) if row[4] else None
                    a = float(row[10]) if row[10] else None
                except Exception: continue
                if a is None or b is None or not (0 < b < a < 1): continue
                BK.setdefault((row[2], row[3]), []).append((ts, b, a))
    for k in BK: BK[k].sort()
    print(f"  series de libro cargadas: {len(BK)}", flush=True)

    CL = {}
    for path in sorted(glob.glob(os.path.join(DIR, "tape_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                wal = r.get("proxy")
                if wal not in tset: continue
                oc = r.get("outcome")
                if oc not in ("Up", "Down"): continue
                rows = BK.get((r.get("cid"), oc))
                if not rows: continue
                try: ts = int(float(r["ts_trade"])) - OFFSET; px = float(r["price"])
                except Exception: continue
                i = bisect.bisect_right(rows, (ts, 9, 9)) - 1
                if i < 0 or ts - rows[i][0] > 12: continue
                _, b, a = rows[i]
                buy = (r.get("trade_side") or "").upper().startswith("B")
                c = CL.setdefault(wal, {"taker": 0, "maker": 0, "medio": 0})
                if buy:
                    if px >= a - 1e-9: c["taker"] += 1
                    elif px <= b + 1e-9: c["maker"] += 1
                    else: c["medio"] += 1
                else:
                    if px <= b + 1e-9: c["taker"] += 1
                    elif px >= a - 1e-9: c["maker"] += 1
                    else: c["medio"] += 1

    print("\n" + "=" * 100)
    print("  B) ¿MAKER O TAKER? (precio de entrada contra el libro, desfase de 3 s corregido)")
    print("=" * 100)
    print(f"  {'wallet':>14}{'z 1ª':>8}{'z 2ª':>8}{'clasificadas':>14}{'taker%':>9}{'maker%':>9}{'entre%':>9}")
    zmap = {c[0]: (c[1], c[2]) for c in cand}
    agg = {"taker": 0, "maker": 0, "medio": 0}
    for wal in top:
        c = CL.get(wal)
        if not c: continue
        n = c["taker"] + c["maker"] + c["medio"]
        if n < 30: continue
        for k in agg: agg[k] += c[k]
        z1, z2 = zmap.get(wal, (float("nan"), float("nan")))
        print(f"  {wal[:12]:>14}{z1:>+8.2f}{z2:>+8.2f}{n:>14}"
              f"{100*c['taker']/n:>8.0f}%{100*c['maker']/n:>8.0f}%{100*c['medio']/n:>8.0f}%")
    tn = sum(agg.values())
    if tn:
        print(f"  {'CONJUNTO':>14}{'':>8}{'':>8}{tn:>14}"
              f"{100*agg['taker']/tn:>8.0f}%{100*agg['maker']/tn:>8.0f}%{100*agg['medio']/tn:>8.0f}%")
    print("\n  → TAKER mayoritario: su edge es INFORMACIÓN y se puede replicar desde la Pi (y contradice la")
    print("    ley de eficiencia que llevamos 20 pruebas confirmando, así que habría que ver QUÉ miran).")
    print("    MAKER mayoritario: es COLA, y ya sabemos que exige <10 ms — no copiable con esta máquina.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
