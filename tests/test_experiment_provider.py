"""Experimental resource policy at the retained preparation boundary."""

import copy
import json
from types import SimpleNamespace

from test_translation_experiment import identity, snapshot
from translation_fixture import EchoModel

from coworker.server.youtube import HostManuscripts
from youtube_strataread.workbench.experiment import run_baseline
from youtube_strataread.workbench.library import PreparationService
from youtube_strataread.workbench.translation import DEFAULTS
from youtube_strataread.workbench.workspace import LocalWorkspace


def test_main_baseline_crosses_historical_call_and_token_budgets(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config.update(max_calls=1, max_tokens=1000)
    model = EchoModel()
    result = run_baseline(
        tmp_path / "experiment", snapshot(tmp_path), identity(), model, config=config
    )
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 4
    assert record["consumption_policy"] == "no-local-cumulative-budget"
    assert record["usage"]["reported"]["input"] == 400
    assert LocalWorkspace.open(tmp_path / "experiment/workspace").document("sample")["version"] == 1


def test_restored_historical_settings_cannot_reenable_budget_or_erase_completed_work(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config.update(max_calls=1, max_tokens=1000)
    root = tmp_path / "experiment"
    model = EchoModel()
    model.truncate_at = 3
    record = json.loads(
        run_baseline(root, snapshot(tmp_path), identity(), model, config=config).read_text()
    )
    assert record["state"] == "failed"
    workspace = LocalWorkspace.open(root / "workspace")
    assert workspace.translation_checkpoint("sample").exists()
    workspace.set_preparation_state("sample", "queued")
    model.truncate_at = None
    service = PreparationService(
        workspace,
        SimpleNamespace(
            acquire=lambda _: (_ for _ in ()).throw(AssertionError("retained source expected"))
        ),
        HostManuscripts(
            SimpleNamespace(model="mock-model", provider_complete=model),
            workspace,
            pipeline_options={"enforce_local_budgets": False},
        ),
    )
    assert service.run_next()
    assert workspace.asset("sample")["preparation_state"] == "ready"
    assert len(model.calls) == 5
    assert workspace.document("sample")["version"] == 1


def test_only_verified_exact_proxy_quota_signal_discards_current_attempt(tmp_path):
    from youtube_strataread.workbench.experiment_provider import QuotaContract

    class ProviderFailure(Exception):
        def __init__(self, code, status):
            self.body = {"error": {"code": code, "message": "private detail"}}
            self.status_code = status

    contract = QuotaContract(
        "0" * 64, 429, "insufficient_quota", "private verified contract fixture", verified=True
    )
    for name, failure, verified, discarded in [
        ("quota", ProviderFailure("insufficient_quota", 429), True, True),
        ("rate", ProviderFailure("rate_limit_exceeded", 429), True, False),
        ("billing", ProviderFailure(None, 402), True, False),
        ("auth", ProviderFailure("insufficient_quota", 401), True, False),
        ("unknown", ProviderFailure("insufficient_quota", 429), False, False),
    ]:
        source = snapshot(tmp_path)
        root = tmp_path / name
        model = EchoModel()
        run_baseline(root, source, identity(), model)
        ws = LocalWorkspace.open(root / "workspace")
        old = ws.document("sample")
        model = EchoModel()
        model.fail_at, model.error = 2, failure
        selected = (
            contract
            if verified
            else QuotaContract("0" * 64, 429, "insufficient_quota", "unverified", verified=False)
        )
        record = json.loads(
            run_baseline(root, source, identity(), model, quota_contract=selected).read_text()
        )
        assert record["state"] != "ready"
        assert ws.translation_checkpoint("sample").exists() is not discarded
        assert ws.document("sample") == old
        assert ws.retained_transcript("sample")
        assert "private detail" not in record["reason"]


def test_transient_requests_stay_bounded_without_local_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("youtube_strataread.workbench.translation.time.sleep", lambda _: None)
    attempts = []

    def unavailable(*args, **kwargs):
        attempts.append(kwargs)
        raise TimeoutError("private timeout payload")

    record = json.loads(
        run_baseline(
            tmp_path / "experiment", snapshot(tmp_path), identity(), unavailable
        ).read_text()
    )
    assert record["state"] == "waiting_retry"
    assert record["attempts"] == 3
    assert len(attempts) == 3
    assert all(call["max_tokens"] == 8192 for call in attempts)
    assert (
        LocalWorkspace.open(tmp_path / "experiment/workspace")
        .translation_checkpoint("sample")
        .exists()
    )
    assert "private timeout payload" not in record["reason"]


def test_proxy_probe_without_completion_records_precise_unverified_blocker(tmp_path):
    from youtube_strataread.workbench.experiment_provider import probe_provider

    path = probe_provider(
        tmp_path / "probe", identity(), None, blocker="Configured proxy credentials are unavailable"
    )
    record = json.loads(path.read_text())
    assert record["state"] == "unverified"
    assert record["blocker"] == "Configured proxy credentials are unavailable"
    assert record["requests"] == []
    assert not record["capabilities"]["output_budget_effective"]["verified"]


def test_probe_distinguishes_wire_evidence_from_sent_parameters_and_redacts_transport(tmp_path):
    from coworker.providers.base import AssistantTurn, TokenUsage
    from youtube_strataread.workbench.experiment_provider import probe_provider

    wire = []
    calls = []

    def complete(model, messages, **options):
        calls.append(options)
        wire.append(
            {
                "model": model,
                "max_tokens": options["max_tokens"],
                "Authorization": "never save",
                "url": "private",
            }
        )
        if len(calls) == 1:
            return AssistantTurn(
                text="EDISON-PROBE-OK", finish_reason="stop", usage=TokenUsage(input=100, output=8)
            )
        return AssistantTurn(
            text="alpha " * options["max_tokens"],
            finish_reason="length",
            usage=TokenUsage(input=20, output=options["max_tokens"]),
        )

    model = identity()
    # Simulated transport exercises reporting, not an actual proxy contract.
    path = probe_provider(tmp_path / "probe", model, complete, wire_requests=wire)
    record = json.loads(path.read_text())
    assert len(calls) == 3
    assert record["state"] == "unverified"
    assert not record["capabilities"]["output_budget_effective"]["verified"]
    assert "never save" not in path.read_text()
    assert "private" not in path.read_text()
    assert not record["capabilities"]["timeout"]["verified"]
    assert not record["capabilities"]["quota_exhaustion"]["verified"]


def test_probe_cli_missing_credentials_is_explicit_unverified_result(tmp_path, monkeypatch, capsys):
    from youtube_strataread.workbench.experiment_provider import main

    monkeypatch.delenv("EDISON_EXPERIMENT_API_KEY", raising=False)
    monkeypatch.delenv("EDISON_EXPERIMENT_PROXY_URL", raising=False)
    code = main(
        [
            "--root",
            str(tmp_path / "probe"),
            "--model",
            "configured-model",
            "--proxy-version",
            "unknown",
            "--configuration-sha256",
            "0" * 64,
            "--execute",
        ]
    )
    assert code == 2
    record = json.loads(
        __import__("pathlib").Path(json.loads(capsys.readouterr().out)["record"]).read_text()
    )
    assert record["state"] == "unverified"
    assert "EDISON_EXPERIMENT_API_KEY" in record["blocker"]
