@echo off
chcp 65001 >nul
echo ============================================
echo   微博热榜爬虫系统 - 启动菜单
echo ============================================
echo.
echo   [1] 微博热榜流水线    (python main.py)
echo   [2] 作者监控          (python author_monitor_main.py)
echo   [3] 直播吧评论爬取    (python zhibo8_main.py)
echo   [4] API服务 + Swagger (uvicorn api_server:app)
echo   [5] 安装依赖          (pip install -r requirements.txt)
echo   [0] 退出
echo.
set /p choice=请选择操作:

if "%choice%"=="1" (
    echo 正在启动微博热榜流水线...
    python main.py
) else if "%choice%"=="2" (
    echo 正在启动作者监控...
    set /p author_id=输入作者ID(留空监控全部):
    if "%author_id%"=="" (
        python author_monitor_main.py
    ) else (
        python author_monitor_main.py --author-id %author_id%
    )
) else if "%choice%"=="3" (
    echo 正在启动直播吧评论爬取...
    set /p match_url=输入比赛URL:
    python zhibo8_main.py --url "%match_url%"
) else if "%choice%"=="4" (
    echo 正在启动API服务...
    echo Swagger文档: http://localhost:8000/docs
    echo 按 Ctrl+C 停止服务
    echo.
    uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
) else if "%choice%"=="5" (
    echo 正在安装依赖...
    pip install -r requirements.txt
    echo 依赖安装完成
    pause
) else if "%choice%"=="0" (
    exit
) else (
    echo 无效选择
    pause
)
