# paperqueue

A stack of papers to read. Paste a link, push it; pop from the top or take from the bottom to read it.
Each reading finishes with a required comment and a continuous 0–5 usefulness rating, which are stored with the paper in history.
Works as a terminal app and as a web page (installable on phones), with one queue synced across devices.

There are three ways to run it, pick the one you need:

1. **Terminal only**: the TUI keeps the stack in a local file. No server, no account.
2. **Your own server, on your machine**: run the sync server locally with `wrangler dev` ([Local development](#local-development)). No Cloudflare account needed.
3. **Your own server, on Cloudflare**: deploy `web/` to *your* Cloudflare account ([Deploy](#deploy)); the free plan is enough. The repository contains no account ID or credentials, so a deploy always goes to whichever account you log in with.

Prerequisites: [uv](https://docs.astral.sh/uv/) for the terminal app (it fetches Python 3.12+ itself),
and Node.js 22 or newer for the server.

## Terminal app

```sh
uv tool install git+https://github.com/paolotron/paperqueue   # install the `paperqueue` command
uv run paperqueue                                              # or run it from a clone
```

The app starts with the link input focused, so letters go into it: paste a link and press `Enter`,
then `Esc` to move to the list, where the keys below work.

| key | action |
|---|---|
| `Enter` | push the pasted link (arXiv abs/pdf links, bare ids, or any URL) |
| `p` | pop the top (most recent) paper: remove it and open its PDF |
| `b` | take the bottom (oldest) paper: remove it and open its PDF |
| `o` | open the highlighted paper's PDF without removing it |
| `O` | open the highlighted paper's web page instead of the PDF |
| `d` | delete the highlighted paper (it goes to history without a review) |
| `e` / `c` | rename the highlighted paper / set its conference (also in history) |
| `a` / `Esc` | focus the input / the list |
| `h` | history of read and deleted papers: `i` shows the review, `r`/`Enter` re-adds one to the top of the stack (its old review is cleared), `o` opens it, `x` forgets it, `Esc` goes back |
| `t` | cycle the palettes (remembered in `~/.config/paperqueue/ui.toml`) |
| `q` | quit |

After `p` or `b` the PDF opens and a review panel replaces the list: write a comment, set the usefulness
with `←`/`→`, then `Ctrl+S` (or the button) saves it and moves the paper to history. `Esc` cancels:
the paper stays where it was on the stack, there is no way to skip the review.

Besides the three De Stijl palettes, `t` reaches `dinn`: strictly two colours, ink on bone, with
every tone between them dithered through a 4x4 Bayer matrix the way *Return of the Obra Dinn* does
it. Under `dinn` a half-block band sits above the stack, one dithered step per paper and solid ink
at the top, so the texture is the depth of the pile.

Reading opens the PDF when one can be found: arXiv links, pages with a PDF link or `citation_pdf_url`,
Scholar Inbox links (resolved to arXiv by title) and OpenReview forum links. Titles and PDFs are fetched in the background.
OpenReview blocks scripted page fetches, so its papers get a working PDF link but keep the URL as their title:
press `e` to give them one.

Without a server configured, the stack lives in `~/.local/share/paperqueue/stack.json`.

## Synced server (Cloudflare Worker)

`web/` is a Cloudflare Worker with a Durable Object that holds the shared stack, serves the web page,
exposes a small JSON API and resolves titles/PDFs itself. One secret token protects it.
It uses a SQLite-backed Durable Object, which the Workers free plan includes; one person's queue stays far
below the free limits.

### Deploy

1. Install the dependencies (`cd web && npm install`), then authenticate wrangler with the Cloudflare account
   you want to deploy to, either way:
   - **Simplest**: `npx wrangler login` (browser sign-in).
   - **Scoped token** (useful if wrangler is already logged into another account you don't want to touch):
     in that account's dashboard, *My Profile → API Tokens → Create Token → "Edit Cloudflare Workers"* template.
     Copy `web/.env.example` to `web/.env` and fill in the token and the account ID (shown on the account's
     Workers overview page). `.env` is git-ignored. Export it before every wrangler command: `set -a; . ./.env; set +a`.

   Either way, `npx wrangler whoami` shows which account the next command will deploy to.
2. Generate the access token that clients will use, e.g. `openssl rand -hex 32`, and keep it somewhere safe.
3. Deploy:

   ```sh
   npx wrangler secret put TOKEN     # paste the access token from step 2
   npx wrangler deploy               # prints the https://paperqueue.<subdomain>.workers.dev URL
   ```

   The Worker is called `paperqueue`; change `name` in `web/wrangler.jsonc` to deploy it under another name.
   Logs are enabled (`observability` in `wrangler.jsonc`) and show up under the Worker's *Observability* tab.

The access token is the only thing protecting your queue: use a long random one, and never commit
`web/.env` or `web/.dev.vars` (both are git-ignored). To change it, run `npx wrangler secret put TOKEN`
again (it applies immediately, no redeploy), then update `token` in each terminal's `config.toml`; the
web page asks for the new token on its next request, or double-click the header to enter it.

### Use it

- **Phone / browser**: open the Worker URL, paste the access token once, then "Add to Home Screen".
  On Android the installed app also appears in the share sheet, so links can be shared straight to the stack.
  Same key bindings as the TUI (`p`/`b`/`o`/`O`/`d`/`a`/`h`, `↑↓` or `j`/`k` to move, `Esc` to leave the input,
  `i`/`r`/`x` in history, `c`/`e` to set the conference or rename). The review dialog has a Cancel button, and `Esc` works too:
  the paper stays on the stack. On a phone, tap a paper to put the cursor on it and tap a key in the footer bar to act on
  it; tapping it again opens the PDF.

  The stack can be drawn four ways — `v` cycles them, and the footer key doubles as the readout:

  | layout | what it is |
  |---|---|
  | `mondrian` | a De Stijl composition: one rectangle per paper, area by position in the stack, a minority of blocks in red/blue/yellow. Deterministic — the same stack always paints the same picture, and a paper keeps its colour for life. Pages when there are more papers than the canvas can hold legibly. |
  | `clues` | a crossword clue list: boxed numerals, titles as clues, the cursor as the current entry |
  | `picross` | nonogram rows on graph paper; the margin numbers are the real run-lengths of each row's cells, which encode age, PDF, arXiv id |
  | `table` | the plain table: `#`, title, PDF, added |

  `t` cycles the palette: `destijl` (white, black rules, primaries), `graph` (ruled paper),
  `dinn` (1-bit ink on bone, every tone a Bayer tile: mondrian blocks take five dither densities
  instead of pigments, picross cells stipple, and the token dialog veils the page with a hatch),
  `dark`, `system`. Both choices are remembered per device. Quick-add from any desktop browser with a bookmarklet:

  ```
  javascript:location.href='https://paperqueue.<subdomain>.workers.dev/?url='+encodeURIComponent(location.href)
  ```
- **Terminal**: create `~/.config/paperqueue/config.toml`:

  ```toml
  url = "https://paperqueue.<subdomain>.workers.dev"
  token = "<access token>"
  ```

  (or set `PAPERQUEUE_URL` and `PAPERQUEUE_TOKEN`). The TUI then reads and writes only through the server
  and shows "synced" in the header; it will not start while the server is unreachable.
  Run `paperqueue import` once to push an existing local `stack.json`. The merge never overwrites:
  a paper already on the server, in its stack or its history, keeps the server's copy and is reported as skipped.

### Local development

```sh
cd web && npm install
cp .dev.vars.example .dev.vars    # sets the local access token to "dev-token"
npx wrangler dev                  # http://localhost:8787, data kept in web/.wrangler/; keeps running
```

Then, in another terminal:

```sh
cd web && npx vitest run          # Durable Object, API and link-resolution tests
PAPERQUEUE_URL=http://localhost:8787 PAPERQUEUE_TOKEN=dev-token uv run paperqueue
uv run pytest                     # terminal app tests
```

### API

All routes need `Authorization: Bearer <token>` (401 otherwise) and answer JSON. Mutations return `{paper, state}`.
Papers are identified by their normalized URL, the `url` field in `/api/state` (e.g. `https://arxiv.org/abs/1706.03762`
for a bare `1706.03762`); the `?url=` and `{url}` parameters below take that form.

| method & path | action |
|---|---|
| `GET /api/state` | `{version, papers, history}` (papers bottom→top, history oldest→newest) |
| `POST /api/papers` `{url}` | normalize and push (409 if already queued) |
| `POST /api/pop` / `POST /api/take` `{url?, comment, usefulness}` | complete a reading and move the top / bottom paper into history; `comment` is required and `usefulness` is a 0–5 number (400 otherwise). `url`, if given, must be the paper currently at that end, so a stale client cannot review the wrong paper (404 with `paper: null` when it is not, or when the stack is empty) |
| `DELETE /api/papers?url=` | delete a paper into history |
| `POST /api/history/restore` `{url}` / `DELETE /api/history?url=` | re-add / forget |
| `POST /api/import` `{papers, history}` | merge a local `stack.json`; URLs the server already has are skipped. Returns `{added_papers, added_history, state}` |

## License

MIT, see [LICENSE](LICENSE).
