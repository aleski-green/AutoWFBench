"""Adapter unit tests and explicit opt-in real Docker integration tests."""
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from autowfbench.core.common import ROOT, http_json, digest
from autowfbench.core.contracts import load_challenge
from autowfbench.runtime.engine import EnvironmentProcess
from autowfbench.runtime.environments.base import ManagedEnvironment, OperationError
from autowfbench.runtime.environments.checkout import CheckoutAdapter, PUBLIC_PATHS
from autowfbench.runtime.environments.sandbox import DockerSandbox
from autowfbench.runtime.verifiers.checkout import successful


CHALLENGE = "production-checkout-recovery-realistic"
OLD = 'prepared_order["currency"],\n                prepared_order["total"]'
NEW = 'prepared_order["total"],\n                prepared_order["currency"]'
SUMMARY = '\n'.join('# '+s+'\nObserved evidence.\n' for s in ('Root cause', 'Customer impact', 'Fix', 'Verification'))


class FakeSandbox:
    """Unit-test double only; never available through production configuration."""
    image_id = "sha256:unit-test-double"

    def __init__(self, workspace, *_):
        self.workspace = workspace
        self.requests = []

    def run(self, request):
        self.requests.append(copy.deepcopy(request))
        fixed = OLD not in (self.workspace / "app.py").read_text()
        if request['mode'] == 'tests':
            return {'passed': fixed, 'tests_run': 5, 'failures': int(not fixed), 'errors': 0}
        if request['mode'] == 'batch':
            responses = []
            for order in request['orders']:
                try:
                    valid = order['currency'] in ('USD', 'EUR') and bool(order['items'])
                    valid &= all(Decimal(i['unit_price']) > 0 and type(i['quantity']) is int and i['quantity'] > 0 for i in order['items'])
                    valid &= fixed or order['currency'] != 'EUR'
                    amount = sum(Decimal(i['unit_price']) * i['quantity'] for i in order['items'])
                except Exception:
                    valid = False
                responses.append({'status': 200, 'order_id': order['order_id'], 'payment_status': 'paid', 'payment': {'status': 'paid', 'amount': str(amount), 'currency': order['currency']}} if valid else {'status': 500})
            return {'responses': responses}
        return {'response': {'status': 200 if fixed else 500}}

    def close(self):
        pass


class RealisticAdapterTests(unittest.TestCase):
    def make(self, sandbox=FakeSandbox):
        package = load_challenge(CHALLENGE)
        adapter = CheckoutAdapter(package, sandbox_factory=sandbox)
        self.addCleanup(adapter.close)
        return ManagedEnvironment(package, adapter)

    def repair(self, env):
        return env.execute('checkout.patch', {'old': OLD, 'new': NEW})

    def test_fresh_workspaces_are_isolated_and_removed(self):
        a, b = self.make(), self.make()
        self.assertNotEqual(a.adapter.workspace, b.adapter.workspace)
        self.assertEqual(a.initial, b.initial)
        self.assertTrue(self.repair(a)['ok'])
        self.assertIn(OLD, b.adapter.snapshot()['files']['app.py'])
        workspace = a.adapter.workspace
        a.close()
        self.assertFalse(workspace.exists())

    def test_only_public_assets_copied(self):
        env = self.make()
        names = {str(p.relative_to(env.adapter.workspace)) for p in env.adapter.workspace.rglob('*') if p.is_file()}
        self.assertEqual(names, PUBLIC_PATHS)
        self.assertFalse(any('verifier' in p or 'scorecard' in p or '.env' in p for p in names))

    def test_discovery_exposes_only_public_contracts(self):
        env = self.make()
        value = env.execute('capabilities.list', {})['value']
        self.assertEqual({c['name'] for c in value['capabilities']}, set(env.package['definition']['capabilities']))
        for private in ('scorecard', 'asset_digests', 'access_token', 'eur_fixed'):
            self.assertNotIn(private, json.dumps(value))

    def test_path_and_capability_restrictions(self):
        env = self.make()
        for path in ('../scorecard.json', '/etc/passwd', 'verifier.py', '.env', 'tests/../app.py'):
            self.assertFalse(env.execute('source.read', {'path': path})['ok'])
        self.assertFalse(env.execute('source.read', {'path': 'app.py', 'extra': True})['ok'])
        self.assertFalse(env.execute('shell.exec', {'command': 'id'})['ok'])
        self.assertFalse(env.execute('artifact.write', {'path': 'payments.py', 'content': 'x'})['ok'])

    def test_symlinks_are_rejected(self):
        env = self.make()
        path = env.adapter.workspace / 'app.py'
        path.unlink()
        path.symlink_to(ROOT / 'benchmark-lock.json')
        self.assertEqual(env.execute('source.read', {'path': 'app.py'})['error']['code'], 'FORBIDDEN_PATH')

    def test_patch_rejects_invalid_ambiguous_and_module_changes(self):
        env = self.make()
        for old, new in ((OLD, OLD), ('absent', 'x'), ('payment', 'x'), (OLD, '('), ('from orders', 'import os\nfrom orders')):
            self.assertFalse(env.execute('checkout.patch', {'old': old, 'new': new})['ok'])
        self.assertEqual(env.initial, env.adapter.snapshot())

    def test_patch_records_hashes_and_actual_source_change(self):
        env = self.make()
        result = self.repair(env)
        self.assertTrue(result['ok'])
        self.assertNotEqual(result['value']['before_sha256'], result['value']['after_sha256'])
        self.assertNotIn(OLD, env.execute('source.read', {'path': 'app.py'})['value']['content'])
        self.assertEqual(env.events[-2]['data']['operation'], 'checkout.patch')

    def test_baseline_independent_verification_fails(self):
        evidence = self.make().finalize()
        self.assertFalse(evidence['checks']['eur_fixed'])
        self.assertFalse(evidence['checks']['safe_patch'])
        self.assertTrue(evidence['checks']['verification_available'])

    def test_recovery_trace_and_protected_checks(self):
        env = self.make()
        self.assertFalse(env.execute('tests.run', {})['value']['passed'])
        self.repair(env)
        self.assertTrue(env.execute('tests.run', {})['value']['passed'])
        env.execute('artifact.write', {'content': SUMMARY})
        evidence = env.finalize()
        self.assertTrue(all(evidence['checks'].values()), evidence)
        self.assertEqual(evidence, env.finalize())
        self.assertEqual(env.execute('source.read', {'path': 'app.py'})['error']['code'], 'RUN_CLOSED')

    def test_claimed_public_test_success_does_not_replace_verification(self):
        env = self.make()
        env.events.append({'data': {'operation': 'tests.run', 'result': {'ok': True, 'value': {'passed': True}}}})
        self.assertFalse(env.finalize()['checks']['eur_fixed'])

    def test_immutable_asset_tampering_is_detected(self):
        env = self.make()
        self.repair(env)
        (env.adapter.workspace / 'payments.py').write_text('tampered')
        checks = env.finalize()['checks']
        self.assertFalse(checks['safe_patch'])
        self.assertFalse(checks['regressions'])

    def test_tool_limit_and_errors_are_recorded(self):
        env = self.make()
        env.package['definition']['limits']['tool_calls'] = 1
        env.execute('capabilities.list', {})
        self.assertEqual(env.execute('tests.run', {})['error']['code'], 'TOOL_LIMIT')
        self.assertEqual(len(env.events), 2)

    def test_sandbox_failure_is_observable_and_not_a_pass(self):
        env = self.make()
        with patch.object(env.adapter.sandbox, 'run', side_effect=OperationError('SANDBOX_UNAVAILABLE', 'test unavailable')):
            self.assertEqual(env.execute('tests.run', {})['error']['code'], 'SANDBOX_UNAVAILABLE')
            evidence = env.finalize()
        self.assertFalse(evidence['checks']['verification_available'])
        self.assertIn('error', evidence['verification'][-1]['data'])

    def test_malformed_public_test_result_is_not_success(self):
        env = self.make()
        for result in ({}, {'passed': True, 'tests_run': 0}, {'passed': 'yes', 'tests_run': 5}):
            with patch.object(env.adapter.sandbox, 'run', return_value=result):
                self.assertFalse(env.execute('tests.run', {})['ok'])

    def test_verifier_rejects_malformed_and_wrong_charge_results(self):
        order = {'order_id': 'x', 'currency': 'EUR'}
        for response in (None, {}, {'payment': []}, {'payment': {'amount': 'NaN'}}, {'payment': {'amount': True}}):
            self.assertFalse(successful(response, order, '1.00'))
        env = self.make()
        with patch.object(env.adapter.sandbox, 'run', return_value={'responses': []}):
            self.assertFalse(env.finalize()['checks']['verification_available'])

    def test_asset_and_verifier_hashes_are_bound_to_package(self):
        package = load_challenge(CHALLENGE)
        assets = package['environment']['asset_digests']
        for name in ('autowfbench/runtime/verifiers/checkout.py', 'autowfbench/runtime/environments/worker.py', f'benchmark/challenges/{CHALLENGE}/assets/app.py'):
            self.assertEqual(assets[name], digest((ROOT / name).read_text()))
        original = package['hashes']['environment']
        package['environment']['asset_digests']['test'] = 'different'
        self.assertNotEqual(original, digest(package['environment']))


class DockerCommandTests(unittest.TestCase):
    def test_output_limit_and_timeout_kill_cli_process(self):
        real_popen = subprocess.Popen
        sandbox = DockerSandbox('/tmp', 'image')
        for script, code, timeout in (("print('x' * 210000)", 'OUTPUT_LIMIT', 2), ('import time; time.sleep(2)', 'EXECUTION_TIMEOUT', .05)):
            def launch(_command, **kwargs):
                return real_popen([sys.executable, '-c', script], **kwargs)
            with patch('autowfbench.runtime.environments.sandbox.subprocess.Popen', side_effect=launch):
                with self.assertRaises(OperationError) as error:
                    sandbox._docker(['test-helper'], timeout=timeout)
            self.assertEqual(error.exception.code, code)

    def test_execution_failure_still_removes_container(self):
        sandbox = DockerSandbox('/tmp', 'image')
        sandbox.image_id = 'sha256:verified'
        with patch.object(sandbox, '_docker', side_effect=[OperationError('EXECUTION_TIMEOUT', 'timeout'), subprocess.CompletedProcess([], 0, '')]) as docker:
            with self.assertRaises(OperationError):
                sandbox.run({'mode': 'tests'})
        self.assertEqual(docker.call_args_list[-1].args[0][:2], ['rm', '-f'])
        self.assertFalse(sandbox.active)

    def test_failed_cleanup_stays_tracked_and_close_retries(self):
        sandbox = DockerSandbox('/tmp', 'image')
        sandbox.active.add('test-container')
        results = [subprocess.CompletedProcess([], 1, 'daemon error'), subprocess.CompletedProcess([], 0, 'removed')]
        with patch.object(sandbox, '_docker', side_effect=results):
            with self.assertRaises(OperationError) as error:
                sandbox._remove('test-container')
            self.assertEqual(error.exception.code, 'CLEANUP_FAILED')
            self.assertEqual(sandbox.active, {'test-container'})
            sandbox.close()
        self.assertFalse(sandbox.active)

    def test_restricted_container_command_and_immutable_image_id(self):
        with tempfile.TemporaryDirectory() as workspace:
            sandbox = DockerSandbox(workspace, 'python:3.11-slim')
            results = [subprocess.CompletedProcess([], 0, '1.0\n'), subprocess.CompletedProcess([], 0, 'sha256:verified\n'), subprocess.CompletedProcess([], 0, 'container-id'), subprocess.CompletedProcess([], 0, '{"response":{"status":500}}'), subprocess.CompletedProcess([], 0, '')]
            with patch.object(sandbox, '_docker', side_effect=results) as docker:
                self.assertEqual(sandbox.run({'mode': 'observe', 'order': {}})['response']['status'], 500)
            command = docker.call_args_list[2].args[0]
            self.assertEqual(command[0], 'create')
            self.assertEqual(docker.call_args_list[3].args[0][:3], ['start', '--attach', '--interactive'])
            self.assertLessEqual(docker.call_args_list[3].kwargs['timeout'], sandbox.timeout)
            for flag in ('--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user=65534:65534', '--pull=never', 'sha256:verified'):
                self.assertIn(flag, command)
            mounts = [command[i+1] for i, flag in enumerate(command) if flag == '--mount']
            self.assertEqual(len(mounts), 2)
            self.assertTrue(all(m.endswith(',readonly') for m in mounts))
            self.assertFalse(any('verifiers' in m or '.docker' in m or 'scorecard' in m for m in mounts))
            self.assertEqual(docker.call_args_list[-1].args[0][:2], ['rm', '-f'])

    def test_missing_daemon_never_executes_application(self):
        sandbox = DockerSandbox('/tmp', 'image')
        with patch.object(sandbox, '_docker', return_value=subprocess.CompletedProcess([], 1, '')) as docker:
            with self.assertRaisesRegex(OperationError, 'daemon'):
                sandbox.run({'mode': 'tests'})
        self.assertEqual(docker.call_count, 1)

    def test_missing_image_never_pulls_implicitly(self):
        sandbox = DockerSandbox('/tmp', 'image')
        with patch.object(sandbox, '_docker', side_effect=[subprocess.CompletedProcess([], 0, '1'), subprocess.CompletedProcess([], 1, '')]) as docker:
            with self.assertRaisesRegex(OperationError, 'image'):
                sandbox.run({'mode': 'tests'})
        self.assertEqual(docker.call_count, 2)


class RunScopedHTTPTests(unittest.TestCase):
    def test_real_child_process_inspection_patch_auth_and_freeze(self):
        process = EnvironmentProcess(CHALLENGE, 0)
        try:
            def call(op, args):
                return http_json(process.public_url + '/tools', {'operation': op, 'arguments': args}, process.run_token)
            self.assertTrue(call('capabilities.list', {})['ok'])
            self.assertIn(OLD, call('source.read', {'path': 'app.py'})['value']['content'])
            self.assertTrue(call('checkout.patch', {'old': OLD, 'new': NEW})['ok'])
            self.assertNotIn(OLD, call('source.read', {'path': 'app.py'})['value']['content'])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                http_json(process.public_url + '/admin/finalize', {}, process.run_token)
            self.assertEqual(caught.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError):
                http_json(process.public_url + '/tools', {'operation': 'capabilities.list'}, 'wrong')
            evidence = process.finalize()
            self.assertTrue(evidence['checks']['safe_patch'])
            self.assertEqual(call('tests.run', {})['error']['code'], 'RUN_CLOSED')
        finally:
            process.close()


@unittest.skipUnless(os.environ.get('AWB_TEST_DOCKER') == '1', 'Set AWB_TEST_DOCKER=1 for actual container execution; requires Docker and image')
class DockerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = load_challenge(CHALLENGE)['environment']['runtime']
        try:
            DockerSandbox('/tmp', runtime['image']).preflight()
        except OperationError as exc:
            raise RuntimeError('Real Docker integration blocked: ' + str(exc)) from exc

    def test_real_application_recovery_and_independent_verification(self):
        package = load_challenge(CHALLENGE)
        adapter = CheckoutAdapter(package)
        self.addCleanup(adapter.close)
        env = ManagedEnvironment(package, adapter)
        self.assertFalse(env.execute('tests.run', {})['value']['passed'])
        self.assertTrue(env.execute('checkout.patch', {'old': OLD, 'new': NEW})['ok'])
        self.assertTrue(env.execute('tests.run', {})['value']['passed'])
        env.execute('artifact.write', {'content': SUMMARY})
        self.assertTrue(all(env.finalize()['checks'].values()))

    def test_candidate_code_cannot_read_controller_files_or_secrets(self):
        package = load_challenge(CHALLENGE)
        adapter = CheckoutAdapter(package)
        self.addCleanup(adapter.close)
        env = ManagedEnvironment(package, adapter)
        source = adapter.snapshot()['files']['app.py']
        body = source[source.index('    """Charge'):]
        probe = '''    import os
    from pathlib import Path
    return {"secrets": [k for k in os.environ if k.startswith("AWB_")],
            "protected": [str(p) for p in Path("/app").rglob("*") if p.name in ("verifier.py", "scorecard.json", ".env")],
            "controller_exists": Path("/autowfbench/runtime/verifiers/checkout.py").exists()}
'''
        self.assertTrue(env.execute('checkout.patch', {'old': body, 'new': probe})['ok'])
        with patch.dict(os.environ, {'AWB_ENV_ADMIN_TOKEN': 'must-not-enter-container'}):
            result = env.execute('checkout.observe', {'order': {}})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['value']['response'], {'secrets': [], 'protected': [], 'controller_exists': False})

    def test_infinite_loop_is_stopped(self):
        package = load_challenge(CHALLENGE)
        adapter = CheckoutAdapter(package)
        self.addCleanup(adapter.close)
        env = ManagedEnvironment(package, adapter)
        adapter.sandbox.preflight()
        adapter.sandbox.timeout = 1
        source = adapter.snapshot()['files']['app.py']
        body = source[source.index('    """Charge'):]
        self.assertTrue(env.execute('checkout.patch', {'old': body, 'new': '    while True:\n        pass\n'})['ok'])
        with patch.object(adapter.sandbox, '_docker', wraps=adapter.sandbox._docker) as docker:
            self.assertEqual(env.execute('checkout.observe', {'order': {}})['error']['code'], 'EXECUTION_TIMEOUT')
        removed = docker.call_args_list[-1].args[0]
        self.assertEqual(removed[:2], ['rm', '-f'])
        inspection = adapter.sandbox._docker(['inspect', removed[-1]])
        self.assertNotEqual(inspection.returncode, 0)
        self.assertIn('No such', inspection.stdout)


if __name__ == '__main__':
    unittest.main()
