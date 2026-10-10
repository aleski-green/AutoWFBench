import copy
import unittest

from autowfbench.composer.n8n import validate_workflow
from autowfbench.runtime.apps import catalog


def workflow():
    return {'name':'test', 'nodes':[
        {'id':'1','name':'Start','type':'n8n-nodes-base.manualTrigger','typeVersion':1,'parameters':{},'position':[0,0]},
        {'id':'2','name':'Context','type':'n8n-nodes-base.set','typeVersion':3.4,'parameters':{},'position':[100,0]},
        {'id':'3','name':'Submit','type':'n8n-nodes-base.set','typeVersion':3.4,'parameters':{},'position':[200,0]}],
        'connections':{'Start':{'main':[[{'node':'Context','type':'main','index':0}]]},'Context':{'main':[[{'node':'Submit','type':'main','index':0}]]}}}


class ComposerTests(unittest.TestCase):
    def test_empty_duplicate_disconnected_and_invalid_edges_rejected(self):
        candidates=[]
        w=workflow();w['nodes']=[];candidates.append(w)
        w=workflow();w['nodes'][1]['id']='1';candidates.append(w)
        w=workflow();w['connections']['Context']['main']=[[]];candidates.append(w)
        w=workflow();w['connections']['Context']['main'][0][0]['node']='missing';candidates.append(w)
        for w in candidates:
            with self.assertRaises(ValueError):validate_workflow(w,catalog('crm'))

    def test_external_actions_and_privileged_nodes_rejected(self):
        w=workflow();n=w['nodes'][2]
        n.update(type='n8n-nodes-base.httpRequest',typeVersion=4.2,parameters={'method':'POST','url':'https://example.com'})
        with self.assertRaises(ValueError):validate_workflow(w,catalog('crm'))
        n.update(type='n8n-nodes-base.executeCommand',typeVersion=1)
        with self.assertRaises(ValueError):validate_workflow(w,catalog('crm'))
        n.update(type='n8n-nodes-base.code',typeVersion=2,parameters={'jsCode':"return require('fs').readFileSync('/tmp/secret');"})
        with self.assertRaises(ValueError):validate_workflow(w,catalog('crm'))

    def test_http_must_bind_to_run_context_and_documented_path(self):
        w=workflow();n=w['nodes'][2]
        n.update(type='n8n-nodes-base.httpRequest',typeVersion=4.2,parameters={'method':'POST','url':"={{ $('Context').first().json.environment.base_url + '/apps/crm/crm.read' }}",'headerParameters':{'parameters':[{'name':'Authorization','value':"={{ 'Bearer ' + $('Context').first().json.environment.access_token }}"}]}})
        validate_workflow(w,catalog('crm'))
        n['parameters']['headerParameters']['parameters'][0]['value']='Bearer fixed'
        with self.assertRaises(ValueError):validate_workflow(w,catalog('crm'))
        n['parameters']['url']="={{ $('Context').first().json.environment.base_url + '/admin/finalize' }}"
        with self.assertRaises(ValueError):validate_workflow(w,catalog('crm'))

if __name__=='__main__':unittest.main()
