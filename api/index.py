import os
import time
from typing import Literal

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(title="SocialSpyAgent Web", version="1.6.0")
TIMEOUT = 20
HOST = "instagram-scraper-stable-api.p.rapidapi.com"
CACHE_TTL_SECONDS = 300
CACHE: dict[str, tuple[float, object]] = {}


class SearchRequest(BaseModel):
    platform: Literal["instagram"] = "instagram"
    mode: Literal["search", "account"] = "account"
    query: str = Field(min_length=1, max_length=200)
    timeframe: Literal[1, 2, 3, 4] = 4


class ToolRequest(BaseModel):
    action: Literal["hashtag", "media", "comments", "reel"]
    value: str = Field(min_length=1, max_length=500)


def api_key():
    key = os.getenv("RAPIDAPI_KEY")
    if not key:
        raise HTTPException(503, "RAPIDAPI_KEY no configurada")
    return key


def headers(key: str, content_type: str | None = None):
    h = {"x-rapidapi-key": key, "x-rapidapi-host": HOST}
    if content_type:
        h["Content-Type"] = content_type
    return h


def normalize_username(query: str):
    q = query.strip()
    if "instagram.com/" in q:
        q = q.split("instagram.com/", 1)[1]
    return q.strip("/@ ")


def cache_get(key: str):
    row = CACHE.get(key)
    if not row:
        return None
    created_at, data = row
    if time.time() - created_at > CACHE_TTL_SECONDS:
        CACHE.pop(key, None)
        return None
    return data


def cache_set(key: str, data):
    CACHE[key] = (time.time(), data)
    if len(CACHE) > 100:
        oldest = min(CACHE.items(), key=lambda item: item[1][0])[0]
        CACHE.pop(oldest, None)


def check_response(response: requests.Response, label: str):
    if response.ok:
        return
    if response.status_code == 429:
        raise HTTPException(429, f"{label}: cuota o rate limit alcanzado")
    if response.status_code == 401:
        raise HTTPException(401, f"{label}: clave inválida")
    if response.status_code == 403:
        raise HTTPException(403, f"{label}: endpoint no autorizado por el plan")
    raise HTTPException(response.status_code, f"{label}: {response.text[:300]}")


def get_profile_details(username: str, key: str):
    try:
        r = requests.get(
            f"https://{HOST}/ig_get_fb_profile_hover.php",
            headers=headers(key, "application/json"),
            params={"username_or_url": username},
            timeout=TIMEOUT,
        )
        if not r.ok:
            return None
        data = r.json()
        profile = data.get("user_data") if isinstance(data, dict) else None
        return profile if isinstance(profile, dict) else None
    except Exception:
        return None


def instagram_search(query: str, exact: bool = False):
    key = api_key()
    wanted = normalize_username(query)
    ck = f"profile:{'exact' if exact else 'search'}:{wanted.lower()}"
    cached = cache_get(ck)
    if cached is not None:
        return cached, True

    r = requests.post(
        f"https://{HOST}/search_ig.php",
        headers=headers(key, "application/x-www-form-urlencoded"),
        data={"search_query": wanted},
        timeout=TIMEOUT,
    )
    check_response(r, "RapidAPI Instagram")
    data = r.json()
    results = []

    for row in data.get("users", []) if isinstance(data, dict) else []:
        user = row.get("user", row) if isinstance(row, dict) else None
        if not isinstance(user, dict):
            continue
        username = str(user.get("username") or "")
        if exact and username.lower() != wanted.lower():
            continue
        pic = user.get("profile_pic_url")
        hd = user.get("hd_profile_pic_url_info")
        if isinstance(hd, dict) and hd.get("url"):
            pic = hd["url"]

        rec = {
            "full_name": user.get("full_name"),
            "username": username,
            "instagram_id": user.get("pk") or user.get("id"),
            "is_verified": bool(user.get("is_verified")),
            "profile_pic_url": pic,
            "url": f"https://www.instagram.com/{username}/" if username else None,
            "detail_available": False,
        }

        if exact and username:
            detail = get_profile_details(username, key)
            if isinstance(detail, dict):
                linked = detail.get("linked_fb_info") or {}
                linked_user = linked.get("linked_fb_user") if isinstance(linked, dict) else None
                hd2 = detail.get("hd_profile_pic_url_info")
                if isinstance(hd2, dict) and hd2.get("url"):
                    rec["profile_pic_url"] = hd2["url"]
                rec.update({
                    "full_name": detail.get("full_name") or rec.get("full_name"),
                    "instagram_id": detail.get("pk") or detail.get("id") or rec.get("instagram_id"),
                    "is_verified": bool(detail.get("is_verified")),
                    "is_private": bool(detail.get("is_private")),
                    "follower_count": detail.get("follower_count"),
                    "following_count": detail.get("following_count"),
                    "media_count": detail.get("media_count"),
                    "biography": detail.get("biography") or "",
                    "is_business": bool(detail.get("is_business")),
                    "external_url": detail.get("external_url") or "",
                    "facebook_name": linked_user.get("name") if isinstance(linked_user, dict) else None,
                    "facebook_url": linked_user.get("profile_url") if isinstance(linked_user, dict) else None,
                    "detail_available": True,
                })
        results.append(rec)

    cache_set(ck, results)
    return results, False


def public_tool(action: str, value: str):
    key = api_key()
    value = value.strip()
    ck = f"tool:{action}:{value.lower()}"
    cached = cache_get(ck)
    if cached is not None:
        return cached, True

    if action == "hashtag":
        hashtag = value.lstrip("#")
        r = requests.get(
            f"https://{HOST}/search_hashtag.php",
            headers=headers(key, "application/json"),
            params={"hashtag": hashtag},
            timeout=TIMEOUT,
        )
    elif action == "media":
        r = requests.get(
            f"https://{HOST}/get_media_data_v2.php",
            headers=headers(key, "application/json"),
            params={"media_code": value},
            timeout=TIMEOUT,
        )
    elif action == "comments":
        r = requests.get(
            f"https://{HOST}/get_post_comments.php",
            headers=headers(key, "application/json"),
            params={"media_code": value, "sort_order": "popular"},
            timeout=TIMEOUT,
        )
    elif action == "reel":
        r = requests.get(
            f"https://{HOST}/get_reel_title.php",
            headers=headers(key, "application/json"),
            params={"reel_post_code_or_url": value, "type": "reel"},
            timeout=TIMEOUT,
        )
    else:
        raise HTTPException(400, "Acción no soportada")

    check_response(r, "RapidAPI Instagram")
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:5000]}
    cache_set(ck, data)
    return data, False


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "rapidapi_configured": bool(os.getenv("RAPIDAPI_KEY")),
        "version": "1.6.0",
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "endpoints": [
            "/search_ig.php",
            "/ig_get_fb_profile_hover.php",
            "/search_hashtag.php",
            "/get_media_data_v2.php",
            "/get_post_comments.php",
            "/get_reel_title.php",
        ],
    }


@app.post("/api/search")
def search(payload: SearchRequest):
    results, cached = instagram_search(payload.query, exact=(payload.mode == "account"))
    return {"count": len(results), "cached": cached, "results": results}


@app.post("/api/tool")
def tool(payload: ToolRequest):
    data, cached = public_tool(payload.action, payload.value)
    return {"action": payload.action, "cached": cached, "data": data}


PAGE = r'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SocialSpyAgent Web</title>
<style>
body{font-family:system-ui;background:#0b1020;color:#eef2ff;margin:0}.w{max-width:1050px;margin:auto;padding:36px 18px}.p,.c{background:#121a2c;border:1px solid #26334d;border-radius:16px;padding:18px;margin-bottom:16px}.g{display:grid;grid-template-columns:1fr 1fr;gap:12px}input,select,button{box-sizing:border-box;width:100%;padding:12px;border-radius:10px;border:1px solid #34425f;background:#0e1628;color:white}button{background:#356af6;font-weight:700;cursor:pointer}.profile{display:grid;grid-template-columns:110px 1fr;gap:18px}.avatar{width:104px;height:104px;border-radius:50%;object-fit:cover}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.stat{background:#0e1628;padding:10px;border-radius:10px;text-align:center}.badge{display:inline-block;background:#202b44;border-radius:999px;padding:6px 10px;margin:3px}.private{background:#4b2c38}.public{background:#244536}.s{color:#9ca9bf}.err{color:#ff9b9b}a{color:#7aa7ff}pre{white-space:pre-wrap;word-break:break-word;background:#0e1628;border-radius:12px;padding:14px;max-height:520px;overflow:auto}.note{background:#0e1628;padding:12px;border-radius:10px;margin-top:10px}@media(max-width:700px){.g,.profile{grid-template-columns:1fr}}
</style></head><body><main class="w">
<h1>SocialSpyAgent Web</h1><p>OSINT sobre información pública de Instagram.</p>
<section class="p"><h2>Perfil</h2><div class="g"><select id="mode"><option value="account" selected>cuenta exacta</option><option value="search">búsqueda amplia</option></select><input id="query" placeholder="usuario de Instagram"></div><p><button id="go">Analizar perfil</button></p><div id="status" class="s">Listo.</div></section>
<section id="out"></section>
<section class="p"><h2>Herramientas públicas</h2><div class="g"><select id="tool"><option value="hashtag">Hashtag</option><option value="media">Foto/video por media code</option><option value="comments">Comentarios por media code</option><option value="reel">Reel por URL o código</option></select><input id="toolValue" placeholder="#hashtag, media code o URL"></div><p><button id="toolGo">Consultar</button></p><div id="toolStatus" class="s">Usá únicamente contenido público.</div></section><section id="toolOut"></section>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const n=v=>v==null?'—':Number(v).toLocaleString('es-AR');
function profileCard(x){const privacy=x.detail_available?(x.is_private?'Privada':'Pública'):'No disponible';return `<article class="c"><div class="profile">${x.profile_pic_url?`<img class="avatar" src="${esc(x.profile_pic_url)}">`:''}<div><h2>${esc(x.full_name||x.username)}</h2><p>@${esc(x.username||'')}</p><p><span class="badge ${x.is_private?'private':'public'}">${privacy}</span><span class="badge">${x.is_verified?'Verificada':'No verificada'}</span></p>${x.detail_available?`<div class="stats"><div class="stat"><b>${n(x.follower_count)}</b><br>Seguidores</div><div class="stat"><b>${n(x.following_count)}</b><br>Seguidos</div><div class="stat"><b>${n(x.media_count)}</b><br>Publicaciones</div></div>`:''}${x.biography?`<p>${esc(x.biography)}</p>`:''}${x.instagram_id?`<p class="s">ID: ${esc(x.instagram_id)}</p>`:''}${x.url?`<a target="_blank" rel="noopener" href="${esc(x.url)}">Instagram</a>`:''}${x.facebook_url?` · <a target="_blank" rel="noopener" href="${esc(x.facebook_url)}">Facebook</a>`:''}${x.detail_available&&x.is_private?`<div class="note">Cuenta privada: se muestran solo metadatos públicos. El contenido privado no es accesible.</div>`:''}</div></div></article>`}
async function runProfile(){const q=query.value.trim();if(!q)return;go.disabled=true;status.textContent='Analizando…';out.innerHTML='';try{const r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:'instagram',mode:mode.value,query:q,timeframe:4})});const d=await r.json();if(!r.ok)throw Error(d.detail||'Error');status.textContent=d.count+' resultado'+(d.count===1?'':'s')+(d.cached?' · caché':'');out.innerHTML=d.results.map(profileCard).join('')||'<article class="c">Sin resultados.</article>'}catch(e){status.className='s err';status.textContent='Error: '+e.message}finally{go.disabled=false}}
async function runTool(){const v=toolValue.value.trim();if(!v)return;toolGo.disabled=true;toolStatus.textContent='Consultando…';toolOut.innerHTML='';try{const r=await fetch('/api/tool',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:tool.value,value:v})});const d=await r.json();if(!r.ok)throw Error(d.detail||'Error');toolStatus.textContent='Resultado'+(d.cached?' · caché':'');toolOut.innerHTML=`<article class="c"><pre>${esc(JSON.stringify(d.data,null,2))}</pre></article>`}catch(e){toolStatus.className='s err';toolStatus.textContent='Error: '+e.message}finally{toolGo.disabled=false}}
go.addEventListener('click',runProfile);query.addEventListener('keydown',e=>{if(e.key==='Enter')runProfile()});toolGo.addEventListener('click',runTool);toolValue.addEventListener('keydown',e=>{if(e.key==='Enter')runTool()});
</script></main></body></html>'''


@app.get("/api", response_class=HTMLResponse)
def homepage():
    return PAGE
