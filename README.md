# Система оркестрации агентов (Python)

Этот репозиторий содержит базовый, расширяемый “скелет” оркестратора, который запускает несколько ролей через **OpenAI Agents SDK** и ведёт детальные отчёты каждого прогона.

## Быстрый старт (установка)

1) Создай и активируй виртуальное окружение:

```bash
python -m venv .venv
source .venv/bin/activate
```

2) Установи зависимости оркестратора:

```bash
pip install -e .
```

3) Создай `.env` (он не коммитится) и положи ключ:

```
OPENAI_API_KEY=...
```

## Запуск

```bash
python -m orchestrator.main
```

В stdout печатаются только короткие статусы (путь к отчётам, раунды, итоговый вердикт).

## Структура репозитория (что где заполнять)

В репо строго разделены зоны ответственности:

- `orchestrator/` — код оркестратора и инструментов (tools/policies/flow). Это “движок”.
- `project/` — “источники истины” проекта: видение, архитектура, конвенции, задачи, решения, отчёты.
- `workspace/` — рабочая зона продукта: код, тесты, requirements/pyproject, README продукта. **Только агент-исполнитель (Implementer) меняет `workspace/`.**

### Основные файлы в `project/`

- `project/tasks/current.md` — **активная задача**, которую оркестратор будет выполнять. Внутри Python-кода задачи не хранятся.
- `project/tasks/backlog.md` — список будущих задач (необязательно, но удобно).
- `project/tasks/done.md` — журнал выполненных задач (обновляет Tech Writer при PASS).
- `project/vision.md` — краткое видение (опционально).
- `project/architecture.md` — текущая архитектура/границы (опционально, но желательно).
- `project/conventions.md` — правила кодстайла, структуры, тестов, именования (опционально, но очень полезно).
- `project/decisions/` — “ADR-lite” решения (создаёт Tech Writer, если в ходе задачи появилось решение).
- `project/reports/` — отчёты прогонов (генерируется автоматически; коммитить не нужно).

## Как работает процесс (по шагам)

Оркестратор запускает пайплайн:

1) **Planner** читает:
   - `project/tasks/current.md` (обязательно)
   - `project/tasks/backlog.md` (опционально, в усечённом виде; помогает учитывать “горизонт” следующих задач)
   - `project/vision.md`, `project/architecture.md`, `project/conventions.md` (если есть)
   и выдаёт:
   - короткий план (<= 8 пунктов)
   - acceptance criteria (<= 6 пунктов)
   Planner **никогда не меняет файлы**.

2) **Fixup-loop (Implementer ↔ Reviewer)**, максимум несколько раундов:
   - **Implementer** имеет право менять **только `workspace/`** через инструменты `fs_*` и `run_cmd`.
     Он должен попытаться запустить тесты командой `python -m pytest -q` (через `run_cmd`).
     Установка зависимостей запрещена: `run_cmd` блокирует `pip install`, `poetry install`, `npm install` и т.п.
   - **Reviewer** проверяет отчёт Implementer’а и выносит решение:
     - `VERDICT: PASS|FAIL`
     - `ACTION: CONTINUE|SKIP`
     - получает `git diff`/patch по изменениям в `workspace/` (чтобы реально ревьюить код без доступа к файловой системе)
     Если проблема в окружении/зависимостях (например, нет `pytest`) — Reviewer обязан поставить `VERDICT: FAIL` и `ACTION: SKIP` и написать точные ручные шаги установки.
   - Если Reviewer повторяет один и тот же текст 2 раза подряд — включается “stuck detection” и прогон останавливается (`SKIP`).

3) **Tech Writer** запускается только:
   - при `PASS` (по умолчанию), либо
   - при `FAIL`, если Reviewer явно попросил обновить документацию (FIXES с префиксом `DOCS:`).
   Tech Writer может менять **только `project/`** (кроме `project/reports/`).
   При `PASS` он также может (опционально) добавить следующую задачу в `project/tasks/backlog.md`.

## Отчёты и артефакты

Каждый запуск создаёт папку:

```
project/reports/run-YYYYMMDD-HHMMSS/
```

Внутри:

- `plan.txt` — вывод Planner.
- `implementer.txt` — отчёты Implementer по раундам.
- `reviewer.txt` — вердикты Reviewer по раундам.
- `tech_writer.txt` — изменения документации (если запускался).
- `diff_round_N.patch` — `git diff`/patch по `workspace/` для соответствующего раунда (включая новые файлы).
- `artifacts.json` — структурированные данные: команды, результаты, пути файлов, вердикты.

## Как писать задачи (практика)

Рекомендуемый формат для `project/tasks/current.md`:

- Цель (что должно появиться в `workspace/`).
- Ограничения (например: “без установки deps автоматически”, “использовать pytest”, “не трогать X”).
- Acceptance criteria (проверяемые пункты).
- Команды проверки (например `python -m pytest -q`).
- Non-goals (что *не* делать).

Подход на период (например, неделя/спринт):

1) Положи крупную цель в `project/tasks/backlog.md`.
2) Выбери одну конкретную, проверяемую задачу и перенеси в `project/tasks/current.md`.
3) Запусти оркестратор, посмотри `project/reports/run-.../`.
4) Если `FAIL CONTINUE` — Fixup-loop продолжит итерацию.
5) Если `FAIL SKIP` из-за зависимостей — сделай ручные шаги из `FIXES` и перезапусти.
6) При `PASS` Tech Writer обновит `project/tasks/done.md` и (при необходимости) `project/architecture.md` / `project/decisions/`.

## Demo-task (fallback)

Да: “тестовое приложение” — это **фоллбек-задача**.

Если `project/tasks/current.md` фактически пустой (пустые строки/заголовки/`TODO` без реального текста), оркестратор автоматически использует встроенную demo-задачу: создать tiny-пакет в `workspace/` (`app/greeter.py`, pytest-тесты, `requirements.txt`, `README.md`).

## Конфигурация моделей (по ролям)

По умолчанию Implementer использует `gpt-5.1-codex-mini` (как модель для изменений кода).
Другие роли можно (и часто полезно) настраивать отдельно через env — см. `.env.example`.

Переменные:

- `ORCH_MODEL_PLANNER`
- `ORCH_MODEL_IMPLEMENTER`
- `ORCH_MODEL_REVIEWER`
- `ORCH_MODEL_TECH_WRITER`

Рекомендация: оставь кодовую модель для Implementer, а для Planner/Reviewer/Tech Writer можно попробовать более “текстовую” модель (если она у тебя доступна) — это часто дешевле и стабильнее для описательных задач.

## Кастомизация промптов (инструкций агентам)

Дефолтные инструкции для ролей “вшиты” в `orchestrator/agents.py`, но их можно переопределять файлами из `orchestrator/prompts/`:

- `orchestrator/prompts/planner.md`
- `orchestrator/prompts/implementer.md`
- `orchestrator/prompts/reviewer.md`
- `orchestrator/prompts/tech_writer.md`

Правило: если файл фактически пустой (например, только заголовок/`TODO`), используется встроенный дефолт.

Важно: не ломай форматы вывода, иначе оркестратор не сможет распарсить результаты (особенно `Reviewer` с `VERDICT/ACTION/FIXES`).

## Diff для Reviewer (git diff)

Оркестратор автоматически прикладывает в вход Reviewer секцию `DIFF:` — это `git diff`/patch по изменениям в `workspace/` (включая новые файлы). Это позволяет Reviewer делать настоящее code review без доступа к файловой системе.

Поддерживаются оба варианта структуры:

- `workspace/` — отдельный git‑репозиторий продукта (есть `workspace/.git`) → diff берётся **изнутри `workspace/`**.
- git‑репозиторий на верхнем уровне → diff берётся по поддереву `workspace/`.

Переменная:

- `ORCH_REVIEWER_DIFF_MAX_CHARS` (по умолчанию 12000) — ограничение размера `DIFF:` в промпте Reviewer (полный патч всё равно сохраняется в `project/reports/run-.../diff_round_N.patch`).

## Red Flags (автоскан для Reviewer)

Оркестратор выполняет статический скан `workspace/` (по `.py/.pyi` файлам) на потенциально рискованные паттерны:
`InMemory*`, `Mock/Fake`, `:memory:` и т.п. Результат передаётся Reviewer в секцию `RED_FLAGS:`.

Это **не** заменяет ревью, но служит сигналом:
если `RED_FLAGS` показывает in‑memory/моки **в рабочем коде**, а `TASK/PLAN/ACCEPTANCE` требуют реальную
инфраструктуру (Postgres/RabbitMQ/MinIO/Docker Compose и т.п.), Reviewer обязан выдать `FAIL + CONTINUE`.

Переменная:

- `ORCH_REVIEWER_RED_FLAGS_MAX_CHARS` (по умолчанию 4000) — лимит длины секции `RED_FLAGS:`.

## Ретраи (устойчивость к сетевым сбоям)

Если во время шага (Planner/Implementer/Reviewer/Tech Writer) происходит временная сетевая ошибка (например `APIConnectionError`), оркестратор повторяет шаг целиком детерминированно с exponential backoff и пишет детали попыток в `artifacts.json`.

Переменные:

- `ORCH_RETRY_MAX_ATTEMPTS` (по умолчанию 3)
- `ORCH_RETRY_PLANNER_MAX_ATTEMPTS`, `ORCH_RETRY_IMPLEMENTER_MAX_ATTEMPTS`, `ORCH_RETRY_REVIEWER_MAX_ATTEMPTS`, `ORCH_RETRY_TECH_WRITER_MAX_ATTEMPTS`
- `ORCH_RETRY_BASE_DELAY_SECONDS` (по умолчанию 1)
- `ORCH_RETRY_MAX_DELAY_SECONDS` (по умолчанию 8)

## Лимиты шагов (max_turns)

Agents SDK ограничивает “длину” диалога агента в шагах (`max_turns`). Если агент зациклился (слишком много tool-вызовов/перепланирования), шаг может упасть с `MaxTurnsExceeded`.

Переменные:

- `ORCH_MAX_TURNS` (глобально, опционально)
- `ORCH_MAX_TURNS_PLANNER` (по умолчанию 6)
- `ORCH_MAX_TURNS_IMPLEMENTER` (по умолчанию 80)
- `ORCH_MAX_TURNS_REVIEWER` (по умолчанию 10)
- `ORCH_MAX_TURNS_TECH_WRITER` (по умолчанию 10)
