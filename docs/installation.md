# Install

## Debian

apt update
apt install ...

## Clone repository

git clone https://github.com/yourname/energydash-pro.git

## Create Python environment

python3 -m venv venv

## Install packages

pip install -r requirements.txt

## Initialize database

python init_db.py

## Start services

systemctl start energydash-api
