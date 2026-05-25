from abc import ABC, abstractmethod
from typing import Any, Optional
import logging


class BaseSkill(ABC):
    """技能抽象基类"""
    
    def __init__(self, config: dict = None, logger: logging.Logger = None):
        self.config = config or {}
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        self._initialize()
    
    def _initialize(self):
        """可重写的初始化方法（默认空）"""
        pass
    
    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """执行技能的抽象方法，必须由子类实现"""
        pass
    
    def validate_input(self, *args, **kwargs) -> bool:
        """输入验证（默认返回True）"""
        return True
    
    def on_success(self, result: Any):
        """成功回调"""
        self.logger.info(f"{self.name} 执行成功")
    
    def on_error(self, error: Exception):
        """错误回调"""
        self.logger.error(f"{self.name} 执行出错: {error}")
    
    @property
    def name(self) -> str:
        """自动设置为类名"""
        return self.__class__.__name__
    
    def run(self, *args, **kwargs) -> Any:
        """运行技能的主方法，包含验证、执行和回调逻辑"""
        if not self.validate_input(*args, **kwargs):
            raise ValueError("输入验证失败")
        
        try:
            result = self.execute(*args, **kwargs)
            self.on_success(result)
            return result
        except Exception as e:
            self.on_error(e)
            raise
