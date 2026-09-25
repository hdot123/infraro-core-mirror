# Changelog

## [0.19.0](https://github.com/hdot123/infraro-core/compare/v0.18.9...v0.19.0) (2026-09-25)


### Features

* **ci:** add BYOM infrastructure (setup-byom-go-github + droid-review) ([#137](https://github.com/hdot123/infraro-core/issues/137)) ([4093315](https://github.com/hdot123/infraro-core/commit/409331586268c96d1d18c2b20c3719a5378b9426))
* **engine:** 审计防线加固四件套（接口门 fail-closed / liveness conclusion / 反向 import AST 锁 / 零红移序） ([#141](https://github.com/hdot123/infraro-core/issues/141)) ([bc810b7](https://github.com/hdot123/infraro-core/commit/bc810b71e339d67a2d3aec172919161747ba3f7f))


### Bug Fixes

* **branch-cleanup:** revalidate protected branches against remote before tracker write ([#131](https://github.com/hdot123/infraro-core/issues/131)) ([04af200](https://github.com/hdot123/infraro-core/commit/04af200deaa62154d64b545e58788aa7c6e053c4))
* **budget-guard:** auto-close stale window alert on recovery ([#130](https://github.com/hdot123/infraro-core/issues/130)) ([747ca70](https://github.com/hdot123/infraro-core/commit/747ca70b61011df7b1aae7a2c796988c01ddf3de))
* **docs:** 防线区块自含重写，移除悬空章节引用——不写字面文件名 ([#145](https://github.com/hdot123/infraro-core/issues/145)) ([57a8487](https://github.com/hdot123/infraro-core/commit/57a8487397de182fe9d517b6503ed5bfca6c4815))
* **engine:** liveness conclusion streak 穿透 self-heal 抑制链 + conclusion 覆盖三值 + 防线区块自含重写 ([#144](https://github.com/hdot123/infraro-core/issues/144)) ([1a32344](https://github.com/hdot123/infraro-core/commit/1a32344756fc03e8523bdf3ca0648df6e38638f4))
* **m5:** F10 readiness remediation - remove fabricated evidence, fix ruff, redesign emergency channel ([#150](https://github.com/hdot123/infraro-core/issues/150)) ([6ee7b48](https://github.com/hdot123/infraro-core/commit/6ee7b489fb0247efdf79738674ca97b88b40318c))
* **test:** VAL-FIX-006/VAL-FIX-003 测试锁加固 ([#146](https://github.com/hdot123/infraro-core/issues/146)) ([c6ab9a2](https://github.com/hdot123/infraro-core/commit/c6ab9a2b159556358ac0d9875f55957680369618))
* restore L117/L113 suppression and fix drift guard ast.walk coverage ([#127](https://github.com/hdot123/infraro-core/issues/127)) ([0882d5d](https://github.com/hdot123/infraro-core/commit/0882d5df2234632df9fe1c2bab134aa270c8c179))
* 修复抑制契约测试三处硬编码值错误（scrutiny feature） ([#126](https://github.com/hdot123/infraro-core/issues/126)) ([596254c](https://github.com/hdot123/infraro-core/commit/596254cc5bf0767abf579f34a18aeb1c7b58d22c))


### Code Refactoring

* **packs:** daily_audit 拆分为 5 个平铺私有模块 + 门面（含常驻契约测试与两份治理决策文档） ([#134](https://github.com/hdot123/infraro-core/issues/134)) ([a17ad26](https://github.com/hdot123/infraro-core/commit/a17ad260c51ced0e07dab515d74ff1c6ca06ed23))


### Documentation

* sync droid-review BYOM model section to new go-gateway chain ([#139](https://github.com/hdot123/infraro-core/issues/139)) ([3112a47](https://github.com/hdot123/infraro-core/commit/3112a47f6ab9e5ffaee92d40ce24299d5e19ea7f))
* **arch:** sync §1.2 physical tree to actual structure ([#140](https://github.com/hdot123/infraro-core/issues/140)) ([0196dcd](https://github.com/hdot123/infraro-core/commit/0196dcd631e10ea44b6f9a987ad30e07cbc99a32))
* **evidence:** add [REDACTED-HOST] runner layout inventory (7 instances) ([#147](https://github.com/hdot123/infraro-core/issues/147)) ([fe6f40a](https://github.com/hdot123/infraro-core/commit/fe6f40a5b950c90ecee6640586131f95d04e981a))
* **evidence:** channel-research-poc-redo corrected document - removed falsified PoC section, marked tests as not executed ([#149](https://github.com/hdot123/infraro-core/issues/149)) ([66a205d](https://github.com/hdot123/infraro-core/commit/66a205d9c6807f64371753f031ce1e8d5bc7617d))
* **evidence:** 归档 2026-09 审计防线加固验证产物（R6 终审链 15 份 + 各防线证据 + sha256 manifest） ([#143](https://github.com/hdot123/infraro-core/issues/143)) ([f6a7175](https://github.com/hdot123/infraro-core/commit/f6a717507e3f69beb10b4bedfd79403b2aa0a2e8))


### Miscellaneous

* **ci:** bump actions/upload-artifact from v5 to v7.0.1 ([#124](https://github.com/hdot123/infraro-core/issues/124)) ([3477790](https://github.com/hdot123/infraro-core/commit/34777902551ad8dbdbaa47c22cf72d2f489d7e9a))
* **deps:** update ruff requirement from >=0.16.7 to >=0.16.8 ([#133](https://github.com/hdot123/infraro-core/issues/133)) ([9d8ab24](https://github.com/hdot123/infraro-core/commit/9d8ab244a2ac75650018c4226b9c92077bde562c))
* **governance:** 架构分层 v3 步骤 1-3 治理基线（安全最小集 + 真源登记 + 归类声明） ([#132](https://github.com/hdot123/infraro-core/issues/132)) ([911effd](https://github.com/hdot123/infraro-core/commit/911effd873e541f9cc915036242a560cc0811a6d))
* **review:** bump droid exec auto level low → medium in shard runner ([#138](https://github.com/hdot123/infraro-core/issues/138)) ([b6b2d73](https://github.com/hdot123/infraro-core/commit/b6b2d730a888944dd2b3e01802e7ef1497bcb16b))

## [0.18.9](https://github.com/hdot123/infraro-core/compare/v0.18.8...v0.18.9) (2026-09-19)


### Bug Fixes

* **ci:** 分支清理 caller 与模板的 secret 引用大小写收敛 ([#116](https://github.com/hdot123/infraro-core/issues/116)) ([3e77833](https://github.com/hdot123/infraro-core/commit/3e77833f37a8bb417a68c5d30ebbdde67f244ada))
* **trigger-error-droid:** update fallback path from ~/memory to ~/infraro-core ([#81](https://github.com/hdot123/infraro-core/issues/81)) ([ccfda3c](https://github.com/hdot123/infraro-core/commit/ccfda3c18439f8d880120135c5c3dfff47fbc5b6))
* **webhook:** update repository references from hdot123-org/memory to hdot123/infraro-core ([#80](https://github.com/hdot123/infraro-core/issues/80)) ([f94aa9d](https://github.com/hdot123/infraro-core/commit/f94aa9dc42f22e87fb9b2fde812ad6191d7f2563))


### Documentation

* 入口文档治理段落对齐终态 + 锁文件 relock ([#118](https://github.com/hdot123/infraro-core/issues/118)) ([01c8b6e](https://github.com/hdot123/infraro-core/commit/01c8b6ed9a4c71724eaeb40237d61db65afb6b4a))

## [0.18.8](https://github.com/hdot123/infraro-core/compare/v0.18.7...v0.18.8) (2026-09-18)


### Bug Fixes

* Provide explicit cache-dir for uv lock command (PR [#64](https://github.com/hdot123/infraro-core/issues/64)) ([#65](https://github.com/hdot123/infraro-core/issues/65)) ([b316d05](https://github.com/hdot123/infraro-core/commit/b316d0589017a5f5172d91ef4a92883895e697a4))
* **workflow:** add guard for PIP_CACHE_DIR to prevent empty string pollution ([#76](https://github.com/hdot123/infraro-core/issues/76)) ([fb6e88b](https://github.com/hdot123/infraro-core/commit/fb6e88b6d23825182a161e00eb20463a94ff579f))
* **workflow:** correct uv lock cache-dir parameter fallback (PR [#64](https://github.com/hdot123/infraro-core/issues/64)) ([#68](https://github.com/hdot123/infraro-core/issues/68)) ([4baaae0](https://github.com/hdot123/infraro-core/commit/4baaae0b9b9ec7ec1600954ed22adaa6a16fb1cd))
* **workflow:** correct uv lock cache-dir parameter fallback for real (PR [#64](https://github.com/hdot123/infraro-core/issues/64)) ([#69](https://github.com/hdot123/infraro-core/issues/69)) ([2e3e865](https://github.com/hdot123/infraro-core/commit/2e3e8655f026b50e6ba165f300adecfc07dce13e))
* **workflow:** prevent empty UV_CACHE_DIR pollution in setup-venv action ([#75](https://github.com/hdot123/infraro-core/issues/75)) ([c6abd91](https://github.com/hdot123/infraro-core/commit/c6abd91a84936a97314eda0d7dbab715627f1891))
* **workflow:** remove --cache-dir parameter to fix uv lock error (PR [#64](https://github.com/hdot123/infraro-core/issues/64)) ([#71](https://github.com/hdot123/infraro-core/issues/71)) ([5cba44f](https://github.com/hdot123/infraro-core/commit/5cba44f53d5d34a382547979ad3902dc09182c85))
* **workflow:** schedule-safe runner default for scan/heartbeat runs-on ([#63](https://github.com/hdot123/infraro-core/issues/63)) ([758fe58](https://github.com/hdot123/infraro-core/commit/758fe5831984633e9f62bd22697ed8868cb6949d))
* **workflow:** use --no-cache flag to avoid cache-dir requirement (PR [#64](https://github.com/hdot123/infraro-core/issues/64)) ([#72](https://github.com/hdot123/infraro-core/issues/72)) ([5ea446d](https://github.com/hdot123/infraro-core/commit/5ea446d8e77912677016244eecae66b13b012a8f))
* **workflow:** use simplest possible cache-dir approach to fix --cache-dir error (PR [#64](https://github.com/hdot123/infraro-core/issues/64)) ([#70](https://github.com/hdot123/infraro-core/issues/70)) ([af6cde3](https://github.com/hdot123/infraro-core/commit/af6cde35b92c34270c105c8ba71aeacf4c62db29))


### Documentation

* refresh engine entry doc to 2026-09 state ([#62](https://github.com/hdot123/infraro-core/issues/62)) ([8790bc7](https://github.com/hdot123/infraro-core/commit/8790bc770ab00afbdf6e18b218a85a641147df36))

## [0.18.7](https://github.com/hdot123/infraro-core/compare/v0.18.6...v0.18.7) (2026-09-17)


### Bug Fixes

* **workflow:** fromJSON label passthrough for runner input ([#57](https://github.com/hdot123/infraro-core/issues/57)) ([f737c99](https://github.com/hdot123/infraro-core/commit/f737c99699bee85fc5a76a7f3626bc65cfc6bbe0))

## [0.18.6](https://github.com/hdot123/infraro-core/compare/v0.18.6...v0.18.6) (2026-09-17)


### Bug Fixes

* **workflow:** fromJSON label passthrough for runner input ([#57](https://github.com/hdot123/infraro-core/issues/57)) ([f737c99](https://github.com/hdot123/infraro-core/commit/f737c99699bee85fc5a76a7f3626bc65cfc6bbe0))

## [0.18.6](https://github.com/hdot123/infraro-core/compare/v0.18.5...v0.18.6) (2026-09-17)


### Features

* **heartbeat:** scanner_workflow input for consumer scanner filename override (r38) ([#53](https://github.com/hdot123/infraro-core/issues/53)) ([30b6e50](https://github.com/hdot123/infraro-core/commit/30b6e5032022e799f7208e94e0ac9db179783942))
* **workflow:** add runner input and dispatch guard for consumer-slot-finalization ([#54](https://github.com/hdot123/infraro-core/issues/54)) ([df0ee1b](https://github.com/hdot123/infraro-core/commit/df0ee1b0bcad10fb68d6324e44e02bc631ff3d8c))


### Bug Fixes

* Engine main redfix relock fixture drift ([#49](https://github.com/hdot123/infraro-core/issues/49)) ([48190b4](https://github.com/hdot123/infraro-core/commit/48190b49a15d6e203cb5f2172bb894e49f6c6a8f))
* **governance:** align F8 ruleset contract with r34b adjudicated terminal state ([#52](https://github.com/hdot123/infraro-core/issues/52)) ([d1bd3e3](https://github.com/hdot123/infraro-core/commit/d1bd3e38b6aaa568a07b9fe647b8f695a91c3fdb))

## [0.18.5](https://github.com/hdot123/infraro-core/compare/v0.18.4...v0.18.5) (2026-09-16)


### Features

* **config:** add release-as 0.18.5 to fix version calculation ([#45](https://github.com/hdot123/infraro-core/issues/45)) ([9d12b66](https://github.com/hdot123/infraro-core/commit/9d12b66a6098290bfb57316d9d09da22f7a6634e))
* engine-substrate-boundary - desensitize IP whitelists and host paths ([#26](https://github.com/hdot123/infraro-core/issues/26)) ([0c92d34](https://github.com/hdot123/infraro-core/commit/0c92d34bbe44dc943a42362d1e340d833b9e31ab))
* **engine:** snake-only convergence for workflow_call inputs/secrets ([#32](https://github.com/hdot123/infraro-core/issues/32)) ([c61f798](https://github.com/hdot123/infraro-core/commit/c61f7985a131b0d2f0ad4b46a975e8ce010a0f77))
* substrate gate suite (0-4) for engine repository ([#21](https://github.com/hdot123/infraro-core/issues/21)) ([7a82aef](https://github.com/hdot123/infraro-core/commit/7a82aefbaed732124ee6681902c50788d376c2cd))


### Bug Fixes

* complete substrate-r1-fix-boundary by registering .github/ runner mentions ([#29](https://github.com/hdot123/infraro-core/issues/29)) ([3c2b1b2](https://github.com/hdot123/infraro-core/commit/3c2b1b235957a3952a851440962a65eaf33100ea))
* **gh-proxy:** align IP placeholder literal to unblock contract tests ([#30](https://github.com/hdot123/infraro-core/issues/30)) ([a87c81c](https://github.com/hdot123/infraro-core/commit/a87c81ce86a81c6c8e0d21e0e064cf9e06cf06e2))
* remove release-as line to unstick v0.18.5 release ([#40](https://github.com/hdot123/infraro-core/issues/40)) ([34f9d19](https://github.com/hdot123/infraro-core/commit/34f9d19c07ccb9a55288edfda5f3466f6a8bb45b))
* **substrate:** gh-proxy comma-separated ALLOWED_IPS + file-level gate3 exposure stock ([#31](https://github.com/hdot123/infraro-core/issues/31)) ([bc2eedf](https://github.com/hdot123/infraro-core/commit/bc2eedfa4138a96579c77b14da45f772d1ae16b4))

## [0.18.4](https://github.com/hdot123/infraro-core/compare/v0.18.4...v0.18.4) (2026-09-16)


### Features

* engine-substrate-boundary - desensitize IP whitelists and host paths ([#26](https://github.com/hdot123/infraro-core/issues/26)) ([0c92d34](https://github.com/hdot123/infraro-core/commit/0c92d34bbe44dc943a42362d1e340d833b9e31ab))
* **engine:** snake-only convergence for workflow_call inputs/secrets ([#32](https://github.com/hdot123/infraro-core/issues/32)) ([c61f798](https://github.com/hdot123/infraro-core/commit/c61f7985a131b0d2f0ad4b46a975e8ce010a0f77))
* substrate gate suite (0-4) for engine repository ([#21](https://github.com/hdot123/infraro-core/issues/21)) ([7a82aef](https://github.com/hdot123/infraro-core/commit/7a82aefbaed732124ee6681902c50788d376c2cd))


### Bug Fixes

* complete substrate-r1-fix-boundary by registering .github/ runner mentions ([#29](https://github.com/hdot123/infraro-core/issues/29)) ([3c2b1b2](https://github.com/hdot123/infraro-core/commit/3c2b1b235957a3952a851440962a65eaf33100ea))
* **gh-proxy:** align IP placeholder literal to unblock contract tests ([#30](https://github.com/hdot123/infraro-core/issues/30)) ([a87c81c](https://github.com/hdot123/infraro-core/commit/a87c81ce86a81c6c8e0d21e0e064cf9e06cf06e2))
* **substrate:** gh-proxy comma-separated ALLOWED_IPS + file-level gate3 exposure stock ([#31](https://github.com/hdot123/infraro-core/issues/31)) ([bc2eedf](https://github.com/hdot123/infraro-core/commit/bc2eedfa4138a96579c77b14da45f772d1ae16b4))

## [0.18.4](https://github.com/hdot123/infraro-core/compare/v0.18.4...v0.18.4) (2026-09-15)


### Features

* engine-substrate-boundary - desensitize IP whitelists and host paths ([#26](https://github.com/hdot123/infraro-core/issues/26)) ([0c92d34](https://github.com/hdot123/infraro-core/commit/0c92d34bbe44dc943a42362d1e340d833b9e31ab))
* substrate gate suite (0-4) for engine repository ([#21](https://github.com/hdot123/infraro-core/issues/21)) ([7a82aef](https://github.com/hdot123/infraro-core/commit/7a82aefbaed732124ee6681902c50788d376c2cd))


### Bug Fixes

* complete substrate-r1-fix-boundary by registering .github/ runner mentions ([#29](https://github.com/hdot123/infraro-core/issues/29)) ([3c2b1b2](https://github.com/hdot123/infraro-core/commit/3c2b1b235957a3952a851440962a65eaf33100ea))
* **gh-proxy:** align IP placeholder literal to unblock contract tests ([#30](https://github.com/hdot123/infraro-core/issues/30)) ([a87c81c](https://github.com/hdot123/infraro-core/commit/a87c81ce86a81c6c8e0d21e0e064cf9e06cf06e2))
* **substrate:** gh-proxy comma-separated ALLOWED_IPS + file-level gate3 exposure stock ([#31](https://github.com/hdot123/infraro-core/issues/31)) ([bc2eedf](https://github.com/hdot123/infraro-core/commit/bc2eedfa4138a96579c77b14da45f772d1ae16b4))

## [0.18.4](https://github.com/hdot123/infraro-core/compare/v0.18.4...v0.18.4) (2026-09-14)


### Features

* engine-substrate-boundary - desensitize IP whitelists and host paths ([#26](https://github.com/hdot123/infraro-core/issues/26)) ([0c92d34](https://github.com/hdot123/infraro-core/commit/0c92d34bbe44dc943a42362d1e340d833b9e31ab))
* substrate gate suite (0-4) for engine repository ([#21](https://github.com/hdot123/infraro-core/issues/21)) ([7a82aef](https://github.com/hdot123/infraro-core/commit/7a82aefbaed732124ee6681902c50788d376c2cd))


### Bug Fixes

* complete substrate-r1-fix-boundary by registering .github/ runner mentions ([#29](https://github.com/hdot123/infraro-core/issues/29)) ([3c2b1b2](https://github.com/hdot123/infraro-core/commit/3c2b1b235957a3952a851440962a65eaf33100ea))
* **gh-proxy:** align IP placeholder literal to unblock contract tests ([#30](https://github.com/hdot123/infraro-core/issues/30)) ([a87c81c](https://github.com/hdot123/infraro-core/commit/a87c81ce86a81c6c8e0d21e0e064cf9e06cf06e2))
* **substrate:** gh-proxy comma-separated ALLOWED_IPS + file-level gate3 exposure stock ([#31](https://github.com/hdot123/infraro-core/issues/31)) ([bc2eedf](https://github.com/hdot123/infraro-core/commit/bc2eedfa4138a96579c77b14da45f772d1ae16b4))

## [0.18.4](https://github.com/hdot123/infraro-core/compare/v0.18.4...v0.18.4) (2026-09-13)


### Features

* engine-substrate-boundary - desensitize IP whitelists and host paths ([#26](https://github.com/hdot123/infraro-core/issues/26)) ([0c92d34](https://github.com/hdot123/infraro-core/commit/0c92d34bbe44dc943a42362d1e340d833b9e31ab))
* substrate gate suite (0-4) for engine repository ([#21](https://github.com/hdot123/infraro-core/issues/21)) ([7a82aef](https://github.com/hdot123/infraro-core/commit/7a82aefbaed732124ee6681902c50788d376c2cd))

## [0.18.4](https://github.com/hdot123/infraro-core/compare/v0.18.4...v0.18.4) (2026-09-13)


### Features

* add version gate to shellcheck Layer-1 step mirroring actionlint ([#7](https://github.com/hdot123/infraro-core/issues/7)) ([8a0476f](https://github.com/hdot123/infraro-core/commit/8a0476f4087b4cda9646a08199fa30bcfc76e8a5))
* import engine at 0.18.4 baseline with personal-account rewiring ([#2](https://github.com/hdot123/infraro-core/issues/2)) ([3695445](https://github.com/hdot123/infraro-core/commit/3695445b1d8df8997de35d39256ffe596d229c8c))
* update governance contract tests to match v3 expectations ([#5](https://github.com/hdot123/infraro-core/issues/5)) ([1f4a458](https://github.com/hdot123/infraro-core/commit/1f4a458fa0b2282e88543f53faf4444039b2ce0f))


### Bug Fixes

* add release-as 0.18.4 and setup-uv step for release-please workflow ([#11](https://github.com/hdot123/infraro-core/issues/11)) ([640ed4c](https://github.com/hdot123/infraro-core/commit/640ed4c781b61d62ca262ba4fd824cf0e799d000))
* Apply F2 CI fixes round 3 ([#3](https://github.com/hdot123/infraro-core/issues/3)) ([9a7b1a7](https://github.com/hdot123/infraro-core/commit/9a7b1a79577eff38bc97b7b7f64f73e10c87c35d))
* fetch-depth 0 for QA full-history contract tests ([#6](https://github.com/hdot123/infraro-core/issues/6)) ([cbd4af9](https://github.com/hdot123/infraro-core/commit/cbd4af9264f3130f689ebe1c10fb43eacb11d83c))

## [0.18.3](https://github.com/hdot123/infraro-core/compare/v0.18.2...v0.18.3) (2026-09-11)


### Bug Fixes

* **ci:** 漂移门禁跳过 CI runner，mission 沙箱 HOME 不作比对基准 ([#290](https://github.com/hdot123/infraro-core/issues/290)) ([f702b2b](https://github.com/hdot123/infraro-core/commit/f702b2b72a53173f12b9db0243e6d9e3c67a4032))
* droid-task 执行器兜底修复与 team 级并发护栏 ([#285](https://github.com/hdot123/infraro-core/issues/285)) ([01ff273](https://github.com/hdot123/infraro-core/commit/01ff2739f586335f1a174ac15b259d5885c767ad))

## [0.18.2](https://github.com/hdot123/infraro-core/compare/v0.18.1...v0.18.2) (2026-09-11)


### Bug Fixes

* 修复 webhook 脚本 1Password Linear 凭据死引用（404） ([#284](https://github.com/hdot123/infraro-core/issues/284)) ([d93a3f6](https://github.com/hdot123/infraro-core/commit/d93a3f69dcb99af14f523820d9a4a04594548ae9))

## [0.18.1](https://github.com/hdot123/infraro-core/compare/v0.18.0...v0.18.1) (2026-09-10)


### Documentation

* **readme:** add governance deliverables visibility ([#282](https://github.com/hdot123/infraro-core/issues/282)) ([1d7d491](https://github.com/hdot123/infraro-core/commit/1d7d49141f21a35f89710dce7550b1c0464dbed8))

## [0.18.0](https://github.com/hdot123/infraro-core/compare/v0.17.0...v0.18.0) (2026-09-09)


### Features

* add governance-playbook feature ([#277](https://github.com/hdot123/infraro-core/issues/277)) ([7e73a41](https://github.com/hdot123/infraro-core/commit/7e73a413d0f60dddb39639892116850417eeb598))
* **governance:** add residual as-code templates and 3-repo correction plan ([#278](https://github.com/hdot123/infraro-core/issues/278)) ([4ca5e66](https://github.com/hdot123/infraro-core/commit/4ca5e6635c9da93def180318b2acb478fbdb0d2e))


### Bug Fixes

* **governance:** M2 scrutiny round 1 blocking + mechanical fixes ([#279](https://github.com/hdot123/infraro-core/issues/279)) ([ca81bab](https://github.com/hdot123/infraro-core/commit/ca81babc9d42bd3330768cae5eaa23f939e3f91d))
* **governance:** VAL-SPEC-016 双侧修复 + 非阻断文档缺陷修复 ([#280](https://github.com/hdot123/infraro-core/issues/280)) ([a48f0f1](https://github.com/hdot123/infraro-core/commit/a48f0f15cd2515cb4ce7f305348094607c95319c))

## [0.17.0](https://github.com/hdot123/infraro-core/compare/v0.16.1...v0.17.0) (2026-09-09)


### Features

* **autofix:** add notify-only failure filter ([#270](https://github.com/hdot123/infraro-core/issues/270)) ([3f70da4](https://github.com/hdot123/infraro-core/commit/3f70da4ac27382b77212ac48207658fd989b11dc))
* **executor:** 执行器宿主仓自检出 + 目标仓子目录检出 ([#271](https://github.com/hdot123/infraro-core/issues/271)) ([bcdcb8f](https://github.com/hdot123/infraro-core/commit/bcdcb8fbe2f386078bdd12d052d3b4d063b051f6))
* **executor:** 统一执行器 — droid-task.yml + setup-droid-byok composite + 契约测试 ([#268](https://github.com/hdot123/infraro-core/issues/268)) ([e37cef4](https://github.com/hdot123/infraro-core/commit/e37cef4b7b2e1ff82548f137f83d9e42f3635f5a))
* P0-A watchdog source awareness ([#269](https://github.com/hdot123/infraro-core/issues/269)) ([996922b](https://github.com/hdot123/infraro-core/commit/996922b0cd6df8c18615ec437e5ba7e234e693d7))

## [0.16.1](https://github.com/hdot123/infraro-core/compare/v0.16.0...v0.16.1) (2026-09-08)


### Bug Fixes

* **branch-cleanup:** 退休 INFRA-893 追踪的四个 mencbo 保护分支 (INFRA-893) ([#266](https://github.com/hdot123/infraro-core/issues/266)) ([38dcd88](https://github.com/hdot123/infraro-core/commit/38dcd887dd7464ac1f521dfb1f8d5c4c5a757473))

## [0.16.0](https://github.com/hdot123/infraro-core/compare/v0.15.2...v0.16.0) (2026-09-08)


### Features

* **ci:** add BYOK smoke job (bailian coding endpoint) ([#253](https://github.com/hdot123/infraro-core/issues/253)) ([07f5747](https://github.com/hdot123/infraro-core/commit/07f57476bc39843c1bcfef8664b700798fd0b43b))
* **ci:** add droid runner pilot phase-1 smoke workflow ([#252](https://github.com/hdot123/infraro-core/issues/252)) ([5f6eb20](https://github.com/hdot123/infraro-core/commit/5f6eb207afd0d7ab075486146f731c60f864033d))
* **ci:** add droid-autofix workflow ([#256](https://github.com/hdot123/infraro-core/issues/256)) ([732ae2d](https://github.com/hdot123/infraro-core/commit/732ae2d236ea8f927b439a45dd358e6c31979c26))


### Documentation

* note fork-PR rejection policy in droid-review (v0.15.2) ([#255](https://github.com/hdot123/infraro-core/issues/255)) ([5c31db2](https://github.com/hdot123/infraro-core/commit/5c31db2043043ec754a0f95773cd53a4fe295d86))

## [0.15.2](https://github.com/hdot123/infraro-core/compare/v0.15.1...v0.15.2) (2026-09-08)


### Bug Fixes

* **security:** fail-closed fork guard for droid-review chains ([#250](https://github.com/hdot123/infraro-core/issues/250)) ([0d0bdca](https://github.com/hdot123/infraro-core/commit/0d0bdca4e9b0c4fde3d5edad76ef4c8b489f5e47))

## [0.15.1](https://github.com/hdot123/infraro-core/compare/v0.15.0...v0.15.1) (2026-09-07)


### Bug Fixes

* **engine:** shards 引擎 checkout 纳入 SHA 真源 + 审计仓默认链 + 删 CI 空转步 ([#246](https://github.com/hdot123/infraro-core/issues/246)) ([227e974](https://github.com/hdot123/infraro-core/commit/227e974301aeb8e80cce60f12298922305d40cf8))

## [0.15.0](https://github.com/hdot123/infraro-core/compare/v0.14.0...v0.15.0) (2026-09-06)


### Features

* **engine:** decouple engine delivery from consumer language ([#243](https://github.com/hdot123/infraro-core/issues/243)) ([24b3e4f](https://github.com/hdot123/infraro-core/commit/24b3e4ff759879b2221b422ab6d5007b839ad1af))

## [0.14.0](https://github.com/hdot123/infraro-core/compare/v0.13.0...v0.14.0) (2026-09-06)


### Features

* **webhook-scripts:** 漂移门禁三态实现与单测 ([#228](https://github.com/hdot123/infraro-core/issues/228)) ([af754ae](https://github.com/hdot123/infraro-core/commit/af754aec13c9155c035f268bbc8598dd70eaad84))


### Bug Fixes

* **cf:** 存档技术性去激活 routes 块 + 契约测试升级 (VAL-CF-001) ([#230](https://github.com/hdot123/infraro-core/issues/230)) ([d91e0ae](https://github.com/hdot123/infraro-core/commit/d91e0ae9c6d3661bc8f17cb98ffd785558883b68))
* **engine:** add cross-run dedup to publish_findings (VAL-DEDUP-001) ([#229](https://github.com/hdot123/infraro-core/issues/229)) ([abeb68c](https://github.com/hdot123/infraro-core/commit/abeb68c0899fe26cbb00a2e676d9e9a31e148600))
* **engine:** treat jsonl empty stdout at exit 0 as zero findings ([#241](https://github.com/hdot123/infraro-core/issues/241)) ([8b010aa](https://github.com/hdot123/infraro-core/commit/8b010aac7524364ee80bcb24dd0d8a56e5810650))
* **gate:** 修复分页守卫死代码 + 归因勘误落地 (M3 scrutiny 非阻塞项收口) ([#231](https://github.com/hdot123/infraro-core/issues/231)) ([6ffed5a](https://github.com/hdot123/infraro-core/commit/6ffed5aa14eeb8424996edd6546e8bb229a0c02e))
* **test:** 修复测试参数 bug + 扩充分页场景测试至 31+ 条 ([#235](https://github.com/hdot123/infraro-core/issues/235)) ([848ab5f](https://github.com/hdot123/infraro-core/commit/848ab5f0c5d2e797e8584f706a5011c918bc261f))
* **workflow:** 修正两处失实 pin 注释 v0.13.0 → post-v0.13.0 (INFRA-778) ([#236](https://github.com/hdot123/infraro-core/issues/236)) ([2b43b97](https://github.com/hdot123/infraro-core/commit/2b43b974c621170e2cceee06713243f2b79e7063))


### Documentation

* 完善 README 开发文档 + 修正裁决文档证据归属 + 标记废弃章节 ([#240](https://github.com/hdot123/infraro-core/issues/240)) ([6c8b14a](https://github.com/hdot123/infraro-core/commit/6c8b14a78822470e932007d9a3feb7a4a7a88ad1))

## [0.13.0](https://github.com/hdot123/infraro-core/compare/v0.12.0...v0.13.0) (2026-09-05)


### Features

* **webhook-scripts:** 收编 write_comment.py + 修正 trigger-ci-droid 误导注释 ([#226](https://github.com/hdot123/infraro-core/issues/226)) ([38f720a](https://github.com/hdot123/infraro-core/commit/38f720a55e57e0820a89d81562539d1f14b8038a))


### Bug Fixes

* **gate:** 修复引擎 gate 三缺陷——自排除+具名打印+落盘 jq (INFRA-767) ([#224](https://github.com/hdot123/infraro-core/issues/224)) ([0bcb45b](https://github.com/hdot123/infraro-core/commit/0bcb45b612c4d7eef102741ffcc4be2468565f79))

## [0.12.0](https://github.com/hdot123/infraro-core/compare/v0.11.1...v0.12.0) (2026-09-05)


### Features

* **release:** add error isolation and timeout handling ([#215](https://github.com/hdot123/infraro-core/issues/215)) ([6cfd4d3](https://github.com/hdot123/infraro-core/commit/6cfd4d34b55d15c31b6e82ba8a3ed8c25b569896))
* **webhook:** 新增 trigger-release.sh 发版公告触发脚本 (INFRA-753) ([#213](https://github.com/hdot123/infraro-core/issues/213)) ([35459d0](https://github.com/hdot123/infraro-core/commit/35459d00144fbf9f86e8465144fb3d2242ea8443))
* 实现轮询触发面 poll-releases.sh 与文档同步 ([#219](https://github.com/hdot123/infraro-core/issues/219)) ([fbf4630](https://github.com/hdot123/infraro-core/commit/fbf463058ccbe48e67d18aba4670453f4bba4a4c))
* 新增 release-announce 工作流与契约测试 (INFRA-754) ([#212](https://github.com/hdot123/infraro-core/issues/212)) ([5bb512a](https://github.com/hdot123/infraro-core/commit/5bb512ae01bd7e77a70ea17412d272493eb1e5b7))


### Bug Fixes

* **announce:** 支持 Cloudflare Access Service Token 可选头 + onboarding 三处纠偏 ([#218](https://github.com/hdot123/infraro-core/issues/218)) ([89df0e8](https://github.com/hdot123/infraro-core/commit/89df0e86acd040705e9219b381544164109d1393))
* **poll:** 加固 poll-releases.sh 自举失败语义与哨兵机制 (INFRA-762) ([#220](https://github.com/hdot123/infraro-core/issues/220)) ([f5668ca](https://github.com/hdot123/infraro-core/commit/f5668cafcb8014fb46e20a2944e85c8e48b8a438))
* **webhook:** select_consumers() 对 repoPath 做 os.path.expanduser 展开 (INFRA-758) ([#216](https://github.com/hdot123/infraro-core/issues/216)) ([2a10205](https://github.com/hdot123/infraro-core/commit/2a1020508983799633c5308239969489c52f2e2b))


### Documentation

* README 增加发版公告链路与轮询触发面说明 (INFRA-764) ([#222](https://github.com/hdot123/infraro-core/issues/222)) ([f075d3a](https://github.com/hdot123/infraro-core/commit/f075d3aab4b49d4fb8f380c5b646d5eae611ca70))
* 固化发版公告接入标准与文档契约测试 (INFRA-760) ([#217](https://github.com/hdot123/infraro-core/issues/217)) ([c92de37](https://github.com/hdot123/infraro-core/commit/c92de3747f392bc007de4e85329179c2acf9ea38))
* 固化权限同步守则 (INFRA-764) ([#221](https://github.com/hdot123/infraro-core/issues/221)) ([161b9d0](https://github.com/hdot123/infraro-core/commit/161b9d0e0ba103432d0437c570b6b23691045381))

## [0.11.1](https://github.com/hdot123/infraro-core/compare/v0.11.0...v0.11.1) (2026-09-04)


### Bug Fixes

* **workflow:** branch-cleanup 自仓 action pin 补 bump 至 PR [#198](https://github.com/hdot123/infraro-core/issues/198) squash SHA，修复 freshness 契约红 (INFRA-737) ([#199](https://github.com/hdot123/infraro-core/issues/199)) ([442abb5](https://github.com/hdot123/infraro-core/commit/442abb577a4690455be53875b1b52d5645657783))


### Documentation

* **cf-worker:** wangguan 网关接管后文档与守卫对齐（契约测试墓碑化） ([#196](https://github.com/hdot123/infraro-core/issues/196)) ([8068886](https://github.com/hdot123/infraro-core/commit/80688864bb5e50230d51bbef43ca821115e8b915))
* README 对齐引擎迁移终态 ([#201](https://github.com/hdot123/infraro-core/issues/201)) ([fc83128](https://github.com/hdot123/infraro-core/commit/fc831286f83598f1c9100c947dd776d440736e92))
* README 补齐引擎迁移终态遗漏面 (INFRA-739) ([#202](https://github.com/hdot123/infraro-core/issues/202)) ([d3a013d](https://github.com/hdot123/infraro-core/commit/d3a013d267115d955fb0ad8eba39d39787baf5d5))

## [0.11.0](https://github.com/hdot123/infraro-core/compare/v0.10.0...v0.11.0) (2026-09-02)


### Features

* **cf-worker:** 四通道入站认证 + /webhook/posthog-error fail-closed 收紧（无路由行为变更） ([#190](https://github.com/hdot123/infraro-core/issues/190)) ([df56b5b](https://github.com/hdot123/infraro-core/commit/df56b5bd3cafa33b0358d17204284a2805e14cde))


### Bug Fixes

* **cf-worker:** M4 scrutiny 轮 1 移交修复（无路由行为变更） ([#186](https://github.com/hdot123/infraro-core/issues/186)) ([8ab4f1f](https://github.com/hdot123/infraro-core/commit/8ab4f1fdd515e9b055e9ca4b3127ff3159fb5fe8))
* **cf-worker:** 通道3改读 Linear-Signature 头名并锁定生产路由形状 ([#191](https://github.com/hdot123/infraro-core/issues/191)) ([4f5c73e](https://github.com/hdot123/infraro-core/commit/4f5c73e157fcff799c20037ca469937f115c901a))

## [0.10.0](https://github.com/hdot123/infraro-core/compare/v0.9.0...v0.10.0) (2026-09-02)


### Features

* **cf-worker:** gh-proxy 私有仓 PAT 注入 + 三层门禁版本化 ([#180](https://github.com/hdot123/infraro-core/issues/180)) ([c0ea2c7](https://github.com/hdot123/infraro-core/commit/c0ea2c7e4c6af0d61a2b874f48ab5805764da5f8))
* **cf-worker:** unified parity round 3 ([#184](https://github.com/hdot123/infraro-core/issues/184)) ([712a267](https://github.com/hdot123/infraro-core/commit/712a267fd5b7af8802cfdebab3e2a774e46034b2))


### Bug Fixes

* **release:** relock 步骤分支探测改用 gh 查询修复永久空跳过 ([#177](https://github.com/hdot123/infraro-core/issues/177)) ([536ec26](https://github.com/hdot123/infraro-core/commit/536ec26ba0d8ba1fd1c0551350bccdffe44fda86))

## [0.9.0](https://github.com/hdot123/infraro-core/compare/v0.8.0...v0.9.0) (2026-09-01)


### Features

* **ci:** add set -euo pipefail to notify job run blocks ([#151](https://github.com/hdot123/infraro-core/issues/151)) ([3c0073b](https://github.com/hdot123/infraro-core/commit/3c0073b4282ca67b7d5424411e5e13d9bbe88a7e))
* **platform:** F7 repo 级 Actions 平台策略锁定 (VAL-M3-001~012) ([#164](https://github.com/hdot123/infraro-core/issues/164)) ([e59d127](https://github.com/hdot123/infraro-core/commit/e59d12750debc1f0e4951bc036ef5a144739d69a))
* **platform:** F8 Rulesets 迁移与合并设置闭环 (VAL-M3-013~023) ([#166](https://github.com/hdot123/infraro-core/issues/166)) ([b1fb69f](https://github.com/hdot123/infraro-core/commit/b1fb69fb7802159f43c7e276b862e74a4b165254))


### Bug Fixes

* **infra:** uv.lock 根包版本对齐至 0.8.0 ([#163](https://github.com/hdot123/infraro-core/issues/163)) ([1e28fc4](https://github.com/hdot123/infraro-core/commit/1e28fc403342765508e0578df5bbdb31700c8b6f))
* **release:** uv.lock 根包版本守护与 Release PR 自动 relock (INFRA-712) ([#165](https://github.com/hdot123/infraro-core/issues/165)) ([e997363](https://github.com/hdot123/infraro-core/commit/e997363033979f25ede50a170c32b52abba31dd0))
* **workflow:** 恢复引擎仓自扫 evolution 定时触发面 (INFRA-717) ([#173](https://github.com/hdot123/infraro-core/issues/173)) ([d2db025](https://github.com/hdot123/infraro-core/commit/d2db0254b8130975d018d12bfc93a88b611bb93b))

## [0.8.0](https://github.com/hdot123/infraro-core/compare/v0.7.2...v0.8.0) (2026-08-31)


### Features

* **ci:** notify-ci-complete payload 补 run_url 字段 (INFRA-690) ([#139](https://github.com/hdot123/infraro-core/issues/139)) ([cde4139](https://github.com/hdot123/infraro-core/commit/cde41395ee00c2eadfddf00bc7cbcd2df4e39ae6))
* **workflow:** F5 bash 容错统一 + PRT 安全面审计 (VAL-M2-201~210) ([#138](https://github.com/hdot123/infraro-core/issues/138)) ([4d9bb3a](https://github.com/hdot123/infraro-core/commit/4d9bb3a92e780cd72a4ea1c630d418b30cfd134b))
* **workflow:** SHA 锁定所有外部 action 引用 (F3) ([#125](https://github.com/hdot123/infraro-core/issues/125)) ([9569b19](https://github.com/hdot123/infraro-core/commit/9569b191e15b7be4a7d8cf1b3276049a69f181bd))


### Bug Fixes

* add notify-ci-complete job to notify n8n after CI completion ([#137](https://github.com/hdot123/infraro-core/issues/137)) ([4103a09](https://github.com/hdot123/infraro-core/commit/4103a09961d81666000b564830d16a456c2b7b9f))
* **evolution:** 抑制 actions 自包含分发副本的固有重复块误报 (INFRA-691) ([#142](https://github.com/hdot123/infraro-core/issues/142)) ([7741b4b](https://github.com/hdot123/infraro-core/commit/7741b4b90f0d92c42e95528530ed462d3dec9dfa))
* **tests:** 修复 test_sha_references_have_version_comment 静默吞异常（INFRA-686） ([#133](https://github.com/hdot123/infraro-core/issues/133)) ([821501d](https://github.com/hdot123/infraro-core/commit/821501d5f85ca394a2d732d182146738585e18a1))
* **workflow:** F4 显式最小权限声明 — ci/qa 顶层基线 + review 系 job 级 + id-token 移除 (VAL-M2-101/102/103/104/105/106/107) ([#134](https://github.com/hdot123/infraro-core/issues/134)) ([b52961d](https://github.com/hdot123/infraro-core/commit/b52961dc5655554ab6c7723812466163ea9f75bf))
* **workflow:** 补全 4 个 workflow 顶层最小权限基线（INFRA-688） ([#135](https://github.com/hdot123/infraro-core/issues/135)) ([ae622a1](https://github.com/hdot123/infraro-core/commit/ae622a12a2225fd7580287268ad3f26c2f69d98b))


### Documentation

* F6 文档收尾 — §7 演进路线补 M6、config.yml 注释修正、skip 理由补记 ([#145](https://github.com/hdot123/infraro-core/issues/145)) ([1f5dc02](https://github.com/hdot123/infraro-core/commit/1f5dc023910452555dce25e9b4555150fc12cbf0))

## [0.7.2](https://github.com/hdot123/infraro-core/compare/v0.7.1...v0.7.2) (2026-08-31)


### Bug Fixes

* **evolution:** self-audit 心跳阈值 2h→8h 消除结构性 tick 间隔误报 (INFRA-651) ([#114](https://github.com/hdot123/infraro-core/issues/114)) ([5636d68](https://github.com/hdot123/infraro-core/commit/5636d6826379a2550ae7c4ca65930a96471f966e))

## [0.7.1](https://github.com/hdot123/infraro-core/compare/v0.7.0...v0.7.1) (2026-08-30)


### Bug Fixes

* **evolution:** check_config_yml 计入 rule_packs 展开生效工具数 ([#104](https://github.com/hdot123/infraro-core/issues/104)) ([64a4b55](https://github.com/hdot123/infraro-core/commit/64a4b55369d0f9776b175973cfb0249a08980c25))
* **evolution:** 修复引擎仓自扫配置并抑制固有布局误报 (INFRA-659) ([#102](https://github.com/hdot123/infraro-core/issues/102)) ([87b43b2](https://github.com/hdot123/infraro-core/commit/87b43b242dc13b0a2541550856718a6789be2bca))

## [0.7.0](https://github.com/hdot123/infraro-core/compare/v0.6.1...v0.7.0) (2026-08-30)


### Features

* **actions:** auto-merge 动作收编入本仓，退役 shared-workflows pin（VAL-HARD-104） ([#98](https://github.com/hdot123/infraro-core/issues/98)) ([cda47d7](https://github.com/hdot123/infraro-core/commit/cda47d7012b2a5a04d01ecec0261516581b02625))
* **engine:** auto_close_resolved 与 rule_id 域恒定落日志——VAL-CROSS-006/007 证据面 ([#99](https://github.com/hdot123/infraro-core/issues/99)) ([3954109](https://github.com/hdot123/infraro-core/commit/395410992eca5bf117919095bca3ed4b79b82629))


### Bug Fixes

* **evolution:** 探针失败不再冒充 severe outage，未知 staleness 用 None 表示 (INFRA-639) ([#87](https://github.com/hdot123/infraro-core/issues/87)) ([c23eab9](https://github.com/hdot123/infraro-core/commit/c23eab98d4647887aab18aaac4931845a518c834))


### Documentation

* **onboarding:** config 示例补齐引擎必填键，新增指南契约测试防回归（VAL-CROSS-008） ([#100](https://github.com/hdot123/infraro-core/issues/100)) ([3bf890b](https://github.com/hdot123/infraro-core/commit/3bf890bc86d4100b8849603d6a0cff6b6d7be283))
* QA 家族上线后同步命名契约与门禁矩阵（INFRA-596） ([#41](https://github.com/hdot123/infraro-core/issues/41)) ([77d5f0e](https://github.com/hdot123/infraro-core/commit/77d5f0e6a0600797c8255ea8726c86bb6d4bd4b9))
* **roadmap:** 中央调度路线图（VAL-HARD-106） ([#97](https://github.com/hdot123/infraro-core/issues/97)) ([698dff8](https://github.com/hdot123/infraro-core/commit/698dff834db2dfeb802ca87ebcb657ebb6dd1af5))

## [0.6.1](https://github.com/hdot123/infraro-core/compare/v0.6.0...v0.6.1) (2026-08-30)


### Bug Fixes

* **engine:** 修复 audit_layout adapter schema 漂移——消费真实 findings 输出并补 rule_id 全工具契约测试 ([#91](https://github.com/hdot123/infraro-core/issues/91)) ([8bf173c](https://github.com/hdot123/infraro-core/commit/8bf173cae5f8ec6ee0030e3763d95b0b1573a380))

## [0.6.0](https://github.com/hdot123/infraro-core/compare/v0.5.1...v0.6.0) (2026-08-30)


### Features

* **M5:** webhook-scripts 生产同步真源迁入本仓 + 消费仓接入指南 ([#81](https://github.com/hdot123/infraro-core/issues/81)) ([e067af5](https://github.com/hdot123/infraro-core/commit/e067af54038d1840ea56a7b20b431cdce1b94271))


### Bug Fixes

* **ci:** type-bundle 去重 mypy src 域——删除 Run mypy 重复步骤 ([#75](https://github.com/hdot123/infraro-core/issues/75)) ([b0cdbc2](https://github.com/hdot123/infraro-core/commit/b0cdbc211a2f1886851d422e870f1b3327122d98))
* **linear:** create path 回填新建 tracker URL——同步不再恒走 no-tracker skip ([#79](https://github.com/hdot123/infraro-core/issues/79)) ([480fee5](https://github.com/hdot123/infraro-core/commit/480fee534209384a07a07a19a2f338faaef8dea8))
* **linear:** 项目同步改用现行 issueUpdate mutation——VAL-GATE-118 真红根因修复 ([#76](https://github.com/hdot123/infraro-core/issues/76)) ([5485b40](https://github.com/hdot123/infraro-core/commit/5485b40af51dc8e3c9d2c09c07d72fa54641aa38))
* **M5:** reusable workflow_call 双形态键声明恢复——解锁消费仓门禁死锁 ([#86](https://github.com/hdot123/infraro-core/issues/86)) ([4f22a15](https://github.com/hdot123/infraro-core/commit/4f22a15bea759f5659352951f462dfdb5f410591))
* **pack:** 修复 pack↔engine 接缝三缺陷——error_patterns jsonl 声明/工具名统一 engine 键/daily audit 零宿主写 ([#80](https://github.com/hdot123/infraro-core/issues/80)) ([3efcc82](https://github.com/hdot123/infraro-core/commit/3efcc82765723df5351c5c0a396362474442b505))
* **workflows:** droid-review-shards reusable 移除顶层 concurrency——INFRA-626 系统性收尾 ([#73](https://github.com/hdot123/infraro-core/issues/73)) ([7f02477](https://github.com/hdot123/infraro-core/commit/7f024771b3d478302b4127814fd6622ed91bdff7))
* **workflows:** reusable 移除顶层 concurrency——caller 同名组自死锁 ([#72](https://github.com/hdot123/infraro-core/issues/72)) ([4592006](https://github.com/hdot123/infraro-core/commit/4592006198e8c6bc5737c7c6ea2002867c6de445))

## [0.5.1](https://github.com/hdot123/infraro-core/compare/v0.5.0...v0.5.1) (2026-08-29)


### Bug Fixes

* **engine:** __init__ 改 PEP 562 lazy export——消除与消费仓同名裸名模块的 import 碰撞 ([#70](https://github.com/hdot123/infraro-core/issues/70)) ([becff80](https://github.com/hdot123/infraro-core/commit/becff809d9e63d7251c6f0e918584cc99ddc36f8))

## [0.5.0](https://github.com/hdot123/infraro-core/compare/v0.4.0...v0.5.0) (2026-08-29)


### Features

* **auto-merge:** 抽离 auto-merge-pipeline reusable workflow——resolve+triage+merge 执行体（M4 门禁切换基建） ([#65](https://github.com/hdot123/infraro-core/issues/65)) ([05ca61c](https://github.com/hdot123/infraro-core/commit/05ca61c6dd13724fada13214efd91379279c17b6))
* **engine:** 移植 INFRA-578/588/597 自愈套件到 evolution 引擎——双向 workflow_dispatch 拉起 + 告警抑制 ([#67](https://github.com/hdot123/infraro-core/issues/67)) ([2872f94](https://github.com/hdot123/infraro-core/commit/2872f940ccd20f8690cee25e81d4cc37cf2d63a9))
* **runner:** 重构 Layer 1+2 宿主工具链锁定与共享缓存（取代 PR [#37](https://github.com/hdot123/infraro-core/issues/37)，INFRA-590） ([#56](https://github.com/hdot123/infraro-core/issues/56)) ([b146a58](https://github.com/hdot123/infraro-core/commit/b146a5850aefe80da02f8e762ad180addf4116b7))
* **watchdog:** 抽离 droid-review-watchdog-handlers reusable workflow（M4 门禁切换基建） ([#64](https://github.com/hdot123/infraro-core/issues/64)) ([0270da1](https://github.com/hdot123/infraro-core/commit/0270da1434b3307582545798f2b6de204e43e4f0))


### Bug Fixes

* **ci:** droid CLI fallback 下载加 --max-time 300 兜底（Fixes INFRA-613） ([#63](https://github.com/hdot123/infraro-core/issues/63)) ([80a4461](https://github.com/hdot123/infraro-core/commit/80a44610a97f8ae97c22093ab718c1838af5057b))

## [0.4.0](https://github.com/hdot123/infraro-core/compare/v0.3.0...v0.4.0) (2026-08-29)


### Features

* ci-ok 轮询等待 droid-review 完成后再放行（INFRA-598） ([#44](https://github.com/hdot123/infraro-core/issues/44)) ([3e04580](https://github.com/hdot123/infraro-core/commit/3e04580bb7096d98487e301b2c2c851b0c635444))
* **droid-review:** Factory CLI 安装宿主优先化——PATH 探测 + 版本下限 gate，缺失才 fallback 下载 ([#62](https://github.com/hdot123/infraro-core/issues/62)) ([d9489f4](https://github.com/hdot123/infraro-core/commit/d9489f4c8b4dedd75778bc877e16ee1b658e067b))
* **droid-review:** 抽离 droid-review-shards reusable workflow + aggregate composite（M4 门禁切换基建） ([#59](https://github.com/hdot123/infraro-core/issues/59)) ([6dfb73a](https://github.com/hdot123/infraro-core/commit/6dfb73aa998182a4a05b3b244407f0178c1a0c1a))
* enforce zero-red merge policy (ci-ok blocks any red check) ([#38](https://github.com/hdot123/infraro-core/issues/38)) ([708b995](https://github.com/hdot123/infraro-core/commit/708b9954e848a8731fcefc6c12d72051ef87f125))
* **gate:** 新增 QA workflow 家族 (gate-infra-qa-workflow) ([#39](https://github.com/hdot123/infraro-core/issues/39)) ([1e6ae15](https://github.com/hdot123/infraro-core/commit/1e6ae15058a8ccf36a4d2d3dce9af850866dbf07))
* **runner:** setup-venv fast-fail 加固 + infra-cli venv create 便利入口 ([#45](https://github.com/hdot123/infraro-core/issues/45)) ([8396748](https://github.com/hdot123/infraro-core/commit/83967484adfa5535b80fd466f3d5bee102c73287))
* 启用 droid-review 门禁（PR-A：workflow 启用） ([#43](https://github.com/hdot123/infraro-core/issues/43)) ([0702c9e](https://github.com/hdot123/infraro-core/commit/0702c9ed981234837e8b58dcfb383a14aff64a13))
* 恢复自仓 auto-merge 触发器（memory-core 同构 + triage 路径修复） ([#48](https://github.com/hdot123/infraro-core/issues/48)) ([da4ff28](https://github.com/hdot123/infraro-core/commit/da4ff28970750ccb3bc4ad69cd8f1076f6db2441))


### Bug Fixes

* branch-cleanup 状态隔离重做，基于新 main 重构 PR [#35](https://github.com/hdot123/infraro-core/issues/35)（INFRA-589） ([#57](https://github.com/hdot123/infraro-core/issues/57)) ([6cedfe7](https://github.com/hdot123/infraro-core/commit/6cedfe736d9d7745a21a64d07c3b5f8b7bf5f9a4))
* **ci:** actionlint 步骤宿主优先，免疫 [REDACTED-HOST] raw 直连黑洞 ([#53](https://github.com/hdot123/infraro-core/issues/53)) ([aac6a5a](https://github.com/hdot123/infraro-core/commit/aac6a5ae8b305ba920cba21bf7586f3cc993882d))
* **ci:** auto-merge 去 checkout 内联 triage 至 RUNNER_TEMP，根除共享工作区 sparse-checkout 污染 ([#50](https://github.com/hdot123/infraro-core/issues/50)) ([6f615fa](https://github.com/hdot123/infraro-core/commit/6f615faa7db75a76eeb08447610c49233f760720))
* **ci:** branch-cleanup thin caller 转发仓级 LINEAR_PROJECT_INFRA_CORE_ID（INFRA-606） ([#54](https://github.com/hdot123/infraro-core/issues/54)) ([f65270b](https://github.com/hdot123/infraro-core/commit/f65270bab2bc4b122a35dc020deae486f1506e78))
* **ci:** check_droid_review.sh 双副本网络韧性加固 + 副本字节一致防护 ([#58](https://github.com/hdot123/infraro-core/issues/58)) ([60a3acc](https://github.com/hdot123/infraro-core/commit/60a3acc97facda17db5107b1363f3995d57be8c8))
* **ci:** gh 调用仓库上下文守卫全量覆盖 engine 与工作流（INFRA-601） ([#51](https://github.com/hdot123/infraro-core/issues/51)) ([2742171](https://github.com/hdot123/infraro-core/commit/2742171ba807557a63cacad207786a506b54fc73))
* **ci:** 守卫与 shell 脚本 gh 调用显式仓库上下文，免疫 runner insteadOf 镜像重写 ([#49](https://github.com/hdot123/infraro-core/issues/49)) ([4ef996a](https://github.com/hdot123/infraro-core/commit/4ef996a106374e245dd173c819b3578c9651d6de))
* **droid-review:** exit 137 完成期竞态韧性——单次重试 + session jsonl 兜底恢复 ([#55](https://github.com/hdot123/infraro-core/issues/55)) ([135a1b5](https://github.com/hdot123/infraro-core/commit/135a1b57d34448743c88cc33e59da55e62b2cb14))
* Linear project 同步按仓库归约 tracking issue（INFRA-586） ([#36](https://github.com/hdot123/infraro-core/issues/36)) ([ffcc1b1](https://github.com/hdot123/infraro-core/commit/ffcc1b16b1978839704972fc1c89f9c1e17c4712))
* 零红铁律落地——移除 advisory job 的 continue-on-error（INFRA-595） ([#40](https://github.com/hdot123/infraro-core/issues/40)) ([833e6b8](https://github.com/hdot123/infraro-core/commit/833e6b84d5dd579040ead0af3900f672e1bdc615))


### Performance Improvements

* **droid-review:** BYOM 改走 ts 内网直达 Kong，解开公网绕行 ([#47](https://github.com/hdot123/infraro-core/issues/47)) ([89ef28a](https://github.com/hdot123/infraro-core/commit/89ef28a85db5a96290a600d680d815701d06bc48))

## [0.3.0](https://github.com/hdot123/infraro-core/compare/v0.2.0...v0.3.0) (2026-08-27)


### Features

* **ci:** 基础层第二步 - 结构化门禁接线 ([#21](https://github.com/hdot123/infraro-core/issues/21)) ([38b6289](https://github.com/hdot123/infraro-core/commit/38b628910f842be31030e08b3add031b4ff7d6e4))
* **ci:** 补齐基础层五道门禁（shellcheck/health-check/repo-consistency/telemetry-audit/business-policy-tests） ([#25](https://github.com/hdot123/infraro-core/issues/25)) ([9d525d8](https://github.com/hdot123/infraro-core/commit/9d525d872eb56b93d005b2a5ee6f5ea51cb6daab))
* **guard:** 落地门禁守卫资产与 pytest markers 基础设施 ([#20](https://github.com/hdot123/infraro-core/issues/20)) ([ad8c756](https://github.com/hdot123/infraro-core/commit/ad8c756fa888c69d21d172a74e8efc3333d54de6))
* INFRA-583 M4 收尾——branch-cleanup 自仓切 thin caller 并加双副本漂移防护 ([#27](https://github.com/hdot123/infraro-core/issues/27)) ([e500f37](https://github.com/hdot123/infraro-core/commit/e500f376ceb38a12f014de6e211474f006815920))
* M4 branch-cleanup composite action（定时+即时双模式） ([#26](https://github.com/hdot123/infraro-core/issues/26)) ([6cf8bc9](https://github.com/hdot123/infraro-core/commit/6cf8bc95ddaabbb15b5fc18a23f1c37f63c4c0f3))
* **M4:** 添加 setup-labels reusable workflow 与契约测试 ([#30](https://github.com/hdot123/infraro-core/issues/30)) ([afe6570](https://github.com/hdot123/infraro-core/commit/afe65707cdf0695bb1c91b8fbb553d38d9e5e47c))


### Bug Fixes

* 修复 rule_packs 懒加载并更新契约测试 ([#23](https://github.com/hdot123/infraro-core/issues/23)) ([6164aa3](https://github.com/hdot123/infraro-core/commit/6164aa3e867e69fe5dd791b0f91652b61f335cb0))

## [0.2.0](https://github.com/hdot123/infraro-core/compare/v0.1.0...v0.2.0) (2026-08-26)


### Features

* **M3:** version_sync 迁移 ([#18](https://github.com/hdot123/infraro-core/issues/18)) ([ccf09c3](https://github.com/hdot123/infraro-core/commit/ccf09c3ade9cba75e48e293271fdb75c05a25c98))

## 0.1.0 (2026-08-26)


### Features

* M1 scaffold - infra-core 组织级演进引擎自举 ([25d438b](https://github.com/hdot123/infraro-core/commit/25d438b6d66d3a0a236ca1eb7f2ec746cbdd055f))
* M1 scaffold - infra-core 组织级演进引擎自举 ([25be345](https://github.com/hdot123/infraro-core/commit/25be34504db0db261b5a2e53862354a91c829878))
* memory 规则包迁入 infra-core packs/memory/ ([#12](https://github.com/hdot123/infraro-core/issues/12)) ([889099d](https://github.com/hdot123/infraro-core/commit/889099d21868d20b318727364502ad10a517eefe))
* 全量切换自建 runner + per-run venv 隔离（重提，绕开卡死 run） ([#10](https://github.com/hdot123/infraro-core/issues/10)) ([0764fb2](https://github.com/hdot123/infraro-core/commit/0764fb225f7e6850a4d1fd8608ef8d3c4085e064))
* 引擎移植（M2）- 自 memory-core 移植演进引擎至 infra-core ([#7](https://github.com/hdot123/infraro-core/issues/7)) ([ac9e3a1](https://github.com/hdot123/infraro-core/commit/ac9e3a14341bef2b0dc738833f74d289d08f00f9))
* 配置 release-please 自动发版基建 ([#14](https://github.com/hdot123/infraro-core/issues/14)) ([49673d3](https://github.com/hdot123/infraro-core/commit/49673d357060e934d4d8ac63589cd25f1790b02f))


### Bug Fixes

* .gitignore 补全 memory-hook 产物屏蔽（AGENTS.md、tools/） ([#6](https://github.com/hdot123/infraro-core/issues/6)) ([c98892e](https://github.com/hdot123/infraro-core/commit/c98892e3d4912c0cb3dd65e53829781222198f8d))
* governance action 脚本路径修复 + 契约测试加固 + .gitignore 防护 ([#5](https://github.com/hdot123/infraro-core/issues/5)) ([6e9b268](https://github.com/hdot123/infraro-core/commit/6e9b26809cbdb27d8857e346b8f05925f98d49ca))
* M2 热修——六 workflow 触发器禁用为 dispatch-only 桩 + .evolution 运行态出库 + rule_packs/report-only/pack_tool 单测 ([#8](https://github.com/hdot123/infraro-core/issues/8)) ([1c664cf](https://github.com/hdot123/infraro-core/commit/1c664cfd0a5b4f385d02a3e31530e3cda92d0563))
* release-please workflow 使用 DISPATCH_TOKEN 替代 GITHUB_TOKEN ([#15](https://github.com/hdot123/infraro-core/issues/15)) ([6a1a208](https://github.com/hdot123/infraro-core/commit/6a1a2086f256cd57d7efed4ca3507987b235556c))
* 修复 ruff 格式问题（空白行、未使用导入） ([29bf650](https://github.com/hdot123/infraro-core/commit/29bf65065cae5bd13ee21e083362cfc3f9d0ebcc))
* 补齐 cli.py 函数类型注解，满足 mypy --strict 门禁 ([#4](https://github.com/hdot123/infraro-core/issues/4)) ([dc0a1b8](https://github.com/hdot123/infraro-core/commit/dc0a1b8b623ff42a0e8d6de4babe756e3c699405))
