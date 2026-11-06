"""Kredit akcionářů Pivovar Hostivař z dat konzumačních karet FoodMan (DBF).

Vstup: hot_kred.dbf (seznam karet; akcionáři = FIRMA 'AC'), _tmhkobr.dbf (+ případně hkredobr.dbf) = pohyby karet z H1 i H2.
Pohyb: CASTKA (+ dobití / storno, − čerpání), U_CIS = číslo účtu (21/23… = H1, 11/13… = H2), 'OPRAVA' = ruční dobití/oprava kreditu.
Zůstatek = součet všech pohybů karty od začátku (souhlasí se zůstatky v pokladně).
Výstup: stejná struktura jako loyalman_<slug>.json + HTML ze šablony sablona_hostivar.html.
Položky: z exportů pokladen H1/H2 (RRMM_Pivovar Hostivar H1/H2.TXT nebo jejich .zip v hotove) se k čerpání dohledá účet
(stejný den, karta a částka, nejbližší čas) a jeho položky; dohledané položky se ukládají do stavu a při dalším běhu zůstávají.
Použití: python3 hostivar.py <složka s DBF> <složka _data> <výstupní html> [první měsíc RRRR-MM] [exporty TXT/ZIP …]
"""
import json, os, sys, datetime, re, io, zipfile
from collections import defaultdict
from dbfread import DBF

def nacti(slozka):
    f = {n.lower(): os.path.join(slozka, n) for n in os.listdir(slozka)}
    K = [r for r in DBF(f['hot_kred.dbf'], encoding='cp1250', char_decode_errors='replace')]
    P = []
    for n in ('hkredobr.dbf', '_tmhkobr.dbf'):
        if n in f: P += list(DBF(f[n], encoding='cp1250', char_decode_errors='replace'))
    seen, U = set(), []
    for r in P:  # stejný pohyb v archivu i v aktuálním souboru jen jednou
        k = (r['DATUM'], r['CAS'], r['CIS_KRED'], r['U_CIS'], r['CASTKA'])
        if k in seen or not (r['DATUM'] or '').strip(): continue
        seen.add(k); U.append(r)
    U.sort(key=lambda r: (r['DATUM'], r['CAS']))
    return K, U

def pokladna(u):
    u = (u or '').strip()
    if u == 'OPRAVA': return ''
    if u.isdigit(): return {'1': 'H2', '2': 'H1'}.get(u[0], 'pokladna ' + u[:2])
    return u.title()

def ucty_z_exportu(cesty):
    """{(RRRRMMDD, karta): [[HH:MM, částka, [[název, ks, Kč], …]], …]} z exportů pokladny (TXT nebo ZIP s TXT)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import zpracuj as z
    idx = defaultdict(list)
    for c in cesty:
        soubory = []
        if c.lower().endswith('.zip'):
            with zipfile.ZipFile(c) as zf:
                for n in zf.namelist():
                    if n.lower().endswith('.txt'):
                        tmp = os.path.join('/tmp', 'hv_' + os.path.basename(n)); open(tmp, 'wb').write(zf.read(n)); soubory.append(tmp)
        else: soubory.append(c)
        for f in soubory:
            rows, _ = z.read_rows(f); doc = None; docs = []
            for r in rows:
                if r[0] in ('UCHLA', 'USHLA'): doc = {'h': r, 'pol': [], 'plt': []}; docs.append(doc)
                elif r[0] in ('UCPOL', 'USPOL') and doc: doc['pol'].append(r)
                elif r[0] in ('UCPLT', 'USPLT') and doc: doc['plt'].append(r)
            for d in docs:
                h = d['h']; total = z.num(h[6]); hruba = sum(z.num(p[5]) * z.num(p[6]) for p in d['pol'])
                for p in d['plt']:
                    m = re.search(r',(\d+)/', p[8])
                    if not m: continue
                    cast = z.num(p[6]); f_ = cast / hruba if abs(hruba) > 0.005 else 0
                    pol = []  # [název, ks, jednotka, středisko, Kč/ks, Kč celkem (po slevě, podíl karty)]
                    for q in d['pol']:
                        n = re.sub(r'\s+', ' ', q[2][:27]).strip(); j = q[2][27:].strip()
                        if not n or set(n) <= set('-* '): continue
                        ks, cena = z.num(q[5]), z.num(q[6])
                        if abs(ks) < 0.001 and abs(cena) < 0.005: continue
                        pol.append([n, round(ks, 3), j, q[8].strip().split('/')[0], round(cena, 2), round(ks * cena * f_, 2)])
                    idx[(h[1], m.group(1))].append([h[2][:5], cast, pol])
    return idx

def prirad_polozky(D, idx, stare):
    """K čerpání doplní položky účtu: z exportů (stejný den + karta + částka, nejbližší čas), jinak z minulého stavu."""
    mins = lambda t: int(t[:2]) * 60 + int(t[3:5])
    for p in D['p']:
        for m_, x in p['m'].items():
            for t in x[4]:
                if t[1] != 'C': continue
                kl = (p['e'], m_[:4] + t[0][3:5] + t[0][0:2], t[0][-5:], t[3])
                dd = m_[:4] + t[0][3:5] + t[0][0:2]; tm = t[0][-5:]
                kand = [u for u in idx.get((dd, p['e']), []) if abs(abs(u[1]) - abs(t[3])) < 0.5 and not u[-1:] == ['x']]
                if kand:
                    u = min(kand, key=lambda u: abs(mins(u[0]) - mins(tm)))
                    if abs(mins(u[0]) - mins(tm)) <= 30: t[4] = u[2]; u.append('x'); continue
                if kl in stare: t[4] = stare[kl]

def sestav(K, U, od='2026-01'):
    akc = {r['CIS_KRED'].strip(): r for r in K if (r['FIRMA'] or '').strip().upper() == 'AC'}
    posl = max(r['DATUM'] for r in U)
    do = f'{posl[:4]}-{posl[4:6]}'
    months, y, m = [], int(od[:4]), int(od[5:])
    while f'{y}-{m:02d}' <= do:
        months.append(f'{y}-{m:02d}'); m += 1
        if m > 12: y, m = y + 1, 1
    poc = defaultdict(float); tx = defaultdict(lambda: defaultdict(list))
    for r in U:
        k = r['CIS_KRED'].strip(); c = float(r['CASTKA'] or 0)
        if k not in akc or not c: continue
        mes = f"{r['DATUM'][:4]}-{r['DATUM'][4:6]}"
        if mes < months[0]: poc[k] += c; continue
        typ = 'D' if (r['U_CIS'] or '').strip() == 'OPRAVA' else 'C'
        cas = (r['CAS'] or '')[:5]
        ucet = (r['U_CIS'] or '').strip()
        tx[k][mes].append([f"{r['DATUM'][6:8]}.{r['DATUM'][4:6]}. {cas}", typ, pokladna(r['U_CIS']), round(c, 2), None, ucet if ucet.isdigit() else '', (r['CAS'] or '')[:8]])  # [datum čas, D/C, pokladna, Kč, položky, číslo účtu, čas s vteřinami]
    D = {'months': months, 'to': datetime.datetime.strptime(posl, '%Y%m%d').strftime('%d.%m.%Y'), 'p': []}
    for k, r in sorted(akc.items(), key=lambda x: (x[1]['JMENO'] or '').strip()):
        if r['STORNO'] and not poc[k] and not tx[k]: continue  # zrušená karta bez zůstatku a pohybů
        p = {'n': (r['JMENO'] or '').strip(), 'e': k, 'm': {}}
        tel = next((str(r[f] or '').strip() for f in ('TEL_GSM', 'TEL_DOMU', 'TEL_OFFICE') if str(r[f] or '').strip()), '')
        if tel: p['tel'] = tel
        if (r['EMAIL'] or '').strip(): p['mail'] = r['EMAIL'].strip()
        b = round(poc[k], 2)
        for m_ in months:
            T = tx[k][m_]
            d = round(sum(t[3] for t in T if t[1] == 'D'), 2); c = round(sum(t[3] for t in T if t[1] == 'C'), 2)
            p['m'][m_] = [b, d, c, round(b + d + c, 2), T]; b = round(b + d + c, 2)
        D['p'].append(p)
    return D

if __name__ == '__main__':
    src, state, out = sys.argv[1:4]; od = sys.argv[4] if len(sys.argv) > 4 else '2026-01'
    K, U = nacti(src); D = sestav(K, U, od)
    sp = os.path.join(state, 'loyalman_hostivar-akcionari.json'); stare = {}
    if os.path.exists(sp):
        for p in json.load(open(sp, encoding='utf-8'))['p']:
            for m_, x in p['m'].items():
                for t in x[4]:
                    if len(t) > 4 and t[4] and len(t[4][0]) >= 6: stare[(p['e'], m_[:4] + t[0][3:5] + t[0][0:2], t[0][-5:], t[3])] = t[4]
    prirad_polozky(D, ucty_z_exportu(sys.argv[5:]), stare)
    here = os.path.dirname(os.path.abspath(__file__))
    json.dump(D, open(os.path.join(state, 'loyalman_hostivar-akcionari.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    tpl = open(os.path.join(here, 'sablona_hostivar.html'), encoding='utf-8').read()
    nazev = 'Pivovar Hostivar akcionáři'
    open(out, 'w', encoding='utf-8').write(tpl.replace('/*TITLE*/', nazev).replace('/*DATA*/', json.dumps(D, ensure_ascii=False, separators=(',', ':'))))
    m = D['months'][-1]; S = [round(sum(p['m'][m][i] for p in D['p'])) for i in range(4)]
    print(json.dumps({'klic': 'hostivar-akcionari', 'nazev': nazev, 'html': out, 'mesic': m, 'do': D['to'],
                      'pocatecni_stav': S[0], 'dobiti': S[1], 'cerpani': S[2], 'konecny_stav': S[3],
                      'aktivni': sum(1 for p in D['p'] if p['m'][m][4]), 'akcionaru': len(D['p'])}, ensure_ascii=False, indent=1))
