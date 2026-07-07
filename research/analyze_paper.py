"""Analiza maker_paper_log.csv: fill rate, WIN de fills, EV, selección adversa (total y por mercado)."""
import csv, os, sys, math, statistics
from collections import Counter
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
P=os.path.join(os.path.dirname(__file__),"maker_paper_log.csv")
if len(sys.argv)>1: P=sys.argv[1]
rows=list(csv.DictReader(open(P,encoding="utf-8")))
n=len(rows)
st=Counter(r["status"] for r in rows)
print(f"ventanas registradas: {n}")
print(f"  estados: {dict(st)}")

def mkt(r): return "15m" if "-15m-" in r["slug"] else "5m"
posted=[r for r in rows if r["status"] in ("filled","no_fill","cancelled")]
filled_all=[r for r in rows if r["status"]=="filled"]
fills=[r for r in rows if r["status"]=="filled" and r["won"] in ("0","1")]  # resueltos
print(f"\n  posteadas (no skip): {len(posted)}")
if posted:
    print(f"  fill rate: {len(filled_all)/len(posted):.0%}  ({len(filled_all)}/{len(posted)})")
print(f"  fills resueltos: {len(fills)}")

def report(fs, label):
    nf=len(fs)
    if nf==0:
        print(f"  [{label}]  sin fills"); return None
    wins=sum(int(r["won"]) for r in fs)
    bids=[float(r["bid"]) for r in fs if r["bid"]]
    wr=wins/nf; ap=statistics.mean(bids); se=math.sqrt(wr*(1-wr)/nf)
    ev=sum((1/float(r["bid"])-1) if r["won"]=="1" else -1 for r in fs)/nf
    print(f"  [{label:>5}]  n={nf:>3}  WIN {wr:.1%} (IC95% {max(0,wr-1.96*se):.1%}-{min(1,wr+1.96*se):.1%})"
          f"  bid medio {ap:.1%}  EV/fill {ev:+.1%}")
    return wr,se,ap

if len(fills)>=20:
    print()
    r=report(fills,"TODOS")
    for m in ("5m","15m"): report([f for f in fills if mkt(f)==m], m)
    wr,se,ap=r
    print("\n  VEREDICTO:")
    if   wr-1.96*se>ap: print("    → CAPTURAMOS el edge de ejecución (win > bid, significativo). REAL.")
    elif wr>ap:         print("    → positivo pero NO significativo — deja correr más (más fills).")
    else:               print("    → selección adversa: nos llenan en los perdedores. No lo capturamos.")
else:
    print("  aún pocos fills (<20) — deja correr el bot más tiempo.")
