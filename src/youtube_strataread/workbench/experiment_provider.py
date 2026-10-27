"""Explicit experimental provider contracts; unknown signals never discard work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from youtube_strataread.workbench.retry import Failure, classify_model
from youtube_strataread.workbench.translation import ConfirmedCreditExhausted


@dataclass(frozen=True)
class QuotaContract:
    """A separately verified exact error contract for one configured proxy.

    Synthetic tests exercise this rule without attesting any real proxy. No built-in
    rule assumes an OpenAI-shaped proxy implements OpenAI's billing semantics.
    """

    configuration_sha256: str
    status_code: int
    error_code: str
    evidence_reference: str
    verified: bool = False

    def recognizes(self, error: Exception) -> bool:
        if not self.verified or not self.evidence_reference or not self.error_code:
            return False
        body = getattr(error, "body", None)
        if not isinstance(body, dict):
            return False
        details = body.get("error", body)
        return (
            isinstance(details, dict)
            and getattr(error, "status_code", None) == self.status_code
            and details.get("code") == self.error_code
        )


def classify_experiment_model(error: Exception, now: float) -> Failure:
    if isinstance(error, ConfirmedCreditExhausted):
        return Failure("fatal", str(error))
    status = getattr(error.__cause__ or error, "status_code", None)
    if status == 402:
        return Failure("fatal", "模型服务返回 HTTP 402；未确认额度耗尽，本次停止，已完成阶段保留。")
    return classify_model(error, now)


def probe_provider(
    root,
    model,
    complete,
    *,
    blocker: str | None = None,
    input_characters: int = 4096,
    output_tokens: int = 256,
    timeout_seconds: float = 20,
    wire_requests: list[dict[str, Any]] | None = None,
):
    """Make three bounded synthetic probes, or persist an honest missing-input result.

    ``complete`` uses the host positional completion contract. ``wire_requests`` is
    optional metadata captured by the actual transport: model, output limit and
    request timeout only. Headers, endpoints and raw error bodies never belong here.
    A successful probe establishes only its observed size, never a model's maximum.
    """
    import time
    import uuid
    from dataclasses import asdict
    from pathlib import Path

    from youtube_strataread.workbench.experiment import _checked_root, _repository, _write_json

    if not 128 <= input_characters <= 100000 or not 16 <= output_tokens <= 4096:
        raise ValueError("Probe sizes must stay within the explicit bounded envelope")
    if not 0 < timeout_seconds <= 120:
        raise ValueError("Probe timeout must be within 120 seconds")
    root = Path(root)
    root = _checked_root(root, root.parent / "synthetic-probe-input")
    marker = root / "provider-probe.json"
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise ValueError("Provider probe output must be a new or owned directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    _write_json(marker, {"format": "edison-provider-probe", "schema_version": 1})
    directory = root / uuid.uuid4().hex
    directory.mkdir(mode=0o700)
    capabilities = {
        key: {"verified": False, "reason": "No real configured-proxy observation"}
        for key in (
            "input_size",
            "output_budget_forwarding",
            "output_budget_effective",
            "finish_reason",
            "usage",
            "truncation",
            "timeout",
            "quota_exhaustion",
        )
    }
    record = {
        "schema_version": 1,
        "repository": _repository(),
        "model": asdict(model),
        "state": "unverified",
        "blocker": blocker,
        "requests": [],
        "capabilities": capabilities,
        "limits": {
            "input_characters": input_characters,
            "output_tokens": output_tokens,
            "timeout_seconds": timeout_seconds,
            "automatic_runs": 0,
        },
        "limitations": [
            "Observed sizes are lower bounds, not maximum capacity.",
            "Parameter transmission alone does not prove the proxy honored it.",
            "No account exhaustion is induced; quota semantics require separate provider-specific evidence.",
        ],
    }
    if complete is None:
        record["blocker"] = blocker or "No configured completion adapter supplied"
    else:
        sentinel = "EDISON-PROBE-OK"
        cases = [
            (
                "input",
                "Ignore the filler. Reply exactly with the final marker.\n"
                + "filler " * (input_characters // 7)
                + "\n"
                + sentinel,
                32,
            ),
            (
                "short-output",
                "Write the word alpha separated by spaces at least 1000 times. Do not stop early.",
                8,
            ),
            (
                "larger-output",
                "Write the word alpha separated by spaces at least 1000 times. Do not stop early.",
                output_tokens,
            ),
        ]
        for name, prompt, limit in cases:
            started = time.monotonic()
            item = {
                "case": name,
                "input_characters": len(prompt),
                "requested_output_tokens": limit,
                "requested_timeout_seconds": timeout_seconds,
            }
            try:
                turn = complete(
                    model.id,
                    [{"role": "user", "content": prompt}],
                    tools=None,
                    max_tokens=limit,
                    temperature=0,
                    timeout=timeout_seconds,
                )
                usage = getattr(turn, "usage", None)
                item.update(
                    response=turn.text,
                    finish_reason=turn.finish_reason,
                    usage=usage.as_dict() if usage else None,
                    response_characters=len(turn.text or ""),
                )
            except Exception as error:
                item["error_type"] = type(error).__name__
                item["status_code"] = getattr(error, "status_code", None)
                item["timeout_observed"] = isinstance(error, TimeoutError) or type(
                    error
                ).__name__ in {"APITimeoutError", "ReadTimeout", "ConnectTimeout"}
            item["elapsed_seconds"] = time.monotonic() - started
            record["requests"].append(item)
        wire = wire_requests or []
        # Retain a strict allowlist even if a caller accidentally supplies headers.
        record["wire_requests"] = [
            {
                key: request[key]
                for key in ("model", "max_tokens", "max_completion_tokens", "timeout_seconds")
                if key in request
            }
            for request in wire
        ]
        real = not model.simulated
        observed = record["requests"]

        def observed_capability(key, condition, reason):
            capabilities[key] = {"verified": bool(real and condition), "reason": reason}

        observed_capability(
            "input_size",
            observed[0].get("response", "").strip() == sentinel,
            f"Requested {observed[0]['input_characters']} synthetic characters; inspect exact marker result",
        )
        forwarded = all(
            any(
                w.get("model") == model.id
                and (w.get("max_tokens") == limit or w.get("max_completion_tokens") == limit)
                for w in wire
            )
            for limit in (32, 8, output_tokens)
        )
        observed_capability(
            "output_budget_forwarding",
            forwarded,
            "Transport observations required; adapter arguments are not wire evidence",
        )
        short, larger = observed[1:]
        short_output = (short.get("usage") or {}).get("output")
        larger_output = (larger.get("usage") or {}).get("output")
        effective = (
            short.get("finish_reason") in {"length", "max_tokens"}
            and isinstance(short_output, int)
            and 0 < short_output <= 8
            and isinstance(larger_output, int)
            and short_output < larger_output <= output_tokens
            and larger.get("response_characters", 0) > short.get("response_characters", 0)
        )
        observed_capability(
            "output_budget_effective",
            forwarded and effective,
            "Requires a length-limited short response and a longer response with a larger allocation",
        )
        observed_capability(
            "finish_reason",
            all(item.get("finish_reason") is not None for item in observed),
            "All probe completions must expose a finish reason",
        )
        observed_capability(
            "usage",
            all(item.get("usage") and any(item["usage"].values()) for item in observed),
            "Provider-reported token fields only; absence remains unknown",
        )
        observed_capability(
            "truncation",
            any(item.get("finish_reason") in {"length", "max_tokens"} for item in observed),
            "Only observed length termination establishes truncation metadata",
        )
        observed_capability(
            "timeout",
            any(item.get("timeout_observed") for item in observed),
            "No timeout induced; remains unverified unless actually observed",
        )
        record["state"] = (
            "observed" if real and any("response" in item for item in observed) else "unverified"
        )
        if model.simulated:
            record["blocker"] = "Simulated adapter cannot verify the configured proxy"
    path = directory / "contract.json"
    _write_json(path, record)
    return path


def main(argv: list[str] | None = None) -> int:
    """Explicit, bounded OpenAI-compatible proxy probe; no global secret store."""
    import argparse
    import json
    import os
    from pathlib import Path

    from youtube_strataread.workbench.experiment import ModelIdentity

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--proxy-version", required=True)
    parser.add_argument("--configuration-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--input-characters", type=int, default=4096)
    parser.add_argument("--output-tokens", type=int, default=256)
    parser.add_argument("--timeout-seconds", type=float, default=20)
    args = parser.parse_args(argv)
    identity = ModelIdentity(args.model, args.proxy_version, args.configuration_sha256)
    key = os.environ.get("EDISON_EXPERIMENT_API_KEY")
    base_url = os.environ.get("EDISON_EXPERIMENT_PROXY_URL")
    blocker = None
    if not args.execute:
        blocker = "Explicit --execute was not supplied; no model requests were made"
    elif not key or not base_url:
        blocker = "Missing EDISON_EXPERIMENT_API_KEY or EDISON_EXPERIMENT_PROXY_URL; configured proxy remains unverified"
    wire: list[dict[str, Any]] = []
    complete = None
    client = None
    if blocker is None:
        import httpx
        from openai import OpenAI

        from coworker.providers.openai_provider import OpenAIProvider

        def observe(request):
            body = json.loads(request.content)
            wire.append(
                {
                    key: body[key]
                    for key in ("model", "max_tokens", "max_completion_tokens")
                    if key in body
                }
            )

        client = OpenAI(
            api_key=key,
            base_url=base_url,
            max_retries=0,
            http_client=httpx.Client(event_hooks={"request": [observe]}),
        )
        provider = OpenAIProvider(client=client)

        def complete(model, messages, **options):
            return provider.complete(model=model, messages=messages, **options)

    try:
        path = probe_provider(
            args.root,
            identity,
            complete,
            blocker=blocker,
            input_characters=args.input_characters,
            output_tokens=args.output_tokens,
            timeout_seconds=args.timeout_seconds,
            wire_requests=wire,
        )
    finally:
        if client:
            client.close()
    record = json.loads(path.read_text())
    print(json.dumps({"record": str(path), "state": record["state"]}))
    return 0 if record["state"] == "observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
