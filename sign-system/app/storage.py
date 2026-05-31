import json
import shutil
from datetime import datetime
from pathlib import Path
from threading import Lock

BACKUP_KEEP = 10


class TokenStorage:
    def __init__(self, path: Path):
        self.path = path
        self.backup_dir = path.parent / "backup"
        self.backup_dir.mkdir(exist_ok=True)
        self._lock = Lock()
        if not path.exists():
            path.write_text("{}", encoding="utf-8")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _dump(self, data: dict):
        # Атомарная запись через временный файл
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._make_backup()

    def _make_backup(self):
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = self.backup_dir / f"tokens_{ts}.json"
            shutil.copy2(self.path, dst)
            # Оставляем только последние BACKUP_KEEP копий
            backups = sorted(self.backup_dir.glob("tokens_*.json"))
            for old in backups[:-BACKUP_KEEP]:
                old.unlink(missing_ok=True)
        except Exception:
            pass

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

    def delete(self, token: str):
        with self._lock:
            data = self._load()
            data.pop(token, None)
            self._dump(data)
