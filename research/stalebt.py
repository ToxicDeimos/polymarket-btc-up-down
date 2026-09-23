"""
stalebt.py — SIMULACIÓN REALISTA: ¿sigue ahí el ask cuando llegamos? booklag confirmó el mecanismo con dos
medidas independientes que dan el mismo número:
  · tras un salto de BTC, a los 200 ms todavía QUEDAN +4,04pp por recorrer al libro (pico de correlación
    cruzada en 0,20 s), y restando medio spread por cruzar salen ~3,5pp;
  · la wallet grande (9.366 operaciones, z +3,26 y +3,20 en dos mitades) bate el precio que paga en +3,0pp,
    PLANO en las siete franjas de precio.
Un camino mira el P&L de un wallet y el otro cronometra dos relojes. Mismo número ⇒ es el mecanismo.

Lo que booklag NO puede decir: que el medio se mueva no significa que haya algo que comprar. El maker puede
CANCELAR en vez de dejarse levantar. Aquí se reconstruye el libro entero desde los eventos del WSS (fotos B
+ cambios C) y se simula de verdad:

    salto de BTC en t  →  esperamos NUESTRA latencia L  →  miramos el mejor ask que EXISTE en ese instante
    y su TAMAÑO  →  compramos lo que haya  →  marcamos contra el medio ya asentado (t+8 s), neto de comisión.

Se barre la latencia (100 ms … 1 s) para ver el presupuesto real, y el tamaño disponible para saber si el
negocio es de céntimos o de dólares. Control: la misma regla comprando el token CONTRARIO debe perder.

    cd ~/polymarket-btc-up-down/research && python3 stalebt.py
"""
import csv, os, sys, glob, math, bisect
from array import array

DIR = os.path.dirname(__file__)
LAT = (0.1, 0.2, 0.3, 0.5, 0.75, 1.0)
JUMPS = (5.0, 10.0)
JW = 1.0            # ventana del salto, s
SETTLE = 8.0        # cuándo consideramos el libro ya asentado, s
COOL = 10.0         # separación mínima entre disparos, s


def fee(p): return 0.07 * p * (1 - p)
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def med(xs):
    s = sorted(xs); return s[len(s) // 2] if s else float("nan")


def main():
    paths = sorted(glob.glob(os.path.join(DIR, "queue_events_*.csv")))
    if not paths: print("no hay queue_events_*.csv"); return
    SP = []
    EV = {}          # ws -> lista de eventos (t, tok, typ, side, price, size)
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try: t = float(r["ts"]); typ = r["typ"]
                except Exception: continue
                if typ == "S":
                    try: SP.append((t, float(r["price"])))
                    except Exception: pass
                elif typ in ("B", "C"):
                    try:
                        ws = int(r["ws"]); px = float(r["price"]); sz = float(r["size"] or 0)
                    except Exception: continue
                    sd = (r.get("side") or "").lower()
                    side = "bid" if sd.startswith(("b", "buy")) else "ask"
                    bb = r.get("bb"); ba = r.get("ba")
                    try: bb = float(bb) if bb else None
                    except Exception: bb = None
                    try: ba = float(ba) if ba else None
                    except Exception: ba = None
                    EV.setdefault(ws, []).append((t, r["tok"], side, px, sz, bb, ba))
    SP.sort()
    for ws in EV: EV[ws].sort()
    print(f"spot: {len(SP):,} · ventanas: {len(EV)} · eventos: {sum(len(v) for v in EV.values()):,}",
          flush=True)
    if len(SP) < 2000: print("muestra corta — dejar grabando más"); return
    sts = array("d", [t for t, _ in SP]); spx = array("d", [p for _, p in SP])

    def sp_at(t):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= 2.0) else None

    # ---------- serie (ts, bid, ask, tamaño del ask) por token, UNA sola pasada por ventana ----------
    # El mejor bid/ask NO se reconstruye: viene en cada evento (bb/ba) del propio exchange. Reconstruirlo
    # fallaba porque el filtro de banda de queuewatch deja de actualizar los niveles que se alejan y estos
    # se quedan congelados con tamaño >0, apareciendo bids fantasma por encima del ask.
    # El libro solo se usa para saber el TAMAÑO que hay en el nivel del mejor ask.
    SER = {}

    def series(ws):
        s = SER.get(ws)
        if s is not None: return s
        lv = {"Up": {}, "Down": {}}                  # tok -> precio -> tamaño (solo asks)
        cur = {"Up": [None, None], "Down": [None, None]}
        out = {"Up": [], "Down": []}
        for t, tok, side, px, sz, bb, ba in EV[ws]:
            if side == "ask": lv[tok][px] = sz
            if bb is not None: cur[tok][0] = bb
            if ba is not None: cur[tok][1] = ba
            b, a = cur[tok]
            if b is None or a is None or not (0 < b < a < 1): continue
            out[tok].append((t, b, a, lv[tok].get(a, 0.0)))
        SER[ws] = out
        return out

    def at(ws, tok, t):
        ser = series(ws)[tok]
        if not ser: return None
        i = bisect.bisect_right(ser, (t, 9, 9, 9)) - 1
        if i < 0 or t - ser[i][0] > 5.0: return None
        _, b, a, asz = ser[i]
        return (b, a, asz, (b + a) / 2)

    # ---------- disparos ----------
    for J in JUMPS:
        FIRE = []          # (ws, t, tok favorecido)
        for ws, ev in EV.items():
            t0 = ev[0][0]; t1 = ev[-1][0]
            i = bisect.bisect_left(sts, t0); last = -99
            while i < len(sts) and sts[i] < t1 - SETTLE:
                t = sts[i]; a = sp_at(t - JW)
                if a is not None and abs(spx[i] - a) >= J and t - last > COOL:
                    FIRE.append((ws, t, "Up" if spx[i] > a else "Down")); last = t
                i += 1
        if len(FIRE) < 25:
            print(f"\nsaltos ≥${J:.0f}: solo {len(FIRE)} — muestra corta"); continue
        bywin = {}
        for ws, t, tok in FIRE: bywin.setdefault(ws, []).append((t, tok))
        print("\n" + "=" * 104)
        print(f"  SALTOS DE BTC ≥${J:.0f} en {JW:.0f}s · n={len(FIRE)} · comprar el lado favorecido L después")
        print("=" * 104)
        print(f"  {'latencia':>10}{'disparos':>10}{'con ask':>9}{'ask':>7}{'tam.':>7}"
              f"{'medio {:.0f}s'.format(SETTLE):>11}{'bruto':>8}{'neto':>8}{'espejo':>8}")
        for L in LAT:
            rows = []; mir = []
            for ws, lst in bywin.items():
                for t, tok in lst:
                    other = "Down" if tok == "Up" else "Up"
                    for who, dst in ((tok, rows), (other, mir)):
                        q = at(ws, who, t + L); r2 = at(ws, who, t + SETTLE)
                        if not q or not r2: continue
                        dst.append({"ask": q[1], "sz": q[2], "end": r2[3]})
            if len(rows) < 20: continue
            g = mean([r["end"] - r["ask"] for r in rows])
            n = mean([r["end"] - r["ask"] - fee(r["ask"]) for r in rows])
            gm = mean([r["end"] - r["ask"] - fee(r["ask"]) for r in mir]) if len(mir) >= 20 else float("nan")
            print(f"  {L:>8.2f}s{len(bywin):>10}{len(rows):>9}{mean([r['ask'] for r in rows]):>7.3f}"
                  f"{med([r['sz'] for r in rows]):>7.0f}{mean([r['end'] for r in rows]):>11.3f}"
                  f"{100*g:>+8.2f}{100*n:>+8.2f}{100*gm:>+8.2f}")

    print("\nLECTURA: la columna 'neto' es lo que quedaría de verdad a cada latencia, ya con la comisión y")
    print("comprando el ask que REALMENTE existía en ese instante (si el maker canceló, no hay fila). 'tam.' es")
    print("el tamaño mediano disponible: multiplicado por 'neto' da los dólares por disparo, que es lo que")
    print("decide si merece la pena montarlo. El 'espejo' tiene que salir claramente negativo; si también gana,")
    print("estamos midiendo la deriva del libro y no el retraso. Y ojo: esto marca contra el medio asentado,")
    print("no contra la resolución — mide el mecanismo, no el resultado final de la apuesta.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
