# Attendance Kiosk Setup (Raspberry Pi 5)

This app provides:
- Touchscreen UI with `Start Attendance`, `Pause`, `Stop`, `Export CSV`
- Live tag reads from M7E Hecto (`readasync` backend)
- CSV download over local web server (phone-friendly)
- Optional email sending of exported CSV

## 1) Install dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-pip build-essential
```

## 2) Start app manually

From project root:

```bash
chmod +x run-attendance-kiosk.sh
./run-attendance-kiosk.sh
```

Open:
- On Pi touchscreen: `http://localhost:8080`
- On phone (same Wi-Fi): `http://<PI_IP>:8080`

## 3) Configure reader defaults (optional)

```bash
export ATTENDANCE_URI="tmr:///dev/ttyUSB0"
export ATTENDANCE_ANTENNA="1"
export ATTENDANCE_READ_POWER="1900"
export ATTENDANCE_PORT="8080"
./run-attendance-kiosk.sh
```

## 4) Optional email export setup

Set SMTP environment variables before launching:

```bash
export ATTENDANCE_SMTP_HOST="smtp.gmail.com"
export ATTENDANCE_SMTP_PORT="587"
export ATTENDANCE_SMTP_USER="your@email.com"
export ATTENDANCE_SMTP_PASS="app_password"
export ATTENDANCE_SMTP_FROM="your@email.com"
export ATTENDANCE_SMTP_TLS="1"
```

Then use "Send Email" in UI.

## 5) Kiosk mode autostart (Chromium)

Create autostart file:

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/attendance-kiosk.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Attendance Kiosk
Exec=sh -c "cd /home/pi/attendanceTaker && ./run-attendance-kiosk.sh & sleep 5 && chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:8080"
X-GNOME-Autostart-enabled=true
EOF
```

Replace `/home/pi/attendanceTaker` with your real project path.

## 6) CSV location

Exported files are saved in:

```text
exports/
```

And can be downloaded via app endpoint:

```text
/downloads/<filename>
```
