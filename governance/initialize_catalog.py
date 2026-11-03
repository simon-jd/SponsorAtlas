"""Initialize this isolated local catalog and local viewer without logging secrets."""

import json
import os
import secrets

from governance.catalog import RUNTIME, register
from governance.local_auth import token
from governance.viewer_auth import ADMIN, Client, Directory, ensure_viewer


def initialize():
    credential = token()
    api = Client(token=credential)
    try:
        api.preflight()
        with Directory(RUNTIME, private=False) as directory:
            if not directory.exists("admin-credential.json"):
                password = secrets.token_urlsafe(32) + "aA1!"
                # Preserve the pending value before the supported API mutation.
                with Directory(RUNTIME / "admin-initialize-pending", create=True) as pending:
                    pending.write("candidate.json", json.dumps({
                        "username": ADMIN, "password": password}).encode())
                    api.change_password(password, username=ADMIN)
                    directory.write("admin-credential.json", pending.read("candidate.json"))
        ensure_viewer(api, RUNTIME)
    finally:
        api.close()
    os.environ["SA_OM_TOKEN"] = credential
    register()


if __name__ == "__main__":
    initialize()
