#!/bin/bash
if [ ! -d "venv" ]; then
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi
./venv/bin/python m3u_generator.py
