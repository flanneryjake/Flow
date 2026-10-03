"""Tests for apply_1451_ctx.py on COPIES: CTX1451_DIR=<folder with local_lane.py + fallback_lane.py> pytest -q"""
import ast
import hashlib
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get('CTX1451_DIR', HERE)
sys.path.insert(0, HERE)
import apply_1451_ctx as K  # noqa: E402

FILES = K.FILES


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


@pytest.fixture()
def rig(tmp_path):
    for n in FILES:
        shutil.copy2(os.path.join(SRC, n), tmp_path / n)
    return tmp_path


def state(d):
    return {n: sha(d / n) for n in FILES}


def test_check_writes_nothing(rig):
    s = state(rig)
    assert K.main(['--dir', str(rig), '--check']) == 0
    assert state(rig) == s and sorted(os.listdir(rig)) == sorted(FILES)


def test_apply_second_refused_revert_identical(rig):
    s = state(rig)
    assert K.main(['--dir', str(rig), '--apply']) == 0
    after = state(rig)
    assert all(after[n] != s[n] for n in FILES)
    assert K.main(['--dir', str(rig), '--apply']) == 0
    assert state(rig) == after
    assert K.main(['--dir', str(rig), '--revert']) == 0
    assert state(rig) == s


def test_anchor_miss_writes_nothing(rig):
    p = rig / 'fallback_lane.py'
    p.write_bytes(p.read_bytes().replace(b'"num_ctx": 16384', b'"num_ctx": 12000'))
    s = state(rig)
    assert K.main(['--dir', str(rig), '--apply']) == 1
    assert state(rig) == s and not [f for f in os.listdir(rig) if K.TAG in f]


def test_crlf_kept(rig):
    for n in FILES:
        p = rig / n
        p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    assert K.main(['--dir', str(rig), '--apply']) == 0
    for n in FILES:
        b = (rig / n).read_bytes()
        assert b.count(b'\n') == b.count(b'\r\n')


def ctx_expr(path):
    """Evaluate the num_ctx expression of the patched request body."""
    tree = ast.parse(open(path, encoding='utf-8').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == 'num_ctx':
                    return compile(ast.Expression(v), path, 'eval')
    raise AssertionError('no num_ctx')


@pytest.mark.parametrize('env,want', [('32768', 32768), ('', 16384), (None, 16384)])
def test_num_ctx_follows_server_default(rig, monkeypatch, env, want):
    assert K.main(['--dir', str(rig), '--apply']) == 0
    if env is None:
        monkeypatch.delenv('OLLAMA_CONTEXT_LENGTH', raising=False)
    else:
        monkeypatch.setenv('OLLAMA_CONTEXT_LENGTH', env)
    for n in FILES:
        assert eval(ctx_expr(str(rig / n)), {'os': os, 'int': int}) == want


def test_only_num_ctx_changed(rig):
    old = {n: (rig / n).read_text(encoding='utf-8') for n in FILES}
    assert K.main(['--dir', str(rig), '--apply']) == 0
    for n in FILES:
        new = (rig / n).read_text(encoding='utf-8')
        assert new.replace(K.NEW, K.OLD) == old[n]
        assert 'qwen3.6' not in K.NEW and 'LOCAL_MODEL' not in K.NEW   # model choice untouched (#1685)
