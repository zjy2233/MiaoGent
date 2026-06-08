import urllib.request, re

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 豆瓣即将上映
urls = [
    ('豆瓣热映', 'https://movie.douban.com/cinema/nowplaying/'),
    ('豆瓣即将上映', 'https://movie.douban.com/cinema/later/'),
]

for name, url in urls:
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode('utf-8', errors='ignore')
        print(f'\n===== {name} =====')
        
        # 提取电影标题
        titles = re.findall(r'alt="(.*?)"', html)
        ratings = re.findall(r'class="rating_num"[^>]*>(.*?)<', html)
        
        seen = set()
        for i, t in enumerate(titles):
            if t and t not in seen and len(t) < 30:
                seen.add(t)
                if i < len(ratings):
                    print(f'  🎬 {t} ⭐ {ratings[i]}')
                else:
                    print(f'  🎬 {t}')
        
        if not titles:
            print('  未解析到电影，打印部分内容：')
            print(html[:800])
    except Exception as e:
        print(f'{name}失败: {e}')

# 试试猫眼
try:
    url = 'https://maoyan.com/films?showType=1'  # 正在热映
    req = urllib.request.Request(url, headers={**headers, 'Cookie': 'uuid=1'})
    resp = urllib.request.urlopen(req, timeout=15)
    html = resp.read().decode('utf-8', errors='ignore')
    print(f'\n===== 猫眼热映 =====')
    titles = re.findall(r'title="(.*?)"', html)
    seen = set()
    for t in titles:
        if t and t not in seen and len(t) < 30 and '部' not in t:
            seen.add(t)
            print(f'  🎬 {t}')
except Exception as e:
    print(f'猫眼失败: {e}')
