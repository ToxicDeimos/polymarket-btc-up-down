"""
wallet1.py — DE DÓNDE SALE SU DINERO. Dejamos de generalizar y vamos a por las wallets concretas que ganan de
forma indudable (0x0c7c520440: z +3,26 en la 1ª mitad y +3,20 en la 2ª, cruzando el spread).

La tensión a resolver: ese dinero existe, y todas nuestras pruebas dicen que el mercado está calibrado
(turnedge: su señal compra a 0,282 y gana el 28%; el espejo compra a 0,728 y gana el 72%). Las dos cosas solo
caben juntas si su dinero NO viene de acertar la dirección. Se comprueba directamente:

 A) SU PROPIA CALIBRACIÓN — por franja de precio, ¿sus COMPRAS ganan más de lo que pagan? Si compran a 0,30 y
    ganan el 30%, no hay ventaja direccional y hay que buscar el dinero en otro sitio. Lo mismo con sus VENTAS.
    Esta es la prueba que nunca habíamos hecho sobre ellos.
 B) ¿ENTRADAS O SALIDAS? — descomponer su P&L entre posiciones CERRADAS dentro de la ventana (round-trip: el
    dinero es spread/salida) y posiciones AGUANTADAS hasta la resolución (el dinero es selección).
    ⚠ La cinta global va al 41%, así que de muchas posiciones vemos UNA SOLA PATA y las contamos como
    aguantadas cuando quizá se cerraron. Por eso se reporta también cuánto pesa cada grupo: si casi todo el
    P&L sale de posiciones de una sola operación, la lectura "aguantan" es sólida; si sale de las de varias,
    la cobertura nos está engañando y su negocio es la salida.
 C) RETRATO — tamaño, momento de la ventana, cuántas ventanas tocan, compras frente a ventas, y qué parte de
    su P&L viene de comprar frente a vender.

    cd ~/polymarket-btc-up-down/research && python3 wallet1.py            # las mejores por z
    cd ~/polymarket-btc-up-down/research && python3 wallet1.py 0x0c7c...  # una concreta
"""
import csv, os, sys, glob, math, time

DIR = os.path.join(os.path.dirname(__file__), "lab")
MINP = 150
TOPN = 6
PB = [("<0,15", 0, .15), ("0,15-0,30", .15, .30), ("0,30-0,45", .30, .45), ("0,45-0,55", .45, .55),
      ("0,55-0,70", .55, .70), ("0,70-0,85", .70, .85), ("≥0,85", .85, 1.01)]


def mean(xs): return sum(xs) / len(xs) if xs else float("nan")


def main():
    want = [a.lower() for a in sys.argv[1:] if a.startswith("0x")]
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]

    TR = {}          # wallet -> lista de operaciones
    seen = set()
    for path in sorted(glob.glob(os.path.join(DIR, "tape_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                slug = r.get("slug") or ""
                if not slug.startswith("btc-updown-"): continue
                oc = r.get("outcome"); wal = (r.get("proxy") or "").lower()
                if oc not in ("Up", "Down") or not wal: continue
                if want and wal not in want: continue
                k = (r.get("tx", ""), oc, r.get("price"), r.get("trade_side"), r.get("ts_trade"))
                if k in seen: continue
                seen.add(k)
                try:
                    px = float(r["price"]); sz = float(r["size"] or 0)
                    ts = int(float(r["ts_trade"])); ws = int(slug.split("-")[-1])
                except Exception: continue
                if sz <= 0: continue
                v = "5m" if "-5m-" in slug else "15m"
                TR.setdefault(wal, []).append(
                    (r.get("cid"), oc, ts, ws, v, px, sz,
                     (r.get("trade_side") or "").upper().startswith("B")))
    print(f"wallets con operaciones: {len(TR)}", flush=True)
    if not TR: print("sin datos"); return

    # z por wallet (varianza teórica del precio), para elegir a las mejores
    def profile(tr):
        POS = {}
        for cid, oc, ts, ws, v, px, sz, buy in tr:
            a = POS.setdefault((cid, oc), [0.0, 0.0, 0.0, 0.0, 0, ws, v, []])
            a[0] += sz if buy else -sz
            a[1] += (-px * sz) if buy else (px * sz)
            a[2] += px * sz; a[3] += sz; a[4] += 1
            a[7].append((ts - ws) / (300.0 if v == "5m" else 900.0))
        return POS

    rank = []
    for wal, tr in TR.items():
        POS = profile(tr)
        exc = var = 0.0; n = 0
        for (cid, oc), a in POS.items():
            win = reso.get(cid)
            if win not in ("Up", "Down") or abs(a[0]) < 1e-6: continue
            p = min(max(a[2] / a[3] if a[3] > 0 else .5, 1e-4), 1 - 1e-4)
            pnl = a[1] + a[0] * (1.0 if win == oc else 0.0)
            exc += pnl - (a[1] + a[0] * p); var += (a[0] ** 2) * p * (1 - p); n += 1
        if n >= MINP and var > 1e-9:
            rank.append((exc / math.sqrt(var), wal, n))
    rank.sort(reverse=True)
    sel = [w for _, w, _ in rank[:TOPN]] if not want else list(TR.keys())
    print(f"perfilando {len(sel)} wallets\n", flush=True)

    for wal in sel:
        tr = TR[wal]; POS = profile(tr)
        tot = flatp = heldp = 0.0; nflat = nheld = 0; n1 = 0; p1 = 0.0
        buypnl = sellpnl = 0.0
        wins = set(); szs = []; fracs = []
        CAL = {"B": {}, "S": {}}
        for (cid, oc), (sh, cash, pxsz, szs_, nops, ws, v, fr) in POS.items():
            win = reso.get(cid)
            if win not in ("Up", "Down"): continue
            w1 = 1 if win == oc else 0
            pnl = cash + sh * w1
            tot += pnl; wins.add(cid); szs.append(szs_); fracs += fr
            if abs(sh) < 1e-6: flatp += pnl; nflat += 1
            else: heldp += pnl; nheld += 1
            if nops == 1: n1 += 1; p1 += pnl
            if sh > 0: buypnl += pnl
            elif sh < 0: sellpnl += pnl
        for cid, oc, ts, ws, v, px, sz, buy in tr:
            win = reso.get(cid)
            if win not in ("Up", "Down"): continue
            w1 = 1 if win == oc else 0
            for nm, lo, hi in PB:
                if lo <= px < hi:
                    d = CAL["B" if buy else "S"].setdefault(nm, [0, 0.0, 0.0])
                    d[0] += 1; d[1] += px; d[2] += w1
                    break
        print("=" * 104)
        print(f"  {wal}")
        print("=" * 104)
        print(f"  operaciones {len(tr)} · posiciones {len(POS)} · ventanas {len(wins)} · "
              f"P&L ${tot:,.0f}")
        print(f"  tamaño mediano {sorted(szs)[len(szs)//2]:.0f} sh · momento mediano de la ventana "
              f"{100*sorted(fracs)[len(fracs)//2]:.0f}%")
        print(f"\n  B) ¿ENTRADAS O SALIDAS?")
        print(f"     cerradas dentro de la ventana : {nflat:>6} posiciones · ${flatp:>10,.0f}")
        print(f"     aguantadas a resolución       : {nheld:>6} posiciones · ${heldp:>10,.0f}")
        print(f"     de UNA sola operación (sin duda de cobertura): {n1:>6} · ${p1:>10,.0f} "
              f"({100*p1/tot if tot else float('nan'):.0f}% del total)")
        print(f"     P&L de posiciones compradoras ${buypnl:,.0f} · vendedoras ${sellpnl:,.0f}")
        for side, lab in (("B", "COMPRAS"), ("S", "VENTAS")):
            rows = [(nm, d) for nm, d in CAL[side].items() if d[0] >= 40]
            if not rows: continue
            print(f"\n  A) CALIBRACIÓN DE SUS {lab}: ¿gana más de lo que paga?")
            print(f"  {'franja':>12}{'n':>8}{'precio':>9}{'gana%':>9}{'dif pp':>9}{'z':>8}")
            for nm, _, _ in PB:
                d = CAL[side].get(nm)
                if not d or d[0] < 40: continue
                n, sp, sw = d
                pm = sp / n; g = sw / n
                se = math.sqrt(max(pm * (1 - pm), 1e-9) / n)
                print(f"  {nm:>12}{n:>8}{100*pm:>9.1f}{100*g:>8.1f}%{100*(g-pm):>+9.2f}"
                      f"{(g-pm)/se:>+8.2f}")
        print()

    print("LECTURA: si en A) el 'dif pp' de sus COMPRAS es ~0 en todas las franjas, no tienen ventaja")
    print("direccional y su dinero no sale de acertar — hay que mirarlo en B). Si el P&L se concentra en")
    print("posiciones 'de una sola operación', la lectura 'aguantan hasta la resolución' es sólida y entonces")
    print("SÍ es selección y el 'dif pp' tiene que salir positivo en algún sitio. Si se concentra en las de")
    print("varias operaciones, con el 41% de cobertura estamos viendo medias posiciones y su negocio es la")
    print("SALIDA, no la entrada: ahí no hay nada que copiar sin ver la cinta completa.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
