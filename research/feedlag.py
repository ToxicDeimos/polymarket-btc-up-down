"""
feedlag.py — ¿EL LIBRO QUE LEEMOS VA RETRASADO?

En la sesión en vivo mueren órdenes contra niveles con 267, 389 y 463 acciones. Que un nivel así se
evapore en 400 ms, una y otra vez, es difícil de creer. Hay una explicación alternativa que no hemos
comprobado nunca: **que nuestro feed WSS del libro vaya por detrás de la realidad**.

Si va retrasado, no es que lleguemos tarde: es que apuntamos a un precio que ya no existía cuando lo
vimos. Y sería mucho peor que un problema de ejecución, porque el markout del mecanismo se calcula
contra ESE MISMO libro — o sea que contaminaría toda la medición, no solo las órdenes.

La prueba es directa: mantener abierta la misma suscripción que usa el bot y, cada pocos segundos,
pedir el libro por REST (que es lo que ve el motor de emparejamiento) y comparar.

  · si coinciden casi siempre  -> el feed está bien y el problema es de latencia de la orden
  · si el WSS va por detrás    -> el fallo es de medición y hay que repensarlo todo

    cd ~/polymarket-btc-up-down/research && python3 feedlag.py [segundos]
"""
import json, sys, time, threading, collections
import websocket

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from stalebot import discover, get, WSS          # noqa: E402

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 180


def mejor_ask_rest(tok_id):
    d = get(f"https://clob.polymarket.com/book?token_id={tok_id}")
    if not isinstance(d, dict): return None, 0.0
    asks = [(float(a["price"]), float(a["size"])) for a in d.get("asks", []) if a.get("price")]
    if not asks: return None, 0.0
    # el CLOB devuelve los asks de mayor a menor; el mejor para comprar es el MAS BARATO
    p, s = min(asks, key=lambda x: x[0])
    return p, s


def main():
    t = time.time(); ws = int(t - (t % 300))
    mk = discover(ws) or discover(ws + 300)
    if not mk: print("no hay mercado ahora mismo"); return
    toks = mk["toks"]; id2 = {toks["Up"]: "Up", toks["Down"]: "Down"}
    bk = {"Up": [None, 0.0], "Down": [None, 0.0]}      # [ask, size] segun el WSS
    lv = {"Up": {}, "Down": {}}

    def on_msg(w, msg):
        try: ev = json.loads(msg)
        except Exception: return
        for d in (ev if isinstance(ev, list) else [ev]):
            et = d.get("event_type")
            if et == "book":
                tok = id2.get(d.get("asset_id"))
                if not tok: continue
                lv[tok] = {float(a["price"]): float(a["size"]) for a in d.get("asks", [])}
                vivos = [(p, s) for p, s in lv[tok].items() if s > 0]
                if vivos:
                    p, s = min(vivos, key=lambda x: x[0]); bk[tok] = [p, s]
            elif et == "price_change":
                for ch in d.get("price_changes", []):
                    tok = id2.get(ch.get("asset_id"))
                    if not tok: continue
                    try:
                        if (ch.get("side") or "").upper().startswith("S"):
                            lv[tok][float(ch["price"])] = float(ch.get("size", 0))
                        ba = ch.get("best_ask")
                        if ba not in (None, ""):
                            bk[tok] = [float(ba), lv[tok].get(float(ba), 0.0)]
                    except Exception: pass

    app = websocket.WebSocketApp(WSS, on_open=lambda w: w.send(json.dumps(
        {"type": "market", "assets_ids": [toks["Up"], toks["Down"]]})), on_message=on_msg,
        on_error=lambda a, b: None)
    threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10),
                     daemon=True).start()
    time.sleep(4)

    print(f"comparando {DUR}s · ventana {ws}\n")
    print(f"  {'hora':>8}{'lado':>6}{'ask WSS':>10}{'ask REST':>10}{'dif':>8}{'sz WSS':>9}{'sz REST':>9}")
    difs = collections.Counter(); n = 0; fin = time.time() + DUR
    while time.time() < fin:
        for tok in ("Up", "Down"):
            w_p, w_s = bk[tok]
            if w_p is None: continue
            r_p, r_s = mejor_ask_rest(toks[tok])
            if r_p is None: continue
            n += 1
            d = round(r_p - w_p, 4)
            difs[d] += 1
            if d:
                print(f"  {time.strftime('%H:%M:%S'):>8}{tok:>6}{w_p:>10.3f}{r_p:>10.3f}"
                      f"{d:>+8.3f}{w_s:>9.0f}{r_s:>9.0f}")
        time.sleep(2)
    app.close()

    print(f"\n  {n} comparaciones")
    ig = difs.get(0.0, 0)
    print(f"  coinciden exactamente: {ig} ({100*ig/max(n,1):.0f}%)")
    for d in sorted(x for x in difs if x):
        print(f"    REST {d:+.3f} respecto al WSS: {difs[d]} veces ({100*difs[d]/n:.0f}%)")
    print("\n  Si coinciden casi siempre, el feed esta bien y el problema es la latencia de la orden.")
    print("  Si el REST sale sistematicamente MAS CARO, nuestro libro va por detras y apuntamos a")
    print("  precios que ya no existen — y eso contaminaria tambien el markout del mecanismo.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
