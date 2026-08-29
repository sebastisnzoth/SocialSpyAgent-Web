import os
import time
from typing import Literal

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(title="SocialSpyAgent Web", version="1.8.0")
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
    action: Literal["hashtag", "media", "comments", "reel", "followers", "following"]
    value: str = Field(min_length=1, max_length=500)
    pagination_token: str = Field(default="", max_length=3000)
    amount: int = Field(default=12, ge=1, le=25)


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
    q = q.split("?", 1)[0].split("#", 1)[0]
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
    if len(CACHE) > 150:
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
                elif detail.get("profile_pic_url"):
                    rec["profile_pic_url"] = detail.get("profile_pic_url")
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


def public_tool(action: str, value: str, pagination_token: str = "", amount: int = 12):
    key = api_key()
    value = value.strip()
    normalized_value = normalize_username(value) if action in ("followers", "following") else value
    token_key = pagination_token[-40:] if pagination_token else "first"
    ck = f"tool:{action}:{normalized_value.lower()}:{token_key}:{amount}"
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
    elif action in ("followers", "following"):
        if not normalized_value:
            raise HTTPException(400, "Usuario inválido")
        profile = get_profile_details(normalized_value, key)
        if isinstance(profile, dict) and bool(profile.get("is_private")):
            raise HTTPException(403, "La cuenta es privada. No se enumeran seguidores ni seguidos de cuentas privadas.")
        r = requests.post(
            f"https://{HOST}/get_ig_user_followers_v2.php",
            headers=headers(key, "application/x-www-form-urlencoded"),
            data={
                "username_or_url": f"https://www.instagram.com/{normalized_value}/",
                "data": action,
                "amount": str(amount),
                "pagination_token": pagination_token,
            },
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
        "version": "1.8.0",
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "features": ["profile", "followers", "following", "pagination", "hashtag", "media", "comments", "reel"],
    }


@app.post("/api/search")
def search(payload: SearchRequest):
    results, cached = instagram_search(payload.query, exact=(payload.mode == "account"))
    return {"count": len(results), "cached": cached, "results": results}


@app.post("/api/tool")
def tool(payload: ToolRequest):
    data, cached = public_tool(payload.action, payload.value, payload.pagination_token, payload.amount)
    return {"action": payload.action, "cached": cached, "data": data}


PAGE = r'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SocialSpyAgent Web</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;background:linear-gradient(180deg,#070b16 0,#0b1020 42%,#0a0f1d 100%);color:#eef2ff;margin:0;min-height:100vh}.w{max-width:1120px;margin:auto;padding:32px 18px 70px}.hero{padding:18px 0 8px}.hero h1{font-size:clamp(2rem,5vw,3.2rem);margin:0;letter-spacing:-.04em}.hero p{color:#9aa8c4;font-size:1.05rem}.pill{display:inline-flex;padding:6px 10px;border:1px solid #2b3c5d;border-radius:999px;background:#111a2d;color:#9ec1ff;font-size:.82rem}.panel,.card{background:rgba(17,26,45,.88);border:1px solid #263654;border-radius:18px;box-shadow:0 14px 36px rgba(0,0,0,.18)}.panel{padding:18px;margin:18px 0}.panel h2{margin:0 0 6px}.muted,.status{color:#9aa8c4}.grid2{display:grid;grid-template-columns:1fr 1.3fr;gap:12px;margin-top:14px}.actions{display:flex;gap:10px;margin-top:12px}.actions button{flex:1}input,select,button{width:100%;padding:13px 14px;border-radius:12px;border:1px solid #344564;background:#0c1425;color:#fff;font:inherit}input:focus,select:focus{outline:2px solid #4f7cff;outline-offset:1px}button{border:0;background:linear-gradient(135deg,#3d6df2,#6a5df6);font-weight:750;cursor:pointer}button.secondary{background:#16223a;border:1px solid #344564}button:disabled{opacity:.55;cursor:not-allowed}.results{display:grid;gap:14px}.profile{padding:18px}.profileRow{display:grid;grid-template-columns:112px 1fr;gap:18px}.avatar{width:104px;height:104px;border-radius:50%;object-fit:cover;border:2px solid #33476d;background:#0c1425}.name{font-size:1.45rem;margin:0}.handle{margin:4px 0 10px;color:#9aa8c4}.badges{display:flex;gap:7px;flex-wrap:wrap}.badge{display:inline-flex;padding:6px 9px;border-radius:999px;background:#202d48;font-size:.82rem}.badge.ok{background:#183d31}.badge.warn{background:#4a2936}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}.stat{background:#0c1425;border:1px solid #243451;border-radius:12px;padding:11px;text-align:center}.stat b{display:block;font-size:1.15rem}.links{display:flex;gap:14px;flex-wrap:wrap}a{color:#8cb4ff;text-decoration:none}a:hover{text-decoration:underline}.note{margin-top:12px;padding:11px 12px;border-radius:12px;background:#0c1425;border:1px solid #263654;color:#c5d0e5}.people{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}.person{display:flex;gap:11px;align-items:center;padding:12px;background:#10192b;border:1px solid #263654;border-radius:14px}.person img{width:54px;height:54px;border-radius:50%;object-fit:cover;background:#0c1425}.person h3{font-size:.98rem;margin:0 0 3px}.person p{margin:0;color:#9aa8c4;font-size:.88rem}.person .mini{margin-top:5px;font-size:.78rem}.toolCard{padding:18px}.jsonToggle{margin-top:12px}.json{display:none;white-space:pre-wrap;word-break:break-word;background:#0a1220;border:1px solid #243451;border-radius:12px;padding:13px;max-height:500px;overflow:auto}.err{color:#ff9f9f}.toolbar{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px}.toolbar h2{margin:0}@media(max-width:720px){.grid2,.profileRow{grid-template-columns:1fr}.stats{grid-template-columns:repeat(3,1fr)}.avatar{width:88px;height:88px}.actions,.toolbar{flex-direction:column;align-items:stretch}}
</style></head><body><main class="w">
<header class="hero"><span class="pill">Instagram Public Intelligence · v1.8</span><h1>SocialSpyAgent</h1><p>Perfil, relaciones públicas, hashtags, publicaciones, comentarios y reels en un solo panel.</p></header>
<section class="panel"><h2>Analizar perfil</h2><div class="muted">Buscá una cuenta exacta o coincidencias públicas.</div><div class="grid2"><select id="mode"><option value="account" selected>Cuenta exacta</option><option value="search">Búsqueda amplia</option></select><input id="query" placeholder="@usuario o URL de Instagram" autocomplete="off"></div><div class="actions"><button id="go">Analizar perfil</button></div><div id="status" class="status">Listo.</div></section>
<section id="out" class="results"></section>
<section class="panel"><h2>Explorar datos públicos</h2><div class="muted">Elegí una herramienta y consultá contenido públicamente disponible.</div><div class="grid2"><select id="tool"><option value="followers">Seguidores públicos</option><option value="following">Seguidos públicos</option><option value="hashtag">Hashtag</option><option value="media">Foto/video por media code</option><option value="comments">Comentarios por media code</option><option value="reel">Reel por URL o código</option></select><input id="toolValue" placeholder="usuario, #hashtag, media code o URL" autocomplete="off"></div><div class="actions"><button id="toolGo">Consultar</button></div><div id="toolStatus" class="status">Las cuentas privadas no se enumeran.</div></section>
<section id="toolOut" class="results"></section>
<script>
const $=id=>document.getElementById(id);const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const num=v=>v==null?'—':Number(v).toLocaleString('es-AR');
const mode=$('mode'),query=$('query'),go=$('go'),status=$('status'),out=$('out'),tool=$('tool'),toolValue=$('toolValue'),toolGo=$('toolGo'),toolStatus=$('toolStatus'),toolOut=$('toolOut');let nextToken='';let activeTool='';let activeValue='';
function profileCard(x){const privacy=x.detail_available?(x.is_private?'Privada':'Pública'):'No disponible';return `<article class="card profile"><div class="profileRow">${x.profile_pic_url?`<img class="avatar" src="${esc(x.profile_pic_url)}" alt="Foto de perfil">`:'<div></div>'}<div><h2 class="name">${esc(x.full_name||x.username||'Perfil')}</h2><p class="handle">@${esc(x.username||'')}</p><div class="badges"><span class="badge ${x.is_private?'warn':'ok'}">${esc(privacy)}</span><span class="badge">${x.is_verified?'Verificada':'No verificada'}</span>${x.is_business?'<span class="badge">Business</span>':''}</div>${x.detail_available?`<div class="stats"><div class="stat"><b>${num(x.follower_count)}</b>Seguidores</div><div class="stat"><b>${num(x.following_count)}</b>Seguidos</div><div class="stat"><b>${num(x.media_count)}</b>Posts</div></div>`:''}${x.biography?`<p>${esc(x.biography)}</p>`:''}${x.instagram_id?`<p class="muted">Instagram ID: ${esc(x.instagram_id)}</p>`:''}<div class="links">${x.url?`<a href="${esc(x.url)}" target="_blank" rel="noopener">Abrir Instagram</a>`:''}${x.facebook_url?`<a href="${esc(x.facebook_url)}" target="_blank" rel="noopener">Facebook${x.facebook_name?' · '+esc(x.facebook_name):''}</a>`:''}${x.external_url?`<a href="${esc(x.external_url)}" target="_blank" rel="noopener">Sitio externo</a>`:''}</div>${x.detail_available&&x.is_private?'<div class="note">Cuenta privada: solo se muestran metadatos públicos del perfil.</div>':''}</div></div></article>`}
function personCard(u){const username=u.username||'';return `<article class="person">${u.profile_pic_url?`<img src="${esc(u.profile_pic_url)}" alt="">`:'<div></div>'}<div><h3>${esc(u.full_name||username||'Cuenta')}</h3><p>@${esc(username)}</p><div class="mini"><span class="badge ${u.is_private?'warn':'ok'}">${u.is_private?'Privada':'Pública'}</span>${u.is_verified?'<span class="badge">✓ Verificada</span>':''}</div>${username?`<a href="https://www.instagram.com/${encodeURIComponent(username)}/" target="_blank" rel="noopener">Abrir</a>`:''}</div></article>`}
function rawCard(data,title='Resultado'){const raw=esc(JSON.stringify(data,null,2));return `<article class="card toolCard"><div class="toolbar"><h2>${esc(title)}</h2><button class="secondary" onclick="this.parentElement.nextElementSibling.style.display=this.parentElement.nextElementSibling.style.display==='block'?'none':'block'">Ver JSON</button></div><pre class="json">${raw}</pre></article>`}
function renderToolData(action,data,append=false){if((action==='followers'||action==='following')&&data&&Array.isArray(data.users)){nextToken=data.pagination_token||'';const cards=data.users.map(personCard).join('');const head=append?'':`<article class="card toolCard"><div class="toolbar"><h2>${action==='followers'?'Seguidores':'Seguidos'}</h2><span class="muted">${num(data.count||data.users.length)} resultados reportados</span></div><div id="peopleGrid" class="people"></div><div id="moreWrap"></div></article>`;if(!append)toolOut.innerHTML=head;const grid=$('peopleGrid');if(grid)grid.insertAdjacentHTML('beforeend',cards);const wrap=$('moreWrap');if(wrap)wrap.innerHTML=nextToken?'<div class="actions"><button id="moreBtn" class="secondary">Cargar más</button></div>':'';const more=$('moreBtn');if(more)more.addEventListener('click',()=>runTool(true));return}toolOut.innerHTML=rawCard(data,action==='hashtag'?'Resultado de hashtag':action==='media'?'Detalle de publicación':action==='comments'?'Comentarios públicos':'Datos del reel')}
async function runProfile(){const q=query.value.trim();if(!q){status.textContent='Escribí un usuario.';return}go.disabled=true;status.className='status';status.textContent='Analizando…';out.innerHTML='';try{const r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:'instagram',mode:mode.value,query:q,timeframe:4})});const d=await r.json();if(!r.ok)throw Error(d.detail||'Error');status.textContent=`${d.count} resultado${d.count===1?'':'s'}${d.cached?' · caché':''}`;out.innerHTML=d.results.map(profileCard).join('')||'<article class="card toolCard">Sin resultados públicos.</article>'}catch(e){status.className='status err';status.textContent='Error: '+e.message}finally{go.disabled=false}}
async function runTool(loadMore=false){const v=(loadMore?activeValue:toolValue.value.trim());const action=(loadMore?activeTool:tool.value);if(!v)return;if(!loadMore){activeValue=v;activeTool=action;nextToken='';toolOut.innerHTML=''}toolGo.disabled=true;const more=$('moreBtn');if(more)more.disabled=true;toolStatus.className='status';toolStatus.textContent=loadMore?'Cargando más…':'Consultando…';try{const r=await fetch('/api/tool',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,value:v,pagination_token:loadMore?nextToken:'',amount:12})});const d=await r.json();if(!r.ok)throw Error(d.detail||'Error');toolStatus.textContent='Resultado'+(d.cached?' · caché':'');renderToolData(action,d.data,loadMore)}catch(e){toolStatus.className='status err';toolStatus.textContent='Error: '+e.message}finally{toolGo.disabled=false;const m=$('moreBtn');if(m)m.disabled=false}}
go.addEventListener('click',runProfile);query.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();runProfile()}});toolGo.addEventListener('click',()=>runTool(false));toolValue.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();runTool(false)}});
</script></main></body></html>'''


@app.get("/api", response_class=HTMLResponse)
def homepage():
    return PAGE
