@echo off
title CodeMind LLM Servers

set LLAMA_BIN=E:\AI\llamacpp\llama.cpp\build\bin\Release\llama-server.exe
set MODEL_DIR=E:\AI\mod

echo Starting Arctic-Text2SQL on port 8000...
start "Arctic-Text2SQL" "%LLAMA_BIN%" -m "%MODEL_DIR%\Arctic-Text2SQL-R1-7B.i1-Q4_K_M.gguf" --alias Arctic-Text2SQL --reasoning off --host 0.0.0.0 --port 8000 -ngl 40 -c 8192 --temp 0.2 --top-p 0.9 --repeat-penalty 1.1 --mirostat 2 --mirostat-lr 0.05 -ctk q8_0 -ctv q8_0 -fa auto
timeout /t 5 /nobreak >nul
echo Both servers launched.
echo Arctic-Text2SQL : http://127.0.0.1:8000
