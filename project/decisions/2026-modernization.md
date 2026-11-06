# Источники обновления оркестратора

Даты в этой таблице относятся к публикациям, на которые опиралось обновление.

| Период | Источники | Что применили |
| --- | --- | --- |
| Май | [AI Weekly Digest](https://ai-weekly-updates.vercel.app/), [Rethinking the Agent Harness](https://www.oreilly.com/radar/this-week-in-ai-rethinking-the-agent-harness/) | Отказ с ненулевым кодом, полный патч для ревью, проверка формата вердикта |
| Июнь | [This Week in AI, июньский архив](https://www.oreilly.com/radar/topics/this-week-in-ai/page/2/), [Agentic Code Review](https://www.oreilly.com/radar/agentic-code-review/) | Раздельные отчёты, отказ от повторного запуска ролей с побочными эффектами, защита метаданных Git |
| Июль | [AI Weekly Digest](https://ai-weekly-updates.vercel.app/), [This Week in AI, июльский архив](https://www.oreilly.com/radar/topics/this-week-in-ai/) | Проверки под контролем оркестратора, удаление произвольного shell из SDK-исполнителя, остановка при неполном патче |
| Август | [This Week in AI, августовский архив](https://www.oreilly.com/radar/topics/this-week-in-ai/), [Codex as a platform, 19 августа](https://developers.openai.com/blog/codex-as-a-platform) | Явный выбор checkout, задачи и проверок, поддержка Codex CLI как исполнителя |
| Сентябрь | [This Week in AI, сентябрьский архив](https://www.oreilly.com/radar/topics/this-week-in-ai/), [Agents SDK v0.22.1](https://github.com/openai/openai-agents-python/releases/tag/v0.22.1) | Проверка версии патча, ограничение данных в трассировке, исключение ключа OpenAI из окружения команд проверки |
| Октябрь и ноябрь | [This Week in AI, архив](https://www.oreilly.com/radar/topics/this-week-in-ai/), [Agents SDK v0.24.0, 15 октября](https://github.com/openai/openai-agents-python/releases/tag/v0.24.0) | Сохранение результатов по раундам, проверка с актуальной веткой SDK, автономные тесты в CI |

SDK предоставляет файловые инструменты, Codex CLI запускает агента в своей
песочнице. В обоих случаях оркестратор сам запускает обязательные проверки
и принимает вердикт ревью.
