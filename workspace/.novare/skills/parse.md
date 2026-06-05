---
name: parse
description: 解析论文 PDF 并提取关键信息
---
请解析以下论文：$ARGUMENTS

执行步骤：
1. 如果输入是关键词，先用 paper_search 找到论文
2. 如果输入是论文 ID 或 URL，直接用 paper_parse 解析
3. 解析完成后，提取以下信息：
   - 研究问题（Research Question）
   - 方法（Methodology）
   - 关键发现（Key Findings）
   - 局限性（Limitations）
   - 主要贡献（Contributions）
4. 用中文输出结构化摘要
