# Репозиторные сценарии

Каталог содержит 897 задач из 12 открытых проектов. Каждый JSON фиксирует
репозиторий, исходный коммит, строку в источнике, хеш постановки и регрессионные
проверки. Исправления, патчи тестов и подсказки из датасета хранятся у источника.
Отбор воспроизводим и ограничен задачами с различимыми заголовками и конечным
списком регрессионных проверок.

Источник — [SWE-bench, test split](https://huggingface.co/datasets/SWE-bench/SWE-bench/tree/c6fe717fd7a4c3ac1daa4055a4fd082c6a1d28a2).
Команда `source` возвращает адрес файла по закреплённой ревизии.
Работа с постановкой требует дополнительной зависимости PyArrow.

```sh
python -m pip install -e '.[dev,benchmark]'
agent-orchestrator-bench list --summary
agent-orchestrator-bench list --repo pallets/flask
agent-orchestrator-bench list --repo django/django --match migrations --limit 10
curl -fL "$(agent-orchestrator-bench source)" -o swe-bench-test.parquet
agent-orchestrator-bench verify swe-bench-test.parquet
```

Из вывода `list` выбери идентификатор кейса. Подготовка скачивает один коммит
в новый detached checkout. Файл задачи создаётся из исходной постановки.

```sh
CASE=pallets__flask-4045
agent-orchestrator-bench checkout "$CASE" /tmp/benchmark-checkout
agent-orchestrator-bench prompt "$CASE" swe-bench-test.parquet /tmp/benchmark-task.md
```

Подготовь окружение целевого проекта и команду, которую можно воспроизвести.
Просмотри код и зависимости перед запуском. Параметр `--check` выполняет команду
на хосте. Для незнакомого кода запускай весь прогон в отдельном окружении.

```sh
agent-orchestrator --workspace /tmp/benchmark-checkout --task /tmp/benchmark-task.md --check 'python -m pytest -q'
agent-orchestrator-bench record "$CASE" sdk /path/to/run-directory >> outcomes.jsonl
```

Для сравнения подготовь второй checkout того же кейса и запусти вариант
`--implementer codex` с той же командой проверки. Запиши результат под меткой
`codex`, затем сравни варианты.

```sh
agent-orchestrator-bench compare outcomes.jsonl sdk codex
```

Сравнение учитывает только пары с совпадающими кейсом, исходным коммитом и
командами проверки. Непарные записи показываются отдельно. Статус `accepted`
означает, что патч принят локальным оркестратором с указанными проверками.
Для оценки по методике SWE-bench нужен его отдельный
[Docker harness](https://github.com/SWE-bench/SWE-bench).

Повторно собрать каталог можно в пустую папку.

```sh
PYTHONPATH=. python tools/import_swe_bench.py swe-bench-test.parquet /tmp/rebuilt-scenarios --count 897
```
