"""Configure already verified distributions and generate isolated local secrets."""

import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".tools/governance"


def prepare():
    env = RUNTIME / ".env"
    if env.exists():
        raise FileExistsError("Refuse replacing runtime secrets")
    env.write_text(
        "SA_OM_DB_PASSWORD="
        + secrets.token_urlsafe(36)
        + "\nSA_OM_FERNET_KEY="
        + Fernet.generate_key().decode()
        + "\n"
    )
    env.chmod(0o600)
    conf = RUNTIME / "openmetadata-2.0.6/conf"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = conf / "sponsoratlas-private.der"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    (conf / "sponsoratlas-public.der").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    (
        RUNTIME / "elasticsearch-9.3.0/config/elasticsearch.yml"
    ).write_text("""cluster.name: sponsoratlas-governance
node.name: sponsoratlas-local
network.host: 0.0.0.0
http.port: 9200
discovery.type: single-node
xpack.security.enabled: false
xpack.ml.enabled: false
ingest.geoip.downloader.enabled: false
path.data: /runtime/data
path.logs: /runtime/logs
""")
    config = conf / "openmetadata.yaml"
    # Bound every rotating file appender in the isolated local server.
    text = (
        config.read_text()
        .replace("archivedFileCount: 25", "archivedFileCount: 2")
        .replace("maxFileSize: 50MB", "maxFileSize: 5MB")
    )
    config.write_text(text)


if __name__ == "__main__":
    prepare()
