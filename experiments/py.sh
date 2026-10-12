#!/bin/sh
# 用實驗 venv 執行 Python（-X utf8 避免 cp932）
exec "$LOCALAPPDATA/ddr-pose/venv-exp/Scripts/python.exe" -X utf8 "$@"
