@echo off
:: 用项目 .venv 执行 python 脚本（Windows 原生）
:: 用法: run.bat scripts\foo.py <args>
:: 首次运行自动创建 .venv 并安装依赖

set SCRIPT_DIR=%~dp0
set VENV=%SCRIPT_DIR%.venv

if not exist "%VENV%\Scripts\python.exe" (
    echo [run.bat] .venv 不存在，正在安装依赖...
    uv venv "%VENV%"
    uv pip install --python "%VENV%\Scripts\python.exe" -r "%SCRIPT_DIR%pyproject.toml"
)

"%VENV%\Scripts\python.exe" %*
