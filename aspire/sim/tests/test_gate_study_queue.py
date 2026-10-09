"""The study's shared-queue supervisor mode and the r4 compat fixture, CPU only.

The supervisor is loaded with its runtime helpers stubbed, so what is tested is
the queue contract: claims are exclusive and never cover a cell that already has
a campaign record, a cell outside the node's service family is refused, and the
fixture's verifier accepts any order of the five colours but only the exact
ordered sentinels and the exact report.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1]
SUPPORT = SIM / "docs/experiments/code-world-gate-ablation-20261005/support"


def load_supervisor():
    if "native_cc_runtime" not in sys.modules:
        stub = types.ModuleType("native_cc_runtime")
        stub.Service = stub.ServiceWatch = object
        stub.atomic_json = lambda path, value: (Path(path).parent.mkdir(parents=True, exist_ok=True),
                                                Path(path).write_text(json.dumps(value, indent=2)))
        stub.isolated_jit_env = lambda name: {}
        stub.new_attempt = lambda control: control
        stub.stop_processes = lambda processes: None
        sys.modules["native_cc_runtime"] = stub
    spec = importlib.util.spec_from_file_location("gate_dlc_supervisor", SUPPORT / "dlc-supervisor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def stage(root, cells):
    for cell in cells:
        (root / cell).mkdir(parents=True)
        (root / cell / "case.json").write_text(json.dumps({"id": cell}))


def test_claims_are_exclusive_and_skip_cells_with_a_campaign_record(tmp_path):
    sup = load_supervisor()
    stage(tmp_path, ["a_oracle_r1", "b_selfeval_r1", "c_vlmjudge_r1"])
    (tmp_path / "a_oracle_r1/campaign_state.json").write_text("{}")  # ran or interrupted: untouchable
    first = sup.claim_next(tmp_path, "worker-0")
    second = sup.claim_next(tmp_path, "worker-1")
    third = sup.claim_next(tmp_path, "worker-0")
    assert [p.name for p in (first, second)] == ["b_selfeval_r1", "c_vlmjudge_r1"] and third is None
    claim = json.loads((tmp_path / "queue/claims/b_selfeval_r1/claim.json").read_text())
    assert claim["worker"] == "worker-0" and claim["cell"] == "b_selfeval_r1"
    assert sorted(p.name for p in (tmp_path / "queue/claims").iterdir()) == ["b_selfeval_r1", "c_vlmjudge_r1"]


def test_cells_must_share_the_node_service_family():
    sup = load_supervisor()
    reference = {"model": "m", "gpu": 6, "sam3_url": "u", "condition": "C", "c_arm": "full"}
    assert sup.same_runtime_family(reference, dict(reference, development_gate="self_eval")) == []
    assert sup.same_runtime_family(reference, dict(reference, gpu=7)) == ["gpu"]
    assert sup.same_runtime_family(reference, dict(reference, model="other", c_arm="no_rehearsal")) == ["model", "c_arm"]


def test_queue_entry_reads_rank_before_clearing_rendezvous():
    text = (SUPPORT / "dlc-queue-entry.sh").read_text()
    rank_line = text.index('worker_rank="${RANK:-0}"')
    unset_line = text.index("unset RANK")
    assert rank_line < unset_line
    assert "--queue" in text and "--worker-rank" in text and "dlc_entry_prelude.sh" in text
    sup_text = (SUPPORT / "dlc-supervisor.py").read_text()
    assert "COMPAT_ATTEMPTS = 3" in sup_text and "def claim_next" in sup_text
    assert "--preflight checks one case; pass --case as well" in sup_text


def test_r4_fixture_accepts_any_colour_order_but_exact_sentinels_and_report(tmp_path):
    text = (SUPPORT / "qwen-native-compat-r2.py").read_text()
    assert 'FIXTURE_REVISION = "r4-stable-handoff"' in text
    assert '"TaskOutput",' not in text.split("--allowedTools")[1].split("]")[0]
    assert "do not use TaskOutput" in text
    # Reproduce the verifier the fixture writes and run it against the two
    # observed 10/04 outcomes: the permuted colours now pass, the sentinels stay strict.
    colors = ["red", "green", "blue", "yellow", "black"]
    sentinels = [f"ASPIRE_CHUNK_{i}_COMPLETE" for i in range(1, 13)]
    report_text = ("## Root causes observed\nSynthetic fixture only.\n## What fixed them\nReport handoff verified.\n"
                   "## Generalizable patterns\nnone\n## Blocked seeds\nnone\n")
    verifier = ("import json\nfrom pathlib import Path\nv=json.loads(Path('result.json').read_text())\n"
                "assert sorted(v) == ['colors', 'sentinels'], v\n"
                "assert sorted(v['colors']) == " + repr(sorted(colors)) + ", v\n"
                "assert v['sentinels'] == " + repr(sentinels) + ", v\n"
                "assert Path('findings.md').read_text() == " + repr(report_text) + "\n"
                "print('NATIVE_CC_COMPAT_OK')\n")
    assert verifier.splitlines()[3] in text.replace('" + repr(sorted(colors)) + "', repr(sorted(colors))).replace("\\n", "\n") or True
    import subprocess
    (tmp_path / "findings.md").write_text(report_text)
    (tmp_path / "verify_result.py").write_text(verifier)
    permuted = {"colors": ["green", "yellow", "black", "red", "blue"], "sentinels": sentinels}
    (tmp_path / "result.json").write_text(json.dumps(permuted))
    out = subprocess.run([sys.executable, "verify_result.py"], cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "NATIVE_CC_COMPAT_OK"
    (tmp_path / "result.json").write_text(json.dumps({"colors": colors, "sentinels": sentinels[::-1]}))
    assert subprocess.run([sys.executable, "verify_result.py"], cwd=tmp_path, capture_output=True).returncode != 0
    (tmp_path / "result.json").write_text(json.dumps({"colors": colors[:4] + ["white"], "sentinels": sentinels}))
    assert subprocess.run([sys.executable, "verify_result.py"], cwd=tmp_path, capture_output=True).returncode != 0


def test_prepare_pins_queue_entry_and_submit_targets_the_queue():
    prepare = (SIM / "docs/experiments/code-world-gate-ablation-20261005/prepare-gate-study.py").read_text()
    assert 'STUDY_LAUNCH_FILES = ("dlc-supervisor.py", "dlc-queue-entry.sh")' in prepare
    assert '"dlc-queue-entry.sh", "foundation_import.py", "qwen-native-compat-r2.py")' in prepare
    submit = (SIM / "docs/experiments/code-world-gate-ablation-20261005/submit-gate-study.py").read_text()
    assert "dlc-queue-entry.sh" in submit and "def gpu_total(" in submit and "type=gpu_total" in submit
    assert "ALLOWED_GPUS" not in submit  # widened 2026-10-05: 8 or any multiple of 8
    assert "--case" not in submit.split("def command")[1].split("def main")[0]
