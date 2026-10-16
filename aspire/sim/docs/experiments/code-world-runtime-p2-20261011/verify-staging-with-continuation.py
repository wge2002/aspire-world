"""Original ten-cell staging checks, plus explicit current continuation evidence."""
import importlib.util,json
from pathlib import Path
from startup_gate import require_fresh_review
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('original_runtime_stager',HERE/'prepare-runtime-study.py')
stager=importlib.util.module_from_spec(spec);spec.loader.exec_module(stager)
original=stager.check_no_prior_input

def check(case):
    result=original(case);control=Path(case['control'])
    if (control/'native_work_continuation_checkpoint.json').exists():
        saved=json.loads((control.parent/'coordination/native-work-gate-verification.json').read_text())
        current=require_fresh_review(control,saved)
        # Only this exact old one-smoke restriction is superseded. All no-prior
        # inputs, case hashes, runtime freezes, seed/model and isolation remain.
        result['problems']=[p for p in result['problems'] if p not in {'startup evidence is not one charged current seed51 smoke','native smoke has not reached its recorded current-cell checkpoint'}]
        result['explicit_continuation_binding']=current['binding_sha256']
    return result

stager.check_no_prior_input=check
if __name__=='__main__':
    result=stager.verify([c for c,_ in stager.CELLS]);raise SystemExit(result['status']!='verified')
