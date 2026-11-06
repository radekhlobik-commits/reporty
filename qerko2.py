#!/usr/bin/env python3
"""Doplnění účtů Qerko do reportu tržeb ze sestavy pokladny „TRŽBY PO ÚČTECH“ (TRZ_UCTY1, PDF).

Použití:  python3 qerko2.py <sestava.pdf> <export_pokladny.TXT> <slozka_data> <klic_restaurace>
          např. python3 qerko2.py 2.pdf zu2610.TXT ./data karl-nsk-holka

Účty placené přes Qerko (n_qerko) pokladna do TXT exportu nezapisuje (jen jejich storna). Sestava „Tržby po účtech“
je obsahuje: číslo účtu, částku, den, čas uzavření a dobu sezení. Položky účtu se dohledají v exportu: simuluje se
stav stolů (objednávky OBHLA/OBPOL, storna OSHLA/OSPOL, přesuny PRHLA/PRPOL, uzavřené účty UCHLA/UCPOL) a účet Qerko
se spáruje se stolem, jehož otevřená útrata v čase uzavření odpovídá částce (případně se skupinou několika
po sobě jdoucích plateb Qerko na stejném stole). Nespárované účty dostanou položky „Qerko – jídlo / nápoje“
v poměru spárovaných účtů měsíce.

Výstup: <slozka_data>/qerko.json  {klic: {"RRRR-MM": [řádky ve formátu exportu UCHLA/UCPOL/UCPLT]}}
– zpracuj.py je při zpracování exportu daného měsíce přidá k řádkům exportu (jako běžné účty, platba „Qerko“).
"""
import collections, csv, io, json, os, re, subprocess, sys


def num(s): return float(str(s).replace(' ', '').replace(',', '.'))


def n(s):
    try: return float(s)
    except ValueError: return 0.0


def pdf_qerko(pdf):
    t = subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True, text=True).stdout
    od = re.search(r'Od\s+(\d\d)\.(\d\d)\.(\d{4})', t)
    if not od: raise SystemExit('V PDF není období „Od dd.mm.rrrr“ – je to sestava Tržby po účtech?')
    yyyy, mm = od.group(3), od.group(2)
    pokl = '01'; out = []
    for l in t.splitlines():
        mp = re.match(r'^(\d{2})\s+\d{2} - ', l)
        if mp: pokl = mp.group(1)
        m = re.match(r'^\s*(\d{5})\s+(-?[\d ]*\d,\d\d)\s+(-?[\d ]*\d,\d\d)\s+(-?[\d ]*\d,\d\d)\s+n_qerko\s+(.*)$', l, re.I)
        if not m: continue
        rest = m.group(5)
        dm = re.search(r'(?:(\d{1,3}:\d\d)\s+)?(\d\d) (\d\d):(\d\d)\s+(-?1)\s+(\S+)', rest)
        if not dm: continue
        out.append({'c': pokl + m.group(1), 't': num(m.group(4)), 'trzba': num(m.group(2)), 'den': f'{yyyy}{mm}{dm.group(2)}',
                    'cas': f'{dm.group(3)}:{dm.group(4)}', 'doba': dm.group(1) or '', 'obsl': dm.group(6)})
    return yyyy, mm, out


def rows_of(path):
    raw = open(path, 'rb').read()
    try: txt = raw.decode('utf-8')
    except UnicodeDecodeError: txt = raw.decode('cp1250')
    return [r for r in csv.reader(io.StringIO(txt)) if len(r) >= 9]


def main(pdf, txt, state, klic):
    yyyy, mm, bills = pdf_qerko(pdf)
    kladne = [b for b in bills if b['t'] > 0]  # záporné = storna / změny platby – ty už v exportu jsou
    R = rows_of(txt)
    st_kod = {}  # číslo položky -> středisko (z uzavřených účtů)
    for r in R:
        if r[0] == 'UCPOL' and r[8].strip(): st_kod.setdefault(r[3].strip(), r[8].strip())
    # události z exportu v časovém pořadí (soubor je chronologický); položky patří k poslední hlavičce
    ev, cur = [], None
    for r in R:
        t = r[0]
        if t.endswith('HLA'):
            cur = {'t': t, 'h': r, 'pol': [], 'ts': r[1] + ' ' + r[2][:5]}; ev.append(cur)
        elif t.endswith('POL') and cur and r[1] == cur['h'][3]:
            cur['pol'].append(r)
    for i, b in enumerate(kladne):
        ev.append({'t': 'QERKO', 'b': b, 'ts': b['den'] + ' ' + b['cas'], 'i': i})
    ev.sort(key=lambda e: (e['ts'], e['t'] == 'QERKO'))  # stabilní: v rámci minuty nejdřív export, pak Qerko
    # dávky objednávek po stolech: [čas, stůl, hodnota, položky]; storno a přesun se odečítá / přičítá
    mins = lambda ts: int(ts[9:11]) * 60 + int(ts[12:14])
    per = collections.defaultdict(list)
    for e in ev:
        t = e['t']
        if t not in ('OBHLA', 'OSHLA', 'PRHLA'): continue
        L = [[p[3].strip(), p[2], n(p[5]), n(p[6]), p[7], p[8]] for p in e['pol']]
        if t == 'OSHLA': L = [[x[0], x[1], -abs(x[2]), abs(x[3]), x[4], x[5]] for x in L]
        if t == 'PRHLA':
            per[(e['ts'][:8], e['h'][5].strip())].append({'ts': e['ts'], 'v': -sum(x[2] * x[3] for x in L), 'L': [[x[0], x[1], -x[2], x[3], x[4], x[5]] for x in L], 'u': False})
            tb = e['h'][6].strip()
        else: tb = e['h'][5].strip()
        per[(e['ts'][:8], tb)].append({'ts': e['ts'], 'v': sum(x[2] * x[3] for x in L), 'L': L, 'u': False})
    par = {}
    poradi = sorted(range(len(kladne)), key=lambda i: (kladne[i]['den'], kladne[i]['cas']))
    for i in poradi:
        b = kladne[i]; t = mins(b['den'] + ' ' + b['cas'])
        doba = (int(b['doba'].split(':')[0]) * 60 + int(b['doba'].split(':')[1])) if b['doba'] else 240
        od = t - doba - 45; best = None
        for (den, tb), B in per.items():
            if den != b['den']: continue
            for j in range(len(B)):
                if B[j]['u'] or mins(B[j]['ts']) < od or mins(B[j]["ts"]) > t + 3: continue
                s_ = 0.0
                for k in range(j, len(B)):
                    if B[k]["u"] or mins(B[k]["ts"]) > t + 3: break
                    s_ += B[k]['v']
                    if abs(s_ - b['t']) < 0.01:
                        sk = (mins(B[k]['ts']), -(k - j))
                        if best is None or sk > best[0]: best = (sk, tb, B, j, k)
        if best:
            _, tb, B, j, k = best; L = []
            for x in B[j:k + 1]: x['u'] = True; L += x['L']
            agg = collections.OrderedDict()
            for x in L:
                key = (x[0], x[1], x[3], x[4], x[5])
                agg[key] = agg.get(key, 0) + x[2]
            par[i] = (tb, [[c, nm, q, pr, d, g] for (c, nm, pr, d, g), q in agg.items() if abs(q) > 1e-9])
    sp41 = sp51 = 0.0
    for tb, L in par.values():
        for x in L:
            s = st_kod.get(x[0]) or ('51' if n(x[4]) > 1.15 else '41')
            if s == '51': sp51 += x[2] * x[3]
            else: sp41 += x[2] * x[3]
    pomer = sp51 / (sp41 + sp51) if sp41 + sp51 > 0 else 0.5
    out = []
    for i, b in enumerate(kladne):
        tb, L = par.get(i, ('0', None))
        out.append(['UCHLA', b['den'], b['cas'], b['c'], 'QRK', tb, f"{b['t']:.2f}", f"{b['t']:.2f}",
                    'Qerko – doplněno ze sestavy Tržby po účtech' + ('' if L else ' (položky odhadem)')])
        if L:
            soucet = sum(x[2] * x[3] for x in L); k = b['t'] / soucet if soucet else 1
            for x in L:
                out.append(['UCPOL', b['c'], x[1], x[0], '1', f'{x[2]:.3f}', f'{x[3] * k:.2f}', x[4], st_kod.get(x[0]) or ('51' if n(x[4]) > 1.15 else '41')])
        else:
            nap = round(b['t'] * pomer, 2)
            if b['t'] - nap: out.append(['UCPOL', b['c'], 'Qerko – jídlo (odhad)', 'QRK41', '1', '1.000', f"{b['t'] - nap:.2f}", '1.12', '41'])
            if nap: out.append(['UCPOL', b['c'], 'Qerko – nápoje (odhad)', 'QRK51', '1', '1.000', f'{nap:.2f}', '1.21', '51'])
        out.append(['UCPLT', b['c'], 'N_QERKO', 'CZK', ' ', f"{b['t']:.3f}", f"{b['t']:.2f}", '0.00', 'Qerko,'])
    p = os.path.join(state, 'qerko.json')
    Q = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
    Q.setdefault(klic, {})[f'{yyyy}-{mm}'] = out
    json.dump(Q, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    print(json.dumps({'klic': klic, 'mesic': f'{yyyy}-{mm}', 'uctu_qerko': len(kladne), 'kc': round(sum(b['t'] for b in kladne), 2),
                      'storna_v_pdf_kc': round(sum(b['t'] for b in bills if b['t'] < 0), 2),
                      'sparovano_uctu': len(par), 'sparovano_kc': round(sum(kladne[i]['t'] for i in par), 2),
                      'podil_napoju_odhad': round(pomer, 3)}, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) < 5: raise SystemExit(__doc__)
    main(*sys.argv[1:5])
