import yaml
from pathlib import Path
from typing import Dict, Any, Optional

from .exceptions import ConfigError


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self.load_config()
    
    def load_config(self) -> Dict[str, Any]:
        """加载YAML配置文件"""
        if not self.config_path.exists():
            raise ConfigError(f"配置文件不存在: {self.config_path}")
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f) or {}
        
        return self._config
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项（支持点号分隔的嵌套访问，如 'crawler.timeout'）"""
        keys = key.split('.')
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value
    
    @property
    def config(self) -> Dict[str, Any]:
        return self._config
    
    def reload(self):
        """重新加载配置"""
        self.load_config()
