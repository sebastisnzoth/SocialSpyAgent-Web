import datetime
import os
from typing import Literal

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(title="SocialSpyAgent Web", version="1.0.0")
TIMEOUT = 20


class SearchRequest(BaseModel):
    platform: Literal["youtube", "instagram", "tiktok"]
    mode: Literal["search", "account"] = "search"
    query: str = Field(min_length=1, max_length=120)
    timeframe: Literal[1, 2, 3, 4] = 4


def after_date(tf: int):
    now = datetime.datetime.now(datetime.timezone.utc)
    return {1: now-datetime.timedelta(days=1), 2: now-datetime.timedelta(days=7), 3: now-datetime.timedelta(days=30), 4: None}[tf]


def parse_date(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            n = int(value)
            if n > 10_000_000_000:
                n //= 1000
            return datetime.datetime.fromtimestamp(n, tz=datetime.timezone.utc)
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
    except Exception:
        return None


def filter_time(items, cutoff):
    if cutoff is None:
        return items
    out = []
    for item in items:
        dt = parse_date(item.get("upload_date") or item.get("publishedAt") or item.get("published_at"))
        if dt and dt >= cutoff:
            out.append(item)
    return out


def yt_search(query, cutoff):
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        raise HTTPException(503, "GOOGLE_API_KEY no configurada")
    params = {"key": key, "part": "snippet", "type": "video", "q": query, "maxResults": 25}
    if cutoff:
        params["publishedAfter"] = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=TIMEOUT)
    if not r.ok:
        raise HTTPException(r.status_code, f"YouTube API: {r.text[:300]}")
    items = r.json().get("items", [])
    ids = [x.get("id", {}).get("videoId") for x in items if x.get("id", {}).get("videoId")]
    if not ids:
        return []
    d = requests.get("https://www.googleapis.com/youtube/v3/videos", params={"key": key, "part": "snippet,statistics", "id": ",".join(ids)}, timeout=TIMEOUT)
    if not d.ok:
        raise HTTPException(d.status_code, f"YouTube API: {d.text[:300]}")
    out = []
    for v in d.json().get("items", []):
        s, st = v.get("snippet", {}), v.get("statistics", {})
        out.append({"title": s.get("title"), "channelTitle": s.get("channelTitle"), "viewCount": int(st.get("viewCount", 0)), "likeCount": int(st.get("likeCount", 0)), "commentCount": int(st.get("commentCount", 0)), "publishedAt": s.get("publishedAt"), "url": f"https://www.youtube.com/watch?v={v.get('id')}"})
    return out


def rapid(platform, mode, query):
    key = os.getenv("RAPIDAPI_KEY")
    if not key:
        raise HTTPException(503, "RAPIDAPI_KEY no configurada")
    if platform == "instagram":
        host = "instagram360.p.rapidapi.com"
        endpoint = "/userreels/" if mode == "account" else "/searchreels/"
        params = {"username": query} if mode == "account" else {"query": query}
    else:
        host = "tiktok-api6.p.rapidapi.com"
        endpoint = "/user/videos" if mode == "account" else "/search/general/query"
        params = {"username": query} if mode == "account" else {"query": query}
    r = requests.get(f"https://{host}{endpoint}", headers={"x-rapidapi-key": key, "x-rapidapi-host": host}, params=params, timeout=TIMEOUT)
    if not r.ok:
        raise HTTPException(r.status_code, f"RapidAPI: {r.text[:300]}")
    data = r.json()
    if isinstance(data, list):
        return data
    for k in ("data", "items", "videos", "reels", "result", "results"):
        if isinstance(data.get(k), list):
            return data[k]
        if isinstance(data.get(k), dict):
            for kk in ("items", "videos", "reels"):
                if isinstance(data[k].get(kk), list):
                    return data[k][kk]
    return []


@app.get("/api/health")
def health():
    return {"ok": True, "google_api_configured": bool(os.getenv("GOOGLE_API_KEY")), "rapidapi_configured": bool(os.getenv("RAPIDAPI_KEY"))}


@app.post("/api/search")
def search(payload: SearchRequest):
    q = payload.query.strip()
    cutoff = after_date(payload.timeframe)
    if payload.platform == "youtube":
        results = yt_search(q, cutoff)
    else:
        results = filter_time(rapid(payload.platform, payload.mode, q), cutoff)
    return {"platform": payload.platform, "mode": payload.mode, "query": q, "timeframe": payload.timeframe, "count": len(results), "results": results}


PAGE = '''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SocialSpyAgent Web</title><style>body{font-family:system-ui;background:#0b1020;color:#eef2ff;margin:0}.w{max-width:980px;margin:auto;padding:40px 18px}.p{background:#121a2c;border:1px solid #26334d;border-radius:18px;padding:18px}.g{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}input,select,button{width:100%;padding:12px;border-radius:10px;border:1px solid #34425f;background:#0e1628;color:white}button{background:#356af6;font-weight:700;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px}.c{background:#121a2c;border:1px solid #26334d;border-radius:14px;padding:14px;overflow:hidden}a{color:#7aa7ff}.s{color:#9ca9bf;margin:12px 0}@media(max-width:760px){.g,.cards{grid-template-columns:1fr}}</style></head><body><main class="w"><h1>SocialSpyAgent Web</h1><p>OSINT sobre contenido público de YouTube, Instagram y TikTok.</p><section class="p"><div class="g"><select id="platform"><option>youtube</option><option>instagram</option><option>tiktok</option></select><select id="mode"><option value="search">buscar</option><option value="account">cuenta</option></select><select id="timeframe"><option value="1">24h</option><option value="2">7 días</option><option value="3">30 días</option><option value="4" selected>todo</option></select><input id="query" placeholder="consulta o usuario"></div><p><button id="go">Analizar</button></p><div id="status" class="s">Listo.</div></section><section id="out" class="cards"></section></main><script>const e=s=>String(s??'').replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[m]));go.onclick=async()=>{status.textContent='Analizando…';out.innerHTML='';try{let r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:platform.value,mode:mode.value,query:query.value,timeframe:+timeframe.value})});let d=await r.json();if(!r.ok)throw Error(d.detail||'Error');status.textContent=d.count+' resultados';for(let x of d.results){let t=x.title||x.caption||x.description||x.username||'Resultado',u=x.url||x.video_url||'';out.innerHTML+=`<article class="c"><b>${e(t)}</b><p>${e(x.publishedAt||x.upload_date||'')}</p>${u?`<a target="_blank" rel="noopener" href="${e(u)}">Abrir</a>`:''}</article>`}}catch(x){status.textContent=x.message}};</script></body></html>'''


@app.get("/api", response_class=HTMLResponse)
def homepage():
    return PAGE
