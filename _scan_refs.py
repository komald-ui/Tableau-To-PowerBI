import os, glob, re

base = r"C:\Tableau to Power BI\PowerBI\Salesforce\Salesforce.SemanticModel\definition\tables"

measures = {}
calc_cols = {}
AGG = re.compile(r"\b(SUM|SUMX|COUNT|COUNTA|COUNTX|COUNTROWS|DISTINCTCOUNT|AVERAGE|AVERAGEX|CALCULATE|DIVIDE|RANKX|SELECTEDVALUE)\s*\(", re.I)

for tf in glob.glob(os.path.join(base, "*.tmdl")):
    txt = open(tf, encoding="utf-8").read()
    for m in re.finditer(r"\tmeasure '([^']+)' = (.+)", txt):
        measures[m.group(1)] = m.group(2)
    for m in re.finditer(r"\tcolumn '([^']+)' = (.+)", txt):
        calc_cols[m.group(1)] = m.group(2)

print(f"Total measures: {len(measures)}, calc columns: {len(calc_cols)}")
print(f"\nCalc columns with aggregation (potential misclassifications):")
for name, expr in sorted(calc_cols.items()):
    if AGG.search(expr):
        # Pure CALCULATE(SELECTEDVALUE(...)) = cross-table scalar lookup = valid calc column
        stripped = expr.strip()
        is_pure_lookup = bool(re.match(r"^CALCULATE\(SELECTEDVALUE\(", stripped, re.I))
        # Used inside IF/CONVERT wrapping CALCULATE(SELECTEDVALUE(...)) = still row-level
        is_wrapped_lookup = bool(re.search(r"CALCULATE\(SELECTEDVALUE\(", stripped, re.I)) and not re.search(r"\bSUM\b|\bSUMX\b|\bCOUNT\b|\bCOUNTA\b|\bCOUNTX\b|\bCOUNTROWS\b|\bDISTINCTCOUNT\b|\bAVERAGE\b|\bAVERAGEX\b|\bDIVIDE\b|\bRANKX\b", stripped, re.I)
        # Has real aggregation (SUM, COUNT, etc.) - should be measure
        has_real_agg = bool(re.search(r"\b(SUM|SUMX|COUNT|COUNTA|COUNTX|COUNTROWS|DISTINCTCOUNT|AVERAGE|AVERAGEX|DIVIDE|RANKX)\s*\(", stripped, re.I))
        # References measures (e.g., [Nb of Won Opportunities])
        refs_measures = []
        for mname in measures:
            if mname in stripped:
                refs_measures.append(mname)
        
        tag = "OK-lookup" if (is_pure_lookup or is_wrapped_lookup) and not has_real_agg else "SHOULD-BE-MEASURE" if has_real_agg or refs_measures else "CHECK"
        if refs_measures:
            tag += " (refs measures: " + ", ".join(refs_measures[:3]) + ")"
        print(f"  [{tag}] {name} = {expr[:100]}")
