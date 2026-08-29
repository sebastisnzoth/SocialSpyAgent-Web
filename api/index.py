import os
from typing import Literal

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(title="SocialSpyAgent Web", version="1.3.0")
TIMEOUT = 20


class SearchRequest(BaseModel):
    platform: Literal["instagram"] = "instagram"
    mode: Literal["search", "account"] = "account"
    query: str = Field(min_length=1, max_length=120)
    timeframe: Literal[1, 2, 3, 4] = 4


def instagram_search(query: str, exact: bool = False):
    key = os.getenv("RAPIDAPI_KEY")
    if not key:
        raise HTTPException(503, "RAPIDAPI_KEY no configurada")

    host = "instagram-scraper-stable-api.p.rapidapi.com"
    response = requests.post(
        f"https://{host}/search_ig.php",
        headers={
            "x-rapidapi-key": key,
            "x-rapidapi-host": host,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"search_query": query.lstrip("@").strip()},
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
    wanted = query.lstrip("@").strip().lower()
    results = []

    for row in data.get("users", []) if isinstance(data, dict) else []:
        user = row.get("user", row) if isinstance(row, dict) else None
        if not isinstance(user, dict):
            continue

        username = str(user.get("username") or "")
        if exact and username.lower() != wanted:
            continue

        pic = user.get("profile_pic_url")
        hd = user.get("hd_profile_pic_url_info")
        if isinstance(hd, dict) and hd.get("url"):
            pic = hd.get("url")

        results.append({
            "full_name": user.get("full_name"),
            "username": username,
            "instagram_id": user.get("pk") or user.get("id"),
            "is_verified": bool(user.get("is_verified")),
            "profile_pic_url": pic,
            "url": f"https://www.instagram.com/{username}/" if username else None,
        })

    return results


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "rapidapi_configured": bool(os.getenv("RAPIDAPI_KEY")),
        "instagram_provider": "instagram-scraper-stable-api",
        "instagram_search_endpoint": "/search_ig.php",
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
body{font-family:system-ui;background:#0b1020;color:#eef2ff;margin:0}.w{max-width:900px;margin:auto;padding:40px 18px}.p{background:#121a2c;border:1px solid #26334d;border-radius:18px;padding:18px}.g{display:grid;grid-template-columns:1fr 1fr;gap:12px}input,select,button{box-sizing:border-box;width:100%;padding:12px;border-radius:10px;border:1px solid #34425f;background:#0e1628;color:white}button{background:#356af6;font-weight:700;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px}.c{background:#121a2c;border:1px solid #26334d;border-radius:14px;padding:14px}.avatar{width:72px;height:72px;border-radius:50%;object-fit:cover}.s{color:#9ca9bf;margin:12px 0}.err{color:#ff9b9b}a{color:#7aa7ff}@media(max-width:700px){.g,.cards{grid-template-columns:1fr}}
</style>
</head>
<body>
<main class="w">
<h1>SocialSpyAgent Web</h1>
<p>Búsqueda de perfiles públicos de Instagram.</p>
<section class="p">
<div class="g">
<select id="mode"><option value="account" selected>cuenta exacta</option><option value="search">búsqueda amplia</option></select>
<input id="query" placeholder="usuario de Instagram" autocomplete="off">
</div>
<p><button id="go" type="button">Buscar</button></p>
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

  async function runSearch(){
    const q=queryEl.value.trim();
    if(!q){statusEl.textContent='Escribí un usuario.';return;}
    goBtn.disabled=true; statusEl.classList.remove('err'); statusEl.textContent='Buscando…'; outEl.innerHTML='';
    try{
      const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:'instagram',mode:modeEl.value,query:q,timeframe:4})});
      const data=await response.json();
      if(!response.ok) throw new Error(data.detail||('Error HTTP '+response.status));
      statusEl.textContent=data.count+' resultado'+(data.count===1?'':'s');
      if(!data.results.length){outEl.innerHTML='<article class="c">No se encontraron coincidencias públicas.</article>';return;}
      for(const x of data.results){
        outEl.insertAdjacentHTML('beforeend',`<article class="c">${x.profile_pic_url?`<img class="avatar" src="${esc(x.profile_pic_url)}" alt="">`:''}<h3>${esc(x.full_name||x.username)}</h3><p>@${esc(x.username)}</p>${x.instagram_id?`<p>ID: ${esc(x.instagram_id)}</p>`:''}<p>Verificada: ${x.is_verified?'Sí':'No'}</p>${x.url?`<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Abrir perfil</a>`:''}</article>`);
      }
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
