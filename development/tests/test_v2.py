import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fastjsonschema

from autowfbench.core.common import digest
from autowfbench.core.contracts import load_challenge
from autowfbench.core.scoring import calculate
from autowfbench.interfaces.solution import solve
from autowfbench.runtime.apps import catalog
from autowfbench.runtime.environment import ChallengeEnvironment

CRM = 'crm-lead-qualification'
INC = 'production-checkout-recovery'


def reference(challenge, seed=0):
    env = ChallengeEnvironment(load_challenge(challenge), seed)
    def http(url, data, token):
        return env.execute(data['operation'], data['arguments'])
    with patch('autowfbench.interfaces.solution.http_json', side_effect=http):
        submission = solve({'challenge': {'id': challenge}, 'run_id': 'test', 'environment': {'base_url': '', 'access_token': ''}})
    return env, submission


class V2Tests(unittest.TestCase):
    def test_crm_judge_anchors_do_not_require_checkout_artifacts(self):
        card=load_challenge(CRM)['scorecard']
        text=json.dumps([c['anchors'] for c in card['criteria'] if c['evaluator']=='llm']).lower()
        self.assertNotIn('checkout',text)
        self.assertNotIn('incident-summary',text)
        self.assertIn('artifacts are optional',text)

    def test_reference_handles_all_scenarios_and_varied_values(self):
        for challenge in (CRM, INC):
            for seed in range(12):
                with self.subTest(challenge=challenge, seed=seed):
                    env, _ = reference(challenge, seed)
                    evidence = env.finalize()
                    self.assertTrue(all(evidence['checks'].values()), evidence['checks'])

    def test_exported_schemas_match_real_observations(self):
        for challenge,kind in ((CRM,'crm'),(INC,'checkout')):
            validators = {op['operation']:fastjsonschema.compile(op['result']) for op in catalog(kind)['operations']}
            for seed in range(6):
                env,_ = reference(challenge,seed)
                for event in env.events:
                    data = event['data']
                    if data['result']['ok']:
                        validators[data['operation']](data['result']['value'])

    def test_ambiguous_commit_is_idempotent_and_conflicting_key_rejected(self):
        env = ChallengeEnvironment(load_challenge(CRM), 5)
        args = {'changes':{'status':'Qualified'}, 'idempotency_key':'same'}
        first = env.execute('crm.update', args)
        self.assertEqual(first['error']['code'], 'AMBIGUOUS_COMMIT')
        self.assertEqual(env.execute('crm.read', {})['value']['status'], 'Qualified')
        self.assertTrue(env.execute('crm.update', args)['ok'])
        self.assertEqual(env.mutations.count('crm.update'), 1)
        conflict = env.execute('crm.update', {'changes':{'status':'NotQualified'},'idempotency_key':'same'})
        self.assertEqual(conflict['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(env.state['lead']['status'], 'Qualified')

    def test_unknown_fields_and_types_fail_before_mutation(self):
        for change in ({'company':'replacement'}, {'budget_aed':'180000'}, {'status':'qualified'}, {'timeline_weeks':0}):
            env=ChallengeEnvironment(load_challenge(CRM))
            self.assertFalse(env.execute('crm.update',{'changes':change,'idempotency_key':'x'})['ok'])
            self.assertEqual(env.state['lead'],env.initial['lead'])

    def test_retrying_permanent_errors_fails_recovery_even_without_state_damage(self):
        env,_=reference(CRM,4)
        env.execute('crm.update',{'changes':{'status':'Qualified'},'idempotency_key':'retry'})
        env.execute('crm.read',{})
        self.assertFalse(env.finalize()['checks']['recovery'])

    def test_duplicate_followups_and_unverified_writes_fail(self):
        env,_=reference(CRM)
        record=env.state['followups'][0]
        env.execute('followup.create',{k:v for k,v in record.items() if k!='followup_id'} | {'idempotency_key':'duplicate'})
        env.execute('followup.read',{})
        self.assertFalse(env.finalize()['checks']['correct_followup'])
        env,_=reference(CRM)
        env.events=[e for e in env.events if e['data']['operation']!='crm.read']
        self.assertFalse(env.finalize()['checks']['recovery'])

    def test_fixed_qualified_answer_fails_negative_and_missing_fact_cases(self):
        for seed in (2,3,8,9):
            env,_=reference(CRM,seed)
            env.state['lead']['status']='Qualified'
            self.assertFalse(env.finalize()['checks']['correct_lead'])

    def test_score_gates_prevent_good_prose_hiding_critical_failure(self):
        for challenge in (CRM, INC):
            env,submission=reference(challenge)
            evidence=env.finalize()
            card=load_challenge(challenge)['scorecard']
            events=evidence['events']+evidence['verification']+[{'id':'candidate-final','source':'candidate'}]
            run={'run_id':'test','checks':evidence['checks'],'termination_reason':'completed','events':events}
            response={'run_id':'test','scorecard_digest':digest(card),'status':'complete','issues':[],'criteria':[{'criterion_id':c['id'],'answer':'yes','reason':'Synthetic calibration fixture, not an LLM result','evidence_refs':[next(e['id'] for e in events if e['source']==source) for source in c['required_evidence']]} for c in card['criteria'] if c['evaluator']=='llm']}
            self.assertEqual(calculate(run,card,response)['score_0_10'],10)
            for criterion in card['criteria']:
                if criterion['id'] not in card['gates']:
                    continue
                r,j=copy.deepcopy(run),copy.deepcopy(response)
                if criterion['evaluator']=='deterministic':
                    r['checks'][criterion['check']]=False
                else:
                    next(c for c in j['criteria'] if c['criterion_id']==criterion['id'])['answer']='no'
                self.assertLess(calculate(r,card,j)['score_0_10'],8,criterion['id'])
            run['checks']['efficient']=False
            self.assertEqual(calculate(run,card,response)['score_0_10'],9)
            self.assertTrue(calculate(run,card,response)['execution_pass'])

    def test_empty_attempt_cannot_pass_even_with_all_yes_judge(self):
        for challenge in (CRM,INC):
            checks=ChallengeEnvironment(load_challenge(challenge)).finalize()['checks']
            self.assertFalse(checks['evidence_gathered'])
            self.assertFalse(checks['correct_lead'] if challenge==CRM else checks['checkout_correct'])

if __name__=='__main__':
    unittest.main()
