FROM python:3.11-slim

# torch CPU needs libgomp
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching (big: torch CPU ~800MB)
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY server.py client.py run_server.sh README.md ./
# Voice reference wavs for the Dharmendra clone (~9MB total; committed via Git LFS or directly)
COPY voice_ref.wav voice_ref_hi.wav ./

ENV HF_HOME=/data/hf_cache \
    CLONE_MAP_PATH=/data/current_clone.json \
    PORT=8001

EXPOSE 8001

CMD ["python", "server.py"]