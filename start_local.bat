@echo off
:: Antinet-AML 适配服务本机启动脚本（评测期间保持此窗口常开、机器不睡眠）
cd /d C:\D\zhiyi\agent_memory_challenge

IF NOT EXIST .venv (
    echo [setup] 首次运行：创建虚拟环境并安装依赖...
    python -m venv .venv
    call .venv\Scripts\activate
    pip install -r requirements.txt
) ELSE (
    call .venv\Scripts\activate
)

echo [run] 启动 AML 适配服务（监听 127.0.0.1:8080，鉴权=AML_API_KEY/LDBD Key）
python app.py
pause
