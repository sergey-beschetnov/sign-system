import zipfile
import shutil
from datetime import datetime
from pathlib import Path


BACKUP_DAILY_KEEP = 30  # хранить последние 30 ежедневных архивов


def run_daily_backup(base_dir: Path):
    """
    Создаёт ежедневный ZIP-архив в backup/daily/.
    Включает: tokens.json, все signed/*.pdf, все uploads/*.pdf, rolling-копии токенов.
    Хранит последние BACKUP_DAILY_KEEP архивов.
    """
    daily_dir = base_dir / "backup" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    zip_path = daily_dir / f"backup_{ts}.zip"

    tokens_path  = base_dir / "tokens.json"
    signed_dir   = base_dir / "signed"
    uploads_dir  = base_dir / "uploads"
    rolling_dir  = base_dir / "backup"

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Основная БД
            if tokens_path.exists():
                zf.write(tokens_path, "tokens.json")

            # Подписанные договоры
            for f in sorted(signed_dir.glob("*.pdf")):
                zf.write(f, f"signed/{f.name}")

            # Оригинальные загруженные PDF
            for f in sorted(uploads_dir.glob("*.pdf")):
                zf.write(f, f"uploads/{f.name}")

            # Rolling-бэкапы токенов (на случай повреждения tokens.json)
            for f in sorted(rolling_dir.glob("tokens_*.json")):
                zf.write(f, f"rolling/{f.name}")

        size_kb = zip_path.stat().st_size // 1024
        print(f"[Backup] ✓ {zip_path.name}  ({size_kb} КБ)")

        # Удаляем старые архивы
        archives = sorted(daily_dir.glob("backup_*.zip"))
        for old in archives[:-BACKUP_DAILY_KEEP]:
            old.unlink(missing_ok=True)
            print(f"[Backup] удалён старый архив: {old.name}")

    except Exception as e:
        print(f"[Backup] ✗ Ошибка создания архива: {e}")
