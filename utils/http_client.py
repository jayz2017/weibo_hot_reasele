import requests
import random
import time
from typing import Dict, Any, Optional, List

class HTTPClient:
    """HTTP客户端封装"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.timeout = config.get('timeout', 30)
        self.retry_times = config.get('retry_times', 3)
        self.user_agents = config.get('user_agents', [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        ])
        self.session = requests.Session()
    
    def _get_random_headers(self) -> Dict[str, str]:
        return {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'application/json, text/html, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://weibo.com/'
        }
    
    def get(self, url: str, params: Optional[Dict] = None, **kwargs) -> requests.Response:
        """GET请求（带重试机制）"""
        for attempt in range(self.retry_times):
            try:
                response = self.session.get(
                    url,
                    headers=self._get_random_headers(),
                    params=params,
                    timeout=self.timeout,
                    **kwargs
                )
                response.raise_for_status()
                
                interval = self.config.get('request_interval', 2)
                time.sleep(interval)
                
                return response
                
            except requests.RequestException as e:
                if attempt == self.retry_times - 1:
                    raise e
                time.sleep(2 ** attempt)
    
    def post(self, url: str, data: Optional[Dict] = None, json_data: Optional[Dict] = None, **kwargs) -> requests.Response:
        """POST请求"""
        for attempt in range(self.retry_times):
            try:
                response = self.session.post(
                    url,
                    headers=self._get_random_headers(),
                    data=data,
                    json=json_data,
                    timeout=self.timeout,
                    **kwargs
                )
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                if attempt == self.retry_times - 1:
                    raise e
                time.sleep(2 ** attempt)
    
    def close(self):
        """关闭会话"""
        self.session.close()
