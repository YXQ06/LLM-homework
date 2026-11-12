---
name: example-hello
description: 示例技能：演示 xAgent 的 Agent Skills 机制（frontmatter 只暴露 name/description，正文按需注入）。把本目录复制改名即可创建自己的技能。
---

# 示例技能：example-hello

这是一个**自建的最小技能骨架**，用于演示 xAgent 的技能发现与加载机制，
同时也是 `createSkill` 工具生成结果的标准结构参考。

## 何时使用

当用户说“用 example-hello 试一下技能机制”，或需要验证技能目录是否被正确加载时。

## 步骤

1. 确认当前工作目录的 `.agents/skills/example-hello/SKILL.md` 存在；
2. 回复一行：`example-hello 技能已加载 ✅`；
3. 说明技能机制：初始化时只读取 `name` 与 `description` 注入系统提示词，
   正文只在模型决定使用该技能时才加载，避免占满上下文。

## 约束

- 本技能不读写任何文件，纯演示；
- 技能名必须与目录名一致，且只能包含小写字母、数字与连字符。
