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
python manage.py migrate
python manage.py seed_data
python manage.py createsuperuser   # or set username/password to daraz/daraz
python manage.py runserver
```

Then open http://127.0.0.1:8000/

## Features
- Daraz-style navbar with search bar and auto-changing image slider
- Flash sale, Categories, and Just for you sections with a floating sidebar (scroll shortcuts)
- Product detail page with Buy Now / Add to cart and a review form
- Admin panel at `/admin/` to manage products and reviews (default login: daraz / daraz)
- Signup / Login / Logout with Django's built-in auth system (working out of the box)
- "Continue with Google" button on login/signup — to activate it, set the `GOOGLE_CLIENT_ID`
  environment variable to a real OAuth Client ID from https://console.cloud.google.com/apis/credentials
  (create an OAuth 2.0 Web Client, add your site's URL under "Authorized JavaScript origins").
  Until it's set, the Google button is shown but disabled.
