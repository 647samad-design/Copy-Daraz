#!/bin/bash
# One-command updater: pulls latest code, installs deps, applies migrations, runs server.
# Usage: bash update.sh
set -e
echo "Pulling latest code..."
git pull
echo "Installing dependencies..."
pip install -r requirements.txt --quiet
echo "Applying migrations..."
python manage.py migrate
echo "Starting server..."
python manage.py runserver 0.0.0.0:8000
