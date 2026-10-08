# Decision Review — 0.2.0

Standalone Python-плагин для **Hermes Agent**, ID **`decision-review`**: независимая вероятностная оценка дочерних результатов и финалов основного агента через **OpenRouter Decisions API**. Исходные результаты сохраняются; русские заметки имеют префикс **`DECISIONS`**. Имя плагина не зависит от производителя или выбранной модели.

> Decision Review даёт оценку, а не факт, доказательство выполнения или разрешение на инструменты. Плагин не выполняет фактические проверки и исправления, не запускает `delegate_task`, не делает HTTP retry и не обещает автоматического исправления ответа. Консервативный RETRY возможен даже при фактически успешной задаче.

## 0.2.0: новая identity и модель из конфигурации

- Старый ID `pplx-decider-review` заменён на `decision-review`; продукт называется **Decision Review**, не PPLX/Sol-reviewer.
- Default `reviewer_model` — буквально **`openai/gpt-6-luna-decisions`**. Другую совместимую **Decisions-модель** выбирают одной plugin-relative настройкой, без правки исходников.
- Маршрут сохранён: provider **`openrouter`**, только `POST https://openrouter.ai/api/alpha/decisions`. Chat API, прямой OpenAI API и fallback на другую модель не добавляются. Синтаксически допустимый model ID не доказывает доступность или поддержку typed Decisions на сервере.
- Пороги, бюджеты, redaction, owner-local secret scope, one-in-flight/no-retry, cache/receipt fences и шесть callbacks сохраняются. После смены модели нужна **отдельная калибровка**: прежние пороги не доказывают равную точность, превосходство Luna не установлено.
- Новый журнал — `plugin-data/decision-review/reviews.jsonl`. Старый `plugin-data/pplx-decider-review` не переносится, не очищается и не удаляется.

Это подготовленный source-контракт, **не отчёт об исполненных тестах, публикации, установке или runtime reload**. Платный canary не выполнен. Текущий перенос ограничен разрешённым профилем **default**; siblings и all-profile activation не заявляются. Исторические receipts и результаты 0.1.x ниже не являются приёмкой 0.2.0.

## Граница возможностей native core

Плагин сохраняет public SPI и callbacks, не изменяет Hermes core, SDK или managed PM workspace. Поддерживаемая legacy-поверхность `pre_verify` требует tracked edits и integer `attempt=0`; при наличии такого вызова режим `bounded` может передать русскую рекомендацию `action=continue`. `advisory` не запрашивает продолжение; final-transform сам не возобновляет agent loop.

**Универсальный corrective loop перед всеми финалами не обещается.** В выбранном стандартном SDK не обнаружены producers `all_finals`/`verification_pass_status`. Приём дополнительных metadata в callback или ручной вызов с `all_finals=True` не доказывает нативную доставку. Отсутствующие host-возможности остаются prerequisite/неподтверждёнными; core fork или обход public wrappers не предлагается.

Если совместимый host действительно передаст `verification_pass_status`, `requested` означает только запрос, а `completed` — сообщённое host-состояние, не доказательство исправления каждого замечания. Без metadata — `unknown`. Оценка **исходного черновика (DRAFT)** не выдаётся за оценку исправленного финала; повторного main HTTP в том же удерживаемом native turn нет. Никакой псевдоответ основной модели к финалу не дописывается. Первый streaming draft может уже показываться как interim.

## Миграция `pplx-decider-review` → `decision-review`

Подробный порядок и откат: [docs/migration-0.2.0.md](docs/migration-0.2.0.md).

1. Зафиксируйте старую installed source-копию и собственные значения разрешённого профиля. Не копируйте `.env`, весь config, live store или настройки соседних профилей.
2. **Отключите OLD до включения NEW**, чтобы не получить двойные callbacks и расходы. Используйте штатный `plugins disable` и plugin-relative `settings.enabled=false`; сохранённое выключение само по себе не выгружает уже зарегистрированные callbacks. Пока loaded OLD не отключён подтверждённо, NEW не активируйте. Reload — отдельное разрешение владельца, не часть этой подготовки.
3. Установите проверенный полный commit в отключённом состоянии. Репозиторий остаётся **[sbrejnev988-coder/pplx-decider-review](https://github.com/sbrejnev988-coder/pplx-decider-review)**: rename/push GitHub не заявлены. Читайте фактический native allow-list после установки; не полагайтесь только на имя флага `--no-enable`.
4. Через поддерживаемые scoped `config get/set` перенесите прежние plugin settings в `plugins.entries.decision-review.settings`. Все настройки, кроме **`reviewer_model`**, сохраняйте; temporary `enabled=false` — только безопасная стадия переключения. `reviewer_model` задайте **`openai/gpt-6-luna-decisions`**. Прежние `mode`, пороги, retry/timeout/log budgets и branch toggles не заменяйте defaults.
5. Отдельно проверьте конфигурацию, scanner/Doctor и отсутствие active OLD; лишь затем, при отдельном согласовании, восстановите прежнее состояние NEW и выполните разрешённую загрузку. Старые source/config и журналы оставьте для отката.

Наличие файла или saved allow-list не доказывает loaded identity. Для default и каждого отдельно разрешённого дальнейшего профиля нужны собственные readbacks; перенос в default не означает активацию остальных.

## Установка и разрешения

Команды ниже — инструкция, **они не были исполнены этой source-подготовкой**. Замените `PROFILE` только на разрешённый профиль (для текущего переноса — `default`), а `FULL_COMMIT_SHA` — на реально опубликованный проверенный полный SHA:

```bash
hermes -p PROFILE plugins install https://github.com/sbrejnev988-coder/pplx-decider-review.git --ref FULL_COMMIT_SHA --no-enable
hermes -p PROFILE plugins doctor decision-review --ci
```

Не выдумывайте SHA и не используйте плавающий main. При scanner CAUTION требуется отдельный допуск для точного candidate; dangerous verdict не обходится. Dependency/install flags выбирайте по актуальному `--help` своей версии Hermes. Existing live gateway guard и ограничения PM сохраняются.

`OPENROUTER_API_KEY` остаётся в штатном secret storage владельца профиля. Не помещайте значение в YAML, shell history или Git и не копируйте окружение. Native scoped secret resolver и обязательный `redact_for_egress` сохраняются. Фильтры **не гарантируют удаления любого секрета**: limited goal/evidence отправляются наружу лишь после согласования egress. Decisions расходует OpenRouter usage; бесплатность и универсальный spending cap не обещаются.

## Настройки и смена модели

Корень — **`plugins.entries.decision-review.settings`**; все типы и диапазоны — в `plugin.yaml`. Plugin-relative ключ — только **`reviewer_model`**, не зарезервированный core `model`. Старый `settings.model` не alias и не fallback. Основная модель Hermes, delegation и generative `auxiliary.review` независимы от этого typed Decisions route.

| Ключ | Default |
|---|---|
| `enabled` | `false` |
| `provider`, `reviewer_model` | `openrouter`, `openai/gpt-6-luna-decisions` |
| `endpoint`, `language` | `https://openrouter.ai/api/alpha/decisions`, `ru` |
| `review_subagents`, `review_main_agent` | `true` |
| `mode` | `bounded` |
| `min_accept_confidence`, `retry_threshold` | `0.8`, `0.65` |
| `max_review_retries`, `fail_open_on_api_error` | `1`, `true` |
| `timeout_seconds`, `callback_budget_seconds` | `10`, `25` |
| `state_ttl_seconds`, `cache_ttl_seconds` | `1800`, `300` |
| `log_enabled`, `log_max_bytes`, `log_keep_files` | `true`, `1048576`, `3` |

Выбор Luna и readback одной настройки, без изменения source:

```bash
hermes -p PROFILE config set plugins.entries.decision-review.settings.reviewer_model openai/gpt-6-luna-decisions
hermes -p PROFILE config get plugins.entries.decision-review.settings.reviewer_model
```

Для следующей смены замените **только значение `reviewer_model`** на заранее проверенный точный Decisions ID; provider/endpoint не менять. Валидация запрещает fallback, но не проверяет серверный каталог без запроса. Saved value не доказывает принятие работающим backend; runtime loading и API-проверка требуют отдельных допусков. Калибровка после model switch — отдельная работа, не основание молча менять пороги.

## Решения и данные

Запрос содержит пять атомарных `noul`, `next_action` типа `choice` и русскую пятиуровневую `score`-рубрику. Ответ требует всех вопросов, корректных типов, конечных чисел и полных распределений. `score` может быть дробным; погрешность суммы вероятностей — 0.001. Actual response model должен совпадать с **выбранным в запросе** model ID буквально либо иметь ровно ASCII suffix `-YYYYMMDD`; другая модель не принимается. Nullable actual model/ID не выдумываются. Optional `provider` — полученная и валидированная metadata, не вывод из model slug.

- **ACCEPT:** выполнение/надёжность ≥0.8, adverse-сигналы <0.35; choice/confidence/quality также допускают принятие.
- **RETRY:** выполнение/надёжность <0.65 либо adverse-сигнал ≥0.65; рекомендация основному агенту, не разрешение на действия. Child-cap — одна рекомендация по parent session/goal; main-cap — один native-turn в удерживаемом TTL state.
- **INSPECT:** неоднозначность, неполнота, недоступный reviewer или исчерпанный бюджет.

Choice не отменяет противоречащие основные метрики. **`verified=true` означает валидный transport/typed contract, не истинность результата.** Исходные native statuses/evidence не заменяются.

Terminal string-JSON `delegate_task.results` оценивается для каждого meaningful child отдельно; dispatched/running и control actions пропускаются. Для совместимости сохранён plugin-owned JSON-ключ **`pplx_review`**, при конфликте — `{original, pplx_review}`. Это legacy data-contract, не привязка к PPLX-модели и не host SPI. Его переименование не требуется для новой identity; оригинальные поля не перезаписываются.

Async требует настоящих start/stop, recorded dispatch, owner/parent match и актуальной typed delivery row с anchored COMPLETE-маркером. Текстового маркера недостаточно. Async ACCEPT понижается до INSPECT, если native stop не подтверждает полноту. Memoization и receipt ограничены owner, goal/evidence, конфигурацией и recorded turn/execution provenance; неоднозначные границы — в [историческом аудите](docs/audit.md).

## Ресурсы и журнал

State: до 128 sessions и 128 records каждого типа на session; TTL/cache — не durable spending/retry ledger. Egress: до 20000 UTF-8 bytes после native redaction. Один HTTP worker/request in-flight на runtime; caller timeout не освобождает слот раньше natural completion. Полная локальная identity читает весь ответ линейно кусками по 4096 символов; это не hard CPU/wall-clock containment.

Transport не следует redirects, не наследует proxy env, ограничивает ответ 128 КиБ. Streaming deadline не прерывает мгновенно blocking network phase/DNS/OS I/O. Unload запрещает применение позднего результата, но worker не убивается. Журнал нового ID ограничен 1 МиБ на файл и 1–3 файлами всего, включая current; retention касается **только нового корня**. Goal/prompts, raw errors/Authorization и credentials не сохраняются. Requested model отличается от nullable actual model; ошибка журналирования не заменяет native result.

## Воспроизводимые offline проверки

Тесты 0.2.0 подготовлены; выполнение объединённого кандидата — отдельный этап. Синтетический `answer_payload` возвращает **`request['model']`**, а `provider='Synthetic OpenAI'` — фиксированная fixture label, не утверждение о реальном upstream. Существующие negative model/response tests, zero key/HTTP/retry, core-marker и DRAFT assertions сохранены. Исторические пять Windows symlink skips не удалены и не превращены в PASS новой версии.

Без Hermes SDK и API key, после разрешённой установки зависимостей:

```bash
python -m venv .venv
# Активируйте .venv средствами своей ОС.
python -m pip install -r requirements-dev.txt
python -B scripts/run_tests.py
```

Runner создаёт synthetic home, очищает credential environment, запрещает network/credential-file I/O, отключает pytest plugin autoload и сохраняет JUnit/summary в напечатанном scratch root. Старые roots не удаляет. Default исключает `test_native_config.py`: это **offline unit**, не native SDK proof. Установка зависимостей может требовать сеть и не входит в offline запуск.

С совместимым interpreter и verified текущим Hermes checkout можно отдельно проверять native config:

```bash
python -B scripts/run_tests.py --core /path/to/hermes-agent
```

Не добавляйте чужие Python/site-packages и не подменяйте PluginContext/validator. `tests/native_onepass_unittest.py` — отдельный сохраняемый набор; no-edit all-finals/metadata cases имеют указанную выше host-предпосылку и не доказывают её наличия в стандартном SDK. Успешный MockTransport callback не доказывает live producer path или реальный ответ Luna. Публичные результаты конкретного опубликованного commit — в [GitHub Actions](https://github.com/sbrejnev988-coder/pplx-decider-review/actions), не в выдуманном новом repo URL.

Практический гайд: [GUIDE_RU.md](GUIDE_RU.md). Исторический аудит [docs/audit.md](docs/audit.md) не переписан.

## Архив release notes 0.1.x — не приёмка 0.2.0

Ниже сохранён прежний текст без изменения provenance, model-упоминаний, counts, budgets или receipts. Упоминания Sol/PPLX и reviewed custom core относятся только к прежнему состоянию; они не обещают текущему стандартному SDK универсальный all-finals loop.

## 0.1.8: стабильная заметка и корректные regression controls

В режиме `bounded` повторная обработка того же fallback-финала сохраняет его первоначальную заметку и не превращает её в оценку черновика. Если оценка получена в `pre_verify`, изменился кандидат или native core сообщает `requested/completed`, заметка по-прежнему относится к **исходному DRAFT**, а не подтверждает исправленный финал. Повторного main-review HTTP в этом native turn нет; это ограничение не переносится на `advisory` или child review.

Identity кандидата учитывает весь исходный ответ, в том числе хвост после лимита egress и различия в редактируемых фрагментах. Raw text и локальный digest не добавляются в запрос или журнал. Перед применением final-scoped заметки tool evidence повторно сверяется под общей блокировкой; при позднем изменении сохраняется исходный ответ без устаревшей заметки и без дополнительного HTTP. Это не доказывает полноту внешней оценки: reviewer по-прежнему получает только ограниченную redacted projection.

Regression controls разделяют TTL актуальности receipt и бюджет запроса: истечение TTL, смена task/goal или отказ HTTP не пополняют main-слот. Новый настоящий turn допускает новый main review; прежний goal-cap дочерних рекомендаций сохраняется. Проверки model metadata и отключённой ветви выполняются через допустимый reservation с положительными и отрицательными controls.

Это исходники плагина, не автоматическое обновление установленных копий. Изменения core, настройка `agent.pre_verify_all_finals`, разрешение egress и загрузка работающим Desktop/gateway остаются отдельными операциями. Native controls требуют совместимого SDK; portable CI не является live Sol/PPLX proof. Результат конкретного commit — в GitHub Actions.

## История: 0.1.7, один проход Sol до финала

В режиме `bounded` native `pre_verify` передаёт проверенный RETRY/INSPECT основному агенту как настоящий `action=continue` с русским user-nudge. Sol должен самостоятельно сверить факты и требования, исправить подтверждённые ошибки либо явно сообщить, что замечание не подтвердилось. ACCEPT, ошибка API, отключённая ветвь, child и `advisory` не требуют продолжения.

Без native opt-in остаётся прежнее условие: `attempt` — integer 0 и есть tracked path. Для разрешённых ответов без edits нужен **reviewed core с `agent.pre_verify_all_finals: true` (строгий bool)**, передающий `all_finals=True`. Плагин не включает этот глобальный режим сам. Core обязан ограничить feedback одним проходом на реальный пользовательский ход, сохранить `max_verify=0` и блокировать новый `delegate_task` во время прохода; седьмой plugin hook не добавлен.

Слот owner/session/native-turn атомарно резервируется до review. После него final-transform сохраняет текст модели и использует отдельный ограниченный receipt **исходного черновика (DRAFT)**, даже если final изменился или cache очищен. Повторного автоматического PPLX-запроса нет. Истёкший или не совпадающий scope receipt не применяется; native `requested/completed` также запрещает новый запрос при утрате receipt. `verification_pass_status=completed` показывается только по native metadata, `requested` не означает завершения; без metadata — `unknown`. Оценка DRAFT не выдаётся за PPLX-подтверждение исправленного final. Псевдоответ `Sol:` к финалу не дописывается.

Пороги, transport/model/endpoint, секреты, redaction, таймауты и namespace guard неизменны. Новый пользовательский native ход сбрасывает только main-слот; прежний goal-cap дочерних рекомендаций сохранён. Первый streaming candidate может уже быть виден как interim: проверка до первого отображения не обещается.

Это описание source-контракта: публикация исходников не доказывает установку, scanner consent, успешный интегрированный core loop либо активацию профилей. Focused native SDK/dispatcher regressions — `tests/native_onepass_unittest.py`; они запускаются отдельным stdlib runner в plugin lane, а не автоматически portable pytest без SDK. Fixtures используют только настоящий PluginContext/validator/home/secret scope и контролируемые MockTransport Decisions, не реальный Sol/PPLX API.

## История: 0.1.6, понятные сообщения reviewer

Русские заметки PPLX теперь выводятся отдельными пунктами с процентами. Они явно отделяют оценку модели от доказанных фактов: рекомендация RETRY сначала требует сверки требований и реальных результатов, а исправления — только при подтверждённых недочётах. При недоступности reviewer сообщается «заключение PPLX отсутствует», а не утверждается, что фактическая проверка результата не состоялась.

Пороги, provider/model/endpoint, исходные результаты, режимы и запрет автоматического запуска субагента не изменены. Заметка при финальном ответе по-прежнему не запускает новый цикл работы. Добавлены регрессии текста и сохранения исходного результата. CI выбранного commit смотрите в GitHub Actions; установка в профили и загрузка уже работающим процессом проверяются отдельно.

## История: 0.1.5 после полной ревизии

Версия подготовлена на основе `204d1099f34a5469e94f52d7a77c20c0283d4591` и выборочно использует проверенные идеи `pplx-decider-review-fixed.zip`. Архив не заменял tests/CI: прежние файлы сохранены. Исправлены смешение политик в кеше при ABA настроек, повтор старого verdict новой задачей, обработка evidence после deadline и fail-open для non-finite JSON. Добавлены scope/lifecycle fences, ограниченная проекция, согласованность choice/score и Unicode-safe egress.

Локальная приёмка неизменённых code/test files: **216 passed / 5 skipped** с native SDK (221 cases) и **208 passed / 5 skipped** через standalone unit runner (213 cases), failures/errors — 0. Пять skips каждой suite: Windows symlink privilege error 1314. Наборы перекрываются, их нельзя суммировать. Независимый recheck non-finite исправления прошёл; live OpenRouter behavior этим не доказывается. Актуальный cross-platform результат конкретного commit — в [GitHub Actions](https://github.com/sbrejnev988-coder/pplx-decider-review/actions).

Native scanner v9 оставил **CAUTION: 4 findings**: pip install через requirements с pinned прямыми зависимостями, текст о os.environ в README, Unicode test fixtures и defensive sensitive-field regex. Scanner override не выполнялся. Публикация source не означает разрешения на установку/egress и не обновляет установленные копии или процессы.

Порядок работы и ограничения: [GUIDE_RU.md](GUIDE_RU.md). Предыдущий [аудит 0.1.4](docs/audit.md) сохранён как история, не как доказательство готовности 0.1.5.

## Лицензия

MIT, по явному решению владельца: [LICENSE](LICENSE). Installed source, saved config, loaded runtime и исполненные проверки — отдельные статусы; подготовка этой версии не означает автоматического обновления старых копий.
