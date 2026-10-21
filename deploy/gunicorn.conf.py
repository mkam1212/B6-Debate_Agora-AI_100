"""Threaded streaming service; one worker by default for the SQLite app."""
import os
from pathlib import Path


def env_int(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f'{name} must be an integer') from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f'{name} must be between {minimum} and {maximum}')
    return value


bind = '127.0.0.1:' + str(env_int('GIZMOAPP_PORT', 8001, 1024, 65535))
workers = env_int("GIZMOAPP_GUNICORN_WORKERS", 1, 1, 16)
worker_class = 'gthread'
threads = env_int('GIZMOAPP_GUNICORN_THREADS', 8, 1, 64)
timeout = env_int('GIZMOAPP_GUNICORN_TIMEOUT', 300, 30, 900)
graceful_timeout = 150
keepalive = 5
preload_app = False
accesslog = '-'
errorlog = '-'
capture_output = True
control_socket_disable = True
worker_tmp_dir = '/dev/shm' if Path('/dev/shm').is_dir() else None
# ProxyFix is enabled separately and only for a trusted reverse proxy.
forwarded_allow_ips = '127.0.0.1,::1'
