# Next steps -- final-round prep

Read this first in a new session. History and measurements: `docs/build-log.md`;
the reasoning behind each choice: `docs/ARCHITECTURE_DECISIONS.md`.

## Where things stand (2026-11-12)

- **Account** `IBIEQZU-HF18990` (locator NI08942, AWS ap-northeast-1, Standard).
  Connection `4ASHWINRAVI0045` in `~/.snowflake/connections.toml`, key pair from
  `scripts/setup_data_account.py`; `.env` points at it. The AISH45 connection is
  separate and is never used for this project.
- **Capabilities** (`conf/generated/capabilities.4ASHWINRAVI0045.yml`): AI_COMPLETE
  (20 models), agents, Cortex Search, masking / RAP / DMFs available. External
  access integration still blocked.
- **Data**: dev dataset regenerated with the retail scale (decisions doc,
  section 7), loaded in full, 10 signals / 10 cases, `verify` exit 0.
- **Agent**: `RISK_COPILOT_AGENT.STR.RC_STR_AGENT` in the same account, service
  user `RC_AGENT_SVC` (key `~/.snowflake/keys/rc_agent_svc.p8`). The AI STR
  pipeline runs end to end to READY_FOR_ATTESTATION.
- **App**: SiS `RISK_COPILOT_APP_DEV` redeployed with the live simulator and the
  shared conversation. Public service user `RC_PUBLIC_APP_USER_DEV` re-created;
  its secrets file points at the new account.
- **AI STR in Snowflake** (Nov 2026): `GENERATE_AI_STR` runs the agent chain
  (Cortex Agent first, its tools as fallback, Cortex Search regulation
  excerpts) and returns the PDF; button 4 works on every hosted link. Spend cap
  5 min per case, 30 runs a day.
- **Gates**: 647 passed, 3 skipped; ruff, mypy, SQL lint clean.

## Needs you

1. **Slack tokens** -- fill `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`,
   `SLACK_SIGNING_SECRET` in `bridge/.env` (placeholders now), install
   `bridge/requirements.txt`, run `python bridge/slack_bot.py`, then try
   `str CASE-4399c4c86db5c6ea` and a plain question; the question should appear
   on the Ask page's *Shared conversation*.
2. **Community Cloud** -- paste BOTH files, one after the other, into the app's
   Secrets: `~/.snowflake/keys/rc_public_app_user_dev.streamlit-secrets.toml`
   (`[snowflake]`) and `rc_public_officer_user_dev.streamlit-secrets.toml`
   (`[snowflake_officer]` + `[demo_auth]`, the officer's sign-in password --
   read it from that file; it is no longer shown on the sign-in page). Without
   the second file the app works but the officer cannot sign.
3. **Click once in SiS** -- one sign-off as the Principal Officer, then reset.
   Not yet clicked inside SiS (the browser tool cannot tick a form checkbox).
4. **Push** -- the hosted links pull `feature/ashwinFinals` from GitHub; nothing
   reaches them until it is pushed (then reboot both apps).
5. **Rotate the public key** -- `riskcopilot -p dev app public-access --rotate`
   (an earlier SiS deploy uploaded it to the app stage; since removed), then
   re-paste the public secrets file into both hosted apps.

## Open work (in priority order)

| # | Task | Notes |
|---|---|---|
| 1 | Structuring false-positive control | Dev has no genuine near-miss after the retail scale; plant a small cohort of legitimate single-account deposits in the band so the control is "0 of N" again |
| 2 | Bridge data-side role | `bridge/.env` uses `RC_ADMIN_DEV`; a dedicated role with USAGE on DRAFT / SUBMIT / REVIEW and the semantic view is the least-privilege version |
| 3 | Second rule | `JAROWINKLER_SIMILARITY` sanctions screening or `INCOME_ACTIVITY_MISMATCH` (income is now realistic, so the rule can be calibrated) |
| 4 | Benford gate | chi2 202.9 after the scale (was 107.9; limit 60, report-only) |
| 5 | Ruff in `app/` and `bridge/` | line-length findings from the Slack merge; not in the gate's scope |

## Live demo runbook (dashboard only)

1. Governance > Demo controls: reset the cases.
2. Overview > Live simulator > **Arm** (2 baseline cases). Live mode turns on.
3. **Auto: 1 tick/min**, or **Tick** by hand; tiles and cases move each tick.
4. **Inject demo deposits** -- the hero's case opens.
5. Investigate the hero: Gather, Draft (planted defect), Review REJECT, Draft,
   Review APPROVE, sign as the officer.
6. End with **Release all**, then reset the cases.

## Rules to keep

- No literal object names in SQL or the app; values always bound.
- Gates before every commit: pytest, ruff, mypy, `python -m risk_copilot.pipeline.lint`.
- Never commit keys, `.env`, `bridge/.env` or `secrets.toml`.
