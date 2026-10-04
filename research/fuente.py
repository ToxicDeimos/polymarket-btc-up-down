"""
fuente.py — ¿QUÉ MERCADO NOS ENTERA ANTES DE QUE BTC SE HA MOVIDO?

Todo nuestro presupuesto de latencia empieza cuando RECIBIMOS la señal, y medimos que el tick de
Binance llega con ~130 ms de retraso: sus servidores están en Asia. El resto del presupuesto ya no se
puede tocar — el origen de Polymarket está en Europa (un /book cuesta 50 ms desde Madrid, imposible
si estuviera en EE.UU.) y los ~300 ms del POST son proceso suyo, igual para todo el mundo.

Así que la única pieza reducible es la fuente de la señal. Y hay mercados más cerca: Kraken 44 ms,
Bitstamp 60, Coinbase 64, contra los 130 de Binance.

PERO el retraso de transporte no basta para decidir, y por dos motivos:
  1) FRECUENCIA: Binance da ~350 operaciones en 90 s y Kraken ~30. Un feed que se actualiza diez
     veces menos puede enterarse más tarde aunque su latencia sea menor.
  2) LIDERAZGO: Binance marca el precio global y los demás lo siguen. Si Kraken refleja el
     movimiento 40 ms más tarde, nos comemos la mitad de la ventaja.

Las dos se contestan midiendo lo único que importa: **en NUESTRO reloj de recepción, ¿quién se entera
antes?** Se correlacionan los cambios de precio de cada mercado contra los de Binance a distintos
desfases; el desfase que maximiza la correlación dice cuántos ms adelanta o atrasa, ya incluyendo
transporte y frecuencia juntos.

RESULTADO (3-oct-2026, desde Madrid, 180 s con mercado en movimiento):

    mercado    nos entera vs Binance   correlacion   una actualizacion cada
    Coinbase          -50 ms              0,206              78 ms
    Kraken           -100 ms              0,237             685 ms
    Bitstamp         -150 ms              0,353             117 ms
    Binance             ---                 ---             0-5 ms (12.169 en 180 s)

Todos NEGATIVOS: nos enteramos MAS TARDE que con Binance, pese a que Binance esta el doble de lejos
(130 ms de transporte contra 44-64). La frecuencia aplasta a la latencia: para un disparador que mira
ventanas de 0,2 s, un feed que se actualiza cada 117 ms no puede ver el movimiento.

⇒ Binance es la mejor fuente y no hay nada que cambiar. Con esto se cierra la ultima pieza reducible
del presupuesto de latencia, y con ella la idea de mudarse a un VPS.

    cd ~/polymarket-btc-up-down/research && python3 fuente.py [segundos]
"""
import json, sys, time, threading, statistics as st
import websocket

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 180
PASO = 0.05          # rejilla de 50 ms
MAXLAG = 20          # +-1 s


def mid_de(b, a):
    try:
        b, a = float(b), float(a)
        return (b + a) / 2 if b > 0 and a > 0 else None
    except Exception:
        return None


FUENTES = {
    "Binance": ("wss://stream.binance.com:9443/ws/btcusdt@bookTicker", None,
                lambda x: mid_de(x.get("b"), x.get("a"))),
    "Coinbase": ("wss://ws-feed.exchange.coinbase.com",
                 {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]},
                 lambda x: mid_de(x.get("best_bid"), x.get("best_ask")) if x.get("type") == "ticker" else None),
    "Bitstamp": ("wss://ws.bitstamp.net",
                 {"event": "bts:subscribe", "data": {"channel": "order_book_btcusd"}},
                 lambda x: mid_de(x["data"]["bids"][0][0], x["data"]["asks"][0][0])
                 if x.get("event") == "data" and x.get("data", {}).get("bids") else None),
    "Kraken": ("wss://ws.kraken.com/v2",
               {"method": "subscribe", "params": {"channel": "ticker", "symbol": ["BTC/USD"]}},
               lambda x: mid_de(x["data"][0].get("bid"), x["data"][0].get("ask"))
               if x.get("channel") == "ticker" and x.get("data") else None),
}


def main():
    series = {k: [] for k in FUENTES}        # (hora de RECEPCION nuestra, mid)
    apps = []
    for nom, (url, sub, saca) in FUENTES.items():
        def on_open(w, s=sub):
            if s: w.send(json.dumps(s))
        def on_msg(w, m, nom=nom, saca=saca):
            try:
                x = json.loads(m); v = saca(x)
                if v: series[nom].append((time.time(), v))
            except Exception: pass
        app = websocket.WebSocketApp(url, on_open=on_open, on_message=on_msg,
                                     on_error=lambda a, b: None)
        threading.Thread(target=lambda a=app: a.run_forever(ping_interval=20, ping_timeout=10),
                         daemon=True).start()
        apps.append(app)

    print(f"escuchando {DUR}s…", flush=True)
    time.sleep(DUR)
    for a in apps: a.close()

    for nom, v in series.items():
        if v:
            dt = [b[0] - a[0] for a, b in zip(v, v[1:])]
            print(f"  {nom:<10}{len(v):>6} actualizaciones · una cada "
                  f"{1000*st.median(dt):.0f} ms de mediana" if len(dt) > 5 else f"  {nom:<10}{len(v):>6}")
        else:
            print(f"  {nom:<10}     0  (sin datos — ¿cambió el formato del canal?)")

    # rejilla comun: ultimo valor conocido cada 50 ms
    vivos = {k: v for k, v in series.items() if len(v) > 50}
    if "Binance" not in vivos or len(vivos) < 2:
        print("\nmuestra insuficiente para comparar"); return
    t0 = max(v[0][0] for v in vivos.values()); t1 = min(v[-1][0] for v in vivos.values())
    n = int((t1 - t0) / PASO)
    rej = {}
    for k, v in vivos.items():
        g, i, ult = [], 0, v[0][1]
        for j in range(n):
            t = t0 + j * PASO
            while i < len(v) and v[i][0] <= t: ult = v[i][1]; i += 1
            g.append(ult)
        rej[k] = [b - a for a, b in zip(g, g[1:])]      # cambios

    def corr(x, y):
        mx, my = st.mean(x), st.mean(y)
        sx = sum((a - mx) ** 2 for a in x) ** 0.5
        sy = sum((b - my) ** 2 for b in y) ** 0.5
        if sx == 0 or sy == 0: return 0.0
        return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)

    base = rej["Binance"]
    print(f"\n  rejilla de {PASO*1000:.0f} ms · {len(base)} puntos")
    print(f"\n  {'mercado':<10}{'adelanto vs Binance':>22}{'correlación':>14}")
    for k, v in rej.items():
        if k == "Binance": continue
        mejor, mcorr = 0, -2
        for L in range(-MAXLAG, MAXLAG + 1):
            if L >= 0: a, b = base[L:], v[:len(v) - L] if L else v
            else: a, b = base[:len(base) + L], v[-L:]
            m = min(len(a), len(b))
            if m < 50: continue
            c = corr(a[:m], b[:m])
            if c > mcorr: mcorr, mejor = c, L
        ms = mejor * PASO * 1000
        print(f"  {k:<10}{(f'{ms:+.0f} ms'):>22}{mcorr:>14.3f}")
    print("\n  POSITIVO = ese mercado nos entera ANTES que Binance (en nuestro reloj).")
    print("  La correlación dice si se mueven juntos: por debajo de ~0,3 la medida no vale.")
    print("  ⚠ Esto ya incluye transporte Y frecuencia de actualización: es lo que de verdad")
    print("    importa, porque medimos cuándo lo sabemos NOSOTROS, no cuándo ocurrió.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
