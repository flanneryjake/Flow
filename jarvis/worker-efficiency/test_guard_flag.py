"""apply_guard_flag.py on the rig agent.py snapshot (WE_SNAPSHOTS=<dir with rig/agent>), then drive code_restore."""
import os, shutil, subprocess, sys, tempfile, time, types

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get('WE_SNAPSHOTS', '')


def _installed():
    d = tempfile.mkdtemp()
    src = os.path.join(SNAP, 'rig', 'agent', 'agent.py')
    shutil.copy(src, d)
    flag = os.path.join(d, 'audit', 'fixes-running.flag')
    run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, 'apply_guard_flag.py'), '--agent', d,
                                     '--flag', flag] + list(a), capture_output=True, text=True)
    return d, src, flag, run


def _code_restore(d, flag):
    s = open(os.path.join(d, 'agent.py'), encoding='utf-8').read()
    i = s.index('def code_restore(c, snap):')
    j = s.index('\ndef ', i + 10)
    logs, guarded = [], os.path.join(d, 'guarded.py')
    g = {'os': os, 'STATE': {}, 'log': logs.append, 'card_dir': lambda c: d, '_guarded_files': lambda: [guarded]}
    os.environ['JARVIS_FIX_FLAG'] = flag
    exec(s[i:j], g)
    return g, logs, guarded


def test_check_apply_twice_revert():
    d, src, flag, run = _installed()
    assert run('--check').returncode == 0
    assert not os.path.exists(flag)
    assert run().returncode == 0 and os.path.exists(flag)
    r = run()
    assert r.returncode == 0 and 'already installed' in r.stdout
    assert run('--revert').returncode == 0
    assert open(src, 'rb').read() == open(os.path.join(d, 'agent.py'), 'rb').read()


def test_flag_touched_during_card_keeps_fix():
    d, src, flag, run = _installed()
    assert run().returncode == 0
    g, logs, guarded = _code_restore(d, flag)
    open(guarded, 'wb').write(b'old')
    snap = {guarded: b'old'}
    g['STATE']['run_t0'] = time.time()
    open(guarded, 'wb').write(b'fixed by claude')
    os.utime(flag, None)
    assert g['code_restore']({'number': 1060}, snap) == ''
    assert open(guarded, 'rb').read() == b'fixed by claude'
    assert any('not reverting' in m for m in logs)


def test_card_edit_without_flag_still_reverted():
    d, src, flag, run = _installed()
    assert run().returncode == 0
    g, logs, guarded = _code_restore(d, flag)
    old = time.time() - 3600
    os.utime(flag, (old, old))
    open(guarded, 'wb').write(b'old')
    snap = {guarded: b'old'}
    g['STATE']['run_t0'] = time.time()
    open(guarded, 'wb').write(b'card rewrote me')
    out = g['code_restore']({'number': 1060}, snap)
    assert 'Code patch staged' in out
    assert open(guarded, 'rb').read() == b'old'
