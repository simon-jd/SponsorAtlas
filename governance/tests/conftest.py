import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tmp_path():
    """viewer_auth only opens directories under the project root, so the
    default /tmp basetemp is refused with FILE_SAFETY; use a project-owned one."""
    base = ROOT / ".tools" / "test-tmp"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="governance-", dir=base))
    yield path
    shutil.rmtree(path, ignore_errors=True)
