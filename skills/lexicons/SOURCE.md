# 合规词库来源

文件来自开源词库 [konsheng/Sensitive-lexicon](https://github.com/konsheng/Sensitive-lexicon)（Vocabulary/ 目录，MIT License），
经 jsdelivr 镜像下载、去重合并、ASCII 重命名，仅保留内容合规相关类别：

| 本仓库文件 | 原文件 |
|---|---|
| political.txt | 政治类型.txt + 反动词库.txt |
| terror.txt | 暴恐词库.txt + 涉枪涉爆.txt |
| porn.txt | 色情类型.txt + 色情词库.txt |
| ads.txt | 广告类型.txt |

情绪类 L1/L2 业务信号词不在此列（见 agents/sensitive_words.py 手工维护）。
更新方式：直接编辑本目录 `*.txt` 即可，运行时会按文件 mtime/size 自动热加载并重建 AC 自动机，无需重启服务；`agents/sensitive_words.py` 中的业务词表仍需重启服务才会生效。
