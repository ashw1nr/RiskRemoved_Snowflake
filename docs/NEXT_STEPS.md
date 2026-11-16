# Operator notes

How the dev deployment is wired, and how to run the live demo. History and
measurements: `docs/build-log.md`; the reasoning behind each choice:
`docs/ARCHITECTURE_DECISIONS.md`.

## Deployment

- **Account**: AWS ap-northeast-1, Standard edition. The CLI connects with the
  key-pair connection named in `.env` (created by `scripts/setup_data_account.py`).
- **Capabilities** (`conf/generated/capabilities.4ASHWINRAVI0045.yml`): AI_COMPLETE
  (20 models), Cortex Agents, Cortex Search, masking / row-access policies and
  DMFs. `riskcopilot doctor` reads this file and gates features on it.
- **Data**: retail-scaled dev dataset (decisions doc, section 7), 10 signals and
  10 cases after a full release, `riskcopilot verify` exit 0.
- **Agent**: `RISK_COPILOT_AGENT.STR.RC_STR_AGENT`, run by its own service user.
  `GENERATE_AI_STR` drives the agent chain (Cortex Agent first, its tools as the
  fallback, Cortex Search regulation excerpts) to READY_FOR_ATTESTATION and
  returns the PDF. Spend cap: 5 min per case, 30 runs a day.
- **App**: Streamlit in Snowflake (`RISK_COPILOT_APP_DEV`) and Streamlit
  Community Cloud. The hosted app runs as two key-pair service users:
  `[snowflake]` (`RC_PUBLIC_APP_USER_DEV`, no compliance role) for everyone, and
  `[snowflake_officer]` (`RC_PUBLIC_OFFICER_USER_DEV`) only while the Principal
  Officer is signed in, so `ATTEST_STR` enforces maker-checker in the database.
  Both sections, plus `[demo_auth]`, go into the hosted app's Secrets; the files
  are written by `riskcopilot -p dev app public-access [--officer]`.
- **Slack bridge**: `bridge/slack_bot.py` with tokens in `bridge/.env`
  (`bridge/SLACK_SETUP.md`). Questions from Slack and the Ask page share one
  conversation.
- **Gates**: pytest, ruff, mypy and `python -m risk_copilot.pipeline.lint`.

## Live demo runbook (dashboard only)

1. Governance > Demo controls: reset the cases.
2. Overview > Live simulator > **Arm** (2 baseline cases). Live mode turns on.
   Arm clears the whole case chain: never press it between drafting and signing.
3. **Auto: 1 tick/min**, or **Tick** by hand; tiles and cases move each tick.
4. **Inject demo deposits** -- the hero's case opens.
5. Investigate the hero: Gather, Draft (planted defect), Review REJECT, Draft,
   Review APPROVE.
6. As the analyst, press **Sign**: Snowflake refuses ("only the compliance
   officer role may attest"). Switch to the officer and sign: the STR is FILED.
7. Governance > Actions: the two `ATTEST_STR` rows, with the Snowflake user
   behind each.
8. End with **Release all**, then reset the cases.

## Roadmap

| # | Item | Approach |
|---|---|---|
| 1 | Sanctions screening rule | Exact match raises a signal; the `JAROWINKLER_SIMILARITY` band raises a review task, never a filing |
| 2 | `INCOME_ACTIVITY_MISMATCH` rule | Declared income is now retail-realistic, so the 3x threshold in `POLICY_CLAUSE` can be calibrated against it |
| 3 | Structuring near-miss cohort | Legitimate single-account deposits inside the band, so the false-positive control reports "0 of N" |
| 4 | Dedicated bridge role | USAGE on DRAFT / SUBMIT / REVIEW and the semantic view only, in place of the admin role |
| 5 | Benford gate as a hard gate | Report-only today; calibrate the limit to the retail-scaled data |

## Rules to keep

- No literal object names in SQL or the app; values always bound.
- Gates before every commit: pytest, ruff, mypy, `python -m risk_copilot.pipeline.lint`.
- Never commit keys, `.env`, `bridge/.env` or `secrets.toml`.
