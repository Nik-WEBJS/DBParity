"""Локальная веб-консоль: браузер вместо терминала."""
from .server import ConsoleServer, create_server

__all__ = ["ConsoleServer", "create_server"]
