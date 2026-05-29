import json
from pathlib import Path
from threading import Lock


class TokenStorage:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        if not path.exists():
            path.write_text("{}", encoding="utf-8")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _dump(self, data: dict):
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, token: str) -> dict | None:
        with self._lock:
            return self._load().get(token)

    def save(self, token: str, value: dict):
        with self._lock:
            data = self._load()
            data[token] = value
            self._dump(data)

    def all(self) -> dict:
        with self._lock:
            return self._load()
