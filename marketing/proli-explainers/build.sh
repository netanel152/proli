#!/bin/sh
# One command: the Proli explainer films (Pictogram Motion), 1080×1920, 24 fps — two films, two cuts each.
#   sh build.sh                       all four: customer + pro, full + short (15–20 s)
#   sh build.sh customer              one film, both cuts          (customer | pro)
#   sh build.sh pro short             one film, one cut            (full | short)
#   WORKERS=4 sh build.sh             more render workers (default 3)
# The end-card call to action (placeholders are drawn until these are set):
#   WA_NUMBER="050-000-0000" WA_QR=qr.png sh build.sh      the WhatsApp number, and a QR image in this folder
# The pro film's approve scene (what the pro really sees — confirm against the E2E):
#   APPROVE=typed sh build.sh pro     the typed word instead of the reply-button tap (default: button)
set -e
LIB=/c/Users/netan/lemo-opuscar
HERE=$(cd "$(dirname "$0")" && pwd)
export LIB
[ -f "$LIB/core/render/video.mjs" ] || { echo "set LIB to the library folder"; exit 1; }
PY=${PY:-"$LIB/.venv/bin/python"}
WORKERS=${WORKERS:-3}
FILMS=${1:-"customer pro"}
CUTS=${2:-"full short"}
SIZE=1080x1920
EXTRA="approve=${APPROVE:-button}"
[ -n "$WA_NUMBER" ] && EXTRA="$EXTRA&wa=$WA_NUMBER"
[ -n "$WA_QR" ] && { [ -f "$HERE/$WA_QR" ] || { echo "WA_QR: no such file in $HERE: $WA_QR"; exit 2; }; EXTRA="$EXTRA&qr=$WA_QR"; }
cd "$LIB"
"$PY" core/audio/sampler.py --need "$HERE/mix.py"
mkdir -p "$HERE/out"
FAIL=0
for F in $FILMS; do
  case $F in customer) BASE=proli-customers ;; pro) BASE=proli-pros ;; *) echo "film: customer | pro"; exit 2 ;; esac
  for C in $CUTS; do
    case $C in full) NAME=$BASE; SUB=subtitles_he_$F ;; short) NAME=$BASE-short; SUB=subtitles_he_${F}_short ;; *) echo "cut: full | short"; exit 2 ;; esac
    TAG=${F}_$C
    QS="film=$F&cut=$C&$EXTRA"
    node core/render/events.mjs "$HERE" --size $SIZE --q "$QS" --out "$HERE/events_$TAG.json"
    node core/render/readcheck.mjs "$HERE" --size $SIZE --q "$QS" --step 0.1 > "$HERE/out/readcheck_$TAG.txt" \
      && echo "readcheck ok ($TAG)" || { FAIL=1; echo "readcheck FAILED ($TAG):"; grep -v '^OK' "$HERE/out/readcheck_$TAG.txt" | tail -8; }
    node core/render/video.mjs "$HERE" --size $SIZE --q "$QS" --fps 24 --workers "$WORKERS" --out "$HERE/out/video_$TAG.mp4"
    "$PY" "$HERE/mix.py" "$F" "$C"
    sh core/render/mux.sh "$HERE/out/video_$TAG.mp4" "$HERE/out/mix_$TAG.wav" "$HERE/$NAME.mp4" 24 0
    "$PY" "$HERE/srt.py" "$HERE/events_$TAG.json" "$HERE/$SUB.srt"
  done
done
# poster: the customer film's first step card
node core/render/still.mjs "$HERE" 6.5 --size $SIZE --q "film=customer&cut=full&$EXTRA" --out "$HERE/out/poster" > /dev/null
ffmpeg -v error -y -i "$HERE/out/poster/t_6.5.jpg" -q:v 3 "$HERE/poster.jpg"
[ -z "$WA_NUMBER" ] && echo "note: the end cards carry the PLACEHOLDER number and QR — set WA_NUMBER and WA_QR before publishing"
[ "$FAIL" = 0 ] || { echo "done, but a readcheck failed: $HERE"; exit 1; }
echo "done: $HERE"
