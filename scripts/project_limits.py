"""This project's local resource share (36 GB, set by the user's resource budget). Docker data on /mnt/dev-data is
not counted; the checkout and its caches are."""

# Doubled from 18 GB by the user on 2026-10-27.
DISK_SHARE_BYTES = 36 * 10**9
