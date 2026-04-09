# ubuntu-codexbar

English README: [README.md](README.md)

这是一个面向 Ubuntu 的公开仓库，包含一个给 Codex 用的 skill，以及当前
`codexbar` Python CLI，用来管理本地保存的 Codex 登录档案和 usage 视图。

## 覆盖问题

- 管理 `~/.codexbar/profiles` 下的多套保存登录
- 区分保存的 `active_profile` 和当前磁盘 root auth
- 查看 current-root usage 和各 profile 保存下来的 session snapshot
- 解释重复身份、stale state 和 canonical profile 规则

## 主要内容

- `ubuntu-codexbar/`: 可安装的 Codex skill
- `src/codexbar/`: Python CLI 实现
- `tests/`: pytest 测试

## 安装

1. 在仓库根目录安装 CLI：

```bash
python3 -m pip install --user --no-deps --no-build-isolation -e .
```

安装后验证 `codexbar` 确实来自当前仓库：

```bash
python3 -c "import codexbar; print(codexbar.__file__)"
```

2. 把 `ubuntu-codexbar/` 复制到 `${CODEX_HOME:-$HOME/.codex}/skills/`。
3. 重启 Codex 或刷新 skills。
4. 用 `$ubuntu-codexbar` 调用这个 skill。

`codexbar usage --all --refresh` 目前还保留作兼容参数，但不会再触发 live
saved-profile probing。

## 致谢

- [`isxlan0/Codex_AccountSwitch`](https://github.com/isxlan0/Codex_AccountSwitch)
- [`lizhelang/codexbar`](https://github.com/lizhelang/codexbar)
