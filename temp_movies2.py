import urllib.request, re, sys

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 豆瓣即将上映
try:
    url = 'https://movie.douban.com/cinema/later/'
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=15)
    html = resp.read().decode('utf-8', errors='ignore')
    
    # 提取电影信息
    titles = re.findall(r'<a[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL)
    dates = re.findall(r'<span[^>]*class="[^"]*date[^"]*"[^>]*>(.*?)</span>', html, re.DOTALL)
    types = re.findall(r'<span[^>]*class="[^"]*types[^"]*"[^>]*>(.*?)</span>', html, re.DOTALL)
    
    print('===== 豆瓣即将上映 =====')
    for i, t in enumerate(titles):
        title = re.sub(r'<[^>]+>', '', t).strip()
        date = re.sub(r'<[^>]+>', '', dates[i]).strip() if i < len(dates) else '未知'
        genre = re.sub(r'<[^>]+>', '', types[i]).strip() if i < len(types) else '未知'
        print(f'  [{date}] {title} | {genre}')
    
    if not titles:
        print('未解析到结果')
        # 尝试另一种提取
        items = re.findall(r'alt="(.*?)"', html)
        seen = set()
        for item in items:
            if item and item not in seen and len(item) < 30:
                seen.add(item)
                print(f'  {item}')
except Exception as e:
    print(f'豆瓣即将上映失败: {e}')

# 猫眼热映  
try:
    url = 'https://maoyan.com/films?showType=1'
    req = urllib.request.Request(url, headers={**headers, 'Cookie': 'uuid=1'})
    resp = urllib.request.urlopen(req, timeout=15)
    html = resp.read().decode('utf-8', errors='ignore')
    
    print('\n===== 猫眼热映 =====')
    # 提取电影名和评分
    movie_blocks = re.findall(r'<div[^>]*class="[^"]*movie-item[^"]*"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>.*?title="([^"]*)"', html, re.DOTALL)
    scores = re.findall(r'class="[^"]*score[^"]*"[^>]*>([\d.]+)', html)
    
    for i, (href, title) in enumerate(movie_blocks[:10]):
        score = scores[i] if i < len(scores) else '暂无评分'
        print(f'  {title} | 评分: {score}')
    
    if not movie_blocks:
        # 备用提取
        titles2 = re.findall(r'title="(.*?)"', html)
        seen = set()
        for t in titles2:
            if t and t not in seen and len(t) < 30 and '部' not in t and '猫眼' not in t:
                seen.add(t)
                print(f'  {t}')
except Exception as e:
    print(f'猫眼失败: {e}')
