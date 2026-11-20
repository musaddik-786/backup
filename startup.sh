#!/bin/bash
cd /home/site/wwwroot
gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:${PORT:-8000} app.main:app
