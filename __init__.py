"""Clash Of Clans plugin entry point."""

from .tools import register_tools, TOOLSET


def register(ctx) -> None:
    """Called by the Hermes plugin loader."""
    register_tools(ctx)


__all__ = ["register", "register_tools", "TOOLSET"]
