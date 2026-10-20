import os

from .gizmoapp_server import create_app

app = create_app(shell_variant=os.getenv("GIZMOAPP_SHELL"))
