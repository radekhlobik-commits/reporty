#!/usr/bin/env python3
"""Doplnění plateb Qerko do reportu tržeb.

Použití:  python3 qerko.py <QERKO.pdf> <export_pokladny.TXT> <slozka_data>

Vstup: PDF sestava pokladny „T2/9/1 - Tržby a rozpětí po účtech“ filtrovaná na platbu n_qerko
(účty Qerko se do TXT exportu nezapisují, jen jejich případná storna/změny platby).
PDF neobsahuje datum účtu, proto se každý účet Qerko páruje s objednávkami (OBHLA/OBPOL)
na stolech v TXT exportu stejného měsíce: hledá se navazující skupina objednávek jednoho stolu
v jednom dni, jejíž součet = částka účtu; pořadí čísel účtů musí odpovídat pořadí v čase.
Nespárované účty dostanou den a hodinu podle sousedních spárovaných účtů (odhad).
Rozdělení jídlo/nápoje (středisko 41 / 51) se počítá z DPH účtu (12 % / 21 %).

Výstup: <slozka_data>/<klic>_qerko.json – zpracuj.py ho při generování reportu přičte
jako platbu „Qerko“ (obsluha „Qerko“, položky „Qerko – jídlo/nápoje“).
"""
import bisect, collections, csv, io, json, os, re, subprocess, sys, unicodedata


def norm(s):
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower().strip()


def pdf_bills(pdf):
    t = subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True, text=True).stdout
    od = re.search(r'Od:\s*\n?\s*(\d\d)\.(\d\d)\.(\d{4})', t)
    rest = None
    for l in t.splitlines():
        if l.strip() and not l.strip().startswith(('T2/', 'n_qerko')):
            rest = re.split(r'\s{2,}', l.strip())[0]; break
    out = []
    num = r'(-?\d{1,3}(?: \d{3})*,\d\d|-?\d+,\d\d)'
    for l in t.splitlines():
        m = re.match(r'^\s*(\d{7})\s+' + r'\s+'.join([num] * 4) + r'\s*$', l)
        if m:
            f = [float(x.replace(' ', '').replace(',', '.')) for x in m.groups()[1:]]
            out.append({'c': m.group(1), 't': f[0], 'dph': f[3]})
    celk = re.search(r'Celkem tržba\s+(-?[\d ]+,\d\d)', t)
    return {'od': od.groups() if od else None, 'restaurace': rest, 'ucty': out,
            'celkem': float(celk.group(1).replace(' ', '').replace(',', '.')) if celk else None}


def n(s):
    try: return float(s)
    except ValueError: return 0.0


def order_batches(path):
    raw = open(path, 'rb').read()
    try: txt = raw.decode('utf-8')
    except UnicodeDecodeError: txt = raw.decode('cp1250')
    rows = [r for r in csv.reader(io.StringIO(txt)) if len(r) >= 9]
    bat, cur, kopie = [], None, {}
    for r in rows:
        t = r[0]
        if t.endswith('HLA'):
            ts = r[1] + ' ' + r[2][:5]; cur = None
            if t == 'OBHLA': cur = [ts, int(n(r[5])), 0.0]; bat.append(cur)
            elif t == 'OSHLA': cur = [ts, int(n(r[5])), 0.0]; bat.append(cur)
            elif t == 'PRHLA':
                a, b = [ts, int(n(r[5])), 0.0], [ts, int(n(r[6])), 0.0]; bat += [a, b]; cur = ('pr', a, b)
            elif t == 'UCHLA' and 'kopie' in r[8].lower():
                kopie[r[3].strip()] = (ts, int(n(r[5])))
            continue
        if cur is None: continue
        if t == 'OBPOL': cur[2] += n(r[5]) * n(r[6])
        elif t == 'OSPOL': cur[2] -= abs(n(r[5]) * n(r[6]))
        elif t == 'PRPOL': v = n(r[5]) * n(r[6]); cur[1][2] -= v; cur[2][2] += v
    return bat, kopie


def match(bills, bat, kopie):
    mins = lambda ts: int(ts[9:11]) * 60 + int(ts[12:14])
    per = collections.defaultdict(list)
    for ts, tb, v in bat:
        if abs(v) > 0.001: per[(ts[:8], tb)].append((ts, v))
    cand = collections.defaultdict(set)
    for (d, tb), L in per.items():
        for i in range(len(L)):
            s = 0.0
            for j in range(i, min(len(L), i + 10)):
                if mins(L[j][0]) - mins(L[i][0]) > 300: break
                s += L[j][1]
                cand[round(s, 2)].add((L[j][0], tb))
    for c, (ts, tb) in kopie.items():  # kopie účtu Qerko v exportu = přesný čas
        for b in bills:
            if b['c'] == c: b['kopie'] = (ts, tb)
    times = sorted({c[0] for cs in cand.values() for c in cs} | {b['kopie'][0] for b in bills if 'kopie' in b})
    ix = {t: i for i, t in enumerate(times)}; N = len(times)
    tree = [(0, -1)] * (N + 2); nodes = []

    def upd(i, val):
        i += 1
        while i <= N + 1:
            if val[0] > tree[i][0]: tree[i] = val
            i += i & -i

    def qry(i):
        i += 1; r = (0, -1)
        while i > 0:
            if tree[i][0] > r[0]: r = tree[i]
            i -= i & -i
        return r
    for qi, b in enumerate(bills):
        if b['t'] <= 0: continue
        cs = {b['kopie']} if 'kopie' in b else cand.get(round(b['t'], 2), ())
        w = 50 if 'kopie' in b else 1
        new = []
        for t, tb in cs:
            p = qry(ix[t]); new.append((p[0] + w, p[1], qi, t, tb))
        base = len(nodes); nodes += new
        for k, x in enumerate(new): upd(ix[x[3]], (x[0], base + k))
    best = max(range(len(nodes)), key=lambda i: nodes[i][0]) if nodes else -1
    while best >= 0:
        sc, par, qi, t, tb = nodes[best]; bills[qi]['cas'] = t; bills[qi]['stul'] = tb; best = par
    sp = [i for i, b in enumerate(bills) if 'cas' in b]
    for i, b in enumerate(bills):
        if 'cas' in b or b['t'] <= 0: continue
        k = bisect.bisect(sp, i); p = sp[k - 1] if k else None; q = sp[k] if k < len(sp) else None
        src = p if q is None or (p is not None and (bills[p]['cas'][:8] == bills[q]['cas'][:8] or i - p <= q - i)) else q
        if src is not None: b['odhad'] = bills[src]['cas']
    return bills


def split(b):
    """Hrubá částka 12 % (jídlo, 41) a 21 % (nápoje, 51) podle DPH účtu."""
    t, d = b['t'], b['dph']
    x = (d - t * 12 / 112) / (21 / 121 - 12 / 112)
    x = min(max(x, 0.0), t)
    return round(t - x, 2), round(x, 2)


def main(pdf, txt, state):
    P = pdf_bills(pdf)
    bills = [b for b in P['ucty'] if b['t'] > 0]  # záporné = storna/změny platby, ty už jsou v TXT exportu
    bat, kopie = order_batches(txt)
    match(bills, bat, kopie)
    fn = os.path.basename(txt)
    m = re.match(r'^(\d{2})(\d{2})_(.+)\.(txt|csv)$', fn, re.I)
    key = f'20{m.group(1)}-{int(m.group(2)):02d}_{re.sub(r"[^a-z0-9._-]+", "-", m.group(3).lower())}'
    days = {}
    for b in bills:
        ts = b.get('cas') or b.get('odhad')
        if not ts: continue
        d, hr = ts[:8], str(int(ts[9:11]))
        X = days.setdefault(d, {'p': 0.0, 'pn': 0, 'h': {}, 'hn': {}, 'k': {'41': 0.0, '51': 0.0}, 'kn': {'41': 0, '51': 0}, 'kh': {}})
        j, a = split(b)
        X['p'] += b['t']; X['pn'] += 1
        X['h'][hr] = X['h'].get(hr, 0.0) + b['t']; X['hn'][hr] = X['hn'].get(hr, 0) + 1
        X['k']['41'] += j; X['k']['51'] += a
        X['kn']['41'] += j > 0.5; X['kn']['51'] += a > 0.5
        kh = X['kh'].setdefault(hr, {'41': 0.0, '51': 0.0}); kh['41'] += j; kh['51'] += a
    rnd = lambda o: {k: rnd(v) for k, v in o.items()} if isinstance(o, dict) else (round(o, 2) if isinstance(o, float) else o)
    sparovano = [b for b in bills if 'cas' in b]
    out = {'zdroj': os.path.basename(pdf), 'restaurace': P['restaurace'], 'celkem_pdf': P['celkem'],
           'ucty': len(bills), 'celkem_ucty': round(sum(b['t'] for b in bills), 2),
           'sparovano': len(sparovano), 'sparovano_kc': round(sum(b['t'] for b in sparovano), 2),
           'days': rnd(dict(sorted(days.items()))),
           'detail': [[b['c'], b['t'], b.get('cas') or b.get('odhad'), 'přesně' if 'cas' in b else 'odhad'] for b in bills]}
    json.dump(out, open(os.path.join(state, key + '_qerko.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print(json.dumps({k: v for k, v in out.items() if k not in ('days', 'detail')} | {'klic': key}, ensure_ascii=False))


if __name__ == '__main__':
    main(*sys.argv[1:4])
