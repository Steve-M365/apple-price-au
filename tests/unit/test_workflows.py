"""CI contract tests for every GitHub Actions workflow.

The workflows are the deploy gate, so their shape is tested like code: they must
parse, they must run on a hosted runner, collection workflows must be both
scheduled and manually dispatchable, secrets may never reach a command line, and
the data gate must stay green exactly as configured.

YAML is parsed with ``BaseLoader`` on purpose. GitHub Actions' ``on:`` key is a
YAML 1.1 boolean under the default loader, which would silently rename the
trigger block to ``True``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

SECRET_REF = re.compile(r"\bsecrets\.[A-Za-z_][A-Za-z0-9_]*")
REQUIRED_PATH_FILTERS = {"data/**", "scripts/**", "config/**", "tests/**"}


def _workflow_paths(repo_root: Path) -> list[Path]:
    directory = repo_root / ".github" / "workflows"
    return sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load(path: Path) -> dict[str, Any]:
    payload = yaml.load(_raw(path), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict), f"{path.name} does not contain a YAML mapping"
    return cast(dict[str, Any], payload)


def _triggers(document: dict[str, Any]) -> dict[str, Any]:
    on = document.get("on")
    assert on is not None, "workflow has no 'on:' trigger block"
    if isinstance(on, str):
        return {on: ""} if on else {}
    assert isinstance(on, dict), "'on:' must be an event name or a mapping of events"
    return cast(dict[str, Any], on)


def _jobs(document: dict[str, Any]) -> dict[str, Any]:
    jobs = document.get("jobs")
    assert isinstance(jobs, dict) and jobs, "workflow has no jobs"
    return cast(dict[str, Any], jobs)


def _steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in _jobs(document).values():
        assert isinstance(job, dict)
        raw_steps = job.get("steps")
        if raw_steps is None:
            continue
        assert isinstance(raw_steps, list)
        steps.extend(cast(dict[str, Any], step) for step in raw_steps)
    return steps


def _run_blocks(document: dict[str, Any]) -> Iterator[str]:
    for step in _steps(document):
        run = step.get("run")
        if isinstance(run, str):
            yield run


def _clean(block: str) -> str:
    """Strip shell comments so a YAML annotation cannot satisfy a check."""
    return "\n".join(line.split("#", 1)[0] for line in block.splitlines())


def _job_commands(job: dict[str, Any]) -> str:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return ""
    runs = [
        cast(str, step["run"])
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]
    return _clean("\n".join(runs))


def _commands(document: dict[str, Any]) -> str:
    """Run blocks with comments removed: what the runner will actually execute."""
    return "\n".join(_clean(run) for run in _run_blocks(document))


def _secret_offenders(node: Any, *, allowed: bool = False) -> list[str]:
    """Every ``secrets.*`` reference that is not under an ``env:``/``with:`` block."""
    offenders: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child_allowed = allowed or key in {"env", "with"}
            offenders.extend(_secret_offenders(value, allowed=child_allowed))
    elif isinstance(node, list):
        for item in node:
            offenders.extend(_secret_offenders(item, allowed=allowed))
    elif isinstance(node, str) and SECRET_REF.search(node) and not allowed:
        offenders.append(node)
    return offenders


def _collects_data(path: Path, document: dict[str, Any]) -> bool:
    label = f"{path.stem} {document.get('name', '')}".lower()
    if "collect" in label:
        return True
    commands = _commands(document)
    return "scripts.collectors" in commands or "scripts/collectors" in commands


def test_workflow_directory_contains_at_least_one_workflow(repo_root: Path) -> None:
    assert _workflow_paths(repo_root), ".github/workflows/ has no workflow files"


@pytest.mark.parametrize("path", _workflow_paths(Path(__file__).resolve().parents[2]))
def test_every_workflow_parses_as_yaml(path: Path) -> None:
    document = _load(path)
    assert "name" in document, f"{path.name} has no workflow name"


@pytest.mark.parametrize("path", _workflow_paths(Path(__file__).resolve().parents[2]))
def test_every_workflow_has_triggers_jobs_and_a_runner(path: Path) -> None:
    document = _load(path)
    assert _triggers(document), f"{path.name} declares no triggers"

    jobs = _jobs(document)
    assert any(isinstance(job, dict) and job.get("runs-on") for job in jobs.values()), (
        f"{path.name} has no job with a runner"
    )


@pytest.mark.parametrize("path", _workflow_paths(Path(__file__).resolve().parents[2]))
def test_collection_workflows_are_manual_and_scheduled(path: Path) -> None:
    document = _load(path)
    if not _collects_data(path, document):
        pytest.skip(f"{path.name} does not collect data")

    triggers = _triggers(document)
    assert "workflow_dispatch" in triggers, f"{path.name} cannot be run by hand"
    if path.stem == "validate-data":
        # The validation gate is deliberately exempt: it reacts to changes and
        # has nothing to collect on a schedule.
        return
    assert "schedule" in triggers, f"{path.name} collects data but is never scheduled"


@pytest.mark.parametrize("path", _workflow_paths(Path(__file__).resolve().parents[2]))
def test_secrets_stay_in_env_or_with_blocks(path: Path) -> None:
    document = _load(path)
    assert _secret_offenders(document) == [], (
        f"{path.name} interpolates secrets outside env:/with:. Put the value in "
        "an env: block and pass the environment variable name on the command line."
    )
    leaked = [run for run in _run_blocks(document) if SECRET_REF.search(run)]
    assert leaked == [], f"{path.name} puts a secret on a command line: {leaked}"


@pytest.mark.parametrize("path", _workflow_paths(Path(__file__).resolve().parents[2]))
def test_deploy_workflows_depend_on_validation(path: Path) -> None:
    document = _load(path)
    label = f"{path.stem} {document.get('name', '')}".lower()
    if "deploy" not in label:
        pytest.skip(f"{path.name} is not a deploy workflow")

    jobs = cast(dict[str, dict[str, Any]], _jobs(document))
    validation_jobs = {
        job_id for job_id, job in jobs.items() if "validate_data" in _job_commands(job)
    }
    assert validation_jobs, f"{path.name} has no job that runs the data gate"

    dependencies: set[str] = set()
    for job_id, job in jobs.items():
        needs = job.get("needs")
        if not needs:
            continue
        needed = [needs] if isinstance(needs, str) else list(needs)
        for dependency in needed:
            assert dependency in jobs, f"{path.name}: job {job_id} needs unknown job {dependency}"
        dependencies.update(needed)

    assert validation_jobs & dependencies, (
        f"{path.name} deploys without a 'needs:' dependency on the validation job"
    )


def test_no_env_file_is_committed_and_gitignore_covers_it(repo_root: Path) -> None:
    assert not (repo_root / ".env").exists(), "a .env file is committed; secrets must not be"
    assert not list(repo_root.glob(".env.local")), "local env overrides must never be committed"
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\.env$", gitignore), ".gitignore must ignore .env"


def test_actionlint_passes_when_available(repo_root: Path) -> None:
    executable = shutil.which("actionlint")
    paths = [str(path) for path in _workflow_paths(repo_root)]
    if executable is None:
        print(
            "actionlint is not on PATH: skipping shellcheck-backed workflow lint "
            "(CI installs it; run `brew install actionlint` to check locally)"
        )
        return

    completed = subprocess.run([executable, *paths], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_validate_data_workflow_runs_on_changes_and_by_hand(repo_root: Path) -> None:
    path = repo_root / ".github" / "workflows" / "validate-data.yml"
    assert path.is_file(), "the data gate workflow is missing"
    triggers = _triggers(_load(path))

    for event in ("push", "pull_request"):
        assert event in triggers, f"validate-data.yml does not run on {event}"
        filters = triggers[event].get("paths")
        assert isinstance(filters, list), f"validate-data.yml {event} has no path filters"
        missing = REQUIRED_PATH_FILTERS - set(cast(list[str], filters))
        assert not missing, f"validate-data.yml {event} ignores {sorted(missing)}"
    assert "workflow_dispatch" in triggers, "validate-data.yml cannot be dispatched by hand"


def test_validate_data_workflow_is_read_only(repo_root: Path) -> None:
    document = _load(repo_root / ".github" / "workflows" / "validate-data.yml")
    permissions = document.get("permissions")
    assert isinstance(permissions, dict), "validate-data.yml must declare permissions"
    assert permissions.get("contents") == "read"


def test_validate_data_workflow_runs_the_gate_without_strict(repo_root: Path) -> None:
    path = repo_root / ".github" / "workflows" / "validate-data.yml"
    document = _load(path)
    gate_blocks = [
        block
        for block in _run_blocks(document)
        if "scripts.validation.validate_data" in _clean(block)
    ]
    assert gate_blocks, "the validation CLI is never run"
    for block in gate_blocks:
        assert "--strict" not in _clean(block), (
            "validate-data.yml must not run --strict while require_adapter_modules is false; "
            "APA-017 (the adapter framework) flips it in the commit that lands it"
        )
    assert "require_adapter_modules" in _raw(path), (
        "the handover comment must name the config key that gates --strict, not a card id: "
        "card ids shift as the board grows, so pinning one makes this test rot"
    )


def test_validate_data_workflow_publishes_a_summary_and_artefact(repo_root: Path) -> None:
    document = _load(repo_root / ".github" / "workflows" / "validate-data.yml")
    assert any("GITHUB_STEP_SUMMARY" in run for run in _run_blocks(document)), (
        "the validation report is not written to the job summary"
    )

    uploads = [step for step in _steps(document) if "upload-artifact" in str(step.get("uses", ""))]
    assert uploads, "the JSON report is never uploaded"
    assert any(str(step.get("if", "")).startswith("failure()") for step in uploads), (
        "the JSON report must be uploaded on failure"
    )


def test_validate_data_workflow_runs_the_quality_gates(repo_root: Path) -> None:
    document = _load(repo_root / ".github" / "workflows" / "validate-data.yml")
    commands = _commands(document)
    assert "ruff check" in commands, "the data gate does not lint"
    assert "ruff format --check" in commands, "the data gate does not check formatting"
    assert "mypy --strict scripts/models scripts/validation" in commands, (
        "the typed surface (models, validation) is not type-checked"
    )


def test_coverage_config_targets_scripts_only(repo_root: Path) -> None:
    config = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    coverage = config["tool"]["coverage"]["run"]
    assert coverage["source"] == ["scripts"], "coverage measures more than the pipeline code"
    assert any("export_schema" in pattern for pattern in coverage["omit"]), (
        "generated schema exporters must be excluded from coverage"
    )


def test_test_step_enables_coverage(repo_root: Path) -> None:
    document = _load(repo_root / ".github" / "workflows" / "validate-data.yml")
    commands = _commands(document)
    assert "--cov=scripts" in commands, "pytest runs without coverage"
    assert "--cov-report=term-missing" in commands, "coverage is not reported per module"
    assert "--cov-fail-under=" in commands, "coverage has no gate"
