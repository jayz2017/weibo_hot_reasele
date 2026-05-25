import logging
import sys
from pathlib import Path
from typing import Optional

def setup_logger(name: str = "weibo_crawler", 
                 level: str = "INFO",
                 log_file: Optional[str] = None,
                 format_str: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s") -> logging.Logger:
    """
    配置并返回日志记录器
    
    Args:
        name: 日志记录器名称
        level: 日志级别 DEBUG/INFO/WARNING/ERROR
        log_file: 日志文件路径（可选）
        format_str: 日志格式字符串
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    if not logger.handlers:
        formatter = logging.Formatter(format_str)
        
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
    
    return logger
