@echo off
rem One-click stop: main stack + local embedding + reranker (matches start.bat).
rem To include Langfuse, pass -Langfuse to scripts\stop.ps1 instead.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1" -Reranker -LocalEmbedding
echo.
pause
