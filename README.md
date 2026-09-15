# Ubaid Footy Alerts

A free Pakistan-football news bot: it watches Google News for a list of
keywords and players, and posts any new matching article straight to a
Discord channel — pinging `@everyone` so nobody misses it.

No server, no VPS, no paid API. It runs entirely on GitHub Actions'
free scheduler and costs nothing as long as this repo stays public.

## How it works

| File | Role |
|---|---|
| [`keywords.json`](keywords.json) | The list of Google News search queries — one per keyword/player. |
| [`check.py`](check.py) | Fetches each query's Google News RSS feed, skips articles already recorded in `seen.json`, sorts the rest oldest → newest, and posts each as a Discord embed with `@everyone`. |
| [`seen.json`](seen.json) | Map of article link → first-seen time. Committed back to the repo after every run so nothing gets posted twice. |
| [`.github/workflows/check.yml`](.github/workflows/check.yml) | GitHub Actions workflow that runs `check.py` every 10 minutes and commits the updated `seen.json`. |

Each run:
1. Loads `keywords.json` and queries Google News RSS for every entry.
2. Drops anything already in `seen.json` (and anything duplicated across
   keywords in the same run).
3. Sorts what's left by publish date so the channel reads in proper
   chronological order, not grouped by keyword.
4. Posts each as a Discord embed (title, link, source, colored sidebar,
   timestamp) with an `@everyone` ping, one message every ~1 second to
   stay well under Discord's rate limit.
5. Saves the updated `seen.json` and commits it.

### Known limitation: no article thumbnails

Google News RSS links point to a Google interstitial page that redirects
to the real article via JavaScript, not a normal HTTP redirect. That
means neither this script nor Discord's own link preview can reach the
publisher's page to grab its image. Getting a real thumbnail would
require running a full headless browser on every check (expensive, slow,
and overkill for a free 10-minute-interval bot), so embeds ship without
one on purpose.

## One-time setup

1. **Discord webhook**: in the target channel, go to
   **Edit Channel → Integrations → Webhooks → New Webhook**, then
   **Copy Webhook URL**.
2. **GitHub secret**: in your own terminal (so the URL never passes
   through chat or gets committed to the repo):
   ```bash
   gh secret set DISCORD_WEBHOOK_URL --repo <owner>/<repo>
   ```
   Paste the webhook URL when prompted. (Or add it manually via
   **Settings → Secrets and variables → Actions → New repository
   secret**.)

That's the whole setup — the workflow picks it up automatically on the
next scheduled run.

## Adding or removing keywords

1. Edit `keywords.json`. Each entry is a full Google News search query.
   - Wrap exact names in `\"quotes\"` so it matches the phrase, not the
     words separately.
   - Keep (or add) a trailing `football` on generic/common names — it
     cuts down false matches a lot (e.g. plain "Ali Khan" or "Hassan
     Ali" are common Pakistani names with lots of unrelated news).
2. Commit and push.
3. Done — the next scheduled run (within 10 minutes) picks up the new
   list automatically. No code changes needed.

To remove a keyword, delete its line from the JSON array.

## Manually testing

- **Trigger a run now**: Actions tab → "Football News Checker" →
  "Run workflow", or:
  ```bash
  gh workflow run check.yml --repo <owner>/<repo>
  ```
- **Watch it**:
  ```bash
  gh run watch --repo <owner>/<repo>
  ```
  or refresh the Actions tab. The log prints `[posted] ...` for every
  article sent, or a `Done. N new article(s) posted.` summary line.
- **Force a test message**: remove one known link from `seen.json`
  (or clear it to `{}`) and re-run — that article gets treated as new
  and reposted. Clearing it entirely will re-flood the channel with
  every currently-matching article, so prefer removing just one.

## Notes

- Schedule is every 10 minutes (`.github/workflows/check.yml`), the
  fastest practical interval on GitHub Actions' free public-repo tier.
- `seen.json` is capped at the 3000 most recently seen links so it
  doesn't grow forever.
- Keep this repo **public** — private repos only get 2,000 free Actions
  minutes/month, which this schedule would burn through in under two
  weeks. Public repos get unlimited free Actions minutes. Nothing
  sensitive lives in the code; the webhook URL is stored only as an
  encrypted repository secret.
