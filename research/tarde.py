"""
tarde.py — COMPRAR AL GANADOR YA DECIDIDO, DESPUES DEL CIERRE. ¿Existe y es +EV?

lentos.py separo las wallets en RAPIDAS (markout>0,3: le quitan al libro la cotizacion vieja, ganan
+1,75→+1,56 pp) y LENTAS (pierden −1,26→−0,65). Pero dentro de las lentas habia cinco que aguantan
los DOS meses con un perfil muy concreto: precio medio **0,90-0,99**, percentil alto, y la columna
de tiempo marcando **1.800-3.300 s desde la apertura de una ventana que dura 300** ⇒ operan media
hora o mas DESPUES del cierre.

Es decir: compran el resultado YA DECIDIDO a alguien que prefiere su dinero ahora a esperar la
liquidacion on-chain. No es prediccion y **no necesita velocidad**, que es justo la restriccion que
nos cierra todo lo demas.

Nunca lo medimos: endgame_monitor miro de T−40 s a T+15 s y concluyo que cerca del cierre el ganador
cotiza 0,98-1,00 y no hay nada que comprar ([[mm-market-making-pivot]]). Esto ocurre 40 minutos mas
tarde y es otra cosa.

Y la comision juega a favor por primera vez: fee = 0,07·p·(1−p), que a p=0,98 son **0,14 pp** en vez
de los 1,75 de p=0,50. En los extremos el peaje casi desaparece.

Se mide, para TODAS las compras de la cinta (no solo las de esas wallets):
  · reparto por MOMENTO respecto al cierre (dentro de la ventana / 0-10 min despues / 10-30 / 30-60 / +60)
  · dentro de cada tramo, por PRECIO, el acierto real y el EV **neto de comision de taker**
  · train (septiembre) / test (octubre), y el volumen disponible, que es lo que marca la capacidad

⚠ El ws de cada ventana se saca del SLUG (btc-updown-5m-<ws>), no se estima.

    cd ~/polymarket-btc-up-down/research && python3 tarde.py
"""
import csv, json, os, sys, time, urllib.request, urllib.error
from collections import defaultdict

csv.field_size_limit(10 ** 7)
DIR = os.path.dirname(os.path.abspath(__file__))
CINTA = os.path.join(DIR, "izzy_cinta2.csv")
TOK = os.path.join(DIR, "clob_tokens2.csv")
IZZY = "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2"
CORTE = "2026-10-01"
PAUSA = 0.1


def get(url):
    for i in range(3):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(r, timeout=40) as f:
                return json.loads(f.read().decode())
        except Exception:
            if i == 2: raise
            time.sleep(1.5 * (i + 1))


def fee(p):
    return 0.07 * p * (1 - p)


def main():
    corte = int(time.mktime(time.strptime(CORTE, "%Y-%m-%d")) - time.timezone)

    # cid -> ws, desde el SLUG (nada de estimarlo)
    print("Mapa de ventanas…")
    cid2ws, off = {}, 0
    while off <= 10000:
        d = get(f"https://data-api.polymarket.com/trades?user={IZZY}&limit=500&offset={off}")
        if not d: break
        for t in d:
            s = t.get("slug") or ""
            if s.startswith("btc-updown-5m"):
                try: cid2ws[t["conditionId"]] = int(s.rsplit("-", 1)[1])
                except Exception: pass
        if len(d) < 500: break
        off += 500
        time.sleep(PAUSA)
    print(f"  {len(cid2ws)} ventanas con hora de apertura conocida")

    gan = {}
    for r in csv.reader(open(TOK, encoding="utf-8", errors="replace")):
        if len(r) == 4: gan[(r[0], r[1])] = int(r[3])

    TRAMOS = [(None, 300, "dentro (0-300 s)"), (300, 900, "cierre +0-10 min"),
              (900, 1800, "cierre +10-25 min"), (1800, 3600, "cierre +25-55 min"),
              (3600, None, "mas de 55 min")]
    CORTES_P = [0.90, 0.95, 0.98, 0.995]
    ETI_P = ["<0,90", "0,90-0,95", "0,95-0,98", "0,98-0,995", ">=0,995"]

    def tramo(dt):
        for i, (a, b, _) in enumerate(TRAMOS):
            if (a is None or dt >= a) and (b is None or dt < b): return i
        return len(TRAMOS) - 1

    def cubo(p):
        for i, c in enumerate(CORTES_P):
            if p < c: return i
        return len(CORTES_P)

    # [n, acciones, gano*acc, pagado*acc, fee*acc] por (tramo, cubo, periodo)
    acc = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])
    n = 0
    print("Leyendo la cinta…")
    for r in csv.reader(open(CINTA, encoding="utf-8", errors="replace")):
        if len(r) != 7: continue
        cid, ts, asset, side, p, s, wal = r
        if side != "BUY" or not asset: continue
        g = gan.get((cid, asset)); ws = cid2ws.get(cid)
        if g is None or ws is None: continue
        try: ts, p, s = int(ts), float(p), float(s)
        except ValueError: continue
        a = acc[(tramo(ts - ws), cubo(p), 0 if ts < corte else 1)]
        a[0] += 1; a[1] += s; a[2] += g * s; a[3] += p * s; a[4] += fee(p) * s
        n += 1
        if n % 200000 == 0: sys.stdout.write(f"\r  {n:,}"); sys.stdout.flush()
    print(f"\r  {n:,} compras clasificadas")

    print("\n" + "=" * 100)
    print("  COMPRAR DESPUES DEL CIERRE — acierto real y EV NETO de comision, por momento y precio")
    print("=" * 100)
    for it, (_, _, nom) in enumerate(TRAMOS):
        filas = [(ic, acc[(it, ic, 0)], acc[(it, ic, 1)]) for ic in range(len(ETI_P))]
        filas = [f for f in filas if f[1][1] + f[2][1] > 0]
        if not filas: continue
        sz = sum(f[1][1] + f[2][1] for f in filas)
        print(f"\n  ── {nom} ──   {sz:,.0f} acciones")
        print(f"    {'precio':<13}{'n':>8}{'acciones':>12}{'acierto':>9}{'pagado':>8}"
              f"{'EV bruto':>10}{'EV NETO':>10}{'| sep':>8}{'oct':>8}")
        for ic, s_, o_ in filas:
            t_ = [s_[i] + o_[i] for i in range(5)]
            if t_[1] <= 0: continue
            ac = t_[2] / t_[1]; pg = t_[3] / t_[1]
            br = 100 * (t_[2] - t_[3]) / t_[1]
            ne = 100 * (t_[2] - t_[3] - t_[4]) / t_[1]
            f_s = f"{100*(s_[2]-s_[3]-s_[4])/s_[1]:+.2f}" if s_[1] > 0 else "—"
            f_o = f"{100*(o_[2]-o_[3]-o_[4])/o_[1]:+.2f}" if o_[1] > 0 else "—"
            print(f"    {ETI_P[ic]:<13}{t_[0]:>8}{t_[1]:>12,.0f}{100*ac:>8.1f}%{pg:>8.3f}"
                  f"{br:>10.2f}{ne:>10.2f}{f_s:>8}{f_o:>8}")

    print("\n" + "=" * 100)
    print("  EV NETO = acierto − precio pagado − comision de taker (0,07·p·(1−p)).")
    print("  A p=0,98 la comision son 0,14 pp, no 1,75: en los extremos el peaje casi desaparece.")
    print("  Lo que hay que ver: un tramo POSTERIOR AL CIERRE con EV neto positivo, el MISMO signo")
    print("  en septiembre y en octubre, y acciones suficientes para que quepa dinero.")
    print("=" * 100)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
