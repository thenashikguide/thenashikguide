# The Nashik Guide – faceless Instagram autopilot

One GitHub repository runs everything, for free, even when your computer is off:

| What | Where |
|---|---|
| Public pilgrim guide website | `https://<you>.github.io/<repo>/` (file: `index.html`) |
| Your private control dashboard | `https://<you>.github.io/<repo>/dashboard/` |
| The bot (writes, designs, posts, tracks) | runs on GitHub Actions |

## What the bot does every day (no face, no camera)

| Time (IST) | Action |
|---|---|
| 09:00 | AI writes today's 3 posts from verified facts, creates **original AI artwork**, builds 2 image posts + 1 Reel, publishes post 1 |
| 13:00 | Publishes post 2 (the Reel) |
| 19:00 | Publishes post 3 |
| 23:00 | Saves followers, reach, likes, saves, shares; updates the dashboard |

- **Reels**: 4 original pictures with slow zoom + text + a soft original drone music, ending with a "Follow" screen.
- **Learning**: each morning the AI looks at which posts scored best and makes more like them.
- **AI label**: every post is marked "AI info" on Instagram, as Meta asks.
- **Change the mix** in `config.yaml` → `daily_plan` (e.g. `["reel", "image", "reel"]`).

## Dashboard buttons
- **Post next now** – publish the next scheduled post immediately
- **Skip** – drop a post you don't like
- **Remake today's posts** – fresh text and art for anything not yet posted
- **Update stats now**
- **Pause / Resume bot**

---

## One-time setup (about 1 hour)

### 1. Instagram
Switch the account to **Creator** or **Business** and link a **Facebook Page**.

### 2. Keys you need
| Secret name | Where to get it | Cost |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Uses your **Claude Pro** plan. On your laptop: install Claude Code (`npm install -g @anthropic-ai/claude-code`), run `claude setup-token`, log in, copy the token it prints | included in Pro (counts toward your Pro usage limits) |
| *or* `ANTHROPIC_API_KEY` | only if you'd rather pay per use: console.anthropic.com → API keys | pay-as-you-go |
| `OPENAI_API_KEY` | platform.openai.com → API keys (for the artwork) | per image; ~6 images/day |
| `IG_USER_ID` + `IG_ACCESS_TOKEN` | developers.facebook.com → create a Business app → add Instagram → API setup with Instagram login → generate token with `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_insights` | free |

No OpenAI key? The bot still runs and draws simple built-in illustrations instead.

### 3. GitHub
1. Create a **public** repository (Instagram must be able to download the images) and upload **everything in this folder**, including the hidden `.github` folder and `.gitignore`.
2. **Settings → Secrets and variables → Actions**
   - Secrets: the four above.
   - Variables: `GRAPH_HOST` = `graph.instagram.com` (if you used Instagram login).
3. **Settings → Pages** → Source: *Deploy from a branch* → Branch `main`, folder `/ (root)` → Save.
4. **Actions** tab → enable workflows → *Instagram Autopilot* → **Run workflow** (task `post`) to test.

### 4. Connect the dashboard buttons
1. GitHub → your profile → **Settings → Developer settings → Fine-grained tokens → Generate**.
2. Repository access: **only this repository**. Permissions: **Actions: Read and write**.
3. Open your dashboard → **Settings** → paste the repository name and the token → Save.
   (The token stays only in your browser. Anyone can view the dashboard's numbers, but only you can press the buttons.)

---

## Keep in mind
- **Facts**: update `facts.md` when officials announce new dates. The AI only uses dates from it.
- **Token expiry**: Instagram tokens last 60 days. Refresh and update the `IG_ACCESS_TOKEN` secret every ~50 days.
- **Media storage**: full-size images/videos sit on a `media` branch that keeps only the last 7 days.
- **No fake growth**: the bot never auto-follows, auto-likes or auto-DMs.
- **Test offline**: `pip install -r requirements.txt`, install ffmpeg, then `DRY_RUN=1 python bot.py generate`.
