from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone

app=FastAPI(title="Scrapely API",version="1.0.0")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["GET"],allow_headers=["*"])
HEADERS={"User-Agent":"Scrapely/1.0 (+public web data extraction tool)"}
TIMEOUT=12
MAX_BYTES=5_000_000

def clean(text): return " ".join((text or "").split())

@app.get("/api/health")
def health(): return {"status":"ok"}

@app.get("/api/extract")
def extract(url:str=Query(...,min_length=8,max_length=2048)):
    p=urlparse(url)
    if p.scheme not in {"http","https"} or not p.netloc:
        raise HTTPException(400,"Enter a valid http:// or https:// URL.")
    try:
        r=requests.get(url,headers=HEADERS,timeout=TIMEOUT,allow_redirects=True,stream=True)
        r.raise_for_status()
        ct=r.headers.get("content-type","").lower()
        if "text/html" not in ct and "application/xhtml+xml" not in ct:
            raise HTTPException(415,"That URL does not point to an HTML page.")
        data=r.raw.read(MAX_BYTES+1,decode_content=True)
        if len(data)>MAX_BYTES: raise HTTPException(413,"The page is too large for this extractor.")
        html=data.decode(r.encoding or "utf-8",errors="replace")
        soup=BeautifulSoup(html,"html.parser")
        title=clean(soup.title.get_text(" ",strip=True)) if soup.title else ""
        desc=""
        tag=soup.find("meta",attrs={"name":lambda v:v and v.lower()=="description"})
        if tag: desc=clean(tag.get("content",""))
        headings=[{"level":t.name,"text":clean(t.get_text(" ",strip=True))} for t in soup.find_all(["h1","h2","h3","h4","h5","h6"]) if clean(t.get_text(" ",strip=True))]
        links=[];seen=set()
        for t in soup.find_all("a",href=True):
            href=t.get("href","").strip()
            if not href or href.startswith(("#","mailto:","tel:","javascript:")): continue
            u=urljoin(r.url,href)
            if urlparse(u).scheme not in {"http","https"} or u in seen: continue
            seen.add(u);links.append({"text":clean(t.get_text(" ",strip=True)) or u,"url":u})
            if len(links)>=500: break
        images=[];seen=set()
        for t in soup.find_all("img"):
            src=(t.get("src") or t.get("data-src") or "").strip()
            if not src: continue
            u=urljoin(r.url,src)
            if urlparse(u).scheme not in {"http","https"} or u in seen: continue
            seen.add(u);images.append({"alt":clean(t.get("alt","")),"url":u})
            if len(images)>=300: break
        metadata=[]
        for t in soup.find_all("meta"):
            name=t.get("name") or t.get("property") or t.get("http-equiv");content=t.get("content")
            if name and content: metadata.append({"name":clean(name),"content":clean(content)})
            if len(metadata)>=200: break
        return {"url":r.url,"title":title,"description":desc,"fetchedAt":datetime.now(timezone.utc).isoformat(),"headings":headings,"links":links,"images":images,"metadata":metadata,"summary":{"headings":len(headings),"links":len(links),"images":len(images),"metadata":len(metadata)}}
    except HTTPException: raise
    except requests.Timeout: raise HTTPException(504,"The website took too long to respond.")
    except requests.RequestException as e: raise HTTPException(502,f"Could not fetch that page: {e}")
    except Exception: raise HTTPException(500,"The page could not be parsed.")
