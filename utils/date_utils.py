import re
from datetime import datetime, timedelta
from typing import Optional, List


def extract_weibo_title(content: str) -> str:
    """
    从微博内容中提取 #话题# 作为标题

    优先取第一个 #...# 中的内容作为标题；
    如果没有 #话题# 格式，则取内容前30个字符作为兜底。

    Examples:
        >>> extract_weibo_title("#高考加油# 每一份努力都算数")
        '高考加油'
        >>> extract_weibo_title("#原来高考还没结束#【#多地高考3天或4天#】")
        '原来高考还没结束'
        >>> extract_weibo_title("今天天气不错")
        '今天天气不错'
    """
    if not content:
        return ''

    # 匹配所有 #话题# 内容
    hashtags = re.findall(r'#([^#]+)#', content)
    if hashtags:
        return hashtags[0].strip()

    # 兜底：取前30字符
    return content[:30].strip()


def get_current_time_str(format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
    """获取当前时间字符串"""
    return datetime.now().strftime(format_str)

def get_current_date_str() -> str:
    """获取当前日期字符串 YYYYMMDD"""
    return datetime.now().strftime('%Y%m%d')

def parse_weibo_time(time_str: str) -> Optional[datetime]:
    """
    解析微博时间格式为标准 datetime

    支持的格式：
      - "48分钟前" / "3小时前" / "17小时前"
      - "今天09:26" / "今天 11:59"
      - "昨天 14:30"
      - "5-22 14:00" / "5-22   14:00"（当年月-日 时:分）
      - "06月02日 21:18" / "06月02日 21:55"
      - "2024-01-15 10:30"（标准格式）
      - "刚刚"
    """
    if not time_str:
        return None

    time_str = time_str.strip()
    now = datetime.now()

    # "刚刚"
    if time_str == '刚刚':
        return now

    # "XX分钟前"
    m = re.match(r'^(\d+)\s*分钟前$', time_str)
    if m:
        return now - timedelta(minutes=int(m.group(1)))

    # "XX小时前"
    m = re.match(r'^(\d+)\s*小时前$', time_str)
    if m:
        return now - timedelta(hours=int(m.group(1)))

    # "今天HH:MM"
    m = re.match(r'^今天\s*(\d{1,2}):(\d{2})$', time_str)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)

    # "昨天 HH:MM"
    m = re.match(r'^昨天\s*(\d{1,2}):(\d{2})$', time_str)
    if m:
        yesterday = now - timedelta(days=1)
        return yesterday.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)

    # "MM月DD日 HH:MM"（当年）
    m = re.match(r'^(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})$', time_str)
    if m:
        return now.replace(month=int(m.group(1)), day=int(m.group(2)),
                           hour=int(m.group(3)), minute=int(m.group(4)),
                           second=0, microsecond=0)

    # "M-D HH:MM" 或 "M-D   HH:MM"（当年，短格式）
    m = re.match(r'^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$', time_str)
    if m:
        return now.replace(month=int(m.group(1)), day=int(m.group(2)),
                           hour=int(m.group(3)), minute=int(m.group(4)),
                           second=0, microsecond=0)

    # "YYYY-MM-DD HH:MM" / "YYYY-MM-DD HH:MM:SS"
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue

    return None


def normalize_weibo_time(time_str: str, default: str = '') -> str:
    """
    将微博时间字符串转换为标准格式 YYYY-MM-DD HH:MM:SS

    无法解析时返回 default（默认空字符串）

    Examples:
        >>> normalize_weibo_time("48分钟前")
        '2026-06-09 20:42:00'
        >>> normalize_weibo_time("今天09:26")
        '2026-06-09 09:26:00'
        >>> normalize_weibo_time("5-22 14:00")
        '2026-05-22 14:00:00'
        >>> normalize_weibo_time("06月02日 21:18")
        '2026-06-02 21:18:00'
    """
    dt = parse_weibo_time(time_str)
    if dt:
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    return default
