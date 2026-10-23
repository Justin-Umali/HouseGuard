#!/usr/bin/env bash
# HomeGuard installer for Raspberry Pi OS (Pi 3). Run from the project folder: bash install.sh
set -e
cd "$(dirname "$0")"

echo "==> System packages"
sudo apt update
sudo apt install -y python3-venv python3-pip python3-opencv python3-numpy python3-lgpio v4l-utils curl

echo "==> Python virtual environment (uses system OpenCV/numpy to avoid slow builds)"
python3 -m venv --system-site-packages venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install flask gunicorn gpiozero
echo "==> Firebase (optional cloud dashboard; can take several minutes on a Pi 3)"
./venv/bin/pip install firebase-admin || echo "  firebase-admin failed to install (cloud sync will stay off)"

echo "==> Optional MobileNet-SSD model (better than the HOG fallback)"
mkdir -p models
curl -fL -o models/MobileNetSSD_deploy.prototxt \
  https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt || echo "  prototxt download failed (HOG fallback will be used)"
curl -fL -o models/MobileNetSSD_deploy.caffemodel \
  https://github.com/PINTO0309/MobileNet-SSD-RealSense/raw/master/caffemodel/MobileNetSSD/MobileNetSSD_deploy.caffemodel \
  || echo "  caffemodel download failed (HOG fallback will be used)"
# Remove half-downloaded files so the app falls back cleanly
[ -s models/MobileNetSSD_deploy.caffemodel ] || rm -f models/MobileNetSSD_deploy.caffemodel models/MobileNetSSD_deploy.prototxt

[ -f .env ] || { cp .env.example .env; echo "==> Created .env - EDIT IT NOW (password + email): nano .env"; }

echo "==> Camera check"
ls /dev/video* 2>/dev/null || echo "  No camera found at /dev/video*"
echo
echo "Done. Test run:  ./venv/bin/python app.py"
echo "Auto-start on boot: see README.md (systemd)"
