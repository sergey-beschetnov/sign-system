"""
Вставляет подпись клиента во все страницы PDF где есть место для подписи.
Стратегия: ищем последнюю страницу и вставляем подпись в фиксированные зоны.
Дополнительно добавляем штамп с метаданными.
"""

import fitz  # PyMuPDF
from pathlib import Path
from io import BytesIO


# Зоны подписи на последней странице (относительные координаты 0..1)
# Можно настроить под конкретный шаблон договора
DEFAULT_SIGNATURE_ZONES = [
    # Основной договор — реквизиты сторон, подпись клиента
    {"page": -1, "rel_x": 0.55, "rel_y": 0.82, "rel_w": 0.35, "rel_h": 0.05},
]

# Зоны для каждой страницы с приложениями (если нужно — добавь)
EXTRA_ZONES = []


def embed_signature_on_pdf(
    src_pdf: str,
    sig_png: str,
    out_pdf: str,
    signer_name: str,
    signed_at: str = "",
    contract_number: str = "",
) -> bool:
    try:
        doc = fitz.open(src_pdf)
        sig_img = open(sig_png, "rb").read()

        # Ищем страницы где есть слово "Подпись" или "подпись"
        sign_pages = _find_signature_pages(doc)

        if not sign_pages:
            # Фолбэк — последняя страница
            sign_pages = [len(doc) - 1]

        for page_idx in sign_pages:
            page = doc[page_idx]
            _insert_signature(page, sig_img, signer_name)

        doc.save(out_pdf, garbage=4, deflate=True)
        doc.close()
        return True

    except Exception as e:
        print(f"PDF error: {e}")
        return False


def _find_signature_pages(doc: fitz.Document) -> list[int]:
    """Ищет страницы содержащие место для подписи клиента"""
    result = []
    keywords = ["Подпись", "подпись", "ПОДПИСЬ", "Клиент:", "КЛИЕНТ", "Менеджер"]

    for i, page in enumerate(doc):
        text = page.get_text() 
        if any(kw in text for kw in keywords):
            result.append(i)

    return list(dict.fromkeys(result))


def _insert_signature(page: fitz.Page, sig_bytes: bytes, signer_name: str):
    """Вставляет подпись в нужное место на странице"""
    pw = page.rect.width
    ph = page.rect.height

    # Ищем точное место подписи клиента через поиск текста
    zones = _locate_signature_zones(page, pw, ph)

    for zone in zones:
        rect = fitz.Rect(zone["x"], zone["y"], zone["x"] + zone["w"], zone["y"] + zone["h"])
        # Конвертируем bytes в BytesIO для insert_image (создаем новый для каждой зоны)
        sig_stream = BytesIO(sig_bytes)
        page.insert_image(rect, stream=sig_stream, keep_proportion=True)


def _locate_signature_zones(page: fitz.Page, pw: float, ph: float) -> list[dict]:
    """Ищет зоны подписи: фиксированная позиция + (Подпись) + ячейка Подпись:"""
    sig_h = 40
    zones = []
    seen = set()

    # 1. Фиксированная позиция строки "Клиент ___" внизу каждой страницы
    fx = pw * 0.72
    fy = ph - 58
    zones.append({"x": fx, "y": fy, "w": 150, "h": sig_h})
    seen.add((round(fx / 25), round(fy / 25)))

    # 2. Метка "(Подпись)" под линией → подпись чуть выше метки
    for block in page.get_text("blocks"):
        x0, y0, x1 = block[0], block[1], block[2]
        text_lower = block[4].strip().lower()
        if "(подпись)" in text_lower and y0 >= ph * 0.65:
            blk_w = max(x1 - x0, 130)
            sx = max(10, min(x0 - 20, pw - blk_w - 10))
            sy = y0 - sig_h + 10
            key = (round(sx / 25), round(sy / 25))
            if key not in seen:
                seen.add(key)
                zones.append({"x": sx, "y": sy, "w": blk_w, "h": sig_h})

    # 3. Ячейка "Подпись:" в таблице реквизитов — search_for надёжнее блоков
    for rect in page.search_for("Подпись:"):
        sx = rect.x1 + 25
        sy = rect.y0 - 20
        avail_w = pw - sx - 5
        if avail_w >= 60:
            blk_w = min(130, avail_w)
        else:
            sx = rect.x0
            blk_w = min(130, pw - rect.x0 - 5)
        key = (round(sx / 25), round(sy / 25))
        if key not in seen:
            seen.add(key)
            zones.append({"x": sx, "y": sy, "w": blk_w, "h": sig_h})

    return zones


def _add_stamp(page: fitz.Page, signer_name: str, signed_at: str, contract_number: str):
    """Добавляет штамп с метаданными подписания"""
    pw = page.rect.width
    ph = page.rect.height

    # Штамп в левом нижнем углу
    stamp_rect = fitz.Rect(20, ph - 60, pw * 0.48, ph - 10)

    # Фон штампа
    page.draw_rect(stamp_rect, color=(0.27, 0.67, 0.15), fill=(0.95, 1.0, 0.93), width=0.5)

    # Текст штампа
    stamp_text = (
        f"✓ Подписан электронно\n"
        f"Подписант: {signer_name}\n"
        f"Дата: {signed_at}\n"
        f"Договор № {contract_number}"
    )

    page.insert_textbox(
        fitz.Rect(25, ph - 58, pw * 0.48 - 5, ph - 12),
        stamp_text,
        fontsize=7,
        color=(0.1, 0.4, 0.05),
        fontname="helv",
    )
