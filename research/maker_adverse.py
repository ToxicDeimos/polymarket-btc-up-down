"""
EXPERIMENTO #10 — ¿el edge MAKER exige CANCELAR (gestión de selección adversa) o aguanta a resolución?

Establecido: el edge de los ganadores es hacer de MAKER en opciones baratas (<40¢), +3pp estructural
(maker_edge). La pregunta que decide si un maker LENTO (Pi, REST) puede capturarlo:

  Un maker postea un bid en el lado X y espera. Le llenan cuando alguien VENDE X = X cayendo. La
  selección adversa es que esos fills se agrupan cuando X va a perder. Un maker PROFESIONAL cancela
  el bid si el subyacente (BTC) se mueve EN CONTRA de X antes de llenarse. ¿Ayuda eso?

Se mide sobre los fills MAKER REALES de la cinta (miles), con potencia:
  para cada fill maker, el move de BTC (spot del lab) en los LOOKBACK s ANTES del fill, con signo
  HACIA X. Si BTC se movió en contra de X justo antes del fill = pick-off (un cancel lo habría evitado).
  Se parte el edge maker por ese move:
    · si el edge SUBE de 'en contra' a 'a favor' → cancelar-al-girarse CAPTURA el edge → hay que
      construir la cancelación en el bot.
    · si el edge es PLANO → es puro spread, aguantar a resolución vale → maker2 tal cual es correcto.
  Y un barrido de umbral de cancelación: cuánto edge se gana y cuántos fills se retienen.

Resolución por CHAINLINK donde hay dato (spot si no). El move ANTES del fill usa spot Binance del lab
(que es la señal RÁPIDA que un cancel real usaría). Solo mide fills que YA ocurrieron: es un techo del
beneficio del cancel (ignora nuestra latencia de reacción), pero si aquí no ayuda, en vivo tampoco.

    python maker_adverse.py
Autónomo (stdlib).
"""
import os, sys, csv, glob, math, bisect

DIR      = os.path.join(os.path.dirname(__file__), "lab")
MAXAGE   = 12       # frescura del libro para clasificar maker/taker
LOOKBACK = 60       # s antes del fill para medir el move de BTC (proxy del pick-off)
ZONE_HI  = 0.40     # foco: opciones baratas, donde vive el edge

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
        print(f"  {label:<28} (sin fills)"); return None
    n = len(rs); wr = sum(r["won"] for r in rs) / n; ap = sum(r["p"] for r in rs) / n
    se = math.sqrt(wr * (1 - wr) / n); edge = (wr - ap) * 100
    rel = edge / (ap * 100) * 100 if ap else 0
    sig = "SIG" if (wr - 1.96 * se) > ap else ("+" if wr > ap else "")
    print(f"  {label:<28} n={n:>6}  win {wr:6.2%}  precio {ap:6.2%}  EDGE {edge:+6.2f}pp  "
          f"rel {rel:+6.1f}%  [{max(0,wr-1.96*se):.1%}-{min(1,wr+1.96*se):.1%}] {sig}")
    return edge

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    days = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR, "tape_*.csv"))})
    if not days: print("sin tape_*.csv"); return
    tape, books = load("tape", days), load("books", days)
    lcl, lsp = series(load("chainlink", days)), series(load("spot", days))
    print(f"días: {', '.join(days)}  |  cinta {len(tape)}  |  libros {len(books)}  |  lookback {LOOKBACK}s")

    bidx = {}
    for b in books:
        try:
            ts = int(b["ts"]); b1 = float(b["b1"]) if b.get("b1") else None; a1 = float(b["a1"]) if b.get("a1") else None
        except Exception: continue
        bidx.setdefault((b.get("cid"), b.get("side")), []).append((ts, b1, a1))
    for k in bidx: bidx[k].sort()

    seen, M = set(), []
    for x in tape:
        if x.get("trade_side") != "BUY": continue
        key = (x.get("tx"), x.get("ts_trade"), x.get("price"), x.get("outcome"), x.get("proxy"))
        if key in seen: continue
        seen.add(key)
        slug = x.get("slug", "") or ""
        try:
            ws = int(slug.split("-")[-1]); t = int(x["ts_trade"]); p = float(x["price"])
        except Exception: continue
        if not (0 < p < 1): continue
        wlen = 900 if "-15m-" in slug else 300
        # MAKER? (precio <= mejor bid con libro fresco)
        arr = bidx.get((x.get("cid"), x.get("outcome")))
        if not arr: continue
        i = bisect.bisect_right([y[0] for y in arr], t) - 1
        if i < 0 or t - arr[i][0] > MAXAGE: continue
        b1 = arr[i][1]
        if b1 is None or p > b1 + 1e-9: continue                 # no es maker
        # resolución
        o, c = lcl(ws, 60), lcl(ws + wlen, 60)
        if o is None or c is None: o, c = lsp(ws, 12), lsp(ws + wlen, 12)
        if o is None or c is None: continue
        won = 1 if x.get("outcome") == ("Up" if c >= o else "Down") else 0
        # move de BTC en los LOOKBACK s ANTES del fill, con signo HACIA X (el lado comprado)
        sp_a, sp_b = lsp(t - LOOKBACK, 12), lsp(t, 12)
        if sp_a is None or sp_b is None: continue
        raw_bps = (sp_b - sp_a) / sp_b * 10000
        toward = raw_bps if x.get("outcome") == "Up" else -raw_bps   # >0 BTC fue A FAVOR de X, <0 en contra
        M.append({"p": p, "won": won, "toward": toward,
                  "phase": t - ws, "depth": b1 - p})               # fase del fill y profundidad bajo el mejor bid
    if len(M) < 300:
        print(f"solo {len(M)} fills maker con spot para lookback — deja acumular"); return
    Z = [m for m in M if m["p"] < ZONE_HI]
    print(f"fills MAKER con señal: {len(M)}  ·  en zona <{ZONE_HI:.0%}: {len(Z)}\n")

    print("=" * 100)
    print(f"1) EDGE MAKER (<{ZONE_HI:.0%}) según el move de BTC HACIA X en los {LOOKBACK}s ANTES del fill")
    print("=" * 100)
    print("   'en contra' = BTC se movió hacia hacer PERDER a X justo antes de llenarnos = pick-off = lo que un cancel evitaría\n")
    line("TODO (<40¢)", Z)
    print("   — por franja del move pre-fill (bps hacia X):")
    for lo, hi, lab in [(-1e9, -2, "muy EN CONTRA <−2bps"), (-2, -0.5, "en contra −2..−0.5"),
                        (-0.5, 0.5, "plano −0.5..0.5"), (0.5, 2, "a favor 0.5..2"), (2, 1e9, "muy a favor >2bps")]:
        line(lab, [m for m in Z if lo <= m["toward"] < hi])

    print("\n" + "=" * 100)
    print("2) SIMULACIÓN DE CANCELACIÓN — quedarse SOLO con fills donde BTC no se giró más de T contra X")
    print("=" * 100)
    base = sum(m["won"] for m in Z) / len(Z) - sum(m["p"] for m in Z) / len(Z)
    print(f"   sin cancelar (base): EDGE {base*100:+.2f}pp sobre {len(Z)} fills\n")
    for T in (3, 2, 1, 0.5, 0):
        keep = [m for m in Z if m["toward"] >= -T]
        if not keep: continue
        e = sum(m["won"] for m in keep) / len(keep) - sum(m["p"] for m in keep) / len(keep)
        ret = len(keep) / len(Z) * 100
        print(f"   cancelar si BTC va >{T:>4}bps en contra:  retiene {ret:5.1f}% de fills  →  EDGE {e*100:+6.2f}pp"
              + ("   ← mejora" if e > base + 0.005 else ""))

    print("\n" + "=" * 100)
    print("3) ¿ESTÁ EL EDGE EN EL PUNTO DE OPERACIÓN DE MAKER2? — por FASE del fill y PROFUNDIDAD")
    print("=" * 100)
    print("   maker2 postea a 195s (fase 195-300) AL mejor bid (profundidad ≈0). Si el edge de los ganadores")
    print("   vive en fases TEMPRANAS o comprando MÁS PROFUNDO, maker2 está en el sitio equivocado — y lo")
    print("   sabemos YA con la potencia de la población, sin esperar a sus fills.\n")
    print("   — por FASE del fill (maker2 vive en 195-300):")
    for lo, hi, lab in [(0, 60, "0-60s"), (60, 120, "60-120s"), (120, 195, "120-195s"),
                        (195, 300, "195-300s  ← FASE DE MAKER2"), (300, 1e9, ">300s (15m)")]:
        line(lab, [m for m in Z if lo <= m["phase"] < hi])
    print("   — por PROFUNDIDAD bajo el mejor bid (¢ por debajo; maker2 postea a ≈0 = al toque):")
    for lo, hi, lab in [(-1e9, 0.005, "al mejor bid ≈0  ← MAKER2"), (0.005, 0.02, "1-2¢ más profundo"),
                        (0.02, 0.05, "2-5¢ más profundo"), (0.05, 1e9, ">5¢ más profundo")]:
        line(lab, [m for m in Z if lo <= m["depth"] < hi])
    # combo: el punto EXACTO de maker2 (fase 195-300 Y al toque)
    m2 = [m for m in Z if 195 <= m["phase"] < 300 and m["depth"] < 0.02]
    print()
    line("PUNTO EXACTO maker2 (195-300s, ≤2¢)", m2)

    print("\n" + "=" * 100)
    print("LECTURA")
    print("=" * 100)
    print("· Sección 1/2: si el EDGE sube de 'en contra' a 'a favor' y un umbral MEJORA el base → cancelar")
    print("  captura el edge (ya construido, 3bps). Si es plano → puro spread.")
    print("· Sección 3 (la que decide el destino de maker2 SIN esperar): si el 'PUNTO EXACTO maker2'")
    print("  (195-300s, al toque) es +EV como el resto → maker2 está bien colocado, el rojo de n=20 es ruido")
    print("  y convergerá. Si es −EV mientras las fases tempranas / la profundidad SÍ ganan → maker2 opera")
    print("  en el sitio equivocado; el arreglo es postear ANTES y/o MÁS PROFUNDO, no esperar.")

if __name__ == "__main__":
    main()
