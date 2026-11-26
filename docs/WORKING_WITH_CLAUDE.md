# Working with Claude on this repo

Three habits, one page. `CLAUDE.md` is what Claude reads; this is what the person at the
keyboard does.

## One topic per chat

Start a new chat when the task changes. A chat carries everything said in it into every later
turn, so an unrelated second task is paid for twice: it costs tokens on every message, and the
first task's details sit in the context and pull the answer the wrong way. One issue, one chat
is the same rule `/take-issue` already follows (one issue per run), and `docs/PARALLEL_TRACKS.md`
is the same rule for the working tree (one worktree per issue).

Finished a task and want to start the next? New chat. Need the old one's conclusion? Paste the
two lines that matter, not the transcript.

## Do not switch model in the middle of a chat

Pick the model when the chat starts and keep it. The prompt cache belongs to a model, so a
switch makes the whole conversation so far be read again at full price, and the new model
starts from a history written by another. If the task turns out to need a different model,
open a new chat and carry the conclusion over.

`/cost` shows the cache ratio; a chat that has been long and cheap per turn is a chat to leave alone.

## Which model for which job

| Model | Use it for | Examples here |
|---|---|---|
| **Sonnet** | Routine work, which is most of it | a bug with a clear repro, a copy change in `messages.py`, adding a test, a script or workflow modelled on an existing one, docs sync, running `/test`, `/logs`, `/health` |
| **Opus** | Hard reasoning, where a wrong answer is expensive to find later | a race between two workers, an FSM invariant across the guard chain, a matching or scheduling change, a security review, a bug nobody can reproduce |
| **Fable** | The hardest planning only | the shape of a multi-week change, a decomposition like the PRO-139 guard-chain split, a migration plan that has to be right before anyone writes code |

When unsure, start on Sonnet. A chat that stalls on reasoning, not on typing, is the signal to
open a new one on Opus with the problem restated. Fable is for deciding *what* to build; once the
plan exists, building it is Sonnet's work.

Most subagents (`.claude/agents/`) pin their own `model:`, so they do not follow the chat's
choice (`code-reviewer` is the one that inherits it). Their tool grants are in `docs/AGENT_PACK.md`.
