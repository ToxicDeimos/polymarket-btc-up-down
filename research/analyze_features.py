"""
Busca filtros extra que suban el win rate hacia el 71% de izzyaussie — SIN autoengaño.
Split TRAIN (antiguo) / TEST (reciente). Cada filtro se juzga por su rendimiento en
AMBOS. Un filtro que sube en TRAIN pero no en TEST = overfit, se descarta.
"""
import csv, os, sys, math, statistics
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__)
rows=[]
for r in csv.DictReader(open(os.path.join(D,"features.csv"),encoding="utf-8")):
    try:
        rows.append({"ws":int(r["ws"]),"wlen":int(r["wlen"]),"spike":abs(float(r["spike"])),
                     "cp":float(r["cheap_price"]),"div":float(r["divergence"]),
                     "vol":float(r["vol_pre"]),"rev":int(r["reverting"]),"won":int(r["won"])})
    except (ValueError,KeyError): pass
rows.sort(key=lambda x:x["ws"])
h=int(len(rows)*0.6); tr,te=rows[:h],rows[h:]
print(f"total {len(rows)} | TRAIN {len(tr)} | TEST {len(te)}\n")

def stats(sub):
    if not sub: return None
    n=len(sub); wr=sum(x["won"] for x in sub)/n; ap=statistics.mean(x["cp"] for x in sub)
    ev=sum((1/(x["cp"]+0.01)-1) if x["won"] else -1 for x in sub)/n
    return n,wr,ap,ev
def show(name,filt):
    a=stats([x for x in tr if filt(x)]); b=stats([x for x in te if filt(x)])
    if not a or not b or a[0]<12 or b[0]<12:
        print(f"  {name:28} (muestra insuficiente)"); return
    flag=""
    if a[1]>a[2]+0.03 and b[1]>b[2]+0.03: flag="  <== SOBREVIVE en ambos"
    print(f"  {name:28} TRAIN win {a[1]:.0%} (n={a[0]}, EV{a[3]:+.0%}) | TEST win {b[1]:.0%} (n={b[0]}, EV{b[3]:+.0%}){flag}")

allm=statistics.median([x['div'] for x in rows]); volm=statistics.median([x['vol'] for x in rows])
print("BASELINE:")
show("todos (spike<=10)", lambda x:True)
print("\nFILTROS DE UN FEATURE (TRAIN vs TEST):")
show("spike <= 4",          lambda x:x["spike"]<=4)
show("cheap_price < 0.35",  lambda x:x["cp"]<0.35)
show("cheap_price < 0.40",  lambda x:x["cp"]<0.40)
show(f"divergence > {allm:.2f}", lambda x:x["div"]>allm)
show(f"vol_pre < {volm:.1f}",    lambda x:x["vol"]<volm)
show("spike ya revirtiendo",lambda x:x["rev"]==1)
show("ventana 5m",          lambda x:x["wlen"]==300)
show("ventana 15m",         lambda x:x["wlen"]==900)
print("\nCOMBINACIONES (2 features):")
show("spike<=4 & div alta", lambda x:x["spike"]<=4 and x["div"]>allm)
show("cp<0.40 & revirtiendo",lambda x:x["cp"]<0.40 and x["rev"]==1)
show("cp<0.40 & vol baja",  lambda x:x["cp"]<0.40 and x["vol"]<volm)
print("\n(Solo son señal REAL los que ganan por encima de su break-even en TRAIN Y TEST.)")
