#!/usr/bin/env bash
# 24/7 YouTube live stream on the Oracle Cloud VM.
#
# Plays every programme in $ROOT/programs in name order (names start with the date), forever.
# A programme is a folder with scene.mp4 (a short H.264 loop, keyframe every 2 s) and audio.m4a
# (AAC). Both are already encoded on the laptop, so this only copies packets: almost no CPU.
# The bot adds new programmes and removes old ones; a folder only appears once fully uploaded.
set -u
ROOT=${STREAM_ROOT:-/home/ubuntu/stream}
URL="${STREAM_URL:-rtmp://a.rtmp.youtube.com/live2}/${YOUTUBE_STREAM_KEY:?set YOUTUBE_STREAM_KEY in /etc/ambient-stream.env}"

while true; do
  played=0
  for dir in "$ROOT"/programs/*/; do
    [ -f "$dir/scene.mp4" ] && [ -f "$dir/audio.m4a" ] || continue
    played=1
    echo "playing $(basename "$dir")"
    ffmpeg -hide_banner -loglevel warning -nostdin \
      -re -stream_loop -1 -i "$dir/scene.mp4" \
      -re -i "$dir/audio.m4a" \
      -map 0:v:0 -map 1:a:0 -c copy -shortest \
      -f flv -flvflags no_duration_filesize "$URL" \
      || { echo "ffmpeg exited with $?; next programme in 5 s"; sleep 5; }
  done
  [ "$played" = 1 ] || { echo "no programmes yet"; sleep 60; }
done
