#!/bin/sh
# One command: both Proli explainer films (Pictogram Motion), 1080×1920, 24 fps.
#   sh build.sh                 both films
#   sh build.sh customer        one film (customer | pro)
#   WORKERS=4 sh build.sh       more render workers (default 3)
set -e
LIB=/c/Users/netan/lemo-opuscar
HERE=$(cd "$(dirname "$0")" && pwd)
export LIB
[ -f "$LIB/core/render/video.mjs" ] || { echo "set LIB to the library folder"; exit 1; }
PY=${PY:-"$LIB/.venv/bin/python"}
WORKERS=${WORKERS:-3}
FILMS=${1:-"customer pro"}
SIZE=1080x1920
cd "$LIB"
"$PY" core/audio/sampler.py --need "$HERE/mix.py"
mkdir -p "$HERE/out"
for F in $FILMS; do
  case $F in customer) NAME=proli-customers ;; pro) NAME=proli-pros ;; *) echo "film: customer | pro"; exit 2 ;; esac
  node core/render/events.mjs "$HERE" --size $SIZE --q "film=$F" --out "$HERE/events_$F.json"
  node core/render/video.mjs "$HERE" --size $SIZE --q "film=$F" --fps 24 --workers "$WORKERS" --out "$HERE/out/video_$F.mp4"
  "$PY" "$HERE/mix.py" "$F"
  sh core/render/mux.sh "$HERE/out/video_$F.mp4" "$HERE/out/mix_$F.wav" "$HERE/$NAME.mp4" 24 0
  node core/render/readcheck.mjs "$HERE" --size $SIZE --q "film=$F" --step 0.1 > "$HERE/out/readcheck_$F.txt" && echo "readcheck ok ($F)" || { grep -v '^OK' "$HERE/out/readcheck_$F.txt" | tail -8; }
  "$PY" "$HERE/srt.py" "$F" "$HERE/subtitles_he_$F.srt"
done
# poster: the customer film's brand card
node core/render/still.mjs "$HERE" 6.5 --size $SIZE --q film=customer --out "$HERE/out/poster" > /dev/null
ffmpeg -v error -y -i "$HERE/out/poster/t_6.5.jpg" -q:v 3 "$HERE/poster.jpg"
echo "done: $HERE"
