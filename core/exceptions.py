class CrawlerBaseException(Exception):
    """爬虫基础异常"""
    pass

class DataFetchError(CrawlerBaseException):
    """数据获取异常"""
    pass

class ParseError(CrawlerBaseException):
    """解析异常"""
    pass

class FilterError(CrawlerBaseException):
    """过滤异常"""
    pass

class ScreenshotError(CrawlerBaseException):
    """截图异常"""
    pass

class BrowserError(CrawlerBaseException):
    """浏览器控制异常"""
    pass

class StorageError(CrawlerBaseException):
    """存储异常"""
    pass

class ConfigError(CrawlerBaseException):
    """配置异常"""
    pass
