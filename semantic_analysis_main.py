#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微博内容语义分析回填入口。

用法:
    python semantic_analysis_main.py
    python semantic_analysis_main.py --keyword 罗永浩 --limit 200
    python semantic_analysis_main.py --article-id 64
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.config_manager import ConfigManager
from skills.semantic_analyzer import SemanticAnalyzer
from utils.logger import setup_logger
from utils.mysql_manager import MySQLManager


def parse_args():
    parser = argparse.ArgumentParser(description="微博文章/评论中文语义分析回填")
    parser.add_argument("--config", "-c", default="config.yaml", help="配置文件路径")
    parser.add_argument("--keyword", "-k", default=None, help="按热点关键词筛选文章")
    parser.add_argument("--article-id", "-a", type=int, default=None, help="只分析指定文章ID")
    parser.add_argument("--limit", "-l", type=int, default=500, help="最多处理文章数")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出详细日志")
    return parser.parse_args()


def load_articles(mysql: MySQLManager, keyword: str = None, article_id: int = None, limit: int = 500) -> List[Dict[str, Any]]:
    if article_id:
        row = mysql.get_article_by_id(article_id)
        return [row] if row else []

    total, rows = mysql.get_articles(keyword=keyword, page=1, page_size=min(limit, 1000))
    return rows[:limit]


def main() -> int:
    args = parse_args()
    config = ConfigManager(args.config).config
    logger = setup_logger("SemanticAnalysis", level="DEBUG" if args.verbose else "INFO")

    mysql_config = config.get("mysql", {})
    if not mysql_config.get("enabled", False):
        print("MySQL 未启用，无法回填语义分析")
        return 1

    mysql = MySQLManager(mysql_config, logger)
    analyzer = SemanticAnalyzer(config, logger)

    try:
        articles = load_articles(mysql, keyword=args.keyword, article_id=args.article_id, limit=args.limit)
        if not articles:
            print("没有找到需要分析的文章")
            return 0

        article_count = 0
        comment_count = 0
        for article in articles:
            article_id = int(article.get("id") or 0)
            if not article_id:
                continue

            article_analysis = analyzer.analyze_record(
                article,
                source_type="article",
                source_id=article_id,
                article_id=article_id,
                keyword=article.get("keyword", ""),
            )
            mysql.save_semantic_analysis(article_analysis)
            article_count += 1

            comments = mysql.get_comments_by_article_id(article_id, limit=1000)
            comment_analyses = [
                analyzer.analyze_record(
                    comment,
                    source_type="comment",
                    source_id=comment.get("id", 0),
                    article_id=article_id,
                    keyword=article.get("keyword", ""),
                )
                for comment in comments
            ]
            if comment_analyses:
                mysql.save_semantic_analysis_batch(comment_analyses)
                comment_count += len(comment_analyses)

        print(f"语义分析完成：文章 {article_count} 篇，评论 {comment_count} 条")
        return 0
    except Exception as exc:
        logger.exception("语义分析失败")
        print(f"语义分析失败: {exc}")
        return 1
    finally:
        mysql.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and "--verbose" in sys.argv:
        logging.basicConfig(level=logging.DEBUG)
    sys.exit(main())
