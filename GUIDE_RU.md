# Decision Review — 0.2.0

ID: **`decision-review`**. Русские заметки: **`DECISIONS ACCEPT`**, **`DECISIONS RETRY`**, **`DECISIONS INSPECT`**; продукт — **Decision Review**. Default reviewer — точный **`openai/gpt-6-luna-decisions`**, но имя плагина не зависит от модели или её производителя.

## Что означает оценка

Плагин добавляет typed Decisions-оценку отдельно от исходного результата. Вероятности — мнение reviewer по ограниченным предоставленным данным, не установленные факты, доказательство выполнения или разрешение на действия. `verified=true` подтверждает только валидный transport/typed contract. При отсутствии валидного ответа говорится «заключение Decision Review отсутствует»; это не утверждение, что фактические проверки вообще не проводились.

RETRY рекомендует основному агенту самостоятельно сверить требования и реальные свидетельства. Исправлять следует только подтверждённые недочёты. Плагин не выполняет такую сверку или исправления сам, не запускает субагентов, не делает HTTP retry и не обещает автоматического corrective loop. Заметка final-transform не запускает новый цикл работы и не имитирует новый ответ основной модели.

## Модель меняется одной настройкой

Plugin-relative ключ — **`reviewer_model`**, полный путь — `plugins.entries.decision-review.settings.reviewer_model`. Это не main model, delegation или generative `auxiliary.review`. Зарезервированный ключ `model` не alias; core namespace guard сохраняется.

```bash
hermes -p PROFILE config set plugins.entries.decision-review.settings.reviewer_model openai/gpt-6-luna-decisions
hermes -p PROFILE config get plugins.entries.decision-review.settings.reviewer_model
```

Замените `PROFILE` только на разрешённый профиль: текущий перенос касается **default**, не всех профилей. Для следующей совместимой Decisions-модели меняется только значение `reviewer_model`, не исходники. Provider остаётся `openrouter`, endpoint — **`https://openrouter.ai/api/alpha/decisions`**. Не используйте chat model/API или прямой vendor endpoint вместо Decisions; fallback на другой маршрут/модель не добавляется. Допустимый формат ID не доказывает серверную поддержку модели.

Порог ACCEPT 0.8 и RETRY 0.65, остальные диапазоны и бюджеты сохраняются. После смены модели требуется **отдельная калибровка**, а не автоматический перенос выводов о качестве. Превосходство Luna не проверено. Saved config — не доказательство adoption открытым backend; reload и платный запрос требуют собственных разрешений.

## Порядок миграции identity

Полная процедура и откат — [docs/migration-0.2.0.md](docs/migration-0.2.0.md).

- Сохраните old source и прежние собственные settings. Старый ID — `pplx-decider-review`, новый — `decision-review`.
- **Отключите OLD до включения NEW.** Иначе возможны двойные callbacks и расходы. Native disabled state и `settings.enabled=false` нужны вместе; запись config не доказывает выгрузку callbacks из работающего процесса. Не активируйте NEW, пока actual OLD не отключён; owner reload отдельно, не автоматически.
- Через штатный scoped config writer перенесите прежние значения в `plugins.entries.decision-review.settings`, без замены целого YAML или копирования окружения. Сохраняются mode, пороги, timeout/cache/log/retry budgets и branch toggles. Единственное целевое изменение настройки — `reviewer_model=openai/gpt-6-luna-decisions`; temporary disabled flags нужны для безопасной стадии переключения.
- После разрешённых scanner/Doctor/config checks и подтверждённого отсутствия active OLD восстанавливается прежнее состояние enabled у NEW. Если старый плагин был выключен, новый не включайте самовольно. Saved/readback/loaded/executed — разные стадии.
- Новый журнал — **`plugin-data/decision-review/reviews.jsonl`**. Старый **`plugin-data/pplx-decider-review`** оставляется на месте, без переноса, удаления, исправления исторических model strings или pruning.

Публичный репозиторий по-прежнему **https://github.com/sbrejnev988-coder/pplx-decider-review**. ID manifest не переименовывает GitHub repo. Rename/push, installation, live profile changes и all-profile activation этим source-пакетом не заявляются.

## Native callbacks: поддерживаемая граница

Шесть hooks сохранены: `pre_llm_call`, `transform_tool_result`, `subagent_start`, `subagent_stop`, `pre_verify`, `transform_llm_output`. Public SPI, core flags и native поля **не переименовываются**, Hermes core/SDK/PM не меняются.

Legacy `pre_verify` с tracked paths и integer `attempt=0` может передать bounded `action=continue`, если host действительно вызывает этот callback и обрабатывает рекомендацию. Без этого условия плагин не принуждает продолжение. Advisory, disabled, child, ACCEPT и API error не создают такой nudge.

**All-finals/no-edit corrective loop не является подтверждённой возможностью выбранного стандартного SDK.** Producers `all_finals` и `verification_pass_status` в нём не обнаружены. Plugin callback оставляет совместимые параметры на будущее, но ручная передача kwargs или MockTransport не заменяет producer proof. Не патчите core, не создавайте core fork и не изменяйте public wrappers ради этой функции.

Main reservation резервируется до HTTP по owner/session/native-turn. В `bounded` повтор того же fallback-финала сохраняет исходную заметку; pre-verify receipt, другой кандидат либо переданные native `requested/completed` означают **исходный DRAFT**, не оценку исправленного финала. TTL/goal/config fence и потеря receipt не пополняют main-бюджет; новый запрос допускается лишь новым native turn в удерживаемом state. Если metadata отсутствует, статус остаётся `unknown`; `requested` не означает завершения, `completed` принимается только из metadata и не доказывает устранение каждого замечания.

Локальная identity использует полный исходный ответ, не redacted/lossy projection. Позднее изменение evidence блокирует stale decoration под общей блокировкой без нового HTTP. Egress ограничен excerpt, поэтому полнота внешней оценки не обещается. Первый streaming draft может показываться как interim.

Sync/async provenance, родительский goal-cap, оригинальные statuses/evidence и collision envelope сохраняются. Legacy JSON-ключ **`pplx_review`** — plugin-owned контракт совместимости; он не выбирает модель и не требует core patch. Внутренние `PPLX_TEST_NATIVE_CORE`/`PPLX_TEST_PRODUCTION_ROOT` сохранены для старых test runners; это не пользовательское имя продукта.

## Проверки и ограничения

0.2.0 source и тестовые pins подготовлены. Full suite, сеть, платный canary и живой профиль в этой подготовке не использовались. Объединённые проверки относятся к следующему разрешённому этапу; подготовленные tests не являются выполненными tests.

Offline fixture возвращает **выбранную в запросе `request['model']`**, не фиксированный default. `provider='Synthetic OpenAI'` — явно синтетическая постоянная метка и не вывод о настоящем upstream по slug. Сохранены negative model/response contracts, zero key/HTTP/retry, native namespace/marker и DRAFT controls; added регрессия отдельно проверяет нетронутый legacy log root. Исторические пять Windows symlink skips не удалены; новые counts не выдумываются.

После отдельно разрешённой подготовки зависимостей:

```bash
python -B scripts/run_tests.py
# Только с совместимым выбранным interpreter и настоящим проверенным SDK:
python -B scripts/run_tests.py --core PATH_TO_REVIEWED_HERMES_CORE
```

Runner создаёт synthetic home, запрещает сеть и credential-file I/O, не удаляет старые roots и печатает retained JUnit/summary. Default unit и native config tests перекрываются и не суммируются. Native one-pass unittest собирается отдельно и требует producer-возможностей для своих all-finals cases; сохранение его assertions не является доказательством поддержки стандартным SDK. Не добавляйте чужие site-packages и не подменяйте настоящий PluginContext/validator permissive mocks.

- State/cache/receipt process-local и ограничены: TTL/eviction/restart — не durable spending ledger и не гарантия обнаружения всех не наблюдавшихся ABA настроек.
- Deadline кооперативный; blocking network/DNS/OS I/O может пережить caller timeout. Worker не убивается и держит in-flight слот до natural completion.
- Полная локальная identity читает ответ линейно кусками по 4096 символов; bounded buffer — не hard CPU/wall-clock containment.
- Sync generation не exact receipt→child mapping; неопределённая provenance не является доказательством успеха.
- Redaction не гарантирует распознавания любого свободнотекстового секрета. Внешний egress и OpenRouter usage требуют отдельного решения владельца.
- Requested и actual model различаются; response model принимается только для точного выбранного ID или его ASCII date suffix. Provider metadata не изобретается.
- Audit 1 МиБ на файл, 1–3 файла всего, только новый корень. Runtime reload, live server behavior, host abandon и межпроцессная ротация не доказаны unit tests.

Не запускайте `__init__.py` как CLI. Не меняйте live YAML вручную и не заменяйте целиком профиль. `OPENROUTER_API_KEY` используется только штатным scoped resolver владельца; environment/config/секреты соседей не копируются. Native scanner CAUTION требует актуального допуска на точные bytes; не обходите dangerous verdict или live-gateway guard.

## Откат

Baseline с локальными правками, прежние FAIL receipts и старые журналы сохранены. Для обратного переключения сначала выключите NEW и подтвердите отсутствие его loaded callbacks, затем восстановите именно прежнюю source-копию OLD и её собственные settings через поддерживаемые операции. После отдельного owner-разрешения можно вернуть прежнее enabled состояние OLD. Не включайте обе identity, не применяйте `git reset --hard` к чужим/грязным деревьям и не удаляйте новые или старые логи автоматически.

## Архив 0.1.8–0.1.5 — сохранённая история, не результат 0.2.0

Ниже прежний текст сохранён буквально. Его Sol/PPLX/custom-core утверждения, counts, SHA и бюджеты относятся к прежней ревизии; текущий стандартный SDK не объявляется этим текстом поддерживающим all-finals loop. [docs/audit.md](docs/audit.md) также остаётся историческим документом.

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
