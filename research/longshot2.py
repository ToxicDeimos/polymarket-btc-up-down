"""
longshot2.py — AUTOPSIA del primer candidato que pasa todas las puertas. longshot.py: el sesgo
favorito-longshot es REAL en las 6 franjas de los DOS mercados (sobreprecio +0,4 a +1,5pp), vender da +0,91
(5m) y +1,51 (15m) con tr/te/−top1%/semanas todo positivo y z +3,4 / +3,2, y el control de compra cae
simétrico (−1,68 / −2,32, z −6,3 / −4,9). Además coincide con la tabla de zonas de markout hecha por otro
camino (<0,30 +0,66 = vender el longshot; >0,90 −0,93 = vender el favorito: los dos lados del mismo sesgo).

Tres cosas pueden matarlo y hay que resolverlas ANTES de arriesgar nada:

 A) EJECUCIÓN — vender al ask significa ponerse en COLA y esperar a que nos levanten. La prueba honesta es
    la escalera: ask (maker, optimista) → medio → BID (cruzando, pesimista). Si aguanta al bid, el edge no
    depende de ganar ninguna cola y se puede ejecutar el primer día. Si solo vive en el ask, hace falta
    medir el relleno.
 B) PROFUNDIDAD — cuánto tamaño hay ya en nuestro nivel (queuewatch pilló 11.330 acciones en 0,01: ahí no
    vendemos nunca) y cuánto volumen llega DESPUÉS a levantarlo. Relleno realista = volumen posterior a
    nuestro precio > tamaño delante.
 C) COMISIÓN — asumimos maker sin fee y con rebate, pero la API devuelve maker_base_fee = 1000. Barremos el
    EV con cuatro supuestos, del más benigno al más duro, para ver cuál lo rompe.

 D) RIESGO Y TAMAÑO — el reparto es asimétrico (mediana +15pp, pierde el 17%, la peor −98pp). Secuencia
    cronológica real: peor racha, mayor caída acumulada, y la fracción de Kelly. El EV medio no dice nada
    sobre sobrevivir a la mala racha.
 E) ¿DÓNDE VIVE? por momento de la ventana y por franja, con una observación por ventana.

    cd ~/polymarket-btc-up-down/research && python3 longshot2.py
"""
import csv, os, sys, glob, time, math, bisect
from array import array

DIR = os.path.join(os.path.dirname(__file__), "lab")
OFFSET = 3
PB = [("0,01-0,03", .005, .03), ("0,03-0,06", .03, .06), ("0,06-0,10", .06, .10),
      ("0,10-0,15", .10, .15), ("0,15-0,22", .15, .22), ("0,22-0,30", .22, .30)]
FR = (0.25, 0.50, 0.75)

# supuestos de comisión para el VENDEDOR, de benigno a duro
FEES = [
    ("maker sin fee, con rebate", lambda p: -0.20 * 0.07 * p * (1 - p)),
    ("maker sin fee ni rebate",   lambda p: 0.0),
    ("paga la tarifa de taker",   lambda p: 0.07 * p * (1 - p)),
    ("1000 pb sobre min(p,1-p)",  lambda p: 0.10 * min(p, 1 - p)),
]


def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_books():
    """(slug, side) -> cid, ts, bid, bsz, ask, asz"""
    raw = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 12 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0])
                    b = float(row[4]) if row[4] else None; bs = float(row[5]) if row[5] else 0.0
                    a = float(row[10]) if row[10] else None; asz = float(row[11]) if row[11] else 0.0
                except Exception: continue
                if a is None or b is None or not (0 < b < a < 1): continue
                raw.setdefault((row[1], row[3]), [row[2], []])[1].append((ts, b, bs, a, asz))
    B = {}
    for k, (cid, v) in raw.items():
        v.sort()
        ts = array("i"); bd = array("f"); bz = array("f"); ak = array("f"); az = array("f")
        for t, b, bs, a, asz in v:
            ts.append(t); bd.append(b); bz.append(bs); ak.append(a); az.append(asz)
        B[k] = (cid, ts, bd, bz, ak, az)
    del raw
    return B


def load_tape():
    T = {}
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("outcome") not in ("Up", "Down"): continue
                try: ts = int(float(r["ts_trade"])) - OFFSET; pr = float(r["price"])
                except Exception: continue
                sz = 0.0
                for k in ("size", "amount", "shares", "qty"):
                    if r.get(k):
                        try: sz = float(r[k]); break
                        except Exception: pass
                sd = (r.get("trade_side") or r.get("side") or "").upper()
                T.setdefault((r["cid"], r["outcome"]), []).append((ts, pr, sz or 1.0, sd))
    for k in T: T[k].sort()
    return T


def gates(vals, wss):
    if len(vals) < 40: return None
    mid = sorted(wss)[len(wss) // 2]
    tr = [v for v, w in zip(vals, wss) if w < mid]; te = [v for v, w in zip(vals, wss) if w >= mid]
    s = sorted(vals); k = max(1, int(len(vals) * 0.01))
    byw = {}
    for v, w in zip(vals, wss): byw.setdefault(week(w), []).append(v)
    wt = [x for x in byw.values() if len(x) >= 10]
    wp = sum(1 for x in wt if mean(x) > 0)
    m = mean(vals)
    sd = math.sqrt(mean([(x - m) ** 2 for x in vals])) or 1e-12
    return (len(vals), 100 * m, 100 * mean(tr) if len(tr) >= 10 else float("nan"),
            100 * mean(te) if len(te) >= 10 else float("nan"), 100 * mean(s[:-k]),
            f"{wp}/{len(wt)}", m / (sd / math.sqrt(len(vals))))


def show(lab, g, w=26):
    if g is None: print(f"  {lab:>{w}}{'(pocos)':>9}"); return
    n, m, tr, te, t1, wk, z = g
    print(f"  {lab:>{w}}{n:>7}{m:>+9.2f}{tr:>+8.2f}{te:>+8.2f}{t1:>+9.2f}{wk:>8}{z:>+7.2f}")


def hdr(w=26):
    print(f"  {'caso':>{w}}{'n':>7}{'EV pp':>9}{'tr':>8}{'te':>8}{'-top1%':>9}{'sem+':>8}{'z':>7}")


def main():
    print("cargando libro…", flush=True); B = load_books()
    print(f"  series: {len(B)}", flush=True)
    print("cargando cinta…", flush=True); TP = load_tape()
    print(f"  series con cinta: {len(TP)}", flush=True)
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]
    print(f"  resoluciones: {len(reso)}", flush=True)

    OBS = {"5m": [], "15m": []}
    for (slug, side), (cid, tss, bids, bzs, asks, azs) in B.items():
        win = reso.get(cid)
        if win not in ("Up", "Down"): continue
        v = "5m" if "-5m-" in slug else "15m"
        ws = int(slug.split("-")[-1]); wlen = 300 if v == "5m" else 900; close = ws + wlen
        w = 1 if win == side else 0
        for f in FR:
            t = ws + int(f * wlen)
            k = bisect.bisect_right(tss, t) - 1
            if k < 0 or t - tss[k] > 15: continue
            a = asks[k]; b = bids[k]
            if a >= 0.30 or a < 0.005: continue
            # volumen posterior que levantaría nuestra oferta (compras a >= nuestro precio)
            vol = 0.0
            for ts2, pr2, sz2, sd2 in TP.get((cid, side), []):
                if t <= ts2 <= close and sd2 == "BUY" and pr2 >= a - 1e-9: vol += sz2
            OBS[v].append({"ws": ws, "f": f, "ask": a, "bid": b, "mid": (a + b) / 2,
                           "asz": azs[k], "w": w, "vol": vol})
            break

    for v in ("5m", "15m"):
        rows = OBS[v]
        if len(rows) < 200: continue
        wss = [r["ws"] for r in rows]
        print("\n" + "=" * 104)
        print(f"  {v} — A) ESCALERA DE EJECUCIÓN (vender y aguantar a resolución; maker sin fee, con rebate)")
        print("=" * 104)
        hdr()
        for nm, kk, ff in (("al ASK (maker, cola)", "ask", FEES[0][1]),
                           ("al MEDIO", "mid", FEES[0][1]),
                           ("al BID (cruzando)", "bid", lambda p: 0.07 * p * (1 - p))):
            vals = [(r[kk] - r["w"]) - ff(r[kk]) for r in rows]
            show(nm, gates(vals, wss))
        print("  (al BID se paga tarifa de taker: es el escenario pesimista y el que decide si esto se puede")
        print("   ejecutar sin ganar ninguna cola)")

        print("\n" + "=" * 104)
        print(f"  {v} — B) ¿NOS LEVANTAN LA OFERTA? tamaño delante y volumen que llega después")
        print("=" * 104)
        print(f"  {'franja':>12}{'n':>7}{'delante (med)':>15}{'volumen después (med)':>23}{'nos llenan':>12}")
        for nm, lo, hi in PB:
            s = [r for r in rows if lo <= r["ask"] < hi]
            if len(s) < 40: continue
            az = sorted(r["asz"] for r in s); vo = sorted(r["vol"] for r in s)
            fill = 100 * mean([1 if r["vol"] > r["asz"] else 0 for r in s])
            print(f"  {nm:>12}{len(s):>7}{az[len(az)//2]:>15.0f}{vo[len(vo)//2]:>23.0f}{fill:>11.0f}%")
        sel = [r for r in rows if r["vol"] > r["asz"]]
        if len(sel) >= 40:
            print("\n  solo cuando el volumen posterior supera el tamaño delante (relleno realista):")
            hdr()
            show("al ASK · relleno realista",
                 gates([(r["ask"] - r["w"]) - FEES[0][1](r["ask"]) for r in sel],
                       [r["ws"] for r in sel]))

        print("\n" + "=" * 104)
        print(f"  {v} — C) ¿QUÉ COMISIÓN LO ROMPE? (vendiendo al ask)")
        print("=" * 104)
        hdr(28)
        for nm, fn in FEES:
            show(nm, gates([(r["ask"] - r["w"]) - fn(r["ask"]) for r in rows], wss), 28)

        print("\n" + "=" * 104)
        print(f"  {v} — D) RIESGO: secuencia cronológica, 1 acción por oportunidad, vendiendo al ask")
        print("=" * 104)
        seq = sorted(rows, key=lambda r: r["ws"])
        pnl = [(r["ask"] - r["w"]) - FEES[0][1](r["ask"]) for r in seq]
        eq = 0.0; peak = 0.0; dd = 0.0; run = 0; worst_run = 0
        for x in pnl:
            eq += x; peak = max(peak, eq); dd = min(dd, eq - peak)
            run = run + 1 if x < 0 else 0; worst_run = max(worst_run, run)

        m = mean(pnl); sd = math.sqrt(mean([(x - m) ** 2 for x in pnl])) or 1e-12
        lose = mean([1 if x < 0 else 0 for x in pnl])
        avg_loss = mean([-x for x in pnl if x < 0]); avg_win = mean([x for x in pnl if x >= 0])
        kelly = (m / (avg_loss * avg_win)) if (avg_loss and avg_win) else float("nan")
        print(f"  oportunidades {len(pnl)} · ganancia total {eq:+.1f} acciones · mayor caída acumulada "
              f"{dd:+.1f}")
        print(f"  pierde el {100*lose:.0f}% · pérdida media {100*avg_loss:.1f}pp · ganancia media "
              f"{100*avg_win:.1f}pp · peor racha seguida {worst_run}")
        print(f"  EV {100*m:+.2f}pp · desviación {100*sd:.1f}pp · Sharpe por operación {m/sd:.3f} · "
              f"Kelly ≈ {100*kelly:.1f}% del capital por operación")
        print(f"  ⚠ la caída de {dd:+.1f} acciones es sobre 1 acción por operación: a 100 acciones son "
              f"{100*abs(dd):.0f} dólares de hueco.")

        print("\n" + "=" * 104)
        print(f"  {v} — E) ¿DÓNDE VIVE? por momento de la ventana y por franja (venta al ask)")
        print("=" * 104)
        hdr()
        for f in FR:
            s = [r for r in rows if abs(r["f"] - f) < 1e-9]
            show(f"momento {int(f*100)}%", gates([(r["ask"] - r["w"]) - FEES[0][1](r["ask"]) for r in s],
                                                 [r["ws"] for r in s]))
        for nm, lo, hi in PB:
            s = [r for r in rows if lo <= r["ask"] < hi]
            show(f"franja {nm}", gates([(r["ask"] - r["w"]) - FEES[0][1](r["ask"]) for r in s],
                                       [r["ws"] for r in s]))

    print("\nLECTURA: A decide si esto es ejecutable HOY — si 'al BID (cruzando)' sigue positivo en tr y te, no")
    print("dependemos de ninguna cola y se puede probar con órdenes mínimas. Si solo vive en el ask, hace falta")
    print("el relleno de B. C dice qué supuesto de comisión lo rompe: si cae con 'tarifa de taker', hay que")
    print("confirmar la tarifa REAL antes de nada. D es el que manda para el tamaño: con una caída acumulada")
    print("grande y Kelly pequeño, esto se opera con una fracción diminuta o no se opera.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
