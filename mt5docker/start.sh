#!/bin/bash
cd /mt5docker

# remove display lock if any
rm -rf /tmp/.X100-lock

# set up display
export DISPLAY=:100
Xvfb :100 -ac -screen 0 1024x768x24 &
x11vnc -storepasswd $VNC_PWD /mt5docker/passwd
x11vnc -display :100 -forever -rfbport 5901 -rfbauth /mt5docker/passwd &
chmod 600 /mt5docker/passwd
/mt5docker/noVNC-master/utils/novnc_proxy --vnc localhost:5901 --listen 6081 &

# install python dependencies if not already installed
if [ ! -f "/opt/wineprefix/pip_installed.flag" ]; then
  wine pip install --no-cache-dir -r /mt5docker/requirements.txt
  touch /opt/wineprefix/pip_installed.flag
fi

# install vcrun2019 if not already installed
if ! grep -q "vcrun2019" /opt/wineprefix/winetricks.log 2>/dev/null; then
  winetricks --unattended vcrun2019
fi

# prevent container termination
while true
do
  sleep 1
done
