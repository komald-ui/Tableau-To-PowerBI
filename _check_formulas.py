import sys, os, zipfile, tempfile, re
sys.path.insert(0, "tableau_export")
from extract_tableau_data import TableauExtractor

twbx = r"C:\Tableau to Power BI\Tableau\Salesforce.twbx"
with zipfile.ZipFile(twbx) as z:
    twb_name = [n for n in z.namelist() if n.endswith(".twb")][0]
    with tempfile.TemporaryDirectory() as td:
        z.extract(twb_name, td)
        ext = TableauExtractor(os.path.join(td, twb_name))
        data = ext.extract_all()

calcs = data.get("calculations", [])
targets = ["Client/Prospect by Account", "_Per Customer", "Probability", "_Pipeline Generated"]
for c in calcs:
    cap = c.get("caption", c.get("name", ""))
    for t in targets:
        if t.lower() in cap.lower():
            role = c.get("role", "?")
            typ = c.get("type", "?")
            formula = c.get("formula", "")[:200]
            print(f"{cap} [{role}/{typ}]")
            print(f"  {formula}")
            print()
            break
