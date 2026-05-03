#!/usr/bin/bash
source ./.venv/bin/activate && pyinstaller --onefile -w --add-data "./assets:./assets" --icon="./assets/favicon.ico" -n dnotool __main__.py
