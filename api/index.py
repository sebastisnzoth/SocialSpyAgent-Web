import os
from typing import Literal

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(title="SocialSpyAgent Web", version="1.4.0")
TIMEOUT = 20
HOST = "instagram-scraper-stable-api.p.rapidapi.com"


class SearchRequest(BaseModel):
    platform: Literal["instagram"] = "instagram"
    mode: Literal["search", "account"] = "account"
    query: str = Field(min_length=1, max_length=120)
    timeframe: Literal[1, 2, 3, 4] = 4


def rapid_headers(key: str, content_type: str | None = None):
    headers = {
        "x-rapidapi-key": key,
        "x-rapidapi-host": HOST,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def normalize_username(query: str):
    q = query.strip()
    if "instagram.com/" in q:
        q = q.split("instagram.com/", 1)[1]
    return q.strip("/@ ")


def get_profile_details(username: str, key: str):
    try:
        response = requests.get(
            f"https://{HOST}/ig_get_fb_profile_hover.php",
            headers=rapid_headers(key, "application/json"),
            params={"username_or_url": username},
            timeout=TIMEOUT,
        )
        if not response.ok:
            return None
        data = response.json()
        profile = data.get("user_data") if isinstance(data, dict) else None
        if not isinstance(profile, dict):
            return None
        return profile
    except Exception:
        return None


def instagram_search(query: str, exact: bool = False):
    key = os.getenv("RAPIDAPI_KEY")
    if not key:
        raise HTTPException(503, "RAPIDAPI_KEY no configurada")

    wanted = normalize_username(query)
    response = requests.post(
        f"https://{HOST}/search_ig.php",
        headers=rapid_headers(key, "application/x-www-form-urlencoded"),
        data={"search_query": wanted},
        timeout=TIMEOUT,
    )

    if not response.ok:
        if response.status_code == 429:
            raise HTTPException(429, "RapidAPI Instagram: cuota o rate limit alcanzado")
        if response.status_code == 401:
            raise HTTPException(401, "RapidAPI Instagram: clave inválida")
        if response.status_code == 403:
            raise HTTPException(403, "RapidAPI Instagram: endpoint no autorizado por el plan")
        raise HTTPException(response.status_code, f"RapidAPI Instagram: {response.text[:300]}")

    data = response.json()
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
            pic = hd.get("url")

        record = {
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
                hd2 = detail.get("hd_profile_pic_url_info")
                if isinstance(hd2, dict) and hd2.get("url"):
                    record["profile_pic_url"] = hd2.get("url")
                elif detail.get("profile_pic_url"):
                    record["profile_pic_url"] = detail.get("profile_pic_url")

                linked_fb = detail.get("linked_fb_info") or {}
                linked_fb_user = linked_fb.get("linked_fb_user") if isinstance(linked_fb, dict) else None
                record.update({
                    "full_name": detail.get("full_name") or record.get("full_name"),
                    "instagram_id": detail.get("pk") or detail.get("id") or record.get("instagram_id"),
                    "is_verified": bool(detail.get("is_verified")),
                    "is_private": bool(detail.get("is_private")),
                    "follower_count": detail.get("follower_count"),
                    "following_count": detail.get("following_count"),
                    "media_count": detail.get("media_count"),
                    "biography": detail.get("biography") or "",
                    "is_business": bool(detail.get("is_business")),
                    "external_url": detail.get("external_url") or "",
                    "facebook_name": linked_fb_user.get("name") if isinstance(linked_fb_user, dict) else None,
                    "facebook_url": linked_fb_user.get("profile_url") if isinstance(linked_fb_user, dict) else None,
                    "detail_available": True,
                })

        results.append(record)

    return results


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "rapidapi_configured": bool(os.getenv("RAPIDAPI_KEY")),
        "instagram_provider": "instagram-scraper-stable-api",
        "instagram_search_endpoint": "/search_ig.php",
        "instagram_profile_endpoint": "/ig_get_fb_profile_hover.php",
    }


@app.post("/api/search")
def search(payload: SearchRequest):
    results = instagram_search(payload.query.strip(), exact=(payload.mode == "account"))
    return {
        "platform": "instagram",
        "mode": payload.mode,
        "query": payload.query.strip(),
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
body{font-family:system-ui;background:#0b1020;color:#eef2ff;margin:0}.w{max-width:980px;margin:auto;padding:40px 18px}.p{background:#121a2c;border:1px solid #26334d;border-radius:18px;padding:18px}.g{display:grid;grid-template-columns:1fr 1fr;gap:12px}input,select,button{box-sizing:border-box;width:100%;padding:12px;border-radius:10px;border:1px solid #34425f;background:#0e1628;color:white}button{background:#356af6;font-weight:700;cursor:pointer}.cards{display:grid;grid-template-columns:1fr;gap:16px;margin-top:18px}.c{background:#121a2c;border:1px solid #26334d;border-radius:16px;padding:18px}.profile{display:grid;grid-template-columns:110px 1fr;gap:18px;align-items:start}.avatar{width:104px;height:104px;border-radius:50%;object-fit:cover;border:2px solid #34425f}.name{margin:0 0 4px;font-size:1.45rem}.handle{color:#9ca9bf;margin:0 0 12px}.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:16px 0}.stat{background:#0e1628;border:1px solid #26334d;border-radius:12px;padding:12px;text-align:center}.stat b{display:block;font-size:1.2rem}.meta{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.badge{background:#202b44;border-radius:999px;padding:6px 10px;font-size:.9rem}.private{background:#4b2c38}.public{background:#244536}.links{display:flex;flex-wrap:wrap;gap:12px;margin-top:14px}a{color:#7aa7ff}.s{color:#9ca9bf;margin:12px 0}.err{color:#ff9b9b}.note{margin-top:14px;padding:12px;border-radius:12px;background:#0e1628;border:1px solid #26334d;color:#cbd5e1}@media(max-width:700px){.g,.profile{grid-template-columns:1fr}.avatar{width:88px;height:88px}.stats{grid-template-columns:1fr 1fr 1fr}}
</style>
</head>
<body>
<main class="w">
<h1>SocialSpyAgent Web</h1>
<p>Análisis de información pública disponible de perfiles de Instagram.</p>
<section class="p">
<div class="g">
<select id="mode"><option value="account" selected>cuenta exacta</option><option value="search">búsqueda amplia</option></select>
<input id="query" placeholder="usuario de Instagram" autocomplete="off">
</div>
<p><button id="go" type="button">Analizar perfil</button></p>
<div id="status" class="s">Listo.</div>
</section>
<section id="out" class="cards"></section>
</main>
<script>
(function(){
  const modeEl=document.getElementById('mode');
  const queryEl=document.getElementById('query');
  const goBtn=document.getElementById('go');
  const statusEl=document.getElementById('status');
  const outEl=document.getElementById('out');
  const esc=(v)=>String(v??'').replace(/[&<>"']/g,(m)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  const n=(v)=>v==null?'—':Number(v).toLocaleString('es-AR');

  function renderProfile(x){
    const privacy=x.detail_available?(x.is_private?'Privada':'Pública'):'Privacidad no disponible';
    const privacyClass=x.is_private?'private':'public';
    let html=`<article class="c"><div class="profile">`;
    html+=x.profile_pic_url?`<img class="avatar" src="${esc(x.profile_pic_url)}" alt="Foto de perfil">`:'<div></div>';
    html+=`<div><h2 class="name">${esc(x.full_name||x.username||'Perfil')}</h2><p class="handle">@${esc(x.username||'')}</p>`;
    html+=`<div class="meta"><span class="badge ${privacyClass}">${esc(privacy)}</span><span class="badge">${x.is_verified?'Verificada':'No verificada'}</span>${x.is_business?'<span class="badge">Cuenta business</span>':''}</div>`;
    if(x.detail_available){html+=`<div class="stats"><div class="stat"><b>${n(x.follower_count)}</b><span>Seguidores</span></div><div class="stat"><b>${n(x.following_count)}</b><span>Seguidos</span></div><div class="stat"><b>${n(x.media_count)}</b><span>Publicaciones</span></div></div>`;}
    if(x.biography){html+=`<p>${esc(x.biography)}</p>`;}
    if(x.instagram_id){html+=`<p class="s">Instagram ID: ${esc(x.instagram_id)}</p>`;}
    html+='<div class="links">';
    if(x.url) html+=`<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Abrir Instagram</a>`;
    if(x.facebook_url) html+=`<a href="${esc(x.facebook_url)}" target="_blank" rel="noopener noreferrer">Facebook${x.facebook_name?' · '+esc(x.facebook_name):''}</a>`;
    if(x.external_url) html+=`<a href="${esc(x.external_url)}" target="_blank" rel="noopener noreferrer">Sitio externo</a>`;
    html+='</div>';
    if(x.detail_available&&x.is_private){html+=`<div class="note">La cuenta es privada. Instagram informa ${n(x.media_count)} publicaciones, pero su contenido no es accesible públicamente desde esta herramienta.</div>`;}
    if(!x.detail_available){html+=`<div class="note">Se encontró el perfil, pero el endpoint de detalles no estuvo disponible. Se muestran solo los datos públicos devueltos por la búsqueda.</div>`;}
    html+='</div></div></article>';
    return html;
  }

  async function runSearch(){
    const q=queryEl.value.trim();
    if(!q){statusEl.textContent='Escribí un usuario.';return;}
    goBtn.disabled=true; statusEl.classList.remove('err'); statusEl.textContent='Analizando perfil…'; outEl.innerHTML='';
    try{
      const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:'instagram',mode:modeEl.value,query:q,timeframe:4})});
      const data=await response.json();
      if(!response.ok) throw new Error(data.detail||('Error HTTP '+response.status));
      statusEl.textContent=data.count+' resultado'+(data.count===1?'':'s');
      if(!data.results.length){outEl.innerHTML='<article class="c">No se encontraron coincidencias públicas.</article>';return;}
      for(const x of data.results){outEl.insertAdjacentHTML('beforeend',renderProfile(x));}
    }catch(err){statusEl.classList.add('err');statusEl.textContent='Error: '+(err.message||String(err));}
    finally{goBtn.disabled=false;}
  }

  goBtn.addEventListener('click',runSearch);
  queryEl.addEventListener('keydown',(ev)=>{if(ev.key==='Enter'){ev.preventDefault();runSearch();}});
})();
</script>
</body>
</html>'''


@app.get("/api", response_class=HTMLResponse)
def homepage():
    return PAGE
