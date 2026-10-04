"""
dinero.py — ¿HAY PROFESIONALES? Los que ganan DOLARES, no los que tienen buen porcentaje.

persiste.py ordenaba por pp/accion y eso tiene un fallo que salta a la vista en su propia tabla: los
12 primeros tenian 444-2.500 acciones. **Ordenar por rentabilidad por accion selecciona a quien tuvo
suerte con poco volumen.** Un maker moviendo 200.000 acciones a +0,5 pp vive de esto y en esa tabla
sale mediocre.

Aqui se ordena por DINERO, que es lo que define a un profesional, y se hacen las dos preguntas que
de verdad importan:

  1. Los que mas GANARON en septiembre, ¿ganaron tambien en octubre?
  2. ¿Cuantas wallets estan en positivo LOS DOS MESES, y cuantas saldrian por azar?
     Bajo la hipotesis de que no hay habilidad, estar en positivo cada mes es una moneda al aire
     (con la probabilidad observada en cada mes por separado), asi que el numero esperado de
     "positivo en ambos" es p_sep × p_oct × N. Si hay muchas mas, hay habilidad.
     El test es binomial exacto por normal; con N~250 basta.

⚠ Septiembre son 30 dias y octubre 4: los importes NO son comparables entre meses. Lo que se compara
  es el SIGNO y el ORDEN, que no dependen de la duracion. Se informa tambien $/dia para dar escala.
⚠ El P&L es solo el de las 472 ventanas que tenemos, no el total de cada wallet. Mismo universo para
  todas, asi que la comparacion es justa.

    cd ~/polymarket-btc-up-down/research && python3 dinero.py [min_acciones]
"""
import csv, os, sys, time, math
from collections import defaultdict

csv.field_size_limit(10 ** 7)
DIR = os.path.dirname(os.path.abspath(__file__))
CINTA = os.path.join(DIR, "izzy_cinta2.csv")
TOK = os.path.join(DIR, "clob_tokens2.csv")
IZZY = "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2"
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
CORTE = "2026-10-01"
DIAS_SEP, DIAS_OCT = 30, 4


def main():
    corte = int(time.mktime(time.strptime(CORTE, "%Y-%m-%d")) - time.timezone)
    gan = {}
    for r in csv.reader(open(TOK, encoding="utf-8", errors="replace")):
        if len(r) == 4: gan[(r[0], r[1])] = int(r[3])

    w = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])      # $sep, sh_sep, $oct, sh_oct
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
        if ts < corte: a[0] += pnl; a[1] += s
        else:          a[2] += pnl; a[3] += s
        n += 1
    print(f"{n:,} operaciones · {len(w):,} wallets")

    el = [(k, a) for k, a in w.items() if a[1] >= MIN and a[3] >= MIN]
    N = len(el)
    print(f"{N} wallets con >={MIN} acciones en septiembre Y en octubre\n")
    if N < 30:
        print("muestra corta"); return

    # ── 1. los que mas DINERO ganaron en septiembre ──
    el.sort(key=lambda x: -x[1][0])
    print("=" * 92)
    print("  LOS 20 QUE MAS DINERO GANARON EN SEPTIEMBRE — y que hicieron en octubre")
    print("=" * 92)
    print(f"  {'wallet':<14}{'$ sep':>10}{'$/dia':>8}{'acc sep':>10}"
          f"{'| $ OCT':>11}{'$/dia':>8}{'acc oct':>10}   {'':<3}")
    for k, a in el[:20]:
        marca = "← izzy" if k == IZZY else ("ok" if a[2] > 0 else "")
        print(f"  {k[:12]:<14}{a[0]:>10,.0f}{a[0]/DIAS_SEP:>8,.0f}{a[1]:>10,.0f}"
              f"{a[2]:>11,.0f}{a[2]/DIAS_OCT:>8,.0f}{a[3]:>10,.0f}   {marca:<3}")
    top = el[:20]
    print(f"\n  LOS 20 JUNTOS: septiembre {sum(a[0] for _, a in top):+,.0f} $"
          f"  →  octubre {sum(a[2] for _, a in top):+,.0f} $"
          f"   ({sum(1 for _, a in top if a[2] > 0)} de 20 en positivo)")

    for m in (10, 50):
        g = el[:m]
        print(f"  LOS {m} MEJORES: septiembre {sum(a[0] for _, a in g):+,.0f} $"
              f"  →  octubre {sum(a[2] for _, a in g):+,.0f} $"
              f"   ({sum(1 for _, a in g if a[2] > 0)} de {m} en positivo)")

    # ── 2. ¿cuantos ganan LOS DOS meses, frente al azar? ──
    ps = sum(1 for _, a in el if a[0] > 0) / N
    po = sum(1 for _, a in el if a[2] > 0) / N
    amb = sum(1 for _, a in el if a[0] > 0 and a[2] > 0)
    esp = ps * po * N
    sd = math.sqrt(N * ps * po * (1 - ps * po))
    z = (amb - esp) / sd if sd else float("nan")
    print("\n" + "=" * 92)
    print("  ¿CUANTOS GANAN LOS DOS MESES?")
    print("=" * 92)
    print(f"  en positivo en septiembre: {100*ps:.1f}%   ·   en octubre: {100*po:.1f}%")
    print(f"  en positivo en AMBOS: {amb} wallets")
    print(f"  esperados por azar:   {esp:.1f}  (±{sd:.1f})")
    print(f"  **z = {z:+.2f}**  → {'HAY mas de los que da el azar' if z > 2 else 'indistinguible del azar'}")

    # ── 3. el dinero total: ¿se concentra o rota? ──
    tot_s = sum(a[0] for _, a in el if a[0] > 0)
    tot_o = sum(a[2] for _, a in el if a[2] > 0)
    rep = sum(a[2] for _, a in top if a[2] > 0)
    print(f"\n  del dinero ganado en octubre por estas {N} wallets ({tot_o:,.0f} $),")
    print(f"  los 20 campeones de septiembre se llevan {rep:,.0f} $ = {100*rep/tot_o:.0f}%")
    print(f"  (si no hubiera habilidad y el dinero rotara al azar, les tocaria ~{100*20/N:.0f}%)")
    print("=" * 92)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
