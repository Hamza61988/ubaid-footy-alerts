# Ubaid Footy Alerts

Watches Google News for Pakistan football keywords/players and posts new
articles to a Discord channel. Runs entirely on GitHub Actions — no
server, no paid API.

## How it works

- `keywords.json` — list of Google News search queries (one per
  keyword/player).
- `check.py` — fetches each query's Google News RSS feed, skips articles
  already recorded in `seen.json`, and posts new ones to a Discord
  webhook.
- `seen.json` — link -> first-seen timestamp, committed back to the repo
  after every run so we never repost the same article.
- `.github/workflows/check.yml` — runs `check.py` every 10 minutes via
  GitHub Actions, using the `DISCORD_WEBHOOK_URL` repository secret.

## One-time setup

1. **Discord webhook**: Server Settings → Integrations → Webhooks →
   New Webhook → pick the target channel → Copy Webhook URL.
2. **GitHub secret**: in your own terminal (so the URL never gets typed
   into chat or committed to the repo), run:
   ```bash
   gh secret set DISCORD_WEBHOOK_URL --repo <owner>/<repo>
   ```
   and paste the webhook URL when prompted. Or add it via
   Settings → Secrets and variables → Actions → New repository secret.

## Adding or removing keywords

1. Edit `keywords.json`. Each entry is a full Google News search query —
   wrap exact names in `\"quotes\"` and keep the trailing `football` to
   cut down on false matches for common names.
2. Commit and push the change.
3. That's it — the next scheduled run (within 10 minutes) picks up the
   new list automatically. No need to touch `check.py` or the workflow.

To remove a keyword, just delete its line from the JSON array.

## Manually testing

- **Trigger a run now**: Actions tab → "Football News Checker" →
  "Run workflow", or `gh workflow run check.yml`.
- **Watch it**: `gh run watch` (or refresh the Actions tab) to see logs —
  it prints `[posted] ...` for every article sent, or a summary line if
  nothing new was found.
- **Force a test message**: temporarily delete one known-good link from
  `seen.json` (or clear it to `{}`) and re-run — that article will be
  treated as new and posted again.
