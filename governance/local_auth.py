"""Short-lived token for this isolated OM's project-owned signing key; never printed."""

import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.serialization import load_der_private_key

ROOT = Path(__file__).resolve().parents[1]


def token():
    key = load_der_private_key(
        (ROOT / ".tools/governance/openmetadata-2.0.6/conf/sponsoratlas-private.der").read_bytes(),
        None,
    )
    return jwt.encode(
        {
            "sub": "sponsoratlas-admin",
            "email": "sponsoratlas-admin@example.invalid",
            "isBot": False,
            "iss": "open-metadata.org",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        key,
        algorithm="RS256",
        headers={"kid": "Gb389a-9f76-gdjs-a92j-0242bk94356"},
    )
