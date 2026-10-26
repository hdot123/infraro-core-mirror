"""Guard: CI 工作流与本地 composite action 禁止出现内网 FQDN 明文（#314 事故守卫）。

背景（2026-10-11 事故）：#314 将 ``webhook.exa.edu.kg`` 明文写入 CI 通知 job 的
``WEBHOOK_URL`` 环境值并被 auto-merge 放行合入 main。本守卫把该洞永久堵上：

- 扫描 ``.github/workflows/`` 下全部 ``*.yml|*.yaml``（含嵌套子目录）与
  本地 composite action
  （常见落点目录 ``actions/**`` 与 ``.github/actions/**``，外加沿工作流/动作
  ``uses: ./...`` 本地引用递归闭包可达的全部 ``action.yml|yaml``——本地引用
  落点不受目录约定限制，如 ``scripts/my-action/``）；
- 任何非注释行出现受保护域名后缀（``exa.edu.kg`` 族，任意子域、scheme 可选、
  大小写不敏感、含根点记法）明文即 FAIL——此类地址只能以 vars/secrets 引用
  形态出现在工作流里；
- 注释行（去空白后以 ``#`` 开头）豁免；文档文件（*.md 等）不在扫描范围；
- 键位置出现的域名同样判违规（保守 fail-closed）。

双向取证：tmp_path 恶意夹具（#314 原式）必须命中；真实仓库树必须通过
（revert #315 后 main 上无明文残留）。
"""

import re
from pathlib import Path, PureWindowsPath

import pytest

pytestmark = [pytest.mark.security, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent

# 受保护域名后缀：内网网关域名族（含任意子域）。新增受保护后缀时在此追加。
PROTECTED_SUFFIXES = ("exa.edu.kg",)

# 主机名 label（RFC 1123 简化版，额外允许 SRV 风格前导下划线）
_LABEL = r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"

# FQDN 明文：可选 scheme + 任意子域 label 链 + 受保护后缀。
# re.IGNORECASE：DNS/URL host 语义大小写不敏感（``WEBHOOK.EXA.EDU.KG``、
#   ``HTTPS://Webhook.Exa.Edu.Kg`` 等变体同样命中，堵大小写绕过）。
# 首尾 lookaround 语义（域名后缀匹配：主机名以受保护后缀结尾才算自家域名）：
#   - 前视仅阻断 alnum/下划线/连字符接续（``xexa.edu.kg`` 不得命中），
#     允许前导点（zone 形态 ``.exa.edu.kg`` 仍命中）；
#   - 尾视仅阻断「继续下一个 label」（``exa.edu.kg.evil.com`` 的可注册域是
#     evil.com，非自家域名，不命中）；终端根点记法（``webhook.exa.edu.kg.``，
#     尾点后跟 /、空白或行尾）放行命中——绝对 FQDN 的根点写法不得绕过。
_FQDN_RES = [
    re.compile(
        r"(?<![A-Za-z0-9_-])"
        r"(?:https?://)?"
        rf"(?:{_LABEL}\.)*" + re.escape(suffix) + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])",
        re.IGNORECASE,
    )
    for suffix in PROTECTED_SUFFIXES
]


def scan_for_fqdn_violations(root: Path) -> list[str]:
    """扫描 root 下 CI 工作流与本地 composite action，返回违规清单。

    返回项格式：``相对路径:行号: FQDN 明文 → <命中文本> ← <行内容截断>``（路径
    一律 POSIX 分隔符渲染，跨平台断言稳定）。空列表 = 通过。

    规则：
    - 注释行（去空白后以 ``#`` 开头）整行豁免；其余行（含键位置）保守判命中；
    - 命中模式见 ``_FQDN_RES``（受保护后缀，scheme 可选，大小写不敏感）；
    - 匹配面为 ``_matchable_lines`` 可匹配行视图（物理行全集 ∪ shell 续行
      合并 ∪ 相邻行拼接补扫，物理行的真超集），跨行拆分形态不漏检。
    """
    resolved_root = root.resolve()
    violations: list[str] = []
    seen: set[str] = set()
    for path in _scan_targets(resolved_root):
        try:
            relative = path.relative_to(resolved_root).as_posix()
        except ValueError:
            continue  # uses 引用逃逸 root 的目标（仓库 checkout 外不可能落地），跳过
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in _matchable_lines(text):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue  # 注释行豁免
            for pattern in _FQDN_RES:
                for match in pattern.finditer(line):
                    entry = (
                        f"{relative}:{lineno}: FQDN 明文 → {match.group(0)!r} ← {stripped[:120]}"
                    )
                    if entry not in seen:
                        seen.add(entry)
                        violations.append(entry)
    return violations


def _matchable_lines(text: str) -> list[tuple[int, str]]:
    """可匹配行视图：物理行全集 ∪ 反斜杠续行合并 ∪ 相邻行拼接（物理行的真超集）。

    任何物理行都以其原始形态保留在视图里——注释豁免只在物理行级别判定
    （YAML 注释按行界定，行尾 ``\\`` 是普通字符，其后继是独立执行行），任何
    合并/拼接视图都不得吞掉物理执行行，否则 ``# docs \\`` 后随一行明文 URL
    即可绕过守卫（scrutiny round 1 实证回归）。在物理行全集之上补充两类
    跨行视图：
    - shell 反斜杠续行（行尾 ``\\``，执行时续行符与换行一并去除）与其后继
      的合并形态，行号取组首——跨行拆分的 URL 执行时是单一请求，须按合并
      后形态匹配；后继行的前导空白一并去除（YAML 块标量公共缩进在 shell
      看到的执行形态中已被剥离，保留它会把同一 token 人为断开）；
      **注释行不发起续行合并**（注释组仅含注释行自身）：bash 实证注释止于
      换行、注释尾反斜杠后继照常执行，若放任注释组吞并后继，拆分 URL 的
      代码续行会借注释豁免整体逃逸（droid-review P1 回归）；
    - 相邻组合行两两拼接（覆盖 YAML 折叠标量/无续行符的长行折断形态）作为
      补充行，行号取前一行——保守 fail-closed，宁多报不漏报；后继组合行
      为注释行时不拼接（注释内容属于豁免面，不得经拼接渗入非注释视图）。
    """
    physical = text.splitlines()
    view = [(i + 1, line) for i, line in enumerate(physical)]
    joined: list[tuple[int, str]] = []
    index = 0
    while index < len(physical):
        start = index + 1  # 1-based 物理行号
        line = physical[index]
        if not line.lstrip().startswith("#"):  # 注释行不发起续行合并
            while line.endswith("\\") and index + 1 < len(physical):
                index += 1
                line = line[:-1] + physical[index].lstrip()
        joined.append((start, line))
        index += 1
    extra = [
        (joined[k][0], joined[k][1] + joined[k + 1][1])
        for k in range(len(joined) - 1)
        if not joined[k + 1][1].lstrip().startswith("#")
    ]
    return view + joined + extra


def _scan_targets(root: Path) -> list[Path]:
    """扫描目标：工作流全量 + 本地 composite action 全可达集。

    - ``.github/workflows/`` 下全部 ``*.yml|yaml``（含嵌套子目录，GitHub
      会执行子目录工作流）；
    - 常见 action 落点目录：``actions/**`` 与 ``.github/actions/**`` 下的
      ``action.yml|yaml``；
    - 沿 ``uses: ./...`` 本地引用递归闭包：工作流 → action → 嵌套 action，
      覆盖任意非常规落点（如 ``scripts/my-action/action.yml``）——本地引用
      的落点不受目录约定限制，须沿引用链追齐。
    """
    targets: set[Path] = set()
    workflows_dir = root / ".github" / "workflows"
    if workflows_dir.is_dir():
        targets.update(
            p for p in workflows_dir.rglob("*") if p.is_file() and p.suffix in {".yml", ".yaml"}
        )
    for actions_dir in (root / "actions", root / ".github" / "actions"):
        if actions_dir.is_dir():
            targets.update(actions_dir.rglob("action.yml"))
            targets.update(actions_dir.rglob("action.yaml"))
    # uses 本地引用闭包：新目标入队，其自身的 uses 继续展开至不动点
    queue = sorted(targets)
    expanded: set[Path] = set()
    while queue:
        path = queue.pop()
        if path in expanded:
            continue
        expanded.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for referenced in _resolve_local_uses(root, path, text):
            if referenced not in targets:
                targets.add(referenced)
                queue.append(referenced)
    return sorted(targets)


# 工作流/composite action 里的本地引用形态：uses: ./.github/actions/foo 等。
# 前视 lookbehind 保证 uses 独立成键（myuses:/plus 等形态不触发）。
_USES_LOCAL_RE = re.compile(r"""(?<![A-Za-z0-9_-])uses\s*:\s*["']?(\./[^\s"'#]+)""")


def _resolve_local_uses(root: Path, source: Path, text: str) -> set[Path]:
    """解析文本中 ``uses: ./...`` 本地引用 → root 下的目标文件集合。

    GitHub 语义：引用目录 → 纳入目录内 ``action.yml|action.yaml``；
    引用 ``.yml|yaml`` 文件（本地可复用工作流形态）→ 直接纳入。
    以 root 相对解析为主（``uses`` 本地路径相对仓库根），引用文件同目录
    解析为辅（fail-closed：宁多扫不漏扫）。
    """
    resolved: set[Path] = set()
    for match in _USES_LOCAL_RE.finditer(text):
        rel = match.group(1).rstrip("/")
        for base in (root, source.parent):
            candidate = (base / rel).resolve()
            if candidate.is_dir():
                for name in ("action.yml", "action.yaml"):
                    action = candidate / name
                    if action.is_file():
                        resolved.add(action)
            elif candidate.is_file() and candidate.suffix in {".yml", ".yaml"}:
                resolved.add(candidate)
    return resolved


# ─── 夹具构造 ───────────────────────────────────────────────────────────

# #314 事故原式（当时 ci.yml 通知 job 的明文写法，两处之一）
PR314_SNIPPET = """\
name: ci
on: [push, pull_request]
jobs:
  notify-ci-complete:
    runs-on: ubuntu-latest
    steps:
      - name: Send CI status webhook
        env:
          WEBHOOK_URL: https://webhook.exa.edu.kg/hooks/ci-complete
          N8N_CI_TOKEN: ${{ secrets.N8N_CI_TOKEN }}
        run: curl -s -X POST "$WEBHOOK_URL"
"""


def _write_fixture(tmp_path: Path, rel: str, text: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ─── 恶意夹具：守卫必须命中 ─────────────────────────────────────────────


def test_guard_flags_pr314_style_hardcoded_url(tmp_path):
    """#314 原式明文 URL（带 scheme）出现在环境值位置 → 必须命中。"""
    _write_fixture(tmp_path, ".github/workflows/ci.yml", PR314_SNIPPET)

    violations = scan_for_fqdn_violations(tmp_path)

    assert violations, "守卫未命中 #314 式写死 URL——检测逻辑失效"
    assert any("webhook.exa.edu.kg" in v for v in violations)
    assert any(".github/workflows/ci.yml" in v for v in violations)


def test_guard_flags_bare_domain_without_scheme(tmp_path):
    """更泛模式：无 scheme 的裸域名（子域形态）在值位置同样命中。"""
    _write_fixture(
        tmp_path,
        ".github/workflows/notify.yml",
        "jobs:\n"
        "  notify:\n"
        "    steps:\n"
        "      - run: curl -X POST ci-webhook.exa.edu.kg/hooks/ci-complete\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert violations, "守卫未命中裸域名（无 scheme）写法"
    assert any("ci-webhook.exa.edu.kg" in v for v in violations)


def test_guard_covers_local_composite_actions(tmp_path):
    """本地 composite action（actions/** 与 .github/actions/**）也在扫描范围。"""
    _write_fixture(
        tmp_path,
        "actions/branch-cleanup/action.yml",
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - run: curl https://webhook.exa.edu.kg/hooks/ci-complete\n"
        "      shell: bash\n",
    )
    _write_fixture(
        tmp_path,
        ".github/actions/setup-venv/action.yml",
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - run: echo https://webhook.exa.edu.kg\n"
        "      shell: bash\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert any("actions/branch-cleanup/action.yml" in v for v in violations), (
        "根级 actions/**/action.yml 未被扫描"
    )
    assert any(".github/actions/setup-venv/action.yml" in v for v in violations), (
        ".github/actions/**/action.yml 未被扫描"
    )


def test_guard_flags_mixed_case_fqdn(tmp_path):
    """大小写变体（DNS/URL host 语义大小写不敏感）不得绕过检测——review P1。"""
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n"
        "  notify:\n"
        "    steps:\n"
        "      - run: curl -s -X POST HTTPS://WEBHOOK.EXA.EDU.KG/hooks/ci-complete\n"
        "      - run: curl -s -X POST https://Webhook.Exa.Edu.Kg/hooks\n"
        "      - run: curl -s -X POST webhook.EXA.edu.KG\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert violations, "守卫未命中大小写变体 FQDN——大小写绕过仍可行"
    assert any("WEBHOOK.EXA.EDU.KG" in v for v in violations), "全大写 host 未命中"
    assert any("Webhook.Exa.Edu.Kg" in v for v in violations), "驼峰混合大小写未命中"
    assert any("'webhook.EXA.edu.KG'" in v for v in violations), "裸域名混合大小写未命中"


def test_guard_flags_trailing_root_dot_fqdn(tmp_path):
    """根点记法（绝对 FQDN，尾随根点）不得绕过检测——review P2。"""
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n"
        "  notify:\n"
        "    steps:\n"
        "      - run: curl -s -X POST https://webhook.exa.edu.kg./hooks/ci-complete\n"
        "      - run: curl -s -X POST webhook.exa.edu.kg.\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert violations, "守卫未命中根点记法 FQDN——trailing dot 绕过仍可行"
    assert any("'webhook.exa.edu.kg'" in v for v in violations)


def test_guard_covers_actions_outside_conventional_dirs(tmp_path):
    """非常规落点的本地 action（工作流 uses: ./ 引用）也在扫描范围——review P3 扫描面。"""
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  build:\n    steps:\n      - uses: ./scripts/my-action\n      - run: echo ok\n",
    )
    _write_fixture(
        tmp_path,
        "scripts/my-action/action.yml",
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - run: curl https://webhook.exa.edu.kg/hooks/ci-complete\n"
        "      shell: bash\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert any("scripts/my-action/action.yml" in v for v in violations), (
        "uses: ./ 引用的非常规落点 action 未被扫描"
    )


def test_guard_follows_nested_local_action_references(tmp_path):
    """composite action 里再引用的本地 action（嵌套 uses: ./）递归入扫描。"""
    _write_fixture(
        tmp_path,
        "actions/outer/action.yml",
        "runs:\n  using: composite\n  steps:\n    - uses: ./scripts/deep-action\n",
    )
    _write_fixture(
        tmp_path,
        "scripts/deep-action/action.yml",
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - run: curl https://webhook.exa.edu.kg\n"
        "      shell: bash\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert any("scripts/deep-action/action.yml" in v for v in violations), (
        "嵌套 uses: ./ 引用的 action 未被扫描"
    )


def test_guard_covers_nested_workflow_dirs(tmp_path):
    """工作流嵌套子目录（``.github/workflows/sub/``）同样入扫描——GitHub 会执行。"""
    _write_fixture(
        tmp_path,
        ".github/workflows/sub/notify.yml",
        "jobs:\n"
        "  notify:\n"
        "    steps:\n"
        "      - run: curl -s -X POST https://webhook.exa.edu.kg/hooks/ci-complete\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert any(".github/workflows/sub/notify.yml" in v for v in violations), (
        "嵌套子目录工作流未被扫描——顶层遍历漏检"
    )


def test_violation_paths_render_with_posix_separators(tmp_path, monkeypatch):
    """违规条目路径渲染强制 POSIX 分隔符——Windows 反斜杠路径不得出现（review P3 可移植性）。"""
    _write_fixture(
        tmp_path,
        "actions/branch-cleanup/action.yml",
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - run: curl https://webhook.exa.edu.kg\n"
        "      shell: bash\n",
    )

    original_relative_to = Path.relative_to

    def windows_style_relative_to(self, other, *args, **kwargs):
        # 模拟 Windows 检出：relative_to 返回反斜杠分隔的 PureWindowsPath
        return PureWindowsPath(str(original_relative_to(self, other, *args, **kwargs)))

    monkeypatch.setattr(Path, "relative_to", windows_style_relative_to)

    violations = scan_for_fqdn_violations(tmp_path)

    assert violations, "夹具未产生违规，用例失效"
    assert all("\\" not in v for v in violations), "违规条目出现反斜杠路径"
    assert any("actions/branch-cleanup/action.yml" in v for v in violations)


def test_scan_survives_symlinked_root(tmp_path):
    """root 经符号链接到达时扫描不得崩溃（uses 闭包 resolve 与未解析 root 失配）——review 跟进 P1。"""
    real_root = tmp_path / "real"
    real_root.mkdir()
    _write_fixture(
        real_root,
        ".github/workflows/ci.yml",
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - uses: ./scripts/my-action\n"
        "      - run: curl -s -X POST https://webhook.exa.edu.kg/hooks/ci-complete\n",
    )
    _write_fixture(
        real_root,
        "scripts/my-action/action.yml",
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - run: curl https://webhook.exa.edu.kg\n"
        "      shell: bash\n",
    )
    link_root = tmp_path / "link"
    link_root.symlink_to(real_root, target_is_directory=True)

    violations = scan_for_fqdn_violations(link_root)  # 修复前：ValueError 崩溃

    assert any(".github/workflows/ci.yml" in v for v in violations)
    assert any("scripts/my-action/action.yml" in v for v in violations), (
        "符号链接 root 下 uses 闭包目标未被扫描"
    )


def test_scan_skips_uses_refs_escaping_root(tmp_path):
    """uses 引用逃逸 root（../ 形态，仓库外不可能落地）不得使扫描崩溃——review 跟进 P1。"""
    root = tmp_path / "real"
    root.mkdir()
    _write_fixture(
        root,
        ".github/workflows/ci.yml",
        "jobs:\n  build:\n    steps:\n      - uses: ./../sibling-action\n",
    )
    _write_fixture(
        tmp_path,
        "sibling-action/action.yml",
        "runs:\n  using: composite\n  steps:\n    - run: curl https://webhook.exa.edu.kg\n      shell: bash\n",
    )

    violations = scan_for_fqdn_violations(root)  # 修复前：ValueError 崩溃

    assert violations == []


def test_guard_flags_backslash_continuation_url(tmp_path):
    """shell 反斜杠续行拆分的 URL 不得绕过（执行时去除续行符后是单一请求）——review 跟进 P2。"""
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n"
        "  notify:\n"
        "    steps:\n"
        "      - run: |\n"
        "          curl -s -X POST \\\n"
        "            https://webhook.exa.\\\n"
        "            edu.kg/hooks/ci-complete\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert violations, "守卫未命中反斜杠续行拆分的 URL——跨行绕过仍可行"
    assert any("'https://webhook.exa.edu.kg'" in v for v in violations)
    assert any(".github/workflows/ci.yml:5:" in v for v in violations), "续行组行号未归属组首行"


def test_comment_backslash_hides_env_line(tmp_path):
    """注释行尾反斜杠（``# docs \\``）不得把下一物理执行行并进注释豁免组。

    YAML 注释按行界定，行尾反斜杠是普通字符，其后一行是独立执行行——其上的
    FQDN 明文必须被扫到。scrutiny round 1 实证 bypass 回归：续行合并先于注释
    豁免执行时，执行行被吞进注释组，违规数为 0。
    """
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n"
        "  notify:\n"
        "    env:\n"
        "      # docs \\\n"
        "      WEBHOOK_URL: https://webhook.exa.edu.kg/hooks/ci-complete\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert violations, "注释尾反斜杠把下一行执行代码藏进注释组，FQDN 明文漏检——bypass 复现"
    assert any("https://webhook.exa.edu.kg" in v for v in violations)
    assert any(".github/workflows/ci.yml:5:" in v for v in violations), "执行行未按物理行命中"


def test_stacked_comments_hide_curl_line(tmp_path):
    """二连注释（末条尾反斜杠）不得藏住其后的 curl 执行行（scrutiny round 1 实证形态）。"""
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n"
        "  notify:\n"
        "    steps:\n"
        "      - run: |\n"
        "          # header note\n"
        "          # docs \\\n"
        "          curl -s -X POST https://webhook.exa.edu.kg/hooks/ci-complete\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert violations, "二连注释（末条尾反斜杠）藏住 curl 执行行，FQDN 明文漏检——bypass 复现"
    assert any("https://webhook.exa.edu.kg" in v for v in violations)
    assert any(".github/workflows/ci.yml:7:" in v for v in violations), "执行行未按物理行命中"


def test_comment_backslash_cannot_hide_split_continuation_url(tmp_path):
    """注释尾反斜杠组不得吞并以反斜杠续行拆分的 URL（droid-review P1 回归）。

    ``# note \\`` 后随 ``curl …exa.\\`` + ``edu.kg…``：完整 FQDN 只出现在被
    吞并的合并组里。注释行不得发起续行合并（注释组仅含注释行自身——YAML
    注释按行界定、行尾反斜杠是普通字符；bash 实证注释止于换行、其后继照常
    执行），拆分 URL 的代码续行组保持独立组首被扫描。
    """
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n"
        "  notify:\n"
        "    steps:\n"
        "      - run: |\n"
        "          # note \\\n"
        "          curl -s -X POST https://webhook.exa.\\\n"
        "          edu.kg/hooks/ci-complete\n",
    )

    violations = scan_for_fqdn_violations(tmp_path)

    assert violations, "注释组吞并续行代码行，拆分 URL 借注释豁免逃逸"
    assert any("'https://webhook.exa.edu.kg'" in v for v in violations)
    assert any(".github/workflows/ci.yml:6:" in v for v in violations), "续行组未按代码组首命中"


def test_uses_regex_ignores_lookalike_keys():
    """uses 键必须独立成键——lookalike 键名（如 myuses:）不触发本地引用解析。"""
    assert _USES_LOCAL_RE.search("    myuses: ./foo") is None
    assert _USES_LOCAL_RE.search("    uses: ./real") is not None


# ─── 豁免与误报控制 ─────────────────────────────────────────────────────


def test_guard_ignores_comment_lines(tmp_path):
    """注释行（# 开头，含说明性域名）豁免——网关域名允许出现在注释里。"""
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "# 通知链说明：webhook.exa.edu.kg 由 vars/secrets 注入，禁止写死。\n"
        "  # 历史注释：https://webhook.exa.edu.kg/hooks/ci-complete\n"
        "jobs:\n"
        "  notify:\n"
        "    steps:\n"
        '      - run: curl -X POST "$WEBHOOK_URL"\n'
        "        env:\n"
        "          WEBHOOK_URL: ${{ vars.CI_NOTIFY_WEBHOOK_URL }}\n",
    )

    assert scan_for_fqdn_violations(tmp_path) == []


def test_guard_ignores_docs_and_non_workflow_files(tmp_path):
    """文档文件（*.md）与工作流范围外的 yml 不在扫描范围。"""
    _write_fixture(
        tmp_path,
        "docs/architecture.md",
        "通知链：webhook.exa.edu.kg /hooks/ci-complete → ci-webhook.exa.edu.kg\n",
    )
    _write_fixture(
        tmp_path,
        "README.md",
        "# 示例配置\n\n```\nWEBHOOK_URL=https://webhook.exa.edu.kg/hooks/ci-complete\n```\n",
    )
    _write_fixture(
        tmp_path,
        "substrate/notes.yml",
        "note: webhook.exa.edu.kg\n",
    )

    assert scan_for_fqdn_violations(tmp_path) == []


def test_guard_ignores_external_domains(tmp_path):
    """外部域名（非受保护后缀）不误报；子域链/前缀粘连不误报。"""
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: |\n"
        "          curl -s https://github.com/hdot123/infraro-core\n"
        "          curl -s -X POST https://us.posthog.com/batch/\n"
        "          echo notexa.edu.kg exa.edu.kg.evil.com\n",
    )

    assert scan_for_fqdn_violations(tmp_path) == []


def test_adjacent_concat_skips_comment_successor(tmp_path):
    """相邻行拼接不并入注释行内容——注释里的域名不得经拼接渗入非注释视图造成误报。

    拼接补扫针对折叠标量/长行折断的值形态；后继是注释行时其内容属于豁免面
    （scrutiny round 1 登记盲区：代码行 + 相邻注释行组合会误报）。
    """
    _write_fixture(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: echo hi\n"
        "      # 说明：域名 webhook.exa.edu.kg 只能经 vars 注入，禁止写死\n",
    )

    assert scan_for_fqdn_violations(tmp_path) == []


# ─── 真实树守卫（本守卫的牙齿，CI 常驻断言）────────────────────────────


def test_live_tree_has_no_plaintext_fqdn():
    """真实仓库树必须通过：revert #315 后 main 上工作流/action 无 FQDN 明文。

    若本测试失败：说明有人把内网域名明文写回了 CI 工作流或 composite action
    ——改回 vars/secrets 引用（参见本模块 docstring 与 #314 事故记录）。
    """
    violations = scan_for_fqdn_violations(REPO_ROOT)

    assert not violations, (
        "CI 工作流/composite action 出现内网 FQDN 明文"
        "（应改走 vars/secrets 引用，禁止写死）：\n" + "\n".join(violations)
    )
