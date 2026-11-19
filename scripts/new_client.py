#!/usr/bin/env python3
"""Scaffold a new client folder.

  python3 new_client.py <client-slug> [--clients-dir DIR]

Creates <clients-dir>/<slug>/ with client.yaml (from the template), a .env (permissions 600, empty keys),
a .gitignore that excludes .env, and batch-1/. Refuses to overwrite an existing client.
"""
import argparse, os, re, shutil, stat, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "client-template")
DEFAULT = os.path.join(HERE, "..", "clients")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--clients-dir", default=DEFAULT)
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", a.slug):
        sys.exit("slug must be lowercase letters, digits and dashes, e.g. acme-health")
    d = os.path.join(a.clients_dir, a.slug)
    if os.path.exists(d):
        sys.exit(f"{d} already exists, not overwriting")
    os.makedirs(os.path.join(d, "batch-1"))
    shutil.copy(os.path.join(TEMPLATE, "client.yaml"), os.path.join(d, "client.yaml"))
    env = os.path.join(d, ".env")
    shutil.copy(os.path.join(TEMPLATE, ".env.example"), env)
    os.chmod(env, stat.S_IRUSR | stat.S_IWUSR)
    open(os.path.join(d, ".gitignore"), "w").write(".env\n")
    print(f"created {d}")
    print("next: add the API keys to .env yourself (do not paste them into chat), then tell Claude 'run the flow for " + a.slug + "'")


if __name__ == "__main__":
    main()
