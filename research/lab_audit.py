"""
lab_audit.py — AUDITORÍA de integridad del lab (5m), SEMANA a SEMANA, para localizar la regresión de datos
que hizo incoherente el análisis de fin-de-ventana. Por semana chequea:
  spot_maxgap : hueco máximo (s) entre lecturas de spot   → sano ~5-15s; grande = feed con cortes
  win_finalask: ask del GANADOR en el último snapshot      → sano ≈0,90; si CAE = el libro no converge
  los_finalask: ask del PERDEDOR en el último snapshot      → sano ≈0,10
  %higher=win : % ventanas donde el lado con MAYOR ask final == el ganador → sano ~95%; si CAE = mapeo/
                resolución rotos (o el mercado deja de tener sentido)
  %dup_ask    : % de asks duplicados consecutivos (ganador) → si SUBE = snapshots congelados/rancios
  %last15s    : % ventanas con algún snapshot en los últimos 15s → cobertura cerca del cierre
Si una semana concreta rompe estas métricas, ahí empezó la corrupción.

    cd ~/polymarket-btc-up-down/research && python3 lab_audit.py
"""
import csv, os, sys, glob, json, time, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
WLEN = 300
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "audit/1.0"})
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


def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_spot_weeks():
    """semana -> lista ts (ordenada)"""
    wk = {}
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: ts = int(row[0])
                except Exception: continue
                wk.setdefault(week(ts), []).append(ts)
    for k in wk: wk[k].sort()
    return wk


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


def main():
    spotwk = load_spot_weeks()
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

    # agregados por semana
    agg = {}   # wk -> dict de listas
    print(f"auditando {len(W)} ventanas 5m…")
    done = 0
    for slug, w in W.items():
        ws = w["ws"]; wk = week(ws)
        up = sorted(w["asks"]["Up"]); dn = sorted(w["asks"]["Down"])
        if not up or not dn: continue
        win = resolve(w["cid"])
        done += 1
        if done % 2000 == 0: print(f"   … {done}/{len(W)}")
        if win not in ("Up", "Down"): continue
        wser = up if win == "Up" else dn
        lser = dn if win == "Up" else up
        win_final = wser[-1][1]; los_final = lser[-1][1]
        higher_is_win = 1 if win_final >= los_final else 0
        last_ts = max(up[-1][0], dn[-1][0])
        last15 = 1 if last_ts >= ws + WLEN - 15 else 0
        # rancio: duplicados consecutivos en la serie del ganador
        dup = sum(1 for i in range(1, len(wser)) if wser[i][1] == wser[i - 1][1])
        dupfrac = dup / (len(wser) - 1) if len(wser) > 1 else 0
        a = agg.setdefault(wk, {"n": 0, "wf": [], "lf": [], "hi": 0, "l15": 0, "dup": []})
        a["n"] += 1; a["wf"].append(win_final); a["lf"].append(los_final)
        a["hi"] += higher_is_win; a["l15"] += last15; a["dup"].append(dupfrac)

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")

    def maxgap(tss):
        if len(tss) < 2: return 0
        return max(tss[i] - tss[i - 1] for i in range(1, len(tss)))

    print("\n" + "=" * 92)
    print("  AUDITORÍA LAB 5m por semana  ·  sano: win_finalask≈0,90 · %higher=win~95% · dup bajo · spotgap bajo")
    print("=" * 92)
    print(f"  {'semana':>8}{'n':>6}{'spot_maxgap':>12}{'win_finalask':>13}{'los_finalask':>13}{'%higher=win':>12}{'%dup_ask':>9}{'%last15s':>9}")
    for wk in sorted(agg):
        a = agg[wk]; n = a["n"]
        if not n: continue
        sg = maxgap(spotwk.get(wk, []))
        print(f"  {wk:>8}{n:>6}{sg:>11}s{mean(a['wf']):>13.3f}{mean(a['lf']):>13.3f}"
              f"{100*a['hi']/n:>11.0f}%{100*mean(a['dup']):>8.0f}%{100*a['l15']/n:>8.0f}%")
    print("\nLECTURA: la semana en que 'win_finalask' se desploma (de ~0,9 a ~0,5) y/o '%higher=win' cae de ~95%")
    print("marca el inicio de la regresión. Si 'spot_maxgap' salta o '%dup_ask' sube ahí → feed/colector; si solo")
    print("cae '%higher=win' con libro sano → mapeo Up/Down o resolución. Si TODO está sano → el bug era mío.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
