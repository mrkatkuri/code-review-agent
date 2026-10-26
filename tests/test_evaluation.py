from unittest.mock import Mock

from evals.langsmith_eval import evaluate_dataset, execution_success, labeled_recall


def test_langsmith_adapter_scores_labels_and_runs_offline_target():
    client = Mock()
    evaluate_dataset("offline-fixture", None, client)
    target = client.evaluate.call_args.args[0]
    result = target({"code": "print(missing)\n", "path": "example.py"})
    assert result["execution_errors"] == []
    assert result["latency_seconds"] >= 0
    score = labeled_recall(
        result, {"expected": [{"line": 1, "source": "ruff", "rule": "F821"}]}
    )
    assert score["score"] == 1
    assert execution_success(result)["score"] is True
    assert labeled_recall(result, {"expected": []})["score"] is None
    assert client.evaluate.call_args.kwargs["data"] == "offline-fixture"
