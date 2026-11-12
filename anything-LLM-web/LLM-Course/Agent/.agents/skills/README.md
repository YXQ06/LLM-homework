# Agent Skills 目录

xAgent 在启动时扫描 `.agents/skills/*/SKILL.md`，只读取每个文件的
YAML Frontmatter（`name` / `description`）注入系统提示词；技能正文只在
模型决定调用该技能时才加载。

## 版本库中保留什么

| 内容 | 是否入库 | 说明 |
|---|---|---|
| `example-hello/` | ✅ | 自建的最小示例技能，演示 frontmatter + 正文结构 |
| 其他第三方整包技能 | ❌ | 见 `.gitignore` 的 `.agents/skills/*` 规则，仓库只保留示例，避免把整包第三方仓库（含 LICENSE、CI 模板、3.5MB 截图）复制进来 |

## 为什么第三方技能不入库

1. `.agents/` 是运行时目录，不是源码目录；技能包体积大（单个可达 6MB），会无端膨胀仓库；
2. 整包复制第三方仓库无法体现“自己实现”的部分，容易被视为未改造的搬运；
3. 需要某个第三方技能时，按以下任一方式获取即可：
   - 在 xAgent 中让模型调用 `createSkill` 生成；
   - 手动把技能目录放到 `.agents/skills/<name>/`（本地生效，不入库）。

## 自己写一个技能

```text
.agents/skills/my-skill/
└── SKILL.md
```

`SKILL.md` 开头必须是 YAML Frontmatter：

```markdown
---
name: my-skill
description: 一句话说明这个技能解决什么问题、什么时候用
---

# 正文：具体步骤、约束、示例
```

`name` 只能由小写字母、数字、连字符组成，且与目录名一致。
