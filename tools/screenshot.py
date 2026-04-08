"""
tools/screenshot.py — Evidence capture and screenshot management

Handles timestamped screenshot capture, organization, and metadata.
"""
from __future__ import annotations
import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from utils.config import get

if TYPE_CHECKING:
    from tools.android_device import AndroidDevice


class EvidenceCapture:
    """
    Manages evidence capture for a single UAT session.
    Creates a structured directory: evidence_dir/run_id/account_id/
    """

    def __init__(self, run_id: str, account_id: str, feature_name: str):
        self.run_id = run_id
        self.account_id = account_id
        self.feature_name = feature_name
        evidence_root = Path(get("uat.evidence_dir", ".tmp/evidence"))
        self.session_dir = evidence_root / run_id / account_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.step_count = 0
        self.log: list[dict] = []
        self.start_time = datetime.now().isoformat()

    def capture(
        self,
        device: "AndroidDevice",
        step_label: str,
        action_taken: str = "",
        notes: str = "",
    ) -> str:
        """
        Capture a screenshot with metadata.
        Returns the file path of the saved screenshot.
        """
        self.step_count += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"step_{self.step_count:03d}_{timestamp}.png"
        save_path = str(self.session_dir / filename)
        device.screenshot(save_path=save_path)
        entry = {
            "step": self.step_count,
            "timestamp": datetime.now().isoformat(),
            "label": step_label,
            "action": action_taken,
            "notes": notes,
            "screenshot": filename,
            "current_package": device.get_current_package(),
        }
        self.log.append(entry)
        return save_path

    def save_log(self) -> str:
        """Write the step log to a JSON file. Returns the path."""
        log_path = self.session_dir / "step_log.json"
        data = {
            "run_id": self.run_id,
            "account_id": self.account_id,
            "feature": self.feature_name,
            "start_time": self.start_time,
            "end_time": datetime.now().isoformat(),
            "total_steps": self.step_count,
            "steps": self.log,
        }
        with open(log_path, "w") as f:
            json.dump(data, f, indent=2)
        return str(log_path)

    def get_evidence_pack(self) -> dict:
        """Return a summary dict for use by evaluator agents."""
        return {
            "run_id": self.run_id,
            "account_id": self.account_id,
            "feature": self.feature_name,
            "session_dir": str(self.session_dir),
            "total_steps": self.step_count,
            "steps": self.log,
        }
