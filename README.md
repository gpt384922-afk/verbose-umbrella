# YC Hunter

Асинхронный Telegram-бот для поиска нужных публичных IP в Yandex Cloud.

Бот подключает аккаунты Yandex Cloud, смотрит организации, биллинг, облака и каталоги, после чего запускает "хант": создает или пересоздает облака, поднимает Compute VM с публичным IP и проверяет, подходят ли эти IP под заданные префиксы.

Важно: проект работает с реальными ресурсами Yandex Cloud. Он может создавать, удалять и пересоздавать облака, виртуальные машины, диски и публичные IP на VM. Запускайте его только на тех аккаунтах и организациях, где вы понимаете последствия.

## Что Нужно Заранее

1. Docker и Docker Compose.
2. Telegram-бот, созданный через BotFather.
3. Токен Telegram-бота.
4. Ваш Telegram chat ID или список разрешенных chat ID.
5. OAuth token от Yandex Cloud для аккаунтов, которые будете добавлять в бота.
6. Активный биллинг в Yandex Cloud.

Если Docker не установлен, проще всего поставить Docker Desktop и запускать проект через `docker compose`.

## Быстрый Запуск

1. Перейдите в папку проекта:

```bash
cd /Users/flow/ychunter
```

2. Создайте файл настроек:

```bash
cp .env.example .env
```

3. Откройте `.env` и заполните минимум эти поля:

```env
TG_TOKEN=токен_вашего_telegram_бота
TG_CHAT_ID=ваш_telegram_chat_id
```

Если доступ нужен нескольким людям, вместо `TG_CHAT_ID` можно использовать:

```env
TG_CHAT_IDS=123456789,987654321
```

4. Запустите проект:

```bash
docker compose up --build
```

Для запуска в фоне:

```bash
docker compose up --build -d
```

5. Откройте Telegram, напишите боту:

```text
/start
```

После этого появится главное меню.

## Как Пользоваться Ботом

Обычный сценарий такой:

1. Нажмите `Добавить аккаунт`.
2. Введите понятное название аккаунта.
3. Вставьте OAuth token от Yandex Cloud.
4. Email, password и secret можно заполнить, если они нужны вам для учета. Если не нужны, отправьте `/skip`.
5. Подтвердите сохранение аккаунта.
6. Нажмите `Синхронизировать`, чтобы бот подтянул организации, биллинг, облака и каталоги.
7. Нажмите `Запустить хант`.
8. Выберите аккаунт или несколько аккаунтов.
9. Выберите организации.
10. Выберите IP-префиксы кнопками:
    - `158.160`, `84.201`, `87.250.247-254` и `77.88.21` — для всех операторов;
    - `51.250` — только для Мегафон.
11. Выберите, сколько подходящих IP нужно поймать за хант.
12. На экране подтверждения проверьте блок существующих IP. Если бот нашел IP из известных префиксов, он покажет их перед запуском. Эти IP и их облака не удаляются автоматически, даже если префикс не выбран целью текущего ханта.
13. Нажмите `Запустить`.

Прогресс можно смотреть в разделе `Активные ханты`.

## Что Значат Разделы В Боте

`Аккаунты` показывает добавленные Yandex Cloud аккаунты. Внутри карточки аккаунта видны организации, облака, статусы и биллинг.

`Добавить аккаунт` запускает пошаговое добавление OAuth token и дополнительных данных.

`Синхронизировать` обновляет сведения из Yandex Cloud: организации, биллинг, облака и каталоги.

`Запустить хант` запускает поиск IP по заданным префиксам.

`Активные ханты` показывает текущие и недавние задачи, количество найденных IP, время работы, сколько IP уже перебрано и общие рабочие метрики.

`Филиалы` доступен только владельцу основного бота из `TG_CHAT_ID` или `TG_CHAT_IDS`. В этом разделе можно подключить дополнительного Telegram-бота как отдельный филиал: указать название, token бота и Telegram chat ID владельца филиала. После сохранения филиальный бот запускается в этом же процессе.

У владельца филиала свое рабочее пространство: свои Yandex Cloud аккаунты, свои организации и свои ханты. Он не видит аккаунты основного бота и других филиалов, а также не может добавлять или отключать филиалы.

## Как Работает Хант

Перед стартом бот проверяет выбранные аккаунты и организации:

- получает организации аккаунта;
- получает активные биллинговые аккаунты;
- проверяет облака и каталоги;
- удаляет заблокированные облака;
- удаляет облака без видимой активной привязки к биллингу;
- проверяет, что в организации есть активный оплаченный cloud или свободный слот для его создания.

По умолчанию hunter держит до `HUNT_PARALLEL_CLOUD_COUNT=5` активных cloud'ов на выбранную организацию и запускает поиск в них одновременно. Внутри каждого cloud он создает batch VM, проверяет публичные IP и удаляет VM с неподходящими адресами. Если нужный IP не найден, cloud удаляется, после освобождения слота создается следующий cloud и поиск продолжается там.

Переменная ниже остается лимитом для количества нужных VM/сохраненных успешных cloud'ов в одном хантe:

```env
HUNT_CLOUD_TARGET_COUNT=5
```

Для каждого проверяемого cloud бот генерирует один SSH-ключ и параллельно создает до `8` Compute VM:

- зоны только `ru-central1-a` и `ru-central1-d`;
- если в folder нет subnet в этих зонах, бот создает VPC network и subnet'ы;
- Debian 12;
- HDD boot disk `5-80 GB`;
- AMD Zen 4 / Intel Ice Lake / Intel Cascade Lake, от `2` vCPU, guaranteed fraction `5-100%`;
- RAM от `0.5 GB`;
- preemptible VM;
- login `user`;
- проверка появления публичного IP каждые `5` секунд.

VM с неподходящим IP удаляется сразу после проверки. Если одна из VM получила нужный IP, бот оставляет эту VM и облако, удаляет остальные VM из batch и отправляет в Telegram IP, VM id, cloud/folder и приватный SSH-ключ. Если облако не дает ни одного нужного IP после batch из `8` VM, бот:

1. удаляет облако;
2. ждет, пока удаление завершится;
3. создает новое облако;
4. привязывает биллинг;
5. генерирует новый SSH-ключ и продолжает поиск через новый VM batch.

Если включить `HUNT_ORGANIZATION_ROTATION_ENABLED=true`, после `HUNT_ORGANIZATION_ROTATION_CLOUD_MISS_COUNT=5` последовательных промахнувшихся cloud'ов в одной организации бот сначала дожидается удаления всех cloud'ов старой организации, затем через Selenium создает новую организацию в `center.yandex.cloud`, создает в ней первое облако обычным API-flow и продолжает хант с той же привязкой биллинга.

Если бот уже поймал IP в нужном префиксе, эта VM и ее облако защищаются от автоматического удаления.

Если уже существующий IP входит в один из известных префиксов `158.160`, `84.201`, `51.250`, `87.250.247-254`, `77.88.21`, бот предупреждает об этом перед запуском и не трогает его облако. Такой IP не засчитывается как успешная находка текущего ханта, если его префикс не выбран пользователем.

## Настройки `.env`

Основные настройки:

```env
TG_TOKEN=1234567890:AA...
TG_CHAT_ID=123456789
DATABASE_DSN=postgresql+asyncpg://postgres:postgres@postgres:5432/ychunter
LOG_LEVEL=INFO
APP_ENV=production
```

`TG_TOKEN` - токен Telegram-бота.

`TG_CHAT_ID` - один разрешенный Telegram chat ID.

`TG_CHAT_IDS` - несколько разрешенных chat ID через запятую. Если он задан, `TG_CHAT_ID` можно не использовать.

Филиальные боты не настраиваются через `.env`. Их token и owner chat ID добавляются в меню `Филиалы` внутри основного бота и сохраняются в PostgreSQL.

`DATABASE_DSN` - строка подключения к PostgreSQL. При запуске через `docker compose` можно оставить стандартное значение.

Настройки Yandex Cloud API и повторов:

```env
YC_MAX_CONCURRENCY=8
YC_REQUEST_RETRIES=5
YC_RETRY_BASE_DELAY=0.8
YC_RETRY_MAX_DELAY=15
```

Настройки удаления и ожидания облаков:

```env
YC_CLOUD_DELETE_POLL_SECONDS=300
YC_CLOUD_DELETE_TIMEOUT_SECONDS=259200
YC_CLOUD_SLOT_WAIT_POLL_SECONDS=30
YC_CLOUD_SLOT_WAIT_TIMEOUT_SECONDS=259200
```

Настройки стратегии поиска:

```env
HUNT_CLOUD_TARGET_COUNT=5
HUNT_WORKER_COUNT=8
HUNT_VM_BATCH_SIZE=8
HUNT_VM_DELETE_DELAY_SECONDS=1
HUNT_VM_POLL_SECONDS=5
HUNT_VM_POLL_TIMEOUT_SECONDS=180
HUNT_VM_ZONES=ru-central1-a,ru-central1-d
HUNT_VM_SUBNET_CIDR_BLOCKS=10.10.0.0/24,10.20.0.0/24
HUNT_VM_IMAGE_FAMILY=debian-12
HUNT_VM_IMAGE_FOLDER_ID=standard-images
HUNT_VM_PLATFORM_ID=standard-v4a
HUNT_VM_DISK_TYPE_ID=network-hdd
HUNT_VM_DISK_SIZE_GB=10
HUNT_VM_CORES=2
HUNT_VM_CORE_FRACTION=20
HUNT_VM_MEMORY_GB=1
HUNT_VM_USERNAME=user
HUNT_PARALLEL_CLOUD_COUNT=5
HUNT_ORGANIZATION_ROTATION_ENABLED=false
HUNT_ORGANIZATION_ROTATION_CLOUD_MISS_COUNT=5
YC_CENTER_URL=https://center.yandex.cloud/
YC_CENTER_AUTO_INSTALL_BROWSER=true
# Для Anty/готового браузерного профиля удобнее указать debugging address профиля.
# YC_CENTER_CHROME_DEBUGGER_ADDRESS=127.0.0.1:9222
# Либо отдельный Chrome profile. В docker compose по умолчанию используется /data/chrome-profile.
YC_CENTER_CHROME_USER_DATA_DIR=/data/chrome-profile
YC_CENTER_CHROME_BINARY=/usr/bin/chromium
YC_CENTER_SELENIUM_HEADLESS=false
# На VPS с отдельным Chrome profile обычно лучше закрывать браузер после Selenium-действия.
YC_CENTER_SELENIUM_QUIT=true
YC_CENTER_WAIT_SECONDS=90
YC_CENTER_ORG_NAME_PREFIX=ycbot-org
```

Docker-образ сам устанавливает Chromium, chromedriver, Xvfb и системные библиотеки для Selenium. По умолчанию Cloud Center открывается не в headless-режиме, а в виртуальном дисплее Xvfb: это ближе к обычному браузеру и лучше подходит для `center.yandex.cloud`. При запуске без Docker бот дополнительно проверяет наличие браузера и, если `YC_CENTER_AUTO_INSTALL_BROWSER=true`, на Debian/Ubuntu попытается поставить пакеты через `apt-get`. Для такого auto-install процесс должен запускаться от root; если прав нет, бот продолжит работу, но Selenium-кнопки вернут ошибку до ручной установки браузера/Xvfb.

Прокси для Cloud Center задается отдельно в карточке аккаунта кнопкой `Указать center proxy`. Он применяется только к Selenium/cookies/созданию организаций и не используется для Yandex Cloud API, чтобы синхронизация и хант не падали из-за антикапча-прокси.

Legacy-настройки старого VPC address flow больше не управляют основным хантингом:

```env
YC_DEFAULT_ZONES=ru-central1-a,ru-central1-b,ru-central1-d
HUNT_CYCLES_PER_CLOUD=4
HUNT_IPS_PER_CYCLE=2
HUNT_POLL_MIN_SECONDS=3
HUNT_POLL_MAX_SECONDS=5
HUNT_POLL_TIMEOUT_SECONDS=30
```

Фоновая чистка:

```env
CLEANUP_INTERVAL_SECONDS=180
```

Анимация для уведомлений о найденном IP:

```env
TG_MATCH_MESSAGE_EFFECT_ID=5427168083074628963
```

Если не хотите использовать эффект, можно убрать переменную или оставить пустой.

## Команды Для Работы

Запустить с пересборкой:

```bash
docker compose up --build
```

Запустить в фоне:

```bash
docker compose up --build -d
```

Посмотреть логи:

```bash
docker compose logs -f ycbot
```

Остановить:

```bash
docker compose down
```

Перезапустить только бота:

```bash
docker compose restart ycbot
```

Полностью удалить контейнеры и базу PostgreSQL:

```bash
docker compose down -v
```

Последняя команда удалит volume `postgres_data`, то есть сохраненные аккаунты, ханты и историю.

## Запуск Без Docker

Такой вариант нужен редко, но он возможен.

1. Установите Python 3.11.
2. Поднимите PostgreSQL отдельно.
3. Создайте виртуальное окружение:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

4. Установите зависимости:

```bash
pip install -r requirements.txt
```

5. Создайте `.env`:

```bash
cp .env.example .env
```

6. В `.env` укажите `DATABASE_DSN`, который ведет на вашу PostgreSQL.
7. Запустите бота:

```bash
python -m ycbot.bot
```

## Структура Проекта

```text
ycbot/
  bot/
    app.py              Telegram-приложение
    handlers.py         обработчики кнопок и сообщений
    keyboards.py        inline-клавиатуры
    notifications.py    уведомления о найденных IP
    ui.py               оформление сообщений
  core/
    hunter.py           основной движок поиска IP
    state_manager.py    состояние задач и облаков в памяти
    scheduler.py        запуск, остановка и контроль хантов
  yc/
    client.py           общий клиент Yandex Cloud API
    clouds.py           организации, биллинг, облака и каталоги
    compute.py          виртуальные машины Compute Cloud
    vpc.py              публичные VPC IP и subnet lookup
  db/
    base.py             база SQLAlchemy
    enums.py            статусы
    models.py           модели таблиц
    repositories.py     запросы к базе
    session.py          подключение к PostgreSQL
  utils/
    retry.py            повтор запросов с backoff
    logger.py           логирование
  app_context.py        сборка зависимостей приложения
  config.py             настройки из .env
  bot.py                точка входа
```

## Что Хранится В Базе

PostgreSQL хранит:

- добавленные аккаунты;
- организации;
- биллинговые аккаунты;
- облака;
- запущенные ханты;
- найденные совпадения IP;
- историю и статусы.

Runtime-состояние активной работы держится в памяти процесса. После перезапуска бот поднимется заново, а постоянные данные останутся в PostgreSQL.

## Надежность И Чистка

Внутри есть фоновая чистка. Она периодически:

- синхронизирует облака;
- убирает зависшие или лишние адреса;
- обновляет состояние удаленных и проблемных ресурсов в базе;
- помогает не оставлять мусор после неудачных операций.

Интервал задается через:

```env
CLEANUP_INTERVAL_SECONDS=180
```

## Частые Проблемы

Бот не отвечает в Telegram:

- проверьте `TG_TOKEN`;
- проверьте, что ваш chat ID указан в `TG_CHAT_ID` или `TG_CHAT_IDS`;
- посмотрите логи командой `docker compose logs -f ycbot`.

Бот пишет, что аккаунтов нет:

- добавьте аккаунт через кнопку `Добавить аккаунт`;
- после добавления нажмите `Синхронизировать`.

Организации или облака не появились:

- проверьте OAuth token;
- убедитесь, что у аккаунта есть доступ к Yandex Cloud;
- убедитесь, что биллинг активен.

Хант не стартует:

- в выбранной организации может не быть активного оплаченного cloud или свободного cloud-слота;
- облака могут быть в процессе удаления;
- биллинг может еще не отображаться в API Yandex Cloud;
- проверьте логи.

База пропала после перезапуска:

- обычный `docker compose down` базу не удаляет;
- `docker compose down -v` удаляет volume PostgreSQL и все сохраненные данные.

## Точка Входа

В Docker бот запускается так:

```bash
python -m ycbot.bot
```

Та же команда используется для ручного запуска без Docker.
