"""
vigila.py — SUPERVISOR DEL COLECTOR

La noche del 1 al 2 de octubre el colector estuvo 11,3 h vivo pero MUDO: el PC encendido, ni un
evento de suspension, ni de red, ni de disco en el registro de Windows, y aun asi dejo de escribir
filas a las 22:33 y no volvio. No se pudo diagnosticar porque al rearrancarlo se sobrescribio su log
(`RedirectStandardOutput` trunca).

Cuando un fallo no se puede diagnosticar, lo que toca es que se cure solo y deje pruebas:

  · cada arranque escribe a su PROPIO fichero con fecha, asi no se pisa nada nunca mas
  · si el CSV no crece en 25 min, da el proceso por colgado, lo mata y lo rearranca
  · todo rearranque queda anotado en vigila.log, con la hora

El umbral de 25 min no es arbitrario: el ritmo medido es ~140 disparos/hora, y hasta en la hora mas
tranquila que hemos visto (20/h) la probabilidad de pasar 25 minutos sin UN solo disparo es del 0,03%.
Si pasa, es que algo esta roto.

    python vigila.py          # se queda en primer plano supervisando
"""
import subprocess, time, os, sys, datetime

DIR  = os.path.dirname(os.path.abspath(__file__))
CSV  = os.path.join(DIR, "pcpaper_v4.csv")
DIAR = os.path.join(DIR, "vigila.log")
MUDO = 25 * 60


def apunta(txt):
    linea = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {txt}"
    with open(DIAR, "a", encoding="utf-8") as f: f.write(linea + "\n")
    print(linea, flush=True)


def tam():
    try: return os.path.getsize(CSV)
    except OSError: return -1


def arranca():
    nom = f"pcpaper_{datetime.datetime.now():%Y%m%d_%H%M%S}.out"
    env = dict(os.environ, STALEPAPER_PREFIJO="pcpaper")
    sal = open(os.path.join(DIR, nom), "wb")
    kw = {}
    if os.name == "nt": kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    p = subprocess.Popen([sys.executable, "stalepaper.py"], cwd=DIR, env=env,
                         stdout=sal, stderr=subprocess.STDOUT, **kw)
    apunta(f"colector arrancado · PID {p.pid} · log {nom}")
    return p


def main():
    apunta("supervisor en marcha")
    p = arranca()
    visto, ultimo = tam(), time.time()
    while True:
        time.sleep(60)
        if p.poll() is not None:
            apunta(f"el colector MURIO (codigo {p.returncode}) · rearranco")
            p = arranca(); visto, ultimo = tam(), time.time(); continue
        t = tam()
        if t != visto:
            visto, ultimo = t, time.time()
        elif time.time() - ultimo > MUDO:
            mudo = (time.time() - ultimo) / 60
            apunta(f"COLGADO: {mudo:.0f} min sin una fila nueva · mato {p.pid} y rearranco")
            try: p.kill()
            except Exception: pass
            time.sleep(3)
            p = arranca(); visto, ultimo = tam(), time.time()


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    try: main()
    except KeyboardInterrupt: apunta("supervisor parado a mano")
