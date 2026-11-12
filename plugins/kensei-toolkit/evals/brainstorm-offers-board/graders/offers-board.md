---
type: llm
focus: last_message
---

The reply should end with a question about what to do next, offered as a list of options (eval
sessions have no AskUserQuestion, so plain text is expected). PASS if that list of options itself
includes creating a task board for the plan (e.g. «Создать доску», "Create a board") as one of the
choices, alongside at least two of: save only, start implementing here, revise the plan.
A `claude "/kensei-toolkit:board …"` launch line shown elsewhere in the reply does NOT count as the
option — the board must be one of the choices in the question. FAIL if it is not, or if the reply
says a board was already created. The language does not matter.
