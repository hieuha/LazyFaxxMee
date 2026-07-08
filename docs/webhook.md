# Webhook integration

> 🌐 Language: **English** · [Tiếng Việt](vi/webhook.md)

The **inbound webhook** lets any external site or service fax **you** — for example straight
from a blog's comment box (e.g. [lazyblog](https://github.com/hieuha/lazyblog)) — without the
end sender needing a FaxxMe account. The calling site authenticates on the sender's behalf with
**your secret key**, and the message prints on your printer like any other fax.

It is a plain HTTP webhook: anyone holding your secret can `POST` a message that prints for you.
So the secret is the whole security boundary — keep it server-side, and rotate/revoke it freely.

```
 visitor            your site (server-side)             FAXXME                 your printer
┌──────────┐  POST  ┌───────────────────────┐  POST    ┌────────────────┐  push ┌──────────┐
│ comment  │ ─────▶ │ blog / app / plugin    │ ───────▶ │ /api/fax/inbound│ ────▶ │  🖨      │
│ box      │ (same  │ holds FAXXME_SECRET_KEY │ Bearer   │ auth by secret  │       └──────────┘
└──────────┘ origin)│ + its own spam checks   │ fxwh_…   │ → your inbox    │
                    └───────────────────────┘          └────────────────┘
```

The call is made **server-side**, not from the visitor's browser. That keeps the secret hidden,
avoids CORS entirely, and lets your site add its own per-visitor checks (captcha, its own rate
limit) before forwarding.

---

## Quickstart

1. **Generate a secret.** Log in to FaxxMe → the **`:: WEBHOOK INTEGRATION`** panel →
   **GENERATE SECRET KEY**. The key (`fxwh_…`) appears masked; click the **eye** to reveal it and
   **copy**. It stays viewable in the panel, so you can come back for it anytime.
2. **Store it server-side** on the calling site (e.g. in its `.env` as `FAXXME_SECRET_KEY`).
   Never ship it to a browser or commit it to git.
3. **POST a message** to `/api/fax/inbound` with `Authorization: Bearer <secret>`. It prints.

```bash
curl -X POST https://fax.hatrunghieu.com/api/fax/inbound \
  -H "Authorization: Bearer fxwh_XXXXXXXX" \
  --data-urlencode "body=Loved this post!" \
  --data-urlencode "name=A reader" \
  --data-urlencode "post=My First Fax" \
  --data-urlencode "url=https://blog.example/first"
```

---

## Endpoint reference

### `POST /api/fax/inbound`

- **Auth:** `Authorization: Bearer <secret key>` header (the `fxwh_…` value). **Not** a session
  cookie — the end sender has no account.
- **Body:** `application/x-www-form-urlencoded` (standard HTML form encoding).

| field | required | max | notes |
|-------|:--------:|:---:|-------|
| `body` | ✅ | `FAXXME_WEBHOOK_MSG_MAX` (500) | the message that prints |
| `name` | – | 40 | sender's name — printed as attribution (`— name`) |
| `post` | – | 120 | source title, e.g. a blog post title |
| `url` | – | 200 | source URL |

> There is **no IP field** — FaxxMe derives the client IP itself (honoring the CF/reverse-proxy
> headers) for per-IP rate limiting, so it can't be spoofed via the request body. That IP is your
> **calling site's** server, not the end visitor, so add your own per-visitor throttle (see
> [Security](#security--protection)).

### Response

`200 OK`

```json
{ "ok": true, "fax_id": 123, "delivered": true }
```

`delivered` is `true` if a printer (browser tab, Pi agent, or the host bridge) took it
immediately, or `false` if it was **queued** and will print when your printer next connects —
either way the fax is accepted and stored.

### Errors

| status | meaning |
|:------:|---------|
| `401` | `missing webhook secret` (no/!Bearer header) or `invalid webhook secret` (unknown/revoked) |
| `400` | `empty message` or `message too long (max 500)` |
| `429` | rate-limited — too many inbound faxes for this author or from this calling-site IP |
| `404` | the route doesn't exist → **the server is running old code, restart it** (see [Troubleshooting](#troubleshooting)) |

---

## Sample requests

**PHP** (e.g. a blog plugin — the secret stays on the server):

```php
<?php
$faxxme = 'https://fax.hatrunghieu.com';
$secret = getenv('FAXXME_SECRET_KEY');   // fxwh_… , kept out of version control

$ch = curl_init("$faxxme/api/fax/inbound");
curl_setopt_array($ch, [
    CURLOPT_POST           => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER     => ["Authorization: Bearer $secret"],
    CURLOPT_POSTFIELDS     => http_build_query([
        'body'      => $_POST['message'] ?? '',
        'name'      => $_POST['name'] ?? '',
        'post'      => $postTitle,
        'url'       => $postUrl,
    ]),
    CURLOPT_TIMEOUT        => 10,
]);
$res  = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);   // 200 ok · 429 too fast · 401 bad secret
curl_close($ch);
```

**Python** (`requests`):

```python
import os, requests

r = requests.post(
    "https://fax.hatrunghieu.com/api/fax/inbound",
    headers={"Authorization": f"Bearer {os.environ['FAXXME_SECRET_KEY']}"},
    data={
        "body": message,
        "name": reader_name,
        "post": post_title,
        "url": post_url,
    },
    timeout=10,
)
r.raise_for_status()
```

**Node.js** (server-side; never expose the secret in front-end JS):

```js
const body = new URLSearchParams({
  body: message, name, post: postTitle, url: postUrl,
});
const r = await fetch("https://fax.hatrunghieu.com/api/fax/inbound", {
  method: "POST",
  headers: { Authorization: `Bearer ${process.env.FAXXME_SECRET_KEY}` },
  body,
});
// r.status: 200 ok · 401 bad secret · 429 too fast
```

---

## Security & protection

- **Call server-side only.** The secret is a bearer credential — anyone with it can print to you.
  Keep it on your server (env var / secrets manager), never in browser JS, a mobile app, or git.
  Because the call is server-to-server there is **no CORS** to configure.
- **Always use HTTPS.** A bearer token over plain HTTP can be sniffed. The production endpoint is
  HTTPS (Cloudflare + Caddy); don't downgrade it.
- **Scope is narrow by design.** A secret can *only* deliver a fax to the author who owns it.
  There is **no recipient field** — a leaked secret can spam *your* paper roll, nothing else.
- **Two-layer rate limiting.** FaxxMe limits inbound faxes **per author** and **per calling-site
  IP**, which it derives server-side (honoring CF/reverse-proxy headers) — it is *not* taken from
  the request, so a caller can't spoof it. FaxxMe only sees your server, not the end visitor, so
  your site should *also* rate-limit / captcha each visitor before forwarding.
- **Fire-and-forget printing.** Accepted messages print immediately, attributed to the reserved
  `@webhook` sender. There is no human moderation step, so the abuse controls above matter. If you
  are being spammed, **revoke the secret** — it stops instantly.
- **Validate before you forward.** Trim/limit length, drop obvious spam, and strip control
  characters on your side. FaxxMe caps `body` at `FAXXME_WEBHOOK_MSG_MAX` and renders Unicode
  (Vietnamese, emoji) as a font raster, but it does not filter content.

### Tuning the limits (server operator)

| env var | default | meaning |
|---------|:-------:|---------|
| `FAXXME_WEBHOOK_RATE_MAX` | `5` | max inbound faxes per window, per author **and** per sender IP (0 = off) |
| `FAXXME_WEBHOOK_RATE_WINDOW` | `300` | the window, in seconds |
| `FAXXME_WEBHOOK_MSG_MAX` | `500` | max characters in an inbound message |

Set these in the FaxxMe service's environment (see [How it works](how-it-works.md) and the main
[README](../README.md) config table), then restart the service.

---

## Managing your secret key

All from the **`:: WEBHOOK INTEGRATION`** panel once logged in:

| action | endpoint | effect |
|--------|----------|--------|
| **Generate / regenerate** | `POST /api/webhook/regenerate` | issues a new `fxwh_…`. **Regenerating immediately invalidates the previous secret** — any site using it starts getting `401` until you update it. |
| **Reveal** | — | the panel shows the secret masked (`••••`); click the **eye** to reveal it, and **copy** to copy it. It stays viewable, so you don't need to save it elsewhere. |
| **Revoke** | `POST /api/webhook/revoke` | clears the secret entirely; the webhook is off until you generate a new one. |
| **Check** | `GET /api/me` → `webhook_secret` | your current secret (the owner's own value), or `null` if none is set. |

Rotate the secret periodically, and whenever it might have leaked. The secret is stored **in
plaintext** server-side (column `users.webhook_secret`) so the panel can re-display it — the
trade-off for viewability. It only ever lets someone spam-print to you and is instantly revocable,
so the stakes are low; still, protect DB access and backups accordingly.

### Administration (server operator)

- Deleting a user (admin panel *tombstone*) anonymizes the account and drops its secret, so its
  webhook stops working.
- The `webhook` sender is a **reserved system account**: it's created on the first inbound fax,
  can't be logged into, and is hidden from the callsign picker. Nobody can register `webhook`.
- Inbound faxes appear in the recipient's inbox and in the admin fax list like any other fax, with
  sender `@webhook`.

---

## How the printed fax looks

The sender header is always the system account — `FROM: Webhook @webhook` — the message prints at
normal size, and the attribution block (`name`, `post`, `url`) prints in a **smaller font**
underneath (size via `FAXXME_FOOTER_FONT_SIZE`, default 22):

```
        FAXXME
--------------------------------
FROM: Webhook @webhook
TIME: 2026-07-08 16:45:02
--------------------------------
Loved this post!

— A reader
My First Fax
https://blog.example/first
--------------------------------
      .: end of message :.
```

ASCII lines print as native ESC/POS text; any line with Vietnamese/emoji is rendered as a font
raster automatically.

---

## Integration recipe (blog comment box)

Because the call is server-side, you can bolt this onto anything that runs code on a server. A
typical blog-comment flow:

1. Add a small form under each post: a message field, an optional name, and a submit button that
   `POST`s to **your own** server (same origin — no FaxxMe URL in the page).
2. On your server, handle that submit: validate + spam-check, then forward to
   `POST {FAXXME}/api/fax/inbound` with the `Authorization: Bearer` header and the fields above,
   passing the post's title/URL as `post`/`url`.
3. Return a friendly "faxed!" / "slow down" message to the visitor based on the `200`/`429`.

For **lazyblog** specifically (PHP, plugin system), this lives naturally as a plugin that renders
the form and handles the forward server-side, with `FAXXME_SECRET_KEY` in the blog's `.env`. The
PHP snippet above is the core of it.

---

## Troubleshooting

| symptom | cause & fix |
|---------|-------------|
| **`404 Not Found`** on `/api/webhook/*` or `/api/fax/inbound` | the running server predates the feature. **Restart it** so the new routes load: `sudo systemctl restart faxxme` (or your deploy's restart). A live route returns `401`, not `404`. |
| **`401 invalid webhook secret`** | wrong/old/revoked secret. Regenerate in the panel and update the caller. |
| **`401 missing webhook secret`** | the `Authorization: Bearer …` header didn't arrive (proxy stripping it? wrong header name?). |
| **`429`** | rate limit hit — you're above `FAXXME_WEBHOOK_RATE_MAX` per window for this author or sender IP. Back off, or raise the limit server-side. |
| **UI still says `BLOG INTEGRATION` / old labels** | hard-refresh the page (`Ctrl+Shift+R`) to pick up the new `app.js`/`index.html`. |
| Fax accepted (`200`) but nothing prints | `delivered:false` means it queued — it prints when your printer/agent/bridge next connects. Check the printer status pill. |

See also: [How it works](how-it-works.md) · [Printer compatibility](printers.md) ·
main [README](../README.md).
