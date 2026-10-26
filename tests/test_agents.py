import json
import unittest
from unittest.mock import Mock, patch

from agents import CodeQualityAgent, SecurityAgent, model_reviewer
from agents import TestCoverageAgent as CoverageAgent
from repository import Snapshot
from schemas import AgentResult


class AgentTests(unittest.TestCase):
    def test_specialized_agents_send_distinct_instructions(self):
        snapshot = Snapshot("/repo", "base", "head", files={"app.py": "print(1)\n"})
        for cls, category, phrase in (
            (CodeQualityAgent, "code_quality", "logic errors"),
            (SecurityAgent, "security", "attack prerequisites"),
            (CoverageAgent, "test_coverage", "expected assertions"),
        ):
            with self.subTest(category=category):
                model = Mock()
                model.invoke.return_value = AgentResult(agent=category)
                agent = cls("unused", structured_model=model)
                self.assertEqual(agent.review(snapshot, []).agent, category)
                messages = model.invoke.call_args.args[0]
                self.assertIn(phrase, messages[0][1])
                self.assertIn(
                    "1: print(1)", json.loads(messages[1][1])["files"]["app.py"]
                )

    def test_wrong_category_is_rejected(self):
        model = Mock()
        model.invoke.return_value = AgentResult(agent="security")
        agent = CodeQualityAgent("unused", structured_model=model)
        with self.assertRaises(ValueError):
            agent.review(Snapshot("/repo", "base", "head"), [])

    def test_factory_routes_to_separate_agents(self):
        with (
            patch("agents.CodeQualityAgent") as quality,
            patch("agents.SecurityAgent") as security,
            patch("agents.TestCoverageAgent") as coverage,
        ):
            review = model_reviewer("configured-model")
            snapshot = Snapshot("/repo", "base", "head")
            for category, cls in (
                ("code_quality", quality),
                ("security", security),
                ("test_coverage", coverage),
            ):
                self.assertIs(
                    review(category, snapshot, []), cls.return_value.review.return_value
                )
                cls.assert_called_once_with("configured-model")
                cls.return_value.review.assert_called_once_with(snapshot, [])
