# Аудит и исправления 0.1.4

## Область

Проверен standalone PPLX reviewer. Начальный source revision — `4fb03af2c838797c83f3d0ff87b150df9620d574`, исходный полный offline/native-config baseline — 132 tests passed. Два независимых аудитора проверили протокол/логику и безопасность/ресурсы. Исправления сделаны в двух отдельных derivatives и сведены с проверкой raw SHA-256, actual command exits и JUnit. Полные private-path receipts не включены в публичный payload.

Hermes core, установленные profile copies, credentials и действующие процессы этим аудитом не менялись. Проверка core namespace использует реальные PluginContext/config reader/validator и synthetic owner, не ослабленный validator.

## Исправленные дефекты

| Граница | Причина в baseline | Изменение |
|---|---|---|
| Sync memoization | Один task_index смешивал два разных дочерних запуска с одинаковой evidence | Новый recorded start меняет parent lifecycle-generation cache fence; task_index остаётся частью identity |
| Main memoization | Общий cache обходил turn-проверку last_final | Actual recorded turn включён в main cache identity; RETRY остаётся ограниченным parent/goal между turns |
| Actual model в audit | При недоступном reviewer requested_model выдавался за actual model | Unknown actual model остаётся null; requested_model сохранён отдельно |
| Provider metadata | Present non-string provider тихо превращался в null при verified=true | Present provider должен быть string ≤128; missing допустим; malformed вызывает unverified INSPECT |
| Compressed response | HTTPX iter_bytes распаковывал gzip до проверки cap | Accept-Encoding: identity; nonidentity Content-Encoding отвергается до чтения/decoder; real stream через iter_raw с cap 131072 bytes |
| Drip response | HTTPX phase timeout не был total processing deadline | Monotonic deadline проверяется между фазами и raw chunks, без HTTP retry; caller end передаётся worker |
| Unload | Late verified response после close мог примениться и записаться | Generation/closed checks и lifecycle lock для cache/nudge/advisory/audit commits; HTTP worker завершается естественно |
| Journal caps | Keep 3→1/2 и уменьшение размера оставляли прежние archives | На write attempt pruning применяется к current/.1/.2, включая oversized-entry skip; посторонние имена не изменяются |

Для каждой из восьми границ сохранён фактический assertion RED, затем focused GREEN на минимальной production-правке. Unload проверен на review/cache/sync/main/nudge/async поверхностях. Положительные controls сохраняют replay cache, requested/actual model и исходные ответы. Symlink/reparse paths проверяются до pruning/rotation; это defensive path check, не атомарная гарантия против сторонней мутации filesystem.

## Не выдаётся за исправление

- **Sync identity — parent-generation fence, не exact receipt→child mapping.** Текущий native sync receipt не содержит child IDs. При отсутствующих start-событиях нельзя доказательно отличить новый запуск от replay; сторонний start также инвалидирует cache. Replay без нового start остаётся cache hit.
- **Deadline кооперативный.** Он прекращает drip на ближайшей проверке; текущая блокирующая network phase, DNS/OS I/O может пережить срок. Единственный in-flight slot удерживается до реального завершения, не освобождается ради нового запроса. Hard kill не реализован.
- **Compressed responses отклоняются.** Даже корректный gzip/brotli-ответ становится fail-open INSPECT, если сервер не соблюдает identity. Поддержка bounded decompression не заявлена.
- **Retained history не мигрируется.** Прежние ошибочные model-строки не переписываются. Старые archives очищаются при следующей попытке записи; log_enabled=false не вызывает pruning.
- **Windows symlink tests могут быть skipped.** В локальном owner процессе создание symbolic link отказало Windows 1314; пять таких tests были пропущены. Это не native доказательство этой защиты. Linux CI выполняет реальные symlink controls, когда доступна обычная POSIX capability.
- **Вероятности не откалиброваны.** next_action=принять и deterministic RETRY допустимы при противоречащих noul metrics. Ревью не источник фактов или permission.
- **CLI stdout issue вне этого repo.** Ранее проверенный native return через штатный turn-report не доказывает исправление quiet CLI stdout. Core не менялся.
- **No activation/live inference claim.** Offline tests не обновляют профили, не reload Desktop/gateway и не делают сетевой запрос к PPLX. Публикация новой версии не означает, что установленный 0.1.3 уже обновлён.

## Test integration после первого полного gate

Первое объединённое acceptance было отклонено: восемь native assertions ожидали 0.1.3 при manifest 0.1.4, а portable runner не мог создать 26 новых cases без SDK. Оба дефекта исправлены отдельно, без изменения production: version assertion синхронизирован, behavior cases используют явно маркированный synthetic unit owner по умолчанию и настоящий прежний native owner при explicit `PPLX_TEST_NATIVE_CORE`. Native validator/reader и их отрицательные controls не ослаблялись.

Ещё одно падение относилось к timing oracle. Возврат caller `Event.wait` по timeout не гарантирует достижения worker monotonic deadline: отдельная примитивная проверка наблюдала такой возврат на Python 3.11 с coarse `GetTickCount64()`. Историческое падение не записало точные timestamps, поэтому его конкретная причина остаётся UNKNOWN; config-cache defect не доказан. Production timing не менялся. Test использует module-local clock seam для expired-negative и predeadline-positive controls, сохраняя удержание единственного worker, deadline stop и natural completion; global clock/native core не подменяются. Исходные неуспешные receipts сохранены.

## Как перепроверить

`python -B scripts/run_tests.py` — portable offline unit suite без native SDK claim; `--core /path/to/hermes-agent` — отдельно native config regression при compatible interpreter/dependencies. Runner печатает actual JUnit/summary location и сохраняет synthetic roots. Source tests включают `tests/test_publication_runtime.py` и `tests/test_publication_metadata.py`.

CI в этом repository запускает Windows/Linux × Python 3.11/3.14 unit jobs с pinned test dependencies и Actions revisions. Фактический outcome конкретного commit проверяется в GitHub Actions; он не выводится из имени файла GREEN. Baseline и historical receipts не пересчитываются как новый итог.
