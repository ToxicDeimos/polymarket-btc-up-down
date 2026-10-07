"""
camino.py — ¿DÓNDE se van los milisegundos? Descomposición del viaje hasta el CLOB.

El botlog del 7-oct destapó algo que no habíamos mirado: el ms_envio no es un número, es una
distribución muy ancha.

    mediana 384 ms  ·  min 174  ·  máx 5.379     (n=379, con conexión caliente)

Entre la mejor orden y la típica hay **210 ms**, y entre la típica y la peor hay cinco segundos.
Eso NO es física: la distancia a Polymarket no cambia entre una orden y la siguiente. Es algo
nuestro — y es más de lo que podríamos ganar cambiando de fuente de señal o mudándonos de país.

Además la Pi está fallando a ratos contra el CLOB: "Connection reset by peer" en el handshake TLS
(saldo.py) y "read operation timed out" en el propio bot. Puede ser la misma causa.

Aquí se parte el viaje en sus cuatro tramos, con sockets a pelo para poder cronometrarlos por
separado, y se repite N veces para ver la DISPERSIÓN de cada uno (la mediana sola esconde justo lo
que buscamos):

    DNS        resolver clob.polymarket.com
    TCP        abrir la conexión
    TLS        el handshake
    servidor   petición → primer byte, con el handshake YA hecho

Y en cada vuelta se hace una SEGUNDA petición por la misma conexión. La diferencia entre la primera
y la segunda es exactamente lo que cuesta el handshake, que es lo que una conexión persistente
ahorraría. (Ya probamos "calentar la conexión" una vez y no sirvió — 402 vs 401 ms, n=8/9 — pero
con esa muestra no se distinguía nada; aquí se mide en serio.)

También se apunta la IP: Cloudflare devuelve varias y no tienen por qué comportarse igual.

    cd ~/polymarket-btc-up-down/research && python3 camino.py [vueltas]
"""
import socket, ssl, statistics as st, sys, time
from collections import Counter

HOST = "clob.polymarket.com"
RUTA = "/ok"                 # devuelve "OK"; la raíz no responde
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
PAUSA = 0.5


def peticion(ss, host, ruta):
    """Manda una petición por una conexión ya abierta y espera al primer byte."""
    req = (f"GET {ruta} HTTP/1.1\r\nHost: {host}\r\n"
           f"User-Agent: research/1.0\r\nConnection: keep-alive\r\n\r\n")
    t = time.perf_counter()
    ss.sendall(req.encode())
    d = ss.recv(8192)
    if not d: raise ConnectionError("el otro lado cerró sin responder")
    return time.perf_counter() - t, d


def q(v, p):
    v = sorted(v); return 1000 * v[min(len(v) - 1, int(p * len(v)))]


def linea(nom, v):
    if not v:
        print(f"  {nom:<22}  (sin muestra)"); return
    print(f"  {nom:<22}{1000*st.median(v):>9.1f}{q(v,.10):>9.1f}{q(v,.90):>9.1f}"
          f"{q(v,.99):>10.1f}{1000*min(v):>9.1f}{1000*max(v):>10.1f}{len(v):>6}")


def main():
    dns, tcp, tls, srv1, srv2, total = [], [], [], [], [], []
    ips = Counter(); fallos = Counter()
    print(f"{N} vueltas contra {HOST}{RUTA} (no se manda ninguna orden)\n")

    for i in range(N):
        s = ss = None
        try:
            t0 = time.perf_counter()
            infos = socket.getaddrinfo(HOST, 443, socket.AF_INET, socket.SOCK_STREAM)
            dns.append(time.perf_counter() - t0)
            ip = infos[0][4][0]; ips[ip] += 1

            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            t1 = time.perf_counter(); s.connect((ip, 443)); tcp.append(time.perf_counter() - t1)

            ctx = ssl.create_default_context()
            t2 = time.perf_counter()
            ss = ctx.wrap_socket(s, server_hostname=HOST)
            tls.append(time.perf_counter() - t2)

            d1, _ = peticion(ss, HOST, RUTA); srv1.append(d1)
            d2, _ = peticion(ss, HOST, RUTA); srv2.append(d2)
            total.append(time.perf_counter() - t0)
        except Exception as e:
            fallos[type(e).__name__] += 1
        finally:
            for x in (ss, s):
                try:
                    if x: x.close()
                except Exception: pass
        sys.stdout.write(f"\r  {i+1}/{N}"); sys.stdout.flush()
        time.sleep(PAUSA)
    print("\n")

    print("=" * 84)
    print(f"  {'tramo':<22}{'mediana':>9}{'p10':>9}{'p90':>9}{'p99':>10}{'min':>9}{'máx':>10}{'n':>6}")
    print("=" * 84)
    linea("DNS", dns)
    linea("TCP (abrir)", tcp)
    linea("TLS (handshake)", tls)
    linea("servidor 1ª pet.", srv1)
    linea("servidor 2ª pet.", srv2)
    linea("TOTAL en frío", total)

    if srv1 and srv2:
        ah = st.median(srv1) - st.median(srv2)
        hs = st.median(dns) + st.median(tcp) + st.median(tls)
        print(f"\n  handshake completo (DNS+TCP+TLS): {1000*hs:.0f} ms de mediana")
        print(f"  una conexión persistente ahorraría ESO en cada orden.")
        if abs(ah) > 0.005:
            print(f"  (la 2ª petición por la misma conexión va {1000*ah:+.0f} ms respecto a la 1ª)")

    if ips:
        print(f"\n  IPs servidas: " + " · ".join(f"{k} ×{v}" for k, v in ips.most_common()))
    if fallos:
        print(f"  ⚠ FALLOS: " + " · ".join(f"{k} ×{v}" for k, v in fallos.items())
              + f"   ({100*sum(fallos.values())/N:.0f}% de las vueltas)")
        print("    La conexión con el CLOB no es fiable desde aquí, y eso ya es un hallazgo.")
    else:
        print(f"  sin fallos en {N} vueltas")

    print("\n" + "=" * 84)
    print("  CÓMO LEERLO. El bot manda la orden por una conexión que el SDK abre por su cuenta.")
    print("  Si el handshake son ~200 ms y la mediana de ms_envio es 384, entonces MÁS DE LA MITAD")
    print("  de lo que tardamos es abrir la conexión, no esperar a Polymarket — y eso se arregla")
    print("  con una conexión persistente, que es gratis y no depende de dónde estemos.")
    print("  Si el handshake es pequeño y la dispersión está en 'servidor', el tiempo es suyo y")
    print("  no hay nada que hacer desde aquí.")
    print("=" * 84)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
