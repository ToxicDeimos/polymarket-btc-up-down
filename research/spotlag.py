"""
spotlag.py — ¿CUÁNTO RETRASO TRAE YA EL TICK DE BINANCE CUANDO LO VEMOS?

Todo nuestro presupuesto de latencia empieza a contar cuando RECIBIMOS el tick de Binance. Pero el
tick viene con retraso desde su servidor, y ese retraso es tiempo en el que el libro de Polymarket ya
se está moviendo sin que lo sepamos. Nunca lo hemos medido: siempre dimos el instante de recepción
como si fuera el instante del hecho.

Importa porque es el único tramo del que no sabemos nada. El envío de la orden ya está desglosado
(61 ms de red, 206 ms de cachés del SDK — ya arreglados —, ~180 ms de servidor). Si resulta que el
tick llega con 250 ms de retraso, es el tramo mayor y cambia qué merece la pena optimizar.

Dos cosas, porque una sin la otra engaña:
  1) EL DESFASE DE NUESTRO RELOJ contra el de Binance. Si el PC va 80 ms adelantado, el retraso
     medido sale 80 ms más corto y nos felicitaríamos por nada. Se mide con /api/v3/time
     descontando medio viaje de ida y vuelta.
  2) EL RETRASO DEL FEED: `aggTrade` trae T = instante de la operación en Binance. Restándole
     nuestra hora de recepción (ya corregida) sale lo que tarda en llegar.

    cd ~/polymarket-btc-up-down/research && python3 spotlag.py [segundos]
"""
import json, sys, time, statistics as st, urllib.request
import websocket

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def desfase_reloj(n=7):
    """Nuestro reloj menos el de Binance, en ms. Positivo = vamos adelantados."""
    d = []
    for _ in range(n):
        t0 = time.time()
        with urllib.request.urlopen("https://api.binance.com/api/v3/time", timeout=10) as r:
            srv = json.loads(r.read())["serverTime"] / 1000.0
        t1 = time.time()
        # el instante del servidor corresponde a algun punto entre t0 y t1: se toma el medio
        d.append(1000 * ((t0 + t1) / 2 - srv))
        time.sleep(0.2)
    d.sort()
    return st.median(d), d[0], d[-1]


def main():
    print("1) desfase de nuestro reloj contra Binance…", flush=True)
    off, lo, hi = desfase_reloj()
    print(f"   mediana {off:+.0f} ms  (min {lo:+.0f}, máx {hi:+.0f})"
          f"   {'vamos adelantados' if off > 0 else 'vamos atrasados'}")
    if hi - lo > 120:
        print("   ⚠ mucha dispersión: la corrección es poco fiable, tomar lo de abajo con pinzas")

    print(f"\n2) retraso del feed durante {DUR}s…", flush=True)
    lags = []

    def on_msg(w, m):
        try:
            d = json.loads(m)
            T = d.get("T") or d.get("E")
            if T: lags.append(1000 * (time.time() - off / 1000.0) - T)
        except Exception: pass

    app = websocket.WebSocketApp("wss://stream.binance.com:9443/ws/btcusdt@aggTrade",
                                 on_message=on_msg, on_error=lambda a, b: None)
    import threading
    threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10),
                     daemon=True).start()
    time.sleep(DUR)
    app.close()

    if len(lags) < 20:
        print(f"   solo {len(lags)} mensajes, muestra insuficiente"); return
    lags.sort()
    q = lambda p: lags[int(len(lags) * p)]
    print(f"   {len(lags)} operaciones")
    print(f"   mediana {st.median(lags):6.0f} ms")
    print(f"   p10 {q(0.10):.0f} · p25 {q(0.25):.0f} · p75 {q(0.75):.0f} · p90 {q(0.90):.0f} ms")

    print("\n" + "=" * 70)
    print("  PRESUPUESTO COMPLETO, de la operación en Binance al intento de emparejar")
    print("=" * 70)
    print(f"  Binance -> nosotros          {st.median(lags):6.0f} ms   <- esto es lo que se medía aquí")
    print(f"  decidir y firmar (cachés ok)      8 ms")
    print(f"  red hasta el CLOB                61 ms")
    print(f"  servidor de Polymarket         ~180 ms")
    print(f"  {'':-<40}")
    print(f"  TOTAL                        {st.median(lags) + 8 + 61 + 180:6.0f} ms")
    print("\n  Si el primer tramo es grande, es ahí donde hay que mirar y no en el envío.")
    print("  ⚠ Y OJO con interpretarlo: nuestras medidas de edge (fillsel, stalebt) están ancladas")
    print("  en el instante de RECEPCIÓN, así que son coherentes consigo mismas. Este número no las")
    print("  invalida — dice cuánto margen quedaría si detectáramos antes.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
