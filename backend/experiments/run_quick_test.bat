@echo off
REM 消融实验快速启动脚本 (Windows)
REM 前置条件: Neo4j + DashVector 服务已启动，.env 已配置

cd /d "%~dp0.."

echo ============================================
echo   MADDx 消融实验 — 快速测试模式 (5 cases)
echo ============================================
echo.

REM 激活虚拟环境
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo [OK] 虚拟环境已激活
) else (
    echo [WARN] 未找到 .venv，使用系统 Python
)

echo 将运行 A/B/C 三组各 5 条用例...
echo.

python -m experiments.run_ablation ^
    --dataset experiments/data/maddx_eval_40.jsonl ^
    --out experiments/results ^
    --conditions A,B,C ^
    --limit 5 ^
    --parallel 1

echo.
echo ============================================
echo 完成！结果保存在 experiments/results/
echo ============================================
pause
