"""Minimal Risk Copilot Slack Bot -- responds HI to every @mention.

Usage:
    pip install slack_bolt python-dotenv
    python slack_bot_simple.py
"""

import os

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)


@app.event("app_mention")
def handle_mention(event, say):
    say(text="HI", thread_ts=event.get("ts"))


if __name__ == "__main__":
    print("Bot is running. @mention it in Slack to get HI.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
