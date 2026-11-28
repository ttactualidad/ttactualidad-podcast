"""Rolling public podcast: the public V1 RSS authorizes every new episode.
The public GitHub snapshot only enriches the matching published revision.
No D1 queries, Windows actions, publication in X, or private credentials.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime, format_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from build_podcast import GUID, ORIGIN, MAX_EPISODES, MAX_BYTES

BASE = "https://ttactualidad.github.io/ttactualidad-podcast/"
RSS = ORIGIN+"/rss/tendencias.xml"
DOSSIERS = "https://raw.githubusercontent.com/fabricelop/europapress-rss/feature/ttactualidad-v2-clean/v2/ttendencias/state/web-top30.json"
MADRID = ZoneInfo("Europe/Madrid")
DAY_CAP = 60
RUN_CAP = 10
CACHE = Path("audio-cache")
SOURCE = Path("rolling")

class Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]
        self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style"): self.hidden+=1
        if tag in ("p","br","li","div","h1","h2","h3"): self.parts.append(" ")
    def handle_endtag(self,tag):
        if tag in ("script","style"): self.hidden=max(0,self.hidden-1)
        if tag in ("p","li","div","h1","h2","h3"): self.parts.append(" ")
    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)

def clean(text):
    p=Cleaner()
    p.feed(text or "")
    p.close()
    return re.sub(r"\s+"," ",re.sub(r"https?://\S+"," ","".join(p.parts))).strip()

def normalized(text):
    return " ".join(re.findall(r"\w+",unicodedata.normalize("NFKC",text).casefold()))

def heading(line):
    return re.sub(r"^[\s#>*_\-]+|[\s*_:]+$","",line).casefold()

def is_research_process_comment(text):
    """Do not narrate internal search diagnostics as news."""
    return bool(re.search(
        r"\b(?:X\s+(?:result[oó]|est[aá]|fue)\s+inaccesible"
        r"|b[uú]squedas?\s+(?:directas?\s+|externas?\s+|realizadas?\s+)*"
        r"(?:en\s+X\s+)?(?:no\s+(?:permitieron|arrojaron)|devolvi[oó]|devolvieron)"
        r"\s+(?:un\s+error|error|resultados?\s+verificables?|recuperar|localizar|consultar)"
        r"|(?:no\s+se\s+ha(?:n)?\s+podido|no\s+hemos\s+podido|no\s+he\s+podido|no\s+fue\s+posible)"
        r"\s+(?:consultar|acceder|localizar|recuperar|leer|verificar|comprobar|determinar)"
        r"\b[^.!?\n]{0,180}\b(?:X\b|publicaciones\b|posts\b|b[uú]squedas\b|volumen de menciones\b)"
        r"|(?:tras|despu[eé]s de)\s+(?:realizar|hacer|efectuar)\s+(?:varias\s+)?b[uú]squedas)\b",
        text, re.I))


def clean_supplemental_narrative(text):
    """Remove only unambiguous method comments, preserving factual caveats."""
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])", text):
        if not is_research_process_comment(sentence):
            kept.append(sentence)
            continue
        suffix = re.search(
            r",\s*aunque\s+(?:no se ha podido|no hemos podido|no fue posible)\s+"
            r"(?:determinar|consultar|verificar|comprobar|localizar|recuperar)\b[^.!?]*[.!?]?$",
            sentence, re.I)
        if suffix and re.search(r"\b(?:podr[íi]a[n]?|quiz[aá]s?|posiblemente|probablemente)\b",
                                sentence[:suffix.start()], re.I):
            prefix = sentence[:suffix.start()].strip() + "."
            if not is_research_process_comment(prefix):
                kept.append(prefix)
                continue
        if re.match(r"^(?:La[s]?\s+b[uú]squedas?|No se ha[n]? podido|No hemos podido|"
                    r"No he podido|No fue posible|X (?:result[oó]|fue|est[aá]) inaccesible)\b",
                    sentence, re.I):
            continue
        # Do not silently rewrite a mixed factual statement.
        return ""
    return " ".join(kept).strip()


def spoken_text(brief,dossier):
    # Fail closed: the public short explanation must match the exact dossier,
    # not merely the title or the identity of a related news story.
    if not isinstance(dossier,str) or not normalized(brief) or normalized(brief) not in normalized(dossier):
        return brief,False
    research=[]
    active=False
    for raw in dossier.splitlines():
        line=raw.strip()
        key=heading(line)
        if re.match(r"^(investigación(?: y contraste(?: de fuentes)?)?|contexto(?: y contraste)?|explicación (?:ampliada|extensa|detallada|larga)|desarrollo(?: de la explicación)?)\s*:?$",key):
            active=True
            continue
        if not active: continue
        if re.match(r"^(fuentes(?:\b|:)|enlaces(?:\b|:)|referencias(?:\b|:)|hashtags(?:\b|:)|remate(?:\b|:)|estado_|confianza editorial|identificador|revisión|límites de la verificación|no se ha publicado|no se ha modificado|tt_v2|ttactualidad_outbox|conclusión:)",key):
            # Keep factual uncertainty if expressed in the narrative, but do
            # not read out source lists or internal operational metadata.
            break
        if not line or re.fullmatch(r"\+\d+|Writing|:::+.*|\[\d+\]",line,re.I): continue
        if re.match(r"^https?://",line): continue
        line=clean(re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)",r"\1",line))
        line=re.sub(r"[*_]{1,3}","",line)
        # Source-only labels, citations and footer lines are not narration.
        if len(line)<35 or len(line.split())<6 or not re.search(r"[.!?;:]",line): continue
        line=clean_supplemental_narrative(line)
        if not line or normalized(line) in normalized(brief): continue
        research.append(line)
    extra=" ".join(research).strip()
    if len(extra)<60 or len(extra)>4500:
        return brief,False
    return brief.rstrip()+(" " if brief.endswith((".","!","?")) else ". ")+extra,True

def get(url,limit):
    req=urllib.request.Request(url,headers={"User-Agent":"TTActualidad-Podcast/1.0","Accept":"*/*"})
    with urllib.request.urlopen(req,timeout=25) as res:
        data=res.read(limit+1)
        if len(data)>limit: raise ValueError("Response exceeds cap")
        return data

def date_of(s):
    date=parsedate_to_datetime(s)
    if date.tzinfo is None: raise ValueError("Missing timezone")
    return date.astimezone(timezone.utc)

def filename(guid):
    return hashlib.sha256(guid.encode()).hexdigest()[:24]+".mp3"

def read_feed(data,now,previous=False):
    root=ET.fromstring(data)
    if root.tag!="rss" or root.find("channel") is None: raise ValueError("Not RSS")
    rows={}
    for item in root.findall("./channel/item"):
        guid=item.findtext("guid","")
        if not GUID.fullmatch(guid): raise ValueError("Unexpected GUID")
        if guid in rows: raise ValueError("Duplicate GUID")
        link=ORIGIN+"/tendencias/"+guid.split(":")[2]
        if item.findtext("link","")!=link: raise ValueError("Unexpected link")
        date=date_of(item.findtext("pubDate",""))
        if date>now+timedelta(minutes=10): raise ValueError("Future date")
        if date<now-timedelta(days=7): continue
        title=clean(item.findtext("title",""))
        body=clean(item.findtext("description",""))
        if not title or not body or len(body)>10000: raise ValueError("Invalid episode text")
        row={"guid":guid,"title":title,"text":body,"link":link,"date":format_datetime(date,usegmt=True),"audio":filename(guid)}
        if previous:
            enc=item.find("enclosure")
            if enc is None or enc.get("url")!=BASE+"audio/"+row["audio"] or enc.get("type")!="audio/mpeg":
                raise ValueError("Unexpected existing enclosure")
            row["bytes"]=int(enc.get("length"))
            if not 1024<=row["bytes"]<=10_000_000: raise ValueError("Invalid audio size")
        rows[guid]=row
    if len(rows)>MAX_EPISODES: raise ValueError("Too many episodes")
    return rows,len(root.findall("./channel/item"))

def audio_duration(path):
    return float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)],text=True).strip())

def recover_audio(row):
    path=CACHE/row["audio"]
    if not path.is_file() or path.stat().st_size!=row["bytes"]:
        data=get(BASE+"audio/"+row["audio"],row["bytes"])
        if len(data)!=row["bytes"]: raise ValueError("Published audio mismatch")
        path.write_bytes(data)
    row["seconds"]=round(audio_duration(path),2)
    return path

def synthesize(row):
    path=CACHE/row["audio"]
    with tempfile.TemporaryDirectory() as tmp:
        wav=Path(tmp)/"voice.wav"
        subprocess.run(["python","-m","piper","-m","es_ES-davefx-medium.onnx","-f",str(wav)],input=row["text"]+"\n",text=True,check=True,timeout=120)
        subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(wav),"-ac","1","-codec:a","libmp3lame","-b:a","48k",str(path)],check=True,timeout=60)
    row["seconds"]=round(audio_duration(path),2)
    row["bytes"]=path.stat().st_size
    if not 0<row["seconds"]<=1200 or not 1024<=row["bytes"]<=10_000_000: raise ValueError("Invalid new audio")
    return path

def plan():
    now=datetime.now(timezone.utc)
    # Read public feed first. Any outage aborts, never overwrites the live podcast.
    old,old_count=read_feed(get(BASE+"feed.xml",2_000_000),now,previous=True)
    incoming,_=read_feed(get(RSS,2_000_000),now)
    if not incoming: raise ValueError("Source feed empty; preserve existing podcast")
    daily=Counter(date_of(x["date"]).astimezone(MADRID).date() for x in old.values())
    selected=[]
    for row in sorted(incoming.values(),key=lambda x:date_of(x["date"]),reverse=True):
        day=date_of(row["date"]).astimezone(MADRID).date()
        if row["guid"] in old or daily[day]>=DAY_CAP: continue
        if len(selected)>=RUN_CAP or len(old)+len(selected)>=MAX_EPISODES: break
        selected.append(row)
        daily[day]+=1
    if not selected and old_count==len(old):
        print("PODCAST_UNCHANGED")
        Path("changed.txt").write_text("no")
        return
    Path("plan.json").write_text(json.dumps({"old":old,"selected":selected},ensure_ascii=False),encoding="utf-8")
    Path("changed.txt").write_text("yes")
    print("PLAN_READY",len(old),"existing",len(selected),"new")


def main():
    if Path("changed.txt").read_text().strip()!="yes": return
    data=json.loads(Path("plan.json").read_text(encoding="utf-8"))
    old,selected=data["old"],data["selected"]
    # Public source only, and only if the source text matches the RSS item.
    dossiers={}
    if selected:
        try:
            raw=json.loads(get(DOSSIERS,2_000_000))
            for item in raw.get("items",[]):
                if item.get("status")!="explicada" or item.get("re_in_progress") is True: continue
                rev=item.get("revision")
                if isinstance(rev,int) and rev>0:
                    dossiers[f'ttactualidad:tendencia:{item.get("trend_id")}:r{rev}']=item.get("explanation","")
        except Exception as exc:
            print("DOSSIER_UNAVAILABLE",type(exc).__name__)
    CACHE.mkdir(exist_ok=True)
    SOURCE.mkdir()
    total=sum(x["bytes"] for x in old.values())
    if total>MAX_BYTES: raise ValueError("Existing site exceeds budget")
    for row in old.values():
        path=recover_audio(row)
        shutil.copyfile(path,SOURCE/row["audio"])
    long_count=0
    for row in selected:
        text,is_long=spoken_text(row["text"],dossiers.get(row["guid"],""))
        # The title is already available in the podcast player; a short spoken
        # title is helpful but do not repeat the literal "por qué es tendencia".
        title=re.sub(r":\s*por qué es tendencia$","",row["title"],flags=re.I).lstrip("#")
        if not normalized(text).startswith(normalized(title)):
            text=title.rstrip(". ")+". "+text
        row["text"]=text
        path=synthesize(row)
        if total+row["bytes"]>MAX_BYTES:
            path.unlink(missing_ok=True)
            print("PODCAST_STORAGE_CAP_REACHED")
            break
        total+=row["bytes"]
        old[row["guid"]]=row
        shutil.copyfile(path,SOURCE/row["audio"])
        long_count+=int(is_long)
        print("EPISODE_OK",row["guid"],"long" if is_long else "brief",row["seconds"],row["bytes"])
    # Keep only audio files belonging to the new seven-day manifest in cache.
    active={row["audio"] for row in old.values()}
    for path in CACHE.glob("*.mp3"):
        if path.name not in active: path.unlink()
    (SOURCE/"manifest.json").write_text(json.dumps(list(old.values()),ensure_ascii=False,indent=2),encoding="utf-8")
    Path("changed.txt").write_text("yes")
    print("ROLLING_READY",len(old),"episodes",long_count,"new long",total,"bytes")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--plan",action="store_true")
    parser.add_argument("--generate",action="store_true")
    args=parser.parse_args()
    if not args.generate: plan()
    if not args.plan: main()
