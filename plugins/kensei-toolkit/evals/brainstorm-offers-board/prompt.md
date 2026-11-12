---
description: brainstorm 2.3.0 offers a task board as one of the closing options and only offers it — it never invokes the board skill on its own.
expected_outcome: The brainstorm skill fires, saves the brainstorm doc and the plan under docs/brainstorms/, and the closing question offers creating a board next to saving only, starting here and revising the plan; the board skill is never invoked and no source file is edited.
tags: [brainstorm, board]
max_turns: 20
timeout_seconds: 480
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

/kensei-toolkit:brainstorm офлайн-кэш ответов API для CLI в src/client.py

Всё уже решили, вопросы не нужны: кэш — JSON-файлы в ~/.cache/cli по URL; читаем из кэша без
сети, пишем после успешного запроса; `cli cache clear` чистит; истечение срока — вне рамок.
Шаги: модуль src/cache.py с тестами, использование в fetch, раздел в README.
Хватит, этого достаточно — сохрани бриф и план.
