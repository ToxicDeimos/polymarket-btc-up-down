"""
endwindow_settle.py — DESEMPATE LIMPIO. La auditoría mostró que los datos están sanos, pero 'win_finalask'
usaba el ÚLTIMO snapshot, que puede ser POST-cierre (el colector sondea ~30s tras el cierre → el ganador ya
saltó a ~1,0 al resolver). Aquí separo, por semana, el ask del GANADOR:
  aT15 = último snapshot con ts ≤ ws+285  (15s antes del cierre, PRE)
  aT5  = último snapshot con ts ≤ ws+295  ( 5s antes, PRE)
  aPre = último snapshot con ts ≤ ws+300  (justo al cierre, PRE)
  aPost= primer  snapshot con ts ≥ ws+305  (tras resolución, POST)
  EV_T15 = 100·(1 − aT15 − fee) = comprar el ganador a T-15 y aguantar a resolución (COTA: asume conocer al
           ganador; en la práctica lo das con el spot al ~89-99% en movimientos decisivos)

Si aPre≈aPost≈0,95 → el mercado converge ANTES del cierre → NO hay desfase (mis scripts contaminados con
post-cierre). Si aPre≈0,60 y aPost≈0,95 → el mercado VA REZAGADO hasta la resolución → desfase REAL; y si es
estable en todas las semanas, sobrevive el gate de estabilidad.

    cd ~/polymarket-btc-up-down/research && python3 endwindow_settle.py
"""
import csv, os, sys, glob, json, time, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
WLEN = 300
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "settle/1.0"})
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
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_books():
    W = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-5m-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                w = W.setdefault(row[1], {"cid": row[2], "ws": int(row[1].split("-")[-1]),
                                         "asks": {"Up": [], "Down": []}})
                w["asks"][row[3]].append((ts, ask))
    return W


def last_le(rows, t):
    v = None
    for ts, a in rows:
        if ts <= t: v = a
        else: break
    return v


def first_ge(rows, t):
    for ts, a in rows:
        if ts >= t: return a
    return None


def main():
    W = load_books()
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: reso[r["cid"]] = r["winner"]

    def resolve(cid):
        if cid in reso: return reso[cid]
        w = winner_clob(cid); time.sleep(0.1)
        if w:
            reso[cid] = w; nf = not os.path.exists(CACHE)
            with open(CACHE, "a", newline="", encoding="utf-8") as f:
                cw = csv.writer(f)
                if nf: cw.writerow(["cid", "winner"])
                cw.writerow([cid, w])
        return w

    agg = {}
    print(f"midiendo {len(W)} ventanas 5m…")
    done = 0
    for slug, w in W.items():
        ws = w["ws"]; wk = week(ws)
        win = resolve(w["cid"]); done += 1
        if done % 3000 == 0: print(f"   … {done}/{len(W)}")
        if win not in ("Up", "Down"): continue
        wser = sorted(w["asks"][win])
        if not wser: continue
        aT15 = last_le(wser, ws + 285); aT5 = last_le(wser, ws + 295)
        aPre = last_le(wser, ws + 300); aPost = first_ge(wser, ws + 305)
        a = agg.setdefault(wk, {"n": 0, "t15": [], "t5": [], "pre": [], "post": [], "ev": []})
        a["n"] += 1
        if aT15 is not None: a["t15"].append(aT15); a["ev"].append(100 * (1 - aT15 - fee(aT15)))
        if aT5 is not None: a["t5"].append(aT5)
        if aPre is not None: a["pre"].append(aPre)
        if aPost is not None: a["post"].append(aPost)

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")

    print("\n" + "=" * 84)
    print("  ASK DEL GANADOR pre-cierre vs post-cierre, por semana  ·  ¿converge antes o salta al resolver?")
    print("=" * 84)
    print(f"  {'semana':>8}{'n':>6}{'aT15':>8}{'aT5':>8}{'aPre≤300':>10}{'aPost≥305':>11}{'EV_T15(pp)':>12}")
    allt15 = []
    for wk in sorted(agg):
        a = agg[wk]
        print(f"  {wk:>8}{a['n']:>6}{mean(a['t15']):>8.3f}{mean(a['t5']):>8.3f}"
              f"{mean(a['pre']):>10.3f}{mean(a['post']):>11.3f}{mean(a['ev']):>+11.2f}")
        allt15 += a['ev']
    print(f"\n  EV_T15 global: {mean(allt15):+.2f}pp  (comprar ganador a T-15, aguantar a resolución; cota)")
    print("\nLECTURA: si aPre≈aPost≈0,95 → converge ANTES del cierre → NO hay desfase (mis scripts cogían el")
    print("post-cierre). Si aT15/aPre ≈0,6 y aPost≈0,95 → el mercado va REZAGADO hasta resolver → desfase REAL,")
    print("y si EV_T15 es + y ESTABLE en todas las semanas (no solo recientes) → sobrevive el gate de estabilidad.")
    print("OJO: EV_T15 es cota (asume conocer al ganador; con el spot lo aciertas ~89-99% en movimientos ≥$50).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
