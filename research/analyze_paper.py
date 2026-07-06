"""Analiza maker_paper_log.csv: fill rate, WIN de fills, EV, selección adversa."""
import csv, os, sys, math, statistics
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
P=os.path.join(os.path.dirname(__file__),"maker_paper_log.csv")
if len(sys.argv)>1: P=sys.argv[1]
rows=list(csv.DictReader(open(P,encoding="utf-8")))
n=len(rows)
from collections import Counter
st=Counter(r["status"] for r in rows)
print(f"ventanas registradas: {n}")
print(f"  estados: {dict(st)}")

fills=[r for r in rows if r["status"]=="filled" and r["won"] in ("0","1")]
nf=len(fills)
print(f"\n  posteadas (no skip): {sum(1 for r in rows if r['status'] in ('filled','no_fill','cancelled'))}")
print(f"  fills resueltos: {nf}")
if nf>=20:
    wins=sum(int(r["won"]) for r in fills)
    bids=[float(r["bid"]) for r in fills if r["bid"]]
    wr=wins/nf; ap=statistics.mean(bids)
    se=math.sqrt(wr*(1-wr)/nf)
    ev=sum((1/float(r["bid"])-1) if r["won"]=="1" else -1 for r in fills)/nf
    print(f"\n  WIN de fills: {wr:.1%} (IC95%: {wr-1.96*se:.1%}-{wr+1.96*se:.1%})")
    print(f"  bid medio: {ap:.1%}  (break-even)")
    print(f"  EV/fill: {ev:+.1%}")
    print(f"\n  VEREDICTO:")
    if wr-1.96*se>ap: print("    → CAPTURAMOS el edge de ejecución (win > bid significativo). REAL.")
    elif wr>ap: print("    → positivo pero no significativo (más fills)")
    else: print("    → selección adversa: nos llenan en los perdedores. No lo capturamos.")
else:
    print("  aún pocos fills — deja correr el bot más tiempo.")
