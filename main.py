import json,re,time,hashlib,html,unicodedata
from difflib import SequenceMatcher
from datetime import datetime,timezone,timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote_plus,urlsplit,urlunsplit,parse_qsl,urlencode
import feedparser,requests,trafilatura
from googlenewsdecoder import gnewsdecoder
from deep_translator import GoogleTranslator

ROOT=Path(__file__).resolve().parent
CFG=json.loads((ROOT/"config.json").read_text(encoding="utf-8"))
DATA=ROOT/"data/articles.json"; STATE=ROOT/"data/state.json"; DOCS=ROOT/"docs"; PEOPLE=DOCS/"people"

def norm(s): return re.sub(r"\s+"," ","".join(c for c in unicodedata.normalize("NFKD",str(s)) if not unicodedata.combining(c)).lower()).strip()
def slug(s): return re.sub(r"[^a-z0-9]+","-",norm(s)).strip("-")
def clean_url(u):
    try:
        p=urlsplit(u); q=[(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k.lower() not in {"fbclid","gclid"}]
        return urlunsplit((p.scheme,p.netloc,p.path,urlencode(q),""))
    except:return u
def domain(u):
    try:return urlsplit(u).netloc.removeprefix("www.")
    except:return ""
def aliases(name):
    a={name,"".join(c for c in unicodedata.normalize("NFKD",name) if not unicodedata.combining(c))}
    extras={"hideki matsui":["松井秀喜"],"jasson dominguez":["Jasson Dominguez"],"carlos rodon":["Carlos Rodon"],"ali sanchez":["Ali Sanchez"],"luis garcia jr.":["Luis Garcia Jr."]}
    a.update(extras.get(norm(name),[])); return [x for x in a if x]
def q_for(p):
    ns=aliases(p["name"]); np="("+" OR ".join(f'"{n}"' for n in ns)+")" if len(ns)>1 else f'"{ns[0]}"'
    return f'{np} (Yankees OR "New York Yankees" OR MLB)'
def parse_dt(s):
    for f in ("%Y%m%dT%H%M%SZ","%Y%m%d%H%M%S","%Y-%m-%dT%H:%M:%SZ"):
        try:return datetime.strptime(str(s),f).replace(tzinfo=timezone.utc)
        except:pass
    return datetime.now(timezone.utc)
def feed_dt(e):
    p=getattr(e,"published_parsed",None) or getattr(e,"updated_parsed",None)
    return datetime(*p[:6],tzinfo=timezone.utc) if p else datetime.now(timezone.utc)
def decode_google(u):
    if "news.google.com" not in u:return clean_url(u)
    try:
        r=gnewsdecoder(u,interval=CFG["settings"].get("google_decode_interval_seconds",0.05))
        return clean_url(r.get("decoded_url")) if isinstance(r,dict) and r.get("status") else None
    except:return None
def gdelt(p):
    try:
        r=requests.get("https://api.gdeltproject.org/api/v2/doc/doc",params={"query":q_for(p),"mode":"artlist","maxrecords":CFG["settings"]["gdelt_results_per_person"],"timespan":f'{CFG["settings"]["max_age_hours"]}h',"sort":"datedesc","format":"json"},timeout=30)
        arr=r.json().get("articles",[])
    except:return []
    return [{"url":clean_url(x.get("url","")),"title":x.get("title",""),"source":x.get("domain",""),"published":parse_dt(x.get("seendate")),"language":x.get("language",""),"country":x.get("sourcecountry",""),"via":"GDELT"} for x in arr if x.get("url")]
def google(p):
    out=[]
    cutoff=datetime.now(timezone.utc)-timedelta(hours=float(CFG["settings"].get("max_age_hours",2)))

    for ed in CFG["google_news_editions"]:
        url=f'https://news.google.com/rss/search?q={quote_plus(q_for(p))}&hl={quote_plus(ed["hl"])}&gl={quote_plus(ed["gl"])}&ceid={quote_plus(ed["ceid"])}'
        f=feedparser.parse(url)

        for e in list(getattr(f,"entries",[]))[:CFG["settings"]["google_results_per_edition"]]:
            published=feed_dt(e)

            # Cheap age filter BEFORE Google URL decoding.
            if published < cutoff:
                continue

            u=decode_google(getattr(e,"link",""))
            if not u:
                continue

            src=""
            try:
                src=e.source.get("title","") if getattr(e,"source",None) else ""
            except:
                pass

            out.append({
                "url":u,
                "title":getattr(e,"title",""),
                "source":src or domain(u),
                "published":published,
                "language":"",
                "country":ed["label"],
                "via":"Google News"
            })
    return out

def extract(u):
    try:
        raw=trafilatura.fetch_url(u)
        if not raw:return None
        x=trafilatura.extract(raw,url=u,output_format="json",with_metadata=True,include_comments=False,include_tables=True,favor_precision=True)
        if not x:return None
        d=json.loads(x); body=(d.get("text") or "").strip()
        return {"title":(d.get("title") or "").strip(),"author":(d.get("author") or "").strip(),"body":body} if body else None
    except:return None
def mentions(text,name): return any(norm(a) in norm(text) for a in aliases(name))
def has_context(text): return any(norm(t) in norm(text) for t in CFG["context_terms"])
def chunks(text,n=4000):
    out=[]; cur=""
    for para in str(text).split("\n"):
        para=para.strip()
        if not para:continue
        while len(para)>n:
            cut=para.rfind(" ",0,n); cut=cut if cut>n//2 else n
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
    except:return text,False
def ensure_translation(a):
    if a.get("rss_title") and a.get("rss_body") and a.get("translation_status")=="translated":return
    a["original_title"]=a.get("original_title") or a.get("title",""); a["original_body"]=a.get("original_body") or a.get("body","")
    a["rss_title"],ok1=translate(a["original_title"]); a["rss_body"],ok2=translate(a["original_body"])
    a["translation_status"]="translated" if ok1 and ok2 else "fallback_original"
def tokens(s): return {w for w in re.findall(r"[a-z0-9]+",norm(s)) if len(w)>2}
def sim(a,b): return SequenceMatcher(None,norm(a),norm(b)).ratio() if a and b else 0
def overlap(a,b):
    x,y=tokens(a),tokens(b); return len(x&y)/len(x|y) if x and y else 0
def dt(a):
    try:return datetime.fromisoformat(a["published_iso"]).astimezone(timezone.utc)
    except:return datetime.now(timezone.utc)
def pset(a): return {norm(x["name"]) for x in a.get("tracked_people",[])}
def dup(a,b):
    d=CFG["deduplication"]
    if not (pset(a)&pset(b)):return False
    if abs((dt(a)-dt(b)).total_seconds())/3600>d["max_hours_apart"]:return False
    ta,tb=a.get("rss_title") or a.get("title",""),b.get("rss_title") or b.get("title",""); ba,bb=a.get("rss_body") or a.get("body",""),b.get("rss_body") or b.get("body","")
    ts,ov=sim(ta,tb),overlap(ta,tb); bs=sim(ba[:d["body_lead_characters"]],bb[:d["body_lead_characters"]])
    return ts>=d["title_similarity_threshold"] or ov>=d["title_token_overlap_threshold"] or (ts>=.5 and bs>=d["body_lead_similarity_threshold"]) or bs>=.82
def score(a):
    s=min(len(a.get("rss_body","")),25000)+min(len(a.get("rss_title","")),180)*2
    if a.get("author"):s+=500
    if a.get("source"):s+=250
    if a.get("translation_status")=="translated":s+=150
    return s
def merge_meta(w,l):
    for k in ("discovery_sources","source_languages","source_countries"):
        w.setdefault(k,[])
        for v in l.get(k,[]):
            if v and v not in w[k]:w[k].append(v)
    w.setdefault("alternate_sources",[])
    info={"source":l.get("source",""),"url":l.get("url",""),"title":l.get("title",""),"body_characters":len(l.get("rss_body",""))}
    if info["url"] and not any(x.get("url")==info["url"] for x in w["alternate_sources"]):w["alternate_sources"].append(info)
def dedup(arr):
    kept=[]
    for a in sorted(arr,key=lambda x:x.get("published_iso",""),reverse=True):
        i=next((i for i,b in enumerate(kept) if dup(a,b)),None)
        if i is None:kept.append(a)
        elif score(a)>score(kept[i]):merge_meta(a,kept[i]);kept[i]=a
        else:merge_meta(kept[i],a)
    return kept
def src_label(a): return (a.get("source") or domain(a.get("url","")) or "Fuente desconocida").strip()
def display_title(a): return f'[{src_label(a)}] {a.get("rss_title") or a.get("title","")}'
def cdata(s): return "<![CDATA["+str(s).replace("]]>","]]]]><![CDATA[>")+"]]>"
def rss(arr,title,desc):
    items=[]
    for a in sorted(arr,key=lambda x:x.get("published_iso",""),reverse=True)[:CFG["settings"]["max_feed_items"]]:
        body=a.get("rss_body") or a.get("body",""); body_html="<p>"+html.escape(body).replace("\n\n","</p><p>").replace("\n","<br>")+"</p>"
        cats="\n".join(f"      <category>{html.escape(p['name'])}</category>" for p in a.get("tracked_people",[]))
        creator=f"      <dc:creator>{cdata(a['author'])}</dc:creator>\n" if a.get("author") else ""
        items.append(f"""    <item><title>{cdata(display_title(a))}</title><link>{html.escape(a['url'])}</link>
      <guid isPermaLink="false">{a['id']}</guid><pubDate>{a['published_rfc2822']}</pubDate><source>{cdata(a.get('source',''))}</source>
{creator}      <description>{cdata(body[:500])}</description><content:encoded>{cdata(body_html)}</content:encoded>{cats}</item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>{cdata(title)}</title><link>{CFG["feed"]["site_url"]}</link><description>{cdata(desc)}</description><lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>{''.join(items)}</channel></rss>"""
def generate(arr):
    DOCS.mkdir(exist_ok=True); PEOPLE.mkdir(parents=True,exist_ok=True)
    (DOCS/"feed.xml").write_text(rss(arr,CFG["feed"]["title"],CFG["feed"]["description"]),encoding="utf-8")
    for p in CFG["people"]:
        sub=[a for a in arr if any(x["name"]==p["name"] for x in a.get("tracked_people",[]))]
        (PEOPLE/f'{slug(p["name"])}.xml').write_text(rss(sub,f'{p["name"]} — Yankees News',f'Noticias sobre {p["name"]}.'),encoding="utf-8")
    cards="".join(f'<article><h2><a href="{html.escape(a["url"])}">{html.escape(display_title(a))}</a></h2><p>{html.escape(", ".join(x["name"] for x in a.get("tracked_people",[])))}</p></article>' for a in sorted(arr,key=lambda x:x.get("published_iso",""),reverse=True)[:250])
    (DOCS/"index.html").write_text(f"<!doctype html><html><meta charset='utf-8'><body><h1>Yankees News</h1><p><a href='feed.xml'>RSS general</a></p>{cards}</body></html>",encoding="utf-8")
def load_state():
    if not STATE.exists():
        return {"next_batch":1}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except:
        return {"next_batch":1}

def save_state(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")

def select_batch():
    state=load_state()
    batch=1 if int(state.get("next_batch",1))==1 else 2
    selected=[p for p in CFG["people"] if int(p.get("batch",1))==batch]

    print(f"Yankees batch {batch}: {len(selected)} names")
    print(f"Rolling window: last {CFG['settings'].get('max_age_hours',2)} hours")
    return selected,batch,state

def main():
    articles=json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    byurl={a.get("url"):a for a in articles}

    cutoff=datetime.now(timezone.utc)-timedelta(
        hours=float(CFG["settings"].get("max_age_hours",2))
    )

    selected,batch,state=select_batch()
    new_ids=[]

    for p in selected:
        print("TRACK:",p["id"],p["name"])

        candidates=gdelt(p)+google(p)
        unique={
            c["url"]:c
            for c in candidates
            if c.get("url") and c["published"]>=cutoff
        }

        for c in sorted(unique.values(),key=lambda x:x["published"],reverse=True):
            if c["url"] in byurl:
                a=byurl[c["url"]]
                if p["name"] not in {x["name"] for x in a.get("tracked_people",[])}:
                    a.setdefault("tracked_people",[]).append(p)
                continue

            ex=extract(c["url"])
            if not ex or len(ex["body"])<CFG["settings"]["minimum_body_characters"]:
                continue

            text=ex["title"]+"\n"+ex["body"]
            if not mentions(text,p["name"]) or not has_context(text):
                continue

            tracked=[x for x in CFG["people"] if mentions(text,x["name"])] or [p]
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
                "tracked_people":tracked,
                "source_languages":[c["language"]] if c["language"] else [],
                "source_countries":[c["country"]] if c["country"] else [],
                "discovery_sources":[c["via"]]
            }

            articles.append(a)
            byurl[a["url"]]=a
            new_ids.append(a["id"])

    articles=sorted(
        articles,
        key=lambda x:x.get("published_iso",""),
        reverse=True
    )[:CFG["settings"]["max_stored_articles"]]

    # Translate every newly accepted story.
    new_set=set(new_ids)
    new_articles=[a for a in articles if a.get("id") in new_set]

    print("New accepted articles:",len(new_articles))
    for a in new_articles:
        ensure_translation(a)

    # Repair only a small number of old failed translations per run.
    repair_limit=int(CFG["settings"].get("old_translation_repairs_per_run",10))
    repairs=0

    for a in articles:
        if repairs>=repair_limit:
            break
        if a.get("id") in new_set:
            continue
        if a.get("translation_status")!="translated":
            ensure_translation(a)
            repairs+=1

    print(f"Old translation repairs: {repairs}/{repair_limit}")

    articles=dedup(articles)

    DATA.write_text(
        json.dumps(articles,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )
    generate(articles)

    # Advance the batch only after a successful run.
    state["last_completed_batch"]=batch
    state["last_completed_at"]=datetime.now(timezone.utc).isoformat()
    state["next_batch"]=2 if batch==1 else 1
    save_state(state)

    print("Unique stories:",len(articles))
    print("Next Yankees batch:",state["next_batch"])

if __name__=="__main__":main()
