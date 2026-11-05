"""Settings, read from the environment. The app refuses to start without a secret and an admin
hash (fail closed): a registry that can later start containers on GPU nodes must not come
up open because someone forgot a variable."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    catalogue_dir: Path     # folder holding library.yaml and results/ (read-only)
    hf_dir: Path            # Hugging Face cache root; holds hub/models--<org>--<name>
    data_dir: Path          # /data volume (unused until a later version: jobs, deployment log)
    secret_key: str
    admin_hash: str
    cookie_secure: bool = False   # True once served over HTTPS; LAN HTTP by default
    inbox_dir: Path = Path("/inbox")   # the only folder the app writes: uploaded result records
    ingest_token: str = ""             # bearer token for POST /api/results; empty = upload disabled
    nodes: tuple[str, ...] = ("node-a", "node-b")   # names offered in the node filter (SPARKVAULT_NODES)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        return cls(
            catalogue_dir=Path(e.get("SPARKVAULT_CATALOGUE_DIR", "/catalogue")),
            hf_dir=Path(e.get("SPARKVAULT_HF_DIR", "/hf")),
            data_dir=Path(e.get("SPARKVAULT_DATA_DIR", "/data")),
            secret_key=e.get("SPARKVAULT_SECRET_KEY", ""),
            admin_hash=e.get("SPARKVAULT_ADMIN_HASH", ""),
            cookie_secure=e.get("SPARKVAULT_COOKIE_SECURE", "0") == "1",
            inbox_dir=Path(e.get("SPARKVAULT_INBOX_DIR", "/inbox")),
            ingest_token=e.get("SPARKVAULT_INGEST_TOKEN", ""),
            nodes=tuple(n.strip() for n in e.get("SPARKVAULT_NODES", "node-a,node-b").split(",") if n.strip()),
        )

    def validate(self) -> None:
        if len(self.secret_key) < 32:
            raise SystemExit("SPARKVAULT_SECRET_KEY must be set to at least 32 characters")
        if self.ingest_token and len(self.ingest_token) < 32:
            raise SystemExit("SPARKVAULT_INGEST_TOKEN, if set, must be at least 32 characters (or leave it empty to disable upload)")
        if not self.admin_hash.startswith("scrypt:"):
            raise SystemExit("SPARKVAULT_ADMIN_HASH must be set (generate: python -m app.hashpw)")
