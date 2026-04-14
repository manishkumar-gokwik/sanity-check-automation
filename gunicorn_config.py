"""Gunicorn production config."""
import os

# Server
bind = "0.0.0.0:5000"
workers = 2
worker_class = "sync"
worker_connections = 1000
timeout = 1800  # 30 min — batch checks take time
keepalive = 5

# Logging
accesslog = "logs/access.log"
errorlog = "logs/error.log"
loglevel = "info"

# Process
preload_app = False  # Each worker loads its own (scheduler per worker)
daemon = False

# Worker lifecycle
max_requests = 1000
max_requests_jitter = 100
graceful_timeout = 60

# Create logs dir
os.makedirs('logs', exist_ok=True)
