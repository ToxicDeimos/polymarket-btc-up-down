"""
cola.py — ESE +1,6 ¢ ¿SE COBRA POR SER RAPIDO O POR ESTAR PUESTO?

makers.py dejo medido que el edge de este mercado es EJECUCION: las wallets grandes compran 1,0-1,6
pp mas barato que sus contemporaneos y eso explica el 52-73% de su beneficio (correlacion +0,638).
Pero ese mismo numero sale de dos mecanismos OPUESTOS para nosotros:

  (a) SER EL PRIMERO EN EL TOQUE — el precio salta y lo cogen antes de que el libro recotice.
      Exige ~10 ms. Imposible desde una Pi (nuestra orden llega a 400 ms).
  (b) TENER UNA OFERTA PUESTA MAS ABAJO — la pusieron hace un minuto y el precio BAJO hasta ella.
      No exige velocidad ninguna: solo estar puesto y aguantar.

Se distinguen con dos huellas que van en sentidos contrarios:

  MARKOUT (lo que vale el token justo despues)
      (a) POSITIVO: compraron antes del movimiento, el precio sube detras de ellos.
      (b) NEGATIVO o plano: les llenan cuando el precio viene cayendo hacia su oferta — eso es
          seleccion adversa, el coste normal de estar puesto.

  PERCENTIL dentro del ultimo minuto (que fraccion del volumen de ese token se negocio MAS BARATO
  que su precio, en los 60 s previos)
      (a) ALTO: compran caro respecto al minuto anterior porque el precio acaba de saltar.
      (b) BAJO: compran en la parte barata del rango, que es donde esta su oferta esperando.

    cd ~/polymarket-btc-up-down/research && python3 cola.py [min_acciones]
"""
import bisect, csv, math, os, sys
from collections import defaultdict

csv.field_size_limit(10 ** 7)
DIR = os.path.dirname(os.path.abspath(__file__))
CINTA = os.path.join(DIR, "izzy_cinta2.csv")
TOK = os.path.join(DIR, "clob_tokens2.csv")
IZZY = "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2"
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
RADIO = 5


def main():
    gan = {}
    for r in csv.reader(open(TOK, encoding="utf-8", errors="replace")):
        if len(r) == 4: gan[(r[0], r[1])] = int(r[3])

    print("Leyendo la cinta…")
    todas = defaultdict(list)       # (cid, asset) -> [(ts, p, s, wal, es_compra)]
    n = 0
    for r in csv.reader(open(CINTA, encoding="utf-8", errors="replace")):
        if len(r) != 7: continue
        cid, ts, asset, side, p, s, wal = r
        if not asset or (cid, asset) not in gan: continue
        try: todas[(cid, asset)].append((int(ts), float(p), float(s), wal, side == "BUY"))
        except ValueError: pass
        n += 1
        if n % 200000 == 0: sys.stdout.write(f"\r  {n:,}"); sys.stdout.flush()
    print(f"\r  {n:,} operaciones")
    for k in todas: todas[k].sort()

    # wallet -> sh, pnl, mejora*sh, sh_mej, m5*sh, sh5, m30*sh, sh30, pctl*sh, sh_p
    w = defaultdict(lambda: [0.0] * 10)
    print("Midiendo markout y percentil…")
    for (cid, asset), v in todas.items():
        g = gan[(cid, asset)]
        ts = [x[0] for x in v]
        for t, p, s, wal, es_compra in v:
            if not es_compra: continue
            a = w[wal]; a[0] += s; a[1] += s * (g - p)

            # mejora frente a contemporaneos (otras COMPRAS a +-RADIO s)
            lo, hi = bisect.bisect_left(ts, t - RADIO), bisect.bisect_right(ts, t + RADIO)
            ps = sz = 0.0
            for j in range(lo, hi):
                if v[j][3] == wal or not v[j][4]: continue
                ps += v[j][1] * v[j][2]; sz += v[j][2]
            if sz > 0: a[2] += s * (ps / sz - p); a[3] += s

            # markout: a que precio se negocia el token despues
            for (d0, d1, iv, isz) in ((3, 8, 4, 5), (25, 35, 6, 7)):
                lo2, hi2 = bisect.bisect_left(ts, t + d0), bisect.bisect_right(ts, t + d1)
                ps2 = sz2 = 0.0
                for j in range(lo2, hi2):
                    ps2 += v[j][1] * v[j][2]; sz2 += v[j][2]
                if sz2 > 0: a[iv] += s * (ps2 / sz2 - p); a[isz] += s

            # percentil dentro del minuto anterior
            lo3 = bisect.bisect_left(ts, t - 60)
            bajo = tot = 0.0
            for j in range(lo3, bisect.bisect_left(ts, t)):
                tot += v[j][2]
                if v[j][1] < p: bajo += v[j][2]
            if tot > 0: a[8] += s * (bajo / tot); a[9] += s

    gr = [(k, a) for k, a in w.items() if a[0] >= MIN and a[3] > 0 and a[9] > 0]
    gr.sort(key=lambda x: -x[1][0])
    print(f"\n{len(gr)} wallets con >={MIN:,} acciones\n")

    print("=" * 96)
    print("  ¿RAPIDOS (a) O PUESTOS (b)?  —  markout + = compraron antes del movimiento")
    print("=" * 96)
    print(f"  {'wallet':<14}{'acciones':>11}{'gana pp':>10}{'mejora':>9}"
          f"{'m5':>9}{'m30':>9}{'pctl 60s':>10}   lectura")
    for k, a in gr[:18]:
        mej = 100 * a[2] / a[3]; ren = 100 * a[1] / a[0]
        m5 = 100 * a[4] / a[5] if a[5] else float("nan")
        m30 = 100 * a[6] / a[7] if a[7] else float("nan")
        pc = a[8] / a[9]
        lec = ""
        if mej > 0.5:
            lec = "RAPIDO (a)" if m5 > 0.3 else ("PUESTO (b)" if m5 < 0 else "mixto")
        marca = " ←izzy" if k == IZZY else ""
        print(f"  {k[:12]:<14}{a[0]:>11,.0f}{ren:>10.2f}{mej:>9.2f}"
              f"{m5:>9.2f}{m30:>9.2f}{pc:>10.2f}   {lec}{marca}")

    # los que de verdad cobran la mejora
    cob = [(k, a) for k, a in gr if 100 * a[2] / a[3] > 0.5]
    if cob:
        sh = sum(a[0] for _, a in cob); s5 = sum(a[5] for _, a in cob)
        s30 = sum(a[7] for _, a in cob); sp = sum(a[9] for _, a in cob)
        print(f"\n  LAS {len(cob)} QUE COBRAN LA MEJORA (>0,5 pp) JUNTAS:")
        print(f"    rentabilidad {100*sum(a[1] for _, a in cob)/sh:+.2f} pp · "
              f"mejora {100*sum(a[2] for _, a in cob)/sum(a[3] for _, a in cob):+.2f} pp")
        print(f"    markout 5 s {100*sum(a[4] for _, a in cob)/s5:+.2f} pp · "
              f"30 s {100*sum(a[6] for _, a in cob)/s30:+.2f} pp")
        print(f"    percentil en el minuto previo {sum(a[8] for _, a in cob)/sp:.2f} "
              f"(0 = compran lo mas barato del minuto, 1 = lo mas caro)")
    print("\n" + "=" * 96)
    print("  markout NEGATIVO + percentil BAJO  → (b) ESTAN PUESTOS: no necesitan velocidad,")
    print("     les llenan cuando el precio baja hasta su oferta. Eso la Pi SI puede hacerlo.")
    print("  markout POSITIVO + percentil ALTO  → (a) SON RAPIDOS: cogen la cotizacion vieja")
    print("     antes de que recotice, y eso exige los 10 ms que no tenemos.")
    print("=" * 96)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
