# Self-Operating-System 🤖🖥️

A **full-PC autonomous AI agent for Windows**. Type a task in plain English and the
agent sees your screen, controls the mouse & keyboard, navigates websites and apps,
recovers from mistakes, and asks you for input when needed.

> "Find the cheapest iPhone 17 Pro online and add it to the cart, then ask me before paying."

It works on **both the desktop and the web** and decides which to use on its own.

---

## ✨ Features

- **Vision + DOM hybrid** — uses Microsoft **OmniParser** (vision) for native Windows apps,
  and **Playwright** (real browser DOM) for websites — picking the right one automatically.
- **Adaptive agent loop** — perceive → decide → act → verify → adapt (handles popups,
  profile pickers, loading screens on its own).
- **Set-of-Mark grounding** — numbered on-screen elements, so clicks land precisely.
- **Human-in-the-loop** — pauses and asks you for OTPs, passwords, payment confirmation
  (never enters secrets itself).
- **Multi-provider LLMs** — OpenAI, Google Gemini, or local Ollama — set a different
  provider/model per agent from the in-app **Settings**.
- **Skills library** — reliable keyboard-first shortcuts (open apps via Windows Search,
  navigate URLs, etc.).
- Safety denylist for dangerous commands (cmd, powershell, regedit, …).

---

## 🧱 Architecture

(assets/architecture.png)

---

## 🚀 Setup (one time)

**Requirements:** Windows 10/11 and Google Chrome installed. **You do NOT need Python**
— the setup downloads its own. (Git only needed if you clone instead of downloading a ZIP.)

1. **Get this repo** — download the ZIP from GitHub (Code → Download ZIP) and extract it,
   or clone:
   ```
   git clone https://github.com/<your-username>/self-operating-system.git
   ```
2. **Double-click `setup.bat`** (or run it in a terminal). It automatically:
   - downloads a **self-contained Python 3.10** into `python310\` (so it works the same on
     any machine, regardless of what Python you have installed — even 3.12)
   - creates `env_operating\` (app) and `env_omniparser\` (perception) virtual environments
   - installs all dependencies (pinned to known-good versions)
   - downloads the ~1 GB OmniParser model weights into `OmniParser\weights\`

   The OmniParser perception code is **already bundled** in this repo (in `OmniParser\`),
   so only the model weights are downloaded. Nothing else to clone.

   > It's a complete sandbox — everything lives inside the project folder and uses the
   > bundled Python 3.10, so a different system Python (e.g. 3.12) won't cause errors.

   **Setup is resumable.** It runs in 4 checkpointed steps (Python → app env →
   OmniParser env → weights). If a step fails (e.g. network drop), fix the issue and
   **run `setup.bat` again** — it skips finished steps and resumes from the failed one.
   To redo just one part, run the matching script in `setup_steps\` directly
   (e.g. `setup_steps\3_env_omniparser.bat`). To start fully fresh, delete the
   `.setup_state\` folder (and the `env_*` folders you want rebuilt).

   Takes ~5–15 minutes depending on your connection.

3. **Add your API keys** — start the app (below), click **⚙ Settings**, paste your
   OpenAI and/or Gemini key, and choose a provider/model per agent.

---

## ▶️ Run

**Double-click `run.bat`.** It:
1. starts the OmniParser perception server **hidden** (waits until its models are loaded)
2. starts the backend **hidden** (waits until it's ready)
3. opens the **desktop UI** — the only window you see

The two servers run in the background (no terminal windows). Their output goes to
`logs\omni_server.log` and `logs\backend.log` if you ever need to debug.
When you close the UI, the background servers are stopped automatically.

Type a task, click **Run**, and watch it work. (The agent minimizes the UI so it can
see the screen; it restores when done or when it needs your input.)

---

## 🔑 LLM providers

Configure per agent in **⚙ Settings**:

| Provider | Get a key | Good models |
|----------|-----------|-------------|
| OpenAI | platform.openai.com | `gpt-4o` (navigator), `gpt-4.1-mini` (planner/verifier) |
| Gemini (free tier) | aistudio.google.com/apikey | `gemini-2.5-flash` |
| Ollama (local, free) | ollama.com | vision: `llama3.2-vision`; text: `llama3.1` |

> The **Navigator needs a vision model**. The Planner/Verifier can use cheaper text models.

---

## 🛠️ Folders created by setup (not in git)

- `env_operating\`, `env_omniparser\` — virtual environments (visible, so you can fix issues)
- `OmniParser\` — the perception engine + weights
- `agent_chrome_profile\` — the agent's dedicated Chrome profile
- `logs\`, `debug\` — run logs and perception artifacts
- `.env` — your keys/settings (never committed)

---

## ⚠️ Notes & limitations

- The agent uses your **real Chrome** (a separate dedicated profile) for web tasks.
  Log into sites once via that profile and it persists.
- Browser tasks on heavily bot-protected sites (e.g. some shopping checkouts) may be limited.
- This controls your real mouse/keyboard — supervise it, and keep the emergency stop handy
  (`Ctrl+Shift+Q`).

## 📄 License

MIT — see [LICENSE](LICENSE).
