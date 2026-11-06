"""Zpracování exportu Loyal Man (Uživatelské transakce, .xlsx) na report kreditu akcionářů.

Volá se ze zpracuj.py pro soubory RRMM_<název>.xlsx, jejichž první buňka je „LOYAL MAN“.
- Stav: <slozka_data>/loyalman_<slug>.json = {"months": [...], "to": "dd.mm.rrrr", "p": [{n, e, m: {RRRR-MM: [počátek, dobití, čerpání, konec, transakce]}}]}
- Počítají se jen transakce z peněženky „Akcionáři“ (lze změnit v <slozka_data>/loyalman.json: {"<slug>": {"penezenka": "..."}}),
  typy: credit = čerpání (C), manual_credit = dobití (D); slevy (discount) se nepočítají.
- Z exportu se berou jen transakce z měsíce v názvu souboru (RRMM); měsíc se přepočítá celý, počáteční stav = konec předchozího měsíce.
"""
import datetime, json, os
from calendar import monthrange


def je_loyalman(path):
    try:
        import openpyxl
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        return str(next(ws.iter_rows(max_row=1, values_only=True))[0] or '').strip().upper() == 'LOYAL MAN'
    except Exception:
        return False


def zpracuj(path, state, slug, yy, mm, tpl, nazev):
    import openpyxl
    cfg_p = os.path.join(state, 'loyalman.json')
    cfg = json.load(open(cfg_p, encoding='utf-8')).get(slug, {}) if os.path.exists(cfg_p) else {}
    penezenka = cfg.get('penezenka', 'Akcionáři')
    ws = openpyxl.load_workbook(path, data_only=True).active
    mesic = f'20{yy}-{mm:02d}'
    R = [r for r in ws.iter_rows(values_only=True)
         if len(r) >= 12 and isinstance(r[11], datetime.datetime) and r[5] == penezenka]  # všechny řádky peněženky (i slevy)
    T = [r for r in R if r[3] in ('credit', 'manual_credit')]
    if not T:
        return None, 'v exportu nejsou žádné transakce peněženky ' + penezenka
    sp = os.path.join(state, f'loyalman_{slug}.json')
    D = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else {'months': [], 'to': '', 'p': []}
    P = {p['e'].lower(): p for p in D['p']}
    for r in R:  # noví akcionáři (i ti bez dobití/čerpání – v seznamu zůstávají s nulovým pohybem)
        e = (r[2] or '').strip()
        if e and e.lower() not in P:
            p = {'n': ' '.join(x for x in (r[1], r[0]) if x).strip() or e, 'e': e, 'm': {}}
            D['p'].append(p); P[e.lower()] = p
    months = sorted(set(D['months']) | {mesic})
    tx = lambda r: [r[11].strftime('%d.%m. %H:%M'), 'D' if r[3] == 'manual_credit' else 'C',
                    (r[8] or '').replace('POKLADNA', '').rstrip(), r[10]]
    V = [r for r in T if r[11].strftime('%Y-%m') == mesic]
    for p in D['p']:
        rs = sorted((r for r in V if (r[2] or '').strip().lower() == p['e'].lower()), key=lambda r: r[11])
        p['m'][mesic] = [0, sum(r[10] for r in rs if r[3] == 'manual_credit'), sum(r[10] for r in rs if r[3] == 'credit'), 0, [tx(r) for r in rs]]
        konec = p['m'].get(months[0], [0])[0]  # přepočet řetězce počátek/konec; počáteční stav prvního měsíce zůstává
        for m in months:
            x = p['m'].setdefault(m, [0, 0, 0, 0, []])
            x[0] = konec; x[3] = round(x[0] + x[1] + x[2], 2); konec = x[3]
    D['months'] = months
    if mesic == months[-1]:
        posl = max(r[11] for r in T).date()
        D['to'] = min(posl, datetime.date(2000 + int(yy), mm, monthrange(2000 + int(yy), mm)[1])).strftime('%d.%m.%Y')
    json.dump(D, open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
    html = tpl.replace('/*TITLE*/', nazev).replace('/*DATA*/', json.dumps(D, ensure_ascii=False, separators=(',', ':')))
    S = [sum(p['m'][mesic][k] for p in D['p']) for k in range(4)]
    od = V and min(r[11] for r in V).strftime('%Y%m%d') or f'20{yy}{mm:02d}01'
    souhrn = {'typ': 'loyalman', 'od': od, 'do': datetime.datetime.strptime(D['to'], '%d.%m.%Y').strftime('%Y%m%d') if mesic == months[-1] else f'20{yy}{mm:02d}{monthrange(2000 + int(yy), mm)[1]}',
              'pocatecni_stav': round(S[0]), 'dobiti': round(S[1]), 'cerpani': round(S[2]), 'konecny_stav': round(S[3]),
              'aktivni': sum(1 for p in D['p'] if p['m'][mesic][4]), 'akcionaru': len(D['p'])}
    return (html, souhrn), None
