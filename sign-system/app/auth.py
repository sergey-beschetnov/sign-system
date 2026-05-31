import json
import random
import secrets
from datetime import datetime, timedelta
from pathlib import Path

_otp_store: dict = {}    # phone -> {code, expires}
_sessions: dict = {}     # session_id -> {phone, name, role, expires}
_otp_attempts: dict = {} # phone -> {count, window_start}

OTP_TTL_MIN = 5
OTP_RATE_LIMIT = 5        # максимум запросов OTP
OTP_RATE_WINDOW_MIN = 10  # за 10 минут
SESSION_TTL_H = 24
CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def clean_phone(phone: str) -> str:
    s = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if s and not s.startswith("+"):
        s = "+" + s
    return s

_clean = clean_phone  # internal alias


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(data: dict):
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_admins() -> list[dict]:
    """Load admins from config.json. Supports both new {admins:[...]} and legacy {admin_phones:[...]}."""
    cfg = _load_config()
    if "admins" in cfg:
        return [
            {
                "phone": clean_phone(a["phone"]),
                "name": a.get("name", "Администратор"),
                "role": a.get("role", "admin"),
                "position": a.get("position", ""),
            }
            for a in cfg["admins"]
        ]
    # Legacy format
    return [
        {"phone": clean_phone(p), "name": "Администратор", "role": "admin"}
        for p in cfg.get("admin_phones", [])
    ]


def save_admins(admins: list[dict]):
    cfg = _load_config()
    cfg["admins"] = admins
    cfg.pop("admin_phones", None)
    _save_config(cfg)


def get_admin(phone: str) -> dict | None:
    key = clean_phone(phone)
    for a in load_admins():
        if a["phone"] == key:
            return a
    return None


def is_allowed(phone: str) -> bool:
    return get_admin(phone) is not None


def check_otp_rate_limit(phone: str) -> bool:
    """Возвращает True если лимит не превышен, False если заблокирован."""
    key = clean_phone(phone)
    now = datetime.now()
    entry = _otp_attempts.get(key)
    if entry and (now - entry["window_start"]).total_seconds() < OTP_RATE_WINDOW_MIN * 60:
        if entry["count"] >= OTP_RATE_LIMIT:
            return False
        entry["count"] += 1
    else:
        _otp_attempts[key] = {"count": 1, "window_start": now}
    return True


def generate_otp(phone: str) -> str:
    code = str(random.randint(1000, 9999))
    _otp_store[clean_phone(phone)] = {
        "code": code,
        "expires": datetime.now() + timedelta(minutes=OTP_TTL_MIN),
    }
    return code


def verify_otp(phone: str, code: str) -> bool:
    key = clean_phone(phone)
    entry = _otp_store.get(key)
    if not entry:
        return False
    if datetime.now() > entry["expires"]:
        _otp_store.pop(key, None)
        return False
    if entry["code"] != code.strip():
        return False
    _otp_store.pop(key, None)
    return True


def create_session(phone: str) -> str:
    sid = secrets.token_urlsafe(32)
    admin = get_admin(phone) or {}
    _sessions[sid] = {
        "phone": clean_phone(phone),
        "name": admin.get("name", "Администратор"),
        "role": admin.get("role", "admin"),
        "position": admin.get("position", ""),
        "expires": datetime.now() + timedelta(hours=SESSION_TTL_H),
    }
    return sid


def get_session(sid: str) -> dict | None:
    if not sid:
        return None
    entry = _sessions.get(sid)
    if not entry:
        return None
    if datetime.now() > entry["expires"]:
        _sessions.pop(sid, None)
        return None
    return entry


def delete_session(sid: str):
    _sessions.pop(sid, None)


def _load_smsc_credentials() -> tuple[str, str] | None:
    cfg = _load_config()
    login = cfg.get("smsc_login", "").strip()
    password = cfg.get("smsc_password", "").strip()
    if login and password:
        return login, password
    return None


def send_sms(phone: str, code: str) -> bool:
    """Отправляет SMS через smsc.ru. Возвращает True если отправлено."""
    from urllib.request import urlopen
    from urllib.parse import urlencode

    if not _load_config().get("sms_enabled", True):
        print(f"[SMS] отключён (sms_enabled=false) → {phone}  код: {code}")
        return False

    creds = _load_smsc_credentials()
    if not creds:
        print(f"[SMS] smsc.ru не настроен → {phone}  код: {code}")
        return False

    login, password = creds
    text = f"Код входа в ЦОМ: {code}. Действителен 5 минут."

    try:
        params = urlencode({
            "login": login,
            "psw": password,
            "phones": phone,
            "mes": text,
            "fmt": 3,
            "charset": "utf-8",
        })
        url = f"https://smsc.ru/sys/send.php?{params}"
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if "error" in data:
            print(f"[SMS] smsc.ru ошибка {data['error_code']}: {data['error']}")
            return False
        print(f"[SMS] → {phone}  id={data.get('id')}  cnt={data.get('cnt')}")
        return True
    except Exception as e:
        print(f"[SMS] Ошибка отправки: {e}")
        return False
