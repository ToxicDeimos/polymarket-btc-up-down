"""
stalebt.py — SIMULACIÓN REALISTA: ¿sigue ahí el ask cuando llegamos? booklag confirmó el mecanismo con dos
medidas independientes que dan el mismo número:
  · tras un salto de BTC, a los 200 ms todavía QUEDAN +4,04pp por recorrer al libro (pico de correlación
    cruzada en 0,20 s), y restando medio spread por cruzar salen ~3,5pp;
  · la wallet grande (9.366 operaciones, z +3,26 y +3,20 en dos mitades) bate el precio que paga en +3,0pp,
    PLANO en las siete franjas de precio.
Un camino mira el P&L de un wallet y el otro cronometra dos relojes. Mismo número ⇒ es el mecanismo.

Lo que booklag NO puede decir: que el medio se mueva no significa que haya algo que comprar. El maker puede
CANCELAR en vez de dejarse levantar. Aquí se reconstruye el libro entero desde los eventos del WSS (fotos B
+ cambios C) y se simula de verdad:

    salto de BTC en t  →  esperamos NUESTRA latencia L  →  miramos el mejor ask que EXISTE en ese instante
    y su TAMAÑO  →  compramos lo que haya  →  marcamos contra el medio ya asentado (t+8 s), neto de comisión.

Se barre la latencia (100 ms … 1 s) para ver el presupuesto real, y el tamaño disponible para saber si el
negocio es de céntimos o de dólares. Control: la misma regla comprando el token CONTRARIO debe perder.

    cd ~/polymarket-btc-up-down/research && python3 stalebt.py
"""
import csv, os, sys, glob, math, time, json, bisect, urllib.request
from array import array

DIR = os.path.dirname(__file__)
LAT = (0.1, 0.2, 0.3, 0.5, 0.75, 1.0)
JUMPS = (5.0, 10.0)
JW = 1.0            # ventana del salto, s
SETTLE = 8.0        # cuándo consideramos el libro ya asentado, s
COOL = 10.0         # separación mínima entre disparos, s


LAB = os.path.join(DIR, "lab")


def fee(p): return 0.07 * p * (1 - p)


_WS2CID = None
CACHE = os.path.join(LAB, "clob_reso_stale.csv")


def clob_winner(cid):
    try:
        rq = urllib.request.Request(f"https://clob.polymarket.com/markets/{cid}",
                                    headers={"User-Agent": "stale/1.0"})
        with urllib.request.urlopen(rq, timeout=10) as r: d = json.load(r)
        for t in d.get("tokens", []):
            if t.get("winner") is True: return t.get("outcome")
    except Exception: pass
    return None


def load_reso(need=()):
    """ws → 'Up'/'Down'. queuewatch guarda ws, no cid: el puente está en books_*.csv del laboratorio.
    Las cachés solo tienen ventanas que algún análisis miró ANTES, así que las de hoy hay que pedirlas."""
    global _WS2CID
    if _WS2CID is not None:
        ws2cid = _WS2CID
    else:
        ws2cid = {}
        for path in sorted(glob.glob(os.path.join(LAB, "books_*.csv"))):
            with open(path, encoding="utf-8") as fh:
                rd = csv.reader(fh); next(rd, None)
                for row in rd:
                    if len(row) < 3 or not row[1].startswith("btc-updown-5m-"): continue
                    try: w = int(row[1].split("-")[-1])
                    except Exception: continue
                    if w not in ws2cid: ws2cid[w] = row[2]
        _WS2CID = ws2cid
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv",
               "clob_reso_stale.csv"):
        p = os.path.join(LAB, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]
    falta = [w for w in need if w in ws2cid and ws2cid[w] not in reso]
    sincid = [w for w in need if w not in ws2cid]
    print(f"  puente ws→cid: {len(need)-len(sincid)} de {len(need)} · "
          f"ya en caché: {len(need)-len(sincid)-len(falta)} · por pedir: {len(falta)}", flush=True)
    if falta:
        nuevo = not os.path.exists(CACHE)
        with open(CACHE, "a", newline="", encoding="utf-8") as fo:
            cw = csv.writer(fo)
            if nuevo: cw.writerow(["cid", "winner"])
            for i, w in enumerate(falta):
                cid = ws2cid[w]
                win = clob_winner(cid)
                if win: reso[cid] = win; cw.writerow([cid, win]); fo.flush()
                time.sleep(0.12)
                if (i + 1) % 25 == 0: print(f"    … {i+1}/{len(falta)}", flush=True)
    return {w: reso[c] for w, c in ws2cid.items() if c in reso}


def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def med(xs):
    s = sorted(xs); return s[len(s) // 2] if s else float("nan")


def main():
    paths = sorted(glob.glob(os.path.join(DIR, 'queue_events_*.csv')))
    if not paths: print('no hay queue_events_*.csv'); return
    # Se procesa FICHERO A FICHERO (uno por dia). Cargarlo todo de golpe no cabe en la RAM de la Pi en
    # cuanto hay varios dias: solo se acumulan los DISPAROS, que son unas decenas al dia.
    ACC = {}
    TOT = {'sp': 0, 'ev': 0, 'win': 0, 'fire': {}}
    for p in paths:
        # Un fichero es un DÍA (17 M de puntos de libro). Guardar los eventos crudos en tuplas de Python
        # eran varios GB y la Pi mataba el proceso. Aquí se construye YA la serie compacta que usa el
        # análisis — (ts, bid, ask, tamaño del ask) en arrays — y los eventos crudos no se guardan nunca.
        SP = []; SER = {}
        lv = {}; cur = {}                       # estado incremental por (ws, tok)
        nev = 0
        with open(p, encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                try: t = float(r['ts']); typ = r['typ']
                except Exception: continue
                if typ == 'S':
                    try: SP.append((t, float(r['price'])))
                    except Exception: pass
                elif typ in ('B', 'C'):
                    try:
                        ws = int(r['ws']); px = float(r['price']); sz = float(r['size'] or 0)
                    except Exception: continue
                    tok = r['tok']; k = (ws, tok)
                    nev += 1
                    sd = (r.get('side') or '').lower()
                    if sd.startswith(('b', 'buy')) is False: lv.setdefault(k, {})[px] = sz
                    c = cur.setdefault(k, [None, None])
                    bb = r.get('bb'); ba = r.get('ba')
                    if bb:
                        try: c[0] = float(bb)
                        except Exception: pass
                    if ba:
                        try: c[1] = float(ba)
                        except Exception: pass
                    b, a = c
                    if b is None or a is None or not (0 < b < a < 1): continue
                    s = SER.get(k)
                    if s is None:
                        s = SER[k] = (array('d'), array('f'), array('f'), array('f'))
                    s[0].append(t); s[1].append(b); s[2].append(a)
                    s[3].append(lv.get(k, {}).get(a, 0.0))
        SP.sort()
        wins = set(k[0] for k in SER)
        TOT['sp'] += len(SP); TOT['win'] += len(wins); TOT['ev'] += nev
        print(f"  {os.path.basename(p)}: spot {len(SP):,} · ventanas {len(wins)} · eventos {nev:,}",
              flush=True)
        del lv, cur
        if len(SP) < 200 or not SER: continue
        una_jornada(SP, SER, ACC, TOT)
        del SP, SER
    print()
    print(f"TOTAL: spot {TOT['sp']:,} · ventanas {TOT['win']} · eventos {TOT['ev']:,}", flush=True)
    tablas(ACC, TOT)


def una_jornada(SP, SER, ACC, TOT):
    """SER[(ws, tok)] = (ts, bid, ask, tamaño del ask) en arrays, ya construido al leer el fichero.
    El mejor bid/ask NO se reconstruye: viene en cada evento (bb/ba) del propio exchange. Reconstruirlo
    fallaba porque el filtro de banda de queuewatch deja de actualizar los niveles que se alejan y estos
    se quedan congelados con tamaño >0, apareciendo bids fantasma por encima del ask."""
    sts = array('d', [t for t, _ in SP]); spx = array('d', [p for _, p in SP])
    wins = sorted(set(k[0] for k in SER))
    RES = load_reso(wins)
    # rango temporal de cada ventana, para buscar los saltos donde hay libro
    RANGE = {}
    for (ws, tok), s in SER.items():
        if not len(s[0]): continue
        r = RANGE.setdefault(ws, [s[0][0], s[0][-1]])
        r[0] = min(r[0], s[0][0]); r[1] = max(r[1], s[0][-1])

    def sp_at(t):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= 2.0) else None

    def at(ws, tok, t):
        s = SER.get((ws, tok))
        if not s or not len(s[0]): return None
        i = bisect.bisect_right(s[0], t) - 1
        if i < 0 or t - s[0][i] > 5.0: return None
        b = s[1][i]; a = s[2][i]
        return (b, a, s[3][i], (b + a) / 2)

    # ---------- disparos: se ACUMULAN, las tablas se imprimen al final ----------
    for J in JUMPS:
        FIRE = []
        for ws, (t0, t1) in RANGE.items():
            i = bisect.bisect_left(sts, t0); last = -99
            while i < len(sts) and sts[i] < t1 - SETTLE:
                t = sts[i]; a = sp_at(t - JW)
                if a is not None and abs(spx[i] - a) >= J and t - last > COOL:
                    FIRE.append((ws, t, 'Up' if spx[i] > a else 'Down')); last = t
                i += 1
        TOT['fire'][J] = TOT['fire'].get(J, 0) + len(FIRE)
        bywin = {}
        for ws, t, tok in FIRE: bywin.setdefault(ws, []).append((t, tok))
        for L in LAT:
            a = ACC.setdefault((J, L), {'rows': [], 'mir': [], 'win': 0})
            a['win'] += len(bywin)
            for ws, lst in bywin.items():
                w1 = RES.get(ws)
                for t, tok in lst:
                    other = 'Down' if tok == 'Up' else 'Up'
                    for who, key in ((tok, 'rows'), (other, 'mir')):
                        q = at(ws, who, t + L); r2 = at(ws, who, t + SETTLE)
                        if not q or not r2: continue
                        # r2 = (bid, ask, tam_ask, medio). Para SALIR de verdad hay que cruzar al BID y
                        # pagar otra comisión: el markout contra el medio es el mecanismo, no el dinero.
                        a[key].append({'ask': q[1], 'sz': q[2], 'end': r2[3], 'exit': r2[0],
                                       'won': (1 if w1 == who else 0) if w1 else None})


def stat(xs):
    if len(xs) < 20: return float('nan'), float('nan')
    m = mean(xs)
    v = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) / math.sqrt(len(xs))
    return 100 * m, 100 * v


def tablas(ACC, TOT):
    for J in JUMPS:
        n = TOT['fire'].get(J, 0)
        if n < 25:
            print(f'saltos >= ${J:.0f}: solo {n} - muestra corta'); continue
        print()
        print('=' * 104)
        print(f'  SALTOS DE BTC >= ${J:.0f} en {JW:.0f}s · n={n} · comprar el lado favorecido L despues')
        print('=' * 104)
        print(f"  {'latencia':>10}{'con ask':>9}{'ask':>7}{'tam.':>6}"
              f"{'MECANISMO (mid)':>18}{'espejo':>8}"
              f"{'SALIR cruzando':>18}{'AGUANTAR a resol.':>21}{'$/disparo':>11}")
        for L in LAT:
            a = ACC.get((J, L))
            if not a or len(a['rows']) < 20: continue
            rows = a['rows']; mir = a['mir']
            nm, en = stat([r['end'] - r['ask'] - fee(r['ask']) for r in rows])
            gm, _ = stat([r['end'] - r['ask'] - fee(r['ask']) for r in mir])
            # salir de verdad: vender cruzando al bid a los SETTLE s, con su propia comision
            rt = [r for r in rows if r.get('exit')]
            ne, ee = stat([r['exit'] - r['ask'] - fee(r['ask']) - fee(r['exit']) for r in rt])
            rr = [r for r in rows if r['won'] is not None]
            nr, er = stat([r['won'] - r['ask'] - fee(r['ask']) for r in rr])
            sz = med([r['sz'] for r in rows])
            mejor = max([x for x in (ne, nr) if x == x], default=float('nan'))
            print(f"  {L:>8.2f}s{len(rows):>9}{mean([r['ask'] for r in rows]):>7.3f}{sz:>6.0f}"
                  f"{f'{nm:+.2f} ± {en:.2f}':>18}{gm:>+8.2f}"
                  f"{f'{ne:+.2f} ± {ee:.2f}':>18}{f'{nr:+.2f} ± {er:.2f}':>21}"
                  f"{sz * mejor / 100:>+11.2f}")


    print()
    print("LECTURA: MECANISMO (mid) esta CONFIRMADO: +2,23 +- 0,39 a 0,10 s en saltos de $10 (z 5,7)")
    print("y +1,41 +- 0,29 en los de $5 (z 4,9), decayendo igual en los dos y con el espejo en -6.")
    print("Pero ese numero NO es dinero: mide que compramos por debajo de donde el libro se asienta,")
    print("y para cobrarlo hay que SALIR. Las dos columnas de la derecha son las realizables:")
    print("  · SALIR cruzando  = vender al bid a los 8 s pagando otra comision. Poca varianza.")
    print("  · AGUANTAR        = a resolucion. Mas valor esperado pero su error tipico son ~1,7pp.")
    print("$/disparo toma la mejor de las dos por el tamano disponible: eso es lo que hay que comparar")
    print("con la realidad (la wallet grande gana ~65 $/dia) antes de montar nada.")
    print("Aviso: fee() asume 0,07*p*(1-p). La API devuelve taker_base_fee=1000, sin confirmar que")
    print("signifique. Si la comision real es mayor, la columna de SALIR cruzando es la que mas sufre.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
