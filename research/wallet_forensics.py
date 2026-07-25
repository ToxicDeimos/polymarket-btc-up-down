"""
EXPERIMENTO #12 — FORENSE de UN ganador: su playbook EXACTO, no estadística agregada.

Toda la ingeniería inversa previa fue agregada ("los makers ganan de media"). Nunca reconstruimos a UN
ganador operación por operación. La pregunta clave sin responder de verdad:

  ¿AGUANTAN a resolución (apuesta al resultado) o hacen SCALP (compran al bid, venden al ask, capturan
   el spread, se quedan planos)? Si es scalp → el edge es el SPREAD, lento y MECÁNICO = replicable.
   maker2/maker3 (comprar y aguantar) sería la estrategia equivocada.

Para los wallets top por z-score (n>=MIN_N), reconstruye por VENTANA:
  · compras (precio, ts, fase) y ventas (precio, ts) → ¿cierra la posición (scalp) o aguanta?
  · precio de entrada RELATIVO al mid del libro (¿qué tan profundo postea?)
  · lado vs el movimiento del spot (¿favorito o despreciado?)
  · P&L: si scalpea, spread capturado; si aguanta, resultado.

Salida: % scalp vs hold, profundidad de entrada típica, lado, y de dónde sale el dinero.
Resolución por CHAINLINK donde hay dato. Autónomo (stdlib).

    python wallet_forensics.py
"""
import os, sys, csv, glob, math, bisect
from collections import defaultdict

DIR   = os.path.join(os.path.dirname(__file__), "lab")
MIN_N = 100
TOPK  = 3       # cuántos ganadores forense

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

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    days = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR, "tape_*.csv"))})
    if not days: print("sin tape_*.csv"); return
    tape = load("tape", days); books = load("books", days)
    lcl, lsp = series(load("chainlink", days)), series(load("spot", days))
    print(f"días: {', '.join(days)}  |  cinta {len(tape)}  |  libros {len(books)}")

    # índice de libro por (cid, side) → mid en un instante
    bidx = {}
    for b in books:
        try:
            ts = int(b["ts"]); b1 = float(b["b1"]) if b.get("b1") else None; a1 = float(b["a1"]) if b.get("a1") else None
        except Exception: continue
        bidx.setdefault((b.get("cid"), b.get("side")), []).append((ts, b1, a1))
    for k in bidx: bidx[k].sort()
    def mid_at(cid, side, t, maxage=15):
        arr = bidx.get((cid, side))
        if not arr: return None
        i = bisect.bisect_right([x[0] for x in arr], t) - 1
        if i < 0 or t - arr[i][0] > maxage: return None
        b1, a1 = arr[i][1], arr[i][2]
        if b1 is None or a1 is None: return None
        return (b1 + a1) / 2

    # normaliza trades; resuelve ganador por ventana
    seen = set(); T = []
    for x in tape:
        key = (x.get("tx"), x.get("ts_trade"), x.get("price"), x.get("outcome"), x.get("proxy"), x.get("trade_side"))
        if key in seen: continue
        seen.add(key)
        slug = x.get("slug", "") or ""
        try:
            ws = int(slug.split("-")[-1]); t = int(x["ts_trade"]); p = float(x["price"]); sz = float(x.get("size") or 0)
        except Exception: continue
        if not (0 < p < 1) or sz <= 0: continue
        T.append({"w": x.get("proxy"), "ws": ws, "t": t, "p": p, "sz": sz,
                  "side": x.get("outcome"), "bs": x.get("trade_side"), "cid": x.get("cid")})
    # z-score por wallet (solo BUY, aguantado, como survivorship) para elegir a los top
    WA = defaultdict(lambda: {"n": 0, "cost": 0.0, "pnl": 0.0, "var": 0.0})
    resolved = {}
    def winner(ws):
        if ws in resolved: return resolved[ws]
        o, c = lcl(ws, 60), lcl(ws + 300, 60)
        if o is None or c is None: o, c = lsp(ws, 12), lsp(ws + 300, 12)
        resolved[ws] = ("Up" if c >= o else "Down") if (o is not None and c is not None) else None
        return resolved[ws]
    for r in T:
        if r["bs"] != "BUY": continue
        wn = winner(r["ws"])
        if wn is None: continue
        a = WA[r["w"]]; won = 1 if r["side"] == wn else 0
        a["n"] += 1; a["cost"] += r["sz"] * r["p"]; a["pnl"] += r["sz"] * (won - r["p"])
        a["var"] += (r["sz"] ** 2) * r["p"] * (1 - r["p"])
    cand = []
    for w, a in WA.items():
        if a["n"] < MIN_N or not a["cost"]: continue
        z = (a["pnl"] / a["cost"]) / (math.sqrt(a["var"]) / a["cost"]) if a["var"] else 0
        cand.append((z, w, a["n"], a["pnl"] / a["cost"]))
    cand.sort(reverse=True)
    if not cand: print("sin wallets con volumen suficiente"); return

    for z, wallet, nn, roi in cand[:TOPK]:
        print("\n" + "=" * 96)
        print(f"WALLET {wallet}  ·  z {z:+.2f}  ·  {nn} compras  ·  ROI {roi:+.1%}")
        print("=" * 96)
        # todas sus operaciones agrupadas por ventana
        byw = defaultdict(list)
        for r in T:
            if r["w"] == wallet: byw[r["ws"]].append(r)
        scalp = hold = 0; depths = []; mom = fade = 0; held_won = held_n = 0
        hold_secs = []; entry_prices = []; band = []   # (precio_entrada, won) de sus posiciones aguantadas
        for ws, ops in byw.items():
            buys = [o for o in ops if o["bs"] == "BUY"]
            sells = [o for o in ops if o["bs"] == "SELL"]
            if not buys: continue
            bsz = sum(o["sz"] for o in buys); ssz = sum(o["sz"] for o in sells)
            first = min(buys, key=lambda o: o["t"])
            entry_prices.append(first["p"])
            # profundidad vs mid del libro al entrar
            m = mid_at(first["cid"], first["side"], first["t"])
            if m is not None: depths.append((m - first["p"]) * 100)   # ¢ por debajo del mid
            # lado vs movimiento del spot (ws→entrada)
            so, se = lsp(ws, 15), lsp(first["t"], 15)
            if so is not None and se is not None and abs(se - so) > 1:
                lead = "Up" if se > so else "Down"
                if first["side"] == lead: mom += 1
                else: fade += 1
            # scalp o hold?
            if ssz >= 0.8 * bsz and sells:
                scalp += 1
                lastsell = max(sells, key=lambda o: o["t"])
                hold_secs.append(lastsell["t"] - first["t"])
            else:
                hold += 1
                wn = winner(ws)
                if wn is not None:
                    w1 = 1 if first["side"] == wn else 0
                    held_n += 1; held_won += w1
                    band.append((first["p"], w1))
        nw = scalp + hold
        if nw == 0: print("  (sin ventanas reconstruibles)"); continue
        print(f"  ventanas operadas: {nw}")
        print(f"  SCALP (compra y vende dentro de la ventana): {scalp} ({scalp*100//nw}%)"
              + (f"  ·  duración mediana {sorted(hold_secs)[len(hold_secs)//2]:.0f}s" if hold_secs else ""))
        print(f"  HOLD  (aguanta a resolución):                {hold} ({hold*100//nw}%)"
              + (f"  ·  win {held_won/held_n:.1%} sobre {held_n}" if held_n else ""))
        if entry_prices:
            ep = sorted(entry_prices)
            print(f"  precio de entrada: mediana {ep[len(ep)//2]:.0%}  (rango {ep[0]:.0%}-{ep[-1]:.0%})")
        if depths:
            dp = sorted(depths)
            print(f"  profundidad vs MID del libro: mediana {dp[len(dp)//2]:+.1f}¢  (>0 = compra por debajo del mid)")
        if mom + fade:
            print(f"  lado vs movimiento: momentum {mom} / fade {fade}  ({mom*100//(mom+fade)}% con el move)")
        # ¿de qué BANDA de precio sale su dinero? win vs precio pagado (EV/share) por banda
        if band:
            print("  DINERO por banda de precio de entrada (win vs precio = EV/share; ¿solo favoritos?):")
            for lo, hi, lab in [(0, .20, "<20¢ longshot"), (.20, .40, "20-40¢"), (.40, .62, "40-62¢"),
                                (.62, .82, "62-82¢"), (.82, .95, "82-95¢ (mi bot)"), (.95, 1.01, "95-99¢")]:
                seg = [(p, w) for p, w in band if lo <= p < hi]
                if not seg: continue
                n = len(seg); wr = sum(w for _, w in seg) / n; ap = sum(p for p, _ in seg) / n
                ev = (wr - ap) * 100
                print(f"     {lab:<18} n={n:>4}  win {wr:5.1%}  precio {ap:5.1%}  EV/share {ev:+6.2f}pp"
                      + ("  ← +EV" if ev > 1 else ("  ← −EV" if ev < -1 else "")))

    print("\n" + "=" * 96)
    print("CÓMO LEER")
    print("=" * 96)
    print("· Si SCALP domina (vende dentro de la ventana) → su edge es el SPREAD, no el resultado. Mecánico")
    print("  y LENTO-replicable: postear bid Y ask, capturar el spread, quedarse plano. Es OTRO bot,")
    print("  el que no construimos. maker2/maker3 (comprar y aguantar) atacaban el problema equivocado.")
    print("· Si HOLD domina → apuestan al resultado; el edge es la SELECCIÓN de qué comprar (lo que no")
    print("  hemos podido replicar). Mira entonces precio de entrada / profundidad / lado por si hay regla.")
    print("· DINERO por banda: ¿el wallet gana SOLO en 82-95¢ (mi bot lo captura) o también en otras bandas")
    print("  (mi bot se pierde parte de su edge)? Si hay +EV en <40¢ o 62-82¢, hay que ampliar el bot.")

if __name__ == "__main__":
    main()
