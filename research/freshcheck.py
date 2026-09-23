"""
freshcheck.py — markout.py dice que cotizando FRESCO (0-2 s) el market making es POSITIVO: markout +0,71/+0,73
y a resolución +0,22 (5m) / +0,36 (15m), con la selección adversa creciendo ~0,5pp por segundo de retraso.
Cuadra con la física (medio spread 0,52 + rebate 0,35 = 0,86 sin toxicidad; observamos 0,71 ⇒ adversa 0,15pp).
Es lo primero positivo del proyecto, así que hay que intentar tumbarlo ANTES de construir nada.

LA FORMA DE QUE SEA MENTIRA: las marcas de tiempo de la cinta y del libro van en SEGUNDOS ENTEROS y vienen de
sistemas distintos (el libro lo sella el colector al hacer poll; la operación la sella Polymarket). "Edad 0" =
mismo segundo, y dentro de ese segundo la operación pudo ocurrir ANTES de la foto ⇒ estaríamos vendiendo a un
precio que ya se movió por esa misma operación = look-ahead, y produciría exactamente este resultado.

DIAGNÓSTICO DECISIVO — EDADES NEGATIVAS. Repetimos el ejercicio usando fotos del libro tomadas DESPUÉS de la
operación (edad −1, −2, −3…). Esas están contaminadas por construcción, así que marcan el listón de lo que
parece un artefacto. Entonces:
   · si edad 0 se parece a edad −1 → contaminación, el hallazgo muere.
   · si edad 0 se parece a edad +1 y +2, y hay un escalón claro entre −1 y 0 → el hallazgo es REAL.
Además: desglose segundo a segundo (no por tramos), semanas positivas del tramo fresco, y la economía de
verdad (rellenos por ventana y pp por ventana) para saber si merece la pena construirlo.

    cd ~/polymarket-btc-up-down/research && python3 freshcheck.py
"""
import csv, os, sys, glob, json, time, math, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
DMIN, DMAX = -4, 10          # edad con signo: negativa = libro POSTERIOR a la operación
HOR = (5, 15)
PB = [("<0,30", 0, .30), ("0,30-0,50", .30, .50), ("0,50-0,70", .50, .70),
      ("0,70-0,90", .70, .90), (">0,90", .90, 1.0)]


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fresh/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.3)


def fee(p): return 0.07 * p * (1 - p)
def rebate(p): return 0.20 * fee(p)
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_books():
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None or bid is None or not (0 < bid < ask < 1): continue
                tmp.setdefault((row[1], row[3]), {"cid": row[2], "r": []})["r"].append((ts, bid, ask))
    B = {}
    for k, v in tmp.items():
        r = sorted(v["r"])
        B[k] = {"cid": v["cid"], "ts": [x[0] for x in r], "bid": [x[1] for x in r], "ask": [x[2] for x in r]}
    return B


def load_tape():
    T = {}; seen = set()
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("outcome") not in ("Up", "Down"): continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"), r.get("trade_side"), r.get("ts_trade"))
                if k in seen: continue
                seen.add(k)
                try: ts = int(float(r["ts_trade"])); pr = float(r["price"])
                except Exception: continue
                sd = (r.get("trade_side") or r.get("side") or "").upper()
                T.setdefault(r["cid"], []).append((ts, r["outcome"], sd, pr))
    for cid in T: T[cid].sort()
    return T


def main():
    B = load_books(); TP = load_tape()
    print(f"series de libro: {len(B)} · cids con cinta: {len(TP)}")
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]

    ROWS = []          # un registro por (operación × foto candidata)
    NTR = {}           # operaciones por ventana, para la economía
    nb = 0
    for (slug, side), bk in B.items():
        cid = bk["cid"]
        if cid not in TP: continue
        nb += 1
        if nb % 4000 == 0: print(f"   … {nb}")
        v = "5m" if "-5m-" in slug else "15m"
        ws = int(slug.split("-")[-1]); close = ws + (300 if v == "5m" else 900)
        tss, bids, asks = bk["ts"], bk["bid"], bk["ask"]
        win = reso.get(cid); res_ok = win in ("Up", "Down")

        def mid_at(t):
            k = bisect.bisect_right(tss, t) - 1
            if k < 0 or t - tss[k] > 20 or tss[k] > close - 5: return None
            return (bids[k] + asks[k]) / 2

        for ts, oc, sd, pr in TP[cid]:
            if oc != side or ts > close - 5 or sd not in ("BUY", "SELL"): continue
            NTR[(v, ws)] = NTR.get((v, ws), 0) + 1
            lo = bisect.bisect_left(tss, ts - DMAX)
            hi = bisect.bisect_right(tss, ts - DMIN)
            for k in range(lo, hi):
                d = ts - tss[k]                       # >0: libro ANTES (correcto) · <0: libro DESPUÉS
                if not (DMIN <= d <= DMAX) or tss[k] > close - 5: continue
                a, b = asks[k], bids[k]
                if sd == "BUY":
                    filled = pr >= a - 1e-9; pos, px = -1, a
                else:
                    filled = pr <= b + 1e-9; pos, px = 1, b
                rec = {"v": v, "ws": ws, "d": d, "fill": 1 if filled else 0, "px": px,
                       "sp": a - b, "mk": {}, "res": None}
                if filled:
                    for h in HOR:
                        mh = mid_at(ts + h)
                        rec["mk"][h] = None if mh is None else (pos * (mh - px) + rebate(px))
                    if res_ok:
                        w = 1 if win == side else 0
                        rec["res"] = pos * (w - px) + rebate(px)
                ROWS.append(rec)
    print(f"registros: {len(ROWS)} · rellenos: {sum(r['fill'] for r in ROWS)}")

    def cell(rs, key):
        vs = [r[key] for r in rs if r[key] is not None] if key == "res" else \
             [r["mk"][key] for r in rs if r["mk"].get(key) is not None]
        return 100 * mean(vs) if len(vs) >= 30 else None

    def fmt(x, w=9, d=2):
        return f"{x:>+{w}.{d}f}" if x is not None else f"{'—':>{w}}"

    # ============ 1) EL DIAGNÓSTICO: segundo a segundo, incluidas edades NEGATIVAS ============
    print("\n" + "=" * 104)
    print("  1) EDAD CON SIGNO, SEGUNDO A SEGUNDO · d<0 = libro tomado DESPUÉS de la operación (contaminado")
    print("     por construcción, marca el listón del artefacto) · d>0 = libro ANTES (honesto)")
    print("=" * 104)
    for v in ("5m", "15m"):
        print(f"\n  ── {v} ──")
        print(f"  {'edad d':>8}{'registros':>11}{'%relleno':>10}{'n rellenos':>12}{'precio':>8}"
              f"{'mk 5s':>9}{'mk 15s':>9}{'a resol.':>10}")
        for d in range(DMIN, DMAX + 1):
            rs = [r for r in ROWS if r["v"] == v and r["d"] == d]
            if not rs: continue
            fs = [r for r in rs if r["fill"]]
            print(f"  {d:>8}{len(rs):>11}{100*mean([r['fill'] for r in rs]):>9.0f}%{len(fs):>12}"
                  f"{(mean([r['px'] for r in fs]) if fs else float('nan')):>8.3f}"
                  f"{fmt(cell(fs,5))}{fmt(cell(fs,15))}{fmt(cell(fs,'res'),10)}")
    print("\n  → si d=0 se parece a d=−1 ⇒ CONTAMINACIÓN (muere). Si hay escalón entre d=−1 y d=0, y d=0 se")
    print("    parece a d=+1/+2 ⇒ el hallazgo de markout.py es REAL.")

    # ============ 2) SOLO HONESTOS (d≥1): ¿aguanta el tramo fresco? ============
    print("\n" + "=" * 104)
    print("  2) SOLO LIBRO ANTERIOR A LA OPERACIÓN (d≥1) — el tramo fresco, por semanas y por precio")
    print("=" * 104)
    for v in ("5m", "15m"):
        fresh = [r for r in ROWS if r["v"] == v and r["fill"] and 1 <= r["d"] <= 2]
        if len(fresh) < 200: continue
        print(f"\n  ── {v} · d ∈ {{1,2}} ── (n {len(fresh)} · precio {mean([r['px'] for r in fresh]):.3f} · "
              f"spread {100*mean([r['sp'] for r in fresh]):.2f}¢)")
        byw = {}
        for r in fresh: byw.setdefault(week(r["ws"]), []).append(r)
        print(f"  {'semana':>10}{'n':>9}{'mk 5s':>9}{'mk 15s':>9}{'a resol.':>10}")
        wp = wt = 0
        for k in sorted(byw):
            s = byw[k]
            c = cell(s, 5)
            if c is not None:
                wt += 1; wp += 1 if c > 0 else 0
            print(f"  {k:>10}{len(s):>9}{fmt(cell(s,5))}{fmt(cell(s,15))}{fmt(cell(s,'res'),10)}")
        print(f"  semanas con markout positivo: {wp}/{wt}")
        print(f"\n  {'zona precio':>10}{'n':>9}{'mk 5s':>9}{'mk 15s':>9}{'a resol.':>10}")
        for nm, lo, hi in PB:
            s = [r for r in fresh if lo <= r["px"] < hi]
            if len(s) < 50: continue
            print(f"  {nm:>10}{len(s):>9}{fmt(cell(s,5))}{fmt(cell(s,15))}{fmt(cell(s,'res'),10)}")

    # ============ 3) ECONOMÍA: ¿merece la pena construirlo? ============
    print("\n" + "=" * 104)
    print("  3) ECONOMÍA con cotización fresca (d ∈ {1,2}) — cuánto da por ventana si lo montamos")
    print("=" * 104)
    print(f"  {'mercado':>10}{'ventanas':>10}{'oper/ventana':>14}{'mk5 por relleno':>18}"
          f"{'pp por ventana':>16}{'(si 1 de cada 4)':>18}")
    for v in ("5m", "15m"):
        wins = [k for k in NTR if k[0] == v]
        if not wins: continue
        fresh = [r for r in ROWS if r["v"] == v and r["fill"] and 1 <= r["d"] <= 2]
        c = cell(fresh, 5)
        if c is None: continue
        # rellenos frescos por ventana que VERÍA un cotizador siempre presente:
        nf = len(fresh) / max(1, len(set(r["ws"] for r in ROWS if r["v"] == v)))
        print(f"  {v:>10}{len(set(w for _, w in wins)):>10}{mean([NTR[k] for k in wins]):>14.1f}"
              f"{c:>+18.2f}{nf*c:>+16.1f}{nf*c/4:>+18.1f}")
    print("  (la columna de la derecha aplica el relleno real medido ~22,7%: solo ganamos 1 de cada 4 colas)")

    print("\nLECTURA: todo depende del escalón entre d=−1 y d=0/+1 del bloque 1. Si está, tenemos por primera vez")
    print("un edge de maker medido honestamente, y el trabajo pasa a ser de INGENIERÍA: recotizar por WSS en")
    print("menos de 2 s. Si no está, el +0,7 era la marca de tiempo y seguimos buscando.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
