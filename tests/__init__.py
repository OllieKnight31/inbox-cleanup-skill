"""Tests run against a throwaway home folder, never the real ~/.inbox-cleanup."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["INBOX_CLEANUP_HOME"] = tempfile.mkdtemp(prefix="inbox-cleanup-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
