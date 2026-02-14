# qianli (千里)

Search Chinese content platforms from the terminal via Chrome DevTools Protocol.

## Sources

| Source | Auth | Status |
|--------|------|--------|
| **WeChat 公众号** (via Sogou) | No | Stable |
| **36kr** (36氪) | No | Stable (slow SPA) |
| **XHS** (小红书) | CDP login | Fragile (anti-bot) |

## Prerequisites

- Chrome running with `--remote-debugging-port=9222`
- Python 3.11+
- XHS requires one-time QR login in the CDP Chrome profile

## Install

```bash
pip install qianli
```

## Usage

```bash
# Search individual sources
qianli wechat "AI 银行"
qianli 36kr "大模型 金融"
qianli xhs "AI banking"

# Search all sources
qianli all "AI banking"

# Read full page content
qianli read https://mp.weixin.qq.com/s/...

# JSON output
qianli wechat "AI" --json

# Limit results
qianli wechat "AI" --limit 3
```

## Output

```
[wechat] 香港金管局GenAI沙盒首批成果发布
         机器之心 · 2025-11-15
         https://mp.weixin.qq.com/s/xxx

[36kr] AI驱动的银行数字化转型
       36氪 · 2025-12-03
       https://36kr.com/p/123456
```

## License

MIT
