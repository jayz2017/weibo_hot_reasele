import asyncio
import re
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('Zhibo8Diag')

TARGET_URL = "https://www.zhibo8.com/nba/2026/0525-match1984335v-jijin.htm"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        )
        page = await context.new_page()

        await page.set_extra_http_headers({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': 'https://news.zhibo8.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        })

        zhibo8_cookies = [
            {'name': 'ZBB_WEBUID', 'value': '1777878716903pogggk0ea4', 'domain': '.zhibo8.com', 'path': '/'},
            {'name': 'isFirstAll', 'value': '1', 'domain': '.zhibo8.com', 'path': '/'},
            {'name': 'Hm_lvt_3212511d67978fc36e99a8ba103a1cc8', 'value': '1777878828,1777958041,1778650644', 'domain': '.zhibo8.com', 'path': '/'},
            {'name': 'sel_num', 'value': '0', 'domain': '.zhibo8.com', 'path': '/'},
            {'name': 'stype', 'value': 'basketball', 'domain': '.zhibo8.com', 'path': '/'},
        ]
        await context.add_cookies(zhibo8_cookies)

        logger.info(f"正在访问: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until='domcontentloaded', timeout=60000)
        await asyncio.sleep(5)

        logger.info("\n" + "="*60)
        logger.info("[1] 检查#pllist中的实际评论内容")
        logger.info("="*60)

        pllist_info = await page.evaluate("""
            () => {
                const pllist = document.getElementById('pllist');
                if (!pllist) return { found: false };

                const results = { found: true, childCount: pllist.children.length };
                const allChildren = pllist.children;
                const commentsFound = [];
                const hotDivs = [];

                for (let i = 0; i < allChildren.length; i++) {
                    const el = allChildren[i];
                    const text = (el.innerText || '').trim();
                    const hasDingCai = /顶\\s*\\(\\d+\\)|up_cnt|down_cnt/.test(text) || /顶\\s*\\(\\d+\\)/.test(el.innerHTML);

                    if (hasDingCai || text.length > 50) {
                        const rect = el.getBoundingClientRect();
                        commentsFound.push({
                            index: i,
                            tag: el.tagName,
                            cls: el.className.toString().substring(0, 80),
                            textLen: text.length,
                            textPreview: text.substring(0, 120).replace(/\\n/g, ' | '),
                            htmlLen: el.innerHTML.length,
                            rect: { x: Math.round(rect.x), y: Math.round(rect.y), h: Math.round(rect.height) },
                            hasDingCai: hasDingCai,
                            innerHTML_snippet: el.innerHTML.substring(0, 300)
                        });
                    }

                    if (el.id === 'hotdiv' || el.className.includes('hot')) {
                        hotDivs.push({
                            id: el.id,
                            cls: el.className,
                            childCount: el.children.length,
                            display: window.getComputedStyle(el).display,
                            innerHTML_len: el.innerHTML.length,
                            innerHTML_preview: el.innerHTML.substring(0, 500)
                        });
                    }
                }

                results.comments = commentsFound;
                results.hotDivs = hotDivs;

                const hotpp = document.getElementById('hotpp');
                if (hotpp) {
                    results.hotpp = {
                        exists: true,
                        childCount: hotpp.children.length,
                        htmlLen: hotpp.innerHTML.length,
                        html: hotpp.innerHTML.substring(0, 500)
                    };
                }

                return results;
            }
        """)

        logger.info(f"#pllist 子元素总数: {pllist_info.get('childCount')}")
        logger.info(f"包含'顶/踩'或长文本的元素: {len(pllist_info.get('comments', []))} 个")

        for c in pllist_info.get('comments', [])[:15]:
            logger.info(f"  [{c['index']}] <{c['tag']}> class={c['cls']}")
            logger.info(f"      textLen={c['textLen']} htmlLen={c['htmlLen']} hasDingCai={c['hasDingCai']}")
            logger.info(f"      preview: {c['textPreview'][:100]}")
            logger.info(f"      rect: {c['rect']}")
            if c.get('innerHTML_snippet'):
                logger.info(f"      html: {c['innerHTML_snippet'][:200]}")

        for hd in pllist_info.get('hotDivs', []):
            logger.info(f"  热门DIV: id={hd['id']} cls={hd['cls']} display={hd['display']} children={hd['childCount']} htmlLen={hd['htmlLen']}")
            if hd.get('html_preview'):
                logger.info(f"    html: {hd['html_preview'][:300]}")

        if pllist_info.get('hotpp'):
            hp = pllist_info['hotpp']
            logger.info(f"  #hotpp: exists={hp['exists']} children={hp['childCount']} htmlLen={hp['htmlLen']}")
            logger.info(f"    html: {hp['html']}")

        logger.info("\n" + "="*60)
        logger.info("[2] 尝试调用评论API (使用aiohttp)")
        logger.info("="*60)

        file_attr = "2026_05_25-news-nba-match1984335date2026vnative"
        pl_path = file_attr.replace('-', '/')
        logger.info(f"  pl_path: {pl_path}")

        import aiohttp
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
            'Accept': '*/*',
            'Referer': TARGET_URL,
            'Cookie': 'ZBB_WEBUID=1777878716903pogggk0ea4; isFirstAll=1; stype=basketball',
        }

        api_urls = [
            f"https://pl.zhibo8.com/{pl_path}",
            f"http://pl.zhibo8.cc/{pl_path}",
            f"https://pl.zhibo8.com/{pl_path}?callback=jsonp123",
            f"http://pl.zhibo8.cc/{pl_path}?callback=jsonp123",
        ]

        for api_url in api_urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        body = await resp.text()
                        logger.info(f"\n  [{resp.status}] {api_url[:80]}")
                        logger.info(f"    len={len(body)} content_type={resp.headers.get('Content-Type','?')}")
                        if len(body) > 10:
                            logger.info(f"    body: {body[:600]}")
                            if any(kw in body for kw in ['顶(', 'up_cnt', '"content"', '"username"', '"name"']):
                                logger.info(f"    *** 评论数据!!! ***")
            except Exception as e:
                logger.info(f"  ERR {api_url[:60]}: {e}")

        await browser.close()
        logger.info("\n诊断完成")

if __name__ == '__main__':
    asyncio.run(main())
