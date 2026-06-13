#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微博热词全链路分析 - 独立运行入口

对指定热词执行完整分析流程：
  搜索博文 → 文章截图 → 评论采集+截图 → 8维语义分析 → 结果输出

使用方式:
    # 使用配置文件中的默认热词（keyword_analyze.default_keywords）
    python keyword_analyze_main.py

    # 分析指定热词
    python keyword_analyze_main.py --keyword "高考"

    # 分析多个热词（逗号分隔）
    python keyword_analyze_main.py --keyword "高考,中考,考研"

    # 有头模式（显示浏览器窗口）
    python keyword_analyze_main.py --headless false

    # 调整每篇热词处理的文章数
    python keyword_analyze_main.py --keyword "高考" --articles 2
"""

import asyncio
import argparse
import json
import sys
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.pipeline import PipelineManager


def parse_args():
    parser = argparse.ArgumentParser(
        description='微博热词全链路分析 - 搜索/截图/评论/语义分析',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python keyword_analyze_main.py                        # 默认热词（配置文件）
  python keyword_analyze_main.py -k "高考"               # 单个热词
  python keyword_analyze_main.py -k "高考,中考"          # 多个热词
  python keyword_analyze_main.py -k "高考" -a 2 -v       # 2篇文章 + 详细日志
        """
    )

    parser.add_argument(
        '--config', '-c',
        type=str,
        default='config.yaml',
        help='配置文件路径 (默认: config.yaml)'
    )

    parser.add_argument(
        '--keyword', '-k',
        type=str,
        default=None,
        help='分析的热词，多个用逗号分隔（默认读取配置文件 default_keywords）'
    )

    parser.add_argument(
        '--articles', '-a',
        type=int,
        default=None,
        help='每个热词处理的文章数 (默认: 配置文件 default_articles_per_kw)'
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


def print_result_summary(result: dict):
    """打印分析结果摘要"""
    stats = result.get('stats', {})
    status = result.get('status', 'unknown')
    errors = result.get('errors', [])

    print(f"\n{'='*60}")
    print(f"  热词「{result.get('keyword')}」分析结果")
    print(f"{'='*60}")
    print(f"  状态: {status}")
    print(f"  文章: {stats.get('articles_count', 0)} 篇")
    print(f"  评论: {stats.get('comments_count', 0)} 条")
    print(f"  截图: {stats.get('screenshots_count', 0)} 张")

    if errors:
        print(f"\n  错误 ({len(errors)}):")
        for e in errors[:5]:
            print(f"    - {e}")

    # 语义分析摘要
    summary = result.get('semantic_analysis', {}).get('summary', {})
    if summary:
        print(f"\n  --- 语义分析摘要 ---")
        sent_dist = summary.get('article_sentiment_dist', {})
        if sent_dist:
            labels = ', '.join([f'{k}({v})' for k, v in sent_dist.items()])
            print(f"  文章情绪分布: {labels}")
        stance_dist = summary.get('stance_dist', {})
        if stance_dist:
            labels = ', '.join([f'{k}({v})' for k, v in stance_dist.items()])
            print(f"  立场分布: {labels}")
        conflict = summary.get('conflict_stats', {})
        if conflict:
            print(f"  冲突分: 均值={conflict.get('avg_score', 0):.3f}, 最大={conflict.get('max_score', 0):.3f}")
        top_concepts = summary.get('top_concepts', [])
        if top_concepts:
            concepts = ', '.join([f"{c['label']}({c['count']})" for c in top_concepts[:5]])
            print(f"  观念TOP: {concepts}")

    # 文章列表
    articles = result.get('articles', [])
    if articles:
        print(f"\n  --- 文章列表 ---")
        for i, a in enumerate(articles, 1):
            sem = a.get('semantic', {})
            sentiment = sem.get('sentiment_label', '-')
            stance = sem.get('stance_label', '-')
            print(f"  [{i}] @{a.get('author_name','?'):10s} | "
                  f"{a.get('title','')[:40]:42s} | "
                  f"情绪:{sentiment:^4s} 立场:{stance:^4s}")

    print(f"{'='*60}\n")


async def run_keyword_analyze(
    config_path: str,
    keywords: list,
    articles_per_kw: int,
    headless: bool = True,
    verbose: bool = False,
):
    """执行热词全链路分析"""

    print("\n" + "=" * 60)
    print("  微博热词全链路分析系统")
    print("=" * 60)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  热词: {', '.join(keywords)}")
    print(f"  每词文章数: {articles_per_kw}")
    print(f"  浏览器模式: {'无头' if headless else '有头'}")
    print("=" * 60 + "\n")

    all_results = []

    for idx, keyword in enumerate(keywords, 1):
        print(f"\n{'─'*50}")
        print(f"  [{idx}/{len(keywords)}] 正在分析热词: 「{keyword}」")
        print(f"{'─'*50}\n")

        try:
            pipeline = PipelineManager(config_path)

            if not headless:
                pipeline.config['browser']['headless'] = False

            result = await pipeline.analyze_keyword_return(
                keyword=keyword,
                articles_per_kw=articles_per_kw,
            )
            all_results.append(result)

            # 打印单次结果摘要
            print_result_summary(result)

        except KeyboardInterrupt:
            print(f"\n⚠️ 用户中断，跳过热词「{keyword}」")
            break

        except Exception as e:
            print(f"\n❌ 热词「{keyword}」分析失败: {e}")
            logging.exception("详细错误:")
            all_results.append({
                'keyword': keyword,
                'status': 'failed',
                'stats': {},
                'errors': [str(e)],
            })

    # 全局汇总
    print(f"\n{'='*60}")
    print(f"  全部任务完成！共处理 {len(all_results)} 个热词")
    print(f"{'='*60}")
    total_articles = sum(r.get('stats', {}).get('articles_count', 0) for r in all_results)
    total_comments = sum(r.get('stats', {}).get('comments_count', 0) for r in all_results)
    total_screenshots = sum(r.get('stats', {}).get('screenshots_count', 0) for r in all_results)
    success_count = sum(1 for r in all_results if r.get('status') == 'completed')
    print(f"  成功: {success_count}/{len(all_results)}")
    print(f"  总计: {total_articles} 篇文章, {total_comments} 条评论, {total_screenshots} 张截图")
    print(f"{'='*60}\n")

    return 0


def main():
    args = parse_args()

    headless = args.headless.lower() == 'true'

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # 确定热词列表：命令行优先，否则从配置文件读取
    if args.keyword:
        keywords = [k.strip() for k in args.keyword.split(',') if k.strip()]
    else:
        from core.config_manager import ConfigManager
        cfg = ConfigManager(args.config).config
        keywords = cfg.get('keyword_analyze', {}).get('default_keywords', ['高考'])

    if not keywords:
        print("错误: 未指定分析热词，请通过 --keyword 参数或配置文件设置")
        sys.exit(1)

    # 确定每词文章数
    articles_per_kw = args.articles
    if articles_per_kw is None:
        from core.config_manager import ConfigManager
        cfg = ConfigManager(args.config).config
        articles_per_kw = cfg.get('keyword_analyze', {}).get('default_articles_per_kw', 3)

    try:
        exit_code = asyncio.run(run_keyword_analyze(
            args.config, keywords, articles_per_kw, headless, args.verbose
        ))
        sys.exit(exit_code)

    except KeyboardInterrupt:
        print("\n\n👋 用户中断执行")
        sys.exit(130)


if __name__ == "__main__":
    main()
