"""Read-only inventory of tracked working-tree files; emits JSON to stdout.

Run: python3 scripts/audit_repo_hygiene.py > /tmp/repo-hygiene.json
Findings are review candidates, never instructions to delete. Empty files are
excluded from duplicate groups because package markers are often intentional.
Untracked/ignored file contents and symlink targets are not read. Git history
and dependencies are not scanned; this is not a secret or unused-code scanner.
"""

import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def paths(root, *args):
    return sorted(
        set(
            git(root, *args).decode("utf-8", "surrogateescape").rstrip("\0").split("\0")
        )
        - {""}
    )


def audit(root):
    tracked = paths(root, "ls-files", "-z")
    sizes = defaultdict(list)
    skipped = []
    backups = []
    for name in tracked:
        path = root / name
        if path.is_symlink() or not path.is_file():
            skipped.append(name)
            continue
        size = path.stat().st_size
        sizes[size].append(name)
        if path.suffix in {".bak", ".patch"} or any(
            part.startswith("restore_") for part in path.parts
        ):
            backups.append(name)

    duplicates = []
    for size, names in sizes.items():
        if not size or len(names) < 2:
            continue
        hashes = defaultdict(list)
        for name in names:
            with (root / name).open("rb") as stream:
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            hashes[digest.hexdigest()].append(name)
        for names_with_hash in hashes.values():
            if len(names_with_hash) > 1:
                duplicates.append({"bytes_each": size, "paths": names_with_hash})

    return {
        "scope": "Tracked working-tree files; candidates require reference review",
        "head": git(root, "rev-parse", "HEAD").decode().strip(),
        "tracked_file_count": len(tracked),
        "tracked_bytes": sum(size * len(names) for size, names in sizes.items()),
        "status_porcelain": git(root, "status", "--porcelain=v1").decode().splitlines(),
        "tracked_matching_ignore_rules": paths(
            root, "ls-files", "-ci", "--exclude-standard", "-z"
        ),
        "backup_candidates": backups,
        "duplicate_groups": sorted(
            duplicates, key=lambda group: (-group["bytes_each"], group["paths"])
        ),
        "largest_files": [
            {"path": name, "bytes": size}
            for size, name in sorted(
                ((size, name) for size, names in sizes.items() for name in names),
                key=lambda item: (-item[0], item[1]),
            )[:20]
        ],
        "skipped_missing_symlink_or_nonfile": skipped,
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(audit(root), indent=2, ensure_ascii=True))
