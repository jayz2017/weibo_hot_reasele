#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微博热榜爬虫系统 - 主程序入口

功能：
- 采集微博热榜数据
- 智能过滤目标话题
- 浏览器渲染截图（文章+评论）
- 结构化数据存储

使用方式：
    # 完整流水线运行
    python main.py
    
    # 单关键词测试
    python main.py --keyword "某个热搜词"
    
    # 只获取热榜数据（不截图）
    python main.py --mode hotsearch
    
作者: weibo_hot_reasele
版本: 1.0.0
"""

import asyncio
import argparse
import sys
import logging
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.pipeline import PipelineManager


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='微博热榜爬虫系统 - 自动采集与可视化截图',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 运行完整流水线
  python main.py --keyword "iPhone" # 只处理指定关键词
  python main.py --mode hotsearch   # 只采集热榜数据
  python main.py --headless false   # 显示浏览器窗口
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
        help='单独测试某个热搜关键词'
    )
    
    parser.add_argument(
        '--mode', '-m',
        type=str,
        choices=['full', 'hotsearch', 'screenshot'],
        default='full',
        help='运行模式: full(完整), hotsearch(仅热榜), screenshot(仅截图) (默认: full)'
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


async def run_full_pipeline(config_path: str, headless: bool = True):
    """运行完整的数据采集流水线"""
    print("\n" + "="*70)
    print("🚀 微博热榜爬虫系统 v1.0.0")
    print("="*70)
    print(f"📅 启动时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⚙️  配置文件: {config_path}")
    print(f"🌐 浏览器模式: {'无头' if headless else '有头'}")
    print("="*70 + "\n")
    
    try:
        pipeline = PipelineManager(config_path)
        
        if not headless:
            pipeline.config['browser']['headless'] = False
        
        await pipeline.run_pipeline()
        
        print("\n✅ 任务执行完成！")
        return 0
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断执行")
        return 130
        
    except Exception as e:
        print(f"\n❌ 执行失败: {e}")
        logging.exception("详细错误信息:")
        return 1


async def run_single_keyword(config_path: str, keyword: str, headless: bool = True):
    """只处理单个关键词（用于调试）"""
    print(f"\n🔍 单关键词测试模式: {keyword}")
    print("-" * 50)
    
    try:
        pipeline = PipelineManager(config_path)
        
        if not headless:
            pipeline.config['browser']['headless'] = False
        
        await pipeline.run_single_keyword(keyword)
        
        print(f"\n✅ 关键词 [{keyword}] 处理完成！")
        return 0
        
    except Exception as e:
        print(f"\n❌ 处理失败: {e}")
        return 1


async def run_hotsearch_only(config_path: str):
    """只采集热榜数据，不进行后续处理"""
    from skills.hotsearch_crawler import HotSearchCrawler
    from skills.data_filter import DataFilter
    from utils.logger import setup_logger
    
    logger = setup_logger("HotSearchOnly", level="INFO")
    
    print("\n📊 热榜数据采集模式")
    print("-" * 50)
    
    try:
        config_manager = __import__('core.config_manager', fromlist=['ConfigManager']).ConfigManager(config_path)
        config = config_manager.config
        
        crawler = HotSearchCrawler(config, logger)
        data_filter = DataFilter(config, logger)
        
        raw_data = crawler.execute()
        filtered_data = data_filter.execute(raw_data)
        
        print(f"\n📈 原始热榜数据: {len(raw_data)} 条")
        print(f"🎯 过滤后数据: {len(filtered_data)} 条")
        
        print("\n📋 热搜榜单 TOP 20:")
        print("-" * 50)
        for i, item in enumerate(filtered_data[:20], 1):
            print(f"{i:2d}. [{item.icon_desc:^4}] {item.word:<30} 🔥{item.num:>10,}")
        
        crawler.close()
        return 0
        
    except Exception as e:
        print(f"\n❌ 采集失败: {e}")
        return 1


def main():
    """主函数入口"""
    args = parse_args()
    
    headless = args.headless.lower() == 'true'
    
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    
    try:
        if args.keyword:
            asyncio.run(run_single_keyword(args.config, args.keyword, headless))
            
        elif args.mode == 'hotsearch':
            asyncio.run(run_hotsearch_only(args.config))
            
        elif args.mode == 'full':
            exit_code = asyncio.run(run_full_pipeline(args.config, headless))
            sys.exit(exit_code)
            
        else:
            print("未知运行模式")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n👋 再见！")
        sys.exit(130)


if __name__ == "__main__":
    main()
