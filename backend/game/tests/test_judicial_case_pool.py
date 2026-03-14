import json
from pathlib import Path

from django.test import SimpleTestCase


class JudicialCasePoolTests(SimpleTestCase):
    def _load_cases(self):
        path = Path(__file__).resolve().parents[1] / "judicial_cases.json"
        with path.open(encoding="utf-8") as f:
            return json.load(f)["cases"]

    def test_pool_contains_mixed_original_valid_cases(self):
        cases = self._load_cases()
        original_valid = [c for c in cases if c.get("judgment_truth") == "ORIGINAL_VALID"]

        self.assertEqual(len(cases), 80)
        self.assertGreaterEqual(len(original_valid), 30)

    def test_original_valid_cases_reward_affirming_over_retrial(self):
        cases = self._load_cases()
        original_valid = [c for c in cases if c.get("judgment_truth") == "ORIGINAL_VALID"]

        for case in original_valid:
            options = {o["action"]: o["immediate_effects"] for o in case["options"]}
            approve = options["核准原判"]
            retrial = options["提审改判"]

            self.assertGreater(approve["prestige"], retrial["prestige"], case["case_id"])
            self.assertGreaterEqual(approve["inspector_favor"], retrial["inspector_favor"], case["case_id"])
            self.assertGreaterEqual(approve["treasury"], retrial["treasury"], case["case_id"])
