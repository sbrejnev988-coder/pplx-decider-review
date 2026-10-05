# pplx-decider-review

Standalone Python-плагин для **Hermes Agent**: независимая вероятностная проверка дочерних результатов и финалов основного агента через **OpenRouter Decisions API**. Исходный результат сохраняется; ревью добавляется отдельно, на русском языке.

> PPLX — оценка, не факт, не доказательство выполнения и не разрешение на инструменты. Плагин не делает автоматический `delegate_task`, HTTP retry или произвольный повтор agent loop. Консервативный RETRY возможен даже при фактически успешной задаче.

## 0.1.6: понятные сообщения reviewer

Русские заметки PPLX теперь выводятся отдельными пунктами с процентами. Они явно отделяют оценку модели от доказанных фактов: рекомендация RETRY сначала требует сверки требований и реальных результатов, а исправления — только при подтверждённых недочётах. При недоступности reviewer сообщается «заключение PPLX отсутствует», а не утверждается, что фактическая проверка результата не состоялась.

Пороги, provider/model/endpoint, исходные результаты, режимы и запрет автоматического запуска субагента не изменены. Заметка при финальном ответе по-прежнему не запускает новый цикл работы. Добавлены регрессии текста и сохранения исходного результата. CI выбранного commit смотрите в GitHub Actions; установка в профили и загрузка уже работающим процессом проверяются отдельно.

## История: 0.1.5 после полной ревизии

Версия подготовлена на основе `204d1099f34a5469e94f52d7a77c20c0283d4591` и выборочно использует проверенные идеи `pplx-decider-review-fixed.zip`. Архив не заменял tests/CI: прежние файлы сохранены. Исправлены смешение политик в кеше при ABA настроек, повтор старого verdict новой задачей, обработка evidence после deadline и fail-open для non-finite JSON. Добавлены scope/lifecycle fences, ограниченная проекция, согласованность choice/score и Unicode-safe egress.

Локальная приёмка неизменённых code/test files: **216 passed / 5 skipped** с native SDK (221 cases) и **208 passed / 5 skipped** через standalone unit runner (213 cases), failures/errors — 0. Пять skips каждой suite: Windows symlink privilege error 1314. Наборы перекрываются, их нельзя суммировать. Независимый recheck non-finite исправления прошёл; live OpenRouter behavior этим не доказывается. Актуальный cross-platform результат конкретного commit — в [GitHub Actions](https://github.com/sbrejnev988-coder/pplx-decider-review/actions).

Native scanner v9 оставил **CAUTION: 4 findings**: pip install через requirements с pinned прямыми зависимостями, текст о os.environ в README, Unicode test fixtures и defensive sensitive-field regex. Scanner override не выполнялся. Публикация source не означает разрешения на установку/egress и не обновляет установленные копии или процессы.

Порядок работы и ограничения: [GUIDE_RU.md](GUIDE_RU.md). Предыдущий [аудит 0.1.4](docs/audit.md) сохранён как история, не как доказательство готовности 0.1.5.

## Совместимость

- Reviewer: буквально `perplexity/pplx-decider-v1-27b`.
- Только `POST https://openrouter.ai/api/alpha/decisions`, config provider `openrouter`. Иной route/model отвергается до scoped key.
- Зависимость `httpx>=0.28,<0.29`; `register(ctx)`, шесть hooks из `plugin.yaml`, без дополнительных tools/capability overrides.
- Native config-контракт проверялся с Hermes core [`8b66a51036c1e20920a17cdd049fdf55c968d683`](https://github.com/NousResearch/hermes-agent/commit/8b66a51036c1e20920a17cdd049fdf55c968d683). Совместимость со всеми другими SDK не обещается.
- Установка, configuration и reload существующего Desktop/gateway — отдельные операции. Репозиторий сам ничего не устанавливает и не меняет core.

## Установка и разрешения

Сначала проверьте код, manifest и native scanner report. Замените placeholders `PROFILE` и `FULL_COMMIT_SHA`:

```bash
hermes -p PROFILE plugins install https://github.com/sbrejnev988-coder/pplx-decider-review.git --ref FULL_COMMIT_SHA --no-enable
hermes -p PROFILE plugins doctor pplx-decider-review --ci
```

Устанавливайте полный проверенный commit SHA, не плавающий main. При scanner caution отдельно подтвердите точный candidate; не обходите dangerous verdict. Дополнительные dependency/install flags выбирайте по актуальному `--help` своей версии Hermes.

Зарегистрируйте **`OPENROUTER_API_KEY` только в secret storage своего Hermes-профиля**. Не помещайте значение в YAML, shell history или Git. Плагин использует native `agent.secret_scope.get_secret` на owner callback и не читает `.env`, auth-файлы или credentials из `os.environ` напрямую.

Review отправляет ограниченные goal/evidence внешнему провайдеру. Фильтры и обязательный native `redact_for_egress` уменьшают риск, но **не гарантируют удаления любого секрета**. Включение требует разрешения владельца на egress. Decisions расходует OpenRouter usage; бесплатность или универсальный spending cap не обещаются.

## Настройки

Корень — `plugins.entries.pplx-decider-review.settings`; все типы/диапазоны в `plugin.yaml`.

| Ключ | Default |
|---|---|
| `enabled` | `false` |
| `provider`, `reviewer_model` | `openrouter`, `perplexity/pplx-decider-v1-27b` |
| `endpoint`, `language` | `https://openrouter.ai/api/alpha/decisions`, `ru` |
| `review_subagents`, `review_main_agent` | `true` |
| `mode` | `bounded` |
| `min_accept_confidence`, `retry_threshold` | `0.8`, `0.65` |
| `max_review_retries`, `fail_open_on_api_error` | `1`, `true` |
| `timeout_seconds`, `callback_budget_seconds` | `10`, `25` |
| `state_ttl_seconds`, `cache_ttl_seconds` | `1800`, `300` |
| `log_max_bytes`, `log_keep_files` | `1048576`, `3` |

После согласования egress для advisory-пилота используйте supported profile-scoped writer:

```bash
hermes -p PROFILE config set plugins.entries.pplx-decider-review.settings.mode advisory
hermes -p PROFILE config set plugins.entries.pplx-decider-review.settings.enabled true
hermes -p PROFILE plugins enable pplx-decider-review --no-allow-tool-override
```

Основная модель, delegation и generative `auxiliary.review` независимы. Core резервирует plugin-relative model: используется только **`reviewer_model`**. Старый `settings.model` не alias; мигрируется отдельно через supported CLI. Проверьте effective reader и выполняйте только разрешённый reload: сохранённый config не доказывает adoption открытым backend.

## Решения и данные

Запрос — пять атомарных noul, next_action типа choice, русская пятиуровневая score-рубрика. Ответ требует всех вопросов, корректных типов, конечных чисел и полных распределений. Score может быть дробным; погрешность суммы вероятностей — 0.001. Actual model совпадает буквально или имеет ровно suffix -YYYYMMDD.

- **ACCEPT:** выполнение/надёжность ≥0.8, adverse-сигналы <0.35; choice/confidence/quality также допускают принятие.
- **RETRY:** выполнение/надёжность <0.65 либо adverse-сигнал ≥0.65; это рекомендация Sol, не действие. Максимум одна рекомендация по parent session/goal в удерживаемом TTL state.
- **INSPECT:** неоднозначность, неполнота, недоступный reviewer или исчерпанный бюджет.

Choice не отменяет противоречащие основные метрики: next_action=принять вместе с RETRY допустим. **verified=true — валидный transport/typed contract, не истинность ответа.** Неизвестный actual model/ID не выдумывается. Usage/optional provider metadata проверяются; произвольные поля не копируются.

### Дочерние результаты и основной агент

Проверяется terminal string-JSON `delegate_task.results`, каждый meaningful child отдельно; dispatched/running и control actions пропускаются. Исходные поля/status/evidence сохраняются. Добавляется `pplx_review`, при конфликте — отдельный `{original, pplx_review}` envelope. Memoization ограничено owner session, evidence, configuration и recorded execution/turn provenance; неоднозначные границы — в [аудите](docs/audit.md).

Async требует настоящих start/stop, recorded dispatch, owner/parent match и актуальной typed delivery row с anchored COMPLETE-маркером. Текстового маркера недостаточно. Restart/TTL/eviction или отсутствие typed provenance могут исключить review. Native stop не даёт всех flags полноты: async ACCEPT понижается до INSPECT.

Bounded допускает один native pre_verify action=continue при nonempty tracked edits и attempt 0; advisory не даёт nudge. Общий final-transform сохраняет final, но **не возобновляет произвольный agent loop**. Terminal/child writes могут не попасть в parent tracker; плагин сам не запускает тесты.

## Ресурсы и журнал

State — до 128 sessions и 128 records каждого типа на session; процессное TTL-состояние, не durable retry ledger. Egress — до 20000 UTF-8 bytes после native redaction. На runtime один HTTP worker/request in-flight; caller timeout не освобождает слот раньше natural worker completion.

Transport не следует redirects, не наследует proxy env, ограничивает ответ 128 КиБ. Streaming deadline не означает мгновенное прерывание блокирующей network phase, DNS или OS I/O. Unload запрещает применение позднего результата; работающий worker не убивается. Compressed-response policy и timing boundary — в [аудите](docs/audit.md).

Audit — только `plugin-data/pplx-decider-review/reviews.jsonl` в owner home: до 1 МиБ на файл и 1–3 файла всего, включая current. Goal, prompts, raw errors/Authorization и credentials не сохраняются. Requested model отличается от nullable actual model. Ошибка журналирования не заменяет native result.

## Воспроизводимые offline проверки

Без Hermes SDK и API key:

```bash
python -m venv .venv
# Активируйте .venv средствами своей ОС.
python -m pip install -r requirements-dev.txt
python -B scripts/run_tests.py
```

Runner создаёт новый synthetic home, очищает credential environment, запрещает network/credential-file I/O, отключает pytest plugin autoload, сохраняет JUnit/summary в напечатанном scratch root. Старые roots не удаляет. Default явно исключает test_native_config.py: это **offline unit**, не live/native SDK proof.

С совместимыми зависимостями и проверенным checkout Hermes можно отдельно выполнить native config-контракт:

```bash
python -B scripts/run_tests.py --core /path/to/hermes-agent
```

Не добавляйте чужие поколения Python/site-packages ради проверки. Key синтетический, сеть запрещена, native core/context/validator не подменяются. CI запускает unit workflow на Windows/Linux и Python 3.11/3.14; статус конкретного commit — в GitHub Actions. Локальные receipts с private paths намеренно не опубликованы. Исправления и ограничения — в [docs/audit.md](docs/audit.md).

## Лицензия

MIT, по явному решению владельца: [LICENSE](LICENSE). Установленные копии старой версии автоматически не обновляются.
