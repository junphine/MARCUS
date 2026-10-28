call ..\.venv\Scripts\activate.bat

set HF_ENDPOINT=https://hf-mirror.com
set DOCLING_SERVE_ARTIFACTS_PATH=D:\Code\BB_kb\MARCUS\models
set DOCLING_SERVE_MAX_SYNC_WAIT=1800
docling-serve run --enable-ui --host 0.0.0.0 --port 5001
