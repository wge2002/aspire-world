"""Render the Q2 per-cell table (markdown) from data/cells.json and data/phaseA_never_passed.json."""
import json, sys
cells = json.load(open(sys.argv[1]))
print("| 轮次 | cell | 子集 | 状态 | 阶段A到首过51(试次/小时) | 版本数 | 各版本开发通过 | B阶段修复模式(次) | 被封模式 | 冻结开发 | held-out | 开发h | token(入/出 M) |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for c in cells:
    first = f"{c['phaseA_first_pass_at']} / {c['hours_to_first_51_pass']}" if c["phaseA_first_pass_at"] else f"从未（{c['phaseA_trials']} 试）"
    modes = ", ".join(f"{k}×{v}" for k, v in c["repair_modes_B"].items()) or "-"
    dev = f"{c['selected_ledger_passed']}/15" if c.get("selected_ledger_passed") is not None else "-"
    ho = c["heldout_rate"] if c["heldout_rate"] is not None else "-"
    hours = c["dev_hours"] or c["trial_span_hours"]
    status = c["status"] + (" (作业已停/空转)" if c["status"] == "solver_running" and c["round"] == "r3" else "")
    print(f"| {c['round']} | {c['cell']} | {c['suite'].replace('libero_','')} | {status} | {first} | {c['n_versions_swept']} | {[v['passed'] for v in c['versions']] or '-'} | {modes} | {', '.join(c['blocked_modes']) or '-'} | {dev} | {ho} | {hours} | {c['input_tokens']/1e6:.0f} / {c['output_tokens']/1e6:.2f} |")
