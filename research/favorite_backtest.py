"""
EXPERIMENTO #12b — la estrategia del WALLET #1: comprar el FAVORITO fuerte y AGUANTAR.

El forense (wallet_forensics) reveló que el top ganador (z+13.45, ROI +168%) compra el FAVORITO
(~87¢, 91% momentum), lo AGUANTA, y gana 85.7%. Es la única pista limpia, mecánica, Pi-ejecutable — y
la zona (82-95¢) que NUNCA operamos (el brazo A era 52-72¢; el 82-95¢ solo en sombra: +2.2%, n=294).

Este backtest lo prueba con potencia sobre la cinta acumulada:
  · para cada ventana 5m, a ENTRY=240s, lee el ASK REAL de cada lado del libro (books_*.csv).
  · FAVORITO = el lado con mayor precio (el que el mercado ve ganador). Lo compramos a su ask, aguantamos.
  · won = el favorito ganó (resolución por CHAINLINK donde hay dato, spot si no).
  · EDGE = win − ask.  Por franja de precio del favorito + TRAIN/TEST por día.

Si el favorito 82-95¢ gana MÁS que su ask, con IC → el edge del wallet #1 es real y replicable (comprar
el favorito tarde y aguantar, lento, sin velocidad). Si no bate al ask → era supervivencia. Con potencia.

    python favorite_backtest.py
Autónomo (stdlib).
"""
import os, sys, csv, glob, math, bisect

DIR   = os.path.join(os.path.dirname(__file__), "lab")
ENTRY = 240

def load(name, days):
    rows = []
    for d in days:
        p = os.path.join(DIR, f"{name}_{d}.csv")
        if os.path.exists(p): rows += list(csv.DictReader(open(p, encoding="utf-8")))
    return rows

def series(rows):
    idx = sorted((int(r["ts"]), float(r["price"])) for r in rows if r.get("price"))
    ks = [x[0] for x in idx]
    def at(ts, maxage):
        i = bisect.bisect_right(ks, ts) - 1
        return idx[i][1] if i >= 0 and ts - idx[i][0] <= maxage else None
    return at

def line(label, rs):
    if not rs:
        print(f"  {label:<22} (sin ventanas)"); return
    n = len(rs); wr = sum(r["won"] for r in rs) / n; ap = sum(r["ask"] for r in rs) / n
    se = math.sqrt(wr * (1 - wr) / n); edge = (wr - ap) * 100
    lo, hi = max(0, wr - 1.96 * se), min(1, wr + 1.96 * se)
    sig = "SIG" if lo > ap else ("+" if wr > ap else "")
    print(f"  {label:<22} n={n:>6}  win {wr:6.2%}  ask {ap:6.2%}  EDGE {edge:+6.2f}pp  [IC {lo:.1%}-{hi:.1%}] {sig}")

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    days = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR, "books_*.csv"))})
    if not days: print("sin books_*.csv"); return
    tape = load("tape", days); books = load("books", days)
    lcl, lsp = series(load("chainlink", days)), series(load("spot", days))
    # cid → ws (de la cinta: slug = btc-updown-5m-{ws})
    cid2ws = {}
    for x in tape:
        slug = x.get("slug", "") or ""
        if "-5m-" not in slug: continue
        try: cid2ws[x.get("cid")] = int(slug.split("-")[-1])
        except Exception: pass
    # índice de ASK por (cid, side)
    aidx = {}
    for b in books:
        try:
            ts = int(b["ts"]); a1 = float(b["a1"]) if b.get("a1") else None
        except Exception: continue
        if a1 is None: continue
        aidx.setdefault((b.get("cid"), b.get("side")), []).append((ts, a1))
    for k in aidx: aidx[k].sort()
    def ask_at(cid, side, t, maxage=20):
        arr = aidx.get((cid, side))
        if not arr: return None
        i = bisect.bisect_right([x[0] for x in arr], t) - 1
        return arr[i][1] if i >= 0 and t - arr[i][0] <= maxage else None
    print(f"días: {', '.join(days)}  |  cinta {len(tape)}  |  libros {len(books)}  |  ventanas {len(cid2ws)}")

    R = []
    for cid, ws in cid2ws.items():
        au = ask_at(cid, "Up", ws + ENTRY); ad = ask_at(cid, "Down", ws + ENTRY)
        if au is None or ad is None: continue
        fav = "Up" if au > ad else "Down"; ask = max(au, ad)
        if not (0.5 < ask < 1): continue                 # el favorito por definición > 50¢
        o, c = lcl(ws, 60), lcl(ws + 300, 60)
        if o is None or c is None: o, c = lsp(ws, 12), lsp(ws + 300, 12)
        if o is None or c is None: continue
        winner = "Up" if c >= o else "Down"
        R.append({"ask": ask, "won": 1 if fav == winner else 0, "ws": ws})
    if len(R) < 100:
        print(f"solo {len(R)} ventanas con libro a {ENTRY}s — deja acumular"); return
    print(f"\nventanas con favorito y libro a {ENTRY}s: {len(R)}\n")

    print("=" * 90)
    print(f"COMPRAR EL FAVORITO al ask a {ENTRY}s y AGUANTAR — ¿gana MÁS que su ask? (estrategia wallet #1)")
    print("=" * 90)
    line("TODOS los favoritos", R)
    print("  — por franja de precio del favorito (la zona del wallet #1 es 82-95¢):")
    for lo, hi, lab in [(0.50, 0.62, "50-62¢"), (0.62, 0.72, "62-72¢"), (0.72, 0.82, "72-82¢"),
                        (0.82, 0.90, "82-90¢"), (0.90, 0.95, "90-95¢"), (0.95, 1.0, "95-99¢")]:
        line(lab, [r for r in R if lo <= r["ask"] < hi])

    # ¿generaliza? train/test por día sobre la zona del wallet #1 (82-95¢)
    Z = [r for r in R if 0.82 <= r["ask"] < 0.95]
    print("\n" + "=" * 90)
    print("TRAIN/TEST por día sobre la ZONA 82-95¢ (¿el edge del favorito generaliza o es una época?)")
    print("=" * 90)
    if len(Z) >= 100:
        Z.sort(key=lambda r: r["ws"]); cut = Z[int(len(Z) * 0.6)]["ws"]
        line("TRAIN (82-95¢)", [r for r in Z if r["ws"] < cut])
        line("TEST  (82-95¢)", [r for r in Z if r["ws"] >= cut])
    else:
        print(f"  solo {len(Z)} en 82-95¢ — poca potencia, deja acumular")

    print("\n" + "=" * 90)
    print("VEREDICTO")
    print("=" * 90)
    z = [r for r in R if 0.82 <= r["ask"] < 0.95]
    if z:
        wr = sum(r["won"] for r in z) / len(z); ap = sum(r["ask"] for r in z) / len(z)
        se = math.sqrt(wr * (1 - wr) / len(z))
        if wr - 1.96 * se > ap:
            print(f"  → 82-95¢: win {wr:.1%} > ask {ap:.1%} SIGNIFICATIVO → la estrategia del wallet #1 es REAL")
            print("    y replicable: comprar el favorito fuerte tarde y aguantar. Lenta, cabe en una Pi.")
        elif wr > ap:
            print(f"  → 82-95¢: win {wr:.1%} > ask {ap:.1%} pero no significativo (n={len(z)}). Prometedor, más datos.")
        else:
            print(f"  → 82-95¢: win {wr:.1%} <= ask {ap:.1%}: comprar el favorito NO bate su precio. El wallet #1")
            print("    era supervivencia (o su edge está en la SELECCIÓN de qué favorito, no en todos). Negativo.")
    print("\nCAVEAT: margen fino y precio alto = alta varianza (una pérdida = ~7 aciertos). +EV real necesita")
    print("muchas apuestas. Y esto compra TODOS los favoritos 82-95¢; el wallet #1 quizá SELECCIONA cuáles.")

if __name__ == "__main__":
    main()
