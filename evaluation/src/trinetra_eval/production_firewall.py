"""IMP-0420: audit the actual production tree from the evaluation side."""

from pathlib import Path

from trinetra_eval.firewall import audit_source


def production_firewall_violations(package: Path) -> tuple[str, ...]:
    """Return opaque relative file locations, never source text or hidden data."""
    if package.is_symlink() or not package.is_dir():
        return ("missing-production-package",)
    failures: list[str] = []
    pending = [package]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            failures.append("unreadable-source-directory")
            continue
        for path in entries:
            if path.is_symlink():
                failures.append("symlink")
            elif path.is_dir():
                pending.append(path)
            elif path.suffix == ".py":
                try:
                    source = path.read_text(encoding="utf-8")
                    violations = audit_source(source, production=True)
                except (OSError, UnicodeDecodeError):
                    violations = (0,)
                if violations:
                    failures.append(path.relative_to(package).as_posix())
    return tuple(failures)
