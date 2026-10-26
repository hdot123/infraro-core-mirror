"""setup-venv composite action 契约测试（预装环境锁定第 3 层：fast-fail 加固）

checkout 残缺（runner 工作区中毒，2026-08-27 runner-02/03 事件）时，
composite 第一步必须立即以可操作错误失败，不让下游报迷惑性错误。
"""

from pathlib import Path

import yaml

ACTION_YAML = (
    Path(__file__).resolve().parents[1] / ".github" / "actions" / "setup-venv" / "action.yml"
)


def _load_action():
    return yaml.safe_load(ACTION_YAML.read_text(encoding="utf-8"))


def test_fast_fail_is_first_step():
    """fast-fail 步骤必须是 composite 的第一步"""
    steps = _load_action()["runs"]["steps"]
    assert steps[0]["name"] == "Fast-fail on incomplete checkout"
    assert steps[0]["shell"] == "bash"


def test_fast_fail_checks_key_files():
    """检查三件关键文件：.git / pyproject.toml / setup-venv action.yml 自身"""
    run = _load_action()["runs"]["steps"][0]["run"]
    for key in (".git", "pyproject.toml", ".github/actions/setup-venv/action.yml"):
        assert key in run, f"fast-fail 未检查关键文件：{key}"


def test_fast_fail_carries_actionable_message():
    """错误消息含 'checkout 残缺' 指引（rerun / 清工作区）并以非零退出"""
    run = _load_action()["runs"]["steps"][0]["run"]
    assert "checkout 残缺" in run
    assert "rerun" in run or "重跑" in run
    assert "exit 1" in run


def test_cache_env_persisted_to_github_env():
    """UV/PIP 缓存目录必须写入 $GITHUB_ENV（composite 步骤间不共享 shell export，

    仅 export 的值在后续 step 中经 ${{ env.* }} 取到空串——2026-10 缓存 env bug）
    """
    run = _load_action()["runs"]["steps"][1]["run"]
    # hosted 分支：RUNNER_TEMP 路径以单行形式写入 GITHUB_ENV（round-5 P1
    # shard-22：断言绑定到 `echo <VAR>=<path> >> "$GITHUB_ENV"` 单行写入形态；
    # 旧断言被无关行拆开满足，删掉真实写入行测试仍绿）
    assert 'UV_CACHE_DIR=$RUNNER_TEMP/uv-cache" >> "$GITHUB_ENV"' in run
    assert 'PIP_CACHE_DIR=$RUNNER_TEMP/pip-cache" >> "$GITHUB_ENV"' in run
    for var in ("UV_CACHE_DIR", "PIP_CACHE_DIR"):
        # self-hosted 分支：全局缓存路径写入 GITHUB_ENV
        assert f"{var}=/var/cache/" in run
    # self-hosted 写入点必须落在 else 分支的 GITHUB_ENV 追加（防止只 export 不落盘）
    assert 'UV_CACHE_DIR=/var/cache/uv" >> "$GITHUB_ENV"' in run
    assert 'PIP_CACHE_DIR=/var/cache/pip" >> "$GITHUB_ENV"' in run


def test_fast_fail_uses_github_error_annotation():
    """使用 ::error:: 注解让失败在 GitHub UI 醒目可见"""
    run = _load_action()["runs"]["steps"][0]["run"]
    assert "::error::" in run
