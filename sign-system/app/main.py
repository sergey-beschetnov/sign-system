import os
import uuid
import base64
import json
import hashlib
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
import uvicorn

from .pdf_utils import embed_signature_on_pdf
from .storage import TokenStorage
from .auth import (
    is_allowed, generate_otp, verify_otp,
    create_session, get_session, delete_session, send_sms,
    load_admins, save_admins, get_admin, clean_phone,
)

app = FastAPI(title="Sign System - ЦОМ")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Middleware: защита /admin/* ──────────────────────────────────────────────

OPEN_PATHS = {"/admin/login", "/admin/login/send", "/admin/login/verify"}

class AdminAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/admin") and path not in OPEN_PATHS:
            sid = request.cookies.get("admin_session", "")
            if not get_session(sid):
                return RedirectResponse(url="/admin/login")
        return await call_next(request)

app.add_middleware(AdminAuthMiddleware)

BASE_DIR = Path(__file__).parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
SIGNED_DIR = BASE_DIR / "signed"
STATIC_DIR = BASE_DIR / "static"

UPLOADS_DIR.mkdir(exist_ok=True)
SIGNED_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/signed", StaticFiles(directory=str(SIGNED_DIR)), name="signed_files")

storage = TokenStorage(BASE_DIR / "tokens.json")


# ─── Авторизация ─────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    phone: str

class VerifyRequest(BaseModel):
    phone: str
    code: str


@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    sid = request.cookies.get("admin_session", "")
    if get_session(sid):
        return RedirectResponse(url="/admin")
    html = (BASE_DIR / "templates" / "login.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.post("/admin/login/send")
async def login_send(req: LoginRequest):
    if not is_allowed(req.phone):
        raise HTTPException(403, "Номер не найден в списке администраторов")
    code = generate_otp(req.phone)
    sms_sent = send_sms(req.phone, code)
    # DEBUG: пока SMS не подключён — возвращаем код в ответе
    if not sms_sent:
        return JSONResponse({"ok": True, "debug_code": code})
    return JSONResponse({"ok": True})


@app.post("/admin/login/verify")
async def login_verify(req: VerifyRequest):
    if not verify_otp(req.phone, req.code):
        raise HTTPException(401, "Неверный или просроченный код")
    sid = create_session(req.phone)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key="admin_session",
        value=sid,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return response


@app.get("/admin/logout")
async def logout(request: Request):
    sid = request.cookies.get("admin_session", "")
    delete_session(sid)
    response = RedirectResponse(url="/admin/login")
    response.delete_cookie("admin_session")
    return response


# ─── Models ───────────────────────────────────────────────────────────────────

class SignRequest(BaseModel):
    token: str
    signature_b64: str  # base64 PNG от canvas
    signer_name: str


class CreateLinkRequest(BaseModel):
    client_name: str
    client_phone: str
    contract_number: str
    # координаты подписей: список [{page, x, y, width, height}]
    signature_zones: list[dict]


# ─── Admin: загрузить PDF и создать ссылку ────────────────────────────────────

def _current_session(request: Request) -> dict:
    sid = request.cookies.get("admin_session", "")
    session = get_session(sid)
    if not session:
        raise HTTPException(401, "Не авторизован")
    return session


def _require_superadmin(request: Request) -> dict:
    session = _current_session(request)
    if session.get("role") != "superadmin":
        raise HTTPException(403, "Требуются права суперадминистратора")
    return session


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    sid = request.cookies.get("admin_session", "")
    session = get_session(sid) or {}
    phone = session.get("phone", "")
    name = session.get("name", "Администратор")
    role = session.get("role", "admin")
    position = session.get("position", "")
    html = (BASE_DIR / "templates" / "admin.html").read_text(encoding="utf-8")
    html = html.replace("{{ADMIN_PHONE}}", phone)
    html = html.replace("{{ADMIN_NAME}}", name)
    html = html.replace("{{ADMIN_ROLE}}", role)
    html = html.replace("{{ADMIN_POSITION}}", position)
    return HTMLResponse(html)


@app.post("/admin/upload")
async def upload_contract(
    request: Request,
    file: UploadFile = File(...),
    client_name: str = Form(...),
    client_phone: str = Form(...),
    contract_number: str = Form(...),
):
    """Загружает PDF и возвращает ссылку для клиента"""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Только PDF файлы")

    sid = request.cookies.get("admin_session", "")
    session = get_session(sid) or {}
    created_by = session.get("name", "")

    # Сохраняем PDF
    file_id = str(uuid.uuid4())
    pdf_path = UPLOADS_DIR / f"{file_id}.pdf"
    pdf_path.write_bytes(await file.read())

    # Создаём токен
    token = str(uuid.uuid4())
    expires_at = (datetime.now() + timedelta(days=7)).isoformat()

    storage.save(token, {
        "file_id": file_id,
        "client_name": client_name,
        "client_phone": client_phone,
        "contract_number": contract_number,
        "created_by": created_by,
        "created_at": datetime.now().isoformat(),
        "expires_at": expires_at,
        "signed": False,
    })

    return JSONResponse({
        "token": token,
        "sign_url": f"/sign/{token}",
        "expires_at": expires_at,
    })


@app.get("/admin/contracts")
async def list_contracts():
    return JSONResponse(storage.all())


@app.delete("/admin/contracts/{token}")
async def delete_contract(token: str, request: Request):
    _require_superadmin(request)
    data = storage.get(token)
    if not data:
        raise HTTPException(404, "Договор не найден")
    file_id = data.get("file_id", "")
    for f in [
        UPLOADS_DIR / f"{file_id}.pdf",
        UPLOADS_DIR / f"{file_id}_sig.png",
        SIGNED_DIR / f"{file_id}_signed.pdf",
    ]:
        if f.exists():
            f.unlink()
    storage.delete(token)
    return JSONResponse({"ok": True})


@app.get("/admin/export")
async def export_contracts(request: Request):
    _require_superadmin(request)
    import csv, io
    from fastapi.responses import Response as FastResponse
    contracts = storage.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Договор", "Клиент", "Телефон", "Создал", "Создан", "Статус", "Дата подписи", "Подписант"])
    for _, d in contracts.items():
        writer.writerow([
            d.get("contract_number", ""),
            d.get("client_name", ""),
            d.get("client_phone", ""),
            d.get("created_by", ""),
            d.get("created_at", "")[:10],
            "Подписан" if d.get("signed") else "Ожидает",
            d.get("signed_at", "")[:10] if d.get("signed") else "",
            d.get("signer_name", "") if d.get("signed") else "",
        ])
    return FastResponse(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contracts.csv"},
    )


# ─── Управление пользователями (суперадмин) ───────────────────────────────────

class AdminUserRequest(BaseModel):
    phone: str
    name: str
    role: str = "admin"


@app.get("/admin/users")
async def list_users(request: Request):
    _require_superadmin(request)
    return JSONResponse(load_admins())


@app.post("/admin/users")
async def add_user(req: AdminUserRequest, request: Request):
    _require_superadmin(request)
    if req.role not in ("admin", "superadmin"):
        raise HTTPException(400, "Недопустимая роль")
    admins = load_admins()
    phone = clean_phone(req.phone)
    if not phone or len(phone) < 5:
        raise HTTPException(400, "Некорректный номер телефона")
    if any(a["phone"] == phone for a in admins):
        raise HTTPException(400, "Пользователь с таким номером уже существует")
    admins.append({"phone": phone, "name": req.name.strip(), "role": req.role})
    save_admins(admins)
    return JSONResponse({"ok": True})


@app.delete("/admin/users/{phone}")
async def delete_user(phone: str, request: Request):
    session = _require_superadmin(request)
    target = clean_phone(phone)
    if target == session.get("phone"):
        raise HTTPException(400, "Нельзя удалить собственный аккаунт")
    admins = load_admins()
    target_admin = next((a for a in admins if a["phone"] == target), None)
    if not target_admin:
        raise HTTPException(404, "Пользователь не найден")
    if target_admin.get("role") == "superadmin":
        raise HTTPException(403, "Нельзя удалить суперадминистратора")
    new_admins = [a for a in admins if a["phone"] != target]
    save_admins(new_admins)
    return JSONResponse({"ok": True})


@app.patch("/admin/users/{phone}")
async def update_user(phone: str, req: AdminUserRequest, request: Request):
    _require_superadmin(request)
    target = clean_phone(phone)
    admins = load_admins()
    for a in admins:
        if a["phone"] == target:
            if req.name:
                a["name"] = req.name.strip()
            if req.role in ("admin", "superadmin"):
                a["role"] = req.role
            save_admins(admins)
            return JSONResponse({"ok": True})
    raise HTTPException(404, "Пользователь не найден")


# ─── Клиентская страница подписания ──────────────────────────────────────────

@app.get("/sign/{token}", response_class=HTMLResponse)
async def sign_page(token: str):
    data = storage.get(token)
    if not data:
        return HTMLResponse("<h1>Ссылка недействительна</h1>", status_code=404)

    if data.get("signed"):
        return HTMLResponse(_already_signed_html(data, token), status_code=200)

    expires = datetime.fromisoformat(data["expires_at"])
    if datetime.now() > expires:
        return HTMLResponse("<h1>Срок действия ссылки истёк</h1>", status_code=410)

    html_path = BASE_DIR / "templates" / "sign.html"
    html = html_path.read_text(encoding="utf-8")
    html = html.replace("{{CLIENT_NAME}}", data["client_name"])
    html = html.replace("{{CONTRACT_NUMBER}}", data["contract_number"])
    html = html.replace("{{TOKEN}}", token)
    html = html.replace("{{PDF_URL}}", f"/uploads_view/{data['file_id']}.pdf")
    return HTMLResponse(html)


@app.get("/uploads_view/{filename}")
async def serve_upload(filename: str):
    path = UPLOADS_DIR / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path), media_type="application/pdf")


# ─── Принять подпись ─────────────────────────────────────────────────────────

@app.post("/sign/submit")
async def submit_signature(req: SignRequest):
    data = storage.get(req.token)
    if not data:
        raise HTTPException(404, "Токен не найден")
    if data.get("signed"):
        raise HTTPException(400, "Договор уже подписан")

    expires = datetime.fromisoformat(data["expires_at"])
    if datetime.now() > expires:
        raise HTTPException(410, "Срок действия ссылки истёк")

    # Декодируем подпись
    sig_data = req.signature_b64.split(",")[1] if "," in req.signature_b64 else req.signature_b64
    sig_bytes = base64.b64decode(sig_data)

    # Сохраняем подпись как PNG
    sig_path = UPLOADS_DIR / f"{data['file_id']}_sig.png"
    sig_path.write_bytes(sig_bytes)

    # Вставляем подпись в PDF
    src_pdf = UPLOADS_DIR / f"{data['file_id']}.pdf"
    out_pdf = SIGNED_DIR / f"{data['file_id']}_signed.pdf"

    signed_ok = embed_signature_on_pdf(
        src_pdf=str(src_pdf),
        sig_png=str(sig_path),
        out_pdf=str(out_pdf),
        signer_name=req.signer_name,
        signed_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
        contract_number=data["contract_number"],
    )

    if not signed_ok:
        raise HTTPException(500, "Ошибка при создании PDF")

    # Хэш подписанного файла
    file_hash = hashlib.sha256(out_pdf.read_bytes()).hexdigest()

    # Помечаем как подписанный
    data.update({
        "signed": True,
        "signed_at": datetime.now().isoformat(),
        "signer_name": req.signer_name,
        "signed_file": f"{data['file_id']}_signed.pdf",
        "file_hash": file_hash,
    })
    storage.save(req.token, data)

    return JSONResponse({
        "success": True,
        "download_url": f"/signed/{data['file_id']}_signed.pdf",
        "file_hash": file_hash,
    })


# ─── Отладка: что видит PyMuPDF в PDF ────────────────────────────────────────

@app.get("/admin/debug-pdf/{token}")
async def debug_pdf(token: str):
    import fitz
    data = storage.get(token)
    if not data:
        raise HTTPException(404)
    src_pdf = UPLOADS_DIR / f"{data['file_id']}.pdf"
    doc = fitz.open(str(src_pdf))
    result = []
    for page_idx, page in enumerate(doc):
        pw, ph = page.rect.width, page.rect.height
        info = {"page": page_idx, "w": pw, "h": ph,
                "blocks_with_podpis": [], "search_for": [], "words_with_podpis": []}
        for b in page.get_text("blocks"):
            if "подпис" in b[4].lower() or "Подпис" in b[4]:
                info["blocks_with_podpis"].append(
                    {"x0": round(b[0],1), "y0": round(b[1],1), "x1": round(b[2],1), "y1": round(b[3],1),
                     "text": repr(b[4][:120])})
        for r in page.search_for("Подпись:"):
            info["search_for"].append({"x0": round(r.x0,1), "y0": round(r.y0,1), "x1": round(r.x1,1), "y1": round(r.y1,1)})
        for w in page.get_text("words"):
            if "подпис" in w[4].lower():
                info["words_with_podpis"].append({"x0": round(w[0],1), "y0": round(w[1],1), "x1": round(w[2],1), "y1": round(w[3],1), "word": w[4]})
        result.append(info)
    doc.close()
    return JSONResponse(result)


# ─── Превью: перегенерировать PDF с имеющейся подписью ───────────────────────

@app.get("/admin/preview/{token}")
async def preview_signed(token: str):
    data = storage.get(token)
    if not data:
        raise HTTPException(404, "Токен не найден")

    sig_path = UPLOADS_DIR / f"{data['file_id']}_sig.png"
    src_pdf = UPLOADS_DIR / f"{data['file_id']}.pdf"

    if not sig_path.exists():
        raise HTTPException(404, "Подпись не найдена — сначала подпишите договор хоть раз")
    if not src_pdf.exists():
        raise HTTPException(404, "Исходный PDF не найден")

    tmp = Path(tempfile.mktemp(suffix=".pdf"))
    embed_signature_on_pdf(
        src_pdf=str(src_pdf),
        sig_png=str(sig_path),
        out_pdf=str(tmp),
        signer_name=data.get("client_name", "Превью"),
        signed_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
        contract_number=data.get("contract_number", "—"),
    )
    return FileResponse(str(tmp), media_type="application/pdf",
                        headers={"Content-Disposition": "inline"})


# ─── Просмотр подписанного PDF ───────────────────────────────────────────────

@app.get("/view/{token}", response_class=HTMLResponse)
async def view_signed(token: str):
    data = storage.get(token)
    if not data or not data.get("signed"):
        raise HTTPException(404, "Подписанный документ не найден")
    pdf_url = f"/signed/{data['signed_file']}"
    return HTMLResponse(_viewer_html(
        token=token,
        pdf_url=pdf_url,
        contract_number=data.get("contract_number", "—"),
        client_name=data.get("client_name", "—"),
        signed_at=data.get("signed_at", "")[:16].replace("T", " "),
    ))


# ─── Скачать подписанный PDF ──────────────────────────────────────────────────

@app.get("/download/{token}")
async def download_signed(token: str):
    data = storage.get(token)
    if not data or not data.get("signed"):
        raise HTTPException(404, "Подписанный документ не найден")
    path = SIGNED_DIR / data["signed_file"]
    return FileResponse(str(path), media_type="application/pdf",
                        filename=f"Договор_{data['contract_number']}_подписан.pdf")


def _viewer_html(token: str, pdf_url: str, contract_number: str, client_name: str, signed_at: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0">
<title>Договор № {contract_number}</title>
<style>
  @font-face {{font-family:'Gilroy';src:url('/static/fonts/Gilroy-Regular.ttf');font-weight:400;}}
  @font-face {{font-family:'Gilroy';src:url('/static/fonts/Gilroy-Semibold.ttf');font-weight:600;}}
  @font-face {{font-family:'Gilroy';src:url('/static/fonts/Gilroy-Bold.ttf');font-weight:700;}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:'Gilroy',sans-serif;background:#0f0f0f;color:#efefef;min-height:100vh;display:flex;flex-direction:column;}}
  .topbar{{background:#171717;border-bottom:1px solid #272727;padding:12px 16px;display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:10;}}
  .topbar img{{height:30px;width:auto;flex-shrink:0;}}
  .topbar-info{{flex:1;min-width:0;}}
  .topbar-info .num{{font-size:13px;font-weight:600;color:#efefef;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
  .topbar-info .sub{{font-size:11px;color:#5a5a5a;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
  .btn-dl{{padding:8px 14px;background:#4CAC26;color:#fff;border:none;border-radius:5px;font-family:'Gilroy',sans-serif;font-size:11px;font-weight:700;cursor:pointer;white-space:nowrap;text-decoration:none;display:inline-flex;align-items:center;gap:6px;letter-spacing:0.04em;text-transform:uppercase;flex-shrink:0;}}
  .btn-dl:hover{{background:#3a8f1e;}}
  .viewer-wrap{{flex:1;display:flex;flex-direction:column;align-items:center;padding:16px 8px;gap:12px;}}
  .page-wrap{{background:#fff;border-radius:4px;overflow:hidden;box-shadow:0 2px 20px rgba(0,0,0,0.5);width:100%;max-width:700px;}}
  .page-wrap canvas{{display:block;width:100%;height:auto;}}
  .nav{{position:sticky;bottom:0;background:#171717;border-top:1px solid #272727;padding:12px 20px;display:flex;align-items:center;justify-content:center;gap:16px;}}
  .nav-btn{{width:40px;height:40px;background:#1f1f1f;border:1px solid #272727;border-radius:5px;color:#efefef;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:border-color 0.2s;}}
  .nav-btn:hover{{border-color:#4CAC26;color:#4CAC26;}}
  .nav-btn:disabled{{opacity:0.25;cursor:not-allowed;}}
  .page-info{{font-size:13px;color:#5a5a5a;min-width:80px;text-align:center;font-weight:600;}}
  .loading{{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px;gap:12px;color:#5a5a5a;font-size:13px;}}
  .spinner{{width:28px;height:28px;border:2px solid #272727;border-top-color:#4CAC26;border-radius:50%;animation:spin 0.7s linear infinite;}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
  .badge-signed{{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;background:rgba(76,172,38,0.1);border:1px solid rgba(76,172,38,0.25);border-radius:4px;font-size:11px;color:#4CAC26;font-weight:600;letter-spacing:0.04em;}}
</style>
</head>
<body>
<div class="topbar">
  <img src="/static/logo.png" alt="ЦОМ">
  <div class="topbar-info">
    <div class="num">Договор № {contract_number}</div>
    <div class="sub">{client_name} · {signed_at}</div>
  </div>
  <a href="/download/{token}" class="btn-dl">↓ Скачать</a>
</div>

<div class="viewer-wrap" id="viewerWrap">
  <div class="loading" id="loading">
    <div class="spinner"></div>
    Загрузка документа...
  </div>
</div>

<div class="nav">
  <button class="nav-btn" id="prevBtn" disabled onclick="changePage(-1)">‹</button>
  <span class="page-info" id="pageInfo">— / —</span>
  <button class="nav-btn" id="nextBtn" disabled onclick="changePage(1)">›</button>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<script>
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

let pdfDoc = null;
let currentPage = 1;
let totalPages = 0;
let rendering = false;
const canvases = [];

async function init() {{
  try {{
    pdfDoc = await pdfjsLib.getDocument('{pdf_url}').promise;
    totalPages = pdfDoc.numPages;
    document.getElementById('loading').remove();
    await renderAllPages();
    updateNav();
  }} catch(e) {{
    document.getElementById('loading').innerHTML = '<div style="color:#b83232">Ошибка загрузки документа</div>';
  }}
}}

async function renderAllPages() {{
  const wrap = document.getElementById('viewerWrap');
  const devicePixelRatio = window.devicePixelRatio || 1;
  const viewportWidth = Math.min(window.innerWidth - 16, 700);

  for (let i = 1; i <= totalPages; i++) {{
    const page = await pdfDoc.getPage(i);
    const viewport = page.getViewport({{ scale: 1 }});
    const scale = (viewportWidth / viewport.width) * devicePixelRatio;
    const scaledViewport = page.getViewport({{ scale }});

    const pageWrap = document.createElement('div');
    pageWrap.className = 'page-wrap';
    pageWrap.id = 'page-' + i;
    pageWrap.dataset.page = i;

    const canvas = document.createElement('canvas');
    canvas.width = scaledViewport.width;
    canvas.height = scaledViewport.height;
    canvas.style.width = (scaledViewport.width / devicePixelRatio) + 'px';
    canvas.style.height = (scaledViewport.height / devicePixelRatio) + 'px';

    pageWrap.appendChild(canvas);
    wrap.appendChild(pageWrap);
    canvases.push(canvas);

    await page.render({{ canvasContext: canvas.getContext('2d'), viewport: scaledViewport }}).promise;
  }}

  // Intersection observer для обновления номера страницы при скролле
  const observer = new IntersectionObserver((entries) => {{
    entries.forEach(e => {{
      if (e.isIntersecting) {{
        currentPage = parseInt(e.target.dataset.page);
        updateNav();
      }}
    }});
  }}, {{ threshold: 0.4 }});

  document.querySelectorAll('.page-wrap').forEach(el => observer.observe(el));
}}

function changePage(delta) {{
  const target = currentPage + delta;
  if (target < 1 || target > totalPages) return;
  document.getElementById('page-' + target).scrollIntoView({{ behavior: 'smooth', block: 'start' }});
}}

function updateNav() {{
  document.getElementById('pageInfo').textContent = currentPage + ' / ' + totalPages;
  document.getElementById('prevBtn').disabled = currentPage <= 1;
  document.getElementById('nextBtn').disabled = currentPage >= totalPages;
}}

init();
</script>
</body>
</html>"""


def _already_signed_html(data: dict, token: str) -> str:
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Договор подписан</title>
<style>
  @font-face{{font-family:'Gilroy';src:url('/static/fonts/Gilroy-Regular.ttf');font-weight:400;}}
  @font-face{{font-family:'Gilroy';src:url('/static/fonts/Gilroy-Semibold.ttf');font-weight:600;}}
  @font-face{{font-family:'Gilroy';src:url('/static/fonts/Gilroy-Bold.ttf');font-weight:700;}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:'Gilroy',sans-serif;background:#0f0f0f;color:#efefef;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px;}}
  .card{{background:#171717;border:1px solid #272727;border-radius:10px;padding:36px 28px;max-width:380px;width:100%;text-align:center;}}
  .icon{{width:56px;height:56px;background:rgba(76,172,38,0.12);border:1px solid rgba(76,172,38,0.3);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 20px;}}
  .icon svg{{width:26px;height:26px;fill:none;stroke:#4CAC26;stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round;}}
  h2{{font-size:18px;font-weight:700;color:#efefef;margin-bottom:6px;}}
  .sub{{font-size:13px;color:#5a5a5a;margin-bottom:24px;}}
  .meta{{background:#1f1f1f;border-radius:6px;padding:14px 16px;margin-bottom:24px;text-align:left;display:flex;flex-direction:column;gap:8px;}}
  .meta-row{{display:flex;justify-content:space-between;gap:12px;}}
  .meta-row span:first-child{{font-size:11px;color:#5a5a5a;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;}}
  .meta-row span:last-child{{font-size:12px;color:#efefef;text-align:right;}}
  .btn{{display:block;padding:13px;border-radius:6px;font-family:'Gilroy',sans-serif;font-size:12px;font-weight:700;text-decoration:none;text-transform:uppercase;letter-spacing:0.06em;transition:background 0.2s;}}
  .btn-primary{{background:#4CAC26;color:#fff;margin-bottom:8px;}}
  .btn-primary:hover{{background:#3a8f1e;}}
  .btn-secondary{{background:#1f1f1f;color:#efefef;border:1px solid #272727;}}
  .btn-secondary:hover{{border-color:#4CAC26;color:#4CAC26;}}
</style></head><body>
<div class="card">
  <div class="icon"><svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg></div>
  <h2>Договор подписан</h2>
  <div class="sub">Электронная подпись принята</div>
  <div class="meta">
    <div class="meta-row"><span>Договор</span><span>№ {data.get('contract_number','—')}</span></div>
    <div class="meta-row"><span>Подписант</span><span>{data.get('signer_name','—')}</span></div>
    <div class="meta-row"><span>Дата</span><span>{data.get('signed_at','—')[:16].replace('T',' ')}</span></div>
  </div>
  <a href="/view/{token}" class="btn btn-primary">Просмотреть договор</a>
  <a href="/download/{token}" class="btn btn-secondary">Скачать PDF</a>
</div>
</body></html>"""


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
