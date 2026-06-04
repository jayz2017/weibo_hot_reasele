#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
直播吧比赛热门评论爬取 - 独立运行入口

从直播吧比赛赛后主页获取热门评论区数据，
包括评论文本、点赞数、截图，存入MySQL。

使用方式:
    # 使用完整URL
    python zhibo8_main.py --url "https://www.zhibo8.com/nba/2026/0525-match1984335v-jijin.htm"

    # 使用参数拼接URL
    python zhibo8_main.py --match-id 1984335 --date 2026/0525 --team jijin

    # 有头模式（显示浏览器）
    python zhibo8_main.py --url "https://www.zhibo8.com/nba/2026/0525-match1984335v-jijin.htm" --headless false
"""

import asyncio
import argparse
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.config_manager import ConfigManager
from skills.browser_controller import BrowserController
from skills.zhibo8_match_scraper import Zhibo8MatchScraper
from utils.mysql_manager import MySQLManager
from utils.logger import setup_logger


def parse_args():
    parser = argparse.ArgumentParser(
        description='直播吧比赛热门评论爬取',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python zhibo8_main.py --url "https://www.zhibo8.com/nba/2026/0525-match1984335v-jijin.htm"
  python zhibo8_main.py --match-id 1984335 --date 2026/0525 --team jijin
  python zhibo8_main.py --url "https://..." --headless false
        """
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--url', '-u',
        type=str,
        default=None,
        help='完整的比赛页面URL'
    )
    group.add_argument(
        '--match-id',
        type=str,
        default=None,
        help='比赛ID (需配合 --date 和 --team 使用)'
    )

    parser.add_argument(
        '--date', '-d',
        type=str,
        default=None,
        help='比赛日期 YYYY/MMDD (如 2026/0525)'
    )

    parser.add_argument(
        '--team', '-t',
        type=str,
        default=None,
        help='球队标识 (如 jijin, kexue 等)'
    )

    parser.add_argument(
        '--config', '-c',
        type=str,
        default='config.yaml',
        help='配置文件路径 (默认: config.yaml)'
    )

    parser.add_argument(
        '--headless',
        type=str,
        choices=['true', 'false'],
        default='true',
        help='是否使用无头浏览器模式 (默认: true)'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='启用详细日志输出'
    )

    return parser.parse_args()


def build_url(args) -> str:
    if args.url:
        return args.url

    match_id = args.match_id or ''
    date = args.date or ''
    team = args.team or ''

    if not all([match_id, date, team]):
        print("错误: 使用 --match-id 时必须同时指定 --date 和 --team")
        sys.exit(1)

    return f"https://www.zhibo8.com/nba/{date}-match{match_id}v-{team}.htm"


async def run_zhibo8_scraper(config_path: str, url: str, headless: bool = True, verbose: bool = False):
    print("\n" + "=" * 70)
    print("🏀 直播吧比赛热门评论爬取系统")
    print("=" * 70)
    print(f"🎯 目标URL: {url}")
    print(f"🌐 浏览器模式: {'无头' if headless else '有头'}")
    print(f"⚙️  配置文件: {config_path}")
    print("=" * 70 + "\n")

    config_manager = ConfigManager(config_path)
    config = config_manager.config

    if not headless:
        config['browser']['headless'] = False

    logger = setup_logger("Zhibo8", level="DEBUG" if verbose else "INFO")

    browser = BrowserController(config, logger)
    await browser.start_browser()
    simple_browser, pw = await browser.create_standalone_context()

    mysql_config = config.get('mysql', {})
    mysql = None
    if mysql_config.get('enabled', False):
        mysql = MySQLManager(mysql_config, logger)

    try:
        scraper = Zhibo8MatchScraper(config, logger, browser=simple_browser, mysql=mysql)
        result = await scraper.scrape_match(url)

        error = result.get('error')
        if error:
            print(f"\n❌ 爬取失败: {error}")
            return 1

        match_info = result.get('match_info', {})
        comments_total = result.get('comments_total', 0)
        comments_processed = result.get('comments_processed', 0)

        print(f"\n{'='*70}")
        print(f"✅ 直播吧评论爬取完成！")
        print(f"   比赛: {match_info.get('match_title', '未知')}")
        print(f"   评论总数: {comments_total}")
        print(f"   处理数量: {comments_processed}")
        print(f"   URL: {url}")
        print(f"{'='*70}")

        comments = result.get('comments', [])
        if comments and verbose:
            print(f"\n📋 评论详情:")
            for i, c in enumerate(comments[:10]):
                print(f"  [{i+1}] @{c['author_name']}: \"{c['content_text'][:60]}\" 顶={c['like_count']} 踩={c['reply_count']}")
            if len(comments) > 10:
                print(f"  ... 还有 {len(comments)-10} 条")

        return 0

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断执行")
        return 130

    except Exception as e:
        print(f"\n❌ 爬取失败: {e}")
        logger.exception("详细错误信息:")
        return 1

    finally:
        await simple_browser.close()
        await pw.stop()
        await browser.close_browser()
        if mysql:
            mysql.close()


if __name__ == "__main__":
    args = parse_args()
    url = build_url(args)
    headless = args.headless.lower() == 'true'

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    exit_code = asyncio.run(run_zhibo8_scraper(args.config, url, headless, args.verbose))
    sys.exit(exit_code)
