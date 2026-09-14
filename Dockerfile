# Linux deployment (no GPU: USE_GPU=false renders with libx264, slower than the RTX laptop).
# docker build -t ambient-bot .
# docker run -d --name ambient-bot --restart unless-stopped \
#   --env-file .env -v $PWD/data:/app/data -v $PWD/output:/app/output -v $PWD/cache:/app/cache \
#   -v $PWD/assets:/app/assets -v $HOME/.ambient_bot:/secrets ambient-bot
# (set YOUTUBE_*_PATH and FREESOUND_TOKEN_PATH in .env to /secrets/...)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV USE_GPU=false
CMD ["sh", "-c", "python main.py db-upgrade && python main.py run-scheduler"]
