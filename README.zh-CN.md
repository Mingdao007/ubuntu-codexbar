# ubuntu-codexbar

English README: [README.md](README.md)

这是一个面向 Ubuntu 的公开仓库，包含一个给 Codex 用的 skill，以及当前
`codexbar` Python CLI，用来管理本地保存的 Codex 登录档案和 usage 视图。

## 覆盖问题

- 管理 `~/.codexbar/profiles` 下的多套保存登录
- 区分保存的 `active_profile` 和当前磁盘 root auth
- 查看本地 usage、各 profile 的缓存 usage、以及 current-root live usage
- 解释重复身份、stale state 和 canonical profile 规则

## 主要内容

- `ubuntu-codexbar/`: 可安装的 Codex skill
- `src/codexbar/`: Python CLI 实现
- `tests/`: pytest 测试

## 安装

1. 在仓库根目录安装 CLI：

```bash
python3 -m pip install --user --no-deps --no-build-isolation .
```

2. 把 `ubuntu-codexbar/` 复制到 `${CODEX_HOME:-$HOME/.codex}/skills/`。
3. 重启 Codex 或刷新 skills。
4. 用 `$ubuntu-codexbar` 调用这个 skill。

## 致谢

- [`isxlan0/Codex_AccountSwitch`](https://github.com/isxlan0/Codex_AccountSwitch)
- [`lizhelang/codexbar`](https://github.com/lizhelang/codexbar)
