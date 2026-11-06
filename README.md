# Agent Orchestrator

Локальный оркестратор для работы над задачей из Markdown. Planner составляет
план, Implementer меняет код, Reviewer проверяет патч и результаты команд.
Цикл исправлений ограничен восемью раундами. Код, план, замечания и проверки
можно сопоставить по отчёту прогона.

## Установка и демо

Нужны Python 3.10+, Git и API-ключ OpenAI для ролей Agents SDK.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Укажи ключ в `.env` и, если нужно, доступные модели в переменных
`ORCH_MODEL_*`. Команда `python -m orchestrator.main` читает
`project/tasks/current.md` и работает в `workspace/`. При пустом файле
задачи запускается демо с tiny-пакетом и pytest.

## Задача в отдельном checkout

Сделай чистый worktree целевого репозитория и подготовь отдельный Markdown
с задачей, ограничениями и критериями приёмки.

```sh
git -C /path/to/product worktree add --detach /path/to/task-worktree HEAD
python -m orchestrator.main --workspace /path/to/task-worktree --task /path/to/task.md --check 'python -m pytest -q'
```

Каждый `--check` задаёт отдельную команду. По умолчанию оркестратор запускает
`python -m pytest -q` своим интерпретатором. Команды запускаются локально
из целевого checkout без shell. Используй их с доверенным кодом и готовым
окружением. SDK-исполнитель получает файловые инструменты с ограничением по
каталогу.

Для Codex добавь `--implementer codex` к команде. Нужен установленный и
авторизованный Codex CLI. Оркестратор запускает его в режиме
`workspace-write` с отключённой сетью для команд песочницы и ограничением
`--codex-timeout` (900 секунд по умолчанию). Остальные роли работают
через Agents SDK.

## Результат

Оркестратор сверяет патч с базовым коммитом, включает staged и новые файлы,
запускает проверки и отклоняет ревью, если патч изменился во время проверки.
Код выхода `0` означает успешный прогон. Результат остаётся в checkout
для проверки и коммита.

В `project/reports/run-*/` сохраняются план, ответы ролей, полный патч каждого
раунда и `artifacts.json` с SHA базы, хешем патча, выводом проверок и вердиктом.
При слишком большом патче прогон останавливается, полный патч остаётся в
отчёте. Настрой лимит через `ORCH_REVIEWER_DIFF_MAX_CHARS` (по умолчанию
12 000 символов). Отчёты могут содержать фрагменты кода и вывод проверок.

Для внешнего checkout Tech Writer включается через `--write-project-notes`.
Контекст и инструкции для ролей находятся в `project/` и
`orchestrator/prompts/`.

## Локальные проверки

```sh
python -m pytest -q
(cd workspace && python -m pytest -q)
```

Для этих проверок API-ключ не нужен. [Источники изменений](project/decisions/2026-modernization.md).
