import urllib.request, urllib.parse, re

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 猫眼 - 即将上映
try:
    url = 'https://maoyan.com/films?showType=2'
    req = urllib.request.Request(url, headers={**headers, 'Cookie': 'uuid=1'})
    resp = urllib.request.urlopen(req, timeout=15)
    html = resp.read().decode('utf-8', errors='ignore')
    
    print('===== 猫眼即将上映 =====')
    titles = re.findall(r'title="(.*?)"', html)
    seen = set()
    for t in titles:
        if t and t not in seen and len(t) < 30 and '部' not in t and '猫眼' not in t:
            seen.add(t)
            print(f'  {t}')
except Exception as e:
    print(f'猫眼即将上映失败: {e}')

# Bing搜电影推荐
try:
    query = urllib.parse.quote('2025年6月最值得看的电影推荐')
    url = f'https://www.bing.com/search?q={query}'
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=15)
    html = resp.read().decode('utf-8', errors='ignore')
    
    print('\n===== Bing电影推荐 =====')
    items = re.findall(r'<h2[^>]*>.*?<a[^>]*href="(.*?)"[^>]*>(.*?)</a>', html, re.DOTALL)
    for href, title in items:
        clean = re.sub(r'<[^>]+>', '', title).strip()
        bad = ['iciba', 'dictionary', 'dict', 'baike', 'collins', 'youdao', 'bing']
        if not any(d in href.lower() for d in bad) and len(clean) > 10:
            print(f'  {clean}')
            print(f'    {href}')
except Exception as e:
    print(f'Bing搜索失败: {e}')

# 豆瓣250
try:
    url = 'https://movie.douban.com/top250'
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=15)
    html = resp.read().decode('utf-8', errors='ignore')
    
    print('\n===== 豆瓣TOP250（前25） =====')
    titles = re.findall(r'<span[^>]*class="title"[^>]*>(.*?)</span>', html)
    for t in titles[:25]:
        clean = t.strip()
        if not clean.startswith('/'):
            print(f'  {clean}')
except Exception as e:
    print(f'豆瓣TOP250失败: {e}')
