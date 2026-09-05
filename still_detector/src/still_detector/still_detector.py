import logging
import os
import sys

from nat.plugin_api import Builder
from nat.plugin_api import FunctionBaseConfig
from nat.plugin_api import FunctionInfo
from nat.plugin_api import register_function

logger = logging.getLogger(__name__)

# This package lives at <repo_root>/still_detector/src/still_detector/still_detector.py
# Our actual detection logic lives at <repo_root>/src/*.py — add both the repo root
# and its src/ dir to sys.path so we can import them with their existing flat names.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")
for _path in (_REPO_ROOT, _SRC_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from github_api import get_pr_context, get_diff  # noqa: E402
from targets import discover_targets  # noqa: E402
from claims import extract_claims, check_claim_against_target  # noqa: E402


class StillDetectorFunctionConfig(FunctionBaseConfig, name="still_detector"):
    """
    Still drift detector: checks docs and AI-agent instruction files for semantic drift against a PR diff.
    """


@register_function(config_type=StillDetectorFunctionConfig)
async def still_detector_function(config: StillDetectorFunctionConfig, builder: Builder):
    """
    Registers the Still drift-detection workflow (addressable via `still_detector` in configuration).
    """

    async def run_still_check(task: str) -> str:
        """
        Runs the full Still drift-detection pipeline against the current PR: discovers
        target files, fetches the diff, extracts claims, and checks each target file
        for semantic drift, in that fixed order.
        """
        repo, pr = get_pr_context()
        diff = get_diff(pr)
        targets = discover_targets(_REPO_ROOT)

        logger.info("Discovered targets: %s", targets)

        if not targets:
            return "No doc or instruction-file targets found in this repo."

        claims = extract_claims(diff)
        logger.info("Extracted claims:\n%s", claims)

        if claims.strip().upper() == "NONE":
            return "No claims in docs/instruction files are affected by this diff."

        report_lines = [f"PR #{pr.number}: {pr.title}", ""]

        for target_path in targets:
            full_path = os.path.join(_REPO_ROOT, target_path)
            with open(full_path) as f:
                target_content = f.read()

            result = check_claim_against_target(claims, target_path, target_content)
            if result.strip().upper() != "NONE":
                report_lines.append(f"=== {target_path} ===")
                report_lines.append(result)
                report_lines.append("")

        if len(report_lines) == 2:
            return "No drift detected in any target file."

        return "\n".join(report_lines)

    yield FunctionInfo.from_fn(run_still_check, description=run_still_check.__doc__)
