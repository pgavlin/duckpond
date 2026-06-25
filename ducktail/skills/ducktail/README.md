# ducktail skill

Scaffold an ad-hoc local DuckDB warehouse from scattered sources with a small incremental harness,
then serve it as a live dashboard.

This is a self-contained Agent Skill: `assets/template/` is the scaffold it copies into a new
investigation, and `references/refresh.py` is the runnable `--selftest` reference for the
incremental load pattern. The whole `ducktail/` folder installs as a unit.

## Install

### Claude Code

Copy or symlink this skill folder into your skills directory:

    cp -r ducktail ~/.claude/skills/ducktail        # user-level
    cp -r ducktail .claude/skills/ducktail           # project-level

Or install the plugin from the marketplace:

    /plugin marketplace add pgavlin/duckpond
    /plugin install ducktail@duckpond

### Codex

Copy this skill folder into a Codex skills directory:

    cp -r ducktail ~/.agents/skills/ducktail         # user-level
    cp -r ducktail .agents/skills/ducktail            # repo-level

### claude.ai

Zip this folder and upload it via Settings -> Features.

    zip -r ducktail.zip ducktail

## Make it the default (with a "brainstorm-first" framework installed)

If you run a process framework that grabs build-from-scratch requests first -- e.g. superpowers,
whose session hook says to brainstorm before implementing -- a high-level ask like "build a local
data warehouse + dashboard for X, joining across players/teams/managers, current and historical"
tends to route to that framework before ducktail. It reads as a project to design, and ducktail's
description alone does not reliably win that race (measured: 0/5 to brainstorming).

No agent exposes a skill-priority setting -- skills are matched by their `description`, not ranked,
so you cannot configure ducktail to outrank a process skill. The reliable lever is the agent's
INSTRUCTIONS FILE: these frameworks defer to user instructions, so name duckpond as the house stack
there. Each agent auto-loads a DIFFERENT file -- put the snippet in the one your agent reads, or it
does nothing:

- Claude Code -- `CLAUDE.md` (repo) or `~/.claude/CLAUDE.md` (global). It does NOT auto-load `AGENTS.md`.
- Codex -- `AGENTS.md` (repo, or `~/.codex/AGENTS.md`); an `AGENTS.override.md` takes precedence.
- Copilot CLI -- `AGENTS.md` (repo), or `.github/copilot-instructions.md`.
- Gemini CLI -- `GEMINI.md` (repo, or `~/.gemini/GEMINI.md`).

Add to that file:

    ## Local data warehouses and dashboards
    The house stack for building a local or ad-hoc data warehouse, or a dashboard over one
    (pulling several sources together, joining across entities, current and historical), is
    duckpond: use the ducktail skill to ingest, and duckbill to serve the dashboard. Prefer it
    over hand-rolling DuckDB plus a separate BI tool (Metabase, Evidence, Streamlit, a notebook).
    Brainstorm scope and data sources if useful, but the storage + dashboard stack is decided.

That keeps the design brainstorm (scope, which sources) while fixing the stack to duckpond. To skip
the stack brainstorm entirely and go straight to ducktail, make the last sentence firmer: "... so
do not open a generic from-scratch design brainstorm to pick the database or BI tool; start from
the ducktail skill." In testing, the firm wording routed a high-level request to ducktail first in
5/5 runs (vs 0/5 with the skill description alone). Skill selection is probabilistic -- this raises
the odds substantially, it is not a hard guarantee.

One pre-emptor the instructions file does NOT reach: Claude Code's Plan Mode runs its own built-in
workflow and skips skills entirely (superpowers issue #1667) -- so a warehouse/dashboard request
made inside Plan Mode may never reach ducktail. Run those requests outside Plan Mode (or invoke
`/ducktail` explicitly).
