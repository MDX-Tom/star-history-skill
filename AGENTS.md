# AGENTS.md - star-history-skill

- 默认使用简体中文回复；代码、命令、路径和 GitHub 标识保持原文。
- 修改公开说明时同步维护 `README.md` 与 `README_EN.md`。
- Skill 主体位于 `skills/deploy-star-history-pages/`；模板放在 `assets/`，可执行工具放在 `scripts/`，详细运维说明放在 `references/`。
- 不把 GitHub Token、Secrets 或真实凭据写入仓库、示例、日志和截图。
- 修改 Skill 后运行 `quick_validate.py`、Python 编译检查和临时仓库安装测试。
- `codex/` 保存本地执行状态并保持忽略；交接时先读 `codex/codex.md`、`codex/NEXT_GOAL.md` 与 `codex/TODO.md`。
