"""Build a static, subscribe-by-URL podcast from verified pilot audio.
No uploads, credentials or calls to TTActualidad from this script.
"""
import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime, format_datetime
from pathlib import Path
from urllib.parse import urlsplit

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("itunes", ITUNES)
ET.register_namespace("atom", ATOM)
NAME = "TTActualidad Tendencias"
DESCRIPTION = "Por qué son tendencia los temas de actualidad en España. Narración sintética en español. Episodios de los últimos siete días."
ORIGIN = "https://ttactualidad.rainyraven22.chatgpt.site"
GUID = re.compile(r"ttactualidad:tendencia:trv2-[a-f0-9]{24}:r[1-9]\d*")
MAX_BYTES = 400_000_000
MAX_EPISODES = 420

def add(parent, tag, value=None, **attrs):
    node = ET.SubElement(parent, tag, attrs)
    if value is not None:
        node.text = str(value)
    return node

def build(source, output, base, now):
    base = base.rstrip("/") + "/"
    if urlsplit(base).scheme != "https" or not urlsplit(base).netloc:
        raise ValueError("HTTPS base required")
    entries = json.loads((source/"manifest.json").read_text(encoding="utf-8"))
    if not isinstance(entries,list) or not 1 <= len(entries) <= MAX_EPISODES:
        raise ValueError("Invalid episode count")
    valid, seen, seen_audio, total = [], set(), set(), 0
    for row in entries:
        guid = row["guid"]
        if not GUID.fullmatch(guid) or guid in seen:
            raise ValueError("Invalid or duplicate episode GUID")
        seen.add(guid)
        date = parsedate_to_datetime(row["date"]).astimezone(timezone.utc)
        if date > now + timedelta(minutes=10):
            raise ValueError("Future episode")
        if date < now - timedelta(days=7):
            continue
        original = source/row["audio"]
        if original.parent != source or original.suffix.lower() != ".mp3":
            raise ValueError("Invalid audio filename")
        size = original.stat().st_size
        if not 1024 <= size <= 10_000_000 or size != row["bytes"]:
            raise ValueError("Unexpected audio size")
        length = float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(original)],text=True).strip())
        if not 0 < length <= 1200 or abs(length - float(row["seconds"])) > 1:
            raise ValueError("Unexpected MP3 duration")
        filename = hashlib.sha256(guid.encode()).hexdigest()[:24]+".mp3"
        if filename in seen_audio:
            raise ValueError("Duplicate audio path")
        seen_audio.add(filename)
        total += size
        if total > MAX_BYTES:
            raise ValueError("Storage safety cap exceeded")
        parts = guid.split(":")
        link = ORIGIN+"/tendencias/"+parts[2]
        if row.get("link") != link:
            raise ValueError("Unexpected source link")
        if not row["title"].strip() or not row["text"].strip():
            raise ValueError("Missing public text")
        valid.append((date,row,filename,original,size,length,link))
    if not valid:
        raise ValueError("No current pilot episodes")
    valid.sort(key=lambda x:x[0],reverse=True)
    output.mkdir(parents=True,exist_ok=False)
    (output/"audio").mkdir()
    root = ET.Element("rss",version="2.0")
    channel = add(root,"channel")
    add(channel,"title",NAME)
    add(channel,"link",base)
    add(channel,"description",DESCRIPTION)
    add(channel,"language","es-ES")
    add(channel,"lastBuildDate",format_datetime(now,usegmt=True))
    add(channel,f"{{{ATOM}}}link",href=base+"feed.xml",rel="self",type="application/rss+xml")
    add(channel,f"{{{ITUNES}}}author","TTActualidad")
    add(channel,f"{{{ITUNES}}}summary",DESCRIPTION)
    add(channel,f"{{{ITUNES}}}type","episodic")
    add(channel,f"{{{ITUNES}}}explicit","false")
    add(channel,f"{{{ITUNES}}}category",text="News")  # set category attribute below
    category = channel[-1]
    category.text = None
    category.set("text","News")
    cards=[]
    for date,row,name,original,size,length,link in valid:
        shutil.copyfile(original,output/"audio"/name)
        item=add(channel,"item")
        add(item,"title",row["title"])
        add(item,"link",link)
        add(item,"description",row["text"])
        add(item,"guid",row["guid"],isPermaLink="false")
        add(item,"pubDate",format_datetime(date,usegmt=True))
        add(item,"enclosure",url=base+"audio/"+name,length=str(size),type="audio/mpeg")
        add(item,f"{{{ITUNES}}}duration",str(round(length)))
        cards.append(f'<article><h2>{html.escape(row["title"])}</h2><p>{date:%d/%m/%Y} · {round(length)} s</p><audio controls preload="none" src="audio/{name}"></audio><details><summary>Texto narrado</summary><p>{html.escape(row["text"])}</p></details><a href="{html.escape(link,quote=True)}">Ficha original</a></article>')
    ET.indent(root)
    ET.ElementTree(root).write(output/"feed.xml",encoding="utf-8",xml_declaration=True)
    (output/"index.html").write_text(f'<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="alternate" type="application/rss+xml" title="{NAME}" href="feed.xml"><title>{NAME}</title><style>body{{max-width:720px;margin:2rem auto;padding:0 1rem;font:16px system-ui;line-height:1.5}}article{{border-top:1px solid #bbb;padding:1rem 0}}audio{{width:100%}}code{{overflow-wrap:anywhere}}</style><h1>{NAME}</h1><p>Piloto de podcast de tendencias. Voz sintética en español.</p><p><a href="feed.xml">RSS del podcast</a> · Copia esta dirección en tu aplicación de podcasts:</p><code>{html.escape(base+"feed.xml")}</code>'+''.join(cards),encoding="utf-8")
    (output/".nojekyll").touch()
    # Check every enclosure against the actual publication package.
    published=ET.parse(output/"feed.xml").getroot().findall("./channel/item")
    assert len(published)==len(valid)
    for entry in published:
        enclosure=entry.find("enclosure")
        target=output/"audio"/enclosure.get("url").rsplit("/",1)[1]
        assert target.stat().st_size==int(enclosure.get("length"))
    print(f"PODCAST_PACKAGE_OK episodes={len(valid)} audio_bytes={total}")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--base",required=True)
    args=p.parse_args()
    build(args.source,args.output,args.base,datetime.now(timezone.utc))
