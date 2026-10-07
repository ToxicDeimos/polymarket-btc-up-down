"""
futuros.py — ¿NOS ENTERAMOS ANTES POR EL PERPETUO QUE POR EL SPOT?

fuente.py cerro la pregunta "¿que EXCHANGE nos entera antes?" comparando Binance spot contra
Coinbase, Kraken y Bitstamp: gano Binance, porque la frecuencia de actualizacion aplasta a la
latencia de transporte (Binance 0-5 ms entre mensajes, Bitstamp 117, Kraken 685). Conclusion de
entonces: no hay nada que ganar cambiando de fuente.

Pero esa comparacion fue SPOT contra SPOT. Nunca comparamos lo obvio: **Binance spot contra Binance
FUTUROS PERPETUOS**. En cripto el descubrimiento de precio ocurre en el perpetuo — ahi esta el flujo
apalancado — y el spot suele ir detras. Si el perp adelanta 30-50 ms, estariamos llegando tarde por
mirar el sitio equivocado, no por estar lejos: mismo exchange, misma distancia, tiempo GRATIS.

Y hay motivo para sospecharlo: quien nos gana la carrera mira algo. Si miran el perp y nosotros el
spot, parte del hueco se explica solo con eso.

METODO: **estudio de eventos**, no correlacion. La primera version correlacionaba los cambios de
las dos series en una rejilla y no sirvio — con el mercado plano casi no hay cambios, y ademas la
base perp-spot deriva, asi que la correlacion mide la deriva y no los saltos compartidos. Salio un
"-120 ms" que era puro ruido.

Aqui se hace lo que de verdad hace el bot: esperar un SALTO y medir quien lo canta antes.
  1. Se detecta un salto de >=UMBRAL dolares en VENTANA segundos (sobre la serie de referencia).
  2. Para cada fuente se busca el primer instante en que ESA fuente cruza el mismo umbral desde su
     propio nivel previo, con el mismo signo.
  3. El adelanto es la diferencia entre esos dos instantes, en NUESTRO reloj de recepcion.
Robusto al mercado plano (solo usa los eventos), inmune a la base, y mide exactamente la pregunta
operativa: cuando el precio se mueve, ¿a que canal le llega antes la noticia?

⚠ Hace falta que haya saltos. Con BTC quieto no hay eventos y el script lo dice en vez de inventar
  un numero.

    cd ~/polymarket-btc-up-down/research && python3 futuros.py [segundos]
"""
import bisect, json, sys, time, threading, statistics as st
import websocket

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 180
UMBRAL = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0   # $: salto de verdad, no ruido de tick
PREVIO = 2.0         # s: ventana en la que se mide el salto
SEPARA = 15.0        # s: separacion minima entre eventos, para no contar el mismo dos veces


def mid(b, a):
    try:
        b, a = float(b), float(a)
        return (b + a) / 2 if b > 0 and a > 0 else None
    except Exception:
        return None


FUENTES = {
    "spot bookTicker": ("wss://stream.binance.com:9443/ws/btcusdt@bookTicker", None,
                        lambda x: mid(x.get("b"), x.get("a"))),
    "perp bookTicker": ("wss://fstream.binance.com/ws/btcusdt@bookTicker", None,
                        lambda x: mid(x.get("b"), x.get("a"))),
    "spot aggTrade":   ("wss://stream.binance.com:9443/ws/btcusdt@aggTrade", None,
                        lambda x: float(x["p"]) if x.get("p") else None),
    # perp aggTrade quitado: el canal devolvia 0 mensajes mientras el bookTicker del mismo host
    # traia 44.945. No merece perseguirlo — la comparacion que importa es bookTicker contra
    # bookTicker, que es lo que mira el disparador del bot.
}
REF = "spot bookTicker"          # lo que usa el bot hoy


def main():
    series = {k: [] for k in FUENTES}
    apps = []
    for nom, (url, sub, saca) in FUENTES.items():
        def on_open(w, s=sub):
            if s: w.send(json.dumps(s))
        def on_msg(w, m, nom=nom, saca=saca):
            try:
                v = saca(json.loads(m))
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

    print()
    for nom, v in series.items():
        if len(v) > 5:
            dt = [b[0] - a[0] for a, b in zip(v, v[1:])]
            print(f"  {nom:<18}{len(v):>7} mensajes · uno cada {1000*st.median(dt):>6.0f} ms "
                  f"de mediana")
        else:
            print(f"  {nom:<18}{len(v):>7}  (sin datos — ¿cambio el nombre del canal?)")

    vivos = {k: v for k, v in series.items() if len(v) > 100}
    if REF not in vivos or len(vivos) < 2:
        print("\nmuestra insuficiente para comparar"); return
    keys = {k: [x[0] for x in v] for k, v in vivos.items()}

    def previo(k, t):
        """ultimo precio conocido de k en o antes de t"""
        i = bisect.bisect_right(keys[k], t) - 1
        return vivos[k][i][1] if i >= 0 else None

    def primer_cruce(k, desde, objetivo, d):
        """primer instante, a partir de 'desde', en que k alcanza el objetivo en la direccion d"""
        i = bisect.bisect_left(keys[k], desde)
        v = vivos[k]
        while i < len(v):
            p = v[i][1]
            if (d > 0 and p >= objetivo) or (d < 0 and p <= objetivo): return v[i][0]
            i += 1
        return None

    # ── eventos: saltos de >=UMBRAL en PREVIO s, detectados en CUALQUIERA de las series ──
    # 🐛 La primera version los buscaba SOLO en la de referencia, y eso elegia justo los momentos
    # en que el spot se habia movido — incluido su ruido propio. Resultado: el perp solo cruzaba
    # en 3 de 17 eventos y el spot salia "primero" el 59% de las veces por construccion. Hay que
    # detectar en las dos y quedarse con la union, o la seleccion decide el ganador.
    cand = []
    for k in vivos:
        for t, p in vivos[k]:
            pb = previo(k, t - PREVIO)
            if pb is None: continue
            d = p - pb
            if abs(d) >= UMBRAL: cand.append((t - PREVIO, 1 if d > 0 else -1))
    cand.sort()
    eventos, ultimo = [], -1e9
    for t_base, d in cand:
        if t_base - ultimo < SEPARA: continue
        ultimo = t_base
        eventos.append((t_base, d))
    print(f"\n  saltos de >={UMBRAL:.0f}$ en {PREVIO:.0f}s detectados: {len(eventos)}")
    if len(eventos) < 10:
        print("  ⚠ muy pocos: BTC estuvo demasiado quieto. Repetir con el mercado moviendose,")
        print("    o bajar UMBRAL. No invento un numero con esta muestra.")
        return

    # ── para cada evento, cuando cruza CADA fuente el mismo umbral desde su propio nivel ──
    adelantos = {k: [] for k in vivos if k != REF}
    primeros = {k: 0 for k in vivos}
    validos = 0
    for t_base, d in eventos:
        cruces = {}
        for k in vivos:
            b = previo(k, t_base)
            if b is None: continue
            tc = primer_cruce(k, t_base, b + d * UMBRAL, d)
            if tc is not None: cruces[k] = tc
        if REF not in cruces or len(cruces) < 2: continue
        validos += 1
        primeros[min(cruces, key=cruces.get)] += 1
        for k, tc in cruces.items():
            if k != REF: adelantos[k].append(cruces[REF] - tc)

    print(f"  eventos utilizables (los cruzan al menos dos fuentes): {validos}\n")
    print(f"  {'fuente':<18}{'n':>5}{'adelanto mediana':>19}{'p25':>9}{'p75':>9}"
          f"{'veces 1ª':>11}")
    print(f"  {REF:<18}{validos:>5}{'(referencia)':>19}{'':>9}{'':>9}"
          f"{100*primeros[REF]/validos:>10.0f}%")
    for k, v in adelantos.items():
        if len(v) < 10:
            print(f"  {k:<18}{len(v):>5}   muestra corta"); continue
        v2 = sorted(v)
        q = lambda p: 1000 * v2[min(len(v2) - 1, int(p * len(v2)))]
        print(f"  {k:<18}{len(v):>5}{f'{1000*st.median(v):+.0f} ms':>19}"
              f"{q(.25):>8.0f}{q(.75):>8.0f}{100*primeros.get(k,0)/validos:>10.0f}%")

    # ── separar las dos cosas que la mediana mezcla ──
    # Un adelanto de segundos NO es ventaja de latencia: es que una serie hizo un movimiento que
    # la otra siguio mucho despues (o que son movimientos distintos). Lo que nos sirve a nosotros
    # es el adelanto cuando las dos cantan EL MISMO salto, que es el de menos de un segundo.
    print(f"\n  MISMO SALTO (las dos cruzan con menos de 1 s de diferencia):")
    print(f"  {'fuente':<18}{'n':>5}{'adelanto mediana':>19}{'p25':>9}{'p75':>9}{'% a favor':>11}")
    for k, v in adelantos.items():
        cerca = sorted(x for x in v if abs(x) < 1.0)
        if len(cerca) < 10:
            print(f"  {k:<18}{len(cerca):>5}   muestra corta"); continue
        q = lambda p: 1000 * cerca[min(len(cerca) - 1, int(p * len(cerca)))]
        afav = 100 * sum(1 for x in cerca if x > 0) / len(cerca)
        print(f"  {k:<18}{len(cerca):>5}{f'{1000*st.median(cerca):+.0f} ms':>19}"
              f"{q(.25):>8.0f}{q(.75):>8.0f}{afav:>10.0f}%")

    print("\n  POSITIVO = esa fuente canta el salto ANTES que el spot bookTicker que usa el bot hoy.")
    print("  'veces 1ª' es la fraccion de saltos en que esa fuente fue la primera en cruzar.")
    print("  Si el perpetuo adelanta de forma clara, cambiar el disparador a fstream es tiempo")
    print("  GRATIS: mismo exchange, misma distancia, solo cambia el canal al que escuchamos.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
