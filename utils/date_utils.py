from datetime import datetime, timedelta
from typing import Optional

def get_current_time_str(format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
    """获取当前时间字符串"""
    return datetime.now().strftime(format_str)

def get_current_date_str() -> str:
    """获取当前日期字符串 YYYYMMDD"""
    return datetime.now().strftime('%Y%m%d')

def parse_weibo_time(time_str: str) -> Optional[datetime]:
    """
    解析微博时间格式
    支持: "10分钟前", "2小时前", "昨天 14:30", "2024-01-15 10:30"
    """
    if not time_str:
        return None
    
    now = datetime.now()
    
    if '分钟前' in time_str:
        minutes = int(time_str.replace('分钟前', '').strip())
        return now - timedelta(minutes=minutes)
    elif '小时前' in time_str:
        hours = int(time_str.replace('小时前', '').strip())
        return now - timedelta(hours=hours)
    elif '昨天' in time_str:
        time_part = time_str.replace('昨天', '').strip()
        yesterday = now - timedelta(days=1)
        if time_part:
            h, m = map(int, time_part.split(':'))
            return yesterday.replace(hour=h, minute=m)
        return yesterday
    else:
        try:
            return datetime.strptime(time_str, '%Y-%m-%d %H:%M')
        except ValueError:
            return None
