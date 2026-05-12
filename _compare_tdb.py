"""Full comparison: TDB Maintenance — Tableau vs Power BI."""
import json, os, glob, re

# === TABLEAU SOURCE ===
with open('tableau_export/worksheets.json', 'r', encoding='utf-8') as f:
    ws = json.load(f)
with open('tableau_export/dashboards.json', 'r', encoding='utf-8') as f:
    dbs = json.load(f)
with open('tableau_export/datasources.json', 'r', encoding='utf-8') as f:
    ds = json.load(f)
with open('tableau_export/calculations.json', 'r', encoding='utf-8') as f:
    calcs = json.load(f)
with open('tableau_export/filters.json', 'r', encoding='utf-8') as f:
    filters = json.load(f)

print("=" * 80)
print("  TABLEAU SOURCE: TDB Maintenance (mar 2026)")
print("=" * 80)

print(f"\n{'WORKSHEETS':40s} ({len(ws)} total)")
print("-" * 80)
tab_ws_names = set()
for w in ws:
    name = w.get('name', '?')
    tab_ws_names.add(name)
    fields = w.get('fields', [])
    marks = w.get('mark_type', '?')
    fcount = len(w.get('filters', []))
    print(f"  {name:42s} mark={marks:15s} fields={len(fields):2d} filters={fcount}")

print(f"\n{'DASHBOARDS':40s} ({len(dbs)} total)")
print("-" * 80)
tab_db_names = set()
for d in dbs:
    name = d.get('name', '?')
    tab_db_names.add(name)
    objs = d.get('objects', [])
    ws_refs = [o.get('name', '') for o in objs if o.get('type') == 'worksheet']
    texts = sum(1 for o in objs if o.get('type') == 'text')
    imgs = sum(1 for o in objs if o.get('type') == 'image')
    filters_c = sum(1 for o in objs if o.get('type') == 'filter_control')
    size = d.get('size', {})
    print(f"  {name:42s} ws={len(ws_refs)} text={texts} img={imgs} filter_ctrl={filters_c} size={size.get('width','?')}x{size.get('height','?')}")
    for wr in ws_refs:
        print(f"    -> {wr}")

print(f"\n{'DATASOURCES':40s} ({len(ds)} total)")
print("-" * 80)
for d in ds:
    caption = d.get('caption', d.get('name', '?'))
    tables = d.get('tables', [])
    ds_cols = d.get('columns', [])
    conn = d.get('connection', {})
    print(f"  {caption:42s} tables={len(tables)} ds_cols={len(ds_cols)} conn={conn.get('class','?')}")
    for t in tables:
        cols = t.get('columns', [])
        print(f"    Table: {t.get('name','?'):30s} columns={len(cols)}")

print(f"\n{'CALCULATIONS':40s} ({len(calcs)} total)")
print("-" * 80)
for c in calcs:
    name = c.get('caption', c.get('name', '?'))
    formula = c.get('formula', '')[:60]
    role = c.get('role', '?')
    dt = c.get('datatype', '?')
    print(f"  {name:42s} role={role:10s} type={dt:8s} formula={formula}")

print(f"\n{'FILTERS':40s} ({len(filters)} total)")
print("-" * 80)
# Deduplicate by field for summary
filter_fields = {}
for fl in filters:
    field = fl.get('field', '')
    # Clean field name
    m = re.findall(r'\[([^\]]+)\]\.\[([^\]]+)\]', field)
    if m:
        clean = re.sub(r'^(none|sum|avg|count|yr|mn|attr):', '', m[0][1])
        clean = re.sub(r':(nk|qk|ok)$', '', clean)
    else:
        clean = field
    mn = fl.get('min', '')
    mx = fl.get('max', '')
    vals = fl.get('values', [])
    key = clean
    if key not in filter_fields:
        filter_fields[key] = {'count': 0, 'types': set(), 'has_values': False}
    filter_fields[key]['count'] += 1
    if fl.get('filter_mode'):
        filter_fields[key]['types'].add(fl.get('filter_mode'))
    if mn or mx or vals:
        filter_fields[key]['has_values'] = True

for field, info in sorted(filter_fields.items()):
    types_str = '/'.join(info['types']) if info['types'] else '?'
    has_vals = 'Y' if info['has_values'] else 'N'
    print(f"  {field:42s} instances={info['count']} mode={types_str:15s} has_data={has_vals}")

# Collect all Tableau fields used across worksheets
all_tab_fields = set()
for w in ws:
    for f in w.get('fields', []):
        fname = f.get('name', f.get('field', ''))
        # Clean
        if '.' in fname and '[' in fname:
            m = re.findall(r'\[([^\]]+)\]\.\[([^\]]+)\]', fname)
            if m:
                fname = re.sub(r'^(none|sum|avg|count|yr|mn|attr):', '', m[0][1])
                fname = re.sub(r':(nk|qk|ok)$', '', fname)
        all_tab_fields.add(fname)

# === POWER BI OUTPUT ===
pbi_base = r'C:\Tableau to Power BI\PowerBI\TDB Maintenance (mar 2026)'
sem_base = os.path.join(pbi_base, 'TDB Maintenance (mar 2026).SemanticModel', 'definition')
rpt_base = os.path.join(pbi_base, 'TDB Maintenance (mar 2026).Report', 'definition')

print("\n\n" + "=" * 80)
print("  POWER BI OUTPUT: TDB Maintenance (mar 2026)")
print("=" * 80)

# Read TMDL tables
pbi_tables = {}
pbi_measures = {}
pbi_columns = {}
pbi_rels = []
for tmdl_file in glob.glob(os.path.join(sem_base, 'tables', '*.tmdl')):
    with open(tmdl_file, 'r', encoding='utf-8') as f:
        content = f.read()
    tname_m = re.search(r"^table\s+'?(.+?)'?\s*$", content, re.MULTILINE)
    tname = tname_m.group(1) if tname_m else os.path.basename(tmdl_file).replace('.tmdl','')
    # Count columns and measures
    cols = re.findall(r'^\tcolumn\s+(.+)$', content, re.MULTILINE)
    measures = re.findall(r'^\tmeasure\s+(.+?)\s*=', content, re.MULTILINE)
    pbi_tables[tname] = {'columns': [c.strip("' ") for c in cols], 'measures': [m.strip("' ") for m in measures]}
    for c in cols:
        pbi_columns[c.strip("' ")] = tname
    for m in measures:
        pbi_measures[m.strip("' ")] = tname

# Read relationships
model_tmdl = os.path.join(sem_base, 'model.tmdl')
if os.path.exists(model_tmdl):
    with open(model_tmdl, 'r', encoding='utf-8') as f:
        model_content = f.read()
    pbi_rels = re.findall(r'relationship\s+(.+)', model_content)

# Read report pages
pages_dir = os.path.join(rpt_base, 'pages')
pbi_pages = []
if os.path.exists(pages_dir):
    for page_dir in sorted(os.listdir(pages_dir)):
        page_json_path = os.path.join(pages_dir, page_dir, 'page.json')
        if os.path.exists(page_json_path):
            with open(page_json_path, 'r', encoding='utf-8') as f:
                pg = json.load(f)
            # Count visuals
            visuals_dir = os.path.join(pages_dir, page_dir, 'visuals')
            vis_count = 0
            vis_types = []
            if os.path.exists(visuals_dir):
                for vdir in os.listdir(visuals_dir):
                    vpath = os.path.join(visuals_dir, vdir, 'visual.json')
                    if os.path.exists(vpath):
                        with open(vpath, 'r', encoding='utf-8') as f:
                            vj = json.load(f)
                        vtype = vj.get('visual', {}).get('visualType', '?')
                        vis_types.append(vtype)
                        vis_count += 1
            pbi_pages.append({
                'name': pg.get('displayName', '?'),
                'width': pg.get('width', '?'),
                'height': pg.get('height', '?'),
                'visuals': vis_count,
                'types': vis_types
            })

# Read report-level filters
report_json_path = os.path.join(rpt_base, 'report.json')
pbi_report_filters = []
if os.path.exists(report_json_path):
    with open(report_json_path, 'r', encoding='utf-8') as f:
        rpt_json = json.load(f)
    pbi_report_filters = rpt_json.get('filterConfig', {}).get('filters', [])

print(f"\n{'SEMANTIC MODEL TABLES':40s} ({len(pbi_tables)} total)")
print("-" * 80)
for tname, info in sorted(pbi_tables.items()):
    print(f"  {tname:42s} columns={len(info['columns']):2d} measures={len(info['measures'])}")
    for m in info['measures']:
        print(f"    [M] {m}")

print(f"\n{'REPORT PAGES':40s} ({len(pbi_pages)} total)")
print("-" * 80)
for pg in pbi_pages:
    types_str = ', '.join(sorted(set(pg['types'])))
    print(f"  {pg['name']:42s} visuals={pg['visuals']:2d} size={pg['width']}x{pg['height']}")
    print(f"    types: {types_str}")

print(f"\n{'RELATIONSHIPS':40s} ({len(pbi_rels)} total)")
print("-" * 80)
for r in pbi_rels:
    print(f"  {r}")

print(f"\n{'REPORT FILTERS':40s} ({len(pbi_report_filters)} total)")
print("-" * 80)
for flt in pbi_report_filters:
    col = flt.get('field', {}).get('Column', {})
    entity = col.get('Expression', {}).get('SourceRef', {}).get('Entity', '?')
    prop = col.get('Property', '?')
    ftype = flt.get('type', '?')
    print(f"  {entity}.{prop:30s} type={ftype}")

# === COMPARISON ===
print("\n\n" + "=" * 80)
print("  COMPARISON SUMMARY")
print("=" * 80)

# Dashboard → Page mapping
print(f"\n{'DASHBOARD → PAGE MAPPING':40s}")
print("-" * 80)
print(f"  Tableau dashboards: {len(dbs)}")
print(f"  PBI pages:          {len(pbi_pages)}")
for d in dbs:
    dname = d.get('name', '?')
    # Find matching PBI page
    matching = [p for p in pbi_pages if p['name'] == dname]
    status = 'OK' if matching else 'MISSING'
    tab_ws = [o.get('name','') for o in d.get('objects',[]) if o.get('type') == 'worksheet']
    if matching:
        print(f"  [OK]      {dname:40s} tab_ws={len(tab_ws)} pbi_vis={matching[0]['visuals']}")
    else:
        print(f"  [MISSING] {dname:40s} tab_ws={len(tab_ws)}")

# Worksheets with no dashboard
orphan_ws = tab_ws_names.copy()
for d in dbs:
    for o in d.get('objects', []):
        if o.get('type') == 'worksheet':
            orphan_ws.discard(o.get('name', ''))
if orphan_ws:
    print(f"\n  Worksheets NOT on any dashboard ({len(orphan_ws)}):")
    for w in sorted(orphan_ws):
        print(f"    - {w}")

# Field coverage
print(f"\n{'FIELD COVERAGE':40s}")
print("-" * 80)
all_pbi_fields = set(pbi_columns.keys()) | set(pbi_measures.keys())
# Clean Tableau fields
clean_tab = set()
for f in all_tab_fields:
    f = f.replace('[', '').replace(']', '')
    clean_tab.add(f)

in_both = clean_tab & all_pbi_fields
tab_only = clean_tab - all_pbi_fields
pbi_only = all_pbi_fields - clean_tab

print(f"  Tableau fields used in visuals:  {len(clean_tab)}")
print(f"  PBI model fields (cols+measures): {len(all_pbi_fields)}")
print(f"  Matched:                          {len(in_both)}")

if tab_only:
    print(f"\n  Fields in Tableau but NOT in PBI ({len(tab_only)}):")
    for f in sorted(tab_only):
        if f and not f.startswith(':') and not f.startswith('Measure'):
            print(f"    - {f}")

# Calculation coverage
print(f"\n{'CALCULATION COVERAGE':40s}")
print("-" * 80)
tab_calc_names = set()
for c in calcs:
    tab_calc_names.add(c.get('caption', c.get('name', '')))
calc_in_pbi = tab_calc_names & all_pbi_fields
calc_missing = tab_calc_names - all_pbi_fields
print(f"  Tableau calculations: {len(calcs)}")
print(f"  Mapped to PBI:        {len(calc_in_pbi)}")
if calc_missing:
    print(f"  NOT in PBI ({len(calc_missing)}):")
    for c in sorted(calc_missing):
        print(f"    - {c}")

print("\n" + "=" * 80)
print("  END OF COMPARISON")
print("=" * 80)
