# Дизайн Ханта Через Compute VM

## Цель

Заменить старый способ перебора публичных IP через VPC address reservation на создание виртуальных машин в Yandex Compute Cloud. Хант по-прежнему готовит `5` облаков на выбранную организацию, но внутри каждого нового облака ищет подходящий IP через `8` параллельно созданных VM. Если подходящий IP найден, бот отправляет в Telegram все данные для доступа, включая приватный SSH-ключ. Если ни одна из `8` VM не дала нужный IP, бот удаляет облако и создает замену.

## Выбранный Подход

Используем параллельный batch из `8` VM на облако.

Для каждого облака:

1. Создать или подготовить облако и folder так же, как сейчас.
2. Сгенерировать один SSH keypair для этого облака.
3. Найти subnet в разрешенных зонах `ru-central1-a` и `ru-central1-d`.
4. Одновременно отправить `8` запросов на создание VM, распределяя зоны только между `a` и `d`.
5. Каждые `5` секунд проверять каждую VM, пока у нее не появится публичный NAT IP или не истечет timeout.
6. VM с неподходящим IP удалить сразу после проверки.
7. Если VM получила IP из выбранного префикса, сохранить match, оставить эту VM и облако, удалить остальные неподходящие/лишние VM, отправить Telegram-уведомление с IP, VM id, cloud/folder и приватным SSH-ключом.
8. Если все `8` VM завершились без match, удалить все VM и удалить облако; после удаления облака scheduler создает новое облако в свободный слот и повторяет batch.

Этот подход быстрее последовательного создания VM и соответствует требованию "8 VM параллельно", но требует строгого cleanup, чтобы не оставлять лишние инстансы и диски.

## Конфигурация VM

VM создаются с конфигурацией, соответствующей скринам:

- зоны: только `ru-central1-a` и `ru-central1-d`;
- platform: AMD Zen 4, `platformId=standard-v4a`;
- vCPU: `2`;
- guaranteed vCPU fraction: `20`;
- RAM: `1 GB`;
- boot disk: `HDD`, `10 GB`, `autoDelete=true`;
- image: Ubuntu 24.04 LTS через latest image family, по умолчанию `ubuntu-2404-lts`;
- scheduling policy: `preemptible=true`;
- public IPv4: через `primaryV4AddressSpec.oneToOneNatSpec.ipVersion=IPV4`;
- login: `user`;
- SSH access: публичный ключ передается в metadata VM как `ssh-keys: user:<public-key>`.

Ключ не создается в организации. Код генерирует keypair на уровне работы с конкретным облаком и использует публичный ключ в metadata всех `8` VM этого облака. Приватный ключ хранится только в памяти до успешного match и отправляется пользователю в Telegram при найденном IP.

## API И Клиенты

Добавляем новый YC клиент `ycbot/yc/compute.py`:

- `ComputeApi.get_latest_image_by_family(folder_id, family)`;
- `ComputeApi.create_instance(...)`;
- `ComputeApi.get_instance(instance_id)`;
- `ComputeApi.delete_instance(instance_id)`;
- `ComputeApi.wait_for_external_ip(instance_id, poll_seconds, timeout_seconds)`;
- helper для извлечения публичного IP из `networkInterfaces[].primaryV4Address.oneToOneNat.address`.

Расширяем `VpcApi` read-only методом `list_subnets(folder_id)` и структурой `Subnet(id, zone_id)`, потому что Compute VM требует `subnetId`, а subnet должен совпадать с зоной VM.

Если в folder нет subnet в `ru-central1-a` или `ru-central1-d`, облако считается непригодным для VM-ханта: бот помечает облако failed, удаляет его и создает замену. Создание сетей и subnet в этом дизайне не добавляем, чтобы не расширять blast radius.

## Изменения В Hunter

`HunterEngine._hunt_cloud_once` перестает создавать VPC addresses. Вместо этого он запускает один VM batch на cloud:

- ставит lifecycle `HUNTING`;
- генерирует SSH keypair;
- создает `8` VM параллельно;
- сохраняет каждую VM в существующий адресный реестр как проверяемый ресурс, используя `vm:<instance_id>` в поле `address_id`;
- polling выполняется с фиксированным интервалом `5` секунд;
- каждый полученный публичный IP записывается в runtime state через `add_checked_ip`;
- match создается через существующий `HuntMatch`, где `address_id` для нового способа фактически становится `vm:<instance_id>`.

Существующая модель `AddressRecord` остается, но ее смысл расширяется до "созданный ресурс, давший публичный IP". Это минимизирует миграции и сохраняет текущие экраны прогресса. В UI текст "address" заменяем на "resource" или "vm", чтобы не вводить пользователя в заблуждение.

## Cleanup

Cleanup должен быть консервативным:

- неподходящая VM удаляется сразу после получения IP;
- VM без IP после timeout удаляется;
- при найденном match сохраняется только matched VM, все остальные VM из batch удаляются;
- если batch полностью промахнулся, cloud удаляется после удаления VM;
- boot disk создается с `autoDelete=true`, поэтому отдельная чистка дисков не нужна;
- фоновый cleanup старых `AddressRecord` переключается с VPC `delete_address` на Compute `delete_instance` для записей, созданных новым VM-хантом.

Чтобы различать старые VPC address records и новые VM records без схемной миграции, `AddressRecord.address_id` для VM будет иметь префикс `vm:` при записи в БД, а API-клиент будет получать чистый instance id после удаления префикса. Старые записи без префикса продолжают чиститься через VPC API.

## Telegram Уведомление

`MatchNotification` расширяем полями:

- `resource_id`;
- `resource_type`, значение `vm`;
- `ssh_username`, значение `user`;
- `ssh_private_key`;
- `zone_id`.

Сообщение о match должно содержать:

- IP;
- prefix;
- account;
- organization;
- cloud id/name;
- folder id;
- VM id;
- zone;
- login `user`;
- приватный SSH-ключ в `<pre>` блоке.

Если Telegram не принимает длинное форматированное сообщение, fallback отправляет сначала обычное сообщение с IP/VM, затем отдельное сообщение с ключом без message effect.

## Настройки

Добавляем настройки с безопасными default:

- `YC_COMPUTE_INSTANCE_URL=https://compute.api.cloud.yandex.net/compute/v1/instances`;
- `YC_COMPUTE_IMAGE_URL=https://compute.api.cloud.yandex.net/compute/v1/images`;
- `YC_VPC_SUBNET_URL=https://vpc.api.cloud.yandex.net/vpc/v1/subnets`;
- `HUNT_VM_BATCH_SIZE=8`;
- `HUNT_VM_POLL_SECONDS=5`;
- `HUNT_VM_POLL_TIMEOUT_SECONDS=180`;
- `HUNT_VM_ZONES=ru-central1-a,ru-central1-d`;
- `HUNT_VM_IMAGE_FAMILY=ubuntu-2404-lts`;
- `HUNT_VM_IMAGE_FOLDER_ID=standard-images`;
- `HUNT_VM_PLATFORM_ID=standard-v4a`;
- `HUNT_VM_DISK_TYPE_ID=network-hdd`;
- `HUNT_VM_DISK_SIZE_GB=10`;
- `HUNT_VM_CORES=2`;
- `HUNT_VM_CORE_FRACTION=20`;
- `HUNT_VM_MEMORY_GB=1`;
- `HUNT_VM_USERNAME=user`.

Старые настройки `HUNT_CYCLES_PER_CLOUD` и `HUNT_IPS_PER_CYCLE` больше не управляют основным способом ханта, но могут остаться для совместимости. README помечает их как legacy.

## Preflight И Защита Существующих IP

Preflight для существующих VPC addresses остается read-only, потому что он защищает уже найденные публичные IP из известных префиксов. Дополнительно preflight проверяет существующие Compute VM в folder и смотрит их внешний NAT IP, чтобы не удалить облако с уже существующей VM в известном префиксе.

Runtime-защита перед работой с облаком также сканирует:

- VPC addresses;
- Compute instances.

Если найден любой IP из известных префиксов, облако не используется для batch и не удаляется автоматически.

## Ошибки И Лимиты

Если часть из `8` VM не создалась, бот продолжает проверять созданные VM. Если ни одна созданная VM не дала match, облако удаляется.

Если Yandex Cloud возвращает quota/limit ошибку на создание VM, ошибка логируется в progress cloud. Уже созданные VM очищаются. Облако удаляется и замещается только после cleanup.

Если нет subnet в зоне `a` или `d`, облако удаляется как непригодное. Если в folder есть subnet только в одной из двух зон, все `8` VM создаются в доступной разрешенной зоне.

Остановка ханта отменяет pending polling tasks, удаляет неподходящие VM из текущих batch и не удаляет VM, которая уже была сохранена как match.

## Тесты

Добавляем focused tests:

- `ComputeApi` строит payload VM с `standard-v4a`, `2` cores, `20` core fraction, `1 GB`, `network-hdd`, `10 GB`, `preemptible=true`, metadata `ssh-keys`;
- `ComputeApi` извлекает внешний IP из `primaryV4Address.oneToOneNat.address`;
- `HunterEngine` создает ровно `8` VM параллельно для cloud batch;
- неподходящие VM удаляются, matched VM сохраняется;
- при полном промахе удаляются VM и затем облако;
- Telegram notification содержит login `user`, VM id и приватный SSH-ключ;
- preflight учитывает существующие Compute VM с известными префиксами;
- cleanup различает legacy VPC address records и новые `vm:` records.

Ручная проверка:

1. Запустить хант на тестовой организации.
2. Убедиться, что создается `5` облаков.
3. В каждом новом облаке увидеть batch до `8` VM только в `ru-central1-a`/`ru-central1-d`.
4. Проверить, что неподходящие VM удаляются после получения IP.
5. Дождаться match и проверить Telegram-сообщение: IP, VM, cloud/folder, login `user`, приватный SSH-ключ.
6. Подключиться: `ssh -i <key-file> user@<ip>`.
