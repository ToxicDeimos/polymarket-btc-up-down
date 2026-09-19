"""
endwindow_traj.py — DESEMPATE. El forense mostró que en TEST el libro cotiza al eventual ganador a ~0,57 a
falta de 20s (no es fantasma; suma≈1). ¿Es desfase REAL (converge a ~0,9 en los últimos segundos) o libro
RANCIO (plano en ~0,57 = valor viejo con ts fresco)? Se distingue mirando la TRAYECTORIA del ask del ganador
en el último minuto, por mitad (train/test) y por si el spot-dir acabó ganando.

 - TEST · ganó=sí: si el ask SUBE 0,57→0,75→0,90 al acercarse al cierre → CONVERGENCIA = desfase real (A).
 - TEST · ganó=sí: si el ask se queda PLANO en ~0,57 → CONGELADO = libro rancio = artefacto (B).

    cd ~/polymarket-btc-up-down/research && python3 endwindow_traj.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
SEL_DELTA = 20          # el spot-dir y el filtro |mov| se fijan en T−SEL_DELTA (igual que el forense)
MOVE = 50
TOL_SPOT = 6
OFFSETS = (120, 90, 60, 40, 20, 10, 5, 2)   # s antes del cierre para la trayectoria
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "traj/1.0"})
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


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort()
    return out, [t for t, _ in out]


def near_le(series, idx, t, tol):
    if not series: return None
    i = bisect.bisect_right(idx, t) - 1
    if i >= 0 and t - series[i][0] <= tol: return series[i]
    return None


def load_books_asks():
    W = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-5m-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                w = W.setdefault(row[1], {"cid": row[2], "ws": int(row[1].split("-")[-1]),
                                         "asks": {"Up": [], "Down": []}})
                w["asks"][row[3]].append((ts, ask))
    return W


def ask_at(rows, tsx, t, tol=8):
    """ask del snapshot más cercano a t dentro de tol (rows ordenado, idx tsx)."""
    if not rows: return None
    i = bisect.bisect_left(tsx, t); best = None; bd = tol + 1
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(rows):
            d = abs(rows[j][0] - t)
            if d < bd: bd = d; best = rows[j][1]
    return best if bd <= tol else None


def main():
    spot, sidx = load_spot()
    W = load_books_asks()
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
        if w: reso[cid] = w
        return w

    WLEN = 300
    # seleccionar ventanas (igual que el forense) y guardar la serie de asks del ganador + idx + hit + ws
    sel = []
    for slug, w in W.items():
        ws = w["ws"]
        for s in ("Up", "Down"): w["asks"][s].sort()
        op = near_le(spot, sidx, ws, TOL_SPOT)
        sp = near_le(spot, sidx, ws + WLEN - SEL_DELTA, TOL_SPOT)
        if op is None or sp is None: continue
        move = sp[1] - op[1]
        if abs(move) < MOVE: continue
        sdir = "Up" if move > 0 else "Down"
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        rows = w["asks"][sdir]; tsx = [t for t, _ in rows]
        sel.append((ws, rows, tsx, 1 if win == sdir else 0))
    n = len(sel)
    print(f"ventanas 5m |mov|≥${MOVE}: {n}")
    if not n: return
    mid = sorted(s[0] for s in sel)[n // 2]

    def mean(xs):
        xs = [x for x in xs if x is not None]; return (sum(xs) / len(xs)) if xs else None

    groups = [("TRAIN·ganó",  lambda s: s[0] < mid and s[3] == 1),
              ("TEST ·ganó",  lambda s: s[0] >= mid and s[3] == 1),
              ("TEST ·perdió", lambda s: s[0] >= mid and s[3] == 0)]
    print("\nTRAYECTORIA del ask del GANADOR-spot (media) por segundos antes del cierre:")
    print(f"  {'grupo':>13}{'n':>6}" + "".join(f"{('T-'+str(o)):>8}" for o in OFFSETS))
    for name, filt in groups:
        sub = [s for s in sel if filt(s)]
        m = len(sub)
        if not m: continue
        cells = []
        for o in OFFSETS:
            vals = [ask_at(s[1], s[2], s[0] + WLEN - o) for s in sub]
            av = mean(vals)
            cells.append(f"{av:>8.3f}" if av is not None else f"{'-':>8}")
        print(f"  {name:>13}{m:>6}" + "".join(cells))
    print("\nLECTURA (fila TEST·ganó): si el ask SUBE hacia ~0,90 al acercarse a T-2 → CONVERGENCIA = desfase")
    print("REAL (A), tradeable. Si queda PLANO en ~0,57 hasta T-2 → CONGELADO = libro rancio = artefacto (B).")
    print("Contraste: TRAIN·ganó debería estar ya alto y plano (~0,95); TEST·perdió debería BAJAR hacia ~0.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
