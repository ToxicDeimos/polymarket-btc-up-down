"""
latency.py — ¿CUÁNTO TARDAMOS? Ya no es la pregunta de si hay edge: stalebt lo midió y existe (el espejo
pierde 2,7-5,7pp en todas las filas, y hay ask con ~100 acciones que levantar). La pregunta es el reloj:

      0,10 s → neto +1,70pp   ·   0,20 s → +0,72   ·   0,30 s → −0,07   ·   0,50 s → −0,95

El punto de corte está en 300 ms y nuestro presupuesto lo hemos estado SUPONIENDO entre 300 y 500. Aquí se
mide de verdad, sin poner ninguna orden, descomponiendo el camino entero:

  1) RELOJ — desviación del reloj de la Pi frente al de Binance (fórmula NTP con la marca del servidor y el
     tiempo de ida y vuelta). Sin esto, el paso 2 mide la desviación del reloj, no la latencia.
  2) BINANCE → PI — con el stream de operaciones (@trade trae marca de evento E), cuánto tarda en llegarnos
     cada evento ya descontada la desviación. Es lo que tardamos en ENTERARNOS.
  3) PI → POLYMARKET — ida y vuelta HTTP real contra el CLOB, repetida, con y sin conexión reutilizada.
     Es lo que tarda la orden en salir (cota inferior: falta el tiempo de casado en su lado).
  4) FIRMA — coste de CPU de firmar una orden EIP-712 en ESTA máquina, en local y sin enviar nada.

El total es nuestro suelo. Si ya son 400 ms, este mecanismo no es para la Pi y hay que decidir otra cosa.
Si son 150, hay margen y el siguiente paso es una orden mínima de verdad.

    cd ~/polymarket-btc-up-down/research && python3 latency.py
"""
import json, time, socket, statistics, urllib.request, sys

N_HTTP = 15
N_WSS = 200
BINANCE_TIME = "https://api.binance.com/api/v3/time"
CLOB = "https://clob.polymarket.com/ok"        # la URL raíz no responde; /ok devuelve "OK" y vale de sonda
PM_WSS_HOST = "ws-subscriptions-clob.polymarket.com"
WSS = "wss://stream.binance.com:9443/ws/btcusdt@trade"


def q(xs, p):
    s = sorted(xs); return s[min(len(s) - 1, int(p * len(s)))]


def show(nm, xs, unit="ms"):
    if not xs: print(f"  {nm:>34}  (sin muestra)"); return
    print(f"  {nm:>34}  mediana {statistics.median(xs):>7.1f} {unit} · "
          f"p90 {q(xs, .90):>7.1f} · p99 {q(xs, .99):>7.1f} · n={len(xs)}")


def main():
    print("=" * 92)
    print("  PRESUPUESTO DE LATENCIA DE ESTA MÁQUINA (no se pone ninguna orden)")
    print("=" * 92)

    # 1) desviación del reloj frente a Binance
    offs = []; rtts = []
    for _ in range(N_HTTP):
        try:
            t0 = time.time()
            with urllib.request.urlopen(BINANCE_TIME, timeout=8) as r:
                srv = json.load(r)["serverTime"] / 1000.0
            t1 = time.time()
            offs.append((srv - (t0 + t1) / 2) * 1000.0); rtts.append((t1 - t0) * 1000.0)
        except Exception: pass
        time.sleep(0.15)
    off = statistics.median(offs) if offs else 0.0
    print("\n  1) RELOJ")
    show("ida y vuelta a Binance (HTTP)", rtts)
    print(f"  {'desviación del reloj de la Pi':>34}  {off:+.1f} ms  "
          f"({'la Pi va por detrás' if off > 0 else 'la Pi va por delante'})")
    if abs(off) > 200:
        print("     ⚠ desviación grande: revisar NTP (timedatectl) antes de fiarse del paso 2")

    # 2) Binance → Pi
    print("\n  2) BINANCE → PI  (cuánto tardamos en enterarnos)")
    lat = []
    try:
        import websocket
        w = websocket.create_connection(WSS, timeout=10)
        t_end = time.time() + 25
        while time.time() < t_end and len(lat) < N_WSS:
            try:
                d = json.loads(w.recv())
                E = d.get("E")
                if E: lat.append((time.time() * 1000.0 - E) - off)
            except Exception: break
        w.close()
    except ImportError:
        print("     falta websocket-client (pip install websocket-client --break-system-packages)")
    except Exception as e:
        print(f"     no se pudo abrir el stream: {e}")
    show("evento de mercado → nuestro proceso", lat)

    # 3) Pi → Polymarket
    print("\n  3) PI → POLYMARKET  (cuánto tarda en salir la orden; cota INFERIOR)")
    cold = []
    for _ in range(N_HTTP):
        try:
            t0 = time.time()
            with urllib.request.urlopen(CLOB, timeout=8) as r: r.read(256)
            cold.append((time.time() - t0) * 1000.0)
        except Exception: pass
        time.sleep(0.1)
    show("ida y vuelta HTTP (conexión nueva)", cold)
    def tcp(host, n=N_HTTP):
        out = []
        for _ in range(n):
            try:
                t0 = time.time()
                s = socket.create_connection((host, 443), timeout=6); s.close()
                out.append((time.time() - t0) * 1000.0)
            except Exception: pass
            time.sleep(0.05)
        return out
    warm = tcp("clob.polymarket.com")
    show("solo abrir el socket TCP (órdenes)", warm)
    pmws = tcp(PM_WSS_HOST)
    show("socket TCP al WSS (recibir libro)", pmws)

    # 4) firma local
    print("\n  4) FIRMA DE LA ORDEN  (solo CPU, nada sale de la máquina)")
    sig = []
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        acct = Account.create()
        msg = encode_defunct(text="orden de prueba para medir el coste de firmar")
        for _ in range(60):
            t0 = time.perf_counter()
            Account.sign_message(msg, private_key=acct.key)
            sig.append((time.perf_counter() - t0) * 1000.0)
        show("firmar en esta CPU", sig)
    except ImportError:
        print("     falta eth_account — instalar para medirlo "
              "(pip install eth-account --break-system-packages)")
    except Exception as e:
        print(f"     no se pudo medir: {e}")

    # ---- total: OJO, Binance NO suma ----
    print("\n" + "=" * 92)
    print("  EL PRESUPUESTO QUE CUENTA")
    print("=" * 92)
    print("  queuewatch sella LAS DOS series (spot y libro) con el reloj de la Pi AL RECIBIRLAS, así que")
    print("  cuando stalebt dice 'a los 100 ms' significa 100 ms después de que NOSOTROS nos enteremos:")
    print(f"  los {statistics.median(lat):.0f} ms de Binance YA ESTÁN DENTRO de esa medición y sumarlos sería"
          if lat else "  la latencia de Binance YA ESTÁ DENTRO de esa medición y sumarla sería")
    print("  contarlos dos veces. (Y el daño que hacen los competidores más cercanos a Binance durante")
    print("  nuestra ceguera también está ya descontado, porque la medición se ancla en nuestra vista.)")
    print("  Lo que SÍ consume presupuesto es solo lo que viene DESPUÉS de enterarnos:")
    parts = []
    if sig: parts.append(("firmar", statistics.median(sig)))
    if cold: parts.append(("mandar la orden (HTTP)", statistics.median(cold)))
    if pmws: parts.append(("recibir el libro (TCP al WSS)", statistics.median(pmws)))
    if not parts:
        print("\n  no se pudo componer el total — revisar los avisos de arriba"); return
    tot = sum(v for _, v in parts)
    print("\n  " + " + ".join(f"{n} {v:.0f}" for n, v in parts) + f"  =  {tot:.0f} ms")
    print("=" * 92)
    if tot <= 150: v = f"≈{tot:.0f} ms → fila de 0,10-0,20 s de stalebt: neto +0,7 a +1,7pp por disparo. VIABLE."
    elif tot <= 250: v = f"≈{tot:.0f} ms → fila de 0,20-0,30 s: neto entre +0,7 y 0. En el filo."
    else: v = f"≈{tot:.0f} ms → fila de 0,30 s o peor: neto ≤0. No es para esta máquina."
    print(f"  → {v}")
    if cold and q(cold, .99) > 3 * statistics.median(cold):
        print(f"  ⚠ cola fea: el p99 del HTTP es {q(cold,.99):.0f} ms frente a "
              f"{statistics.median(cold):.0f} de mediana. Un disparo de cada cien llegará tardísimo;")
        print("    con un edge de 1pp eso importa, así que conviene abortar la orden si se pasa del plazo.")
    print("  Sigue siendo un SUELO: falta el casado en el lado de Polymarket y la cola contra otros que")
    print("  persiguen la misma cotización. El número real solo lo da una orden mínima de verdad.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
