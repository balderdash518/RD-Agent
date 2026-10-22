"""Discover readable trace sessions without deserializing their contents."""

from pathlib import Path


def is_trace_folder(path: Path) -> bool:
    """A session must contain scenario metadata, not just CLI/debug output."""
    return path.is_dir() and any((path / "scenario").glob("**/*.pkl"))


def discover_trace_folders(root: Path) -> list[Path]:
    """Return session paths relative to root, newest first."""
    if is_trace_folder(root):
        return [Path(".")]
    if not root.is_dir():
        return []
    return sorted(
        (path.relative_to(root) for path in root.iterdir() if is_trace_folder(path)),
        key=lambda path: path.name,
        reverse=True,
    )
