import os, glob, re

base = r"C:\Tableau to Power BI\PowerBI"
AGG = re.compile(r"\b(SUM|SUMX|COUNT|COUNTA|COUNTX|COUNTROWS|DISTINCTCOUNT|AVERAGE|AVERAGEX|CALCULATE|DIVIDE|RANKX|SELECTEDVALUE)\s*\(", re.I)

for wb in os.listdir(base):
    sm = os.path.join(base, wb, wb + ".SemanticModel", "definition", "tables")
    if not os.path.isdir(sm):
        continue
    hits = []
    for tf in glob.glob(os.path.join(sm, "*.tmdl")):
        txt = open(tf, encoding="utf-8").read()
        for m in re.finditer(r"\tcolumn '([^']+)' = (.+)", txt):
            n, e = m.group(1), m.group(2)
            if AGG.search(e):
                hits.append((n, e[:120]))
    if hits:
        print(f"\n=== {wb} ({len(hits)}) ===")
        for n, e in hits[:30]:
            print(f"  {n} = {e}")
        if len(hits) > 30:
            print(f"  ...+{len(hits)-30}")
print("\nDone.")
