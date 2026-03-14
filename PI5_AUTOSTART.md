# Pi5 Boot Autostart + Network Status

This project now includes scripts to:
- Start the app automatically after Raspberry Pi boot completes.
- Monitor network connectivity and log `connected` / `disconnected` status.

## Files
- `setup_pi_autostart.sh`: Installs and enables systemd services.
- `run_hioki_app.sh`: Launches `main.py` using venv python if available.
- `monitor_network_status.sh`: Writes network status changes.

## Install on Pi5
From project directory:

```bash
chmod +x setup_pi_autostart.sh
sudo ./setup_pi_autostart.sh
```

Optional target host for connectivity check:

```bash
sudo CHECK_HOST=172.18.72.16 ./setup_pi_autostart.sh
```

## Check status

```bash
systemctl status hioki-app.service
systemctl status hioki-network-status.service
cat network_status.txt
tail -f network_status.log
journalctl -u hioki-network-status.service -f
```

## Notes
- App service is configured for desktop session display `:0`.
- Network status is considered `connected` only when the host is reachable by ping.
- Default host is `172.18.72.16`.
