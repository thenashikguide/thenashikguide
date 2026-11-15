#!/usr/bin/env bash
# Publishes full-size images/videos to the `media` branch so Instagram can download them.
# The branch is rewritten as a single commit holding only the last N days, so the repo stays small.
set -euo pipefail
KEEP_DAYS="${KEEP_DAYS:-7}"
REMOTE="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
WORK="$(mktemp -d)"

if git clone -q --depth 1 --branch media "$REMOTE" "$WORK" 2>/dev/null; then
  rm -rf "$WORK/.git"
fi

# add today's (and any other local) media
if compgen -G "posts/*/media" > /dev/null; then
  for d in posts/*/media; do
    mkdir -p "$WORK/$d"
    cp -r "$d/." "$WORK/$d/"
  done
fi

# drop days older than KEEP_DAYS
CUTOFF="$(date -u -d "${KEEP_DAYS} days ago" +%F)"
for d in "$WORK"/posts/*/; do
  [ -d "$d" ] || continue
  day="$(basename "$d")"
  if [[ "$day" < "$CUTOFF" ]]; then rm -rf "$d"; fi
done

cd "$WORK"
git init -q -b media
git config user.name "insta-autopilot"
git config user.email "actions@users.noreply.github.com"
git add -A
git commit -qm "media $(date -u +%F)" || true
git push -q -f "$REMOTE" media
echo "Media branch updated."
