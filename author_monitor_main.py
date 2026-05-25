#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微博作者监控 - 独立运行入口

从 wb_look_article_info 表读取作者ID，
访问作者微博主页获取最近更新内容，
进行文章截图和评论区截图，数据存入MySQL。

使用方式:
    # 监控所有作者
    python author_monitor_main.py

    # 监控指定作者ID
    python author_monitor_main.py --author-id 7762107285

    # 有头模式
    python author_monitor_main.py --headless false
"""

import asyncio
import argparse
import sys
import logging
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.config_manager import ConfigManager
from skills.browser_controller import BrowserController
from skills.author_monitor import AuthorMonitor
from utils.mysql_manager import MySQLManager
from utils.logger import setup_logger


def parse_args():
    parser = argparse.ArgumentParser(
        description='微博作者监控 - 从数据库读取作者ID并跟踪微博内容',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python author_monitor_main.py                       # 监控所有作者
  python author_monitor_main.py --author-id 7762107285  # 监控指定作者
  python author_monitor_main.py --headless false       # 显示浏览器
        """
    )

    parser.add_argument(
        '--config', '-c',
        type=str,
        default='config.yaml',
        help='配置文件路径 (默认: config.yaml)'
    )

    parser.add_argument(
        '--author-id', '-a',
        type=str,
        default=None,
        help='指定监控的作者ID（不指定则监控数据库中所有作者）'
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


async def run_author_monitor(config_path: str, author_id: str = None, headless: bool = True, verbose: bool = False):
    print("\n" + "=" * 70)
    print("👤 微博作者监控系统")
    print("=" * 70)
    if author_id:
        print(f"🎯 监控作者ID: {author_id}")
    else:
        print("🎯 监控数据库中所有作者")
    print(f"🌐 浏览器模式: {'无头' if headless else '有头'}")
    print(f"⚙️  配置文件: {config_path}")
    print("=" * 70 + "\n")

    config_manager = ConfigManager(config_path)
    config = config_manager.config

    if not headless:
        config['browser']['headless'] = False

    logger = setup_logger("AuthorMonitor", level="DEBUG" if verbose else "INFO")

    browser = BrowserController(config, logger)
    await browser.start_browser()

    mysql_config = config.get('mysql', {})
    mysql = None
    if mysql_config.get('enabled', False):
        mysql = MySQLManager(mysql_config, logger)

    try:
        monitor = AuthorMonitor(config, browser, mysql, logger)

        if author_id:
            result = await monitor.monitor_author(author_id)
        else:
            result = await monitor.monitor_all_authors()

        print(f"\n✅ 作者监控完成！")
        print(f"   结果: {result}")
        return 0

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断执行")
        return 130

    except Exception as e:
        print(f"\n❌ 监控失败: {e}")
        logger.exception("详细错误信息:")
        return 1

    finally:
        await browser.close_browser()
        if mysql:
            mysql.close()


if __name__ == "__main__":
    args = parse_args()
    headless = args.headless.lower() == 'true'

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    exit_code = asyncio.run(run_author_monitor(args.config, args.author_id, headless, args.verbose))
    sys.exit(exit_code)
