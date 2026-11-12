---
name: brainstorm
description: Collaborative design dialogue before implementation — turns an idea into a validated design through focused questions, honest challenge, approach exploration, and incremental validation. Use when the user explicitly asks to brainstorm or design together — "brainstorm", "let's brainstorm", "help me design", "explore options for", «брейншторм», «побрейнштормим», "давай обсудим идею", "помоги спроектировать", "продумай со мной". Not for routine implementation requests or quick questions.
argument-hint: "[topic or idea]"
---

# Brainstorm — turn an idea into a design through dialogue

Collaborative and conversational. The deliverable is a validated design captured in a brainstorm doc plus a standalone implementation plan; implementation is a separate decision at the end, made by the user. If `$ARGUMENTS` is non-empty, it's the topic — start from it instead of asking what to brainstorm.

## Custom rules

If `.claude/brainstorm-rules.md` exists in the project, Read it before starting. Its content (design preferences, technology constraints, conventions, where docs go) supplements this skill's process — it refines the dialogue and can set the doc location, but it doesn't replace the phases. If the file doesn't exist, proceed silently.

## Scale check

Gauge the size before committing to the full process:

- **Small** — a utility, a single component, an isolated tweak. Compress: quick context check, at most one clarifying question, one recommended approach with an alternative mentioned in passing, design in a single message. No brainstorm doc or plan file unless the user asks. End by asking whether to implement now.
- **Large** — a feature, a system, an architectural decision. Full process below.

When genuinely unsure, ask the user which depth they want.

## Asking questions

The user's attention is the scarce resource; a session of 25 single-answer prompts wears it out. Each question should settle exactly one decision, and each AskUserQuestion call should earn its round-trip:

- **One decision per question.** A question never bundles two choices into one set of options.
- **Group independent questions.** When several enumerable questions don't depend on each other's answers, ask them in one AskUserQuestion call (up to 4). Ask a question on its own when its options depend on an earlier answer.
- **Options cover the real answer space.** Make them mutually exclusive alternatives the user would actually pick between; put the recommended one first with "(Recommended)". If an option would only make sense with a caveat, the framing is wrong — rephrase the question.
- **Edits come through Other.** AskUserQuestion always offers a free-text Other, so an option like "Есть правки" / "Needs changes" adds a round-trip with nothing in it. Offer concrete alternatives instead, and tell the user that corrections go in Other.
- Free-form questions only when the answer space is genuinely open.

## Phase 1 — Understand

1. Gather context first: relevant files, docs, recent commits (`git log --oneline -15`). In a large or unfamiliar codebase, delegate the sweep to an Explore agent and keep only its conclusions.
2. Ask what the context doesn't answer. Cover: purpose (what problem this solves), constraints, success criteria, integration points.
3. When you can state the problem back in two sentences, do so and get the user's confirmation before moving on.

## Phase 2 — Challenge

Before designing, stress-test the idea:

- What assumptions is it standing on, and which are unverified?
- What's the simplest thing that could work instead — including "do nothing" or an existing tool?
- What's the riskiest part, and what breaks if it's wrong?

Share findings briefly and honestly. If the idea isn't worth building or a far simpler path exists, say so directly with reasoning — "don't build it" is a legitimate brainstorm outcome. If the user decides to proceed anyway, that's their call; continue without relitigating.

## Phase 3 — Explore approaches

Propose 2–3 genuinely different approaches with trade-offs. Lead with the recommended one and explain why. Keep it conversational, not a formal document:

```
I see three approaches:

**Option A: [name]** (recommended)
- how it works: ...
- pros: ...
- cons: ...

**Option B: [name]**
- ...
```

Then ask which one to take. The user picks before you move on.

## Phase 4 — Design in sections

Validate the design incrementally — a misunderstanding caught in a section costs minutes, in implementation it costs days.

Choose sections that fit the topic instead of forcing one template:

- **Code / system**: architecture, components, data flow, error handling, testing
- **Gameplay feature**: mechanics, data/config, player-facing behavior, edge cases, tuning knobs
- **UX / UI**: flows, states, components, feedback

Present sections in batches of up to three, each 200–300 words, in one message. Then ask one multiSelect AskUserQuestion — "Which sections need changes?" — with one option per section in the batch plus "All good, continue"; the user writes the changes in Other. A picked section or text in Other takes precedence over "All good". If they pick a section without saying what to change, ask about it in plain text. Apply the edits, show only the changed parts, and move to the next batch.

Backtrack freely when something doesn't hold up, including sections already accepted.

## Phase 5 — Capture and hand off

The brainstorm ends with two files on disk and a question, with implementation not yet started. Stay out of plan mode (no EnterPlanMode) and leave code untouched until the user chooses **Start here**: a plan that lives only in this session dies with it, and the user may want to run it later, in another terminal, worktree or machine.

1. Choose the docs directory. Brainstorm docs are the user's personal notes, so they stay out of the project unless the project already keeps them there:
   1. the location set in `.claude/brainstorm-rules.md`, if any;
   2. else the project's existing `docs/brainstorms/` — only if that directory already exists;
   3. else `~/docs/brainstorms/`.

   Create directories only under `~/docs/brainstorms/`, at a location set in `brainstorm-rules.md`, or where the user asked. Name the full path in your message before writing.
2. Write the brainstorm doc as `<dir>/YYYY-MM-DD-<slug>.md`:
   - problem statement and constraints
   - chosen approach and why
   - rejected alternatives with one-line reasons
   - the validated design (sections from Phase 4)
   - open questions
3. Write the implementation plan next to it as `<dir>/YYYY-MM-DD-<slug>-plan.md`. Its reader is a fresh session with no memory of this dialogue, so it must stand on its own:
   - a link to the brainstorm doc and a 2–3 sentence goal
   - context the executor would otherwise have to rediscover: repo/project path, key files and entry points, conventions that matter
   - ordered steps; each names the files it touches and how to verify it is done (a command, a test, an observable behavior)
   - acceptance criteria for the whole change
   - out of scope — what was deliberately left out, so the executor doesn't "helpfully" add it
   - open questions the executor must ask the user about rather than decide

   Concrete over generic: real paths, real names, real commands. Every step says how, not just "implement X".
4. Show both paths and the ready-to-paste launch lines, using the plan's absolute path — the first runs the plan as is, the second starts from a task board for it:
   ```
   claude "Implement the plan in /abs/path/to/…-plan.md — read it and the brainstorm doc it links first"
   claude "/kensei-toolkit:board /abs/path/to/…-plan.md"
   ```
5. Ask via AskUserQuestion — "Design and plan are saved. What's next?":
   - **Save only** — stop here; the user picks the plan up whenever and wherever it suits them
   - **Start here** — implement in this session, following the plan file step by step
   - **Create a board** — invoke `/kensei-toolkit:board <plan path>`: it turns the plan into a task board artifact the user follows and steers from any device, binds this session to it, and stops; implementation still waits for the user to say when
   - **Revise the plan** — the user describes the change in Other; apply it to the plan file, then ask again. If they pick it without saying what to change, ask in plain text.

   Implementation starts only on an explicit **Start here**.

## Early exit

If mid-process the user says "enough, just do it" or clearly wants to move on: summarize the decisions made so far in 3–5 bullets, write the brainstorm doc and the plan from what's known (Phase 5, steps 1–4), and proceed to their requested action. Skip the remaining phases. An explicit "just do it" counts as **Start here**; "enough" alone does not — save and ask.

## Key principles

- YAGNI ruthlessly — strip everything the problem doesn't demand; keep scope minimal.
- Lead with a recommendation and reasoning; the user decides.
- Duplication vs abstraction: when repetition appears in a design, present both options with trade-offs and let the user choose.
- Honest challenge beats polite agreement — the user came for thinking, not validation.

## Language

Use the language the user writes in for everything they read: the dialogue, AskUserQuestion questions and options, the brainstorm doc and the plan file. This skill's own wording (option names like **Start here**) is a description — translate it.
