import json,re,time,hashlib,html,unicodedata
from difflib import SequenceMatcher
from datetime import datetime,timezone,timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote_plus,urlsplit,urlunsplit,parse_qsl,urlencode

import feedparser,requests,trafilatura
from googlenewsdecoder import gnewsdecoder
from deep_translator import GoogleTranslator
from langdetect import detect as detect_language

ROOT=Path(__file__).resolve().parent
CFG=json.loads((ROOT/"config.json").read_text(encoding="utf-8"))
DATA=ROOT/"data/articles.json"
STATE=ROOT/"data/state.json"
DOCS=ROOT/"docs"
PLAYERDIR=DOCS/"players"

def norm(s):
    return re.sub(r"\s+"," ","".join(c for c in unicodedata.normalize("NFKD",str(s)) if not unicodedata.combining(c)).lower()).strip()

def slug(s):
    return re.sub(r"[^a-z0-9]+","-",norm(s)).strip("-")

def clean_url(u):
    try:
        p=urlsplit(u)
        q=[(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k.lower() not in {"fbclid","gclid","mc_cid","mc_eid"}]
        return urlunsplit((p.scheme,p.netloc,p.path,urlencode(q),""))
    except:return u

def domain(u):
    try:return urlsplit(u).netloc.removeprefix("www.")
    except:return ""

def aliases(name):
    a={name,"".join(c for c in unicodedata.normalize("NFKD",name) if not unicodedata.combining(c))}
    extras={
        "fernando tatis jr.":["Fernando Tatis Jr.","Fernando Tatis"],
        "ronald acuna jr.":["Ronald Acuna Jr.","Ronald Acuna"],
        "vladimir guerrero jr.":["Vladimir Guerrero Jr.","Vlad Guerrero Jr."],
        "julio rodriguez":["Julio Rodriguez"],
        "jose altuve":["Jose Altuve"],
        "jose ramirez":["Jose Ramirez"],
        "luis arraez":["Luis Arraez"],
        "andres gimenez":["Andres Gimenez"],
        "cristopher sanchez":["Cristopher Sanchez"],
        "seiya suzuki":["鈴木誠也"],
        "masataka yoshida":["吉田正尚"],
        "shota imanaga":["今永昇太"],
        "jung hoo lee":["이정후"],
        "ha-seong kim":["김하성","Ha Seong Kim"],
        "andres munoz":["Andres Munoz"],
        "ramon urias":["Ramon Urias"],
        "luis urias":["Luis Urias"],
        "jose trevino":["Jose Trevino"],
        "cesar salazar":["Cesar Salazar"],
        "luis gastelum":["Luis Gastelum"],
        "julio urias":["Julio Urias"],
    }
    a.update(extras.get(norm(name),[]))
    return [x for x in a if x]

def q_for(player):
    ns=aliases(player["name"])
    names="("+" OR ".join(f'"{n}"' for n in ns)+")" if len(ns)>1 else f'"{ns[0]}"'

    team=str(player.get("team") or "").strip()
    level=str(player.get("level") or "").strip()

    context=['MLB','baseball','"Major League Baseball"']
    if team and team.lower()!="free agent":
        context.insert(0,f'"{team}"')
    if level=="MiLB":
        context.extend(['MiLB','"Minor League Baseball"','prospect'])

    return f'{names} ('+" OR ".join(context)+')'

def parse_dt(s):
    for fmt in ("%Y%m%dT%H%M%SZ","%Y%m%d%H%M%S","%Y-%m-%dT%H:%M:%SZ","%Y-%m-%dT%H:%M:%S%z"):
        try:
            d=datetime.strptime(str(s),fmt)
            return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
        except:pass
    return datetime.now(timezone.utc)

def feed_dt(e):
    p=getattr(e,"published_parsed",None) or getattr(e,"updated_parsed",None)
    return datetime(*p[:6],tzinfo=timezone.utc) if p else datetime.now(timezone.utc)

def decode_google(u):
    if "news.google.com" not in u:return clean_url(u)
    try:
        r=gnewsdecoder(u,interval=CFG["settings"].get("google_decode_interval_seconds",0.05))
        return clean_url(r.get("decoded_url")) if isinstance(r,dict) and r.get("status") and r.get("decoded_url") else None
    except:return None

def discover_gdelt(player):
    try:
        r=requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query":q_for(player),
                "mode":"artlist",
                "maxrecords":CFG["settings"]["gdelt_results_per_player"],
                "timespan":f'{CFG["settings"]["max_age_hours"]}h',
                "sort":"datedesc",
                "format":"json"
            },
            timeout=30,
            headers={"User-Agent":"EstrellasRestoMLB/1.0"}
        )
        arr=r.json().get("articles",[])
    except Exception as exc:
        print("GDELT error:",exc);return []
    return [{
        "url":clean_url(x.get("url","")),
        "title":x.get("title",""),
        "source":x.get("domain",""),
        "published":parse_dt(x.get("seendate")),
        "language":x.get("language",""),
        "country":x.get("sourcecountry",""),
        "via":"GDELT",
        "player":player["name"]
    } for x in arr if x.get("url")]

def discover_google(player):
    out=[]
    cutoff=datetime.now(timezone.utc)-timedelta(hours=float(CFG["settings"]["max_age_hours"]))
    q=q_for(player)
    for ed in CFG["google_news_editions"]:
        url=f'https://news.google.com/rss/search?q={quote_plus(q)}&hl={quote_plus(ed["hl"])}&gl={quote_plus(ed["gl"])}&ceid={quote_plus(ed["ceid"])}'
        f=feedparser.parse(url)
        for e in list(getattr(f,"entries",[]))[:CFG["settings"]["google_results_per_edition"]]:
            published=feed_dt(e)
            if published<cutoff:
                continue
            u=decode_google(getattr(e,"link",""))
            if not u:continue
            src=""
            try:src=e.source.get("title","") if getattr(e,"source",None) else ""
            except:pass
            out.append({
                "url":u,"title":getattr(e,"title",""),"source":src or domain(u),
                "published":published,"language":"","country":ed["label"],"via":"Google News",
                "player":player["name"]
            })
    return out

def extract(u):
    try:
        raw=trafilatura.fetch_url(u)
        if not raw:return None
        x=trafilatura.extract(raw,url=u,output_format="json",with_metadata=True,include_comments=False,include_tables=True,favor_precision=True)
        if not x:return None
        d=json.loads(x);body=(d.get("text") or "").strip()
        if not body:return None
        return {"title":(d.get("title") or "").strip(),"author":(d.get("author") or "").strip(),"body":body}
    except:return None

def mentions(text,name):
    n=norm(text)
    return any(norm(a) in n for a in aliases(name))

def baseball_context(text):
    n=norm(text)
    return any(norm(t) in n for t in CFG["baseball_context_terms"])

def chunks(text,n=2200):
    out=[];cur=""
    for para in str(text).split("\n"):
        para=para.strip()
        if not para:continue
        while len(para)>n:
            cut=para.rfind(" ",0,n);cut=cut if cut>n//2 else n
            piece,para=para[:cut],para[cut:]
            if cur:out.append(cur);cur=""
            out.append(piece)
        add=para if not cur else "\n\n"+para
        if len(cur)+len(add)<=n:cur+=add
        else:
            if cur:out.append(cur)
            cur=para
    if cur:out.append(cur)
    return out

def translate(text):
    try:
        tr=GoogleTranslator(source="auto",target="es")
        return "\n\n".join(tr.translate(c) or c for c in chunks(text,CFG["translation"]["chunk_size"])),True
    except Exception as exc:
        print("Translation failed:",exc)
        return text,False

def ensure_translation(a):
    a["original_title"]=a.get("original_title") or a.get("title","")
    a["original_body"]=a.get("original_body") or a.get("body","")
    if a.get("translation_status") in {"translated","already_spanish"} and a.get("rss_title") and a.get("rss_body"):
        return
    try:detected=detect_language(a["original_body"][:1800])
    except:detected=""
    if detected=="es":
        a["rss_title"]=a["original_title"]
        a["rss_body"]=a["original_body"]
        a["translation_status"]="already_spanish"
        a["translated_to"]="es"
        return
    a["rss_title"],ok1=translate(a["original_title"])
    a["rss_body"],ok2=translate(a["original_body"])
    a["translation_status"]="translated" if ok1 and ok2 else "partial_or_fallback"
    a["translated_to"]="es"

def tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+",norm(s)) if len(w)>2}

def sim(a,b):
    return SequenceMatcher(None,norm(a),norm(b)).ratio() if a and b else 0

def overlap(a,b):
    x,y=tokens(a),tokens(b)
    return len(x&y)/len(x|y) if x and y else 0

def art_dt(a):
    try:return datetime.fromisoformat(a["published_iso"]).astimezone(timezone.utc)
    except:return datetime.now(timezone.utc)

def pset(a):
    return {norm(x) for x in a.get("tracked_players",[])}

def duplicate(a,b):
    d=CFG["deduplication"]
    if not (pset(a)&pset(b)):return False
    if abs((art_dt(a)-art_dt(b)).total_seconds())/3600>d["max_hours_apart"]:return False
    ta,tb=a.get("rss_title") or a.get("title",""),b.get("rss_title") or b.get("title","")
    ba,bb=a.get("rss_body") or a.get("body",""),b.get("rss_body") or b.get("body","")
    ts,ov=sim(ta,tb),overlap(ta,tb)
    bs=sim(ba[:d["body_lead_characters"]],bb[:d["body_lead_characters"]])
    return ts>=d["title_similarity_threshold"] or ov>=d["title_token_overlap_threshold"] or (ts>=.5 and bs>=d["body_lead_similarity_threshold"]) or bs>=.82

def score(a):
    s=min(len(a.get("rss_body") or a.get("body","")),25000)
    s+=min(len(a.get("rss_title") or a.get("title","")),180)*2
    if a.get("author"):s+=500
    if a.get("source"):s+=250
    if a.get("translation_status")=="translated":s+=150
    return s

def merge_meta(w,l):
    for k in ("discovery_sources","source_languages","source_countries","tracked_players"):
        w.setdefault(k,[])
        for v in l.get(k,[]):
            if v and v not in w[k]:w[k].append(v)
    w.setdefault("alternate_sources",[])
    info={"source":l.get("source",""),"url":l.get("url",""),"title":l.get("title",""),"body_characters":len(l.get("rss_body") or l.get("body",""))}
    if info["url"] and not any(x.get("url")==info["url"] for x in w["alternate_sources"]):
        w["alternate_sources"].append(info)
    w["duplicate_versions_removed"]=len(w["alternate_sources"])

def dedup(arr):
    kept=[]
    for a in sorted(arr,key=lambda x:x.get("published_iso",""),reverse=True):
        i=next((i for i,b in enumerate(kept) if duplicate(a,b)),None)
        if i is None:kept.append(a)
        elif score(a)>score(kept[i]):merge_meta(a,kept[i]);kept[i]=a
        else:merge_meta(kept[i],a)
    return kept

def src_label(a):
    return (a.get("source") or domain(a.get("url","")) or "Fuente desconocida").strip()

def display_title(a):
    return f'[{src_label(a)}] {a.get("rss_title") or a.get("title","")}'

def cdata(s):
    return "<![CDATA["+str(s).replace("]]>","]]]]><![CDATA[>")+"]]>"

def rss(arr,title,desc):
    items=[]
    for a in sorted(arr,key=lambda x:x.get("published_iso",""),reverse=True)[:CFG["settings"]["max_feed_items"]]:
        body=a.get("rss_body") or a.get("body","")
        body_html="<p>"+html.escape(body).replace("\n\n","</p><p>").replace("\n","<br>")+"</p>"
        cats="\n".join(f"      <category>{html.escape(x)}</category>" for x in a.get("tracked_players",[]))
        creator=f"      <dc:creator>{cdata(a['author'])}</dc:creator>\n" if a.get("author") else ""
        items.append(f"""    <item>
      <title>{cdata(display_title(a))}</title>
      <link>{html.escape(a.get('url',''))}</link>
      <guid isPermaLink="false">{a.get('id','')}</guid>
      <pubDate>{a.get('published_rfc2822','')}</pubDate>
      <source>{cdata(a.get('source',''))}</source>
{creator}      <description>{cdata(body[:500])}</description>
      <content:encoded>{cdata(body_html)}</content:encoded>
{cats}
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
<title>{cdata(title)}</title>
<link>{CFG["feed"]["site_url"]}</link>
<description>{cdata(desc)}</description>
<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
{''.join(items)}
</channel></rss>"""

def generate(arr):
    DOCS.mkdir(exist_ok=True);PLAYERDIR.mkdir(parents=True,exist_ok=True)
    (DOCS/"feed.xml").write_text(rss(arr,CFG["feed"]["title"],CFG["feed"]["description"]),encoding="utf-8")
    for p in CFG["players"]:
        sub=[a for a in arr if p["name"] in a.get("tracked_players",[])]
        (PLAYERDIR/f'{slug(p["name"])}.xml').write_text(rss(sub,f'{p["name"]} — Estrellas Resto MLB',f'Noticias sobre {p["name"]}.'),encoding="utf-8")
    cards="".join(f'<article><h2><a href="{html.escape(a["url"])}">{html.escape(display_title(a))}</a></h2><p>{html.escape(", ".join(a.get("tracked_players",[])))}</p></article>' for a in sorted(arr,key=lambda x:x.get("published_iso",""),reverse=True)[:300])
    (DOCS/"index.html").write_text(f"<!doctype html><html><meta charset='utf-8'><body><h1>Estrellas Resto MLB</h1><p><a href='feed.xml'>RSS general</a></p>{cards}</body></html>",encoding="utf-8")

def load_state():
    if not STATE.exists():return {"next_batch":1}
    try:return json.loads(STATE.read_text(encoding="utf-8"))
    except:return {"next_batch":1}

def save_state(s):
    STATE.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding="utf-8")

def select_batch():
    state=load_state()
    batch=1 if int(state.get("next_batch",1))==1 else 2
    selected=[x for x in CFG["players"] if int(x.get("batch",1))==batch]
    print(f"Batch {batch}: {len(selected)} players")
    print(f"Rolling window: last {CFG['settings']['max_age_hours']} hours")
    return selected,batch,state

def main():
    articles=json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    byurl={a.get("url"):a for a in articles}
    cutoff=datetime.now(timezone.utc)-timedelta(hours=float(CFG["settings"]["max_age_hours"]))
    selected,batch,state=select_batch()
    new_ids=[]

    for p in selected:
        print("SEARCH:",p["id"],p["name"])
        candidates=discover_gdelt(p)+discover_google(p)
        unique={c["url"]:c for c in candidates if c.get("url") and c["published"]>=cutoff}

        for c in sorted(unique.values(),key=lambda x:x["published"],reverse=True):
            if c["url"] in byurl:
                a=byurl[c["url"]]
                if p["name"] not in a.setdefault("tracked_players",[]):a["tracked_players"].append(p["name"])
                continue

            ex=extract(c["url"])
            if not ex or len(ex["body"])<CFG["settings"]["minimum_body_characters"]:continue
            full=ex["title"]+"\n"+ex["body"]
            if not mentions(full,p["name"]) or not baseball_context(full):continue

            tracked=[x["name"] for x in CFG["players"] if mentions(full,x["name"])] or [p["name"]]
            pub=c["published"]
            a={
                "id":hashlib.sha256(c["url"].encode()).hexdigest()[:20],
                "title":ex["title"] or c["title"],
                "source":c["source"] or domain(c["url"]),
                "author":ex["author"],
                "url":c["url"],
                "published_iso":pub.isoformat(),
                "published_rfc2822":format_datetime(pub),
                "body":ex["body"],
                "tracked_players":tracked,
                "primary_search_player":p["name"],
                "primary_search_team":p.get("team",""),
                "primary_search_level":p.get("level",""),
                "source_languages":[c["language"]] if c["language"] else [],
                "source_countries":[c["country"]] if c["country"] else [],
                "discovery_sources":[c["via"]]
            }
            articles.append(a);byurl[a["url"]]=a;new_ids.append(a["id"])

    articles=sorted(articles,key=lambda x:x.get("published_iso",""),reverse=True)[:CFG["settings"]["max_stored_articles"]]
    newset=set(new_ids)
    for a in [x for x in articles if x.get("id") in newset]:
        ensure_translation(a)

    repairs=0
    for a in articles:
        if repairs>=CFG["settings"]["old_translation_repairs_per_run"]:break
        if a.get("id") in newset:continue
        if a.get("translation_status") not in {"translated","already_spanish"}:
            ensure_translation(a);repairs+=1

    articles=dedup(articles)
    DATA.write_text(json.dumps(articles,ensure_ascii=False,indent=2),encoding="utf-8")
    generate(articles)

    state["last_completed_batch"]=batch
    state["last_completed_at"]=datetime.now(timezone.utc).isoformat()
    state["next_batch"]=2 if batch==1 else 1
    save_state(state)

    print("Unique stories:",len(articles))
    print("Next batch:",state["next_batch"])

if __name__=="__main__":
    main()
