# Copy-Daraz

Ek simple Daraz jaisi website ka starter project — Django (Python) ke sath banaya gaya hai.

## Structure
- **backend/** – Django project settings, urls, wsgi
- **daraz/** – Main app (backend view + frontend template)
- **db.sqlite3** – Default database (SQLite), migrate hone par create hoti hai

## Routes
- `/` – Frontend home page (Hello World HTML template)
- `/api/hello/` – Backend API endpoint (Hello World text response)
- `/admin/` – Django admin panel

## Run locally
```bash
pip install -r requirements.txt
cp .env.example .env      # then edit .env and add your GOOGLE_CLIENT_ID
python manage.py migrate
python manage.py seed_data
python manage.py createsuperuser   # or set username/password to daraz/daraz123456
python manage.py runserver
```

Then open http://127.0.0.1:8000/

## Updating your Codespace after I push changes
Every time I push new code, run this **one command** in your Codespaces terminal instead of doing
`git pull`, `migrate`, `runserver` separately:
```bash
bash update.sh
```
Note: there's no way to make code changes appear in your browser instantly without pulling — I can
only edit files in my own sandbox and push to GitHub. Codespaces is a separate machine, so it always
needs to fetch the new commits. `update.sh` just makes that a single step instead of four.

## Troubleshooting

**"CSRF verification failed... Origin checking failed"**
This happened because Codespaces serves your site over HTTPS through a dynamic proxy domain, and
Django didn't trust it yet. This is now fixed in `backend/settings.py` (`CSRF_TRUSTED_ORIGINS` and
`SECURE_PROXY_SSL_HEADER`) — just pull the latest code and it will be gone.

**Google Sign-In: "Access blocked: Authorisation error" / "no registered origin" / "invalid_client"**
This is not a code problem — it means your Codespaces URL hasn't been registered with your Google
OAuth Client yet. Fix it in Google Cloud Console:
1. Go to https://console.cloud.google.com/apis/credentials
2. Open your OAuth 2.0 Client (the one whose Client ID is in your `.env`)
3. Under **Authorized JavaScript origins**, add your exact Codespaces URL, e.g.
   `https://humble-happiness-qv99wgprg4g9h9x9v-8000.app.github.dev` (no trailing slash)
4. Under **Authorized redirect URIs**, add the same URL + `/auth/google/`, e.g.
   `https://humble-happiness-qv99wgprg4g9h9x9v-8000.app.github.dev/auth/google/`
5. Save. Changes can take a minute or two to apply.

Since Codespaces URLs can change if you rebuild the container, you'll need to re-add the new URL
each time it changes.
## Features
- Daraz-style navbar with search bar, cart icon with live item count
- Big auto-changing image slider + "Try the app" QR box — shown only on the home page
- Flash sale, Categories (21 categories, 5+ products each), and Just for you sections
- Product detail page with working Buy Now / Add to cart and a review form
- Full shopping cart: add, increase/decrease quantity, remove, live totals
- Checkout with delivery details, creates a real Order in the database
- "My orders" page to view order history
- Admin panel at `/admin/` to manage products, reviews and orders (default login: daraz / daraz123456)
- Signup / Login / Logout with Django's built-in auth system
- "Continue with Google" — works once `GOOGLE_CLIENT_ID` is set in `.env`. Important: in Google Cloud
  Console (APIs & Services → Credentials → your OAuth Client), add your site's exact URL (e.g. your
  Codespaces preview URL) under **both** "Authorized JavaScript origins" and "Authorized redirect URIs",
  otherwise Google will block the sign-in with an origin mismatch error. The `.env` file is not committed
  to the repo (it's in `.gitignore`) — never commit real credentials.
