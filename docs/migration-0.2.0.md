# Миграция Decision Review 0.2.0

**Old ID:** `pplx-decider-review` → **New ID:** `decision-review`.
**Default модель:** точный `openai/gpt-6-luna-decisions`.
**Маршрут:** прежний OpenRouter `https://openrouter.ai/api/alpha/decisions`.
**Public source:** https://github.com/sbrejnev988-coder/pplx-decider-review — repo URL не переименован.

Это инструкция для отдельно разрешённого переключения, **не выполненный rollout**. Текущая подготовка не трогает live profile, не делает reload/restart, сеть, inference или push. Родительский перенос ограничен default; siblings и all-profile activation не заявлены.

## До переключения

1. Разрешите ровно целевой профиль и его настоящий owner home; не выбирайте профиль по hostname или inherited environment.
2. Сохраните exact old source (включая локальные правки) и собственные non-secret settings. Не заменяйте default настройками соседа, не копируйте `.env`, auth store, весь config или live data. Code snapshot — не data backup.
3. Проверьте новый source/manifest, конфигурацию и актуальный native scanner admission. CAUTION требует отдельного решения для точного candidate, dangerous refusal не обходится. Получите настоящий полный published commit SHA; prepared version string не доказывает опубликованность.

## Сначала выключить OLD

Для существующего OLD используйте штатные owner-scoped команды (сначала сверяйте доступные флаги своей CLI):

```bash
hermes -p PROFILE config set plugins.entries.pplx-decider-review.settings.enabled false
hermes -p PROFILE plugins disable pplx-decider-review
hermes -p PROFILE config get plugins.entries.pplx-decider-review.settings.enabled
hermes -p PROFILE plugins list --plain --no-bundled
```

**OLD должен быть выключен до включения NEW.** Иначе одинаковые hooks двух identity могут дать двойную оценку, callbacks и расходы. Native allow-list и plugin settings — разные уровни: выключайте оба. Saved `false` и строка `disabled` не доказывают, что работающий процесс выгрузил старые callbacks. Если это не подтверждено, NEW оставьте disabled; reload/restart допустим только по отдельному решению владельца. Старые файлы и log root не удаляйте.

## Установка NEW и перенос settings

После всех необходимых допусков, в отключённом состоянии:

```bash
hermes -p PROFILE plugins install https://github.com/sbrejnev988-coder/pplx-decider-review.git --ref FULL_COMMIT_SHA --no-enable
hermes -p PROFILE plugins doctor decision-review --ci
hermes -p PROFILE plugins list --plain --no-bundled
```

`PROFILE` для текущего родительского переноса — только `default`. `FULL_COMMIT_SHA` — placeholder, не опубликованный PIN этого документа. Сохраняйте existing live-gateway guard и ограничения PM. По фактическому readback убедитесь, что NEW не активировался автоматически: `--no-enable` не является универсальным доказательством отключения при замене ранее enabled источника.

Settings переносятся через supported `hermes -p PROFILE config get/set` из `plugins.entries.pplx-decider-review.settings` в **`plugins.entries.decision-review.settings`**, по ключам, не заменой целого YAML. Native ID rename не является доказательством переноса settings.

| Настройки | Правило |
|---|---|
| `reviewer_model` | Только целевое изменение: `openai/gpt-6-luna-decisions` |
| `provider`, `endpoint`, `language` | Сохранить прежние допустимые `openrouter`, точный Decisions endpoint, `ru` |
| `review_subagents`, `review_main_agent`, `mode` | Сохранить собственные прежние значения |
| `min_accept_confidence`, `retry_threshold`, `max_review_retries`, `fail_open_on_api_error` | Сохранить пороги/политику; не калибровать без отдельного задания |
| `timeout_seconds`, `callback_budget_seconds`, `state_ttl_seconds`, `cache_ttl_seconds` | Сохранить прежние бюджеты |
| `log_enabled`, `log_max_bytes`, `log_keep_files` | Сохранить прежние настройки нового журнала |
| `enabled` | NEW временно `false`; затем восстановить старое согласованное состояние, не включать раньше OLD-disable proof |
| `api_key` / secret storage | Не переносить значение в YAML или файлы; сохранить собственный native owner-local resolver |

Отсутствующий override остаётся отсутствующим, если это сохраняет прежнее effective значение; для manifest-default смены identity проверьте effective reader. Старый зарезервированный `settings.model` не alias и не основание менять глобальную модель.

```bash
hermes -p PROFILE config set plugins.entries.decision-review.settings.enabled false
hermes -p PROFILE config set plugins.entries.decision-review.settings.reviewer_model openai/gpt-6-luna-decisions
hermes -p PROFILE config get plugins.entries.decision-review.settings.reviewer_model
```

Остальные значения записывайте только из своей сохранённой конфигурации. Не подставляйте новый advisory preset, повышенные лимиты или defaults вместо прежнего mode/budgets. Нельзя молча менять основную модель, delegation, generative auxiliaries, egress или соседние профили.

## Отдельная активация и проверки

Только после подтверждённого отсутствия active OLD, config/readback и native admission, при отдельно разрешённой активации, восстановите согласованное старое enabled состояние NEW. Если OLD был отключён, NEW остаётся отключённым. Для ранее включённого плагина:

```bash
hermes -p PROFILE config set plugins.entries.decision-review.settings.enabled true
hermes -p PROFILE plugins enable decision-review --no-allow-tool-override
```

Файл, native allow-list, saved settings, loaded runtime и исполненный callback проверяются отдельно. Не обещайте runtime reload без fresh loaded source/owner proof. Owner-scoped offline/native controls не доказывают реальный ответ модели. Paid canary здесь не сделан и автоматически не разрешён.

Сохраняемые `all_finals` и `verification_pass_status` callback-параметры не доказывают producer path: в выбранном стандартном SDK эти producers не обнаружены. Legacy tracked-edit `pre_verify` может работать; универсальный all-finals corrective loop остаётся prerequisite/неподтверждённым. Core/SDK/PM/public wrappers не менять и не создавать custom core fork.

## Модель и совместимость данных

После миграции модель меняется **одним plugin-relative `reviewer_model`** через supported config writer, без изменения source. Это только совместимые typed Decisions-модели на прежнем OpenRouter endpoint, не chat API. Format validation не заменяет серверный catalogue/entitlement check. Нет fallback на другую модель. Response model должен совпадать с выбранным ID или иметь ровно ASCII date suffix.

Пороги и resource budgets сохранены, но после смены модели нужна отдельная калибровка. Сравнение качества, цен и превосходства Luna в этой подготовке не выполнялось. Возвращённый provider — валидированная metadata, не факт, вычисленный из slug; `Synthetic OpenAI` в тестах — только synthetic fixture label.

Plugin-owned JSON-ключ `pplx_review` и collision envelope `{original, pplx_review}` сохранены для совместимости consumers. Он не host flag и не модельный selector. Public hooks, native marker names, `verification_pass_status`, `all_finals` и namespace guard не переименовываются. Internal test ENV `PPLX_TEST_*` оставлены для existing runners. Пользовательские заметки имеют префикс `DECISIONS`, продукт — Decision Review.

## Журналы и откат

NEW пишет только **`plugin-data/decision-review/reviews.jsonl`**. Retention 1–3 файла / максимум 1 МиБ применяется только внутри нового корня. **`plugin-data/pplx-decider-review` остаётся на месте без переноса, pruning, удаления и переписывания прежних model strings.** Сохранённая история не используется как новый transport result.

Для отката сначала выключите NEW на обоих уровнях и подтвердите отсутствие его loaded callbacks. Затем отдельной разрешённой операцией восстановите exact старую source-копию OLD с локальными правками и её собственные non-secret settings; только после этого восстанавливается прежнее enabled состояние OLD. Не включайте две identity одновременно. Не удаляйте новые/старые логи или baseline и не применяйте `git reset --hard` к чужим/грязным деревьям.

README/GUIDE содержат отдельно маркированные неизменённые release notes 0.1.x. `docs/audit.md`, старые receipts, provenance, бюджеты и известные Windows symlink skips не переписаны. Подготовленные новые tests не считаются исполненными; объединённый кандидат проверяется родителем на следующем разрешённом этапе.
