# 直播吧比赛热门评论爬取方案

## 需求概述

从直播吧(zhibo8.com)比赛赛后主页获取**热门评论区**的数据，包括：
- 评论文本内容
- 点赞数（顶）
- 截图保存
- MySQL存储

## 目标URL格式

```
https://www.zhibo8.com/nba/{YYYY}/{MMDD}-match{MATCH_ID}v-{team}.htm
示例: https://www.zhibo8.com/nba/2026/0525-match1984335v-jijin.htm
```

## 页面结构分析（基于截图）

```
┌─────────────────────────────────────────────────────┐
│  直播吧导航栏                                        │
├──────────────┬──────────────────────────────────────┤
│              │  ┌─ 热门评论（红框区域）────────────┐ │
│  比赛信息     │  │ 🧑 球友 PnP41Lxa  05-25 10:50    │ │
│  OKC 82      │  │ 顶(2323) 踩(112)                 │ │
│  vs          │  │ 当裁判队失去裁判 😭😭😭            │ │
│  SAS 103     │  │ [回复] [收起回复(167)]            │ │
│              │  ├──────────────────────────────────┤ │
│  视频列表     │  │ 天使之翼_10  05-25 10:52  举 报   │ │
│  ...         │  │ 顶(34) 踩(276)  [回复]             │ │
│              │  │ 裁判队？下载最新直播吧客户端查看图片   │ │
└──────────────┴──┴────────────────────────────────────┘
```

每条评论包含：
- **用户信息**: 头像、昵称、发布时间
- **互动数据**: 顶(点赞数)、踩(回复数)
- **评论内容**: 文本正文
- **操作**: 回复、收起回复

---

## 实现步骤

### Step 1: 创建数据模型 `models/zhibo8_comment_model.py`

定义直播吧评论的数据结构：

```python
@dataclass
class Zhibo8CommentModel:
    match_url: str = ""           # 比赛URL
    match_id: str = ""            # 比赛ID (如 1984335)
    match_title: str = ""         # 比赛标题 (如 OKC vs SAS)
    comment_id: str = ""          # 评论唯一标识
    author_name: str = ""         # 评论者昵称
    content_text: str = ""        # 评论文本
    like_count: int = 0           # 顶(点赞数)
    reply_count: int = 0          # 踩(回复数)
    publish_time: str = ""        # 发布时间
    screenshot_path: str = ""     # 截图路径
```

### Step 2: 创建数据库表

在 `mysql_manager.py` 的 `_ensure_tables()` 中新增表：

```sql
CREATE TABLE IF NOT EXISTS zb8_match_comment (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    match_url VARCHAR(500) DEFAULT '',
    match_id VARCHAR(50) DEFAULT '',
    match_title VARCHAR(500) DEFAULT '',
    comment_id VARCHAR(200) DEFAULT '',
    author_name VARCHAR(200) DEFAULT '',
    content_text TEXT,
    like_count INT DEFAULT 0,
    reply_count INT DEFAULT 0,
    publish_time VARCHAR(50) DEFAULT '',
    screenshot_path VARCHAR(500) DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_match_comment (match_url(255), comment_id(100)),
    INDEX idx_match_id (match_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
```

同时在 `MySQLManager` 中新增方法：
- `save_zhibo8_comment(comment_data)` — 单条插入
- `save_zhibo8_comments_batch(comments_data)` — 批量插入+去重(match_url + comment_id)

### Step 3: 创建核心Skill `skills/zhibo8_match_scraper.py`

新建独立Skill类 `Zhibo8MatchScraper(BaseSkill)`，核心方法：

#### 3.1 `_get_match_info(page, url)` → 提取比赛基本信息
- 从页面提取：主队、客队、比分、比赛日期
- 选择器定位：比分板区域 `.scoreboard` 或类似DOM

#### 3.2 `_get_hot_comments(page)` → 提取热门评论列表
- 定位"热门评论"区域（右侧评论栏）
- JS提取每条评论的：
  - `author_name`: 用户昵称
  - `publish_time`: 发布时间
  - `like_count`: 解析 `顶(N)` 格式
  - `reply_count`: 解析 `踩(N)` 格式
  - `content_text`: 评论正文文本
- 返回 `List[Zhibo8CommentModel]`

#### 3.3 `_screenshot_comment(page, comment_index, screenshot_path)` → 单条评论截图
- 定位到第N条评论元素
- 使用 `element.screenshot()` 或 `page.screenshot(clip=...)` 截取
- 隐藏不必要的导航栏等元素
- 截图保存到 `data/screenshots/zhibo8_comments/` 目录

#### 3.4 `scrape_match(url)` → 主流程（公开方法）
```
1. navigate_to(page, url) → 打开比赛页面
2. 等待页面加载完成（等待评论区域出现）
3. _get_match_info() → 获取比赛信息
4. _get_hot_comments() → 提取所有热门评论
5. 循环每条评论:
   a. 滚动到该评论位置
   b. _screenshot_comment() → 截图
   c. 构建完整的 Zhibo8CommentModel 对象
6. 批量保存到MySQL
7. 返回结果统计
```

### Step 4: 创建独立入口 `zhibo8_main.py`

参照 `author_monitor_main.py` 的模式，创建独立入口：

```python
# 用法:
python zhibo8_main.py --url "https://www.zhibo8.com/nba/2026/0525-match1984335v-jijin.htm"
python zhibo8_main.py --match-id 1984335 --date 2026/0525 --team jijin
python zhibo8_main.py --match-id 1984335 --date 2026/0525 --team jijin --headless false
```

参数说明：
- `--url`: 完整的比赛URL（优先级最高）
- `--match-id`: 比赛ID
- `--date`: 比赛日期 YYYY/MMDD
- `--team`: 对阵球队标识（用于拼接URL）
- `--headless`: 是否无头浏览器模式

### Step 5: 配置文件扩展 `config.yaml`

在 config.yaml 末尾追加：

```yaml
zhibo8:
  enabled: true
  base_url: "https://www.zhibo8.com"
  max_comments: 30          # 最大抓取评论数
  screenshot_dir: "./data/screenshots/zhibo8_comments"
  wait_for_comments: 8000   # 等待评论区加载(ms)
```

### Step 6: DOM诊断与选择器适配（关键）

需要先运行一次诊断脚本，确认直播吧页面的实际DOM结构：

1. **评论区容器选择器**: 确定热门评论区域的父元素class/id
2. **单条评论选择器**: 确定每条评论的DOM结构
3. **顶/踩数据选择器**: 确认点赞数的提取方式
4. **截图范围**: 确定单条评论的精确截取边界

---

## 文件变更清单

| 操作 | 文件路径 | 说明 |
|------|---------|------|
| 新建 | `models/zhibo8_comment_model.py` | 直播吧评论数据模型 |
| 新建 | `skills/zhibo8_match_scraper.py` | 核心爬取Skill |
| 新建 | `zhibo8_main.py` | 独立运行入口 |
| 修改 | `utils/mysql_manager.py` | 新增zb8_match_comment表+CRUD方法 |
| 修改 | `config.yaml` | 新增zhibo8配置段 |

## 与现有代码的关系

- **完全独立模块**：不修改 `pipeline.py`、`main.py`、`author_monitor.py` 等已有文件
- **复用基础设施**：复用 `BrowserController`、`ConfigManager`、`MySQLManager`、`setup_logger`
- **遵循相同模式**：参照 `author_monitor.py` 的独立模块设计模式
