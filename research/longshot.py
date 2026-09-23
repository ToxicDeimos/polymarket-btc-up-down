"""
longshot.py — ¿ESTÁ SESGADA LA COLA? Dos observaciones de hoy apuntan al mismo sitio y no encajan con "todo
está eficiente":
  1) queuewatch pilló a alguien con 11.330 acciones puestas en 0,01 y moviéndolas de cien en cien. Comprar
     Down a 0,01 ≡ vender Up a 0,99. Este mercado NO cobra recompensa de liquidez ⇒ tiene un motivo económico
     que no hemos identificado.
  2) en markout por zona de precio, la franja <0,30 fue la ÚNICA con resultado a resolución positivo en los
     dos mercados, antes y después de corregir el desfase. Lo dejé pasar porque mezclaba largos y cortos.

Hipótesis ESTRUCTURALMENTE distinta a los 19 intentos anteriores: no predecir ni capturar spread, sino
VENDERLE LA COLA A UN COMPRADOR SESGADO (sesgo favorito-longshot, el efecto más documentado en mercados de
predicción). Encaja con que el 84% de la cinta sean COMPRAS: la gente compra billetes de lotería, no los
vende. Y esquiva los tres muros del día: la comisión a 0,05 es 0,33pp (no 1,75), los niveles extremos viven
minutos (no 0,2 s) así que no hay carrera de cola, y no hace falta acertar la dirección.

  A) CALIBRACIÓN HONESTA EN LOS EXTREMOS: por franja fina de precio, ¿gana el token menos de lo que cuesta?
     UNA observación por (ventana, token) — la lección de check15: una ventana = una observación.
  B) EL DINERO: vender el longshot a su ask como MAKER (sin comisión de taker, con rebate) y aguantar a
     resolución. Control obligatorio: comprarlo debe salir simétricamente negativo.
  C) ¿SE PUEDE EJECUTAR? volumen que la CINTA negocia en esas franjas por ventana (con el desfase de 3 s
     corregido) y cuánto vive un ask ahí. Sin volumen no hay negocio por bonito que salga A.
  D) RIESGO: el longshot que SÍ gana cuesta 1 − precio. Reportamos la peor racha y el reparto, porque vender
     cola es ganar poco muchas veces y perder mucho de vez en cuando.

    cd ~/polymarket-btc-up-down/research && python3 longshot.py
"""
import csv, os, sys, glob, time, math, bisect
from array import array

DIR = os.path.join(os.path.dirname(__file__), "lab")
OFFSET = 3        # ts_trade va ~3 s por delante del reloj del libro (clocklag)
PB = [("0,01-0,03", .005, .03), ("0,03-0,06", .03, .06), ("0,06-0,10", .06, .10),
      ("0,10-0,15", .10, .15), ("0,15-0,22", .15, .22), ("0,22-0,30", .22, .30)]
FR = (0.25, 0.50, 0.75)      # momentos de la ventana donde se mira


def fee(p): return 0.07 * p * (1 - p)
def rebate(p): return 0.20 * fee(p)
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_books():
    raw = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); b = float(row[4]) if row[4] else None
                    a = float(row[10]) if row[10] else None
                except Exception: continue
                if a is None or b is None or not (0 < b < a < 1): continue
                raw.setdefault((row[1], row[3]), [row[2], []])[1].append((ts, b, a))
    B = {}
    for k, (cid, v) in raw.items():
        v.sort()
        ts = array("i"); bd = array("f"); ak = array("f")
        for t, b, a in v:
            ts.append(t); bd.append(b); ak.append(a)
        B[k] = (cid, ts, bd, ak)
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
                T.setdefault((r["cid"], r["outcome"]), []).append((ts, pr, sz, sd))
    for k in T: T[k].sort()
    return T


def gates(rows, key):
    """rows: dicts con ws y `key`. Devuelve (n, media, tr, te, -top1%, semanas+, z)."""
    if len(rows) < 40: return None
    v = [r[key] for r in rows]
    mid = sorted(r["ws"] for r in rows)[len(rows) // 2]
    tr = [r[key] for r in rows if r["ws"] < mid]; te = [r[key] for r in rows if r["ws"] >= mid]
    s = sorted(v); k = max(1, int(len(v) * 0.01))
    byw = {}
    for r in rows: byw.setdefault(week(r["ws"]), []).append(r[key])
    wt = [x for x in byw.values() if len(x) >= 10]
    wp = sum(1 for x in wt if mean(x) > 0)
    m = mean(v)
    sd = math.sqrt(mean([(x - m) ** 2 for x in v])) or 1e-12
    return (len(v), 100 * m, 100 * mean(tr) if len(tr) >= 10 else float("nan"),
            100 * mean(te) if len(te) >= 10 else float("nan"), 100 * mean(s[:-k]),
            f"{wp}/{len(wt)}", m / (sd / math.sqrt(len(v))))


def show(lab, g):
    if g is None: print(f"  {lab:>22}{'(pocos)':>9}"); return
    n, m, tr, te, t1, wk, z = g
    print(f"  {lab:>22}{n:>7}{m:>+9.2f}{tr:>+8.2f}{te:>+8.2f}{t1:>+9.2f}{wk:>8}{z:>+7.2f}")


HDR = f"  {'franja':>22}{'n':>7}{'EV pp':>9}{'tr':>8}{'te':>8}{'-top1%':>9}{'sem+':>8}{'z':>7}"


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
    VOL = {"5m": {}, "15m": {}}       # franja -> [volumen total, ventanas]
    nw = {"5m": set(), "15m": set()}
    for (slug, side), (cid, tss, bids, asks) in B.items():
        win = reso.get(cid)
        if win not in ("Up", "Down"): continue
        v = "5m" if "-5m-" in slug else "15m"
        ws = int(slug.split("-")[-1]); wlen = 300 if v == "5m" else 900
        nw[v].add(ws)
        w = 1 if win == side else 0
        # UNA observación por (ventana, token): el primer momento de la rejilla con ask en zona longshot
        for f in FR:
            t = ws + int(f * wlen)
            k = bisect.bisect_right(tss, t) - 1
            if k < 0 or t - tss[k] > 15: continue
            a = asks[k]; b = bids[k]
            if a >= 0.30 or a < 0.005: continue
            OBS[v].append({"ws": ws, "ask": a, "bid": b, "w": w,
                           "sell": (a - w) + rebate(a),          # vender el longshot como MAKER
                           "buy": (w - a) - fee(a)})             # comprarlo como taker (control)
            break
        # volumen de la cinta en franjas longshot, por ventana
        tp = TP.get((cid, side))
        if tp:
            for nm, lo, hi in PB:
                acc = VOL[v].setdefault(nm, [0.0, 0])
                acc[1] += 1
                for ts, pr, sz, sd in tp:
                    if lo <= pr < hi and ws <= ts <= ws + wlen: acc[0] += (sz or 1)

    for v in ("5m", "15m"):
        rows = OBS[v]
        if len(rows) < 100: continue
        print("\n" + "=" * 100)
        print(f"  {v} — CALIBRACIÓN EN LA COLA: ¿gana el longshot menos de lo que cuesta?  "
              f"(ventanas {len(nw[v])})")
        print("=" * 100)
        print(f"  {'franja':>22}{'n':>7}{'ask medio':>11}{'gana%':>9}{'dif pp':>9}")
        for nm, lo, hi in PB:
            s = [r for r in rows if lo <= r["ask"] < hi]
            if len(s) < 40: continue
            am = 100 * mean([r["ask"] for r in s]); gw = 100 * mean([r["w"] for r in s])
            print(f"  {nm:>22}{len(s):>7}{am:>11.2f}{gw:>8.2f}%{am-gw:>+9.2f}")
        print(f"\n  VENDERLO como maker (sin comisión de taker, con rebate) · aguantar a resolución")
        print(HDR)
        for nm, lo, hi in PB:
            show(nm, gates([r for r in rows if lo <= r["ask"] < hi], "sell"))
        show("TODO <0,30", gates(rows, "sell"))
        print(f"\n  control — COMPRARLO como taker (debe salir simétricamente negativo)")
        print(HDR)
        show("TODO <0,30", gates(rows, "buy"))
        # riesgo
        s = sorted(r["sell"] for r in rows)
        print(f"\n  reparto de la venta: peor {100*s[0]:+.1f}pp · p5 {100*s[int(.05*len(s))]:+.1f} · "
              f"mediana {100*s[len(s)//2]:+.1f} · media {100*mean(s):+.2f} · "
              f"pierde el {100*mean([1 if x<0 else 0 for x in s]):.0f}% de las veces")
        print(f"\n  ¿hay con quién operar? volumen de la cinta por ventana en cada franja")
        print(f"  {'franja':>22}{'shares/ventana':>18}")
        for nm, _, _ in PB:
            a = VOL[v].get(nm)
            if a and a[1]: print(f"  {nm:>22}{a[0]/a[1]:>18.1f}")

    print("\nLECTURA: en la calibración, 'dif pp' POSITIVO = el longshot cuesta más de lo que gana = sesgo")
    print("favorito-longshot REAL y vendérselo a quien lo compra es el negocio. Exijo: EV+ en tr Y te, el")
    print("control de compra simétricamente negativo, -top1% positivo (si no, vivimos de no haber tenido")
    print("todavía el día malo) y volumen suficiente por ventana. Ojo al reparto: vender cola es ganar poco")
    print("muchas veces y perder 1−precio de golpe; si el -top1% se hunde, el riesgo de ruina manda sobre el EV.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
