"""
siguen.py — ¿LOS GANADORES SIGUEN GANANDO? El test que nunca hicimos.

En julio-agosto fichamos a 13mm-wrench, izzyaussie y compañia BUSCANDO QUIEN HABIA GANADO. Entre
miles de wallets operando miles de ventanas, los mejores VAN a parecer excelentes por puro azar:
su ROI medido esta sesgado al alza por construccion de la muestra. Nunca lo comprobamos, y encima
montamos cuatro experimentos encima (mirror, criteria, imbalance_gap, mm profundo).

survivorship.py se escribio para contestarlo con el nulo de Monte Carlo, pero necesitaba los
tape_*.csv (todas las wallets) y esos murieron con la SD.

Este test es mas barato y MEJOR, porque es fuera de muestra de verdad: los elegimos con datos de
hasta primeros de agosto. Si su edge era real, **deberia seguir ahi en septiembre y octubre**.
Si era loteria, se habra desinflado. No hace falta el nulo: el tiempo hace de grupo de control.

Que mide, por wallet y por mes:
  · n operaciones, volumen en acciones y en dolares
  · P&L a resolucion, y el edge por accion en puntos porcentuales (P&L / acciones)
    BUY  de O a precio p: gana (1−p) si O resuelve ganador, pierde p si no  → pnl = s·(gano − p)
    SELL de O a precio p: al reves                                          → pnl = s·(p − gano)
  · se informa BRUTO (son makers, y el maker no paga comision) y tambien con tarifa de taker
    como cota inferior, porque /trades no dice si fueron maker o taker.

⚠ La Data API agota la paginacion (offset ~>10k da 400), asi que el historico mas antiguo puede
  quedar cortado; el script avisa de hasta donde llego.

    cd ~/polymarket-btc-up-down/research && python3 siguen.py
"""
import json, sys, time, urllib.request, urllib.error
from collections import defaultdict

GANADORES = {
    "13mm-wrench": "0x57f2faf2eb75fd26bce0b5baf5ee7ffaadd66356",
    "izzyaussie":  "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2",
    "zmbabwe":     "0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7",
    "w-f3a6":      "0xf3a6ef82d0904db48c0ad8016ca62c556fee8c6c",
    "w-9a2f":      "0x9a2f9100cd8accb9bb8ab1e3e025b042c0d5c62b",
    "w-0445":      "0x04454d6a686c5909724dc6a27555875eb86ebbf9",
}
PAGINA = 500
PAUSA = 0.15


def get(url, reintentos=3):
    for i in range(reintentos):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(r, timeout=40) as f:
                return json.loads(f.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None                      # paginacion agotada
            if i == reintentos - 1:
                raise
        except Exception:
            if i == reintentos - 1:
                raise
        time.sleep(1.5 * (i + 1))
    return None


def fee(p):
    return 0.07 * p * (1 - p)


def mes(ts):
    return time.strftime("%Y-%m", time.gmtime(ts))


def operaciones(addr):
    """Todas las operaciones de una wallet, de la mas reciente hacia atras."""
    out, off = [], 0
    while True:
        d = get(f"https://data-api.polymarket.com/trades?user={addr}&limit={PAGINA}&offset={off}")
        if d is None:
            return out, True                      # cortado por la API
        if not d:
            return out, False
        out += d
        if len(d) < PAGINA:
            return out, False
        off += PAGINA
        time.sleep(PAUSA)


def resoluciones(slugs):
    """slug -> indice del outcome ganador (0=Up, 1=Down), en lotes."""
    gan, slugs = {}, sorted(slugs)
    for i in range(0, len(slugs), 100):
        lote = slugs[i:i + 100]
        u = ("https://gamma-api.polymarket.com/markets?closed=true&limit=100&"
             + "&".join(f"slug={s}" for s in lote))
        d = get(u) or []
        for m in d:
            try:
                px = json.loads(m["outcomePrices"])
                gan[m["slug"]] = 0 if float(px[0]) > 0.5 else 1
            except Exception:
                pass
        sys.stdout.write(f"\r  resoluciones {min(i+100, len(slugs))}/{len(slugs)}")
        sys.stdout.flush()
        time.sleep(PAUSA)
    print()
    return gan


def main():
    print("Descargando operaciones…\n")
    todo = {}
    for nom, addr in GANADORES.items():
        ops, cortado = operaciones(addr)
        ups = [o for o in ops if (o.get("slug") or "").startswith("btc-updown")]
        if ops:
            tss = [o["timestamp"] for o in ops]
            ult = time.strftime("%Y-%m-%d", time.gmtime(max(tss)))
            pri = time.strftime("%Y-%m-%d", time.gmtime(min(tss)))
        else:
            ult = pri = "—"
        print(f"  {nom:<13} {len(ops):>6} ops ({len(ups):>6} en btc-updown) · "
              f"de {pri} a {ult}{'  ⚠ historico cortado por la API' if cortado else ''}")
        todo[nom] = ups

    slugs = {o["slug"] for v in todo.values() for o in v}
    print(f"\nResolviendo {len(slugs)} mercados…")
    gan = resoluciones(slugs)
    print(f"  resueltos {len(gan)} de {len(slugs)}")

    print("\n" + "=" * 86)
    print("  ¿SIGUEN GANANDO? — P&L a resolucion por mes (solo btc-updown)")
    print("=" * 86)
    for nom, ops in todo.items():
        porm = defaultdict(lambda: [0, 0.0, 0.0, 0.0])   # n, acciones, pnl bruto, pnl con fee taker
        for o in ops:
            g = gan.get(o["slug"])
            if g is None:
                continue
            try:
                p, s = float(o["price"]), float(o["size"])
            except Exception:
                continue
            gano = 1.0 if int(o["outcomeIndex"]) == g else 0.0
            pnl = s * (gano - p) if o["side"] == "BUY" else s * (p - gano)
            a = porm[mes(o["timestamp"])]
            a[0] += 1; a[1] += s; a[2] += pnl; a[3] += pnl - s * fee(p)
        if not porm:
            print(f"\n  {nom}: sin operaciones resueltas en btc-updown"); continue
        print(f"\n  {nom}")
        print(f"    {'mes':<9}{'ops':>7}{'acciones':>11}{'P&L $':>11}{'pp/accion':>12}{'pp si taker':>13}")
        for m in sorted(porm):
            n, sh, b, t = porm[m]
            print(f"    {m:<9}{n:>7}{sh:>11.0f}{b:>11.2f}{100*b/sh:>12.2f}{100*t/sh:>13.2f}")
        n = sum(v[0] for v in porm.values()); sh = sum(v[1] for v in porm.values())
        b = sum(v[2] for v in porm.values()); t = sum(v[3] for v in porm.values())
        print(f"    {'TOTAL':<9}{n:>7}{sh:>11.0f}{b:>11.2f}{100*b/sh:>12.2f}{100*t/sh:>13.2f}")

    print("\n" + "=" * 86)
    print("  Los fichamos con datos de hasta ~primeros de AGOSTO. Todo lo de septiembre en")
    print("  adelante es fuera de muestra: si el 'pp/accion' se mantiene, hubo habilidad;")
    print("  si se desinfla hacia 0, los eligio el azar y no habia nada que copiar.")
    print("=" * 86)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
