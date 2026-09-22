# Estrellas Resto MLB

GitHub-online-only RSS monitor for **109 MLB / MiLB players**.

## Rotation

The player list is split into two alternating batches:

- **Batch 1:** 55 players
- **Batch 2:** 54 players

The workflow runs every hour and remembers the next batch in:

`data/state.json`

Each batch searches a rolling **2-hour publication window**, so each player is checked once every two runs while preserving overlap.

## New player metadata

Players may include:

- `team`
- `level` (`MLB`, `MiLB`, or `Free Agent`)
- `batch`

For players with a team listed, the team is also used as optional search context to reduce false positives.

## Features

- multilingual discovery using GDELT + Google News
- player-name searches with MLB/baseball context
- team context for the newly added players when available
- MiLB/prospect context for minor leaguers
- automatic Spanish translation
- `[SOURCE]` at the beginning of every RSS title
- smart duplicate detection across publishers/languages
- keeps the most complete repeated article
- alternate repeated sources stored in `alternate_sources`
- master RSS plus one RSS feed per player
- GitHub Actions only; no local PC required

## Workflow

`.github/workflows/update.yml`

Action name:

**Update Estrellas Resto MLB RSS**

Runs every hour at minute `:41`.

## Output

- `docs/feed.xml`
- `docs/players/*.xml`
- `docs/index.html`
- `data/articles.json`
- `data/state.json`

## Current batch totals

Batch 1: **55**

Batch 2: **54**

Total: **109**

## GitHub Pages

For a public repository:

**Settings → Pages → Deploy from a branch → main → /docs**

The general feed will normally be:

`https://YOUR-USERNAME.github.io/estrellas-resto-mlb/feed.xml`
