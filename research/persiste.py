"""
persiste.py — EN ESTE MERCADO, ¿EL GANADOR DE UN MES GANA EL SIGUIENTE?

Es la pregunta de raiz, y nunca la contestamos. En agosto fichamos 6 "ganadores" buscando quien
habia ganado; siguen.py mostro que 5 se fueron y solo izzyaussie sigue y mejora. Pero quedarse con
el unico que sigue vivo es casi circular: **"sigue operando" ya es un filtro de ganadores**, porque
el que pierde lo deja. El z de izzyaussie (+2,35) corregido por haber mirado 6 wallets se queda en
p≈0,11 — no establecido.

survivorship.py se escribio en agosto para zanjarlo con un nulo de Monte Carlo, y nunca pudo correr:
necesitaba la cinta COMPLETA (todas las wallets), que murio con la SD. izzy.py la ha vuelto a
descargar sin querer: 832.606 operaciones de 472 ventanas, con la wallet de cada una.

El test limpio, que elimina la supervivencia POR CONSTRUCCION:

    ordenar TODAS las wallets por su resultado en SEPTIEMBRE   (train)
                              ↓
    medir esas MISMAS wallets en OCTUBRE                       (test)

Nadie se elige por el resultado del test. Si los mejores de septiembre tambien ganan en octubre, hay
habilidad persistente en este mercado. Si salen aleatorios, no la hay — y entonces izzyaussie es el
boleto premiado de una loteria, no un metodo.

Metrica por wallet: pp por accion = Σ pnl / Σ acciones.
    BUY  de O a precio p: pnl = s·(gano − p)      SELL: pnl = s·(p − gano)
Bruto, sin comisiones (el maker no paga; el taker pagaria 0,07·p·(1−p), igual para todos).

⚠ El universo son las 472 ventanas que opero izzyaussie, no todas las del mercado. Es el mismo
  universo para todas las wallets, asi que la comparacion es justa, pero no es el mercado entero.

    cd ~/polymarket-btc-up-down/research && python3 persiste.py [min_acciones]
"""
import csv, os, sys, time, math
from collections import defaultdict

csv.field_size_limit(10 ** 7)
DIR = os.path.dirname(os.path.abspath(__file__))
CINTA = os.path.join(DIR, "izzy_cinta2.csv")
TOK = os.path.join(DIR, "clob_tokens2.csv")
IZZY = "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2"
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 300
CORTE = "2026-10-01"


def main():
    corte = int(time.mktime(time.strptime(CORTE, "%Y-%m-%d")) - time.timezone)

    gan = {}
    for r in csv.reader(open(TOK, encoding="utf-8", errors="replace")):
        if len(r) == 4:
            gan[(r[0], r[1])] = int(r[3])
    print(f"ganadores conocidos: {len(gan)} tokens")

    # wallet -> [pnl_tr, sh_tr, pnl_te, sh_te, n_tr, n_te]
    w = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0, 0])
    n = 0
    for r in csv.reader(open(CINTA, encoding="utf-8", errors="replace")):
        if len(r) != 7: continue
        cid, ts, asset, side, p, s, wal = r
        if not asset or not wal: continue
        g = gan.get((cid, asset))
        if g is None: continue
        try: ts, p, s = int(ts), float(p), float(s)
        except ValueError: continue
        pnl = s * (g - p) if side == "BUY" else s * (p - g)
        a = w[wal]
        if ts < corte: a[0] += pnl; a[1] += s; a[4] += 1
        else:          a[2] += pnl; a[3] += s; a[5] += 1
        n += 1
        if n % 200000 == 0:
            sys.stdout.write(f"\r  {n:,}"); sys.stdout.flush()
    print(f"\r  {n:,} operaciones · {len(w):,} wallets distintas")

    for m in (100, 300, 1000, 3000):
        k = sum(1 for a in w.values() if a[1] >= m and a[3] >= m)
        print(f"  con >={m:>5} acciones en AMBOS periodos: {k:>5} wallets")

    elegibles = [(k, a) for k, a in w.items() if a[1] >= MIN and a[3] >= MIN]
    if len(elegibles) < 20:
        print(f"\nsolo {len(elegibles)} wallets con >={MIN} acciones en ambos: muestra corta")
        return
    datos = [(k, 100 * a[0] / a[1], 100 * a[2] / a[3], a[1], a[3]) for k, a in elegibles]
    datos.sort(key=lambda x: x[1])                      # por el resultado de SEPTIEMBRE
    N = len(datos)
    print(f"\n{N} wallets operaron >={MIN} acciones en septiembre Y en octubre")

    print("\n" + "=" * 84)
    print("  ¿PERSISTE? — wallets ordenadas por SEPTIEMBRE, resultado en OCTUBRE")
    print("=" * 84)
    print(f"  {'quintil (por septiembre)':<28}{'n':>5}{'sep pp/acc':>13}{'OCT pp/acc':>13}"
          f"{'acc oct':>12}")
    for q in range(5):
        tr = datos[q * N // 5:(q + 1) * N // 5]
        s_tr = sum(x[3] for x in tr); s_te = sum(x[4] for x in tr)
        p_tr = sum(x[1] * x[3] for x in tr) / s_tr
        p_te = sum(x[2] * x[4] for x in tr) / s_te
        eti = ["Q1 (los peores)", "Q2", "Q3", "Q4", "Q5 (los mejores)"][q]
        print(f"  {eti:<28}{len(tr):>5}{p_tr:>13.2f}{p_te:>13.2f}{s_te:>12,.0f}")

    # correlacion simple entre el resultado de septiembre y el de octubre
    xs = [x[1] for x in datos]; ys = [x[2] for x in datos]
    mx, my = sum(xs) / N, sum(ys) / N
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs)); sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    r = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (sx * sy) if sx and sy else 0
    # y por RANGOS, que no se deja arrastrar por un valor extremo
    rx = {v: i for i, v in enumerate(sorted(xs))}; ry = {v: i for i, v in enumerate(sorted(ys))}
    ax = [rx[v] for v in xs]; ay = [ry[v] for v in ys]
    max_, may = sum(ax) / N, sum(ay) / N
    sax = math.sqrt(sum((a - max_) ** 2 for a in ax)); say = math.sqrt(sum((b - may) ** 2 for b in ay))
    rr = sum((a - max_) * (b - may) for a, b in zip(ax, ay)) / (sax * say) if sax and say else 0
    print(f"\n  correlacion septiembre→octubre: {r:+.3f}   ·   por rangos: {rr:+.3f}")
    print(f"  (±{1.96/math.sqrt(N-3):.3f} seria el margen de ruido con {N} wallets)")

    print("\n  LOS 12 MEJORES DE SEPTIEMBRE, Y QUE HICIERON EN OCTUBRE")
    print(f"  {'wallet':<14}{'sep pp/acc':>12}{'acc sep':>11}{'OCT pp/acc':>13}{'acc oct':>11}")
    for k, a, b, s1, s2 in sorted(datos, key=lambda x: -x[1])[:12]:
        marca = "  ← izzyaussie" if k == IZZY else ""
        print(f"  {k[:12]:<14}{a:>12.2f}{s1:>11,.0f}{b:>13.2f}{s2:>11,.0f}{marca}")

    izz = [d for d in datos if d[0] == IZZY]
    if izz:
        k, a, b, s1, s2 = izz[0]
        pos = sorted(datos, key=lambda x: -x[1]).index(izz[0]) + 1
        print(f"\n  izzyaussie: puesto {pos} de {N} en septiembre ({a:+.2f}), "
              f"octubre {b:+.2f}")

    print("\n" + "=" * 84)
    print("  Si Q5 gana en octubre y Q1 pierde, hay HABILIDAD y es persistente.")
    print("  Si los cinco quintiles salen parecidos, el resultado de un mes no dice nada del")
    print("  siguiente: los 'ganadores' son boletos premiados y no hay metodo que copiar.")
    print("=" * 84)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
