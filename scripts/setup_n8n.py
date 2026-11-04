"""Start n8n + Mailpit in Docker and install the escalation workflow (one command).

    uv run python scripts/setup_n8n.py

After it finishes:
    n8n UI:        http://localhost:5678   (first visit asks you to create the owner account)
    Mailpit inbox: http://localhost:8025   (every e-mail n8n sends lands here)
    Webhook:       http://localhost:5678/webhook/doc-assistant-escalation
Put the webhook URL into .env as ESCALATION_WEBHOOK_URL.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

WORKFLOW_ID = "DocAssistEscalat"
WEBHOOK_URL = "http://localhost:5678/webhook/doc-assistant-escalation"
COMPOSE = ["docker", "compose", "--profile", "automation"]


def run(*args: str) -> str:
    print("$", " ".join(args))
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(result.stdout, result.stderr, sep="\n", file=sys.stderr)
        raise SystemExit(f"command failed: {' '.join(args)}")
    return result.stdout


def n8n(*args: str) -> str:
    return run(*COMPOSE, "exec", "-T", "n8n", "n8n", *args)


def wait_for(url: str, seconds: int = 120) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 500:
                    return
        except OSError:
            time.sleep(2)
    raise SystemExit(f"{url} did not come up within {seconds} s")


def main() -> int:
    run(*COMPOSE, "up", "-d", "n8n", "mailpit")
    wait_for("http://localhost:5678/healthz")

    n8n("import:credentials", "--input=/automations/n8n_credentials_mailpit.json")
    n8n("import:workflow", "--input=/automations/escalation_workflow.json")
    n8n("update:workflow", f"--id={WORKFLOW_ID}", "--active=true")
    run(*COMPOSE, "restart", "n8n")  # n8n picks up CLI activation on start
    wait_for("http://localhost:5678/healthz")

    # Smoke test: send one escalation like the agent would.
    payload = {
        "id": "ESC-SETUP-TEST",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "question": "Test: da li n8n prima eskalacije?",
        "reason": "setup_n8n.py smoke test",
        "contact": "setup script",
    }
    request = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # /healthz is up a few seconds before published workflows register their webhooks.
    deadline = time.time() + 60
    while True:
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                print(f"webhook answered HTTP {response.status}")
                break
        except urllib.error.HTTPError as error:
            if error.code != 404 or time.time() > deadline:
                raise
            time.sleep(2)

    print(
        "\nDone.\n"
        "  n8n:     http://localhost:5678\n"
        "  inbox:   http://localhost:8025  (the test e-mail should be there)\n"
        f"  add to .env:  ESCALATION_WEBHOOK_URL={WEBHOOK_URL}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
