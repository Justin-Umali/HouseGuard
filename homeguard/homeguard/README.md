# HomeGuard: AI CCTV with 3 detection levels

Raspberry Pi 3 + PIR motion sensor + USB webcam (eMeet) -> person detection -> email alerts -> website.

## The 3 levels

| Level | Name | Trigger |
|------|------|---------|
| 1 | Camera | The AI model sees a person on camera |
| 2 | Motion | The PIR sensor also detects motion (within the confirm window, default 8 s) |
| 3 | Danger | The confirmed presence lasts 3+ seconds -> email with snapshot is sent |

Requiring camera + PIR to agree removes most false alarms (shadows, posters, pets, TV).
Everything (windows, thresholds, cooldown, recipients) is adjustable on the Settings page.

## Wiring (HC-SR501 PIR)

| PIR pin | Pi 3 pin |
|---------|----------|
| VCC | 5V (physical pin 2) |
| GND | GND (physical pin 6) |
| OUT | GPIO17 (physical pin 11) |

The HC-SR501 output is 3.3 V, safe for the Pi. Let it warm up ~60 s after power-on.
Tune its two potentiometers: sensitivity and hold time (turn hold time to minimum).

## Install (Raspberry Pi OS)

```bash
# copy this folder to the Pi, e.g. ~/homeguard
cd ~/homeguard
bash install.sh
nano .env            # set HG_PASSWORD, SMTP_USER, SMTP_PASS, ALERT_TO
./venv/bin/python app.py
```

Open `http://<pi-ip>:5000` (find the IP with `hostname -I`) and sign in.

### Gmail alerts
1. Turn on 2-Step Verification on the sending Google account.
2. Create an **App password** (Google Account -> Security -> App passwords).
3. Put it in `SMTP_PASS` (not your normal password). Use the Settings page -> "Send test email".

### Auto-start on boot
```bash
sudo cp homeguard.service /etc/systemd/system/
sudo nano /etc/systemd/system/homeguard.service   # fix User/paths if not "pi"
sudo systemctl enable --now homeguard
journalctl -u homeguard -f                         # logs
```

## Testing without the PIR
Use "Simulate PIR motion" on the dashboard. The system also runs on a laptop with a webcam
(PIR is simply reported as "not found").

## Performance tips (Pi 3)
- Keep the stream at 640x480 (`FRAME_WIDTH/HEIGHT` in `.env`).
- Raise "Detection interval" in Settings if the Pi runs hot (0.6 s is a good start).
- MobileNet-SSD (downloaded by `install.sh`) is more accurate than the HOG fallback.
  Status on the dashboard shows which one is active.

## Cloud link (Firebase + Vercel)
Want a link you can open from anywhere, with accounts, camera preview and searchable events? See **CLOUD_SETUP.md**.

## Security notes
- Choose a long `HG_PASSWORD`. The site has login, CSRF protection and login rate-limiting.
- Do NOT port-forward this to the internet as is. For remote access use Tailscale or a VPN, or put
  it behind HTTPS (e.g. a Caddy/Nginx reverse proxy).
- Cameras in your home record people: tell household members and guests, and follow local laws.

## Files
```
app.py                 web server + entry point
homeguard/engine.py    3-level logic
homeguard/detector.py  MobileNet-SSD / HOG person detection
homeguard/pir.py       PIR sensor
homeguard/camera.py    threaded USB camera
homeguard/notifier.py  email alerts
homeguard/db.py        SQLite event log
templates/ static/     website
```

## Ideas for next versions
Face recognition for known family members (suppress alerts), short video clips in the email,
Telegram/SMS alerts, siren via relay on a GPIO pin, night mode with IR lighting.
