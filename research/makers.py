"""
makers.py — ¿LO QUE GANAN LOS HABITUALES ES SIMPLEMENTE QUE NO PAGAN EL PEAJE?

persiste.py + dinero.py dejaron esto: SI hay wallets positivas los dos meses, pero el ORDEN entre
ellas no se mantiene (el mejor de septiembre no es el mejor de octubre, correlacion ~0). Lo que
persiste es la CATEGORIA: habituales de mucho volumen, +1 a +2 pp/accion, constantes, mientras los
casuales pierden. No es habilidad individual, es posicion estructural.

Y la comision de taker a precio 0,50 es 0,07·0,5·0,5 = **1,75 pp**. Sospechosamente parecido.

Hipotesis: los habituales son MAKERS. No pagan la comision (en Polymarket la paga el taker) y
compran al bid en vez de al ask. Lo que ganan es el peaje de los demas, no una prediccion mejor.

Se mide con el mismo control emparejado de izzy.py, extendido a todas las wallets grandes:

    mejora = (precio medio que pagaron OTROS por el MISMO token en el MISMO instante) − (su precio)

    mejora > 0  → compra mas barato que sus contemporaneos = comportamiento de maker
    mejora ≈ 0  → paga lo que todos

Y entonces la pregunta que lo cierra: **¿explica la mejora de precio su rentabilidad?** Si las
wallets con mas mejora son las que mas ganan, el edge es EJECUCION (prioridad de cola, inalcanzable
para nosotros, ver mm-market-making-pivot). Si no hay relacion, el edge es otra cosa y hay que
seguir buscando.

⚠ El P&L aqui es BRUTO (sin comisiones) para todos. Es lo correcto para comparar ejecucion, pero
  recuerda que un taker pagaria ademas 0,07·p·(1−p) y un maker no.

    cd ~/polymarket-btc-up-down/research && python3 makers.py [min_acciones]
"""
import bisect, csv, math, os, sys
from collections import defaultdict

csv.field_size_limit(10 ** 7)
DIR = os.path.dirname(os.path.abspath(__file__))
CINTA = os.path.join(DIR, "izzy_cinta2.csv")
TOK = os.path.join(DIR, "clob_tokens2.csv")
IZZY = "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2"
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
RADIO = 5          # segundos para considerar "el mismo instante"


def main():
    gan = {}
    for r in csv.reader(open(TOK, encoding="utf-8", errors="replace")):
        if len(r) == 4: gan[(r[0], r[1])] = int(r[3])

    print("Leyendo la cinta…")
    compras = defaultdict(list)          # (cid, asset) -> [(ts, p, s, wallet)]
    n = 0
    for r in csv.reader(open(CINTA, encoding="utf-8", errors="replace")):
        if len(r) != 7: continue
        cid, ts, asset, side, p, s, wal = r
        if not asset or not wal or side != "BUY": continue
        if (cid, asset) not in gan: continue
        try: compras[(cid, asset)].append((int(ts), float(p), float(s), wal))
        except ValueError: pass
        n += 1
        if n % 200000 == 0: sys.stdout.write(f"\r  {n:,}"); sys.stdout.flush()
    print(f"\r  {n:,} compras en {len(compras):,} token×ventana")
    for k in compras: compras[k].sort()

    # por wallet: acciones, pnl, mejora ponderada
    w = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])    # sh, pnl, mejora·sh, sh_con_gemelo
    print("Comparando cada compra con sus contemporaneas…")
    for (cid, asset), v in compras.items():
        g = gan[(cid, asset)]
        ts = [x[0] for x in v]
        for i, (t, p, s, wal) in enumerate(v):
            a = w[wal]
            a[0] += s; a[1] += s * (g - p)
            lo = bisect.bisect_left(ts, t - RADIO); hi = bisect.bisect_right(ts, t + RADIO)
            ps = sz = 0.0
            for j in range(lo, hi):
                if v[j][3] == wal: continue          # fuera sus propias compras
                ps += v[j][1] * v[j][2]; sz += v[j][2]
            if sz > 0:
                a[2] += s * (ps / sz - p); a[3] += s

    gr = [(k, a) for k, a in w.items() if a[0] >= MIN and a[3] > 0]
    gr.sort(key=lambda x: -x[1][0])
    print(f"\n{len(gr)} wallets con >={MIN:,} acciones compradas\n")
    if len(gr) < 10:
        print("muestra corta"); return

    print("=" * 88)
    print(f"  ¿COMPRAN MAS BARATO QUE SUS CONTEMPORANEOS? (gemelos a ±{RADIO} s)")
    print("=" * 88)
    print(f"  {'wallet':<14}{'acciones':>12}{'pp/accion':>12}{'mejora pp':>12}"
          f"{'% explicado':>13}")
    for k, a in gr[:25]:
        mej = 100 * a[2] / a[3]
        ren = 100 * a[1] / a[0]
        exp = f"{100*mej/ren:.0f}%" if abs(ren) > 0.2 else "—"
        marca = "  ← izzy" if k == IZZY else ""
        print(f"  {k[:12]:<14}{a[0]:>12,.0f}{ren:>12.2f}{mej:>12.2f}{exp:>13}{marca}")

    xs = [100 * a[2] / a[3] for _, a in gr]           # mejora
    ys = [100 * a[1] / a[0] for _, a in gr]           # rentabilidad
    N = len(xs); mx, my = sum(xs) / N, sum(ys) / N
    sx = math.sqrt(sum((v - mx) ** 2 for v in xs)); sy = math.sqrt(sum((v - my) ** 2 for v in ys))
    r = sum((p - mx) * (q - my) for p, q in zip(xs, ys)) / (sx * sy) if sx and sy else 0

    sh = sum(a[0] for _, a in gr); shg = sum(a[3] for _, a in gr)
    print(f"\n  CONJUNTO de las {N}: {100*sum(a[1] for _, a in gr)/sh:+.2f} pp/accion"
          f"  ·  mejora de precio {100*sum(a[2] for _, a in gr)/shg:+.2f} pp")
    print(f"  correlacion entre MEJORA DE PRECIO y RENTABILIDAD: {r:+.3f}"
          f"   (ruido ±{1.96/math.sqrt(N-3):.3f})")
    print("\n" + "=" * 88)
    print("  Si la mejora de precio es ~0 y la rentabilidad +1/+2, NO son makers comprando")
    print("  barato: ganan por otra via. Si la mejora explica la rentabilidad, el edge es")
    print("  EJECUCION y exige la prioridad de cola que ya medimos que no tenemos.")
    print("=" * 88)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
