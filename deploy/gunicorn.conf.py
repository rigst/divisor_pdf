"""
Gunicorn configuration file for Divisor PDF.
"""

import multiprocessing

# Bind to a Unix socket
bind = "unix:/var/www/divisor_pdf/divisor_pdf.sock"

# Garante que o socket seja acessível pelo grupo (www-data / nginx)
umask = 0o007

# Workers count based on CPU cores
workers = multiprocessing.cpu_count() * 2 + 1

# Worker class
worker_class = "sync"

# Maximum request timeout (seconds) — important for large uploads
timeout = 300

# Keep-alive timeout
keepalive = 2

# Process Name
proc_name = "divisor_pdf"

# Logging setup
accesslog = "/var/www/divisor_pdf/media/gunicorn.access.log"
errorlog = "/var/www/divisor_pdf/media/gunicorn.error.log"
loglevel = "info"

# Daemonize or not (systemd will handle execution, so daemon=False is preferred)
daemon = False
