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

# Logging setup — stdout/stderr, capturado pelo journald via systemd.
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Daemonize or not (systemd will handle execution, so daemon=False is preferred)
daemon = False

# Socket de controle (gunicornc) com nome próprio. O padrão do gunicorn 26 é
# `$XDG_RUNTIME_DIR/gunicorn.ctl` e, sem essa variável — o caso sob systemd —,
# cai em `~/.gunicorn/gunicorn.ctl`. Cinco serviços deste servidor rodam como
# `rod` e resolviam todos para o MESMO arquivo; socket unix tem um dono só, e
# quem sobe por último fica com ele — o `gunicornc` passaria a falar com o app
# errado sem avisar. Medido numa auditoria em 12/09/2026.
#
# O certo mesmo seria `/run/divisor_pdf/gunicorn.ctl` com `RuntimeDirectory=`
# na unidade, como o dojo faz; a unidade é root e não está neste repositório,
# então fica para quando ela for tocada.
#
# Exige RESTART, não reload: o SIGHUP relê este arquivo, mas o arbiter não
# reinicia o servidor de controle junto.
control_socket = "/home/rod/.gunicorn/divisor_pdf.ctl"
