# paperqueue

A stack of papers to read. Paste a link, push it; pop from the top or take from the bottom to read it.
Works as a terminal app and as a web page (installable on phones), with one queue synced across devices.

## Terminal app

```sh
uv run paperqueue          # from this directory
uv tool install .          # or install the `paperqueue` command globally
```

| key | action |
|---|---|
| `Enter` | push the pasted link (arXiv abs/pdf links, bare ids, or any URL) |
| `p` | pop the top (most recent) paper: remove it and open its PDF |
| `b` | take the bottom (oldest) paper: remove it and open its PDF |
| `o` | open the highlighted paper's PDF without removing it |
| `O` | open the highlighted paper's web page instead of the PDF |
| `d` | delete the highlighted paper |
| `a` / `Esc` | focus the input / the list |
| `h` | history of read papers: `r`/`Enter` re-adds one to the top of the stack, `o` opens it, `x` forgets it, `Esc` goes back |
| `q` | quit |

Reading opens the PDF when one can be found: arXiv links, pages with a PDF link or `citation_pdf_url`,
Scholar Inbox links (resolved to arXiv by title) and OpenReview forum links. Titles and PDFs are fetched in the background.

Without a server configured, the stack lives in `~/.local/share/paperqueue/stack.json`.

## Synced server (Cloudflare Worker)

`web/` is a Cloudflare Worker with a Durable Object that holds the shared stack, serves the web page,
exposes a small JSON API and resolves titles/PDFs itself. One secret token protects it.

### Deploy

1. In the Cloudflare dashboard of the account you want to use: *My Profile → API Tokens → Create Token →
   "Edit Cloudflare Workers"* template. Copy `web/.env.example` to `web/.env` and fill in the token and the
   account ID (shown on the account's Workers overview page). `.env` is git-ignored.
2. Generate the access token that clients will use, e.g. `openssl rand -hex 32`, and keep it somewhere safe.
3. Deploy:

   ```sh
   cd web
   npm install
   set -a; . ./.env; set +a          # export CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID for wrangler
   npx wrangler secret put TOKEN     # paste the access token from step 2
   npx wrangler deploy               # prints the https://paperqueue.<subdomain>.workers.dev URL
   ```

### Use it

- **Phone / browser**: open the Worker URL, paste the access token once, then "Add to Home Screen".
  On Android the installed app also appears in the share sheet, so links can be shared straight to the stack.
  The page mirrors the TUI: same table, same key bindings (`p`/`b`/`o`/`O`/`d`/`a`/`h`, `↑↓` or `j`/`k` to move,
  `Esc` to leave the input, `r`/`x` in history). On a phone, tap a row to put the cursor on it and tap a key in
  the footer bar to act on it. Quick-add from any desktop browser with a bookmarklet:

  ```
  javascript:location.href='https://paperqueue.<subdomain>.workers.dev/?url='+encodeURIComponent(location.href)
  ```
- **Terminal**: create `~/.config/paperqueue/config.toml`:

  ```toml
  url = "https://paperqueue.<subdomain>.workers.dev"
  token = "<access token>"
  ```

  (or set `PAPERQUEUE_URL` and `PAPERQUEUE_TOKEN`). The TUI then reads and writes only through the server
  and shows "synced" in the header. Run `paperqueue import` once to push an existing local `stack.json`.

### Local development

```sh
cd web && npm install
npx wrangler dev                  # http://localhost:8787, token "dev-token" from .dev.vars
npx vitest run                    # Durable Object, API and link-resolution tests
PAPERQUEUE_URL=http://localhost:8787 PAPERQUEUE_TOKEN=dev-token uv run paperqueue
```

### API

All routes need `Authorization: Bearer <token>` and answer JSON. Mutations return `{paper, state}`.

| method & path | action |
|---|---|
| `GET /api/state` | `{version, papers, history}` (papers bottom→top, history oldest→newest) |
| `POST /api/papers` `{url}` | normalize and push (409 if already queued) |
| `POST /api/pop` / `POST /api/take` | remove the top / bottom paper into history (404 when empty) |
| `DELETE /api/papers?url=` | delete a paper into history |
| `POST /api/history/restore` `{url}` / `DELETE /api/history?url=` | re-add / forget |
| `POST /api/import` `{papers, history}` | merge a local `stack.json` |
