S.M.A.R.T. drive monitor
========================

One-shot Python wrapper around smartctl. It inspects every (or a configured)
disk, and if something looks like failure or impending failure it POSTs to
ntfy so the iOS ntfy app can push it. Healthy weekdays stay silent. Sundays
send a single "all systems good" heartbeat so you know cron/Task Scheduler
is still firing.

The process checks once and exits. It is not a daemon. Cron (Linux) or Task
Scheduler (Windows) owns the interval. Linux and Windows share the same
Python package; the only OS-specific bits are finding smartctl.exe and
CREATE_NO_WINDOW on Windows.

Requires: Python 3.9+, smartctl from smartmontools on PATH (or set
smartctl.path). No third-party Python packages on Linux. On Windows,
`pip install tzdata` if zoneinfo cannot load America/New_York.


What it alerts on
-----------------

FAIL
  SMART overall status failed
  pre-fail attribute below threshold
  reallocated / pending / offline uncorrectable sectors (raw >= threshold)
  reported uncorrectable, end-to-end errors
  failed self-test log
  NVMe critical_warning, media_errors, available spare too low,
  percentage used at 100%
  temperature at or above temp_fail_c (default 60C)
  SCSI grown defect list

WARN (investigation, not yet a corpse)
  cannot open the device (usually missing Administrator/root)
  SMART error log has entries
  spin retries, UDMA CRC (cable/interface)
  NVMe percentage used >= 90
  temperature at or above temp_warn_c (default 50C)
  a drive listed in config.ini is missing from smartctl --scan

Ignored on purpose (noisy / not predictive)
  Raw_Read_Error_Rate, Seek_Error_Rate (Seagate packing)
  Power_On_Hours, Power_Cycle_Count, Load_Cycle_Count
  Hardware_ECC_Recovered

Thresholds are in config.ini and can be tightened without touching code.


Setup
-----

    git clone git@github.com:Michael-W-Kurtis/S.M.A.R.T.-drive-monitor.git
    cd S.M.A.R.T.-drive-monitor
    cp config.example.ini config.ini
    chmod 600 config.ini

Edit config.ini:

  ntfy.topic     long random string; subscribe to the same topic in the
                 ntfy iOS app (Default server https://ntfy.sh). Anyone who
                 knows the topic can read it on the public server, so do
                 not reuse a guessable word.
  ntfy.token     only if your ntfy server requires auth
  monitor.drives empty = all disks from smartctl --scan; otherwise a comma
                 list of names as smartctl prints them
  monitor.silence_alerts  true to run the check and log, but send nothing
  monitor.timezone        used for the Sunday heartbeat
  monitor.check_interval_hours  documentation for the scheduler; unused
                 by the process itself
  smartctl.path  leave empty unless smartctl is not on PATH

Confirm smartctl sees disks (Linux needs root, Windows needs an elevated
prompt):

    sudo smartctl --scan
    python3 smart_monitor.py --config config.ini --scan
    python3 smart_monitor.py --config config.ini --dry-run

`--dry-run` still talks to smartctl; it just skips the ntfy POST.


Linux cron (once a day)
-----------------------

smartctl needs root to open /dev/sdX and NVMe character devices. Put this
in root's crontab (`sudo crontab -e`), not yours, unless you have udev
rules that grant the disk nodes.

    0 8 * * * TZ=America/New_York /usr/bin/python3 /path/to/S.M.A.R.T.-drive-monitor/smart_monitor.py --config /path/to/S.M.A.R.T.-drive-monitor/config.ini >> /var/log/smart-monitor.log 2>&1

Replace /path/to with the clone path. 08:00 local if TZ is set as above.
The script exits after one pass; do not wrap it in a loop.

Create the log file so cron is not fighting permissions:

    sudo touch /var/log/smart-monitor.log
    sudo chmod 640 /var/log/smart-monitor.log


Windows Task Scheduler (once a day)
-----------------------------------

1. Install smartmontools (adds smartctl.exe). Install Python 3.9+.
2. From an elevated cmd.exe, create a daily task that runs as Administrators
   so PhysicalDrive nodes are readable. Adjust the Python and clone paths:

    schtasks /Create /TN "SMART-drive-monitor" /SC DAILY /ST 08:00 /RL HIGHEST /F /TR "python.exe C:\path\to\S.M.A.R.T.-drive-monitor\smart_monitor.py --config C:\path\to\S.M.A.R.T.-drive-monitor\config.ini"

   If python.exe is not on PATH, use the full path to python.exe.

3. Optional GUI equivalent:
   Task Scheduler -> Create Task (not Basic)
     General: Run whether user is logged on or not; Run with highest privileges
     Triggers: Daily at 08:00
     Actions: Start a program
       Program: C:\Python314\python.exe
       Arguments: C:\path\to\S.M.A.R.T.-drive-monitor\smart_monitor.py --config C:\path\to\S.M.A.R.T.-drive-monitor\config.ini
       Start in: C:\path\to\S.M.A.R.T.-drive-monitor

If zoneinfo raises "No time zone found", run:

    python.exe -m pip install tzdata


ntfy iOS
--------

Install ntfy, add a subscription for the topic in config.ini, same server.
Priority 5 (FAIL) uses the urgent ntfy channel; warnings are priority 4;
the Sunday heartbeat is priority 2.


Exit codes
----------

  0  healthy (or silenced with no FAIL)
  1  warnings only
  2  at least one FAIL
  3  config / smartctl / ntfy plumbing error

Cron mail only fires if you let a non-zero exit through and have MAILTO set.
The Sunday heartbeat is an ntfy message, not a cron mail.


Layout
------

  smart_monitor.py     launcher for cron / schtasks
  smart_monitor/       shared library (Linux + Windows)
  config.example.ini   tracked template
  config.ini           gitignored site file
  tests/               unittest (no network, fake smartctl)


Tests
-----

    python3 -m unittest discover -s tests -t . -v
