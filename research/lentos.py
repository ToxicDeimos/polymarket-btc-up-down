"""
lentos.py — ¿EXISTE UNA CLASE DE GANADORES QUE NO CORRE? (la unica que estaria a nuestro alcance)

cola.py dejo el cierre fisico: las 35 wallets que cobran la mejora de precio tienen markout a 5 s
+2,37 ≈ su beneficio total +2,34 ⇒ son RAPIDAS, le quitan al libro la cotizacion vieja antes de que
recotice, y eso exige llegar en ~170 ms cuando nosotros llegamos en 390 (320 intocables).

Pero en esa tabla habia UNA que no encajaba: 0x9e3ed7b661, 144.425 acciones, +2,13 pp, markout
**−0,32** y percentil 0,31 — perfil de estar PUESTA, no de correr. Si gana sin velocidad, es lo
unico que la Pi podria ejecutar.

Perseguir a la excepcion de una lista es exactamente lo que nos ha costado dos meses (izzyaussie).
Asi que aqui NO se persigue a una: se busca la CLASE entera y se le pasa la misma puerta que mato a
todo lo demas — **seleccionar en septiembre y medir en octubre**.

    LENTAS  = markout a 5 s <= UMBRAL_M5  (no cobran la cotizacion vieja)
    RAPIDAS = el resto

  · si las LENTAS rentables de septiembre siguen rentables en octubre y son varias → hay una clase
    alcanzable, y toca averiguar que hacen.
  · si no persisten, o si solo hay una, es ruido y el mercado queda cerrado de verdad.

El markout excluye las operaciones propias de la wallet (una wallet grande comprando en serie
empujaria su propio markout hacia arriba).

    cd ~/polymarket-btc-up-down/research && python3 lentos.py [min_acciones]
"""
import bisect, csv, math, os, sys, time
from collections import defaultdict

csv.field_size_limit(10 ** 7)
DIR = os.path.dirname(os.path.abspath(__file__))
CINTA = os.path.join(DIR, "izzy_cinta2.csv")
TOK = os.path.join(DIR, "clob_tokens2.csv")
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
RADIO = 5
UMBRAL_M5 = 0.3          # pp; por debajo de esto no estan cobrando la cotizacion vieja
CORTE = "2026-10-01"

# indices del acumulador
SH, PNL, MEJ, SMEJ, M5, S5, M30, S30, PCT, SPCT, PRE, TREL, STR = range(13)


def main():
    corte = int(time.mktime(time.strptime(CORTE, "%Y-%m-%d")) - time.timezone)
    gan = {}
    for r in csv.reader(open(TOK, encoding="utf-8", errors="replace")):
        if len(r) == 4: gan[(r[0], r[1])] = int(r[3])

    print("Leyendo la cinta…")
    todas = defaultdict(list)
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

    # ws de cada ventana: el minimo ts redondeado a multiplo de 300 va justo
    ws_de = {}
    for (cid, _), v in todas.items():
        if cid not in ws_de: ws_de[cid] = (v[0][0] // 300) * 300

    w = defaultdict(lambda: [[0.0] * 13, [0.0] * 13])      # wallet -> [septiembre, octubre]
    print("Midiendo…")
    for (cid, asset), v in todas.items():
        g = gan[(cid, asset)]; ts = [x[0] for x in v]; ws = ws_de[cid]
        for t, p, s, wal, es_compra in v:
            if not es_compra: continue
            a = w[wal][0 if t < corte else 1]
            a[SH] += s; a[PNL] += s * (g - p)
            a[PRE] += s * p; a[TREL] += s * (t - ws); a[STR] += s

            lo, hi = bisect.bisect_left(ts, t - RADIO), bisect.bisect_right(ts, t + RADIO)
            ps = sz = 0.0
            for j in range(lo, hi):
                if v[j][3] == wal or not v[j][4]: continue
                ps += v[j][1] * v[j][2]; sz += v[j][2]
            if sz > 0: a[MEJ] += s * (ps / sz - p); a[SMEJ] += s

            for d0, d1, iv, isz in ((3, 8, M5, S5), (25, 35, M30, S30)):
                lo2, hi2 = bisect.bisect_left(ts, t + d0), bisect.bisect_right(ts, t + d1)
                ps2 = sz2 = 0.0
                for j in range(lo2, hi2):
                    if v[j][3] == wal: continue          # fuera sus propias compras
                    ps2 += v[j][1] * v[j][2]; sz2 += v[j][2]
                if sz2 > 0: a[iv] += s * (ps2 / sz2 - p); a[isz] += s

            lo3 = bisect.bisect_left(ts, t - 60)
            bajo = tot = 0.0
            for j in range(lo3, bisect.bisect_left(ts, t)):
                tot += v[j][2]
                if v[j][1] < p: bajo += v[j][2]
            if tot > 0: a[PCT] += s * (bajo / tot); a[SPCT] += s

    def pp(a, iv, isz): return 100 * a[iv] / a[isz] if a[isz] > 0 else float("nan")

    el = [(k, s, o) for k, (s, o) in w.items()
          if s[SH] >= MIN and o[SH] >= MIN / 4 and s[S5] > 0 and o[S5] > 0]
    print(f"\n{len(el)} wallets con >={MIN:,} acciones en septiembre y >={MIN//4:,} en octubre\n")
    if len(el) < 8:
        print("muestra corta para clasificar"); return

    lentas = [x for x in el if pp(x[1], M5, S5) <= UMBRAL_M5]
    rapidas = [x for x in el if pp(x[1], M5, S5) > UMBRAL_M5]

    print("=" * 94)
    print(f"  LAS DOS CLASES (clasificadas por su markout de SEPTIEMBRE)")
    print("=" * 94)
    print(f"  {'clase':<22}{'n':>5}{'acc sep':>12}{'sep pp':>9}{'m5 sep':>9}"
          f"{'| acc oct':>12}{'OCT pp':>9}{'m5 oct':>9}")
    for nom, gr in (("RAPIDAS (m5 > 0,3)", rapidas), ("LENTAS (m5 <= 0,3)", lentas)):
        if not gr: continue
        ss = sum(x[1][SH] for x in gr); so = sum(x[2][SH] for x in gr)
        print(f"  {nom:<22}{len(gr):>5}{ss:>12,.0f}"
              f"{100*sum(x[1][PNL] for x in gr)/ss:>9.2f}"
              f"{100*sum(x[1][M5] for x in gr)/sum(x[1][S5] for x in gr):>9.2f}"
              f"{so:>12,.0f}{100*sum(x[2][PNL] for x in gr)/so:>9.2f}"
              f"{100*sum(x[2][M5] for x in gr)/sum(x[2][S5] for x in gr):>9.2f}")

    # la puerta: LENTAS y RENTABLES en septiembre -> ¿siguen en octubre?
    buenas = [x for x in lentas if 100 * x[1][PNL] / x[1][SH] > 0.5]
    print("\n" + "=" * 94)
    print(f"  LA PUERTA: wallets LENTAS y RENTABLES en septiembre (>+0,5 pp) → octubre")
    print("=" * 94)
    if not buenas:
        print("  NINGUNA. No existe la clase: todo el que gana, corre.")
    else:
        print(f"  {'wallet':<14}{'acc sep':>11}{'sep pp':>9}{'m5':>8}{'pctl':>7}"
              f"{'precio':>8}{'seg':>6}{'| acc oct':>11}{'OCT pp':>9}")
        for k, s, o in sorted(buenas, key=lambda x: -x[1][SH]):
            print(f"  {k[:12]:<14}{s[SH]:>11,.0f}{100*s[PNL]/s[SH]:>9.2f}"
                  f"{pp(s, M5, S5):>8.2f}{s[PCT]/s[SPCT]:>7.2f}"
                  f"{s[PRE]/s[STR]:>8.3f}{s[TREL]/s[STR]:>6.0f}"
                  f"{o[SH]:>11,.0f}{100*o[PNL]/o[SH]:>9.2f}")
        ss = sum(x[1][SH] for x in buenas); so = sum(x[2][SH] for x in buenas)
        pos = sum(1 for x in buenas if x[2][PNL] > 0)
        print(f"\n  JUNTAS: septiembre {100*sum(x[1][PNL] for x in buenas)/ss:+.2f} pp "
              f"({ss:,.0f} acc)  →  OCTUBRE "
              f"{100*sum(x[2][PNL] for x in buenas)/so:+.2f} pp ({so:,.0f} acc)")
        print(f"  {pos} de {len(buenas)} siguen en positivo en octubre")

    print("\n" + "=" * 94)
    print("  Si las LENTAS rentables de septiembre siguen en positivo en octubre Y son varias,")
    print("  hay una clase que gana SIN velocidad: eso si lo puede ejecutar la Pi, y el siguiente")
    print("  paso seria averiguar que hacen. Si no persisten o solo hay una, es ruido.")
    print("=" * 94)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
