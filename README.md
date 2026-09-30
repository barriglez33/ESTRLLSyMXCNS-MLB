# Estrellas MLB y Mexicanos

GitHub-online RSS monitor for MLB stars, Mexican MLB/MiLB players, and postseason topics.

## High-recall discovery model

This version uses a **scan broadly, process narrowly** strategy.

For every search:

1. Google News can inspect up to **25 RSS results**.
2. Publication age is checked before expensive processing.
3. Already-stored URLs are skipped.
4. At most **6 fresh unseen Google articles** per search are decoded/extracted.
5. GDELT can return up to **15 results**, but at most **6 fresh unseen GDELT articles** per search are processed.

This improves article discovery without returning to the timeout-heavy behavior of opening every result.

## Search coverage

- **109 players**
- **28 postseason keywords**
- **137 total searches**

## Three-batch rotation

The searches remain split across three hourly batches.

Because each search is revisited every three hours, this version uses a rolling **4-hour window** to provide a one-hour safety margin for GitHub delays and late indexing.

## Less aggressive deduplication

Duplicate detection is now tighter:

- duplicate time window: **24 hours**
- title similarity: **0.8**
- title-token overlap: **0.68**
- body-lead similarity: **0.75**

This should preserve more genuinely distinct articles about the same player or postseason topic.

## Last 5 runs dashboard

Open:

`docs/run-stats.html`

The dashboard now shows:

- batch number
- searches executed
- fresh discovery candidates
- extraction attempts
- articles accepted before deduplication
- articles added after deduplication
- total stored articles

This makes it easier to see whether a drop happens during discovery, extraction, relevance filtering, or deduplication.

## Workflow

`.github/workflows/update.yml`

Runs every hour at minute `:41`.
