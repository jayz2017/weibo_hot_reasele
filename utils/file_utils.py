import os
import json
from pathlib import Path
from typing import Any, List
from datetime import datetime

def ensure_dir(path: str) -> Path:
    """确保目录存在"""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p

def save_json(data: Any, file_path: str, indent: int = 2):
    """保存JSON文件"""
    ensure_dir(os.path.dirname(file_path))
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)

def load_json(file_path: str) -> Any:
    """加载JSON文件"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def generate_filename(prefix: str, suffix: str = "", ext: str = ".json") -> str:
    """生成带时间戳的文件名"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    name = f"{timestamp}_{prefix}"
    if suffix:
        name += f"_{suffix}"
    return f"{name}{ext}"

def clean_filename(name: str, max_length: int = 100) -> str:
    """清理文件名中的非法字符"""
    illegal_chars = '<>:"/\\|?*'
    for char in illegal_chars:
        name = name.replace(char, '_')
    if len(name) > max_length:
        name = name[:max_length]
    return name.strip()
