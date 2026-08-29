import datetime
import os
from typing import Literal

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(title="SocialSpyAgent Web", version="1.2.2")
TIMEOUT = 20


class SearchRequest(BaseModel):
    platform: Literal["youtube", "instagram", "tiktok"]
    mode: Literal["search", "account"] = "search"
    query: str = Field(min_length=1, max_length=120)
    timeframe: Literal[1, 2, 3, 4] = 4


def after_date(tf: int):
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        1: now - datetime.timedelta(days=1),
        2: now - datetime.timedelta(days=7),
        3: now - datetime.timedelta(days=30),
        4: None,
    }[tf]


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
        dt = parse_date(
            item.get("upload_date")
            or item.get("publishedAt")
            or item.get("published_at")
            or item.get("taken_at")
            or item.get("taken_at_timestamp")
            or item.get("timestamp")
            or item.get("created_at")
        )
        if dt is None or dt >= cutoff:
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
    d = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": key, "part": "snippet,statistics", "id": ",".join(ids)},
        timeout=TIMEOUT,
    )
    if not d.ok:
        raise HTTPException(d.status_code, f"YouTube API: {d.text[:300]}")
    out = []
    for v in d.json().get("items", []):
        s, st = v.get("snippet", {}), v.get("statistics", {})
        out.append({
            "title": s.get("title"),
            "channelTitle": s.get("channelTitle"),
            "viewCount": int(st.get("viewCount", 0)),
            "likeCount": int(st.get("likeCount", 0)),
            "commentCount": int(st.get("commentCount", 0)),
            "publishedAt": s.get("publishedAt"),
            "url": f"https://www.youtube.com/watch?v={v.get('id')}",
        })
    return out


def extract_list(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "media", "videos", "reels", "posts", "results", "result"):
        if isinstance(data.get(key), list):
            return data[key]
    for container_key in ("data", "user_data", "graphql"):
        nested = data.get(container_key)
        if isinstance(nested, list):
            return nested
        if isinstance(nested, dict):
            for key in ("items", "media", "videos", "reels", "posts", "results"):
                if isinstance(nested.get(key), list):
                    return nested[key]
    return []


def normalize_instagram(items):
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        caption = item.get("caption")
        if isinstance(caption, dict):
            caption = caption.get("text")
        code = item.get("code") or item.get("shortcode")
        url = item.get("url") or item.get("permalink") or item.get("video_url")
        if not url and code:
            url = f"https://www.instagram.com/p/{code}/"
        out.append({
            **item,
            "caption": caption or item.get("title") or item.get("description") or item.get("biography"),
            "upload_date": item.get("taken_at") or item.get("taken_at_timestamp") or item.get("timestamp") or item.get("created_at"),
            "url": url,
        })
    return out


def instagram_stable(mode, query, key):
    host = "instagram-scraper-stable-api.p.rapidapi.com"
    headers = {"x-rapidapi-key": key, "x-rapidapi-host": host}
    if mode != "account":
        raise HTTPException(400, "Instagram: por ahora usa modo 'cuenta'.")
    username = query.strip()
    if username.startswith("https://www.instagram.com/") or username.startswith("http://www.instagram.com/"):
        username_or_url = username
    else:
        username_or_url = username.lstrip("@").strip("/")
    endpoint = "/ig_get_fb_profile_hover.php"
    r = requests.get(
        f"https://{host}{endpoint}",
        headers=headers,
        params={"username_or_url": username_or_url},
        timeout=TIMEOUT,
    )
    if not r.ok:
        if r.status_code == 429:
            raise HTTPException(429, "RapidAPI Instagram: cuota o rate limit alcanzado")
        if r.status_code in (401, 403):
            raise HTTPException(r.status_code, "RapidAPI Instagram: clave inválida o API sin suscripción")
        raise HTTPException(r.status_code, f"RapidAPI Instagram: {r.text[:300]}")
    data = r.json()
    items = extract_list(data)
    if items:
        return normalize_instagram(items)
    profile = data.get("user_data") if isinstance(data, dict) else None
    if isinstance(profile, dict):
        username = profile.get("username") or username_or_url
        return [{
            **profile,
            "title": profile.get("full_name") or username,
            "caption": profile.get("biography") or profile.get("bio"),
            "username": username,
            "follower_count": profile.get("follower_count") or profile.get("followers"),
            "following_count": profile.get("following_count") or profile.get("following"),
            "url": f"https://www.instagram.com/{str(username).lstrip('@')}/",
            "record_type": "profile",
        }]
    return []


def rapid(platform, mode, query):
    key = os.getenv("RAPIDAPI_KEY")
    if not key:
        raise HTTPException(503, "RAPIDAPI_KEY no configurada")
    if platform == "instagram":
        return instagram_stable(mode, query, key)
    host = "tiktok-api6.p.rapidapi.com"
    endpoint = "/user/videos" if mode == "account" else "/search/general/query"
    params = {"username": query} if mode == "account" else {"query": query}
    r = requests.get(
        f"https://{host}{endpoint}",
        headers={"x-rapidapi-key": key, "x-rapidapi-host": host},
        params=params,
        timeout=TIMEOUT,
    )
    if not r.ok:
        if r.status_code == 429:
            raise HTTPException(429, "RapidAPI: cuota o rate limit alcanzado")
        if r.status_code in (401, 403):
            raise HTTPException(r.status_code, "RapidAPI: clave inválida o API sin suscripción")
        raise HTTPException(r.status_code, f"RapidAPI: {r.text[:300]}")
    return extract_list(r.json())


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "google_api_configured": bool(os.getenv("GOOGLE_API_KEY")),
        "rapidapi_configured": bool(os.getenv("RAPIDAPI_KEY")),
        "instagram_provider": "instagram-scraper-stable-api",
        "instagram_account_endpoint": "/ig_get_fb_profile_hover.php",
        "default_instagram_account": "pao.arandaok",
    }


@app.post("/api/search")
def search(payload: SearchRequest):
    q = payload.query.strip()
    cutoff = after_date(payload.timeframe)
    if payload.platform == "youtube":
        results = yt_search(q, cutoff)
    else:
        results = filter_time(rapid(payload.platform, payload.mode, q), cutoff)
    return {
        "platform": payload.platform,
        "mode": payload.mode,
        "query": q,
        "timeframe": payload.timeframe,
        "count": len(results),
        "results": results,
    }


PAGE = r'''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SocialSpyAgent Web</title>
<style>
body{font-family:system-ui;background:#0b1020;color:#eef2ff;margin:0}.w{max-width:980px;margin:auto;padding:40px 18px}.p{background:#121a2c;border:1px solid #26334d;border-radius:18px;padding:18px}.g{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}input,select,button{box-sizing:border-box;width:100%;padding:12px;border-radius:10px;border:1px solid #34425f;background:#0e1628;color:white}button{background:#356af6;font-weight:700;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px}.c{background:#121a2c;border:1px solid #26334d;border-radius:14px;padding:14px;overflow:hidden}a{color:#7aa7ff}.s{color:#9ca9bf;margin:12px 0}.err{color:#ff9b9b}@media(max-width:760px){.g,.cards{grid-template-columns:1fr}}
</style>
</head>
<body>
<main class="w">
<h1>SocialSpyAgent Web</h1>
<p>OSINT sobre contenido público de YouTube, Instagram y TikTok.</p>
<section class="p">
<div class="g">
<select id="platform"><option>youtube</option><option selected>instagram</option><option>tiktok</option></select>
<select id="mode"><option value="search">buscar</option><option value="account" selected>cuenta</option></select>
<select id="timeframe"><option value="1">24h</option><option value="2">7 días</option><option value="3">30 días</option><option value="4" selected>todo</option></select>
<input id="query" value="pao.arandaok" placeholder="consulta o usuario" autocomplete="off">
</div>
<p><button id="go" type="button">Analizar @pao.arandaok</button></p>
<div id="status" class="s">Configurado para consultar información pública de @pao.arandaok.</div>
</section>
<section id="out" class="cards"></section>
</main>
<script>
(function(){
  const platformEl=document.getElementById('platform');
  const modeEl=document.getElementById('mode');
  const timeframeEl=document.getElementById('timeframe');
  const queryEl=document.getElementById('query');
  const goBtn=document.getElementById('go');
  const statusEl=document.getElementById('status');
  const outEl=document.getElementById('out');

  const esc=(v)=>String(v??'').replace(/[&<>"']/g,(m)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

  async function runSearch(){
    const q=queryEl.value.trim();
    if(!q){statusEl.textContent='Escribí un usuario o consulta.';return;}
    goBtn.disabled=true;
    goBtn.textContent='Analizando…';
    statusEl.classList.remove('err');
    statusEl.textContent='Consultando API…';
    outEl.innerHTML='';
    try{
      const response=await fetch('/api/search',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({platform:platformEl.value,mode:modeEl.value,query:q,timeframe:Number(timeframeEl.value)})
      });
      let data;
      try{data=await response.json();}catch(_){throw new Error('La API devolvió una respuesta inválida.');}
      if(!response.ok) throw new Error(data.detail||('Error HTTP '+response.status));
      statusEl.textContent=data.count+' resultado'+(data.count===1?'':'s');
      if(!data.results.length){outEl.innerHTML='<article class="c">No se encontraron resultados públicos.</article>';return;}
      for(const x of data.results){
        const title=x.title||x.full_name||x.username||x.caption||x.description||'Resultado';
        const url=x.url||x.permalink||x.video_url||'';
        const caption=x.caption||x.biography||x.publishedAt||x.upload_date||'';
        const followers=x.follower_count??x.followers;
        outEl.insertAdjacentHTML('beforeend',`<article class="c"><b>${esc(title)}</b>${caption?`<p>${esc(caption)}</p>`:''}${followers!=null?`<p>Seguidores: ${esc(followers)}</p>`:''}${url?`<a target="_blank" rel="noopener noreferrer" href="${esc(url)}">Abrir</a>`:''}</article>`);
      }
    }catch(err){
      statusEl.classList.add('err');
      statusEl.textContent='Error: '+(err&&err.message?err.message:String(err));
    }finally{
      goBtn.disabled=false;
      goBtn.textContent='Analizar @'+queryEl.value.trim().replace(/^@/,'');
    }
  }

  goBtn.addEventListener('click',runSearch);
  queryEl.addEventListener('keydown',(ev)=>{if(ev.key==='Enter'){ev.preventDefault();runSearch();}});
  queryEl.addEventListener('input',()=>{goBtn.textContent='Analizar @'+queryEl.value.trim().replace(/^@/,'');});
})();
</script>
</body>
</html>'''


@app.get("/api", response_class=HTMLResponse)
def homepage():
    return PAGE
