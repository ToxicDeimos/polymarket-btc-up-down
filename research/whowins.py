"""
whowins.py — ¿HAY MÁS GANADORES DE LOS QUE PRODUCE EL AZAR? Todo el análisis previo de ganadores partió de 6
wallets ELEGIDAS POR HABER GANADO (winner_mirror, winner_criteria, feature_mine, selection_probe). Eso es
empezar por el final: si las seleccionas por su resultado, siempre encontrarás algo que las distinga, aunque
sea varianza. Con miles de wallets operando, la cola derecha existe por construcción.

La pregunta correcta, que nunca hemos hecho, es de POBLACIÓN: ¿la distribución de resultados por wallet es
más ancha por la derecha de lo que daría el azar? Nunca la pudimos hacer porque mirábamos 6 wallets; pero el
colector lleva semanas guardando `tape_*.csv` con el proxyWallet de CADA operación de btc-updown.

  A) CENSO — P&L por wallet reconstruido posición a posición (maneja round-trips: efectivo + acciones netas ×
     resultado). Concentración: cuánto del beneficio total se llevan los 10 primeros.
  B) ¿HABILIDAD O SUPERVIVENCIA? — z por wallet sobre sus posiciones (una posición = una ventana × token ×
     wallet: la lección de check15). Si solo hay azar, esos z se reparten como una normal estándar. Contamos
     cuántas wallets superan +2 y +3 y lo comparamos con las que TOCARÍAN por azar. Si no hay exceso, no hay
     nada que copiar y está respondido de una vez.
  C) SI HAY EXCESO, ¿QUÉ HACEN DISTINTO? — las wallets con z alto frente al resto, en: comprar vs vender,
     zona de precio, momento de la ventana, si cierran planas (spread) o aguantan (selección) y — la
     verdadera "selección" — EN CUÁNTAS ventanas participan y cuántas se saltan.

⚠ La cinta global va MUESTREADA (limit=200 cada ~10-20 s), así que de cada wallet vemos un subconjunto y en
los momentos de mucha actividad se pierden operaciones. Eso resta potencia y sesga hacia los ratos tranquilos,
pero no inventa exceso en la cola: si aparece, es real. Se reporta la cobertura frente a wintrades.

    cd ~/polymarket-btc-up-down/research && python3 whowins.py
"""
import csv, os, sys, glob, math, time

DIR = os.path.join(os.path.dirname(__file__), "lab")


def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def sd(xs):
    if len(xs) < 2: return 0.0
    m = mean(xs); return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
def cdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def main():
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]
    print(f"resoluciones en caché: {len(reso)}", flush=True)

    # POS[(wallet, cid, outcome)] = [acciones netas, efectivo, n ops, ts mínimo, slug]
    POS = {}
    seen = set(); nrow = 0
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
                    ts = int(float(r["ts_trade"])); px = float(r["price"]); sz = float(r["size"] or 0)
                except Exception: continue
                if sz <= 0: continue
                nrow += 1
                buy = (r.get("trade_side") or "").upper().startswith("B")
                a = POS.setdefault((wal, r.get("cid"), oc), [0.0, 0.0, 0, ts, slug])
                a[0] += sz if buy else -sz
                a[1] += (-px * sz) if buy else (px * sz)
                a[2] += 1
                a[3] = min(a[3], ts)
    print(f"operaciones únicas en la cinta global: {nrow} · posiciones: {len(POS)}", flush=True)
    ncomp = sum(1 for p in glob.glob(os.path.join(DIR, "wintrades_*.csv")))
    print(f"(cinta completa por ventana en {ncomp} ficheros wintrades_*; la global va muestreada)", flush=True)
    if not POS:
        print("sin datos de cinta con wallet — ¿existe tape_*.csv?"); return

    # P&L por posición
    W = {}     # wallet -> {pnl: [...], vol, nwin: set, buys, sells, px: [], flat, hold, slugs:set}
    for (wal, cid, oc), (sh, cash, n, ts, slug) in POS.items():
        win = reso.get(cid)
        if win not in ("Up", "Down"): continue
        pnl = cash + sh * (1.0 if win == oc else 0.0)
        nominal = abs(cash) if abs(cash) > 1e-9 else abs(sh)
        w = W.setdefault(wal, {"pnl": [], "vol": 0.0, "win": set(), "buys": 0, "sells": 0,
                               "flat": 0, "hold": 0, "n": 0, "px": []})
        w["pnl"].append(pnl); w["vol"] += nominal; w["win"].add(cid); w["n"] += n
        if abs(sh) < 1e-6: w["flat"] += 1
        else: w["hold"] += 1
        if sh > 0: w["buys"] += 1
        elif sh < 0: w["sells"] += 1
        if n: w["px"].append(abs(cash) / max(abs(sh), 1e-9) if abs(sh) > 1e-9 else 0.5)

    tot_pnl = sum(sum(w["pnl"]) for w in W.values())
    print(f"wallets: {len(W)} · ventanas distintas: {len(set(c for _, c, _ in POS))} · "
          f"P&L agregado ${tot_pnl:,.0f}", flush=True)

    rk = sorted(W.items(), key=lambda kv: -sum(kv[1]["pnl"]))
    print("\n" + "=" * 104)
    print("  A) CENSO: las 12 wallets más rentables (P&L reconstruido posición a posición)")
    print("=" * 104)
    print(f"  {'wallet':>14}{'posiciones':>12}{'ventanas':>10}{'P&L $':>12}{'$/posición':>12}"
          f"{'compra%':>9}{'planas%':>9}{'z':>7}")
    for wal, w in rk[:12]:
        p = w["pnl"]; s = sd(p)
        z = (mean(p) / (s / math.sqrt(len(p)))) if (s > 0 and len(p) > 1) else float("nan")
        print(f"  {wal[:12]:>14}{len(p):>12}{len(w['win']):>10}{sum(p):>12,.0f}{mean(p):>12.2f}"
              f"{100*w['buys']/max(1,w['buys']+w['sells']):>8.0f}%"
              f"{100*w['flat']/max(1,len(p)):>8.0f}%{z:>+7.2f}")
    pos = [w for _, w in rk if sum(w["pnl"]) > 0]
    top10 = sum(sum(w["pnl"]) for _, w in rk[:10])
    print(f"\n  rentables: {len(pos)}/{len(W)} ({100*len(pos)/len(W):.0f}%) · "
          f"las 10 primeras se llevan ${top10:,.0f} "
          f"({100*top10/tot_pnl if tot_pnl else float('nan'):.0f}% del beneficio agregado)")

    # ---------- B) habilidad o supervivencia ----------
    for MINP in (20, 50):
        cand = [(wal, w) for wal, w in W.items() if len(w["pnl"]) >= MINP and sd(w["pnl"]) > 0]
        if len(cand) < 20: continue
        zs = [mean(w["pnl"]) / (sd(w["pnl"]) / math.sqrt(len(w["pnl"]))) for _, w in cand]
        N = len(zs)
        print("\n" + "=" * 104)
        print(f"  B) ¿HABILIDAD O SUPERVIVENCIA? · wallets con ≥{MINP} posiciones (n={N})")
        print("=" * 104)
        print(f"  {'umbral':>10}{'observadas':>13}{'esperadas por azar':>21}{'exceso':>10}{'veces':>8}")
        for thr in (1.0, 1.5, 2.0, 2.5, 3.0):
            obs = sum(1 for z in zs if z > thr)
            exp = N * (1 - cdf(thr))
            print(f"  {f'z > +{thr}':>10}{obs:>13}{exp:>21.1f}{obs-exp:>+10.1f}"
                  f"{(obs/exp if exp > 0.3 else float('nan')):>8.1f}")
        neg = sum(1 for z in zs if z < -2.0); pos2 = sum(1 for z in zs if z > 2.0)
        print(f"  simetría (control): z < −2,0 → {neg} · z > +2,0 → {pos2}  "
              f"(si son parecidos, es dispersión, no habilidad)")
        print(f"  media de los z {mean(zs):+.2f} · desviación {sd(zs):.2f}  "
              f"(el azar daría media 0 y desviación 1)")

        # ---------- C) ¿qué hacen distinto? ----------
        skilled = [w for (_, w), z in zip(cand, zs) if z > 2.0]
        rest = [w for (_, w), z in zip(cand, zs) if z <= 2.0]
        if len(skilled) >= 5 and len(rest) >= 20:
            nwin_all = len(set(c for _, c, _ in POS))
            print(f"\n  C) QUÉ HACEN DISTINTO · {len(skilled)} con z>+2 frente a {len(rest)} del resto")
            print(f"  {'rasgo':>26}{'z>+2':>12}{'resto':>12}")
            rows = [
                ("posiciones (mediana)", lambda g: sorted(len(w["pnl"]) for w in g)[len(g) // 2]),
                ("ventanas tocadas (mediana)", lambda g: sorted(len(w["win"]) for w in g)[len(g) // 2]),
                ("% de ventanas jugadas", lambda g: 100 * mean([len(w["win"]) / nwin_all for w in g])),
                ("compras % de posiciones", lambda g: 100 * mean(
                    [w["buys"] / max(1, w["buys"] + w["sells"]) for w in g])),
                ("cierran planas %", lambda g: 100 * mean([w["flat"] / max(1, len(w["pnl"])) for w in g])),
                ("ops por posición", lambda g: mean([w["n"] / max(1, len(w["pnl"])) for w in g])),
                ("$ por posición", lambda g: mean([mean(w["pnl"]) for w in g])),
                ("nominal medio $", lambda g: mean([w["vol"] / max(1, len(w["pnl"])) for w in g])),
            ]
            for nm, f in rows:
                try: print(f"  {nm:>26}{f(skilled):>12.1f}{f(rest):>12.1f}")
                except Exception: pass

    print("\nLECTURA: el bloque B decide. Si 'observadas' ≈ 'esperadas por azar' en todos los umbrales y la")
    print("cola izquierda pesa lo mismo que la derecha, entonces los ganadores de este mercado son")
    print("SUPERVIVIENTES y no hay selección que copiar — y queda respondido con datos, no por cansancio. Si")
    print("hay exceso claro en z>+2 y z>+3 y la cola izquierda NO lo acompaña, la habilidad existe, está")
    print("medida, y el bloque C dice por dónde mirar: ojo sobre todo a '% de ventanas jugadas' (la selección")
    print("de verdad puede ser CUÁNDO no operar) y a 'cierran planas' (spread) frente a aguantar (selección).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
