"""opensquad login / logout / whoami"""

from __future__ import annotations

import getpass
import sys

from opensquad.cli.api_client import (
    GatewayClient,
    clear_credentials,
    handle_api_error,
    load_credentials,
    resolve_gateway_url,
)


def run_login(args) -> None:
    gateway = resolve_gateway_url(getattr(args, "gateway", None))
    email = getattr(args, "email", None) or input("Email: ").strip()
    if not email:
        print("[login] Email required")
        sys.exit(1)
    password = getattr(args, "password", None)
    if not password:
        password = getpass.getpass("Password: ")
    language = getattr(args, "language", None) or "zh"

    client = GatewayClient(gateway_url=gateway)
    try:
        # Registration required when no web account exists yet (Web parity).
        status = client.registration_status()
        if status.get("registration_required"):
            name = getattr(args, "name", None) or input("Name: ").strip() or email.split("@")[0]
            data = client.register(name, email, password, language=language)
        else:
            data = client.login(email, password, language=language)
    except Exception as e:
        handle_api_error(e)
        print(f"[login] Failed: {e}")
        sys.exit(1)

    user = data.get("user") or {}
    name = user.get("name") or email
    print(f"[login] OK — {name} <{user.get('email', email)}>")
    print(f"[login] Gateway: {client.gateway_url}")
    print("[login] Credentials saved to ~/.opensquad/cli_credentials.json")


def run_logout(_args) -> None:
    clear_credentials()
    print("[logout] Credentials cleared")


def run_whoami(args) -> None:
    creds = load_credentials()
    if not creds.get("token"):
        print("[whoami] Not logged in. Run: opensquad login")
        sys.exit(1)
    client = GatewayClient(gateway_url=getattr(args, "gateway", None))
    try:
        me = client.me()
    except Exception as e:
        handle_api_error(e)
        print(f"[whoami] Failed: {e}")
        sys.exit(1)
    print(f"Name:    {me.get('name')}")
    print(f"Email:   {me.get('email')}")
    print(f"ID:      {me.get('id')}")
    print(f"Status:  {me.get('status')}")
    print(f"Gateway: {client.gateway_url}")
