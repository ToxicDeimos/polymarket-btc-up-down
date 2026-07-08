"""
Prototipo de LAG en dota2 in-play: ¿el precio de Polymarket va por DETRÁS del gold-lead del feed?

Empareja un partido pro de OpenDota (radiant_lead, gratis sin key) con su market de Polymarket
(precio del equipo Radiant), muestrea cada POLL s, y al parar calcula la correlación cruzada a
distintos lags:  corr(Δgold_lead(t), Δprecio(t+lag)).
  - pico en lag > 0  → Polymarket REPRECIA DESPUÉS del feed = LAG explotable (tomas el lado barato).
  - pico en lag = 0  → reprecia a la vez = eficiente (como murió en BTC).
  - pico en lag < 0  → el precio ADELANTA al feed = nuestro feed es lento, inútil.

Mismo método que corr(Δspot,Δask) del test de BTC, ahora con gold-lead como "subyacente".

    python dota_lag.py                    # auto-empareja un partido pro live en ambos
    python dota_lag.py --match ID --cid C # forzar un partido concreto
    python dota_lag.py --samples N        # recoger N muestras y analizar (test rápido)
    python dota_lag.py --analyze          # solo recalcular corr del log existente
Autónomo (stdlib).
"""
import urllib.request, json, time, csv, os, sys

POLL = 5
LOG = os.path.join(os.path.dirname(__file__), "dota_lag_log.csv")
HEADER = ["t","game_time","radiant_team","radiant_lead","r_score","d_score","py_bid","py_ask","py_mid","py_last"]

def get(url, tries=2):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.5)

def norm(s): return "".join(c for c in (s or "").lower() if c.isalnum())

def live_pro():
    d=get("https://api.opendota.com/api/live")
    return [m for m in d if m.get("team_name_radiant") and m.get("team_name_dire")] if isinstance(d,list) else []

def poly_dota_markets():
    feed=[]
    for off in (0,500,1000,1500):
        tr=get(f"https://data-api.polymarket.com/trades?limit=500&offset={off}")
        if isinstance(tr,list): feed+=tr
    seen=set(); out=[]
    for t in feed:
        slug=t.get("slug","") or ""; cid=t.get("conditionId")
        if not slug.startswith("dota2") or cid in seen: continue
        seen.add(cid)
        d=get(f"https://clob.polymarket.com/markets/{cid}")
        if not isinstance(d,dict): continue
        toks={tok.get("outcome"):tok.get("token_id") for tok in d.get("tokens",[])}
        out.append({"cid":cid,"q":d.get("question",""),"toks":toks})
    return out

def find_match():
    pros=live_pro(); mks=poly_dota_markets()
    for m in sorted(pros, key=lambda x:-(x.get("spectators") or 0)):
        rn=norm(m["team_name_radiant"]); dn=norm(m["team_name_dire"])
        for mk in mks:
            outs={norm(o):o for o in mk["toks"]}
            rmatch=next((o for n,o in outs.items() if n and (n in rn or rn in n)), None)
            dmatch=next((o for n,o in outs.items() if n and (n in dn or dn in n)), None)
            if rmatch and dmatch and rmatch!=dmatch:
                return {"match_id":m["match_id"],"cid":mk["cid"],"q":mk["q"],
                        "radiant_team":rmatch,"radiant_tok":mk["toks"][rmatch]}
    return None

def match_state(match_id):
    for m in live_pro():
        if m.get("match_id")==match_id: return m
    return None

def book_px(tok):
    b=get(f"https://clob.polymarket.com/book?token_id={tok}")
    if not isinstance(b,dict): return None,None,None
    asks=[float(a["price"]) for a in b.get("asks",[])]; bids=[float(x["price"]) for x in b.get("bids",[])]
    ba=min(asks) if asks else None; bb=max(bids) if bids else None
    last=b.get("last_trade_price")
    return bb, ba, (float(last) if last else None)

def log_row(row):
    new=not os.path.exists(LOG)
    with open(LOG,"a",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        if new: w.writerow(HEADER)
        w.writerow(row)

def corr(a,b):
    n=len(a)
    if n<3: return None
    ma=sum(a)/n; mb=sum(b)/n
    num=sum((a[i]-ma)*(b[i]-mb) for i in range(n))
    da=sum((x-ma)**2 for x in a)**.5; db=sum((x-mb)**2 for x in b)**.5
    return num/(da*db) if da>0 and db>0 else None

def analyze():
    if not os.path.exists(LOG): print("sin log"); return
    rows=list(csv.DictReader(open(LOG,encoding="utf-8")))
    def px(r):
        for k in ("py_mid","py_last","py_ask"):
            if r.get(k) not in ("",None,"None"): return float(r[k])
        return None
    def lead(r):
        v=r.get("radiant_lead")
        return float(v) if v not in ("",None,"None") else None
    seq=[(lead(r),px(r)) for r in rows]
    seq=[(l,p) for l,p in seq if l is not None and p is not None]
    if len(seq)<8: print(f"pocas muestras ({len(seq)}) — deja correr más."); return
    dlead=[seq[i][0]-seq[i-1][0] for i in range(1,len(seq))]
    dpx  =[seq[i][1]-seq[i-1][1] for i in range(1,len(seq))]
    print(f"\nmuestras: {len(seq)}  (~{POLL}s cada una, ~{len(seq)*POLL//60}min de partido)")
    print(f"contemporánea corr(Δlead,Δprecio): {corr(dlead,dpx)}")
    print("\nlag(s)  corr(Δlead → Δprecio)")
    best=None
    for k in range(-3,8):
        if k>=0: a,b=dlead[:len(dlead)-k], dpx[k:]
        else:    a,b=dlead[-k:], dpx[:len(dpx)+k]
        c=corr(a,b)
        if c is not None:
            bar="#"*int(abs(c)*20)
            print(f"  {k*POLL:>+4}s   {c:+.3f}  {bar}")
            if best is None or abs(c)>abs(best[1]): best=(k*POLL,c)
    if best:
        print(f"\npico en lag {best[0]:+}s (corr {best[1]:+.3f})")
        if best[0]>0 and best[1]>0.25: print("  → Polymarket reprecia DESPUÉS del feed = LAG EXPLOTABLE ✓")
        elif best[0]==0 and best[1]>0.25: print("  → reprecia a la vez = eficiente (como BTC)")
        elif best[0]<0 and best[1]>0.25:  print("  → el precio ADELANTA al feed = OpenDota va lento, inútil")
        else: print("  → señal débil/ruido — más muestras o el gold-lead no mueve el precio")

def run(mt, limit=None):
    print(f"Partido: {mt['q']}")
    print(f"  radiant={mt['radiant_team']}  match_id={mt['match_id']}  cid={mt['cid'][:14]}...")
    print(f"  registrando cada {POLL}s. Ctrl-C para parar y analizar.\n")
    n=0
    try:
        while True:
            m=match_state(mt["match_id"])
            if m is None:
                print("  (partido ya no está live en OpenDota; ¿terminó? paro.)"); break
            bb,ba,last=book_px(mt["radiant_tok"])
            mid=(bb+ba)/2 if (bb is not None and ba is not None) else None
            log_row([int(time.time()),m.get("game_time"),mt["radiant_team"],m.get("radiant_lead"),
                     m.get("radiant_score"),m.get("dire_score"),bb,ba,mid,last])
            print(f"  t={m.get('game_time')}s lead={m.get('radiant_lead')} px={mid} (bid {bb}/ask {ba}) score {m.get('radiant_score')}-{m.get('dire_score')}")
            n+=1
            if limit and n>=limit: break
            time.sleep(POLL)
    except KeyboardInterrupt:
        print("\nparado.")
    analyze()

def main():
    a=sys.argv[1:]
    if "--analyze" in a: analyze(); return
    limit=None
    if "--samples" in a: limit=int(a[a.index("--samples")+1])
    if "--match" in a and "--cid" in a:
        mid=int(a[a.index("--match")+1]); cid=a[a.index("--cid")+1]
        d=get(f"https://clob.polymarket.com/markets/{cid}")
        toks={t.get("outcome"):t.get("token_id") for t in d.get("tokens",[])} if isinstance(d,dict) else {}
        st=match_state(mid)
        rteam=None
        if st:
            rn=norm(st["team_name_radiant"])
            rteam=next((o for o in toks if norm(o) in rn or rn in norm(o)), None)
        if not rteam: print("no pude casar el equipo radiant con los outcomes; revisa match/cid"); return
        mt={"match_id":mid,"cid":cid,"q":d.get("question",""),"radiant_team":rteam,"radiant_tok":toks[rteam]}
    else:
        print("buscando un partido pro live en ambos sitios...")
        mt=find_match()
        if not mt:
            print("no hay ninguno emparejable ahora mismo. Partidos pro live en OpenDota:")
            for m in live_pro()[:8]:
                print(f"   {m.get('match_id')}  {m.get('team_name_radiant')} vs {m.get('team_name_dire')}  (spec {m.get('spectators')})")
            return
    run(mt, limit)

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
