"""The Nashik Guide - faceless Instagram autopilot.

Commands:
  python bot.py generate            make today's posts (text + original art + reels) if missing
  python bot.py regenerate          re-make today's posts that are not published yet
  python bot.py publish             publish the next scheduled post for today
  python bot.py skip <slot>         skip a post of today (slot number 1, 2, 3...)
  python bot.py monitor             save follower count + per-post stats
  python bot.py report              write data/report.md
  python bot.py dashboard           rebuild data/dashboard.json (done automatically too)

Environment variables (GitHub Secrets):
  CLAUDE_CODE_OAUTH_TOKEN  writes the content using your Claude Pro/Max plan
                           (create it with `claude setup-token`)
  ANTHROPIC_API_KEY   alternative: a Claude Console API key (pay-as-you-go)
  OPENAI_API_KEY      creates the original artwork (optional; fallback art without it)
  IG_USER_ID          Instagram professional account ID
  IG_ACCESS_TOKEN     long-lived token with publish + insights permissions
  GRAPH_HOST          optional, default graph.facebook.com (graph.instagram.com for Instagram Login)
  GRAPH_VERSION       optional, default v24.0
  MEDIA_BASE_URL      optional; public URL prefix where posts/ media is served.
                      Default: raw.githubusercontent.com/<repo>/media/
  DRY_RUN=1           never call Instagram (for testing)
"""
import base64
import csv
import datetime as dt
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import requests
import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent
POSTS = ROOT / "posts"
DATA = ROOT / "data"
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DRY = os.getenv("DRY_RUN") == "1"
SLOT_TIMES = ["09:00", "13:00", "19:00", "21:00", "11:00"]


def now():
    return dt.datetime.now(IST)


def today():
    return now().strftime("%Y-%m-%d")


def log(*a):
    print(*a, flush=True)


# ================================================================ Instagram API
def graph_url(path):
    host = os.getenv("GRAPH_HOST", "graph.facebook.com")
    ver = os.getenv("GRAPH_VERSION", "v24.0")
    return f"https://{host}/{ver}/{path.lstrip('/')}"


def graph(method, path, **params):
    params["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    r = requests.request(method, graph_url(path), params=params, timeout=90)
    if r.status_code >= 400:
        raise RuntimeError(f"Instagram API error {r.status_code}: {r.text[:500]}")
    return r.json()


# ================================================================ history & stats
def load_json(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def load_posted():
    return load_json(DATA / "posted.json", [])


def save_posted(items):
    DATA.mkdir(exist_ok=True)
    (DATA / "posted.json").write_text(json.dumps(items, indent=2, ensure_ascii=False))


def latest_stats():
    f = DATA / "post_stats.csv"
    rows = {}
    if f.exists():
        with f.open() as fh:
            for row in csv.DictReader(fh):
                rows[row["media_id"]] = row
    return rows


def num(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def score(s):
    return num(s.get("likes")) + 3 * num(s.get("comments")) + 4 * num(s.get("saved")) + 5 * num(s.get("shares"))


def performance_summary():
    stats = latest_stats()
    scored = []
    for p in load_posted():
        s = stats.get(p.get("media_id", ""))
        if s:
            scored.append((score(s), num(s.get("reach")), p.get("kind", "image"), p["headline"], p.get("topic", "")))
    if not scored:
        return "No performance data yet - new account. Try a varied mix of formats."
    scored.sort(reverse=True)
    lines = ["Best performing posts (score | reach | format | headline | topic):"]
    lines += [f"- {a} | {b} | {c} | {d} | {e}" for a, b, c, d, e in scored[:6]]
    lines.append("Worst performing posts:")
    lines += [f"- {a} | {b} | {c} | {d} | {e}" for a, b, c, d, e in scored[-3:]]
    return "\n".join(lines)


# ================================================================ writing (Claude)
def ask_claude(prompt, max_tokens=8000):
    """Uses your Claude Pro/Max plan through Claude Code (CLAUDE_CODE_OAUTH_TOKEN),
    or a Console API key (ANTHROPIC_API_KEY) if you have one."""
    if os.getenv("CLAUDE_CODE_OAUTH_TOKEN") and not os.getenv("ANTHROPIC_API_KEY"):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        cmd = ["claude", "-p", "--output-format", "json", "--max-turns", "1"]
        if CFG.get("model"):
            cmd += ["--model", str(CFG["model"])]
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=env, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"Claude Code failed: {r.stderr[-800:] or r.stdout[-800:]}")
        out = json.loads(r.stdout)
        if out.get("is_error"):
            raise RuntimeError(f"Claude Code error: {str(out.get('result'))[:800]}")
        return out.get("result", "")
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=CFG.get("model", "claude-sonnet-5-5"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def write_content(kinds):
    posted = load_posted()
    recent = [p["headline"] for p in posted[-80:]]
    recent_topics = [p.get("topic", "") for p in posted[-9:]]
    facts = ""
    if CFG.get("facts_file") and (ROOT / CFG["facts_file"]).exists():
        facts = (ROOT / CFG["facts_file"]).read_text()
    pillars = "\n".join(f"- {p}" for p in CFG.get("content_pillars", []))
    countdown = ""
    if CFG.get("countdown_to"):
        days = (dt.date.fromisoformat(str(CFG["countdown_to"])) - now().date()).days
        if days >= 0:
            countdown = f"Today is {today()}. {days} days left until the {CFG.get('countdown_label', 'event')}."
    n_scenes = CFG.get("reel", {}).get("scenes", 4)
    style = CFG.get("image", {}).get("art_style", "")
    prompt = f"""You are the content strategist for a FACELESS Instagram account.

Niche: {CFG['niche']}
Audience: {CFG['audience']}
Tone: {CFG['tone']}
Language: {CFG['language']}
{countdown}

Content pillars (rotate; avoid the most recent topics: {recent_topics}):
{pillars}

VERIFIED FACTS - use ONLY these for dates, numbers and names. Never invent facts.
{facts}

{performance_summary()}

Headlines already used (do NOT repeat these ideas):
{json.dumps(recent, ensure_ascii=False)}

Create {len(kinds)} posts for today, in this order of formats: {json.dumps(kinds)}.
Each post from a DIFFERENT pillar. Lean toward what performed best, keep variety.
Mix formats of ideas: quick facts, myth vs fact, mini guides, checklists, "did you know",
mythology stories, questions that invite comments. At most one post may use the countdown.

ARTWORK RULES (every picture is original AI art in this style: {style}):
- Describe scenes: ghats, river Godavari, lamps, temples' architecture, hills, sunrise,
  processions seen from far away or from behind, flowers, diyas, flags, bells.
- No written text, letters or logos inside the picture. No close-up human faces.
  No real identifiable people. Depict deities and sadhus respectfully, never mocking.
- Each image_prompt: one vivid sentence, 25-45 words, vertical composition.

Return ONLY a JSON array. Every object has:
- "kind": the format given ("image" or "reel")
- "topic": 2-4 word category
- "headline": max 8 words, punchy
- "subtext": max 20 words (shown under the headline on image posts)
- "caption": 60-150 words, hook in the first line, value in the middle, end with a
  question or call to action (save / share / follow)
- "hashtags": list of {CFG.get('hashtags_per_post', 12)} hashtags without '#'
- for kind "image": "image_prompt"
- for kind "reel": "scenes": list of {n_scenes} objects {{"image_prompt": ..., "text": max 9 words}}
  that tell a mini story; the first scene's text is the hook.
"""
    text = ask_claude(prompt)
    items = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
    for i, k in enumerate(kinds):
        if i < len(items):
            items[i]["kind"] = k
    return items[: len(kinds)]


def sample_content(kinds):
    """Offline placeholder content for DRY_RUN tests without an API key."""
    out = []
    for i, k in enumerate(kinds, 1):
        p = {"kind": k, "topic": "Trimbakeshwar", "headline": f"Where the Godavari is born ({i})",
             "subtext": "Brahmagiri hill near Trimbakeshwar is the source of the holy Godavari, called Dakshin Ganga.",
             "caption": "Sample caption for testing.", "hashtags": ["nashik", "kumbhmela2027"],
             "image_prompt": "Sunrise over Brahmagiri hill with a temple below"}
        if k == "reel":
            p["scenes"] = [{"image_prompt": "Temple at dawn", "text": t} for t in
                           ["Godavari is born here", "Brahmagiri hill, near Trimbakeshwar",
                            "They call it Dakshin Ganga", "Save this for your Kumbh trip"]]
        out.append(p)
    return out


# ================================================================ artwork
def font(size, bold=True):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for base in ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"]:
        p = Path(base) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ai_image(prompt, out_path, seed_text=""):
    """Create original artwork. Returns True if AI-made, False if fallback art."""
    cfg = CFG.get("image", {})
    key = os.getenv("OPENAI_API_KEY")
    if key and not DRY:
        full = f"{prompt}. Style: {cfg.get('art_style', '')}. No text, no letters, no logos, no close-up faces."
        for attempt in range(3):
            try:
                r = requests.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": cfg.get("model", "gpt-image-1"), "prompt": full,
                          "size": cfg.get("size", "1024x1536"), "quality": cfg.get("quality", "medium"), "n": 1},
                    timeout=240,
                )
                r.raise_for_status()
                d = r.json()["data"][0]
                if d.get("b64_json"):
                    Path(out_path).write_bytes(base64.b64decode(d["b64_json"]))
                else:
                    Path(out_path).write_bytes(requests.get(d["url"], timeout=120).content)
                return True
            except Exception as e:  # noqa: BLE001
                log(f"Image generation failed (attempt {attempt + 1}): {e}")
                time.sleep(10)
    fallback_art(out_path, seed_text or prompt)
    return False


def fallback_art(out_path, seed_text):
    """Simple original illustration: sky gradient, sun, hills, temple, river."""
    rnd = random.Random(seed_text)
    W, H = 1024, 1536
    c = CFG["colors"]
    top, bot = rgb(c["background_top"]), rgb(c["background_bottom"])
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    sx, sy, sr = rnd.randint(250, 780), rnd.randint(380, 620), rnd.randint(90, 140)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for r in range(sr * 3, sr, -6):
        a = int(60 * (1 - (r - sr) / (sr * 2)))
        gd.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(255, 210, 120, max(a, 0)))
    gd.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(255, 220, 150, 255))
    img = Image.alpha_composite(img.convert("RGBA"), glow)
    d = ImageDraw.Draw(img)
    base = 1000
    for layer, shade in enumerate([(92, 30, 30), (70, 22, 24)]):
        pts = [(0, H)]
        x = 0
        while x <= W:
            pts.append((x, base - rnd.randint(60, 200) + layer * 60))
            x += rnd.randint(120, 220)
        pts += [(W, H)]
        d.polygon(pts, fill=shade)
    # temple silhouette
    tx, ty = rnd.randint(260, 760), 1060
    sil = (45, 14, 16)
    d.rectangle([tx - 110, ty, tx + 110, ty + 140], fill=sil)
    for i, w in enumerate([90, 70, 52, 36, 20]):
        y0 = ty - 50 * (i + 1)
        d.polygon([(tx - w, y0 + 50), (tx + w, y0 + 50), (tx + w * 0.7, y0), (tx - w * 0.7, y0)], fill=sil)
    d.polygon([(tx, ty - 300), (tx - 8, ty - 250), (tx + 8, ty - 250)], fill=sil)
    d.line([(tx, ty - 300), (tx, ty - 360)], fill=sil, width=4)
    d.polygon([(tx, ty - 360), (tx + 46, ty - 345), (tx, ty - 330)], fill=(226, 113, 29))
    # river with reflections and diyas
    d.rectangle([0, 1200, W, H], fill=(31, 70, 90))
    for y in range(1210, H, 22):
        for _ in range(4):
            x = rnd.randint(0, W)
            d.line([(x, y), (x + rnd.randint(40, 160), y)], fill=(240, 180, 110), width=2)
    for _ in range(14):
        x, y = rnd.randint(40, W - 40), rnd.randint(1260, H - 60)
        d.ellipse([x - 9, y - 4, x + 9, y + 6], fill=(120, 60, 20))
        d.ellipse([x - 4, y - 18, x + 4, y - 2], fill=(255, 200, 90))
    img.convert("RGB").save(out_path, quality=92)


def cover(img, W, H):
    s = max(W / img.width, H / img.height)
    img = img.resize((math.ceil(img.width * s), math.ceil(img.height * s)), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def wrap(draw, text, f, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(draw, text, max_w, start, minimum, max_lines, bold=True):
    size = start
    while size > minimum:
        f = font(size, bold)
        lines = wrap(draw, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
        size -= 4
    f = font(minimum, bold)
    return f, wrap(draw, text, f, max_w)[:max_lines]


def shade(img, y_from, strength=215):
    """Dark gradient so white text is readable on any artwork."""
    W, H = img.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for y in range(y_from, H):
        a = int(strength * min(1, (y - y_from) / max(1, (H - y_from) * 0.55)))
        d.line([(0, y), (W, y)], fill=(20, 8, 6, a))
    return Image.alpha_composite(img.convert("RGBA"), ov)


def draw_text_block(img, topic, headline, subtext, y_text, pad=80):
    c = CFG["colors"]
    W, H = img.size
    d = ImageDraw.Draw(img)
    accent, txt = rgb(c["accent"]), rgb(c["text"])
    y = y_text
    if topic:
        tf = font(30)
        tag = topic.upper()
        tw = d.textlength(tag, font=tf)
        d.rounded_rectangle([pad, y, pad + tw + 40, y + 52], 26, fill=accent)
        d.text((pad + 20, y + 9), tag, font=tf, fill=rgb(c["background_top"]))
        y += 76
    hf, hl = fit(d, headline, W - 2 * pad, 84, 52, 3)
    for line in hl:
        d.text((pad, y), line, font=hf, fill=txt, stroke_width=2, stroke_fill=(20, 8, 6))
        y += int(hf.size * 1.14)
    if subtext:
        y += 14
        d.rectangle([pad, y, pad + 120, y + 8], fill=accent)
        y += 30
        sf, sl = fit(d, subtext, W - 2 * pad, 40, 30, 3, bold=False)
        for line in sl:
            d.text((pad, y), line, font=sf, fill=txt)
            y += int(sf.size * 1.35)
    return y


def footer(img, pad=80, y=None):
    c = CFG["colors"]
    W, H = img.size
    d = ImageDraw.Draw(img)
    ff = font(30)
    y = y if y is not None else H - 80
    d.text((pad, y), CFG["instagram_handle"], font=ff, fill=rgb(c["accent"]))
    bn = CFG["brand_name"]
    d.text((W - pad - d.textlength(bn, font=ff), y), bn, font=ff, fill=rgb(c["text"]))


def make_feed_image(post, art_path, out_path):
    W, H = 1080, 1350  # 4:5 portrait, the largest feed format
    img = shade(cover(Image.open(art_path).convert("RGB"), W, H), int(H * 0.38))
    # measure the block height first, then anchor it to the bottom
    probe = img.copy()
    end = draw_text_block(probe, post.get("topic"), post["headline"], post.get("subtext"), 0)
    draw_text_block(img, post.get("topic"), post["headline"], post.get("subtext"), H - 130 - end)
    footer(img)
    img.convert("RGB").save(out_path, "JPEG", quality=92)


def make_thumb(src, out_path, w=360):
    im = Image.open(src).convert("RGB")
    im.thumbnail((w, w * 2))
    im.save(out_path, "JPEG", quality=80)


def make_reel(post, media_dir, slot):
    """Faceless Reel: original art scenes + text, slow zoom, soft drone music."""
    rc = CFG.get("reel", {})
    secs = float(rc.get("seconds_per_scene", 3.5))
    fps = 30
    W, H = 1080, 1920
    frames = []
    scenes = post["scenes"]
    for i, sc in enumerate(scenes):
        art = media_dir / f"{slot}_art{i}.png"
        ai_image(sc["image_prompt"], art, seed_text=f"{post['headline']}-{i}")
        img = shade(cover(Image.open(art).convert("RGB"), W, H), int(H * 0.35), 190)
        # keep text above the bottom 380px (Reels UI covers it) and away from the right edge
        probe = img.copy()
        topic = post.get("topic") if i == 0 else None
        end = draw_text_block(probe, topic, sc["text"], None, 0, pad=90)
        draw_text_block(img, topic, sc["text"], None, H - 420 - end, pad=90)
        footer(img, pad=90, y=H - 360)
        fp = media_dir / f"{slot}_frame{i}.jpg"
        img.convert("RGB").save(fp, quality=93)
        frames.append(fp)
    # closing call-to-action frame
    end_img = cover(Image.open(media_dir / f"{slot}_art0.png").convert("RGB"), W, H).filter(ImageFilter.GaussianBlur(18))
    end_img = shade(end_img, 0, 150)
    d = ImageDraw.Draw(end_img)
    f1, f2 = font(72), font(44, bold=False)
    lines = wrap(d, "Follow for your daily Kumbh 2027 guide", f1, W - 180)
    y = 700
    for line in lines:
        d.text((90, y), line, font=f1, fill=(255, 255, 255))
        y += 86
    d.text((90, y + 30), CFG["instagram_handle"], font=f2, fill=rgb(CFG["colors"]["accent"]))
    fp = media_dir / f"{slot}_frame_end.jpg"
    end_img.convert("RGB").save(fp, quality=93)
    frames.append(fp)

    n = len(frames)
    dur = n * secs
    d_frames = int(secs * fps)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for fp in frames:
        cmd += ["-i", str(fp)]
    music = rc.get("music", "drone") != "none"
    if music:
        expr = ("0.07*sin(2*PI*130.81*t)+0.05*sin(2*PI*196.00*t)+0.035*sin(2*PI*261.63*t)"
                "*(0.65+0.35*sin(2*PI*0.2*t))+0.02*sin(2*PI*392.00*t)*(0.5+0.5*sin(2*PI*0.13*t))")
        cmd += ["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={dur}"]
    parts = []
    for i in range(n):
        zoom = "min(zoom+0.0009,1.10)" if i % 2 == 0 else "if(eq(on,1),1.10,max(zoom-0.0009,1.0))"
        parts.append(f"[{i}:v]scale={int(W*1.1)}:{int(H*1.1)},zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':"
                     f"y='ih/2-(ih/zoom/2)':d={d_frames}:s={W}x{H}:fps={fps},setsar=1,format=yuv420p[v{i}]")
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vout]")
    if music:
        parts.append(f"[{n}:a]afade=t=in:d=1.5,afade=t=out:st={dur - 2}:d=2[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
    if music:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    out = media_dir / f"{slot}.mp4"
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-r", str(fps),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", str(dur), str(out)]
    subprocess.run(cmd, check=True)
    shutil.copy(frames[0], media_dir / f"{slot}_cover.jpg")
    for i in range(len(scenes)):  # keep the repo light
        (media_dir / f"{slot}_art{i}.png").unlink(missing_ok=True)
    for fp in frames:
        fp.unlink(missing_ok=True)
    return out


# ================================================================ generate / publish
def day_posts(day=None):
    dd = POSTS / (day or today())
    if not dd.exists():
        return []
    return sorted((json.loads(f.read_text()) for f in dd.glob("*.json")), key=lambda p: p["slot"])


def save_post(post, day=None):
    dd = POSTS / (day or today())
    (dd / f"{post['slot']}.json").write_text(json.dumps(post, indent=2, ensure_ascii=False))


def build_post(post, slot, kind):
    dd = POSTS / today()
    media = dd / "media"
    media.mkdir(parents=True, exist_ok=True)
    post.update({"slot": slot, "kind": kind, "status": "scheduled",
                 "time": SLOT_TIMES[(slot - 1) % len(SLOT_TIMES)], "created_at": now().isoformat(timespec="minutes")})
    if kind == "reel":
        make_reel(post, media, slot)
        post["media"] = f"posts/{today()}/media/{slot}.mp4"
        make_thumb(media / f"{slot}_cover.jpg", dd / f"{slot}_thumb.jpg")
    else:
        art = media / f"{slot}_art.png"
        post["ai_art"] = ai_image(post["image_prompt"], art, seed_text=post["headline"])
        make_feed_image(post, art, media / f"{slot}.jpg")
        art.unlink(missing_ok=True)
        post["media"] = f"posts/{today()}/media/{slot}.jpg"
        make_thumb(media / f"{slot}.jpg", dd / f"{slot}_thumb.jpg")
    post["thumb"] = f"posts/{today()}/{slot}_thumb.jpg"
    save_post(post)
    log(f"Created {kind} {slot}: {post['headline']}")


def cmd_generate(force=False):
    plan = CFG.get("daily_plan", ["image", "image", "image"])
    existing = {p["slot"]: p for p in day_posts()}
    todo = [(i + 1, k) for i, k in enumerate(plan)
            if (i + 1) not in existing or (force and existing[i + 1]["status"] in ("scheduled", "error"))]
    if not todo:
        log(f"Posts for {today()} already exist - nothing to do.")
        return
    kinds = [k for _, k in todo]
    has_ai = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    items = sample_content(kinds) if (DRY and not has_ai) else write_content(kinds)
    for (slot, kind), post in zip(todo, items):
        build_post(post, slot, kind)


def media_url(rel):
    base = os.getenv("MEDIA_BASE_URL")
    if not base:
        base = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/media/"
    return base.rstrip("/") + "/" + rel


def wait_url(url):
    for _ in range(18):
        try:
            if requests.head(url, timeout=20, allow_redirects=True).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(10)


def cmd_publish():
    pending = [p for p in day_posts() if p["status"] in ("scheduled", "error")]
    if not pending:
        log("Nothing left to publish today.")
        return
    post = pending[0]
    caption = post["caption"].strip() + "\n.\n.\n" + " ".join("#" + h.lstrip("#") for h in post["hashtags"])
    url = media_url(post["media"])
    try:
        if DRY:
            log(f"[DRY RUN] would publish {post['kind']} {url}")
            media_id = f"dry-{today()}-{post['slot']}"
        else:
            ig = os.environ["IG_USER_ID"]
            wait_url(url)
            params = {"caption": caption, "is_ai_generated": "true"}
            if post["kind"] == "reel":
                params.update(media_type="REELS", video_url=url, share_to_feed="true",
                              cover_url=media_url(post["media"].replace(".mp4", "_cover.jpg")))
            else:
                params.update(image_url=url)
            container = graph("POST", f"{ig}/media", **params)["id"]
            for _ in range(80):  # videos can take a few minutes
                st = graph("GET", container, fields="status_code,status").get("status_code")
                if st == "FINISHED":
                    break
                if st == "ERROR":
                    raise RuntimeError(f"Instagram could not process the {post['kind']}")
                time.sleep(8)
            media_id = graph("POST", f"{ig}/media_publish", creation_id=container)["id"]
        post.update(status="published", media_id=media_id, published_at=now().isoformat(timespec="minutes"))
        post.pop("error", None)
        save_post(post)
        posted = load_posted()
        posted.append({k: post.get(k) for k in ("media_id", "published_at", "topic", "headline", "kind", "thumb")})
        save_posted(posted)
        log(f"Published {post['kind']} {post['slot']}: {media_id}")
    except Exception as e:
        post.update(status="error", error=str(e)[:400])
        save_post(post)
        raise
    finally:
        cmd_dashboard()


def cmd_skip(slot):
    for p in day_posts():
        if p["slot"] == int(slot) and p["status"] != "published":
            p["status"] = "skipped"
            save_post(p)
            log(f"Skipped post {slot}")
    cmd_dashboard()


# ================================================================ monitor / report / dashboard
POST_METRICS = ["reach", "likes", "comments", "saved", "shares", "views"]


def media_insights(media_id):
    out = {}
    for m in POST_METRICS:  # one by one: some metrics don't exist for every media type
        try:
            v = graph("GET", f"{media_id}/insights", metric=m)["data"][0]
            out[m] = v["values"][0]["value"] if v.get("values") else v.get("total_value", {}).get("value")
        except Exception:  # noqa: BLE001
            pass
    return out


def cmd_monitor():
    if DRY:
        log("[DRY RUN] skipping monitor")
        cmd_dashboard()
        return
    DATA.mkdir(exist_ok=True)
    ig = os.environ["IG_USER_ID"]
    stamp = now().isoformat(timespec="minutes")
    acct = graph("GET", ig, fields="followers_count,follows_count,media_count")
    af = DATA / "account_stats.csv"
    new = not af.exists()
    with af.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["time", "followers", "following", "posts"])
        w.writerow([stamp, acct.get("followers_count"), acct.get("follows_count"), acct.get("media_count")])
    log(f"Followers: {acct.get('followers_count')}")
    pf = DATA / "post_stats.csv"
    new = not pf.exists()
    cutoff = now() - dt.timedelta(days=30)
    with pf.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["time", "media_id", "headline"] + POST_METRICS)
        for p in load_posted():
            if p["media_id"].startswith("dry-") or dt.datetime.fromisoformat(p["published_at"]) < cutoff:
                continue
            s = media_insights(p["media_id"])
            w.writerow([stamp, p["media_id"], p["headline"]] + [s.get(m, "") for m in POST_METRICS])
    cmd_dashboard()


def cmd_report():
    lines = [f"# Instagram report - {today()}", ""]
    af = DATA / "account_stats.csv"
    if af.exists():
        rows = list(csv.DictReader(af.open()))
        if rows:
            first, last = rows[max(0, len(rows) - 8)], rows[-1]
            lines += [f"**Followers:** {last['followers']} (change over last 7 checks: "
                      f"{num(last['followers']) - num(first['followers']):+d})", ""]
    lines += ["## What's working", performance_summary()]
    (DATA / "report.md").write_text("\n".join(lines))
    log("\n".join(lines))


def cmd_dashboard():
    DATA.mkdir(exist_ok=True)
    stats = latest_stats()
    account = []
    af = DATA / "account_stats.csv"
    if af.exists():
        account = [{"time": r["time"], "followers": num(r["followers"])} for r in csv.DictReader(af.open())]
    posts = []
    for p in load_posted()[-120:]:
        s = stats.get(p.get("media_id", ""), {})
        posts.append({**p, **{m: num(s.get(m)) for m in POST_METRICS}, "score": score(s) if s else 0})
    queue = []
    for p in day_posts():
        queue.append({k: p.get(k) for k in ("slot", "kind", "time", "status", "topic", "headline",
                                              "thumb", "media", "error", "media_id")})
    days_left = None
    if CFG.get("countdown_to"):
        days_left = (dt.date.fromisoformat(str(CFG["countdown_to"])) - now().date()).days
    out = {"updated": now().isoformat(timespec="minutes"), "handle": CFG["instagram_handle"],
           "brand": CFG["brand_name"], "daily_plan": CFG.get("daily_plan"),
           "countdown": {"label": CFG.get("countdown_label"), "days_left": days_left},
           "account": account[-400:], "posts": posts, "queue": queue, "today": today()}
    (DATA / "dashboard.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    cmds = {"generate": lambda: cmd_generate(False), "regenerate": lambda: cmd_generate(True),
            "publish": cmd_publish, "monitor": cmd_monitor, "report": cmd_report,
            "dashboard": cmd_dashboard,
            "skip": lambda: cmd_skip(sys.argv[2] if len(sys.argv) > 2 else 0)}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]]()
    if sys.argv[1] in ("generate", "regenerate"):
        cmd_dashboard()
