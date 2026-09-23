"""
clockconfirm.py — CONFIRMAR EL DESFASE POR OTRA VÍA, con datos en vivo y sin modelo de por medio.

clocklag concluyó, por la forma de la campana de coincidencia precio↔cotización (pico afilado y simétrico en
d=3 en los DOS mercados, 130-170k observaciones por barra), que las marcas de tiempo de la cinta de la API van
~3 s POR DELANTE de las del libro que sella nuestro colector. Eso convierte en look-ahead todo lo que cruzó
cinta contra libro, incluido el markout "fresco" de +0,7pp.

Es una conclusión cara, así que hay que verla por un camino independiente. Lo tenemos gratis: el
endgame-monitor apunta cada operación del WSS con el RELOJ DE LA PI (endgame_trades.csv: ts local, ws, lado,
side, precio, tamaño), y esa MISMA operación aparece en wintrades_*.csv con el RELOJ DE POLYMARKET (ts_trade).
Emparejándolas por ventana + lado + precio, la diferencia ts_api − ts_wss ES el desfase, medido sin suponer
nada sobre el comportamiento del mercado.

Interpretación: el WSS llega con unos pocos ms de retraso, así que ts_wss ≈ instante físico en el reloj de la
Pi (el mismo con el que se sellan las fotos del libro). Si la mediana de ts_api − ts_wss sale ≈ +3 s, el
desfase queda confirmado y la corrección es exactamente la que aplica markout.py. Si sale ≈ 0, entonces la
campana de clocklag significaba otra cosa y hay que volver sobre ella.

    cd ~/polymarket-btc-up-down/research && python3 clockconfirm.py
"""
import csv, os, sys, glob, bisect

DIR = os.path.join(os.path.dirname(__file__), "lab")
TLOG = os.path.join(os.path.dirname(__file__), "endgame_trades.csv")
PXTOL = 0.0011          # mismo precio (los precios van en céntimos)
TWIN = 30               # ventana de búsqueda para emparejar, s


def med(xs):
    s = sorted(xs); return s[len(s) // 2] if s else float("nan")


def main():
    if not os.path.exists(TLOG):
        print(f"no encuentro {TLOG} — ¿está corriendo endgame-monitor?"); return
    W = []
    with open(TLOG, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                W.append((float(r["ts"]), int(r["ws"]), r["side"].strip(),
                          (r.get("trade_side") or r.get("tside") or "").upper(), float(r["price"])))
            except Exception:
                continue
    if not W:
        # cabecera distinta: leer posicionalmente
        with open(TLOG, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: W.append((float(row[0]), int(row[1]), row[2].strip(), (row[3] or "").upper(), float(row[4])))
                except Exception: continue
    if not W:
        print("endgame_trades.csv vacío o ilegible"); return
    wss_set = set(w[1] for w in W)
    print(f"operaciones WSS: {len(W)} · ventanas: {len(wss_set)}")

    # ws -> cid  (el monitor es 5m)
    ws2cid = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 3 or not row[1].startswith("btc-updown-5m-"): continue
                try: ws = int(row[1].split("-")[-1])
                except Exception: continue
                if ws in wss_set and ws not in ws2cid: ws2cid[ws] = row[2]
    print(f"ventanas con cid conocido: {len(ws2cid)}")
    if not ws2cid:
        print("ninguna ventana del monitor aparece en books_*.csv — nada que emparejar"); return
    cids = set(ws2cid.values())

    API = {}
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("cid") not in cids or r.get("outcome") not in ("Up", "Down"): continue
                try: ts = int(float(r["ts_trade"])); pr = float(r["price"])
                except Exception: continue
                API.setdefault((r["cid"], r["outcome"]), []).append((ts, pr))
    for k in API: API[k].sort()
    print(f"series de la API emparejables: {len(API)} · operaciones: {sum(len(v) for v in API.values())}")
    if not API:
        print("la cinta de la API aún no cubre esas ventanas — esperar a que el colector las escriba"); return

    deltas = []; nomatch = 0
    for tsw, ws, side, tside, px in W:
        cid = ws2cid.get(ws)
        rows = API.get((cid, side)) if cid else None
        if not rows: nomatch += 1; continue
        lo = bisect.bisect_left(rows, (int(tsw) - TWIN, -1))
        hi = bisect.bisect_right(rows, (int(tsw) + TWIN, 2))
        best = None
        for j in range(lo, hi):
            ts, pr = rows[j]
            if abs(pr - px) > PXTOL: continue
            d = ts - tsw
            if best is None or abs(d) < abs(best): best = d
        if best is None: nomatch += 1
        else: deltas.append(best)

    print(f"\nemparejadas: {len(deltas)} · sin pareja: {nomatch}")
    if len(deltas) < 30:
        print("muy pocas parejas para concluir — dejar correr el monitor unas horas más"); return
    deltas.sort()
    q = lambda p: deltas[min(len(deltas) - 1, int(p * len(deltas)))]
    print(f"\n  ts_API − ts_WSS (s):  p10 {q(.10):+.1f} · p25 {q(.25):+.1f} · MEDIANA {med(deltas):+.1f} · "
          f"p75 {q(.75):+.1f} · p90 {q(.90):+.1f}")
    print(f"\n  {'delta':>8}{'n':>9}{'%':>8}   perfil")
    H = {}
    for d in deltas: H[round(d)] = H.get(round(d), 0) + 1
    mx = max(H.values())
    for d in sorted(H):
        n = H[d]
        print(f"  {d:>+8}{n:>9}{100*n/len(deltas):>7.1f}%   " + "█" * int(round(50 * n / mx)))

    m = med(deltas)
    print(f"\nVEREDICTO: mediana {m:+.1f} s.")
    if m >= 2:
        print(f"  → DESFASE CONFIRMADO: la cinta de la API va ~{m:.0f} s por delante del reloj que sella el")
        print( "    libro. El 'markout fresco' de +0,7pp usaba fotos POSTERIORES a la operación = look-ahead.")
        print( "    La corrección correcta es la que aplica markout.py con OFFSET, y hay que revisar todo lo")
        print( "    que simule rellenos cruzando cinta contra libro (spread_mm, mm_paper, mm_ws).")
    elif abs(m) < 1:
        print("  → SIN DESFASE: los relojes están alineados y la campana de clocklag significa otra cosa")
        print("    (habría que explicar por qué el máximo de coincidencia cae en d=3 y no forma meseta).")
    else:
        print("  → desfase intermedio: repetir con más muestra antes de corregir nada.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
