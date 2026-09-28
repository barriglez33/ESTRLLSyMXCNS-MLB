# Estrellas MLB y Mexicanos

GitHub-online RSS monitor for MLB stars, Mexican MLB/MiLB players, and postseason topics.

## Search coverage

The project now tracks:

- **109 players**
- **28 postseason keywords**
- **137 total searches**

### Postseason categories

- Core Postseason
- Round Formats
- Team & Series Battles
- Strategy & Analytics
- Awards & Media

## Three-batch rotation

The searches are split into:

- **Batch 1:** 46 searches
- **Batch 2:** 46 searches
- **Batch 3:** 45 searches

The workflow runs once per hour:

```text
Hour 1 → Batch 1
Hour 2 → Batch 2
Hour 3 → Batch 3
Hour 4 → Batch 1
```

Because each search runs once every three hours, the scanner uses a rolling **3-hour news window**.

## Last 5 runs dashboard

Open:

`docs/run-stats.html`

This page shows the latest five completed runs, including:

- batch number
- searches executed
- articles added after deduplication
- articles accepted before deduplication
- total stored articles

The dashboard is regenerated automatically after every successful scanner run.

Its history is stored in:

`data/run_history.json`

Only the latest **5 runs** are kept.

## Feeds

Master:

`docs/feed.xml`

Per player:

`docs/players/*.xml`

Per postseason keyword:

`docs/topics/*.xml`

Dashboard:

`docs/index.html`

Run statistics:

`docs/run-stats.html`

## Performance features

- 3 rotating batches
- rolling 3-hour window
- old Google News items discarded before URL decoding
- only fresh unseen URLs are extracted
- new stories translated first
- limited older translation repairs
- smart duplicate detection
- stable RSS build dates
- automatic Git push retries

## GitHub Actions

Workflow:

`.github/workflows/update.yml`

Runs every hour at minute `:41`.
