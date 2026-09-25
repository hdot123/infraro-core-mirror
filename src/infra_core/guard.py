"""引擎入口守卫：校验调用仓授权状态。

入口守卫是引擎管线（droid-review / auto-merge / branch-cleanup）的首要门禁，
在任何引擎能力调用前执行。判定依据：读调用仓的 repo variable `ENGINE_CONSUMERS`，
值为 `authorized` 时放行，否则 fail-closed 拒绝。

设计约束（VAL-M5-014）：
- fail-closed：读取异常或缺失时拒绝，绝不静默放行
- 最小权限：仅读 GITHUB_TOKEN 的 actions:read（读 repo variable）
- 零 PAT：不依赖长期 PAT，仅消费 GITHUB_TOKEN
- 引擎仓豁免：hdot123/infraro-core 自身永不拦（避免循环）
- vars 优先：优先读 repo variable（零 API 调用），gh API 作为回退
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# ── 治理基线（与 workflow 内联脚本文本锚定）─────────────────────────────────
VARIABLE_NAME = "ENGINE_CONSUMERS"
AUTHORIZED_TOKEN = "authorized"
EXEMPT_REPOS = ("hdot123/infraro-core",)  # 引擎仓自身豁免
FAIL_MESSAGE = "未授权消费引擎，走授权流程"
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GuardVerdict:
    """守卫判定结果"""

    allowed: bool
    repo: str
    variable_name: str
    variable_value: str | None
    source: str  # "vars" | "api" | "exempt" | "error"
    reason: str


def check_authorization(
    caller_repo: str,
    variable_name: str = VARIABLE_NAME,
    authorized_token: str = AUTHORIZED_TOKEN,
    exempt_repos: tuple[str, ...] = EXEMPT_REPOS,
) -> GuardVerdict:
    """判定调用仓是否有权消费引擎能力

    判定表（fail-closed）：
    - repo 在豁免列表 → 放行（engine repo itself）
    - vars 上下文有值且为 `authorized` → 放行（优先，零 API）
    - vars 缺失 → 拒绝（variable 不存在）
    - vars 值不为 authorized → 拒绝
    - vars 读取异常（非 404）→ 拒绝（fail-closed）
    - vars 回退到 gh api → 尝试 API 回退（ิง permissions 403 也拒）

    返回结构包含诊断信息（_source, _value, _reason）便于日志审计。
    """
    # 引擎仓自身豁免（避免循环）
    if caller_repo in exempt_repos:
        return GuardVerdict(
            allowed=True,
            repo=caller_repo,
            variable_name=variable_name,
            variable_value=None,
            source="exempt",
            reason=f"引擎仓自身（{caller_repo}），豁免",
        )

    # 优先读 vars 上下文（零权限、零 API 调用）
    vars_value = _read_vars_context(variable_name)
    if vars_value is not None:
        if vars_value == authorized_token:
            return GuardVerdict(
                allowed=True,
                repo=caller_repo,
                variable_name=variable_name,
                variable_value=vars_value,
                source="vars",
                reason=f"variable {variable_name} 值为 authorized（@vars），放行",
            )
        else:
            return GuardVerdict(
                allowed=False,
                repo=caller_repo,
                variable_name=variable_name,
                variable_value=vars_value,
                source="vars",
                reason=(
                    f"variable {variable_name} 值为 {vars_value!r}（非 {authorized_token}），拒绝"
                ),
            )

    # vars 缺失或回退到 API（GITHUB_TOKEN 实测 403）
    api_value = _read_via_api(caller_repo, variable_name)
    if api_value is not None:
        if api_value == authorized_token:
            return GuardVerdict(
                allowed=True,
                repo=caller_repo,
                variable_name=variable_name,
                variable_value=api_value,
                source="api",
                reason=f"variable {variable_name} 值为 authorized（@api），放行",
            )
        else:
            return GuardVerdict(
                allowed=False,
                repo=caller_repo,
                variable_name=variable_name,
                variable_value=api_value,
                source="api",
                reason=(
                    f"variable {variable_name} 值为 {api_value!r}（非 {authorized_token}），拒绝"
                ),
            )

    # API 回退也失败（variable 缺失或读取异常）
    if api_value is None:
        return GuardVerdict(
            allowed=False,
            repo=caller_repo,
            variable_name=variable_name,
            variable_value=None,
            source="error",
            reason=f"variable {variable_name} 缺失或读取异常（@api），拒绝",
        )

    # 理论上不会走到这里
    return GuardVerdict(
        allowed=False,
        repo=caller_repo,
        variable_name=variable_name,
        variable_value=None,
        source="error",
        reason="判定逻辑异常，拒绝",
    )


def _read_vars_context(variable_name: str) -> str | None:
    """读取 vars 上下文注入值（零权限路径，GitHub Actions 运行时注入）

    GITHUB 在 workflow 运行时将 repo variables 注入为环境变量：
    `ENGINE_CONSUMERS` → `os.environ.get("ENGINE_CONSUMERS")`

    返回 None 表示变量不存在（会触发 @api 回退）。
    """
    return os.environ.get(variable_name)


def _read_via_api(repo: str, variable_name: str) -> str | None:
    """通过 gh API 回退读取 repo variable（GITHUB_TOKEN 尝试 actions:read）

    GITHUB_TOKEN 实测对 `/repos/{o}/{r}/actions/variables/{name}` 返回 403，
    故此路径仅为设计稿（瓣式入口）。返回 None 表示读取失败。
    """
    import subprocess

    cmd = [
        "gh",
        "api",
        f"repos/{repo}/actions/variables/{variable_name}",
        "--jq",
        ".value",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # 404 或其他错误都记为 None（触发 fail-closed）
            return None
        return result.stdout.strip()
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：供 workflow 调用

    用法：
        python -m infra_core.guard --repo <repo> [--variable-name <name>]
            [--authorized-token <token>]

    退出码：0 放行 / 1 拒绝
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="infra-guard-check",
        description="引擎入口守卫：校验调用仓授权状态（fail-closed）",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="调用仓 full_name（如 hdot123/consumer-a）",
    )
    parser.add_argument(
        "--variable-name",
        default=VARIABLE_NAME,
        help=f"变量名（默认 {VARIABLE_NAME}）",
    )
    parser.add_argument(
        "--authorized-token",
        default=AUTHORIZED_TOKEN,
        help=f"授权令牌值（默认 {AUTHORIZED_TOKEN}）",
    )
    parser.add_argument(
        "--exempt-repos",
        default=",".join(EXEMPT_REPOS),
        help=f"豁免仓列表（逗号分隔，默认 {','.join(EXEMPT_REPOS)}）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON 输出（含诊断信息）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式（不输出日志，仅退出码）",
    )
    args = parser.parse_args(argv)

    exempt_list = tuple(r.strip() for r in args.exempt_repos.split(",") if r.strip())

    verdict = check_authorization(
        caller_repo=args.repo,
        variable_name=args.variable_name,
        authorized_token=args.authorized_token,
        exempt_repos=exempt_list,
    )

    if not args.quiet:
        if verdict.source == "exempt":
            print(f"✓ {verdict.reason}")
        elif verdict.allowed:
            print(f"✓ {verdict.reason}")
        else:
            print(f"::error::{FAIL_MESSAGE}")
            print(f"  repo: {verdict.repo}")
            print(f"  variable: {verdict.variable_name}")
            print(f"  value: {verdict.variable_value or '<missing>'}")
            print(f"  source: {verdict.source}")
            print(f"  reason: {verdict.reason}")

    if args.json:
        import json

        output = {
            "allowed": verdict.allowed,
            "repo": verdict.repo,
            "variable_name": verdict.variable_name,
            "variable_value": verdict.variable_value,
            "source": verdict.source,
            "reason": verdict.reason,
        }
        print(json.dumps(output))

    return 0 if verdict.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
