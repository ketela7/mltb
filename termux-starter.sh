apt update -y --fix-missing && apt upgrade -y
apt install -y git curl python3-full python3-pip p7zip-full ffmpeg aria2 qbittorrent-nox sabnzbdplus
curl https://rclone.org/install.sh | bash

python3 -m venv mltbenv
mltbenv/bin/pip install --no-cache-dir -r requirements.txt
