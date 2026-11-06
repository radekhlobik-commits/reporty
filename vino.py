"""Dashboard Měsíční prodej vína: python3 vino.py <slozka_data> <vystup.html> [datum]"""
import json,glob,os,re,sys,datetime
R={'dockhouse':'DockHouse','mazel-vinohrady':'Mazel Vinohrady','v-ep':'Výčep','karl-nsk-holka':'Karlínská holka','meatbeer':'Meatbeer'}
def main(state,out,asof=None):
    M=[];I=[]
    for slug,name in R.items():
        for f in sorted(glob.glob(os.path.join(state,f'20[0-9][0-9]-[0-9][0-9]_{slug}.json'))):
            mon=os.path.basename(f)[:7]; d=json.load(open(f,encoding='utf-8'))
            wine={i for i,g in d['grp'].items() if g.strip().lower()=='vino'}; agg={}
            for x in d['days'].values():
                for i,(q,v) in x['i'].items():
                    if i in wine: a=agg.setdefault(i,[0,0]); a[0]+=q; a[1]+=v
            for i,(q,v) in agg.items():
                n=d['names'].get(i,i); I.append([name,mon,re.sub(r'^[.\s]+','',n[:27].strip()),n[27:].strip(),round(q,2),round(v,2)])
            M.append([name,mon,len(d['days']),round(sum(v for q,v in agg.values()),2),round(sum(sum(x['p'].values()) for x in d['days'].values()),2)])
    tpl=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'sablona_vino.html'),encoding='utf-8').read()
    asof=asof or datetime.date.today().strftime('%-d. %-m. %Y')
    open(out,'w',encoding='utf-8').write(tpl.replace('/*DATA*/',json.dumps({'m':M,'i':I},ensure_ascii=False,separators=(',',':'))).replace('/*ASOF*/',asof))
    print(json.dumps({'klic':'prodej-vina','nazev':'Měsíční prodej vína','html':out,'mesicu':len(M),'polozek':len(I)},ensure_ascii=False))
if __name__=='__main__': main(*sys.argv[1:4])
