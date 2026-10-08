# White-label multi-vendor marketplace (Django)

A complete, production-ready marketplace you can rebrand in minutes: customers shop and pay by card,
independent sellers and businesses run their own stores, and the owner earns commission on every sale.

Out of the box it ships as **"Lumen Market"**. Change the name, logo, colours, homepage copy, shipping,
tax and contact details from **Admin → Site settings** — no code changes.

## Highlights

**For customers**
- Fast, accessible storefront (WCAG-minded contrast, 16px+ text, keyboard focus, dark mode, mobile-first)
- Search with live suggestions, category navigation, filters (price, rating, seller, stock) and sorting
- Product pages with gallery, verified-purchase reviews, questions for the seller, related products
- Cart with live updates, saved addresses, coupons, shipping and tax calculation
- **Stripe Checkout** — cards, Apple Pay, Google Pay, Link (whatever you enable in Stripe)
- Order tracking, PDF invoices, one-click cancel with **automatic refund**, return requests, buy again
- Wishlist, product comparison, notifications, referral rewards, support chat

**For sellers**
- Individual and business accounts, with team members for businesses
- Dashboard: sales chart, earnings after commission, low-stock alerts, order fulfilment
- Product listing with image upload; edits to listing details go back to moderation automatically

**For the store owner**
- Store dashboard: revenue, commission, money owed to sellers, approvals and stock alerts
- Admin for products, orders, sellers, coupons, returns, chat, newsletter (with bulk email) and audit log
- Tiered commission, payout tracking, CSV exports
- White-label branding, announcement bar, cash-on-delivery toggle

**Engineering**
- Django 6, Postgres (**Supabase**) or SQLite, S3-compatible file storage (**Supabase Storage**)
- Atomic checkout with row locking (no overselling), idempotent signed Stripe webhooks with amount checks
- Rate limiting, upload validation, private storage for seller ID documents, safe production defaults
- 85+ automated tests: `python manage.py test bees`

## Seller Center (/seller/)

Every individual seller and organization gets their own back office:

- **Overview** – sales, units sold, commission, earnings and average order for 7 / 30 / 90 days or 12 months (with change vs. the previous period), a sales chart, best sellers, sales by country, store health and a getting-started checklist.
- **Orders** – to pack / packed / in transit / delivered tabs, search, bulk "mark packed / handed to courier", CSV export, order page with ship-to address, courier + tracking number and a printable packing slip. Sellers get a notification and email the moment an order is ready to ship, and an alert if it is cancelled.
- **Products** – live / in review / rejected / low / out of stock, quick price and stock edits, copy a listing, units sold and revenue per product.
- **Earnings & payouts** – a ledger of every sale with the commission taken, a downloadable statement (CSV), balance (available / on the way / paid), commission tier progress and payout requests. Staff record payouts (method + reference) under Admin › Sellers; the seller is emailed.
- **Returns**, **Reviews & questions**, **Store settings** (incl. holiday mode, which hides the store's products) and **Team** for organizations (staff members can't see money pages).

`MIN_PAYOUT` in settings (default 10) is the smallest payout a seller can request. Payouts can only be requested from earnings on delivered orders.

## Seller plans (/manage/plans/)

Sellers can buy a monthly plan (Starter free, Pro $19, Business $49 by default) for a lower
commission, more products and a "Pro seller" badge. They pay by card on Stripe's page from
Seller Center > Plan; nothing renews automatically and they get a reminder 3 days before it ends.
Staff can also give a plan by hand on a seller's page (bank transfer, free trial). Edit prices,
limits and perks at /manage/plans/. Default commission for new sellers is in Store settings > Selling.

## Setup wizard (/manage/setup/)

A brand-new store opens a five-step wizard the first time the owner visits the admin: store details,
logo and colours (with live preview), commission, payments & delivery, and a launch checklist.
Run it again any time from Store settings.

## AI listing writer and smart search

The product forms have a "Write it for me" button that drafts a description from the product name
and a few key details. Add `ANTHROPIC_API_KEY=...` to `.env` to use Claude (optional `AI_MODEL`);
without a key a built-in writer is used. Search understands phrases like `hoodie under 40`,
`lamp between 20 and 50` or `cheap earbuds`, matches words in any order and plurals, and shows
close matches when nothing matches every word.

## Demo copy for clients

On a separate copy of the store (never your live store):

    python manage.py demo_setup        # demo shopper, seller and admin + sample orders
    # .env: DEMO_MODE=True, then reload

The sign-in page then shows one-click "Shop as a customer", "Open the Seller Center" and
"Run the marketplace" buttons. Demo accounts can't change store settings, passwords or security,
and `daily_tasks` resets them every day.

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # DEBUG=True is already set for local use
python manage.py migrate
python manage.py seed_data      # demo products and coupons (optional)
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000 — the admin is at `/admin/`, the store dashboard at `/owner/dashboard/`.

## Rebrand the store

Admin → **Site settings**:

| Section | What you set |
|---|---|
| Brand | Store name, tagline, logo (URL or upload), favicon, primary + accent colour |
| Homepage hero | Headline, subheading, optional hero image |
| Contact & social | Support email/phone, company address, social links |
| Checkout | Tax %, flat shipping fee, free-shipping threshold, cash on delivery on/off |
| Announcement bar | Site-wide message and link |

Use a **dark** primary colour (white text sits on it) and a **bright** accent colour (dark text sits on it).
Prices use the `STORE_CURRENCY` environment variable (USD by default).

## Connect Supabase

1. Create a project at https://supabase.com.
2. **Database**: Project Settings → Database → *Connection string* → URI. Put it in `DATABASE_URL`.
   Use the session pooler (port 5432) on a normal server, or the transaction pooler (port 6543) on
   serverless hosts — the settings adapt automatically.
3. **Storage**: Storage → New bucket:
   - `media` — **public** (product images, logos, store banners)
   - `private` — **private** (seller ID documents and certificates)
4. Storage → Settings → **S3 Connection**: enable it, create an access key, and fill in
   `SUPABASE_PROJECT_REF`, `SUPABASE_S3_REGION`, `SUPABASE_S3_ACCESS_KEY_ID`, `SUPABASE_S3_SECRET_ACCESS_KEY`.
5. Run `python manage.py migrate` against the new database and create a superuser.

## Connect Stripe

1. Get API keys at https://dashboard.stripe.com/test/apikeys and set `STRIPE_SECRET_KEY` /
   `STRIPE_PUBLISHABLE_KEY`. "Card, Apple Pay or Google Pay" appears at checkout automatically.
2. Developers → Webhooks → **Add endpoint** `https://YOUR-DOMAIN/payment/stripe/webhook/` with events
   `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
   `checkout.session.async_payment_failed`, `checkout.session.expired`. Copy the signing secret into
   `STRIPE_WEBHOOK_SECRET`.
3. Local testing with the Stripe CLI:
   ```bash
   stripe listen --forward-to localhost:8000/payment/stripe/webhook/
   ```
   Pay with the test card `4242 4242 4242 4242`, any future date, any CVC.
4. Switch to live keys when you're ready to take real payments.

How payments work: placing a card order reserves stock and opens Stripe's hosted page. The webhook
confirms the exact amount and marks the order paid. Abandoned payments expire after 30 minutes and the
stock is returned. As a safety net, schedule `python manage.py release_unpaid_orders` every 15–30 minutes.

## Deploy

Any host that runs Python works (Render, Railway, Fly.io, Heroku, a VPS). Minimum production settings:

```
DEBUG=False
SECRET_KEY=<long random string>
ALLOWED_HOSTS=shop.example.com
CSRF_TRUSTED_ORIGINS=https://shop.example.com
NUM_PROXIES=1
DATABASE_URL=...            # Supabase
SUPABASE_...                # storage keys
STRIPE_...                  # payment keys
EMAIL_HOST=...              # transactional email
```

Build / start commands:

```bash
pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate
gunicorn backend.wsgi --workers 3
```

With more than one worker, also set `REDIS_URL` so rate limits are shared. `python manage.py check --deploy`
should report no issues.

## Using Netlify as the front door

Netlify can't run Python, but it can sit in front of the Django host. `netlify.toml` and
`netlify/edge-functions/proxy.ts` forward every request to the backend so visitors only see the
Netlify domain.

1. Host Django on a Python platform (PythonAnywhere free tier works).
2. In Netlify, set the environment variable `BACKEND_URL=https://YOUR-USERNAME.pythonanywhere.com` and deploy.
3. On the backend, add the Netlify domain:
   ```
   ALLOWED_HOSTS=YOUR-SITE.netlify.app,YOUR-USERNAME.pythonanywhere.com
   CSRF_TRUSTED_ORIGINS=https://YOUR-SITE.netlify.app
   USE_X_FORWARDED_HOST=True
   NUM_PROXIES=2
   ```

## Project layout

```
backend/            settings, URLs, WSGI
bees/               the marketplace app
  views.py          storefront, checkout, seller and staff views
  payments.py       Stripe Checkout + webhook handling
  security.py       redirect safety, client IP, upload validation
  models.py         products, orders, sellers, site settings ...
  templates/bees/   storefront templates (design tokens live in base.html)
  management/       seed_data, release_unpaid_orders
```

## Tests

```bash
python manage.py test bees
```

Covers order totals, shipping/tax, coupons, stock locking, Stripe flows (mocked), refunds,
permissions, rate limiting, open-redirect protection, upload validation and white-label rendering.

## Daily maintenance (PythonAnywhere scheduled task)

One command does everything the store needs once a day — cancel unpaid
card orders, send cart reminders, back up the database and images, and
clear old sessions:

```
cd ~/Lumen-Market && venv/bin/python manage.py daily_tasks
```

On PythonAnywhere: **Tasks** tab → *Scheduled tasks* → paste the line above,
pick a time (e.g. 03:00) → **Create**.

Backups go to the Supabase `private` bucket (folder `backups/`) when
Supabase Storage is configured, otherwise to `backups/` next to
`manage.py`. The newest 14 are kept.

```
python manage.py restore_backup --list     # see backups
python manage.py restore_backup            # restore the newest (then Reload the web app)
```

## Two-step sign-in

Staff must set up an authenticator app before opening the store admin
(Store settings → Security). If someone loses their phone *and* backup
codes:

```
python manage.py reset_two_factor <username or email>
```
