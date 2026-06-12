#!/usr/bin/env bash
set -euo pipefail
cat >/etc/systemd/system/headway-news-monitor.timer <<'TIMER'
[Unit]
Description=Headway news monitor

[Timer]
OnCalendar=*-*-* 04,09,14:00:00
Persistent=true
Unit=headway-news-monitor.service

[Install]
WantedBy=timers.target
TIMER
systemctl daemon-reload
systemctl enable --now headway-news-monitor.timer
systemctl restart headway-news-monitor.timer
systemctl disable --now headway-news-monitor-restore.timer || true
