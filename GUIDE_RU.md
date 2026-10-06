# PPLX Decider Review — 0.1.8

## Стабильная заметка и границы оценки

В режиме `bounded` повтор одинакового fallback-финала сохраняет исходную final-scoped заметку без нового main-review HTTP. Оценка из `pre_verify`, другой кандидат или native статус `requested/completed` по-прежнему помечаются как **исходный черновик (DRAFT)**. Это не подтверждение исправленного финала. Потеря receipt при TTL/goal/config fence не пополняет main-бюджет; один новый main-запрос возможен только в новом native turn. `Advisory` и child review имеют отдельные правила.

Для локальной привязки используется полный исходный текст, а не его обрезанная или redacted копия. Digest не отправляется reviewer и не записывается в audit. Даже изменение только хвоста ответа или скрываемого фрагмента не переносит прежнюю оценку на новый финал. Если tool evidence меняется после проверки кандидата, последний application fence под общей блокировкой оставляет исходный ответ без устаревшей заметки; повторного HTTP нет. Внешняя оценка всё ещё ограничена отфильтрованным excerpt, а не всем текстом.

Regression controls сохраняют положительные и отрицательные случаи: допустимый reservation перед main review, отказ повторного HTTP в том же turn, новый turn, отключение ветви до чтения ключа и сохранение отдельного goal-cap дочерних рекомендаций. Portable suite и native SDK config suite перекрываются; отдельные native integration/unittest cases не собираются portable pytest. Локальные проверки используют synthetic home и контролируемый транспорт, а не live Sol/PPLX. Source-публикация не обновляет установленные файлы и не подтверждает загрузку работающим процессом.

## История 0.1.7: одноразовая проверка до финала

Для `bounded` и meaningful main-goal проверенный RETRY/INSPECT вызывает один native user-nudge Sol. Sol самостоятельно проверяет факты, исправляет подтверждённые недочёты либо сообщает о неподтвердившихся замечаниях. PPLX не является источником истины или разрешением на действия. ACCEPT/API error/child/disabled/advisory не принуждают продолжение.

Legacy: без строгого native `all_finals=True` нужны tracked paths и integer `attempt=0`. Для разрешённых plain-text ответов требуется separately reviewed core с `agent.pre_verify_all_finals: true`; одного обновления плагина недостаточно. Core сохраняет `max_verify=0`, ограничивает feedback одним реальным ходом и запрещает spawn `delegate_task` на correction pass. Hooks по-прежнему шесть.

Main reservation создаётся атомарно до HTTP по owner/session/native-turn. Отдельный ограниченный JSON receipt не разделяет mutable review с cache или caller. После pass final-transform сохраняет модельный текст, не вызывает второй PPLX, а оценку явно помечает как **исходный черновик (DRAFT)**. Cache miss/TTL/config/goal fence не разрешает повторный запрос. Native `verification_pass_status=requested` означает запрос, не завершение; `completed` принимается только из native metadata. Без metadata статус неизвестен. Финальная заметка не содержит имитации нового ответа `Sol:`.

Историческая source-подготовка 0.1.7 и её focused SDK controls не являются приёмкой текущей 0.1.8. Первый streaming draft может показываться как interim. Публикация, установка и загрузка live-процессом проверяются отдельно; MockTransport не доказывает успешный ответ реального Sol/PPLX. Transport, секреты, redaction, thresholds/timeouts и SDK namespace policy сохраняются.

## История: изменение текста в 0.1.6

Заметки показывают вероятности понятными процентами и не объявляют ошибки установленными только по оценке PPLX. RETRY означает рекомендацию перепроверить результат; исправлять следует только подтверждённые недочёты. Отсутствие ответа reviewer описывается как отсутствие заключения PPLX, а не как отсутствие фактических проверок. Автоматического запуска субагента или универсального продолжения агентного цикла нет. Числовая политика и внешние маршруты не менялись.

## История проверки до переноса в 0.1.5

Полная ревизия текущего проекта охватила все 30 tracked files версии 0.1.4, commit `204d1099f34a5469e94f52d7a77c20c0283d4591`. Свежий offline native baseline: **154 passed, 5 skipped**, без failures/errors. Пять skips относятся к отсутствию Windows-привилегии создания symbolic links; прежний публичный CI 0.1.4 проверял их, но не доказывает проверку новой версии.

Исходный ZIP сохранён. SHA-256: `b38dac69c568a6e868b9a093b710ed56b3732f4d197921594c84d4edd0355da5`. Все 38 members прошли path/mode/size, CRC и syntax inspection. В сравнении с текущим checkout: 7 modified, 28 added, 3 identical; 20 current files не входят в ZIP. Это явно неполный review bundle, поэтому файлы текущего проекта не удаляются.

Исторические отчёты ZIP согласованы с его tests: 48 failures на исходнике, 137 passes после правки, 36 policy passes и повтор тех же 137. Это **чужие сохранённые отчёты**, не результаты наших запусков; их нельзя суммировать как новые независимые проверки. Manifest/checksums подтверждают целостность, не внешнюю аутентичность.

## Принципы выборочного переноса

- Не менять model/provider/endpoint, owner-local secret resolution, обязательный native redact, one-in-flight/no-retry, исходные native результаты и лимиты одной рекомендации/continue.
- Один validated config snapshot определяет cache identity и политику запроса; не подменять его повторным чтением более мягкого порога.
- Context fence должен быть снят вместе с goal/evidence и проверен до dispatch и применения. Нельзя принимать старые данные, просто пересняв новую revision после ожидания.
- Признаки неполноты `error/schema_errors` классифицируются **до scrub**; удаление sensitive fields не превращает ошибку в успех.
- Срок проверяется до дорогостоящей projection/hash; prefix-итерация словарей не заменяет общий node/UTF-8 budget.
- `choice` — winning option; `score` — probability-weighted average. Confidence не равна вероятности winner. Это следует из [официального skill](https://openrouter.ai/skills/openrouter-decisions) и [руководства OpenRouter](https://openrouter.ai/blog/tutorials/how-to-use-jev/). Допуски округления локальные, не гарантия точности провайдера.
- Не reinterpret произвольные `code/error_code` без подтверждённого API-контракта и не выдумывать child IDs, отсутствующие в native stop/receipt.
- Unicode replacement допускается только в egress-копии; исходный logical JSON остаётся неизменным.

Каждая production-правка проверена через focused RED→GREEN и positive controls. Локальная итоговая приёмка: native 221 cases — 216 passed/5 skipped; standalone 213 cases — 208 passed/5 skipped; failures/errors — 0. Пять skips связаны с Windows symlink error 1314. Наборы перекрываются; receipts/JUnit и source hashes сверены отдельно, а не выведены из названий GREEN или отчётов исходного архива. Результат CI для выбранного commit проверяйте в GitHub Actions; эти локальные counts не являются live API или cross-platform доказательством.

## Повторить проверки

Сначала внешним доверенным инструментом проверьте archive SHA, полный список members и контрольные суммы. Не запускайте код/скрипт произвольного полученного ZIP ради его первого доказательства безопасности. Приращения с кодом должны быть прочитаны заранее; Python audit hook — supervision, не OS sandbox.

После source review для unit tests, без SDK/key:

```bash
python -m venv .venv
# Активируйте эту среду способом своей ОС.
python -m pip install -r requirements-dev.txt
python -B scripts/run_tests.py
```

Зависимости остаются pytest 9.1.1 и httpx 0.28.1; сеть требуется pip, но не последующим тестам. Runner создаёт synthetic home и отдельно напечатает retained scratch/JUnit/summary. Он не удаляет старые roots. Запуск тестов пишет только тестовые данные; не переносите сюда credentials или рабочие профили.

С verified checkout Hermes и совместимым interpreter/dependencies:

```bash
python -B scripts/run_tests.py --core PATH_TO_REVIEWED_HERMES_CORE
```

Default unit и explicit native config tests — разные уровни доказательства. Настоящие PluginContext/namespace reader/validator нельзя заменять permissive SDK mocks. CLI quiet stdout и live model behavior этим не доказываются.

## Установка и настройки — отдельно

Получение source из GitHub не означает installation или reload. Выбирайте полный commit SHA с проверенным CI, затем отдельно выполните native scanner/Doctor перед разрешённой установкой. Команды — в README; старый commit 0.1.4 не включает новые правки. Для каждой устанавливаемой ревизии требуется отдельное решение по актуальному CAUTION.

Не запускайте `__init__.py` как CLI. Не меняйте живой config вручную и не заменяйте целиком профиль. Используйте supported `hermes -p PROFILE config set ...` для согласованных ключей. Ключ `OPENROUTER_API_KEY` предоставляется только штатным scoped secret resolver владельца, не YAML/исходниками/чатом/архивом.

Пилот — advisory после согласования external egress. Scanner CAUTION требует отдельного решения для точного candidate; прошлое согласие не переносится автоматически. Установка/настройки/reload — отдельные проверки. Не перезапускайте действующий gateway/Desktop ради проверки source-пакета.

## Ограничения

- Policy snapshot устраняет доказанное смешение digest/thresholds, но отсутствие atomic native revision не позволяет заявлять обнаружение **всех** не наблюдавшихся переходных ABA настроек.
- Deadline кооперативный: blocking network phase/DNS/OS I/O может жить дольше срока. Worker не убивается; слот удерживается до natural completion.
- Полная локальная identity читает весь ответ линейно кусками по 4096 символов. Ограниченный временный буфер не означает hard CPU/wall-clock containment.
- Sync generation не exact receipt→child mapping; arbitrary late stop нельзя привязать к исполнению, если native producer не несёт identity. Ambiguous event не доказательство успеха.
- Native redaction не гарантирует распознавания любого свободнотекстового секрета. Egress требует самостоятельного решения владельца.
- TTL/eviction/restart и ограниченный cache — не durable spending/retry ledger. Вероятности PPLX не facts и не tool permission.
- Host reload/abandon, cross-owner switches mid-callback, межпроцессная ротация журнала и live inference не объявляются проверенными только на основании unit suite.

## Сохранение и откат

Исходный ZIP, baseline и прежние FAIL receipts сохраняются. Откат code derivative выполняется отдельной обратимой операцией с сохранением последующих правок; это не откат installed copy. Не применять `git reset --hard` или архивные `fixes.patch` к чужой/грязной копии вслепую. Hermes core, установленные плагины, profiles и процессы не входят в эту source-ревизию.
