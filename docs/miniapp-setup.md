# Настройка мини-приложения Telegram подписки

Эта инструкция описывает, как запустить статическую страницу из каталога `miniapp/index.html`, подключить её к административному API бота и опубликовать через reverse-proxy (nginx или Caddy). Страница отображает текущую подписку пользователя и использует Telegram WebApp init data для авторизации.

## 1. Требования

- Развёрнутый бот Bedolaga c актуальной базой данных.
- Включённое административное API (`WEB_API_ENABLED=true`).
- Доменное имя с действующим TLS-сертификатом (Telegram открывает веб-приложения только по HTTPS).
- Возможность разместить статические файлы (`miniapp/index.html` и `miniapp/app-config.json`) и проксировать запросы `/miniapp/*` к боту.

## 2. Настройка окружения

1. Скопируйте пример конфигурации и включите веб-API:
2. Задайте как минимум следующие переменные:
   ```env
   WEB_API_ENABLED=true                  # включает FastAPI
   WEB_API_HOST=0.0.0.0
   WEB_API_PORT=8080
   WEB_API_ALLOWED_ORIGINS=https://miniapp.example.com
   WEB_API_DEFAULT_TOKEN=super-secret-token
   ```
   - `WEB_API_ALLOWED_ORIGINS` должен содержать домен, с которого будет открываться мини-приложение.
   - `WEB_API_DEFAULT_TOKEN` создаёт bootstrap-токен для запросов от страницы. Его можно заменить на токен, созданный через `POST /tokens`.

## 3. Запуск административного API

После старта проверьте доступность:
```bash
curl -H "X-API-Key: super-secret-token" https://miniapp.example.com/miniapp/health || \
curl -H "X-API-Key: super-secret-token" http://127.0.0.1:8080/health
```

## 4. Подготовка статических файлов

1. При необходимости отредактируйте `miniapp/app-config.json`, чтобы настроить инструкции и ссылки на нужные клиенты.
2. Убедитесь, что файлы доступны для чтения пользователем веб-сервера.

## 5. Настройка кнопки в Telegram

1. В дминке бота настраиваем параметры - Конфигурации бота - Прочее - Miniapp
2. Перезапустите бота. 
3. При необходимости задайте кастомную кнопку на миниапп через `@BotFather` (команда `/setmenu` -> Web App URL).

## 6. Конфигурация nginx

```nginx
server {
    listen 80;
    listen 443 ssl http2;
    server_name miniapp.example.com;

    ssl_certificate     /etc/letsencrypt/live/miniapp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/miniapp.example.com/privkey.pem;

    # Статические файлы мини-приложения
    root /var/www/remnawave-miniapp;
    index index.html;

    location = /miniapp/app-config.json {
        add_header Access-Control-Allow-Origin "*";
        try_files $uri =404;
    }

    location / {
        try_files $uri /index.html =404;
    }

    # Проксирование запросов к административному API
    location /miniapp/ {
        proxy_pass http://127.0.0.1:8080/miniapp/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Если нужно проксировать другие эндпоинты API, добавьте аналогичные location-блоки.
}
```

## 7. Конфигурация Caddy

```caddy
miniapp.example.com {
    encode gzip zstd
    root * /var/www/remnawave-miniapp
    file_server

    @config path /app-config.json
    header @config Access-Control-Allow-Origin "*"

    reverse_proxy /miniapp/* 127.0.0.1:8080 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
    }
}
```
Caddy автоматически выпустит сертификаты через ACME. Убедитесь, что порт 443 проброшен и домен указывает на сервер.

## 8. Проверка работы

1. Откройте мини-приложение прямо в Telegram или через браузер: `https://miniapp.example.com`.
2. В консоли разработчика убедитесь, что запрос к `https://miniapp.example.com/miniapp/subscription` возвращает JSON с данными подписки.
3. Проверьте, что ссылки из блока «Подключить подписку» открываются и копируются без ошибок.

## 9. Диагностика

| Симптом | Возможная причина | Проверка |
|---------|------------------|----------|
| Белый экран и ошибка 401 | Неверный `X-API-Key` или `WEB_API_ALLOWED_ORIGINS`. | Проверьте токен и заголовки запроса, перегенерируйте токен через `/tokens`. |
| Ошибка 404 на `/miniapp/subscription` | Прокси не пробрасывает запросы или API не запущено. | Проверьте лог nginx/Caddy и убедитесь, что бот запущен с `WEB_API_ENABLED=true`. |
| Mini App не открывается в Telegram | URL не соответствует HTTPS или отсутствует сертификат. | Обновите сертификаты и убедитесь, что домен доступен по HTTPS. |
| Нет ссылок подписки | Не настроена интеграция с RemnaWave или у пользователя нет активной подписки. | Проверьте `REMNAWAVE_API_URL/KEY` и статус подписки пользователя. |

После настройки вы сможете использовать мини-приложение в бот-меню и отправлять ссылку пользователям напрямую.
