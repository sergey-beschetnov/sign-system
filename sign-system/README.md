# Union Quzet — Система электронного подписания

## Как работает

1. Менеджер заходит на `/admin`
2. Вводит данные клиента, загружает PDF договора
3. Копирует ссылку и отправляет клиенту (WhatsApp / Telegram)
4. Клиент открывает ссылку, читает договор, рисует подпись пальцем
5. PDF с подписью и штампом готов — скачивают обе стороны

---

## Развёртывание на QNAP

### Вариант 1 — Docker Compose (рекомендуется)

```bash
# 1. Скопировать папку sign-system на QNAP
# 2. Создать файл токенов
mkdir -p data
touch data/tokens.json
echo "{}" > data/tokens.json

# 3. Запустить
docker-compose up -d

# 4. Проверить
docker logs union-quzet-sign
```

Система доступна на `http://QNAP_IP:8000/admin`

### Вариант 2 — Container Station (GUI)

1. Открыть Container Station на QNAP
2. Создать → Upload docker-compose.yml
3. Запустить

---

## Доступ снаружи (Cloudflare Tunnel)

```bash
# На QNAP установить cloudflared
# https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/

cloudflared tunnel create sign-pcn
cloudflared tunnel route dns sign-pcn sign.pcn.kz
cloudflared tunnel run --url http://localhost:8000 sign-pcn
```

После этого клиенты открывают `https://sign.pcn.kz/sign/TOKEN`

---

## Структура файлов

```
sign-system/
├── app/
│   ├── main.py          # FastAPI приложение
│   ├── pdf_utils.py     # Вставка подписи в PDF
│   └── storage.py       # Хранение токенов
├── templates/
│   ├── admin.html       # Панель менеджера
│   └── sign.html        # Страница подписания (клиент)
├── data/
│   ├── uploads/         # Загруженные PDF
│   ├── signed/          # Подписанные PDF
│   └── tokens.json      # База токенов
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Настройка зон подписи

В `pdf_utils.py` система автоматически ищет места с текстом:
- "Подпись:"
- "(Подпись)"  
- "Клиент ___"
- "Клиент:"

Если ваш PDF содержит другие маркеры — добавьте в список `client_markers` в функции `_locate_signature_zones`.

---

## Порты

- `8000` — HTTP (основной)
- Для HTTPS используйте Cloudflare Tunnel или nginx reverse proxy
