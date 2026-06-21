#!/usr/bin/env bash
# 用项目 .venv 执行 python 脚本
# 用法: bash run.sh scripts/foo.py <args>
# 自动安装依赖（首次 / .venv 不存在时）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -f "$VENV/bin/python3" ]; then
    echo "[run.sh] .venv 不存在，正在安装依赖..."
    uv venv "$VENV"
    uv pip install --python "$VENV/bin/python3" -r "$SCRIPT_DIR/pyproject.toml"
fi

exec "$VENV/bin/python3" "$@"
