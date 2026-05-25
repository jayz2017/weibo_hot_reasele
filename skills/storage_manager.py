import json
import csv
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime
from core.base import BaseSkill
from core.exceptions import StorageError
from utils.file_utils import ensure_dir, save_json, load_json, clean_filename, generate_filename
from utils.date_utils import get_current_time_str

class StorageManager(BaseSkill):
    """
    数据存储管理器 Skill 7
    
    功能：
    1. JSON格式化存储各类数据
    2. 按日期/关键词分层存储
    3. 数据去重检查
    4. CSV/Excel导出功能
    5. 存储状态记录（支持断点续传）
    6. 自动清理过期数据
    """
    
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        storage_config = config.get('storage', {})
        base_dir = Path(storage_config.get('base_dir', './data'))

        paths_config = config.get('paths', {})
        screenshot_base = Path(paths_config.get('screenshot_dir', str(base_dir / 'screenshots')))

        self.raw_dir = base_dir / storage_config.get('raw_data_dir', 'raw')
        self.processed_dir = base_dir / storage_config.get('processed_data_dir', 'processed')
        self.screenshots_dir = screenshot_base

        ensure_dir(str(self.raw_dir))
        ensure_dir(str(self.processed_dir))
        ensure_dir(str(self.screenshots_dir / 'articles'))
        ensure_dir(str(self.screenshots_dir / 'comments'))
        
        self.export_format = storage_config.get('export_format', 'json')
        self.auto_cleanup_days = storage_config.get('auto_cleanup_days', 30)
        
        # 已存储数据ID集合（用于去重）
        self.stored_ids: set = set()
        super().__init__(config, logger)
        self._load_stored_state()
    
    def _initialize(self):
        self.logger.info(f"[{self.name}] 存储管理器初始化完成")
        self.logger.info(f"[{self.name}] 原始数据目录: {self.raw_dir}")
        self.logger.info(f"[{self.name}] 处理后目录: {self.processed_dir}")
    
    def execute(self, data: Any, data_type: str = "general") -> Any:
        """
        执行数据存储操作
        
        Args:
            data: 要存储的数据
            data_type: 数据类型 ('hotsearch', 'article', 'comment')
            
        Returns:
            Any: 存储结果（文件路径或状态）
        """
        self.logger.info(f"[{self.name}] 开始存储数据，类型: {data_type}")
        
        try:
            if data_type == "hotsearch":
                result = self.save_hot_search_data(data)
            elif data_type == "article":
                result = self.save_article_data(data)
            elif data_type == "comment":
                result = self.save_comments_data(data)
            else:
                result = f"未知数据类型: {data_type}"
            
            self.on_success(result)
            return result
            
        except Exception as e:
            self.logger.error(f"[{self.name}] 存储失败: {e}")
            raise
    
    def _load_stored_state(self):
        """加载已存储状态"""
        state_file = self.raw_dir / '.stored_state.json'
        if state_file.exists():
            try:
                data = load_json(str(state_file))
                self.stored_ids = set(data.get('ids', []))
            except Exception as e:
                self.logger.warning(f"[{self.name}] 加载存储状态失败: {e}")
    
    def _save_stored_state(self):
        """保存存储状态"""
        state_file = self.raw_dir / '.stored_state.json'
        save_json({'ids': list(self.stored_ids), 'updated': get_current_time_str()}, str(state_file))
    
    def save_hot_search_data(self, hot_list: List[Any], date_str: Optional[str] = None) -> str:
        """
        保存热榜数据
        
        Args:
            hot_list: 热榜数据列表
            date_str: 日期字符串（默认今天）
            
        Returns:
            str: 保存的文件路径
        """
        from utils.date_utils import get_current_date_str
        date_str = date_str or get_current_date_str()
        date_dir = self.raw_dir / date_str
        ensure_dir(str(date_dir))
        
        filename = generate_filename('hotsearch')
        filepath = date_dir / filename
        
        data = [item.to_dict() if hasattr(item, 'to_dict') else item for item in hot_list]
        save_json(data, str(filepath))
        
        self.logger.info(f"[{self.name}] 热榜数据已保存: {filepath}")
        return str(filepath)
    
    def save_article_data(self, article: Any, keyword: str = "") -> str:
        """
        保存单篇文章数据
        
        按关键词建立子目录存储
        """
        from utils.date_utils import get_current_date_str
        safe_keyword = clean_filename(keyword or 'unknown')[:50]
        keyword_dir = self.processed_dir / safe_keyword
        ensure_dir(str(keyword_dir))
        
        filename = f"{clean_filename(get_current_date_str())}_article.json"
        filepath = keyword_dir / filename
        
        data = article.to_dict() if hasattr(article, 'to_dict') else article
        save_json(data, str(filepath))
        
        # 记录到已存储集合
        if hasattr(article, 'url') and article.url:
            self.stored_ids.add(f"article_{hash(article.url)}")
        
        self.logger.debug(f"[{self.name}] 文章数据已保存: {filepath}")
        return str(filepath)
    
    def save_comments_data(self, comments: List[Any], article_url: str = "") -> str:
        """
        保存评论数据列表
        """
        safe_url_hash = str(hash(article_url))[:8] if article_url else 'unknown'
        filename = generate_filename(f'comments_{safe_url_hash}')
        filepath = self.processed_dir / filename
        
        data = [c.to_dict() if hasattr(c, 'to_dict') else c for c in comments]
        save_json(data, str(filepath))
        
        self.logger.debug(f"[{self.name}] 评论数据已保存 ({len(comments)}条): {filepath}")
        return str(filepath)
    
    def check_duplicate(self, data_id: str, data_type: str = "") -> bool:
        """检查数据是否已存在"""
        full_id = f"{data_type}_{data_id}" if data_type else data_id
        return full_id in self.stored_ids
    
    def export_to_csv(self, data_list: List[Dict], output_filename: str, 
                     fieldnames: Optional[List[str]] = None) -> str:
        """
        导出数据为CSV文件
        
        Args:
            data_list: 数据字典列表
            output_filename: 输出文件名
            fieldnames: 字段名列表（自动推断）
            
        Returns:
            str: CSV文件路径
        """
        export_dir = self.processed_dir / 'exports'
        ensure_dir(str(export_dir))
        
        filepath = export_dir / f"{output_filename}.csv"
        
        if not fieldnames and data_list:
            fieldnames = list(data_list[0].keys())
        
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames or [])
            writer.writeheader()
            writer.writerows(data_list)
        
        self.logger.info(f"[{self.name}] CSV导出完成: {filepath} ({len(data_list)}条)")
        return str(filepath)
    
    def cleanup_old_data(self, days: int = 0) -> int:
        """
        清理过期数据
        
        Args:
            days: 天数（使用配置值或传入值）
            
        Returns:
            int: 清理的文件数量
        """
        days = days or self.auto_cleanup_days
        cutoff_date = datetime.now().timestamp() - (days * 24 * 60 * 60)
        removed_count = 0
        
        try:
            for directory in [self.raw_dir, self.processed_dir]:
                if not directory.exists():
                    continue
                    
                for file_path in directory.rglob('*.json'):
                    if file_path.stat().st_mtime < cutoff_date:
                        file_path.unlink()
                        removed_count += 1
            
            self.logger.info(f"[{self.name}] 清理完成，删除 {removed_count} 个过期文件")
            
        except Exception as e:
            self.logger.error(f"[{self.name}] 清理失败: {e}")
        
        return removed_count
    
    def generate_report(self, stats: Dict[str, int]) -> str:
        """
        生成采集报告
        
        Args:
            stats: 统计信息字典 {'articles': 10, 'comments': 100, ...}
            
        Returns:
            str: 报告文件路径
        """
        report_data = {
            'report_time': get_current_time_str(),
            'statistics': stats,
            'storage_location': str(self.processed_dir),
            'screenshot_location': str(self.screenshots_dir)
        }
        
        report_file = self.raw_dir / generate_filename('report')
        save_json(report_data, str(report_file))
        
        self.logger.info(f"[{self.name}] 采集报告已生成: {report_file}")
        return str(report_file)
    
    def close(self):
        """关闭时保存状态"""
        self._save_stored_state()
