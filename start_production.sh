#!/bin/bash
# Production startup script
cd "$(dirname "$0")"
source venv/bin/activate
mkdir -p logs
exec gunicorn --config gunicorn_config.py wsgi:app
