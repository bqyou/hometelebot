"""
Shared UI constants and helpers for consistent Telegram message formatting.

Modern minimal style: clean lines, selective emoji, whitespace for breathing room.
"""

import html

# Box-drawing characters for grouping
BOX_TOP = "\u250c"       # ┌
BOX_MID = "\u2502"       # │
BOX_BOT = "\u2514"       # └
BOX_H   = "\u2500"       # ─

# Separators
SEPARATOR = "\u2500" * 20  # ────────────────────
DIVIDER   = ""              # blank line as divider

# Common symbols
DOT = "\u00b7"  # ·


def e(text: str) -> str:
    """HTML-escape a string for Telegram HTML parse mode."""
    return html.escape(str(text))


def section(title: str, items: list[str]) -> list[str]:
    """Format a section with box-drawing characters.

    Returns lines like:
        ┌ Title
        │ item 1
        │ item 2
        └
    """
    lines = [f"{BOX_TOP} <b>{e(title)}</b>"]
    for item in items:
        lines.append(f"{BOX_MID}  {item}")
    lines.append(BOX_BOT)
    return lines


def header(emoji: str, title: str) -> str:
    """Format a message header: emoji + bold title."""
    return f"{emoji} <b>{e(title)}</b>"


def status_line(label: str, value: str) -> str:
    """Format a key-value status line: label · value."""
    return f"{e(label)} {DOT} {e(value)}"
