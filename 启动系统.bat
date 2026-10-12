@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
echo ================================================
echo  中国上市公司东南亚投资建厂公告雷达 V2.3.2
echo ================================================
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 创建 Python 虚拟环境...
  where py >nul 2>&1
  if %errorlevel%==0 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo 无法创建虚拟环境。请先安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。
  pause
  exit /b 1
)
if not exist ".venv\deps_installed.flag" (
  echo [2/3] 安装依赖（首次启动需要联网，可能需要几分钟）...
  .venv\Scripts\python.exe -m pip install --upgrade pip
  if errorlevel 1 goto INSTALL_FAILED
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto INSTALL_FAILED
  echo installed> .venv\deps_installed.flag
) else (
  echo [2/3] 检查到依赖已安装，跳过安装。
)
echo [3/3] 启动 Streamlit...
.venv\Scripts\python.exe -m streamlit run app.py
if errorlevel 1 goto RUN_FAILED
goto END
:INSTALL_FAILED
echo 依赖安装失败。请检查网络，或在命令行执行：.venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1
:RUN_FAILED
echo 程序退出。如有报错，请截图终端内容。
pause
:END
