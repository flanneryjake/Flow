"""apply_windows_tests.py on the rig's sandbox_gate.py (WE_SNAPSHOTS=<dir with rig/agent>), then drive the patched
may_autopass() with a fake card folder."""
import importlib.util, os, shutil, subprocess, sys, tempfile, time
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get('WE_SNAPSHOTS', '')


@pytest.fixture(scope='module')
def gate():
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(SNAP, 'rig', 'agent', 'sandbox_gate.py'), d)
    assert subprocess.run([sys.executable, os.path.join(HERE, 'apply_windows_tests.py'), '--agent', d]).returncode == 0
    spec = importlib.util.spec_from_file_location('sandbox_gate_e6', os.path.join(d, 'sandbox_gate.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.pin_hits = lambda text: []          # the rig's classifier isn't here
    m._e6_is_admin = lambda: False
    yield m
    shutil.rmtree(d)


def card_folder(script_body, name='add-task.ps1'):
    f = tempfile.mkdtemp()
    with open(os.path.join(f, name), 'w') as s:
        s.write(script_body)
    time.sleep(0.01)
    with open(os.path.join(f, 'sandbox-cmd.sh'), 'w') as s:
        s.write(f'pwsh -File {name} -WhatIf\n')
    with open(os.path.join(f, 'sandbox-result.md'), 'w') as s:
        s.write('<!-- jarvis-sandbox verdict=PASS windows_only=yes timed_out=no id=x -->\n## Command\n')
    return f


SB = {'verdict': 'PASS', 'risk': 'MEDIUM', 'run': 'add-task.ps1', 'intent': 'add a user task', 'result': 'ok'}
HEAD = {'verdict': 'PASS', 'windows_only': 'yes', 'timed_out': 'no'}


def test_windows_only_medium_runs(gate):
    f = card_folder('Register-ScheduledTask -TaskName JarvisX -Action $a -Trigger $t')
    ok, why = gate.may_autopass({'task': 'x'}, SB, HEAD, f, 0, ['run add-task.ps1'])
    assert ok and why.endswith('add-task.ps1')


@pytest.mark.parametrize('body', ['Start-Process pwsh -Verb RunAs', 'New-ItemProperty HKLM:\\Software\\X',
                                  'New-NetFirewallRule -Name x', 'schtasks /create /ru SYSTEM /tn x',
                                  'Register-ScheduledTask x -RunLevel Highest', '#Requires -RunAsAdministrator'])
def test_admin_scripts_still_go_to_jake(gate, body):
    f = card_folder(body)
    ok, why = gate.may_autopass({'task': 'x'}, SB, HEAD, f, 0, ['run it'])
    assert not ok and 'Windows-only part not executed' in why


def test_worker_running_as_admin_refuses(gate):
    gate._e6_is_admin = lambda: True
    try:
        ok, why = gate.may_autopass({'task': 'x'}, SB, HEAD, card_folder('Write-Host hi'), 0, ['run it'])
        assert not ok and '#592' in why
    finally:
        gate._e6_is_admin = lambda: False


def test_high_risk_and_unknown_unchanged(gate):
    f = card_folder('Write-Host hi')
    assert not gate.may_autopass({'task': 'x'}, dict(SB, risk='HIGH'), HEAD, f, 0, ['x'])[0]
    assert not gate.may_autopass({'task': 'x'}, SB, dict(HEAD, windows_only='?'), f, 0, ['x'])[0]


def test_off_switch(gate, monkeypatch):
    monkeypatch.setenv('JARVIS_E6', 'off')
    ok, why = gate.may_autopass({'task': 'x'}, SB, HEAD, card_folder('Write-Host hi'), 0, ['x'])
    assert not ok and 'JARVIS_E6=off' in why


def test_revert_identical():
    d = tempfile.mkdtemp()
    src = os.path.join(SNAP, 'rig', 'agent', 'sandbox_gate.py')
    shutil.copy(src, d)
    run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, 'apply_windows_tests.py'), '--agent', d] + list(a))
    assert run().returncode == 0 and run('--revert').returncode == 0
    assert open(src, 'rb').read() == open(os.path.join(d, 'sandbox_gate.py'), 'rb').read()
