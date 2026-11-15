# Slack Bot Setup Guide -- Risk Copilot STR Agent + "Ask the Data"

Connect Risk Copilot to Slack so analysts can generate AI-drafted, cross-reviewed STRs (with a PDF) and ask data questions from any Slack channel.

| In Slack | What happens |
|---|---|
| `@RiskCopilot pdf CASE-63708232dec1c0d6` (or "Give pdf report for CASE-...") | The case's current STR PDF in seconds; generated first if there is none |
| `@RiskCopilot str CASE-4399c4c86db5c6ea` | Fresh AI STR run across both Snowflake accounts (~2 min); live progress, summary, and the STR PDF in the thread |
| reply inside that thread | Follow-up question to the STR agent (`RC_STR_AGENT`), grounded in that case's evidence package |
| `@RiskCopilot why is CASE-... suspicious?` | The STR agent explains a case from its evidence |
| `@RiskCopilot agent <question>` | Ask the STR agent directly |
| `@RiskCopilot <question>` | Cortex Analyst over `SV_RISK_COPILOT_DEV` (data account) |
| `@RiskCopilot help` | Command list |

> **Pulled this repo and want the STR agent in your own Slack?** Jump to
> [Teammate setup](#teammate-setup-your-own-slack-shared-str-agent).

---

## Prerequisites

- Python 3.11-3.13 (on 3.15, `cffi` and `pyyaml` have no Windows wheels and fail to build without a C compiler)
- A Slack workspace (free tier is fine)
- (Optional) Snowflake credentials for live mode. Without them, the bot runs in demo mode with realistic canned responses.

---

## Step 1: Create the Slack App

1. Go to **https://api.slack.com/apps**
2. Click **Create New App** > **From a manifest**
3. Select your workspace
4. Paste this JSON manifest:

```json
{
  "display_information": {
    "name": "RiskCopilot",
    "description": "AML Risk & Compliance Copilot",
    "background_color": "#1e40af"
  },
  "features": {
    "bot_user": {
      "display_name": "RiskCopilot",
      "always_online": true
    }
  },
  "oauth_config": {
    "scopes": {
      "bot": [
        "app_mentions:read",
        "chat:write",
        "channels:history",
        "channels:read",
        "files:write",
        "im:history"
      ]
    }
  },
  "settings": {
    "event_subscriptions": {
      "bot_events": ["app_mention", "message.im"]
    },
    "org_deploy_enabled": false,
    "socket_mode_enabled": true,
    "token_rotation_enabled": false
  }
}
```

5. Click **Next** > **Create**

> `files:write` lets the bot attach the STR PDF; `im:history` + `message.im` let it answer DMs.
> Changing scopes on an existing app needs **OAuth & Permissions > Reinstall to Workspace**.

---

## Step 2: Collect the 3 Tokens

You need 3 values from the Slack app settings. Here's where to find each one:

### Token 1: Signing Secret

- Go to **Basic Information** (left sidebar)
- Under **App Credentials**, find **Signing Secret**
- Click **Show** and copy it

```
Where to paste:  .env file → SLACK_SIGNING_SECRET=<paste here>
Looks like:      d3a94d62f4a7160b7507ae73332f2ecb
```

### Token 2: App-Level Token (for Socket Mode)

- Stay on **Basic Information**
- Scroll down to **App-Level Tokens**
- Click **Generate Token and Scopes**
- Token Name: `socket-token`
- Click **Add Scope** > select `connections:write`
- Click **Generate**
- Copy the token

```
Where to paste:  .env file → SLACK_APP_TOKEN=<paste here>
Starts with:     xapp-1-...
```

### Token 3: Bot User OAuth Token

- Go to **OAuth & Permissions** (left sidebar)
- If you see **Install to Workspace**, click it and approve
- Copy the **Bot User OAuth Token**

```
Where to paste:  .env file → SLACK_BOT_TOKEN=<paste here>
Starts with:     xoxb-...
```

---

## Step 3: Create the .env File

```bash
cd RiskRemoved_Snowflake/bridge
cp .env.example .env
```

Open `.env` and fill in your 3 Slack tokens:

```ini
# --- REQUIRED: Slack tokens ---
SLACK_BOT_TOKEN=xoxb-YOUR-BOT-TOKEN-HERE
SLACK_APP_TOKEN=xapp-YOUR-APP-TOKEN-HERE
SLACK_SIGNING_SECRET=your-signing-secret-here

# --- OPTIONAL: Snowflake (for live mode) ---
# SNOWFLAKE_ACCOUNT=hqrwrxk-uz26728
# SNOWFLAKE_USER=RAGHAV2310
# SNOWFLAKE_ROLE=RC_SERVICE_ROLE
# SNOWFLAKE_WAREHOUSE=RC_DEMO_WH
# SNOWFLAKE_PRIVATE_KEY_PATH=C:\path\to\rsa_key.p8

# --- Mode ---
DEMO_MODE=true    # Set to "false" when Snowflake credentials are ready
```

> **Security:** The `.env` file is gitignored. Never commit tokens to the repository.

---

## Step 4: Install Dependencies and Start

```bash
cd RiskRemoved_Snowflake/bridge
pip install -r requirements.txt
python slack_bot.py
```

You should see:

```
2026-10-03 10:00:00 INFO Starting Risk Copilot Slack bot (DEMO mode)...
2026-10-03 10:00:00 INFO Semantic view: RISK_COPILOT.CURATED.SV_RISK_COPILOT
```

The bot is now connected to Slack via Socket Mode. No ngrok or public URL needed -- it connects outbound from your machine.

---

## Step 5: Invite the Bot to a Channel

1. In Slack, create a channel called `#compliance-investigation` (or use any existing channel)
2. In the channel, type: `/invite @RiskCopilot`
3. The bot is now listening in that channel

---

## Step 6: Test It

Type in the channel:

```
@RiskCopilot Which branch has the most cash deposits?
```

The bot will respond with:
- The answer text
- A data table
- The SQL that Cortex Analyst generated
- A footer showing latency and mode (demo/live)

### Example Questions to Try

| Question | What It Demonstrates |
|----------|---------------------|
| `@RiskCopilot Which branch has the most cash deposits?` | Branch-level aggregation |
| `@RiskCopilot How many cases are there by status?` | Case pipeline status |
| `@RiskCopilot Show the top 5 customers by cash deposits` | Customer-level ranking |
| `@RiskCopilot How many signals have fired?` | Signal detection stats |
| `@RiskCopilot What is the status of the latest STR?` | STR generation status |

---

## Switching to Live Mode

When Snowflake credentials are available:

1. Edit `.env`:
   ```ini
   DEMO_MODE=false
   SNOWFLAKE_ACCOUNT=hqrwrxk-uz26728
   SNOWFLAKE_USER=RAGHAV2310
   SNOWFLAKE_PRIVATE_KEY_PATH=C:\path\to\rsa_key.p8
   SNOWFLAKE_ROLE=RC_SERVICE_ROLE
   SNOWFLAKE_WAREHOUSE=RC_DEMO_WH
   ```

2. Restart the bot:
   ```bash
   python slack_bot.py
   ```

3. You should see `Starting Risk Copilot Slack bot (LIVE mode)...`

The bot now calls the real Cortex Analyst API against `SV_RISK_COPILOT`, runs the generated SQL, and returns live data.

---

## Teammate setup (your own Slack, shared STR agent)

The STR agent `RC_STR_AGENT` runs in the **agent account** `HQRWRXK-UZ26728` (Raghav). The data,
Cortex Analyst and the `*_DEV` procedures (including `SUBMIT_AI_DRAFT_DEV`, already deployed) are in
the **data account** `HTQQLHX-GI22223` (Ashwin). Everyone runs their own bridge with their own
Slack app; nobody shares a private key.

| What | Where it comes from | In git? |
|---|---|---|
| Code (bot, pipeline, agent tools) | this repo | yes |
| Your data-account key (`SNOWFLAKE_*`) | your own key pair for your user | no (`bridge/keys/`) |
| Your agent-account login (`AGENT_*`) | `agent_account/new_user_key.py` + the agent-account admin | no |
| Slack tokens (`SLACK_*`) | your own Slack app | no (`bridge/.env`) |

1. **Install** (from `RiskRemoved_Snowflake/`):
   ```powershell
   python -m venv bridge\.venv
   bridge\.venv\Scripts\python -m pip install -r bridge\requirements.txt
   ```
2. **Get an agent-account login.** Generate your own key pair and the SQL to register it:
   ```powershell
   bridge\.venv\Scripts\python agent_account\new_user_key.py ASHWIN
   ```
   Send the printed SQL block (it contains only the *public* key) to the agent-account admin, who
   runs it as ACCOUNTADMIN. This creates `RC_AGENT_SVC_ASHWIN` with role `RC_AGENT_ROLE`.
3. **Create your Slack app** with the manifest in [Step 1](#step-1-create-the-slack-app) and collect
   the three tokens ([Step 2](#step-2-collect-the-3-tokens)).
4. **Fill `bridge/.env`** (copy `.env.example` if you have none). Set your Slack tokens, your
   data-account `SNOWFLAKE_*` values with `DEMO_MODE=false`, and the two lines
   `new_user_key.py` printed:
   ```ini
   AGENT_SNOWFLAKE_USER=RC_AGENT_SVC_ASHWIN
   AGENT_PRIVATE_KEY_PATH=keys/rc_agent_svc_ashwin.p8
   ```
   The other `AGENT_*` values default to the shared agent account.
5. **Check both connections** before starting Slack:
   ```powershell
   $env:PYTHONPATH = "src"
   bridge\.venv\Scripts\python -m risk_copilot.agentic.str_pipeline CASE-ca1079bca1c29184 --out str.pdf
   ```
   Expect `[done] READY_FOR_ATTESTATION` (or a `REJECTED_BY_*` status with defects) and `str.pdf`.
6. **Run the bot**: `cd bridge; .\.venv\Scripts\python.exe slack_bot.py`, invite it
   (`/invite @RiskCopilot`), then `@RiskCopilot pdf CASE-63708232dec1c0d6`.
7. The Streamlit case page's **4 · AI STR + PDF** button uses the same `bridge/.env` and keys
   when the app runs locally (`riskcopilot app run`).

> **One Slack app = one running bot.** Slack spreads a Socket Mode app's events across all open
> connections, so two machines running the *same* app would split messages, and a thread
> follow-up could land on the machine that doesn't hold that thread's context. Separate Slack
> apps per person avoid this.
>
> **Agent runs use the agent account's credits** (about 2 minutes of an X-Small warehouse plus
> two LLM calls per STR). Every run is logged in `RISK_COPILOT_AGENT.STR.AGENT_RUN_LOG` with the
> requesting Slack user.

---

## Single-account setup with an existing Slack app (walkthrough)

The steps actually followed to bring the bot up on a new machine, using an already-created
Slack app and one Snowflake account that hosts both the data and the STR agent. Each step
names the error you get if it is skipped.

**You receive (over a private channel, never in git or chat):** the three Slack values, one
Snowflake user name, its `.p8` private key (and passphrase, if encrypted), and the account
identifier (`ORGNAME-ACCOUNTNAME`).

1. **Place the secrets** -- both paths are gitignored:
   ```text
   bridge/.env              from bridge/.env.example (NOT the repo-root .env.example)
   bridge/keys/<user>.p8
   ```
   The repo-root `.env` holds `RC_*` settings for the CLI/app; the bot never reads it.
   Using the root template for `bridge/.env` crashes with `KeyError: 'SLACK_BOT_TOKEN'`.

2. **Fill `bridge/.env`.** With one account, point both blocks at the same login:
   ```ini
   SNOWFLAKE_ACCOUNT=<ORGNAME-ACCOUNTNAME>
   SNOWFLAKE_USER=<USER>
   SNOWFLAKE_ROLE=RC_SERVICE_ROLE_DEV
   SNOWFLAKE_WAREHOUSE=RC_DEV_WH_DEV
   SNOWFLAKE_PRIVATE_KEY_PATH=keys/<user>.p8
   AGENT_SNOWFLAKE_ACCOUNT=<ORGNAME-ACCOUNTNAME>
   AGENT_SNOWFLAKE_USER=<USER>
   AGENT_SNOWFLAKE_ROLE=RC_AGENT_ROLE
   AGENT_SNOWFLAKE_WAREHOUSE=RC_AGENT_WH
   AGENT_PRIVATE_KEY_PATH=keys/<user>.p8
   DEMO_MODE=false
   ```
   The account identifier for a user is in `conf/generated/capabilities.<CONNECTION>.yml`
   (`organization` + `account_name`). Verify role and warehouse names as that user --
   dev objects carry the `_DEV` suffix:
   ```sql
   SELECT CURRENT_AVAILABLE_ROLES();
   SHOW WAREHOUSES;
   ```

3. **Install and start** from inside `bridge/` (relative key paths resolve from there):
   ```powershell
   cd bridge
   py -3.12 -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   .\.venv\Scripts\python.exe slack_bot.py
   ```
   Expect `Starting Risk Copilot Slack bot (LIVE mode)...` and `Bolt app is running!`.

4. **Configure the Slack app** (api.slack.com/apps > your app). Scopes alone are not enough --
   Slack only delivers events the app subscribes to:
   - **OAuth & Permissions > Bot Token Scopes:** `app_mentions:read`, `chat:write`,
     `channels:history`, `channels:read`, `files:write`, `im:history`.
   - **Event Subscriptions > Subscribe to bot events:** `app_mention` and `message.im`, then
     **Save Changes**. `message.channels` is not used by the bot; it can be removed.
   - **App Home > Show Tabs:** enable the **Messages Tab** and allow users to send messages
     (needed for DMs).
   - **Reinstall to Workspace** after any scope change. If the `xoxb-` token changes, update
     `bridge/.env` and restart the bot.

5. **Check the token's real scopes** (prints scope names only, never the token):
   ```powershell
   $t = ((Get-Content bridge\.env | ? { $_ -match '^SLACK_BOT_TOKEN=' }) -split '=',2)[1].Trim()
   (Invoke-WebRequest -UseBasicParsing -Method Post https://slack.com/api/auth.test `
     -Headers @{Authorization="Bearer $t"}).Headers['x-oauth-scopes']
   ```

6. **Invite and test** in a channel: `/invite @RiskCopilot`, then `@RiskCopilot help` (pick the
   bot from the `@` autocomplete). The bot log shows `Question from U...` for each real
   question. Then try `@RiskCopilot what are the pending cases?` and
   `@RiskCopilot pdf CASE-...`.

> **Only one machine per Slack app.** If the person who handed you the tokens also runs the
> bot, Slack splits events between the two processes.

---

## Architecture

The data and the agent live in **two different Snowflake accounts** (different organisations and
regions, both trial), which cannot share data or call each other. The bridge holds one key-pair
connection to each and moves a hashed evidence package between them.

```
Slack (@RiskCopilot str CASE-...)
  |  Socket Mode
  v
slack_bot.py --> risk_copilot.agentic.str_pipeline.generate_ai_str()
  |
  |  DATA ACCOUNT  HTQQLHX-GI22223 / RISK_COPILOT_DEV   (SNOWFLAKE_* keys)
  |   1. current draft, or DRAFT_STR_DEV (deterministic, logged + hashed evidence)
  |   2. artifact, EVIDENCE_QUERY_LOG pointers, signals, clauses
  |   3. 2 Cortex Analyst questions for context
  |
  |  AGENT ACCOUNT HQRWRXK-UZ26728 / RISK_COPILOT_AGENT.STR   (AGENT_* keys)
  |   4. EVIDENCE_PACKAGE (SHA-256 payload hash)
  |   5. RC_STR_AGENT  (agent:run, key-pair JWT)
  |        draft_str_narrative  claude-sonnet-4-6   (maker)
  |        review_str_draft     openai-gpt-5.1      (checker; + every amount/count/date
  |                                                  traced to the evidence; fails closed)
  |        one redraft on REJECT
  |
  |  DATA ACCOUNT
  |   6. SUBMIT_AI_DRAFT_DEV  -> FINDING v(n+1) + ARTIFACT (figures unchanged)  [only if APPROVE]
  |   7. REVIEW_STR_DEV       -> deterministic recompute / citation / clause / hash checks
  |
  |  AGENT ACCOUNT
  |   8. RENDER_STR_PDF -> @STR_REPORTS -> PDF bytes
  v
Slack thread: progress, summary, defects, STR PDF
```

Attestation is unchanged: only the Principal Officer, in the Risk Copilot app's sign-off page
(`ATTEST_STR`, caller's rights), can file. The agent account's objects are in
`agent_account/` (`sql/01_bootstrap.sql` ... `04_agent.sql`, tools in `python/str_tools.py`,
deployed with `agent_account/deploy.py`).

The "Ask the data" path is unchanged:

```
Slack (Priya types in #compliance-investigation)
  │
  │  Socket Mode (outbound connection, no public URL)
  ▼
slack_bot.py (on your machine)
  │
  │  Calls analyst_client.ask(question)
  ▼
analyst_client.py
  │
  ├── DEMO_MODE=true  → returns canned responses (no Snowflake needed)
  │
  └── DEMO_MODE=false → POST /api/v2/cortex/analyst/message
                         │
                         ▼
                      Snowflake Cortex Analyst
                         │
                         ▼
                      SV_RISK_COPILOT (semantic view)
                         │
                         ▼
                      SQL generated → executed → results returned
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `SLACK_BOT_TOKEN not set` | Check `.env` file exists and has the xoxb- token |
| `SLACK_APP_TOKEN not set` | Check `.env` has the xapp- token (App-Level Token, not Bot Token) |
| Bot doesn't respond to @mention | Make sure the bot is invited to the channel (`/invite @RiskCopilot`) |
| `not_in_channel` error | The bot needs to be in the channel. Use `/invite @RiskCopilot` |
| Bot responds to everything | It only responds to @mentions in channels, and all messages in DMs |
| `invalid_auth` error | Regenerate tokens in Slack app settings and update `.env` |
| Snowflake connection fails | Check account, user, and key path in `.env`. Try `DEMO_MODE=true` first. |
| `Could not upload the PDF` | Add `files:write` to the Slack app and reinstall it. The PDF is also on `@RISK_COPILOT_AGENT.STR.STR_REPORTS`. |
| `REJECTED_BY_AGENT_REVIEW` | Working as intended: the agent's checker found a defect (listed in the thread); nothing was written back. Re-run to redraft. |
| `agent:run returned 401/403` | `AGENT_PRIVATE_KEY_PATH` must match the public key set on `RC_AGENT_SVC` in the agent account. |
| DMs get no answer | Add `im:history` and the `message.im` event, then reinstall the app. |
| `KeyError: 'SLACK_BOT_TOKEN'` at start | `bridge/.env` was created from the repo-root template. Recreate it from `bridge/.env.example`. |
| `ModuleNotFoundError: No module named 'yaml'` | Reinstall `requirements.txt` (now includes `pyyaml` and `pydantic`, used by `risk_copilot.config`). |
| `Failed to build 'cffi'` / `pyyaml` needs Visual C++ | Python 3.15 is too new for prebuilt wheels. Recreate the venv with `py -3.12`. |
| `No module named 'snowflake.connector.vendored'` | Fixed in `analyst_client.py` (connector 5.x dropped its vendored `requests`); pull the latest code. |
| `FileNotFoundError: keys/...p8` | Relative key paths resolve from the working directory for Cortex Analyst; start the bot from inside `bridge/`. |
| Analyst generates SQL, then `002043 Object does not exist` | Usually no usable warehouse: `SNOWFLAKE_WAREHOUSE` names one that does not exist (`RC_DEV_WH` vs `RC_DEV_WH_DEV`). Check `SHOW WAREHOUSES` as the bot's role. |
| Log shows only `app_home_opened`, never `Question from` | The app does not subscribe to `app_mention` (Event Subscriptions). `message.channels` alone is ignored by the bot. |
| Home tab says "This is still a work in progress" | Fixed: the bot now publishes the help text on `app_home_opened`. Restart it. |

---

## File Structure

```
bridge/
  .env.example       # Template -- copy to .env and fill in tokens
  .env               # Your actual tokens (gitignored, never committed)
  .gitignore         # Ensures .env and keys/ are not committed
  keys/              # rc_rsa_key.p8 (data account), rc_agent_svc.p8 (agent account)
  requirements.txt   # Python dependencies
  slack_bot.py       # The Slack bot (entry point)
  analyst_client.py  # Cortex Analyst client (live + demo mode)

../src/risk_copilot/agentic/
  connections.py     # data_conn() / agent_conn() from bridge/.env
  agent_client.py    # Cortex Agent REST client (agent:run SSE, threads)
  str_pipeline.py    # generate_ai_str(): the cross-account STR run (also a CLI)
```

Run one STR without Slack:

```bash
cd RiskRemoved_Snowflake
PYTHONPATH=src bridge/.venv/Scripts/python -m risk_copilot.agentic.str_pipeline CASE-... --out str.pdf
```
