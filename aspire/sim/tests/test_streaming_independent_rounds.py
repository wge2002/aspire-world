"""Fresh repeats have no historical input and complete three audited rounds."""
import json
from pathlib import Path

import pytest

from test_streaming_fixloop import cell
from test_streaming_supervised_repair import (sup, round_one, ledger, trial, FAIL,
                                              task_dir, cli, author)
from test_streaming_autonomous_continuation import reviewer_setup
import streaming_fixloop_autonomous as auto
import streaming_fixloop_campaign as camp
import streaming_fixloop_review as review
import streaming_fixloop_rounds as rounds


@pytest.fixture
def independent(sup):
    c=sup
    manifest=Path(c.case['supervised_repair']['prior_input']['manifest'])
    d=json.loads(manifest.read_text())
    d.update(schema=rounds.INDEPENDENT_SCHEMA,parent_cell=None,parent_control=None,
             historical_attempts=0,files=[])
    manifest.write_text(json.dumps(d))
    block=c.case['supervised_repair']
    block['prior_input']['manifest_sha256']=auto.sha(manifest)
    block.update(independent_start=True,autonomous_review={
        'schema':auto.REVIEW_SCHEMA,'operator_dir':str(c.tmp/'operator'),
        'timeout_seconds':600,'max_attempts':2,
        **{k:c.case[k] for k in ['model','effort','context_tokens','max_output_tokens','claude_version']}})
    c.path.write_text(json.dumps(c.case))
    return c


def test_fresh_repeat_automatically_reviews_all_rounds_without_importing_attempts(independent,monkeypatch):
    c=independent
    assert cli(c,'init')[0]==0
    first=ledger(c)
    assert not first['trials'] and not first.get('supervised_continuation')
    provenance=first['identity']['supervised_repair']
    assert provenance['independent_start'] and provenance['prior_input']['historical_attempts']==0
    assert provenance['autonomous_review']['model']==c.case['model']
    prompt=camp.supervised_section(c.case,None)
    assert 'fresh' in prompt.lower() and 'no parent' in prompt.lower()
    assert 'your own earlier code' not in prompt
    assert cli(c,'snapshot')[0]==0
    author(c)
    round_one(c)
    user,operator=reviewer_setup(c,monkeypatch)
    calls=[]
    def runner(argv,**kw):
        assert argv[argv.index('--tools')+1]=='' and '--allowedTools' not in argv
        _,_,_,e=review.awaiting(c.path)
        payload=auto.review_payload(c.case,task_dir(c),ledger(c),e)
        assert payload['independent_start'] and 'corrected_starting_policy' not in payload
        calls.append(e['round'])
        answer={k:payload[k] for k in ['cell','study_id','phase_id','round','evidence_sha256']}
        answer.update(decision='continue',fatal=False,
                      analysis='Own development seeds show failed manipulation; inspect public pose and tracking evidence. '*2,
                      next_round_guidance='Reobserve own target and verify commanded pose before the next manipulation attempt. '*2)
        events=[{'type':'assistant','message':{'model':c.case['model'],'content':[]}},
                {'type':'result','is_error':False,'result':json.dumps(answer),
                 'modelUsage':{'q':{'contextWindow':1000000}}}]
        kw['stdout_path'].write_text('\n'.join(json.dumps(x) for x in events))
        kw['stderr_path'].write_text('')
        return 0
    for r in (1,2):
        assert cli(c,'close-round')[0]==0
        approved=auto.automatic_review(c.path,user,operator,runner=runner,clock=c.clock)
        assert cli(c,'consume-review','--review',approved['review'])[0]==0
        author(c,'fix')
        for seed in range(51,66):
            assert trial(c,'repair',seed,'fix',FAIL)[0]==0
            assert trial(c,'repair',seed,'fix',FAIL)[0]==2
    assert cli(c,'close-round')[0]==0
    final=ledger(c)
    assert calls==[1,2]
    assert sum(t.get('spends_retry') is True for t in final['trials'])==45
    assert not rounds.study_successes(final) and not final['selected']
    assert final['phase_clock']==first['phase_clock']
    assert cli(c,'init')[0]==2


@pytest.mark.parametrize('change',[
    {'parent_cell':'another_repeat'}, {'historical_attempts':15},
    {'files':[{'path':'code.py','source':'other-repeat','sha256':'0'*64}]},
])
def test_fresh_manifest_cannot_smuggle_parent_or_code(independent,change):
    c=independent
    p=Path(c.case['supervised_repair']['prior_input']['manifest'])
    d=json.loads(p.read_text());d.update(change);p.write_text(json.dumps(d))
    c.case['supervised_repair']['prior_input']['manifest_sha256']=auto.sha(p)
    c.path.write_text(json.dumps(c.case))
    assert cli(c,'init')[0]==2
    assert not (task_dir(c)/'development_state.json').exists()
