"""
naiveflow.py — Diecinueve intentos han muerto por el mismo sitio: cruzamos el spread y pagamos 2,8pp
(1,1 de medio spread + 1,7 de comisión) por un bruto de 1-2pp. El maker no paga comisión, cobra rebate y se
queda medio spread; lo descartamos por SELECCIÓN ADVERSA (nos llenan cuando nos equivocamos, acierto 33-43%).

Pero openskew encontró un PERDEDOR SISTEMÁTICO: el "líder ingenuo" (precio vs APERTURA, la regla vieja) pierde
−3,6 a −4,1pp cuando discrepa del líder real (precio vs MEDIA previa a la apertura, la regla D3 verdadera).
Hay dinero equivocado circulando, y no es aleatorio. Entonces la pregunta no es "¿me llenan?" sino "¿QUIÉN me
llena?". Si en los momentos de discrepancia el flujo AGRESOR va al lado ingenuo, ponerle la orden enfrente no
es exponerse a la selección adversa: es cobrársela.

PARTE 1 — ¿EXISTE ese flujo? Con la cinta real: en los momentos de discrepancia, ¿cuánta agresión compra al
  líder INGENUO frente al REAL, y cuánto gana cada flujo? Control: los momentos en que AMBOS coinciden.
  Si el flujo está equilibrado o favorece al real, la idea muere aquí y sale barata.
PARTE 2 — MAKER contra ese flujo: colocamos ask en el lado ingenuo (= comprar el real a su bid), nos llena la
  cinta, aguantamos a resolución. Sin comisión de taker, +rebate (20% de la comisión). DOS modelos de relleno:
  OPTIMISTA (cualquier operación a ≥ nuestro precio nos llena) y BARRIDO (solo si la operación pasa POR ENCIMA
  de nuestro nivel, que es el único caso en que la cola no importa) — porque el relleno real medido fue 22,7%,
  no 93,7% [[fill-maker-mal-medido]].
PARTE 3 — controles: el MISMO maker en momentos de acuerdo (no debería ganar), y al revés (ask en el lado
  real, que debería perder claramente). Más puertas tr/te, semanas+ y EV sin el 1% mejor.

    cd ~/polymarket-btc-up-down/research && python3 naiveflow.py
"""
import csv, os, sys, glob, json, time, math, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
T_TWAP = 1786060800
T_5M60 = 1786665600
SCAN = [0.15 + 0.05 * i for i in range(13)]      # 15% … 75% de la ventana
HOLD = 45          # s que dejamos la orden puesta antes de reevaluar
TICK = 0.01


def regime(v, ws):
    if ws < T_TWAP: return "puntual", 60
    if v == "5m" and ws < T_5M60: return "TWAP30", 30
    return "TWAP60", 60


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nflow/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.3)


def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if isinstance(d, dict):
        for t in d.get("tokens", []):
            if t.get("winner") is True: return t.get("outcome")
    return None


def fee(p): return 0.07 * p * (1 - p)
def rebate(p): return 0.20 * fee(p)
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def med(xs):
    s = sorted(xs); return s[len(s) // 2] if s else float("nan")
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def load_books():
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 12 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None or bid is None: continue
                w = tmp.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})
                w[row[3]].append((ts, ask, bid))
    W = {}
    for slug, w in tmp.items():
        if not w["Up"] or not w["Down"]: continue
        W[slug] = {"cid": w["cid"], "ws": int(slug.split("-")[-1]), "slug": slug,
                   "v": "5m" if "-5m-" in slug else "15m"}
        for s in ("Up", "Down"):
            r = sorted(w[s]); W[slug][s] = ([x[0] for x in r], [x[1] for x in r], [x[2] for x in r])
    return W


def load_tape(cids):
    """cinta real de la ventana. Devuelve cid -> [(ts, outcome, side, price, size)]"""
    T = {}; seen = set(); sides = {}
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                cid = r.get("cid")
                if cid not in cids or r.get("outcome") not in ("Up", "Down"): continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"), r.get("trade_side"), r.get("ts_trade"))
                if k in seen: continue
                seen.add(k)
                try: ts = int(float(r["ts_trade"])); pr = float(r["price"])
                except Exception: continue
                sz = 0.0
                for key in ("size", "amount", "shares", "qty"):
                    if r.get(key):
                        try: sz = float(r[key]); break
                        except Exception: pass
                sd = (r.get("trade_side") or r.get("side") or "").upper()
                sides[sd] = sides.get(sd, 0) + 1
                T.setdefault(cid, []).append((ts, r["outcome"], sd, pr, sz))
    for cid in T: T[cid].sort()
    return T, sides


def report(rows, lab):
    """rows: {'ws','pl'} con pl ya en unidades de 1 share."""
    if len(rows) < 30:
        print(f"  {lab:>34}{'(pocos)':>9}"); return
    def ev(s): return 100 * mean([x["pl"] for x in s])
    mid = sorted(r["ws"] for r in rows)[len(rows) // 2]
    tr = [r for r in rows if r["ws"] < mid]; te = [r for r in rows if r["ws"] >= mid]
    ps = sorted(rows, key=lambda r: r["pl"]); k = max(1, int(len(rows) * 0.01))
    byw = {}
    for r in rows: byw.setdefault(week(r["ws"]), []).append(r)
    wt = [s for s in byw.values() if len(s) >= 10]
    wp = sum(1 for s in wt if ev(s) > 0)
    sd = math.sqrt(mean([(x["pl"] - mean([y["pl"] for y in rows])) ** 2 for x in rows]))
    z = (mean([x["pl"] for x in rows]) / (sd / math.sqrt(len(rows)))) if sd > 0 else float("nan")
    print(f"  {lab:>34}{len(rows):>7}{ev(rows):>+9.2f}"
          f"{(ev(tr) if len(tr)>=10 else float('nan')):>+8.2f}{(ev(te) if len(te)>=10 else float('nan')):>+8.2f}"
          f"{100*mean([x['pl'] for x in ps[:-k]]):>+9.2f}{f'{wp}/{len(wt)}':>8}{z:>+7.2f}")


HDR = f"  {'caso':>34}{'n':>7}{'EV pp':>9}{'tr':>8}{'te':>8}{'-top1%':>9}{'sem+':>8}{'z':>7}"


def main():
    sts, spx = load_spot(); W = load_books()
    TP, sides = load_tape(set(w["cid"] for w in W.values()))
    print(f"ventanas: {len(W)} · con cinta: {len(TP)} · ticks spot: {len(sts)}")
    print("  etiquetas de lado en la cinta: " + " · ".join(f"{k or '(vacío)'}: {v}" for k, v in
                                                            sorted(sides.items(), key=lambda kv: -kv[1])[:6]))

    def avg(a, b):
        lo = bisect.bisect_left(sts, a); hi = bisect.bisect_right(sts, b)
        if hi - lo < 2: return None
        seg = spx[lo:hi]; return sum(seg) / len(seg)

    def le(t, tol):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= tol) else None

    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]

    def resolve(cid):
        if cid in reso: return reso[cid]
        w = winner_clob(cid); time.sleep(0.1)
        if w:
            reso[cid] = w; nf = not os.path.exists(CACHE)
            with open(CACHE, "a", newline="", encoding="utf-8") as fo:
                cw = csv.writer(fo)
                if nf: cw.writerow(["cid", "winner"])
                cw.writerow([cid, w])
        return w

    # ---------- momentos: discrepa / coincide ----------
    MOM = []
    done = 0
    for slug, w in W.items():
        v = w["v"]; ws = w["ws"]; wlen = 300 if v == "5m" else 900; close = ws + wlen
        reg, L = regime(v, ws)
        if reg == "puntual" or w["cid"] not in TP: continue
        ref = avg(ws - L, ws); p0 = le(ws + 2, 8)
        if ref is None or p0 is None: continue
        done += 1
        if done % 2000 == 0: print(f"   … {done}")
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        for f in SCAN:
            t = ws + int(f * wlen)
            if t > close - 60: continue
            cur = le(t, 12)
            if cur is None: continue
            mr = cur - ref; mn = cur - p0
            if mr == 0 or mn == 0: continue
            R = "Up" if mr > 0 else "Down"; N = "Up" if mn > 0 else "Down"

            def bk(X):
                tss, asks, bids = w[X]; k = bisect.bisect_left(tss, t)
                if k < len(tss) and tss[k] <= t + 15 and tss[k] < close - 20:
                    a, b = asks[k], bids[k]
                    if 0 < b < a < 1: return a, b
                return None, None
            aN, bN = bk(N); aR, bR = bk(R)
            if aN is None or aR is None: continue
            MOM.append({"ws": ws, "v": v, "cid": w["cid"], "t": t, "close": close,
                        "R": R, "N": N, "dis": R != N, "win": win,
                        "aN": aN, "bN": bN, "aR": aR, "bR": bR})
    print(f"momentos: {len(MOM)} · discrepan {sum(1 for m in MOM if m['dis'])} "
          f"({100*mean([1 if m['dis'] else 0 for m in MOM]):.0f}%)")

    # ================= PARTE 1 — ¿EXISTE EL FLUJO EQUIVOCADO? =================
    print("\n" + "=" * 104)
    print("  PARTE 1 — AGRESIÓN en los 45 s siguientes: ¿compra al líder INGENUO o al REAL? ¿y quién gana?")
    print("=" * 104)
    print(f"  {'caso':>26}{'momentos':>10}{'compras N':>11}{'compras R':>11}{'sesgo N':>9}"
          f"{'gana N':>9}{'gana R':>9}{'precio N':>10}")
    for v in ("5m", "15m"):
        for dis in (True, False):
            ms = [m for m in MOM if m["v"] == v and m["dis"] == dis]
            if len(ms) < 50: continue
            cN = cR = 0; pN = []; wN = []
            for m in ms:
                for ts, oc, sd, pr, sz in TP[m["cid"]]:
                    if not (m["t"] <= ts <= m["t"] + HOLD): continue
                    if sd not in ("BUY", "BUY_SIDE", ""): continue
                    if oc == m["N"]: cN += 1; pN.append(pr); wN.append(1 if m["win"] == m["N"] else 0)
                    elif oc == m["R"]: cR += 1
            tot = cN + cR
            lab = f"{v} · {'DISCREPAN' if dis else 'coinciden'}"
            gN = 100 * mean([1 if m["win"] == m["N"] else 0 for m in ms])
            gR = 100 * mean([1 if m["win"] == m["R"] else 0 for m in ms])
            print(f"  {lab:>26}{len(ms):>10}{cN:>11}{cR:>11}"
                  f"{(100*cN/tot if tot else float('nan')):>8.0f}%{gN:>8.0f}%{gR:>8.0f}%"
                  f"{med(pN) if pN else float('nan'):>10.3f}")
    print("  (sesgo N > 50% en DISCREPAN = la agresión va al lado equivocado → hay a quién cobrarle)")

    # ================= PARTE 2 — MAKER CONTRA ESE FLUJO =================
    print("\n" + "=" * 104)
    print(f"  PARTE 2 — MAKER: ask en el lado INGENUO (≡ comprar el REAL a su bid), relleno por la CINTA,")
    print(f"            aguantar a resolución · sin comisión de taker, con rebate · orden viva {HOLD}s")
    print("=" * 104)

    def maker(ms, sell_side, sweep):
        """vendemos `sell_side` al mejor ask; nos llena la cinta. pl por share vendida."""
        out = []
        for m in ms:
            X = m[sell_side]                       # lado que vendemos
            px = m["a" + ("N" if sell_side == "N" else "R")]
            need = px + (TICK if sweep else 0.0)
            hit = None
            for ts, oc, sd, pr, sz in TP[m["cid"]]:
                if not (m["t"] <= ts <= m["t"] + HOLD): continue
                if oc != X or sd not in ("BUY", "BUY_SIDE", ""): continue
                if pr >= need - 1e-9: hit = pr; break
            if hit is None: continue
            # vendemos a px: cobramos px, pagamos 1 si X gana. Maker: sin fee, +rebate.
            pl = px - (1 if m["win"] == X else 0) + rebate(px)
            out.append({"ws": m["ws"], "pl": pl})
        return out

    for v in ("5m", "15m"):
        print(f"\n  ── {v} ──")
        print(HDR)
        for sweep in (False, True):
            tag = "BARRIDO" if sweep else "optimista"
            dis = [m for m in MOM if m["v"] == v and m["dis"]]
            agr = [m for m in MOM if m["v"] == v and not m["dis"]]
            report(maker(dis, "N", sweep), f"[{tag}] vender el INGENUO · DISCREPAN")
            report(maker(agr, "N", sweep), f"[{tag}] control: momentos de acuerdo")
            report(maker(dis, "R", sweep), f"[{tag}] control: vender el REAL (al revés)")

    print("\nLECTURA: PARTE 1 decide si la idea tiene base — sesgo N claramente >50% en DISCREPAN significa que")
    print("la agresión compra al lado que pierde 4pp y hay a quién cobrarle. PARTE 2 lo cobra: exijo que")
    print("'vender el INGENUO · DISCREPAN' sea + en tr Y te con -top1% positivo, que 'momentos de acuerdo' sea")
    print("~0 (si también gana, lo que cobramos es el spread y no la discrepancia) y que 'vender el REAL' sea")
    print("claramente NEGATIVO. La fila que manda es la de BARRIDO: es la única donde la cola no nos deja fuera.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
