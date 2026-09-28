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
RUN_HISTORY=ROOT/"data/run_history.json"
DOCS=ROOT/"docs"
PLAYERDIR=DOCS/"players"
TOPICDIR=DOCS/"topics"

def norm(s):
    return re.sub(r"\s+"," ","".join(
        c for c in unicodedata.normalize("NFKD",str(s))
        if not unicodedata.combining(c)
    ).lower()).strip()

def slug(s):
    return re.sub(r"[^a-z0-9]+","-",norm(s)).strip("-") or "feed"

def clean_url(u):
    try:
        p=urlsplit(u)
        q=[(k,v) for k,v in parse_qsl(p.query)
           if not k.lower().startswith("utm_")
           and k.lower() not in {"fbclid","gclid","mc_cid","mc_eid"}]
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

def target_label(target):
    return target["name"] if target["type"]=="player" else target["keyword"]

def q_for(target):
    if target["type"]=="player":
        ns=aliases(target["name"])
        names="("+" OR ".join(f'"{n}"' for n in ns)+")" if len(ns)>1 else f'"{ns[0]}"'
        team=str(target.get("team") or "").strip()
        level=str(target.get("level") or "").strip()
        context=['MLB','baseball','"Major League Baseball"']
        if team and team.lower()!="free agent":
            context.insert(0,f'"{team}"')
        if level=="MiLB":
            context.extend(['MiLB','"Minor League Baseball"','prospect'])
        return f'{names} ('+" OR ".join(context)+')'

    # Postseason topic search.
    kw=target["keyword"]
    return f'({kw}) (MLB OR baseball OR postseason OR playoffs OR "World Series")'

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

def discover_gdelt(target):
    try:
        r=requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query":q_for(target),
                "mode":"artlist",
                "maxrecords":CFG["settings"].get("gdelt_results_per_target",10),
                "timespan":f'{CFG["settings"]["max_age_hours"]}h',
                "sort":"datedesc",
                "format":"json"
            },
            timeout=30,
            headers={"User-Agent":"EstrellasMLB/2.0"}
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
        "target_type":target["type"],
        "target_label":target_label(target),
    } for x in arr if x.get("url")]

def discover_google(target):
    out=[]
    cutoff=datetime.now(timezone.utc)-timedelta(hours=float(CFG["settings"]["max_age_hours"]))
    q=q_for(target)
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
                "target_type":target["type"],"target_label":target_label(target)
            })
    return out

def extract(u):
    try:
        raw=trafilatura.fetch_url(u)
        if not raw:return None
        x=trafilatura.extract(
            raw,url=u,output_format="json",with_metadata=True,
            include_comments=False,include_tables=True,favor_precision=True
        )
        if not x:return None
        d=json.loads(x);body=(d.get("text") or "").strip()
        if not body:return None
        return {"title":(d.get("title") or "").strip(),"author":(d.get("author") or "").strip(),"body":body}
    except:return None

def mentions(text,name):
    n=norm(text)
    return any(norm(a) in n for a in aliases(name))

TOPIC_STOP={
    "the","a","an","and","or","of","to","for","in","on","vs",
    "mlb","2026"
}

def topic_matches(text,keyword):
    n=norm(text)
    k=norm(keyword)
    if k in n:return True

    words=[w for w in re.findall(r"[a-z0-9]+",k) if len(w)>2 and w not in TOPIC_STOP]
    if not words:return False

    hits=sum(1 for w in words if w in n)
    required=max(2,(len(words)+1)//2)
    return hits>=required

def baseball_context(text):
    n=norm(text)
    return any(norm(t) in n for t in CFG["baseball_context_terms"])

def target_matches(text,target):
    if target["type"]=="player":
        return mentions(text,target["name"]) and baseball_context(text)
    return topic_matches(text,target["keyword"]) and baseball_context(text)

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

def identity_tags(a):
    return (
        {f"p:{norm(x)}" for x in a.get("tracked_players",[])}
        | {f"t:{norm(x)}" for x in a.get("matched_topics",[])}
    )

def duplicate(a,b):
    d=CFG["deduplication"]
    if abs((art_dt(a)-art_dt(b)).total_seconds())/3600>d["max_hours_apart"]:
        return False

    ta,tb=a.get("rss_title") or a.get("title",""),b.get("rss_title") or b.get("title","")
    ba,bb=a.get("rss_body") or a.get("body",""),b.get("rss_body") or b.get("body","")
    ts,ov=sim(ta,tb),overlap(ta,tb)
    bs=sim(ba[:d["body_lead_characters"]],bb[:d["body_lead_characters"]])

    shared=identity_tags(a)&identity_tags(b)
    if shared:
        return (
            ts>=d["title_similarity_threshold"]
            or ov>=d["title_token_overlap_threshold"]
            or (ts>=.5 and bs>=d["body_lead_similarity_threshold"])
            or bs>=.82
        )

    # Very strong title/body match can still merge the same story found via different targets.
    return ts>=.91 or (ts>=.78 and bs>=.84)

def score(a):
    s=min(len(a.get("rss_body") or a.get("body","")),25000)
    s+=min(len(a.get("rss_title") or a.get("title","")),180)*2
    if a.get("author"):s+=500
    if a.get("source"):s+=250
    if a.get("translation_status")=="translated":s+=150
    return s

def merge_meta(w,l):
    for k in ("discovery_sources","source_languages","source_countries","tracked_players","matched_topics"):
        w.setdefault(k,[])
        for v in l.get(k,[]):
            if v and v not in w[k]:w[k].append(v)

    w.setdefault("alternate_sources",[])
    info={
        "source":l.get("source",""),
        "url":l.get("url",""),
        "title":l.get("title",""),
        "body_characters":len(l.get("rss_body") or l.get("body",""))
    }
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
    ordered=sorted(arr,key=lambda x:x.get("published_iso",""),reverse=True)[:CFG["settings"]["max_feed_items"]]

    for a in ordered:
        body=a.get("rss_body") or a.get("body","")
        body_html="<p>"+html.escape(body).replace("\n\n","</p><p>").replace("\n","<br>")+"</p>"
        labels=a.get("tracked_players",[])+a.get("matched_topics",[])
        cats="\n".join(f"      <category>{html.escape(x)}</category>" for x in labels)
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

    if ordered:
        build_date=ordered[0].get("published_rfc2822") or format_datetime(art_dt(ordered[0]))
    else:
        build_date="Thu, 01 Jan 1970 00:00:00 +0000"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
<title>{cdata(title)}</title>
<link>{CFG["feed"]["site_url"]}</link>
<description>{cdata(desc)}</description>
<lastBuildDate>{build_date}</lastBuildDate>
{''.join(items)}
</channel></rss>"""

def write_if_changed(path,content):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8")==content:
        return False
    path.write_text(content,encoding="utf-8")
    return True

def run_stats_html(history):
    rows=[]
    for r in reversed(history[-5:]):
        rows.append(
            "<tr>"
            f"<td>{html.escape(r.get('completed_at',''))}</td>"
            f"<td>{r.get('batch','')}</td>"
            f"<td>{r.get('searches_run','')}</td>"
            f"<td><strong>{r.get('articles_added',0)}</strong></td>"
            f"<td>{r.get('accepted_before_dedup',0)}</td>"
            f"<td>{r.get('total_articles',0)}</td>"
            "</tr>"
        )

    total_added=sum(int(r.get("articles_added",0)) for r in history[-5:])
    last_run=history[-1] if history else {}

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estrellas MLB — Last 5 Runs</title>
<style>
body{{font-family:Arial,sans-serif;max-width:1000px;margin:40px auto;padding:0 18px;background:#f6f7f9;color:#171717}}
.card{{background:white;border-radius:12px;padding:22px;margin-bottom:18px;box-shadow:0 2px 10px rgba(0,0,0,.07)}}
.big{{font-size:42px;font-weight:700;margin:4px 0}}
table{{width:100%;border-collapse:collapse;background:white}}
th,td{{padding:12px;border-bottom:1px solid #ddd;text-align:left}}
th{{background:#f0f2f5}}
.small{{color:#666}}
</style>
</head>
<body>
<h1>Estrellas MLB y Mexicanos — Run Stats</h1>
<div class="card">
<div class="small">Articles added in the last 5 runs</div>
<div class="big">{total_added}</div>
<div>Latest run: Batch {last_run.get('batch','—')} · {last_run.get('articles_added',0)} articles added</div>
</div>
<div class="card">
<table>
<thead><tr>
<th>Completed at (UTC)</th>
<th>Batch</th>
<th>Searches</th>
<th>Articles added</th>
<th>Accepted before dedup</th>
<th>Total stored</th>
</tr></thead>
<tbody>
{''.join(rows) if rows else '<tr><td colspan="6">No completed runs recorded yet.</td></tr>'}
</tbody>
</table>
</div>
<p class="small">This page is regenerated automatically after every successful scanner run.</p>
</body>
</html>"""

def generate(arr,history):
    DOCS.mkdir(exist_ok=True)
    PLAYERDIR.mkdir(parents=True,exist_ok=True)
    TOPICDIR.mkdir(parents=True,exist_ok=True)

    changed=0
    changed+=write_if_changed(
        DOCS/"feed.xml",
        rss(arr,CFG["feed"]["title"],CFG["feed"]["description"])
    )

    for p in CFG["players"]:
        sub=[a for a in arr if p["name"] in a.get("tracked_players",[])]
        changed+=write_if_changed(
            PLAYERDIR/f'{slug(p["name"])}.xml',
            rss(sub,f'{p["name"]} — Estrellas MLB',f'Noticias sobre {p["name"]}.')
        )

    for t in CFG.get("topics",[]):
        sub=[a for a in arr if t["keyword"] in a.get("matched_topics",[])]
        changed+=write_if_changed(
            TOPICDIR/f'{slug(t["keyword"])}.xml',
            rss(sub,f'{t["keyword"]} — Estrellas MLB',f'Noticias sobre {t["keyword"]}.')
        )

    cards=[]
    for a in sorted(arr,key=lambda x:x.get("published_iso",""),reverse=True)[:300]:
        tags=a.get("tracked_players",[])+a.get("matched_topics",[])
        cards.append(
            f'<article><h2><a href="{html.escape(a.get("url",""))}">{html.escape(display_title(a))}</a></h2>'
            f'<p>{html.escape(", ".join(tags[:8]))}</p></article>'
        )

    index_html=(
        "<!doctype html><html><meta charset='utf-8'><body>"
        "<h1>Estrellas MLB y Mexicanos</h1>"
        "<p><a href='feed.xml'>RSS general</a> · "
        "<a href='run-stats.html'>Stats últimos 5 runs</a></p>"
        + "".join(cards)
        + "</body></html>"
    )
    changed+=write_if_changed(DOCS/"index.html",index_html)
    changed+=write_if_changed(DOCS/"run-stats.html",run_stats_html(history))

    print("Generated files changed:",changed)

def load_state():
    if not STATE.exists():return {"next_batch":1}
    try:return json.loads(STATE.read_text(encoding="utf-8"))
    except:return {"next_batch":1}

def save_state(s):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding="utf-8")

def load_run_history():
    if not RUN_HISTORY.exists():return []
    try:
        data=json.loads(RUN_HISTORY.read_text(encoding="utf-8"))
        return data if isinstance(data,list) else []
    except:return []

def save_run_history(history):
    RUN_HISTORY.parent.mkdir(parents=True,exist_ok=True)
    RUN_HISTORY.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding="utf-8")

def all_targets():
    out=[]
    for p in CFG["players"]:
        x=dict(p);x["type"]="player";out.append(x)
    for t in CFG.get("topics",[]):
        x=dict(t);x["type"]="topic";out.append(x)
    return out

def select_batch():
    state=load_state()
    try:batch=int(state.get("next_batch",1))
    except:batch=1
    if batch not in {1,2,3}:batch=1

    selected=[x for x in all_targets() if int(x.get("batch",1))==batch]
    print(f"Batch {batch}: {len(selected)} searches")
    print(f"Rolling window: last {CFG['settings']['max_age_hours']} hours")
    return selected,batch,state

def main():
    articles=json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    byurl={a.get("url"):a for a in articles if a.get("url")}
    cutoff=datetime.now(timezone.utc)-timedelta(hours=float(CFG["settings"]["max_age_hours"]))
    selected,batch,state=select_batch()
    new_ids=[]

    for target in selected:
        label=target_label(target)
        print("SEARCH:",target["type"],label)

        candidates=discover_gdelt(target)+discover_google(target)
        unique={c["url"]:c for c in candidates if c.get("url") and c["published"]>=cutoff}

        for c in sorted(unique.values(),key=lambda x:x["published"],reverse=True):
            if c["url"] in byurl:
                a=byurl[c["url"]]
                if target["type"]=="player":
                    if label not in a.setdefault("tracked_players",[]):
                        a["tracked_players"].append(label)
                else:
                    if label not in a.setdefault("matched_topics",[]):
                        a["matched_topics"].append(label)
                if c.get("via") and c["via"] not in a.setdefault("discovery_sources",[]):
                    a["discovery_sources"].append(c["via"])
                continue

            ex=extract(c["url"])
            if not ex or len(ex["body"])<CFG["settings"]["minimum_body_characters"]:
                continue

            full=ex["title"]+"\n"+ex["body"]
            if not target_matches(full,target):
                continue

            tracked=[x["name"] for x in CFG["players"] if mentions(full,x["name"])]
            matched_topics=[x["keyword"] for x in CFG.get("topics",[]) if topic_matches(full,x["keyword"])]

            if target["type"]=="player" and label not in tracked:
                tracked.append(label)
            if target["type"]=="topic" and label not in matched_topics:
                matched_topics.append(label)

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
                "matched_topics":matched_topics,
                "primary_search_target":label,
                "primary_search_type":target["type"],
                "primary_search_category":target.get("category",""),
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

    newset=set(new_ids)

    for a in [x for x in articles if x.get("id") in newset]:
        ensure_translation(a)

    repairs=0
    for a in articles:
        if repairs>=CFG["settings"]["old_translation_repairs_per_run"]:break
        if a.get("id") in newset:continue
        if a.get("translation_status") not in {"translated","already_spanish"}:
            ensure_translation(a);repairs+=1

    accepted_before_dedup=len(new_ids)
    articles=dedup(articles)
    articles_added=sum(1 for a in articles if a.get("id") in newset)

    DATA.write_text(json.dumps(articles,ensure_ascii=False,indent=2),encoding="utf-8")

    next_batch=1 if batch==3 else batch+1
    state["last_completed_batch"]=batch
    state["last_completed_at"]=datetime.now(timezone.utc).isoformat()
    state["next_batch"]=next_batch

    history=load_run_history()
    history.append({
        "completed_at":state["last_completed_at"],
        "batch":batch,
        "searches_run":len(selected),
        "accepted_before_dedup":accepted_before_dedup,
        "articles_added":articles_added,
        "total_articles":len(articles),
        "next_batch":next_batch,
    })
    history=history[-int(CFG["settings"].get("run_history_length",5)):]

    generate(articles,history)
    save_state(state)
    save_run_history(history)

    print("Accepted before dedup:",accepted_before_dedup)
    print("Articles added after dedup:",articles_added)
    print("Unique stories:",len(articles))
    print("Next batch:",next_batch)

if __name__=="__main__":
    main()
