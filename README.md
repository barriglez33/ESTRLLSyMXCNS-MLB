# Yankees News

GitHub-online-only RSS monitor for New York Yankees coverage.

## Timeout-safe batching

The 45 tracked names are split into two alternating batches:

- **Batch 1:** 23 names
- **Batch 2:** 22 names

GitHub Actions runs every hour. A successful run switches to the other batch automatically.

The next batch is stored in:

`data/state.json`

## Rolling two-hour window

Each run only processes news from the previous **2 hours**.

This allows the batches to alternate without leaving a gap while avoiding expensive processing of older articles.

Google News publication dates are checked **before** URL decoding and article extraction.

## Features

- GDELT + Google News multilingual discovery
- Yankees / MLB context filtering
- automatic Spanish translation
- source included in every RSS title
- smart duplicate detection
- keeps the most complete version of repeated coverage
- master RSS plus individual feeds in `docs/people/`
- only new articles are translated immediately
- a maximum of 10 older failed translations are retried per run

## Workflow

`.github/workflows/update.yml`

Action name:

**Update Yankees News RSS**

Schedule:

Every hour at minute `:31`.

## Generated files

- `docs/feed.xml`
- `docs/people/*.xml`
- `docs/index.html`
- `data/articles.json`
- `data/state.json`

Do not delete `data/state.json` unless you intentionally want to reset the batch rotation.
