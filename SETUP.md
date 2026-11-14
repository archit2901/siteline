# Siteline Claude Code kit — how to use it

This folder is the starting repo. It gives every Claude Code session the same project memory (`CLAUDE.md` files),
the same shared shapes (`contracts/models.py`), team settings and commands (`.claude/`), and a paste-in prompt for
every milestone (`prompts/<name>.md`). Claude Code builds everything else.

## 1. Archit creates the repo (5 minutes, before anyone else starts)
```bash
# create an empty PUBLIC repo named siteline on GitHub first (rule: created after 8 pm Nov 13)
git clone git@github.com:<you>/siteline.git && cd siteline
unzip ~/Downloads/siteline-claude-kit.zip -d /tmp/kit && cp -R /tmp/kit/siteline/. .
python3 -m venv .venv && source .venv/bin/activate && pip install "pydantic>=2" pyyaml
PYTHONPATH=. python -m contracts.export        # must print "exported N schemas"
git add -A && git commit -m "Siteline kit: contracts, CLAUDE.md files, prompts" && git push -u origin main
```
Add Gaurav, Parth and Jayan as collaborators. Optional: drop the two design PDFs into `docs/` for extra context.

## 2. Everyone (10 minutes)
```bash
git clone git@github.com:<archit>/siteline.git && cd siteline
git switch -c <your branch>      # Archit: agent · Gaurav: web · Parth: geo · Jayan: sites
cp .env.example .env             # fill in only the keys you have; never commit .env
python3 -m venv .venv && source .venv/bin/activate && pip install "pydantic>=2" pyyaml
npm install -g @anthropic-ai/claude-code   # if Claude Code isn't installed yet
claude                                     # start it in the repo root
```
First message to Claude Code: open `prompts/<your name>.md` and paste the **Setup** block.
Do the human tasks listed at the top of your prompts file at the same time.

## 3. The working loop (every milestone)
1. Paste the milestone block from your prompts file.
2. Let it work; answer its questions; keep it inside your folders.
3. `/milestone P1` (or the current one) — it checks the done criteria with evidence.
4. Commit (small commits are fine), then `/handoff` and post the note in team chat.

## 4. Merging at each milestone (P0 4 am · P1 2 pm · P2 8 pm · P3 2 am)
- Each person: `git fetch && git merge origin/main`, run your tests, then ask Claude:
  "Use the boundary-reviewer subagent on this branch." Fix any BLOCK items, push your branch, open a PR.
- Archit merges in order geo → sites → agent → web, runs `make e2e` (from P1 on), then
  `git tag pN && git push --tags`. If something breaks later, demo the last tag.

## 5. Teammates without Claude Max
The prompts work with any coding assistant (for example Gemini CLI with the hackathon's Gemini access). Start each
block with: "Read CLAUDE.md and <your folder>/CLAUDE.md first." Gemini CLI looks for `GEMINI.md` context files, so
you can also copy `CLAUDE.md` to `GEMINI.md` locally (don't commit the copy).

## 6. Parallel Claude Code sessions (optional)
One branch per session: `git worktree add ../siteline-infra -b infra`, then run `claude` inside `../siteline-infra`.
Merge the extra branch into your main branch before the milestone merge.

## What's in the kit
| Path | What it is |
|---|---|
| `CLAUDE.md` | Project memory: rules, ownership, architecture, milestones, commands |
| `services/*/CLAUDE.md`, `web/CLAUDE.md`, `connectors/CLAUDE.md` | Detailed spec per folder |
| `contracts/models.py`, `contracts/export.py` | The shared shapes (Pydantic v2) and JSON Schema export |
| `docs/DESIGN.md` | Condensed design: workflow, scoring math, worked examples, guardrails, demo |
| `prompts/archit.md`, `gaurav.md`, `parth.md`, `jayan.md` | Paste-in prompts per milestone + human tasks |
| `.claude/settings.json` | Shared permissions (blocks reading .env, force-push, pushing to main) |
| `.claude/commands/` | `/milestone`, `/handoff`, `/contract-change` |
| `.claude/agents/boundary-reviewer.md` | Read-only pre-merge reviewer subagent |
| `packs/`, `evals/`, `catalog/` | Starter YAML: base + orthopedics packs, 4 eval cases, empty Houston catalog |
| `Makefile`, `docker-compose.yml`, `.env.example`, `.gitignore` | Run, test, deploy scaffolding |
