"""
certeza.py — ¿CUANDO SABEMOS EL GANADOR CON CERTEZA SUFICIENTE PARA COMPRAR A 0,99?

tarde.py + el forense post-cierre dejaron esto: en el 44% de las ventanas hay gente VENDIENDO el
token ganador a **0,990 en el segundo 0 del cierre** (1.774 ventas frente a 117 compras) — pagan un
centimo por no esperar a la liquidacion. Comprar barato ahi no existe; lo que existe es estar puesto
comprandoles. Y eso NO necesita velocidad, que es la restriccion que nos cierra todo lo demas.

Pero la aritmetica es un cuchillo: ganas 1 centimo si aciertas y pierdes 99 si fallas.

    acierto necesario para empatar a 0,99 ≈ 99,0%
    nuestra regla D3 acierta               97,0%   ⇒ −2 pp por operacion. Muerto tal cual.

La unica salida: no apostar en todas las ventanas, solo donde el resultado sea INEQUIVOCO. Eso
nunca se ha medido. Aqui se mide la precision de D3 **por tamano de margen**, con muestra grande.

Regla D3 (ver polymarket-updown-twap-resolution): gana Up si
    media(cierres del ultimo minuto de la ventana) > media(cierres del minuto ANTERIOR a la apertura)
margen = la diferencia entre esas dos medias, en dolares.

Se contrasta contra la resolucion REAL de gamma. Y lo que decide:
  · ¿hay un umbral de margen donde el acierto sea >=99,5% con n suficiente?
  · ¿que fraccion de ventanas pasa ese umbral? (eso es la frecuencia del negocio)
  · ⚠ CUIDADO: 99,5% medido sobre n=200 no distingue de 99,0%. Se informa el intervalo.

    cd ~/polymarket-btc-up-down/research && python3 certeza.py [dias]

Cachea klines en btc_1s.csv y resoluciones en reso_5m.csv.
"""
import csv, json, math, os, sys, time, urllib.request, urllib.error
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))
VELAS = os.path.join(DIR, "btc_1s.csv")
RESO = os.path.join(DIR, "reso_5m.csv")
DIAS = int(sys.argv[1]) if len(sys.argv) > 1 else 14
L = 60                 # ventana del TWAP en 5m desde el 14-ago
PAUSA = 0.08


def get(url, reintentos=3):
    for i in range(reintentos):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(r, timeout=40) as f:
                return json.loads(f.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404): return None
            if i == reintentos - 1: raise
        except Exception:
            if i == reintentos - 1: raise
        time.sleep(1.5 * (i + 1))
    return None


def resoluciones(wss):
    """ws -> 1 si gano Up, 0 si Down."""
    gan = {}
    if os.path.exists(RESO):
        for r in csv.reader(open(RESO, encoding="utf-8", errors="replace")):
            if len(r) == 2:
                try: gan[int(r[0])] = int(r[1])
                except ValueError: pass
    faltan = [x for x in wss if x not in gan]
    if faltan:
        print(f"  resoluciones: {len(faltan)} por pedir…")
        f = open(RESO, "a", newline="", encoding="utf-8"); w = csv.writer(f)
        for i in range(0, len(faltan), 100):
            lote = faltan[i:i + 100]
            u = ("https://gamma-api.polymarket.com/markets?closed=true&limit=100&"
                 + "&".join(f"slug=btc-updown-5m-{x}" for x in lote))
            for m in (get(u) or []):
                try:
                    ws = int(m["slug"].rsplit("-", 1)[1])
                    gan[ws] = 0 if float(json.loads(m["outcomePrices"])[0]) < 0.5 else 1
                    w.writerow((ws, gan[ws]))
                except Exception: pass
            f.flush()
            sys.stdout.write(f"\r    {min(i+100, len(faltan))}/{len(faltan)}"); sys.stdout.flush()
            time.sleep(PAUSA)
        f.close(); print()
    return gan


def velas(wss):
    cache = defaultdict(dict)
    if os.path.exists(VELAS):
        for r in csv.reader(open(VELAS, encoding="utf-8", errors="replace")):
            if len(r) == 3:
                try: cache[int(r[0])][int(r[1])] = float(r[2])
                except ValueError: pass
    faltan = [x for x in wss if x not in cache]
    if faltan:
        print(f"  Binance: {len(faltan)} ventanas de velas de 1 s…")
        f = open(VELAS, "a", newline="", encoding="utf-8"); w = csv.writer(f)
        for i, ws in enumerate(faltan, 1):
            d = get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s"
                    f"&startTime={(ws-70)*1000}&endTime={(ws+310)*1000}&limit=400")
            for k in (d or []):
                cache[ws][k[0] // 1000] = float(k[4]); w.writerow((ws, k[0] // 1000, k[4]))
            if not d: cache[ws] = {}
            f.flush()
            if i % 50 == 0 or i == len(faltan):
                sys.stdout.write(f"\r    {i}/{len(faltan)}"); sys.stdout.flush()
            time.sleep(PAUSA)
        f.close(); print()
    return cache


def wilson(k, n):
    """intervalo de Wilson al 95% — el normal se rompe cuando el acierto roza el 100%."""
    if n == 0: return (0.0, 1.0)
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    e = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - e), min(1.0, c + e))


def main():
    ahora = int(time.time())
    fin = (ahora // 300) * 300 - 3600               # una hora de colchon para que esten resueltas
    wss = [fin - 300 * i for i in range(DIAS * 288)]
    wss = sorted(wss)
    print(f"{len(wss)} ventanas de 5m ({DIAS} dias), de "
          f"{time.strftime('%Y-%m-%d', time.gmtime(wss[0]))} a "
          f"{time.strftime('%Y-%m-%d', time.gmtime(wss[-1]))}")

    gan = resoluciones(wss)
    print(f"  resueltas: {len(gan)}")
    kl = velas([x for x in wss if x in gan])

    filas = []
    for ws in wss:
        g = gan.get(ws); v = kl.get(ws)
        if g is None or not v: continue
        pre = [v[t] for t in range(ws - L, ws) if t in v]
        fin_ = [v[t] for t in range(ws + 300 - L, ws + 300) if t in v]
        if len(pre) < L * 0.8 or len(fin_) < L * 0.8: continue
        margen = sum(fin_) / len(fin_) - sum(pre) / len(pre)
        filas.append((abs(margen), 1 if (margen > 0) == (g == 1) else 0))
    print(f"\n{len(filas)} ventanas con velas y resolucion\n")
    if len(filas) < 200:
        print("muestra corta"); return

    CORTES = [2, 5, 10, 20, 40, 80]
    ETI = ["<2$", "2-5$", "5-10$", "10-20$", "20-40$", "40-80$", ">80$"]
    agg = defaultdict(lambda: [0, 0])
    for m, ok in filas:
        i = next((j for j, c in enumerate(CORTES) if m < c), len(CORTES))
        agg[i][0] += 1; agg[i][1] += ok

    print("=" * 84)
    print("  ACIERTO DE LA REGLA D3 SEGUN EL MARGEN (|media final − media previa|)")
    print("=" * 84)
    print(f"  {'margen':<12}{'ventanas':>10}{'acierto':>10}{'IC 95%':>20}{'EV a 0,99':>12}")
    for i in sorted(agg):
        n, k = agg[i]
        lo, hi = wilson(k, n)
        # comprar a 0,99: ganas 0,01 si aciertas, pierdes 0,99 si no (fee despreciable aqui)
        ev = 100 * (k / n * 0.01 - (1 - k / n) * 0.99)
        print(f"  {ETI[i]:<12}{n:>10}{100*k/n:>9.2f}%  [{100*lo:>6.2f}, {100*hi:>6.2f}]"
              f"{ev:>12.2f}")

    print("\n" + "=" * 84)
    print("  ACUMULADO: si solo operaramos por ENCIMA de cada umbral")
    print("=" * 84)
    print(f"  {'umbral':<12}{'ventanas':>10}{'% del total':>13}{'acierto':>10}"
          f"{'IC inferior':>13}{'EV a 0,99':>12}")
    for c in CORTES + [120, 200]:
        sel = [ok for m, ok in filas if m >= c]
        if len(sel) < 30: continue
        n, k = len(sel), sum(sel)
        lo, _ = wilson(k, n)
        ev = 100 * (k / n * 0.01 - (1 - k / n) * 0.99)
        ev_lo = 100 * (lo * 0.01 - (1 - lo) * 0.99)
        print(f"  >={c}$".ljust(14) + f"{n:>10}{100*n/len(filas):>12.0f}%"
              f"{100*k/n:>9.2f}%{100*lo:>12.2f}%{ev:>12.2f}")
    print("\n  Para que comprar a 0,99 sea +EV hace falta acierto > 99,0%.")
    print("  ⚠ Mirar el IC INFERIOR, no el punto: con n=300 un 100% observado es compatible")
    print("    con un 98,8% real, y a 0,99 esa diferencia es la ruina.")
    print("=" * 84)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
