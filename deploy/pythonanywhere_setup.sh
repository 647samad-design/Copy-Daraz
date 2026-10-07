#!/bin/bash
# One-command setup for a free PythonAnywhere account.
#
# Before running: Web tab > Add a new web app > Manual configuration > Python 3.13.
# Then in a Bash console:
#   git clone https://github.com/647samad-design/Lumen-Market.git ~/Lumen-Market
#   bash ~/Lumen-Market/deploy/pythonanywhere_setup.sh
#
# Optional: NETLIFY_DOMAIN=yoursite.netlify.app bash ~/Lumen-Market/deploy/pythonanywhere_setup.sh
set -e

USER_NAME="$(whoami)"
APP_DIR="$HOME/Lumen-Market"
DOMAIN="${USER_NAME}.pythonanywhere.com"
NETLIFY_DOMAIN="${NETLIFY_DOMAIN:-19bees.netlify.app}"
WSGI_FILE="/var/www/${USER_NAME}_pythonanywhere_com_wsgi.py"
PY=python3.13

step() { echo; echo "==> $1"; }

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: $PY not found. Go to Account > System image, choose 'innit', open a NEW Bash console and run this again."
  exit 1
fi

cd "$APP_DIR"

step "1/6 Getting the latest code"
git pull --ff-only || true

step "2/6 Creating the Python environment (takes a few minutes the first time)"
[ -d venv ] || "$PY" -m venv venv
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

step "3/6 Writing settings (.env)"
if [ ! -f .env ]; then
  SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(50))")
  cat > .env <<ENV
DEBUG=False
SECRET_KEY=${SECRET}
ALLOWED_HOSTS=${NETLIFY_DOMAIN},${DOMAIN}
CSRF_TRUSTED_ORIGINS=https://${NETLIFY_DOMAIN},https://${DOMAIN}
USE_X_FORWARDED_HOST=True
NUM_PROXIES=2
SECURE_SSL_REDIRECT=False
STORE_CURRENCY=usd
ENV
  echo "Created .env"
else
  echo ".env already exists - keeping it"
fi

step "4/6 Setting up the database and static files"
python manage.py migrate --noinput
python manage.py collectstatic --noinput >/dev/null
python manage.py seed_data >/dev/null && echo "Demo products added"

step "5/6 Connecting the website"
if [ -f "$WSGI_FILE" ]; then
  cat > "$WSGI_FILE" <<WSGI
import os, sys
path = '${APP_DIR}'
if path not in sys.path:
    sys.path.insert(0, path)
os.chdir(path)
from dotenv import load_dotenv
load_dotenv(os.path.join(path, '.env'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'backend.settings'
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
WSGI
  touch "$WSGI_FILE"
  echo "WSGI file updated"
else
  echo "WARNING: $WSGI_FILE not found. Create the web app first (Web tab > Add a new web app > Manual configuration > Python 3.13), then run this script again."
fi

step "6/6 Done"
cat <<DONE

Now finish on the Web tab (copy these exactly):
  Virtualenv:      ${APP_DIR}/venv
  Static files:    /static/  ->  ${APP_DIR}/staticfiles
                   /media/   ->  ${APP_DIR}/media
Then press the green Reload button and open https://${DOMAIN}

Create your admin login with:
  cd ~/Lumen-Market && source venv/bin/activate && python manage.py createsuperuser
DONE
