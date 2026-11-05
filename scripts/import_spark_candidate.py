#!/usr/bin/env python3
"""Import a reviewed Spark source recovery candidate into a clean Git feature branch.

Never touches production containers, databases, volumes or Docker Hub.
The candidate SHA-256 must be supplied independently by the operator.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile

ALLOWED_TOP = {"api", "tests"}
ALLOWED_FILES = {"README.md", ".env.example"}
BRANCH = "feature/spark-source-recovery-multi-portal"


class ImportErrorSafe(ValueError):
    pass


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def validate(source: Path, expected_sha256: str) -> list[tuple[str, bytes]]:
    if (len(expected_sha256) != 64 or
            any(c not in "0123456789abcdef" for c in expected_sha256)):
        raise ImportErrorSafe("Expected SHA-256 must be 64 lowercase hex characters")
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected_sha256:
        raise ImportErrorSafe("ZIP checksum mismatch")
    files: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(source) as archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) > 80:
            raise ImportErrorSafe("Unexpected number of archive entries")
        for info in infos:
            path = PurePosixPath(info.filename)
            if (path.is_absolute() or ".." in path.parts or
                    "\\" in info.filename or not path.parts):
                raise ImportErrorSafe("Unsafe ZIP path")
            if (path.parts[0] not in ALLOWED_TOP and
                    info.filename not in ALLOWED_FILES):
                raise ImportErrorSafe("Unexpected ZIP entry")
            if path.parts[0] == "api" and (len(path.parts) < 2 or
                    path.parts[1] not in ("app", "Dockerfile", "requirements.txt")):
                raise ImportErrorSafe("Unexpected API path")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ImportErrorSafe("Symbolic links are not allowed")
            if info.file_size > 1_000_000:
                raise ImportErrorSafe("Oversized source entry")
            content = archive.read(info)
            if b"-----BEGIN PRIVATE KEY-----" in content:
                raise ImportErrorSafe("Embedded private key detected")
            files.append((info.filename, content))
    if not any(name == "api/app/main.py" for name, _ in files):
        raise ImportErrorSafe("Missing application entrypoint")
    return files


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("zip_file", type=Path)
    p.add_argument("--expected-sha256", required=True)
    p.add_argument("--apply", action="store_true",
                   help="Explicitly copy source into feature branch")
    p.add_argument("--commit-and-push", action="store_true",
                   help="Commit reviewed source and push branch to origin")
    args = p.parse_args(argv)
    if args.commit_and_push and not args.apply:
        p.error("--commit-and-push requires --apply")
    root = Path(__file__).resolve().parents[1]
    try:
        entries = validate(args.zip_file, args.expected_sha256)
        if _git(root, "branch", "--show-current") != BRANCH:
            raise ImportErrorSafe("Refusing to import outside expected feature branch")
        if _git(root, "status", "--porcelain"):
            raise ImportErrorSafe("Working tree must be clean")
        paths = [("SPARK_SOURCE_RECOVERY.md" if path == "README.md"
                  else ".env.spark-example" if path == ".env.example"
                  else path, data) for path, data in entries]
        print(f"Validated {len(paths)} source files; branch={BRANCH}")
        if not args.apply:
            print("DRY RUN: nothing written")
            return 0
        for name, data in paths:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                raise ImportErrorSafe("Refusing symlinked target")
            target.write_bytes(data)
        if args.commit_and_push:
            _git(root, "add", "--", *(n for n, _ in paths))
            _git(root, "commit", "-m",
                 "auth: recover reviewed Spark source and gated multi-portal candidate")
            _git(root, "push", "origin", "HEAD:" + BRANCH)
            print("Source committed and pushed to feature branch; run CI before merge")
        else:
            print("Source imported to working tree. No Git push performed.")
    except (ImportErrorSafe, OSError, ValueError, subprocess.CalledProcessError,
            zipfile.BadZipFile) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
