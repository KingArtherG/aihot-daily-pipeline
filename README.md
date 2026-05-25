# AI HOT Daily Pipeline

用 AI HOT 作为信息源，自动生成中文 AI 早报、Markdown 备份、RSS 和 GitHub Pages 静态站点。

## 它做什么

- 每天从 AI HOT 拉取最新 AI 资讯
- 生成 `BACKUP/YYYY-MM-DD.md`
- 生成 `public/index.html`、每日文章页和 `public/rss.xml`
- 生成 `cards/YYYY-MM-DD.json`，方便继续喂给新闻卡片/视频生成工具
- 通过 GitHub Actions 每天自动运行并部署到 GitHub Pages

默认运行时间是北京时间每天 08:30。

## 快速部署

1. 新建一个 GitHub 仓库，例如 `my-ai-daily`
2. 把本目录内容推上去
3. 在 GitHub 仓库设置里启用 Pages，Source 选择 `GitHub Actions`
4. 打开 Actions，手动运行一次 `Build AI HOT Daily`

推送后，站点地址通常是：

```text
https://你的GitHub用户名.github.io/仓库名/
```

## 本地运行

```bash
python scripts/build_aihot_daily.py
```

可选环境变量：

```bash
SITE_TITLE="我的 AI 早报"
AUTHOR_NAME="你的名字"
AIHOT_SOURCE="daily"
AIHOT_TAKE="30"
AIHOT_HOURS="24"
BASE_URL="https://yourname.github.io/my-ai-daily/"
python scripts/build_aihot_daily.py
```

`AIHOT_SOURCE` 支持：

- `daily`：使用 AI HOT 已整理好的日报，适合每天 08:00 之后运行
- `selected`：使用最近一段时间的精选条目，适合滚动资讯流

## 连接视频卡片工具

每天会生成：

```text
cards/YYYY-MM-DD.json
```

这份 JSON 可以喂给 `juya-news-card`。最简单的做法是先用网页工具手动导入几条，跑顺后再写脚本调用它的 `/api/generate` 和 `/api/render`。

## 注意

AI HOT 的摘要是 AI 辅助生成。公开发布前，建议人工核对重点条目的原文链接，尤其是模型参数、价格、发布日期、公司公告这类容易误读的信息。
