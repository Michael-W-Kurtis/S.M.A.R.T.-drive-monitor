#!/usr/bin/env python3
"""Launcher so Task Scheduler / cron can point at a single file."""

from smart_monitor.main import main

if __name__ == "__main__":
    raise SystemExit(main())
