# Estrellas Mlb — Freshness Update

This patch makes the feed more current while preserving the high-recall discovery model.

- Discovery window: **2 hours**
- Hard publisher-date cutoff: **3 hours**, only when Trafilatura extracts a date with a real clock time
- Google News still inspects up to **25 RSS entries** and processes at most **6 fresh unseen candidates** per search
- Existing smart deduplication and history are preserved
- GitHub Actions schedule: `1,21,41 * * * *`
- `requirements.txt` includes the `selectolax<1.0` compatibility pin

Date-only metadata such as `2026-10-07` is not rejected because it does not provide enough precision for a three-hour cutoff.
