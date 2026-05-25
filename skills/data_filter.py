import re
import logging
from typing import List, Dict, Any
from core.base import BaseSkill
from core.exceptions import FilterError
from models.hotsearch_model import HotSearchModel

class DataFilter(BaseSkill):
    """
    智能数据过滤器 Skill 2
    
    功能：
    1. 按icon_desc过滤（新、热、新热）
    2. 关键词分类过滤（保留科技、娱乐、花边、体育）
    3. 黑名单机制（排除政府、政治等敏感词）
    4. 排名范围和热度阈值过滤
    """
    
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        filter_config = config.get('filter', {})
        self.allowed_icons = filter_config.get('allowed_icons', ['新', '热', '新热'])
        self.allowed_categories = filter_config.get('allowed_categories', ['科技', '娱乐', '花边', '体育'])
        self.blacklist_keywords = filter_config.get('blacklist_keywords', ['政府', '政治', '市政', '时政'])
        self.min_hot_num = filter_config.get('min_hot_num', 10000)
        self.top_n = filter_config.get('top_n', 20)
        super().__init__(config, logger)
    
    def _initialize(self):
        self.logger.info(f"[{self.name}] 过滤器初始化完成")
        self.logger.info(f"[{self.name}] 允许的图标类型: {self.allowed_icons}")
        self.logger.info(f"[{self.name}] 允许的分类: {self.allowed_categories}")
        self.logger.info(f"[{self.name}] 黑名单关键词: {self.blacklist_keywords}")
    
    def execute(self, items: List[HotSearchModel]) -> List[HotSearchModel]:
        """
        执行所有过滤规则
        
        Args:
            items: 原始热榜数据列表
            
        Returns:
            List[HotSearchModel]: 过滤后的数据列表
        """
        self.logger.info(f"[{self.name}] 开始过滤，原始数据量: {len(items)}")
        
        filtered_items = items.copy()
        
        filtered_items = self.filter_by_icon_type(filtered_items)
        self.logger.debug(f"[{self.name}] 图标过滤后剩余: {len(filtered_items)}")
        
        filtered_items = self.apply_blacklist(filtered_items)
        self.logger.debug(f"[{self.name}] 黑名单过滤后剩余: {len(filtered_items)}")
        
        filtered_items = self.filter_by_hot_num(filtered_items)
        self.logger.debug(f"[{self.name}] 热度过滤后剩余: {len(filtered_items)}")
        
        filtered_items = self.filter_top_n(filtered_items)
        self.logger.debug(f"[{self.name}] TOP N过滤后剩余: {len(filtered_items)}")
        
        self.logger.info(f"[{self.name}] 过滤完成，最终结果: {len(filtered_items)} 条")
        self.on_success(filtered_items)
        return filtered_items
    
    def filter_by_icon_type(self, items: List[HotSearchModel]) -> List[HotSearchModel]:
        """按图标类型过滤"""
        return [item for item in items if item.icon_desc in self.allowed_icons]
    
    def apply_blacklist(self, items: List[HotSearchModel]) -> List[HotSearchModel]:
        """应用黑名单关键词过滤"""
        filtered = []
        for item in items:
            is_blocked = False
            for keyword in self.blacklist_keywords:
                if keyword in item.word or keyword in item.note:
                    is_blocked = True
                    self.logger.debug(f"[{self.name}] 过滤掉敏感词: {item.word} (包含: {keyword})")
                    break
            if not is_blocked:
                filtered.append(item)
        return filtered
    
    def filter_by_hot_num(self, items: List[HotSearchModel]) -> List[HotSearchModel]:
        """按最低热度阈值过滤"""
        return [item for item in items if item.num >= self.min_hot_num]
    
    def filter_top_n(self, items: List[HotSearchModel]) -> List[HotSearchModel]:
        """取排名前N的数据"""
        sorted_items = sorted(items, key=lambda x: x.realpos if x.realpos > 0 else x.rank)
        return sorted_items[:self.top_n]
