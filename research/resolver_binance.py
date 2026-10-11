"""
resolver_binance.py — QUIEN GANO CADA VENTANA, SIN PREGUNTARSELO A POLYMARKET.

Desde el bloqueo (7-oct) la Pi no llega a clob/gamma/data-api, asi que `_resol` no puede resolver
ventanas y con eso se cae TODO el analisis (fillsel, precio, regimen, salida...). Pero la resolucion
no hay que preguntarla: la regla la desciframos en septiembre y es publica.

    REGLA D3 (ver polymarket-updown-twap-resolution): gana Up si
        media de los cierres de los ultimos L s de la ventana
            >  media de los cierres de los L s ANTERIORES a la apertura
    con L=60 desde el 14-ago-2026. Reproduce la resolucion REAL el **97,9% en 15m** y el 97,0% en 5m.

Se calcula con velas de 1 segundo de Binance, que no esta afectado. Escribe un CSV `ws,ganador`
(1 = Up) que los analisis pueden usar en lugar de preguntar al CLOB.

⚠ Es un 97,9%, no un 100%. Vale para medir tendencias sobre miles de ventanas; NO vale para decidir
  una operacion concreta ni para contar dinero al centimo. El ~2% de error son cierres tan ajustados
  que la diferencia entre el feed de Binance y el oraculo de Chainlink cambia el signo — y esos son
  justo los que no se pueden resolver desde fuera.

    cd ~/polymarket-btc-up-down/research && python3 resolver_binance.py paper15_v4.csv [L]

Cachea las velas en btc_1s.csv y el resultado en reso_binance.csv (ambos ignorados por git).
"""
import csv, json, os, sys, time, urllib.request, urllib.error
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))
VELAS = os.path.join(DIR, "btc_1s.csv")
SALIDA = os.path.join(DIR, "reso_binance.csv")
FICHERO = sys.argv[1] if len(sys.argv) > 1 else "paper15_v4.csv"
L = int(sys.argv[2]) if len(sys.argv) > 2 else 60
PAUSA = 0.08


def get(url, reintentos=3):
    for i in range(reintentos):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(r, timeout=30) as f:
                return json.loads(f.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404): return None
            if i == reintentos - 1: raise
        except Exception:
            if i == reintentos - 1: raise
        time.sleep(1.5 * (i + 1))
    return None


def ventanas_del_papel(p):
    """ws y duracion de cada ventana del colector. La duracion se deduce del propio fichero."""
    wss = set()
    with open(p, encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            try: wss.add(int(r["ws"]))
            except Exception: pass
    if not wss: return [], 300
    v = sorted(wss)
    # el hueco MINIMO entre ventanas consecutivas es la duracion (300 en 5m, 900 en 15m)
    hs = [b - a for a, b in zip(v, v[1:]) if b > a]
    dur = min(hs) if hs else 300
    return v, dur


def velas(wss, dur):
    """ws -> {segundo: cierre} para [ws-L-10, ws+dur+10]."""
    cache = defaultdict(dict)
    if os.path.exists(VELAS):
        for r in csv.reader(open(VELAS, encoding="utf-8", errors="replace")):
            if len(r) == 3:
                try: cache[int(r[0])][int(r[1])] = float(r[2])
                except ValueError: pass
    faltan = [w for w in wss if w not in cache]
    if not faltan: return cache
    print(f"  Binance: velas de 1 s para {len(faltan)} ventanas…")
    f = open(VELAS, "a", newline="", encoding="utf-8"); w_ = csv.writer(f)
    for i, ws in enumerate(faltan, 1):
        ini, fin = ws - L - 10, ws + dur + 10
        # la API devuelve como mucho 1000 velas por peticion; una ventana de 15m son ~1.000 s
        t = ini
        while t < fin:
            d = get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s"
                    f"&startTime={t*1000}&endTime={min(t+900, fin)*1000}&limit=1000")
            for k in (d or []):
                s, c = k[0] // 1000, float(k[4])
                cache[ws][s] = c; w_.writerow((ws, s, c))
            if not d: break
            t += 900
            time.sleep(PAUSA)
        if not cache[ws]: cache[ws] = {}
        f.flush()
        if i % 25 == 0 or i == len(faltan):
            sys.stdout.write(f"\r    {i}/{len(faltan)}"); sys.stdout.flush()
    f.close(); print()
    return cache


def main():
    p = os.path.join(DIR, FICHERO)
    if not os.path.exists(p):
        print(f"no encuentro {p}"); return
    wss, dur = ventanas_del_papel(p)
    print(f"{FICHERO}: {len(wss)} ventanas · duracion deducida {dur} s "
          f"({'15m' if dur == 900 else '5m' if dur == 300 else '?'}) · TWAP L={L} s")
    if not wss: return

    kl = velas(wss, dur)
    gan, sin = {}, 0
    for ws in wss:
        v = kl.get(ws)
        if not v: sin += 1; continue
        pre = [v[t] for t in range(ws - L, ws) if t in v]
        fin = [v[t] for t in range(ws + dur - L, ws + dur) if t in v]
        # exigir cobertura: con la mitad de las velas la media no vale
        if len(pre) < L * 0.8 or len(fin) < L * 0.8: sin += 1; continue
        gan[ws] = 1 if sum(fin) / len(fin) > sum(pre) / len(pre) else 0

    with open(SALIDA, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["ws", "gana_up"])
        for ws in sorted(gan): w.writerow((ws, gan[ws]))
    ups = sum(gan.values())
    print(f"\n  resueltas {len(gan)} de {len(wss)}  ·  sin velas suficientes: {sin}")
    print(f"  reparto: Up {100*ups/len(gan):.1f}%  ·  Down {100*(1-ups/len(gan)):.1f}%"
          if gan else "  sin ventanas resueltas")
    print(f"  escrito en {os.path.basename(SALIDA)}")
    print(f"\n  ⚠ Precision esperada de la regla: ~97,9% en 15m, ~97,0% en 5m. Sirve para medir")
    print(f"    tendencias sobre miles de ventanas, NO para contar dinero al centimo.")
    if gan and abs(ups / len(gan) - 0.5) > 0.08:
        print(f"  ⚠ El reparto Up/Down se aleja mucho del 50%: revisar antes de fiarse.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
