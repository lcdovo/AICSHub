@echo off
rem One-click start: main stack + local embedding + reranker; local Qwen LLM stays off.
rem To include Langfuse, pass -Langfuse to scripts\start.ps1 instead.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -Reranker -LocalEmbedding
echo.
pause
