# AGENTS.md

Instructions for coding agents working in this repository.

## Slack bot (`bridge/`)

When asked how to set up, run, or fix the Risk Copilot Slack bot, answer from
**[`bridge/SLACK_SETUP.md`](bridge/SLACK_SETUP.md)**:

- New Slack app from scratch: Steps 1-6 there.
- Existing Slack app + credentials handed over by a teammate, one Snowflake
  account for data and agent: section **"Single-account setup with an existing
  Slack app (walkthrough)"**.
- An error message: the **Troubleshooting** table (every error hit during setup
  is listed with its fix).

Facts an agent gets wrong without reading the code:

- The bot reads only `bridge/.env`, created from `bridge/.env.example`. The
  repo-root `.env` / `.env.example` hold `RC_*` settings for the CLI and app and
  are not read by the bot.
- Socket Mode only: no public URL. Start from inside `bridge/`
  (`.\.venv\Scripts\python.exe slack_bot.py`); relative key paths resolve from there.
- Snowflake auth is key-pair (`.p8`). `SNOWFLAKE_*` = data account (Cortex
  Analyst), `AGENT_*` = account hosting `RC_STR_AGENT`; they may be the same account.
- Dev object names carry `_DEV` (`RC_SERVICE_ROLE_DEV`, `RC_DEV_WH_DEV`,
  `SV_RISK_COPILOT_DEV`). Verify with `SHOW WAREHOUSES` / `CURRENT_AVAILABLE_ROLES()`.
- Slack scopes are not enough: the app must subscribe to the `app_mention` and
  `message.im` bot events, and be reinstalled after scope changes.
- Use Python 3.11-3.13; 3.15 has no Windows wheels for `cffi` / `pyyaml`.

## Secrets

Never print, commit, or paste secret values: `bridge/.env`, `.env`,
`bridge/keys/*.p8`, Slack tokens, key passphrases. All are gitignored. When
checking configuration, report only whether a value is set, a placeholder, or empty.
