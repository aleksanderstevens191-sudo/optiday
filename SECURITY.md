# Security policy

OptiDay records what is on your screen, so security bugs matter more here than in most apps.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Instead, use GitHub's private
reporting: go to the **Security** tab of this repository and click **Report a vulnerability**.

Include what you found, how to reproduce it, and what data it could expose. You should get a
reply within a few days. Please give us a reasonable chance to fix it before you share details.

Especially interesting:

- anything that sends recorded activity, screen text, email or calendar data off the Mac
  (other than to the private server or agent the user has set up and approved);
- ways for a website or another local app to read the dashboard on `127.0.0.1:8765`;
- ways to read the encrypted database without the Keychain key;
- agent tasks that run without the user clicking Approve;
- excluded apps or sites (password managers, banking, private windows) being recorded anyway.

## Supported versions

Only the latest release gets security fixes.

## What is never in this repository

Your activity data lives only on your Mac, in `~/Library/Application Support/OptiDay/`, and its
encryption key lives in your macOS Keychain. Nothing in this repository contains real user data:
the names, emails and screenshots in `docs/`, `optiday/demo.py` and `tests/` are made up.
`.gitignore` blocks databases, keys, logs and screen captures from being committed by accident.
