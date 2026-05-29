import json
import random
import secrets
from datetime import datetime, timedelta
from pathlib import Path

_otp_store: dict = {}   # phone -> {code, expires}
_sessions: dict = {}    # session_id -> {phone, expires}

OTP_TTL_MIN = 5
SESSION_TTL_H = 24
CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def load_allowed_phones() -> list[str]:
    if not CONFIG_PATH.exists():
        return []
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return [_clean(p) for p in data.get("admin_phones", [])]
    except Exception:
        return []


def _clean(phone: str) -> str:
    s = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if s and not s.startswith("+"):
        s = "+" + s
    return s


def is_allowed(phone: str) -> bool:
    return _clean(phone) in load_allowed_phones()


def generate_otp(phone: str) -> str:
    code = str(random.randint(1000, 9999))
    _otp_store[_clean(phone)] = {
        "code": code,
        "expires": datetime.now() + timedelta(minutes=OTP_TTL_MIN),
    }
    return code


def verify_otp(phone: str, code: str) -> bool:
    key = _clean(phone)
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
    _sessions[sid] = {
        "phone": _clean(phone),
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
    """Читает логин и пароль smsc.ru из config.json."""
    if not CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        login = data.get("smsc_login", "").strip()
        password = data.get("smsc_password", "").strip()
        if login and password:
            return login, password
    except Exception:
        pass
    return None


def send_sms(phone: str, code: str) -> bool:
    """Отправляет SMS через smsc.ru. Возвращает True если отправлено."""
    import requests as _requests

    creds = _load_smsc_credentials()
    if not creds:
        print(f"[SMS] smsc.ru не настроен → {phone}  код: {code}")
        return False

    login, password = creds
    text = f"Код входа в ЦОМ: {code}. Действителен 5 минут."

    try:
        resp = _requests.get(
            "https://smsc.ru/sys/send.php",
            params={
                "login": login,
                "psw": password,
                "phones": phone,
                "mes": text,
                "fmt": 3,
                "charset": "utf-8",
            },
            timeout=10,
        )
        data = resp.json()
        if "error" in data:
            print(f"[SMS] smsc.ru ошибка {data['error_code']}: {data['error']}")
            return False
        print(f"[SMS] → {phone}  id={data.get('id')}  cnt={data.get('cnt')}")
        return True
    except Exception as e:
        print(f"[SMS] Ошибка отправки: {e}")
        return False
