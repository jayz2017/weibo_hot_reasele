import json
import logging
from typing import List, Dict, Any
from core.base import BaseSkill
from core.exceptions import DataFetchError, ParseError
from models.hotsearch_model import HotSearchModel
from utils.http_client import HTTPClient

class HotSearchCrawler(BaseSkill):
    """
    热榜数据采集器 Skill 1
    
    功能：
    1. 请求微博热榜API获取实时数据
    2. 解析JSON响应并转换为HotSearchModel对象列表
    3. 数据验证和异常处理
    """
    
    HOTSEARCH_API = "https://weibo.com/ajax/side/hotSearch"
    
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        crawler_config = config.get('crawler', {})
        self.http_client = HTTPClient(crawler_config)
        super().__init__(config, logger)
    
    def _initialize(self):
        """初始化HTTP客户端"""
        self.logger.info(f"[{self.name}] 热榜采集器初始化完成，目标API: {self.HOTSEARCH_API}")
    
    def execute(self) -> List[HotSearchModel]:
        """
        执行热榜数据采集
        
        Returns:
            List[HotSearchModel]: 热榜数据模型列表
        """
        self.logger.info(f"[{self.name}] 开始采集热榜数据...")
        
        try:
            response = self.http_client.get(self.HOTSEARCH_API)
            json_data = response.json()
            
            self.logger.debug(f"[{self.name}] API返回原始数据结构: {type(json_data)}")
            if isinstance(json_data, dict):
                self.logger.info(f"[{self.name}] API返回字段: {list(json_data.keys())}")
                if 'data' in json_data:
                    self.logger.info(f"[{self.name}] data字段类型: {type(json_data['data'])}")
                    if isinstance(json_data['data'], list):
                        # data是直接列表
                        if len(json_data['data']) > 0:
                            self.logger.info(f"[{self.name}] 第一条数据示例: {json_data['data'][0]}")
                    elif isinstance(json_data['data'], dict):
                        # data是字典，需要找到包含列表的字段
                        self.logger.info(f"[{self.name}] data字典字段: {list(json_data['data'].keys())}")
                        for sub_key in ['realtime', 'hotgov', 'list', 'items', 'hot']:
                            if sub_key in json_data['data'] and isinstance(json_data['data'][sub_key], list):
                                json_data = {'data': json_data['data'][sub_key]}
                                self.logger.info(f"[{self.name}] 找到热榜数据在: {sub_key}, 共 {len(json_data['data'])} 条")
                                break
                else:
                    self.logger.warning(f"[{self.name}] 未找到'data'字段，尝试直接解析...")
                    # 尝试其他可能的字段名
                    for key in ['result', 'list', 'hotsearch', 'items']:
                        if key in json_data and isinstance(json_data[key], list):
                            self.logger.info(f"[{self.name}] 找到替代字段: {key}, 包含 {len(json_data[key])} 条数据")
                            json_data = {'data': json_data[key]}
                            break
                    else:
                        # 如果整个响应就是列表
                        if isinstance(json_data, list):
                            json_data = {'data': json_data}
                        else:
                            self.logger.error(f"[{this.name}] 无法识别的数据格式: {str(json_data)[:200]}")
            
            hot_list = self.parse_response(json_data)
            
            self.logger.info(f"[{self.name}] 成功采集 {len(hot_list)} 条热榜数据")
            self.on_success(hot_list)
            return hot_list
            
        except DataFetchError as e:
            self.logger.error(f"[{self.name}] 数据获取失败: {e}")
            raise
        except Exception as e:
            self.logger.error(f"[{self.name}] 未知错误: {e}")
            raise DataFetchError(f"热榜数据采集失败: {e}")
    
    def parse_response(self, json_data: dict) -> List[HotSearchModel]:
        """
        解析API响应JSON数据
        
        Args:
            json_data: API返回的JSON字典
            
        Returns:
            List[HotSearchModel]: 标准化的热榜数据列表
        """
        if 'data' not in json_data or not isinstance(json_data.get('data'), list):
            raise ParseError("API返回数据格式异常")
        
        hot_list = []
        for item in json_data['data']:
            if self.validate_data(item):
                model = HotSearchModel.from_dict(item)
                hot_list.append(model)
        
        return hot_list
    
    def validate_data(self, item: dict) -> bool:
        """
        验证单条数据的完整性
        
        Args:
            item: 单条热榜数据字典
            
        Returns:
            bool: 数据是否有效
        """
        required_fields = ['word', 'num', 'rank']
        return all(field in item for field in required_fields)
    
    def close(self):
        """关闭HTTP客户端"""
        self.http_client.close()
