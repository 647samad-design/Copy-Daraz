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
