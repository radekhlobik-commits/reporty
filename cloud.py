"""Klient API webu onlinereport.cz pro zpracování reportů v cloudu (bez počítače).

Nastavení: nastaveni.json vedle skriptu ({"web": "...", "api_token": "..."}) nebo proměnné REPORTY_WEB / REPORTY_TOKEN.
Příkazy:
  info                                   – stav úložiště na serveru
  stahni <co> <složka>                   – stáhne všechny uložené soubory (co = data | system)
  nahraj <co> <složka> [<původní složka>] – nahraje soubory ze složky (s původní složkou jen změněné)
  exporty <složka in> <složka data>      – stáhne nové exporty ze serveru (složka /reporty na FTP): kořen RRMM_název.ext,
                                           složky Restaurace/[pokladna/]soubor (.txt/.csv/.zip – ZIP se rozbalí, i se složkou pokladny uvnitř);
                                           vstupy uloží do <in> jako RRMM_Restaurace[_pNN].ext, přehled do <in>/_exporty.json
  uklid <složka data> <složka in> <jako> … – po zpracování: zpracované exporty (všechny jejich vstupy v seznamu <jako>) archivuje
                                           na serveru; originály z kořene a exporty starší než minulý měsíc smaže
  report <html> <klíč> <název>           – nahraje report na web
"""
import datetime, gzip, hashlib, json, os, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def cfg():
    p = os.path.join(HERE, 'nastaveni.json')
    c = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
    return os.environ.get('REPORTY_WEB') or c['web'], os.environ.get('REPORTY_TOKEN') or c['api_token']


def api(akce, data=b'', raw=False, **q):
    web, tok = cfg()
    url = web + '?' + urllib.parse.urlencode({'api': akce, **q})
    rq = urllib.request.Request(url, data=data or b'', method='POST',
                                headers={'X-Token': tok, 'Content-Type': 'application/octet-stream', 'User-Agent': 'reporty-cloud/1'})
    for pokus in range(3):
        try:
            with urllib.request.urlopen(rq, timeout=180) as r:
                b = r.read()
            break
        except urllib.error.HTTPError as e:
            raise SystemExit(f'API {akce} {q}: HTTP {e.code} {e.read()[:300]!r}')
        except Exception as e:
            if pokus == 2: raise SystemExit(f'API {akce} {q}: {e}')
    if raw:
        return gzip.decompress(b) if b[:2] == b'\x1f\x8b' else b
    j = json.loads(b)
    if not j.get('ok'): raise SystemExit(f'API {akce} {q}: {j}')
    return j


def sha(b): return hashlib.sha256(b).hexdigest()


def stahni(co, d):
    os.makedirs(d, exist_ok=True); n = 0
    for s in api('seznam', co=co)['soubory']:
        open(os.path.join(d, s['f']), 'wb').write(api('nacist', raw=True, co=co, f=s['f'])); n += 1
    print(json.dumps({'stazeno': n, 'co': co}))


def nahraj(co, d, puvodni=None):
    nah = []
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if not os.path.isfile(p) or f.startswith('.'): continue
        b = open(p, 'rb').read()
        if puvodni:
            q = os.path.join(puvodni, f)
            if os.path.exists(q) and open(q, 'rb').read() == b: continue
        api('ulozit', gzip.compress(b, 6), co=co, f=f); nah.append(f)
    print(json.dumps({'nahrano': nah, 'co': co}, ensure_ascii=False))
    if co == 'data' and puvodni and os.environ.get('REPORTY_BEZ_EMAILU') != '1': souhrn(d, nah)  # po uložení stavu naplánovanou úlohou: krátký e-mail


def zpracovano(dd):
    p = os.path.join(dd, 'zpracovano.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}


def stazeno(dd):  # exporty z webu už zpracované: {cesta na serveru: {"sha": sha souboru na serveru, "jako": [názvy vstupů]}}
    p = os.path.join(dd, 'stazeno.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}


def mesic_z_obsahu(b):
    """RRMM podle nejčastějšího data v logu pokladny (řádky "XXX","RRRRMMDD",…)."""
    import collections, re
    t = b.decode('cp1250', 'replace')
    c = collections.Counter(m.group(1)[2:] + m.group(2) for m in re.finditer(r'^"[A-Z]+","(20\d\d)(0[1-9]|1[0-2])[0-3]\d"', t, re.M))
    return c.most_common(1)[0][0] if c else None


def rrmm_z_nazvu(n):
    import re
    m = re.search(r'(?<!\d)(\d{2}(?:0[1-9]|1[0-2]))(?!\d)', os.path.splitext(os.path.basename(n))[0])
    return m.group(1) if m else None


VYNECHAT = {'html', 'kredithostivar', 'kreditspojovna'}  # složky na FTP, které nejsou exporty pokladen restaurací


def rozbal(b, typ):
    """Obsah archivu (zip / 7z) -> [(cesta uvnitř, obsah)] jen .txt/.csv."""
    import io
    out = []
    if typ == 'zip':
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(b))
        for n in z.namelist():
            if not n.endswith('/'): out.append((n, z.read(n)))
    else:
        try:
            import py7zr
        except ImportError:
            import subprocess; subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'py7zr', '--break-system-packages'], check=True)
            import py7zr
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            with py7zr.SevenZipFile(io.BytesIO(b)) as z: z.extractall(path=t)
            for root, _, fs in os.walk(t):
                for f in fs:
                    p = os.path.join(root, f); out.append((os.path.relpath(p, t).replace(os.sep, '/'), open(p, 'rb').read()))
    return [(n.replace('\\', '/'), c) for n, c in out if n.lower().endswith(('.txt', '.csv'))]


def exporty(din, dd):
    """Stáhne nové exporty do <din>. ZIP/7z rozbalí. Vstupy ukládá do kořene <din> jako RRMM_Restaurace[_pNN].ext;
    přehled (cesta na serveru -> názvy vstupů) uloží do <din>/_exporty.json pro „uklid“."""
    seen = zpracovano(dd); st = stazeno(dd); nove, stare, chyby, ign, mapa = [], 0, [], [], {}
    os.makedirs(din, exist_ok=True)
    for e in api('exporty')['exporty']:
        if 'restaurace' in e and (e['restaurace'].lower() in VYNECHAT or e['restaurace'].startswith('_')): continue
        if e['sha'] in seen or st.get(e['f'], {}).get('sha') == e['sha']: stare += 1; continue
        if 'restaurace' not in e:  # soubor v kořeni složky exportů (RRMM_název.ext)
            open(os.path.join(din, e['f']), 'wb').write(api('export', raw=True, f=e['f'])); mapa[e['f']] = {'sha': e['sha'], 'jako': [e['f']]}
            nove.append({'f': e['f'], 'jako': [e['f']]}); continue
        typ = (e.get('typ') or os.path.splitext(e['f'])[1][1:]).lower()
        if typ not in ('zip', '7z', 'txt', 'csv'): ign.append(e['f']); continue
        b = api('export', raw=True, f=e['f'])
        if typ in ('zip', '7z'):
            try: soubory = rozbal(b, typ)
            except Exception as x:
                chyby.append({'f': e['f'], 'chyba': f'archiv nejde rozbalit ({x.__class__.__name__}) – možná se právě nahrává, zkusí se příště'}); continue
        else:
            soubory = [(e['f'], b)]
        jako = []
        for n, obsah in soubory:
            casti = [x for x in n.split('/') if x]
            pk = e.get('pokladna') or next((x for x in casti[:-1] if x.isdigit()), None)
            r = rrmm_z_nazvu(n) or (e.get('rrmm') if typ in ('zip', '7z') else None) or mesic_z_obsahu(obsah)
            if not r:
                if typ in ('txt', 'csv'): ign.append(e['f'])  # např. log přenosu z pokladny – není export
                else: chyby.append({'f': e['f'] + ' / ' + n, 'chyba': 'nelze zjistit měsíc'})
                continue
            ext = os.path.splitext(n)[1] or '.TXT'
            j = f"{r}_{e['restaurace']}{'_p' + pk if pk else ''}{ext}"
            if j in jako or os.path.exists(os.path.join(din, j)):
                chyby.append({'f': e['f'], 'chyba': f'víc souborů pro {j} – použit první'}); continue
            open(os.path.join(din, j), 'wb').write(obsah); jako.append(j)
        if jako: mapa[e['f']] = {'sha': e['sha'], 'jako': jako}; nove.append({'f': e['f'], 'jako': jako})
    json.dump(mapa, open(os.path.join(din, '_exporty.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({'nove': nove, 'uz_zpracovane': stare, 'ignorovano': ign, 'chyby': chyby}, ensure_ascii=False, indent=1))


def uklid(dd, din, ok_jako):
    """Po úspěšném zpracování: zapamatuje si zpracované exporty (stazeno.json, uloží na web) a na serveru je archivuje.
    ok_jako = názvy vstupů ("zpracovano_jako"), které se zpracovaly bez chyby (nebo „preskoceno“).
    Mazání originálů na serveru: soubory z kořene vždy; ve složkách restaurací jen exporty starší než minulý měsíc."""
    mp = os.path.join(din, '_exporty.json')
    mapa = json.load(open(mp, encoding='utf-8')) if os.path.exists(mp) else {}
    st = stazeno(dd); dnes = datetime.date.today()
    m = dnes.replace(day=1) - datetime.timedelta(days=1); hranice = f'{m.year % 100:02d}{m.month:02d}'
    out = []
    for f, x in mapa.items():
        if not x['jako'] or not all(j in ok_jako for j in x['jako']): continue
        koren = '/' not in f; stary = all(j[:4] < hranice for j in x['jako'])
        r = api('archivuj', f=f, jako=x['jako'][0] if f.lower().endswith(('.txt', '.csv', '.xlsx')) else x['jako'][0].rsplit('.', 1)[0] + '.zip', **({'smazat': 1} if koren or stary else {}))
        st[f] = {'sha': x['sha'], 'jako': x['jako']}; out.append({'f': f, **r})
    # starší zpracované exporty, které na serveru zůstaly (aktuální měsíc mezitím skončil)
    for e in api('exporty')['exporty']:
        x = st.get(e['f'])
        if x and x['sha'] == e['sha'] and '/' in e['f'] and all(j[:4] < hranice for j in x['jako']):
            out.append({'f': e['f'], **api('archivuj', f=e['f'], jako=x['jako'][0] if e['f'].lower().endswith(('.txt', '.csv', '.xlsx')) else x['jako'][0].rsplit('.', 1)[0] + '.zip', smazat=1)})
    json.dump(st, open(os.path.join(dd, 'stazeno.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    api('ulozit', gzip.compress(open(os.path.join(dd, 'stazeno.json'), 'rb').read(), 6), co='data', f='stazeno.json')
    print(json.dumps({'archivovano': out}, ensure_ascii=False, indent=1))


SOUHRN = '.souhrn_reporty.json'  # reporty nahrané v tomto běhu (pro e-mail po uložení stavu)


def report(html, key, title):
    r = api('upload', gzip.compress(open(html, 'rb').read(), 9), key=key, title=title)
    S = json.load(open(SOUHRN, encoding='utf-8')) if os.path.exists(SOUHRN) else {}
    S[key] = title; json.dump(S, open(SOUHRN, 'w', encoding='utf-8'), ensure_ascii=False)
    print(json.dumps(r))


def posli_email(predmet, text):
    web, tok = cfg()
    rq = urllib.request.Request(web + 'posta.php?api=souhrn', data=json.dumps({'predmet': predmet, 'text': text}).encode(), method='POST',
                                headers={'X-Token': tok, 'Content-Type': 'application/json', 'User-Agent': 'reporty-cloud/1'})
    try:
        with urllib.request.urlopen(rq, timeout=60) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e:
        b = e.read()[:300]
        return {'ok': False, 'error': f'HTTP {e.code} ' + b.decode('utf-8', 'replace')}
    except Exception as e:
        return {'ok': False, 'error': str(e)[:300]}


MESICE = ['leden', 'únor', 'březen', 'duben', 'květen', 'červen', 'červenec', 'srpen', 'září', 'říjen', 'listopad', 'prosinec']


def souhrn(d, zmenene):
    """Krátký e-mail po aktualizaci (posta.php?api=souhrn): doplněné měsíce s tržbou a nahrané reporty."""
    import re
    S = json.load(open(SOUHRN, encoding='utf-8')) if os.path.exists(SOUHRN) else {}
    mes = sorted({(m.group(2), m.group(1)) for f in zmenene for m in [re.match(r'^(\d{4}-\d{2})_(.+?)(\.p-[^.]+)?\.json$', f)] if m and not m.group(2).endswith('_qerko')})
    if not mes and not S: return
    radky = []
    for slug, ym in mes:
        days = {}
        for f in os.listdir(d):
            m = re.match(r'^' + re.escape(ym) + '_' + re.escape(slug) + r'(\.p-[^.]+)?\.json$', f)
            if m:
                for den, x in json.load(open(os.path.join(d, f), encoding='utf-8')).get('days', {}).items():
                    days[den] = days.get(den, 0) + sum((x.get('p') or {}).values())
        if not days: continue
        ds = sorted(days); f2 = lambda x: f'{int(x[6:])}. {int(x[4:6])}.'
        tr = f'{round(sum(days.values())):,}'.replace(',', ' ')
        radky.append(f"• {S.get(slug, slug)} – {MESICE[int(ym[5:]) - 1]} {ym[:4]}: data {f2(ds[0])} – {f2(ds[-1])}, tržba za měsíc {tr} Kč")
    ostatni = [t for k, t in S.items() if k not in {s for s, _ in mes}]
    from zoneinfo import ZoneInfo
    ted = datetime.datetime.now(ZoneInfo('Europe/Prague'))
    text = f'Aktualizace reportů {ted.day}. {ted.month}. {ted.year} {ted:%H:%M}\n\n'
    if radky: text += 'Doplněno:\n' + '\n'.join(radky) + '\n\n'
    if ostatni: text += 'Aktualizováno také: ' + ', '.join(ostatni) + '\n\n'
    text += 'Reporty: https://www.onlinereport.cz'
    nazvy = [S.get(s, s) for s, _ in mes] or ostatni
    predmet = 'Reporty aktualizovány: ' + ', '.join(dict.fromkeys(nazvy))
    print(json.dumps({'souhrn_email': posli_email(predmet, text)}, ensure_ascii=False))  # e-mail není podmínkou úspěchu
    try: os.remove(SOUHRN)
    except OSError: pass


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: raise SystemExit(__doc__)
    c = a[0]
    if c == 'info': print(json.dumps(api('info'), ensure_ascii=False, indent=1))
    elif c == 'stahni': stahni(a[1], a[2])
    elif c == 'nahraj': nahraj(a[1], a[2], a[3] if len(a) > 3 else None)
    elif c == 'exporty': exporty(a[1], a[2])
    elif c == 'uklid':  # uklid <data> <in> <jako…>  nebo (starší zápis) uklid <data> <cesta na serveru…>
        if len(a) > 2 and os.path.isdir(a[2]): uklid(a[1], a[2], set(a[3:]))
        else:
            din = 'in' if os.path.isdir('in') else './in'; mp = os.path.join(din, '_exporty.json')
            mapa = json.load(open(mp, encoding='utf-8')) if os.path.exists(mp) else {}
            uklid(a[1], din, {j for f in a[2:] for j in mapa.get(f, {}).get('jako', [])})
    elif c == 'report': report(a[1], a[2], a[3])
    elif c == 'email': print(json.dumps(posli_email(a[1], a[2] if len(a) > 2 else a[1]), ensure_ascii=False))  # email "<předmět>" "<text>" – např. den bez nových dat nebo chyba
    else: raise SystemExit(__doc__)
