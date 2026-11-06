#!/usr/bin/env python3
"""Zpracování exportů z pokladny (TXT/CSV log) na HTML reporty tržeb.

Použití:  python3 zpracuj.py <slozka_se_soubory> <slozka_data> <slozka_vystup>

- Název souboru: RRMM_restaurace.TXT (např. 2610_cantinau_jezd.TXT = říjen 2026, cantinau_jezd).
- Klíč reportu: 2026-10_cantinau_jezd. Data za dny se ukládají do <slozka_data>/<klic>.json;
  nový soubor se stejným názvem dny doplní (dny obsažené v novém souboru přepíše, ostatní ponechá).
- Už zpracovaný soubor (stejný obsah, podle SHA-256) se přeskočí.
- Hezké názvy restaurací: <slozka_data>/nazvy.json, např. {"cantinau_jezd": "Cantina Ujezd"}.
- Vlastní klíče (část za RRMM_): <slozka_data>/klice.json, např. {"trautenberk úpa": "trautenberk-upa"}.
- Sloučené reporty: <slozka_data>/slouceni.json, "*" = všechny restaurace, nebo seznam např. ["bbcrew"] – místo měsíčního reportu se vytvoří jeden
  report <slug> se všemi měsíci (klíč bez RRMM, v šabloně volba měsíce + Od–Do). Data zůstávají po měsících.
- Více pokladen jedné restaurace: <slozka_data>/pokladny.json, např. {"fastgood1": "fastgood"} – soubor RRMM_Fastgood1.TXT
  je další pokladna reportu fastgood; data se ukládají zvlášť do <RRRR-MM>_fastgood.p-fastgood1.json a v reportu se sčítají.
- Přejmenování typů plateb: <slozka_data>/platby.json, např. {"Cantina": "Hotově"}; klíč končící * = začátek názvu,
  např. {"Hotelovy ucet*": "Platba hotelový účet"} sloučí všechny „Hotelovy ucet,<pokoj>/<jméno>/“.
- Vypíše JSON se seznamem vytvořených reportů (klíč, název, cesta k HTML, soubor).
"""
import csv, hashlib, io, json, os, re, sys
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict

MESICE = ['leden', 'únor', 'březen', 'duben', 'květen', 'červen', 'červenec', 'srpen', 'září', 'říjen', 'listopad', 'prosinec']
STR = ['41', '51', '71', '52', '42']  # střediska, která šablona zná
ZAK = re.compile(r'^\W*z[aá]kazn[ií]k', re.I)  # položky „zákazník ráno / večer“ = počet hostů (klíč dne 'gz' po hodinách; šablona je použije místo PHOST)
UVER = re.compile(r'^\s*(konzumační karta|karta faktura|personaln[ií] strava)', re.I)  # platby na kartu / personální strava – ukládají se i položky po držitelích (klíč dne 'ki'; Export Personální strava)
HERE = os.path.dirname(os.path.abspath(__file__))


def read_rows(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8', 'cp1250'):
        try:
            txt = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    return [r for r in csv.reader(io.StringIO(txt)) if len(r) >= 9], hashlib.sha256(raw).hexdigest()


def num(s):
    try: return float(s)
    except ValueError: return 0.0


def rozdel(celek, vahy):
    """Rozdělí celé číslo (haléře) podle vah tak, aby součet přesně seděl (zbytek k největší položce)."""
    sw = sum(vahy)
    if not vahy or abs(sw) < 1e-9: return None
    res = [int(Decimal(celek * v / sw).quantize(Decimal(1), ROUND_HALF_UP)) for v in vahy]
    i = max(range(len(vahy)), key=lambda j: abs(vahy[j])); res[i] += celek - sum(res)
    return res


def dph_ucet(total, lines, plt):
    """Platby účtu po sazbách DPH, v haléřích: {index platby: {sazba: [tržba, DPH]}}.
    Tržba účtu se rozdělí do sazeb podle položek (slevy poměrně), DPH = tržba × sazba / (100 + sazba)
    zaokrouhlené na haléře za každou sazbu účtu (výpočet shora jako na dokladu), pak poměrně do plateb."""
    T = int(Decimal(str(total)).scaleb(2).quantize(Decimal(1), ROUND_HALF_UP))
    A = [int(Decimal(str(num(p[6]))).scaleb(2).quantize(Decimal(1), ROUND_HALF_UP)) for p in plt]
    w = defaultdict(float)
    for l in lines: w[l[4]] += l[2]
    S = sorted(w)
    def sazby(celek):
        g = rozdel(celek, [w[s] for s in S])
        if g is None: return None
        return {s: [g[i], int((Decimal(g[i]) * int(s) / (100 + int(s))).quantize(Decimal(1), ROUND_HALF_UP))] for i, s in enumerate(S)}
    out = {}
    if sum(A) == T and T != 0:
        R = sazby(T)
        if R is None: return {i: {'x': [a, 0]} for i, a in enumerate(A)}
        for s, (g, d) in R.items():
            gi, di = rozdel(g, A), rozdel(d, A)
            for i in range(len(A)): out.setdefault(i, {})[s] = [gi[i], di[i]]
        return out
    for i, a in enumerate(A):  # platby nesedí na součet účtu (nulový účet apod.) – každá platba zvlášť
        R = sazby(a) if a else {}
        out[i] = R if R is not None else {'x': [a, 0]}
    return out


def ik(code, name):
    """Klíč položky = číslo + název (stejné číslo může pokladna v čase použít pro jinou položku)."""
    n = re.sub(r'\s+', ' ', name).strip()
    return code.strip() + '~' + n if n else code.strip()


def pay_label(code, pozn):
    p = pozn.strip().rstrip(',').strip()
    p = re.sub(r'\s*Kč$', '', p)
    return p or code.strip() or 'Neuvedeno'


def map_pay(lab, platby):
    """Přejmenování typu platby podle platby.json; klíč končící * = začátek názvu (např. "Hotelovy ucet*")."""
    if lab in platby:
        return platby[lab]
    for k, v in platby.items():
        if k.endswith('*') and lab.lower().startswith(k[:-1].lower()):
            return v
    return lab


def parse(rows, platby=None, bez_plateb=()):
    platby = platby or {}
    names, grp, grpc, grpn = {}, {}, {}, {}
    for r in rows:  # skupiny zboží z objednávek (OBPOL, PRPOL, CLPOL, OSPOL) – podle čísla i názvu položky (pokladna čísla položek znovu používá)
        if r[0] in ('OBPOL', 'PRPOL', 'CLPOL', 'OSPOL') and r[8].strip():
            grp[ik(r[3], r[2])] = r[8].strip(); grpc[r[3].strip()] = r[8].strip(); grpn[ik('', r[2])] = r[8].strip()
    days = {}

    def day(d):
        if d not in days:
            days[d] = {'h': defaultdict(float), 'hn': defaultdict(int), 'i': {}, 'p': defaultdict(float), 'w': {},
                       'k': {s: 0.0 for s in STR}, 'g': {}, 'wi': {}, 'pn': defaultdict(int), 's': [], 'e': [], 'gh': defaultdict(int), 'kh': {}, 'o': {}, 'on': {}}
        return days[d]

    def add2(o, k, q, v):
        a = o.setdefault(k, [0.0, 0.0]); a[0] += q; a[1] += v

    # seskupení účtů: hlavička + položky + platby
    docs, cur, es, dph, ob, os_, ph = [], None, None, {}, None, None, None
    for r in rows:
        t = r[0]
        if t == 'PHOST':  # počet hostů – započítá se k účtu, který následuje na stejném stole (opakovaný záznam, změna platby ani záznam bez účtu se nepočítají)
            ph = r; continue
        if t == 'OBHLA':  # objednávka číšníka (storna OSHLA se odečítají, zrušené před objednáním ESHLA se nepočítají) – klíč dne 'o' {číšník: {položka: [ks, Kč]}}, 'on' {číšník: počet objednávek}
            ob = None if bez_plateb or r[8].strip().upper() == 'AUTOMATIC' else {'h': r, 'n': False}; os_ = None; continue  # AUTOMATIC = položky vrácené na stůl po stornu účtu (V…), nejde o novou objednávku
        if t == 'OBPOL':
            if ob and r[1] == ob['h'][3]:
                h_ = ob['h']; X_ = day(h_[1]); w_ = h_[4].strip() or '?'; code = ik(r[3], r[2]); q = num(r[5])
                if r[2].strip(): names.setdefault(code, r[2].rstrip())
                add2(X_['o'].setdefault(w_, {}), code, q, q * num(r[6]))
                if not ob['n']: ob['n'] = True; X_['on'][w_] = X_['on'].get(w_, 0) + 1
            continue
        if t == 'OSHLA':  # storno objednávky – položky (záporné) se odečtou od objednávek číšníka, který storno provedl; počet objednávek se nemění
            ob = None; os_ = None if bez_plateb else {'h': r}; continue
        if t == 'OSPOL':
            if os_ and r[1] == os_['h'][3]:
                h_ = os_['h']; X_ = day(h_[1]); w_ = h_[4].strip() or '?'; code = ik(r[3], r[2]); q = num(r[5])  # OSPOL: záporné ks, cena s obráceným znaménkem -> hodnota storna = ks × původní cena
                if r[2].strip(): names.setdefault(code, r[2].rstrip())
                add2(X_['o'].setdefault(w_, {}), code, q, -q * num(r[6]))
            continue
        if t == 'ESHLA':  # zrušená objednávka před objednáním – jen přehled, do tržby se nepočítá
            es = {'h': r, 'pol': []}; day(r[1])['e'].append(es); continue
        if t == 'ESPOL':
            if es and r[1] == es['h'][3]: es['pol'].append(r)
            continue
        if t in ('UCHLA', 'USHLA'):
            if 'kopie' in r[8].lower():  # opakovaný tisk účtu – nepočítat znovu
                cur = None; continue
            cur = {'t': t, 'h': r, 'pol': [], 'plt': []}; docs.append(cur)
            if t == 'UCHLA' and ph and ph[1] == r[1] and ph[5].strip() == r[5].strip():  # hosté (PHOST těsně před účtem na stejném stole) – pro report po dnech podle typu platby
                cur['g'] = int(num(ph[6]))
            ph = None
        elif t in ('UCPOL', 'USPOL') and cur and r[1] == cur['h'][3]:
            cur['pol'].append(r)
        elif t in ('UCPLT', 'USPLT') and cur and r[1] == cur['h'][3]:
            if r[3].strip().upper() != 'CZK' and abs(num(r[5]) - num(r[6])) >= 0.005:
                r = r[:6] + [r[5]] + r[7:]  # cizí měna (např. EUR): 6. sloupec = částka v Kč, 7. = částka v měně; prázdná měna s Kč = 0 – vzít 6. sloupec
            cur['plt'].append(r)

    if bez_plateb:  # varianta reportu bez účtů placených vybranými typy plateb (filtry.json)
        docs = [doc for doc in docs if not (doc['plt'] and all(map_pay(l, platby) in bez_plateb for l in (pay_label(p[2], p[8]) for p in doc['plt'])))]
    for doc in docs:
        h = doc['h']; d = h[1]; X = day(d)
        cnt = doc['t'] == 'UCHLA' and 'zmena platby' not in h[8].lower()  # změna platby = nový účet, nepočítat znovu
        hr = str(int(h[2][:2])) if re.match(r'^\d\d:', h[2]) else '0'
        waiter = h[4].strip() or '?'
        total = num(h[6])
        lines = []
        for p in doc['pol']:
            code = ik(p[3], p[2]); q = num(p[5]); price = num(p[6])
            if code not in grp:  # položka bez objednávky v exportu – skupina podle stejného názvu, jinak podle čísla
                g_ = grpn.get(ik('', p[2])) or grpc.get(p[3].strip())
                if g_: grp[code] = g_
            val = q * price if doc['t'] == 'UCHLA' or q < 0 else -abs(q) * abs(price)  # storno se zápornými ks: ks × cena (storno voucheru/slevové položky = +)
            if doc['t'] == 'USHLA': q = -abs(q)
            st = p[8].strip().split('/')[0]
            nm = p[2].rstrip()
            if nm.strip(): names[code] = nm
            if num(p[7]) > 1: dph[code] = round(num(p[7]), 2)
            lines.append([code, q, val, st, str(round((num(p[7]) - 1) * 100)) if num(p[7]) > 1 else '0'])
        gross = sum(l[2] for l in lines)
        f = total / gross if abs(gross) > 0.005 else 0.0  # slevy rozpuštěny poměrně do položek
        X['h'][hr] += total
        if doc.get('g') and cnt: X['gh'][hr] += doc['g']
        if cnt: X['hn'][hr] += 1
        w = X['w'].setdefault(waiter, [0, 0.0]); w[1] += total
        if cnt: w[0] += 1
        for code, q, val, st, _ in lines:
            if ZAK.search(names.get(code, '')): X.setdefault('gz', defaultdict(int))[hr] += int(round(q))
            v = round(val * f, 2)
            add2(X['i'], code, q, v)
            add2(X['g'], grp.get(code, 'bez skupiny'), q, v)
            add2(X['wi'].setdefault(waiter, {}), code, q, v)
            ip = X.setdefault('ip', {}); ip[code] = ip.get(code, 0.0) + val  # tržba položky před slevou (ceník × ks)
            wp = X.setdefault('wip', {}).setdefault(waiter, {}); wp[code] = wp.get(code, 0.0) + val
            X['k'][st] = X['k'].get(st, 0.0) + v
            kh = X['kh'].setdefault(hr, {}); kh[st] = kh.get(st, 0.0) + v
        labs = []; PD = dph_ucet(total, lines, doc['plt'])
        for pi_, p in enumerate(doc['plt']):
            lab = pay_label(p[2], p[8]); lab = map_pay(lab, platby); labs.append(lab)
            if lab in bez_plateb: continue  # smíšená platba: část placenou vyloučeným typem nezapočítat
            X['p'][lab] += num(p[6])
            pdl = X.setdefault('pd', {}).setdefault(lab, {})  # platba po sazbách DPH: {sazba: [tržba, DPH]} v Kč (součet haléřových hodnot z účtů)
            for s_, (g_, d_) in PD.get(pi_, {}).items():
                if g_ or d_: o_ = pdl.setdefault(s_, [0, 0]); o_[0] += g_; o_[1] += d_
            if UVER.search(lab):  # položky po držitelích (podíl platby na účtu; u nulového účtu rovným dílem mezi platby)
                sh = num(p[6]) / total if abs(total) > 0.005 else 1 / len(doc['plt']); kd = X.setdefault('ki', {}).setdefault(lab, {})
                for code, q, val, st, _ in lines: add2(kd, code, q * sh, val * f * sh)
            X['pn'][lab] += 1 if cnt else 0
            shp = num(p[6]) / total if abs(total) > 0.005 else 1 / len(doc['plt'])  # podíl platby na účtu
            kp = X.setdefault('khp', {}).setdefault(lab, {}).setdefault(hr, {})  # tržba po hodinách a střediscích podle typu platby
            for code, q, val, st, _ in lines: kp[st] = kp.get(st, 0.0) + val * f * shp
            if doc.get('g') and cnt: gp = X.setdefault('ghp', {}).setdefault(lab, {}); gp[hr] = gp.get(hr, 0.0) + doc['g'] * shp  # hosté podle typu platby (podílem)
        if doc['t'] == 'USHLA':  # seznam storen: čas, účet, číšník, stůl, částka, původní účet, platba, položky, příznak změny platby
            doc['row'] = [h[2], h[3].strip(), waiter, h[5].strip(), abs(total), h[8].strip(), ', '.join(dict.fromkeys(labs)),
                          [[l[0], abs(l[1]), abs(round(l[2] * f, 2))] for l in lines]]
            if h[8].strip().upper().startswith('Z'): doc['row'].append('Z')  # Z… = jen změna typu platby
            X['s'].append(doc['row'])
    for d, X in days.items():  # zrušené objednávky: čas, objednávka, číšník, stůl, částka, položky [název, ks, Kč]
        X['e'] = [[e['h'][2], e['h'][3].strip(), e['h'][4].strip() or '?', e['h'][5].strip(), round(sum(num(p[5]) * num(p[6]) for p in e['pol']), 2),
                   [[(p[2][:-3] if len(p[2]) >= 30 else p[2]).strip(), num(p[5]), round(num(p[5]) * num(p[6]), 2)] for p in e['pol']]] for e in X['e'] if e['pol']]

    for X in days.values():
        if bez_plateb: X.pop('o', None); X.pop('on', None)  # objednávky nejdou přiřadit k platbám – ve filtrované variantě nejsou
        for lab, o in X.get('pd', {}).items():
            for s_ in o: o[s_] = [round(o[s_][0] / 100, 2), round(o[s_][1] / 100, 2)]

    def clean(o):
        if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list): return [clean(v) for v in o]
        if isinstance(o, float): return round(o, 2)
        return o
    return names, grp, dph, {d: clean(dict(v)) for d, v in days.items()}


def bez_obj(D, ok):
    """Objednávky po číšnících (klíče dne 'o', 'on') jen pro restaurace z objednavky.json ('*' = všechny); ostatním se do reportu nevkládají."""
    if ok: return D
    return {**D, 'days': {d: {k: v for k, v in x.items() if k not in ('o', 'on')} for d, x in D['days'].items()}}


DETAIL_CISNICI_MESICU = 3  # položky po číšnících (wi, wip) po dnech jen za poslední N měsíců, starší sečtené za měsíc


def kompaktni(D):
    """Zmenší data vkládaná do HTML (data v _data se nemění):
    1) u měsíců starších než posledních DETAIL_CISNICI_MESICU sečte položky po číšnících (wi, wip) za celý měsíc
       a uloží je k prvnímu dni měsíce (seznam takových měsíců v 'wm'),
    2) názvy položek („kód~název“) uloží jednou do seznamu 'K' a v datech dne je nahradí indexy
       (slovník {položka: hodnota} -> {"~": [[index, hodnota], …]}; šablona to při načtení rozbalí zpět),
    3) celá čísla zapíše bez „.0“."""
    days = D.get('days', {})
    mes = sorted({d[:6] for d in days})
    stare = set(mes[:-DETAIL_CISNICI_MESICU]) if len(mes) > DETAIL_CISNICI_MESICU else set()
    nd = {}
    prvni = {}
    for d in sorted(days):
        x = days[d]
        if d[:6] in stare and ('wi' in x or 'wip' in x):
            x = dict(x); f = prvni.setdefault(d[:6], d)
            agg = nd[f] if f in nd else None
            wi, wip = x.pop('wi', {}), x.pop('wip', {})
            if f == d: x['wi'], x['wip'] = {}, {}; agg = x
            for w, it in wi.items():
                o = agg['wi'].setdefault(w, {})
                for k, v in it.items():
                    a = o.setdefault(k, [0, 0]); a[0] = round(a[0] + v[0], 3); a[1] = round(a[1] + v[1], 2)
            for w, it in wip.items():
                o = agg['wip'].setdefault(w, {})
                for k, v in it.items(): o[k] = round(o.get(k, 0) + v, 2)
        nd[d] = x
    K, ix = [], {}
    def kod(o):
        r = []
        for k, v in o.items():
            if k not in ix: ix[k] = len(K); K.append(k)
            r.append([ix[k], v])
        return {'~': r}
    def cis(v):
        if isinstance(v, float): return int(v) if v.is_integer() else round(v, 2)
        if isinstance(v, list): return [cis(a) for a in v]
        if isinstance(v, dict): return {k: cis(a) for k, a in v.items()}
        return v
    for d, x in nd.items():
        x = dict(x)
        for p in ('i', 'ip'):
            if isinstance(x.get(p), dict): x[p] = kod(x[p])
        for p in ('wi', 'wip', 'ki'):
            if isinstance(x.get(p), dict): x[p] = {w: kod(it) for w, it in x[p].items()}
        nd[d] = cis(x)
    out = {k: v for k, v in D.items() if k != 'days'}
    out.update({'days': nd, 'K': K, 'wm': sorted(stare)})
    return out


def build_html(tpl, title, D, stn, denni, mesicni=False, koef=False):
    return (tpl.replace('/*TITLE*/', title).replace('/*STN*/0', json.dumps(stn, ensure_ascii=False))
            .replace('/*DENNI*/0', '1' if denni else '0').replace('/*MESICNI*/0', '1' if mesicni else '0').replace('/*KOEF*/0', '1' if koef else '0')
            .replace('/*DATA*/', json.dumps(kompaktni(D), ensure_ascii=False, separators=(',', ':'))))


def secti(a, b):
    """Sečte data dne ze dvou pokladen (čísla sčítá, seznamy [ks, Kč] po prvcích, seznamy storen/zrušených spojí)."""
    if isinstance(a, dict) and isinstance(b, dict):
        o = dict(a)
        for k, v in b.items(): o[k] = secti(o[k], v) if k in o else v
        return o
    if isinstance(a, list) and isinstance(b, list):
        if len(a) == len(b) == 2 and all(isinstance(x, (int, float)) for x in a + b):
            return [round(a[0] + b[0], 2), round(a[1] + b[1], 2)]
        return a + b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)): return round(a + b, 2)
    return b


def rekey(D):
    """Starší data s položkami jen podle čísla převede na klíč číslo~název (podle názvů z téhož souboru)."""
    N = D.get('names', {})
    def k(c):
        return c if '~' in c or c not in N or N[c] == c else ik(c, N[c])
    if not any('~' not in c and N[c] != c for c in N): return D
    for x in D['days'].values():
        for a in ('i',):
            if a in x: x[a] = {k(c): v for c, v in x[a].items()}
        for a in ('wi', 'ki'):
            if a in x: x[a] = {w: {k(c): v for c, v in o.items()} for w, o in x[a].items()}
        for r in x.get('s', []):
            if len(r) > 7 and isinstance(r[7], list):
                for l in r[7]:
                    if l and isinstance(l[0], str): l[0] = k(l[0])
    for a in ('grp', 'dph'):
        D[a] = {k(c): v for c, v in D.get(a, {}).items()}
    D['names'] = {k(c): v for c, v in N.items()}
    return D


def sloucit(state, mkey, mesic=None):
    """Spojí měsíční data <RRRR-MM>_<mkey>.json (+ další pokladny <RRRR-MM>_<mkey>.p-*.json) do jednoho
    (novější měsíce mají přednost v názvech/skupinách). mesic='RRRR-MM' = jen jeden měsíc."""
    M = {'names': {}, 'grp': {}, 'dph': {}, 'days': {}}
    pm = re.escape(mesic) if mesic else r'\d{4}-\d{2}'
    for f in sorted(os.listdir(state), key=lambda f: (f[:7], '.p-' in f, f)):
        if re.match(r'^' + pm + '_' + re.escape(mkey) + r'(\.p-[^.]+)?\.json$', f):
            D = rekey(json.load(open(os.path.join(state, f), encoding='utf-8')))
            for k in ('names', 'grp', 'dph'): M[k].update(D.get(k, {}))
            for d, x in D['days'].items(): M['days'][d] = secti(M['days'][d], x) if d in M['days'] else x
    M['days'] = dict(sorted(M['days'].items()))
    return M


NERESTAURACE = {'_data', '_system', '_ftp', 'hotove', 'claude outputs'}


def vstupy(src):
    """Vstupní soubory: (a) RRMM_název.txt/csv/xlsx přímo v src; (b) v podsložkách restaurací
    (src/<Restaurace>/zu2611.txt) – název podsložky = restaurace, RRMM z názvu souboru.
    Vrací [(soubor pro výpis, cesta, zpracováno jako „RRMM_Restaurace.ext“)]."""
    out = []
    for fn in sorted(os.listdir(src)):
        p = os.path.join(src, fn)
        if os.path.isfile(p):
            if re.match(r'^\d{4}_.+\.(txt|csv|xlsx)$', fn, re.I):
                out.append((fn, p, fn))
            continue
        if not os.path.isdir(p) or fn.lower() in NERESTAURACE or fn.startswith(('_', '.')):
            continue
        for f in sorted(os.listdir(p)):
            fp = os.path.join(p, f)
            ext = os.path.splitext(f)[1]
            if not os.path.isfile(fp) or ext.lower() not in ('.txt', '.csv', '.xlsx'):
                continue
            rrmm = next((x for x in re.findall(r'(?<!\d)(\d{2}(?:0[1-9]|1[0-2]))(?!\d)', os.path.splitext(f)[0])), None)
            if rrmm:
                out.append((fn + '/' + f, fp, f'{rrmm}_{fn.strip()}{ext}'))
    return out


def main(src, state, out):
    os.makedirs(state, exist_ok=True); os.makedirs(out, exist_ok=True)
    seen_p = os.path.join(state, 'zpracovano.json')
    seen = json.load(open(seen_p, encoding='utf-8')) if os.path.exists(seen_p) else {}
    nazvy_p = os.path.join(state, 'nazvy.json')
    nazvy = json.load(open(nazvy_p, encoding='utf-8')) if os.path.exists(nazvy_p) else {}
    platby_p = os.path.join(state, 'platby.json')
    platby = json.load(open(platby_p, encoding='utf-8')) if os.path.exists(platby_p) else {}
    klice_p = os.path.join(state, 'klice.json')
    klice = json.load(open(klice_p, encoding='utf-8')) if os.path.exists(klice_p) else {}
    opt = lambda n, dflt: json.load(open(os.path.join(state, n), encoding='utf-8')) if os.path.exists(os.path.join(state, n)) else dflt
    strediska, denni, filtry = opt('strediska.json', {}), opt('denni.json', []), opt('filtry.json', {})
    slouceni = opt('slouceni.json', [])
    mesicni = opt('mesicni.json', [])  # restaurace s tlačítkem „Měsíční reporty“ ('*' = všechny)
    pokladny = opt('pokladny.json', {})
    objednavky = opt('objednavky.json', ['monarch'])  # restaurace s kartou „Objednávky po číšnících“ ('*' = všechny)
    tpl = open(os.path.join(HERE, 'sablona.html'), encoding='utf-8').read()
    result = []
    for soubor, fp_in, fn in vstupy(src):
        m = re.match(r'^(\d{2})(\d{2})_(.+)\.(txt|csv|xlsx)$', fn, re.I)
        if not m:
            continue
        yy, mm, rest = m.group(1), int(m.group(2)), m.group(3).lower()
        if not 1 <= mm <= 12:
            result.append({'soubor': soubor, 'zpracovano_jako': fn, 'chyba': 'neplatný měsíc v názvu'}); continue
        if m.group(4).lower() == 'xlsx':  # export Loyal Man (kredit akcionářů)
            import loyalman
            fp = fp_in; sha = hashlib.sha256(open(fp, 'rb').read()).hexdigest()
            if seen.get(sha):
                result.append({'soubor': soubor, 'zpracovano_jako': fn, 'preskoceno': 'už zpracováno ' + seen[sha]}); continue
            if not loyalman.je_loyalman(fp):
                result.append({'soubor': soubor, 'zpracovano_jako': fn, 'chyba': 'xlsx není export Loyal Man'}); continue
            slug = klice.get(rest) or re.sub(r"[^a-z0-9._-]+", "-", rest)
            lcfg = json.load(open(os.path.join(state, 'loyalman.json'), encoding='utf-8')).get(slug, {}) if os.path.exists(os.path.join(state, 'loyalman.json')) else {}
            key = slug; title = nazvy.get(rest) or rest.capitalize()  # průběžný report všech měsíců: klíč i název bez měsíce a roku
            ltpl = open(os.path.join(HERE, 'sablona_loyalman.html'), encoding='utf-8').read()
            res, err = loyalman.zpracuj(fp, state, slug, yy, mm, ltpl, title)
            if err:
                result.append({'soubor': soubor, 'zpracovano_jako': fn, 'chyba': err}); continue
            html, souhrn = res; hp = os.path.join(out, key + '.html')
            open(hp, 'w', encoding='utf-8').write(html); seen[sha] = fn
            result.append({'soubor': soubor, 'zpracovano_jako': fn, 'klic': key, 'nazev': title, 'html': hp, **souhrn}); continue
        rows, sha = read_rows(fp_in)
        if seen.get(sha):
            result.append({'soubor': soubor, 'zpracovano_jako': fn, 'preskoceno': 'už zpracováno ' + seen[sha]}); continue
        pokl = ''
        if rest in pokladny:  # další pokladna existující restaurace (např. fastgood1 -> fastgood)
            pokl = '.p-' + re.sub(r"[^a-z0-9_-]+", "-", rest); rest = pokladny[rest]
        mp = re.match(r'^(.+?)_p(\d{1,3})$', rest)  # export z FTP s číslem pokladny: RRMM_Restaurace_pNN (složka pokladny 01, 11, 21 …)
        if mp:
            rest, pc = mp.group(1), mp.group(2)
            slug_p = klice.get(rest) or re.sub(r"[^a-z0-9._-]+", "-", rest)
            hl_p = os.path.join(state, 'pokladny_hlavni.json')  # první pokladna restaurace = hlavní data (navazuje na dosavadní data), další = .p-NN
            hl = json.load(open(hl_p, encoding='utf-8')) if os.path.exists(hl_p) else {}
            if slug_p not in hl:
                hl[slug_p] = pc; json.dump(hl, open(hl_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            if hl[slug_p] != pc: pokl = '.p-' + pc
        slug = klice.get(rest) or re.sub(r"[^a-z0-9._-]+", "-", rest)
        nazev = nazvy.get(rest) or rest.replace('_', ' ').title()
        dopl = opt('doplnky.json', {}).get(slug, [])  # ručně doplněné účty, které pokladna nezapsala do žurnálu (např. chyba SQL žurnálu)
        vyn = {r[1].strip() for r in dopl if r[0] == 'VYNECH'}  # ["VYNECH", "<číslo účtu>", "důvod"] – účet z exportu vyřadit (pokladna ho nepočítá)
        if vyn: rows = [r for r in rows if (r[3] if r[0] in ('UCHLA', 'USHLA') else r[1] if r[0] in ('UCPOL', 'USPOL', 'UCPLT', 'USPLT') else None) not in vyn]
        dopl = [r for r in dopl if r[0] != 'VYNECH']
        qrk = opt('qerko.json', {}).get(slug, {}).get(f'20{yy}-{mm:02d}', [])  # účty Qerko (nejsou v exportu) – qerko2.py ze sestavy Tržby po účtech
        if qrk:
            ids_q = {r[3] for r in rows if r[0] in ('UCHLA', 'USHLA') and 'kopie' not in r[8].lower()}  # kopie účtu Qerko v exportu nevadí
            rows = rows + [r for r in qrk if (r[3] if r[0] == 'UCHLA' else r[1]) not in ids_q]
        if dopl:
            pref_d = f'20{yy}{mm:02d}'
            hla = [r for r in rows if r[0] in ('UCHLA', 'USHLA')]
            ids = {r[3] for r in hla}
            sig = {(r[1], r[5].strip(), round(num(r[6]), 2)) for r in hla if r[0] == 'UCHLA'}
            nove_ids = {r[3] for r in dopl if r[0] == 'UCHLA' and r[1].startswith(pref_d) and r[3] not in ids
                        and (r[1], r[5].strip(), round(num(r[6]), 2)) not in sig}  # když už je účet v exportu, nepřidávat znovu
            rows = rows + [r for r in dopl if (r[3] if r[0] in ('UCHLA', 'USHLA') else r[1]) in nove_ids]
        varianty = [(f'20{yy}-{mm:02d}_{slug}', nazev, ())]
        if slug in filtry:  # např. grandcru -> 2026-10_grandcru_bez-gc „Grandcru MC“ bez účtů placených „Grand Cru“
            fl = filtry[slug]; varianty.append((f'20{yy}-{mm:02d}_{slug}_{fl["klic"]}', fl.get('nazev', nazev), tuple(fl.get('bez_plateb', []))))
        prazdne = False
        for key, nazev_v, bez in varianty:
            names, grp, dph, days = parse(rows, platby, bez)
            if not days:
                prazdne = True; break
            sp = os.path.join(state, key + pokl + '.json')
            D = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else {'names': {}, 'grp': {}, 'days': {}}
            D['names'].update(names); D['grp'].update(grp); D.setdefault('dph', {}).update(dph)
            pref = f'20{yy}{mm:02d}'
            nove = [d for d in days if d.startswith(pref)] or list(days)
            for d in nove: D['days'][d] = days[d]
            D['days'] = dict(sorted(D['days'].items()))
            json.dump(D, open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
            D = sloucit(state, key[8:], key[:7])  # součet všech pokladen za měsíc
            dd = sorted(D['days'])
            mes = {'dny_v_souboru': len(nove), 'dny_celkem': len(dd), 'od': dd[0], 'do': dd[-1],
                   'trzba_celkem': round(sum(sum(x['p'].values()) for x in D['days'].values()))}
            if slouceni == '*' or slug in slouceni:  # sloučený report všech měsíců: klíč bez RRMM (např. bbcrew), název bez měsíce
                mkey = key[8:]; M = sloucit(state, mkey)
                hp = os.path.join(out, mkey + '.html')
                open(hp, 'w', encoding='utf-8').write(build_html(tpl, nazev_v, bez_obj(M, objednavky == '*' or slug in objednavky), strediska.get(slug, {}), slug in denni, mesicni == '*' or slug in mesicni, slug in opt('koeficient.json', [])))
                md = sorted(M['days'])
                result.append({'soubor': soubor, 'zpracovano_jako': fn, 'klic': mkey, 'nazev': nazev_v, 'html': hp, 'slouceny': True, 'mesic': f'20{yy}-{mm:02d}', **mes,
                               'cele_obdobi_od': md[0], 'cele_obdobi_do': md[-1], 'mesicu': len({d[:6] for d in md})})
                continue
            title = f'{nazev_v}, {MESICE[mm - 1]} 20{yy}'
            hp = os.path.join(out, key + '.html')
            open(hp, 'w', encoding='utf-8').write(build_html(tpl, title, bez_obj(D, objednavky == '*' or slug in objednavky), strediska.get(slug, {}), slug in denni, mesicni == '*' or slug in mesicni, slug in opt('koeficient.json', [])))
            result.append({'soubor': soubor, 'zpracovano_jako': fn, 'klic': key, 'nazev': title, 'html': hp, **mes})
        if prazdne:
            result.append({'soubor': soubor, 'zpracovano_jako': fn, 'chyba': 'v souboru nejsou žádné účty'}); continue
        seen[sha] = fn
    json.dump(seen, open(seen_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:4])
