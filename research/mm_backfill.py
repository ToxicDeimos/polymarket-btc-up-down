"""mm_backfill.py — rellena 'won' de mm_ws_log.csv y mm_paper_log.csv. Los bots dejaban 'won' vacío porque su
resolución se rendía en 300s (estos mercados tardan más en liquidar). Aquí se resuelve a posteriori: ws→cid
(desde books_*.csv) → winner por CLOB (clob/markets/{cid}). Cachea en clob_reso_mmlogs.csv."""
import csv, os, sys, json, glob, time, urllib.request

DIR = os.path.dirname(__file__)
CACHE = os.path.join(DIR, "lab", "clob_reso_mmlogs.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bf/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.4)


def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if isinstance(d, dict):
        for t in d.get("tokens", []):
            if t.get("winner") is True: return t.get("outcome")
    return None


def main():
    # ws -> cid desde los books
    ws2cid = {}
    for p in sorted(glob.glob(os.path.join(DIR, "lab", "books_*.csv"))):
        for row in csv.reader(open(p, encoding="utf-8")):
            if len(row) > 2 and row[1].startswith("btc-updown-5m-"):
                w = row[1].split("-")[-1]
                if w not in ws2cid: ws2cid[w] = row[2]
    print(f"ws→cid desde books: {len(ws2cid)} ventanas")

    # cids ya resueltos (caché propia + las del proyecto)
    reso = {}
    for fn in ("clob_reso_mmlogs.csv", "clob_reso_mw.csv", "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, "lab", fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: reso[r["cid"]] = r["winner"]

    def resolve(ws):
        cid = ws2cid.get(ws)
        if not cid: return None
        if cid in reso: return reso[cid]
        w = winner_clob(cid); time.sleep(0.12)
        if w: reso[cid] = w; nf = not os.path.exists(CACHE)
        with open(CACHE, "a", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            if nf: wr.writerow(["cid", "winner"])
            wr.writerow([cid, w or ""])
        return w

    for fn in ("mm_ws_log.csv", "mm_paper_log.csv"):
        path = os.path.join(DIR, fn)
        if not os.path.exists(path): print(f"{fn}: no existe"); continue
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        hdr = list(rows[0].keys()) if rows else []
        pend = [r for r in rows if r.get("won") not in ("0", "1")]
        print(f"{fn}: {len(rows)} filas, {len(pend)} sin resolver…")
        done = 0
        for r in pend:
            w = resolve(r["ws"])
            if w in ("Up", "Down"):
                r["won"] = "1" if w == r["fav"] else "0"; done += 1
            if done and done % 100 == 0: print(f"   … {done}")
        with open(path, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=hdr); wr.writeheader(); wr.writerows(rows)
        print(f"{fn}: resueltas {done} · quedan {len(pend)-done}")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
