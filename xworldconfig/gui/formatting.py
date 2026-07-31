"""Small text-rendering helpers shared across main_window.py and dialogs.py."""

_PROGRESS_BAR_WIDTH = 80


def render_progress_bar(done: int, total: int) -> str:
    if total <= 0:
        return "[" + "░" * _PROGRESS_BAR_WIDTH + "] working..."
    fraction = min(1.0, done / total)
    filled = round(_PROGRESS_BAR_WIDTH * fraction)
    bar = "█" * filled + "░" * (_PROGRESS_BAR_WIDTH - filled)
    return f"[{bar}] {done:,} / {total:,} tiles ({fraction * 100:.0f}%)"
