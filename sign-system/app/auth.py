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


def send_sms(phone: str, code: str) -> bool:
    """Отправляет SMS. Возвращает True если отправлено, False если заглушка."""
    print(f"[SMS] → {phone}  код: {code}")
    # TODO: подключить SMS-провайдера, вернуть True после подключения
    return False
    # Пример mobizon.kz:
    # import requests
    # requests.get("https://api.mobizon.kz/service/message/sendsmsmessage", params={
    #     "apiKey": "ВАШ_КЛЮЧ",
    #     "recipient": phone,
    #     "text": f"Код входа в ЦОМ: {code}. Действителен 5 минут.",
    # })
