from __future__ import annotations
import json, re, time, html as htmlmod
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, quote, urlparse
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data' / 'jobs.json'
KST = timezone(timedelta(hours=9))
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36'}

SEARCHES = [
    ('잡코리아', 'https://www.jobkorea.co.kr/Search/?stext={q}', [r'/Recruit/GI_Read/\d+']),
    ('사람인', 'https://www.saramin.co.kr/zf_user/search?searchType=search&searchword={q}', [r'/zf_user/jobs/view\?rec_idx=\d+', r'/job-search/view\?rec_idx=\d+']),
    ('인크루트', 'https://search.incruit.com/list/search.asp?col=job&kw={q}', [r'/jobdb_info/jobpost\.asp\?job=\d+', r'/company/jobpost\.asp\?job=\d+']),
    ('알바천국', 'https://www.alba.co.kr/search/Search?wsSrchWord={q}', [r'/job/Detail\?adid=\d+']),
    ('알바몬', 'https://www.albamon.com/search?keyword={q}', [r'/jobs/detail/\d+', r'/recruit/view\?AL_GI_No=\d+']),
]
KEYWORDS = ['보안', '경비', '특수경비', '공항보안', '보안검색', 'CCTV 관제', '출입통제', '경호', '청원경찰']
PHYSICAL = ['보안','경비','특수경비','보안검색','출입통제','순찰','경호','청원경찰','cctv','관제','방호']
CYBER = ['정보보안','사이버보안','보안엔지니어','보안 엔지니어','침해대응','모의해킹','isms','취약점','security engineer','soc analyst']
NEAR_REGIONS = ['인천','김포','부천','강서구','양천구','구로구','금천구','영등포구','마포구','덕양구','마곡','가산','구로디지털','김포공항','인천공항']


def clean(s):
    return re.sub(r'\s+', ' ', htmlmod.unescape(str(s or ''))).strip()

def physical_ok(text):
    t = text.lower()
    return not any(x.lower() in t for x in CYBER) and any(x.lower() in t for x in PHYSICAL)

def near_ok(text):
    t = text.lower()
    return any(x.lower() in t for x in NEAR_REGIONS)

def commute_grade(text):
    t = text.lower()
    if any(x in t for x in ['서구','계양','청라','검단','석남','작전','가정','독정','루원']): return '◎'
    if any(x in t for x in ['인천공항','영종','공항화물청사']): return '△'
    return '○'

def classify(text):
    t = text.lower()
    if '특수경비' in t: return '특수경비'
    if '공항' in t and '보안검색' in t: return '공항 보안검색'
    if 'cctv' in t or '관제' in t: return 'CCTV/통합관제'
    if '경호' in t: return '경호'
    if '청원경찰' in t: return '청원경찰'
    if '출입통제' in t or '시설보안' in t: return '시설보안'
    if '아파트' in t and '경비' in t: return '아파트 경비'
    return '보안/경비'

def infer_shift(text):
    pats = [
        ('주주야야비비', r'주\s*주\s*야\s*야\s*비\s*비|주주야야비비'),
        ('주야비', r'주\s*야\s*비|주야비'),
        ('4조2교대', r'4\s*조\s*2\s*교대|4조2교대'),
        ('3조2교대', r'3\s*조\s*2\s*교대|3조2교대'),
        ('격일', r'격일'), ('주간', r'주간'), ('야간', r'야간'), ('교대', r'교대')
    ]
    for label, p in pats:
        if re.search(p, text, re.I): return label
    times = re.findall(r'(?:[01]?\d|2[0-3]):[0-5]\d\s*[~-]\s*(?:[01]?\d|2[0-3]):[0-5]\d', text)
    return ' / '.join(times[:2]) if times else '공고 확인'

def iso_date(v):
    s = clean(v)
    m = re.search(r'(20\d{2})[-./](\d{1,2})[-./](\d{1,2})', s)
    return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}' if m else ''

def jsonld_job(soup):
    found = []
    def walk(x):
        if isinstance(x, dict):
            if x.get('@type') == 'JobPosting': found.append(x)
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    for sc in soup.select('script[type="application/ld+json"]'):
        try: walk(json.loads(sc.string or sc.get_text()))
        except Exception: pass
    return found[0] if found else {}

def discover(template, patterns, q):
    url = template.format(q=quote(q))
    r = requests.get(url, headers=UA, timeout=18)
    if r.status_code in (403, 429): raise RuntimeError(f'HTTP {r.status_code}')
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'lxml')
    links = []
    for a in soup.find_all('a', href=True):
        full = urljoin(r.url, a['href'])
        if any(re.search(p, full, re.I) for p in patterns): links.append(full)
    return list(dict.fromkeys(links))[:12]

def extract(url, source):
    r = requests.get(url, headers=UA, timeout=18, allow_redirects=True)
    if r.status_code in (403, 429): raise RuntimeError(f'HTTP {r.status_code}')
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'lxml')
    ld = jsonld_job(soup)
    text = clean(soup.get_text(' '))[:15000]
    title = clean(ld.get('title') or (soup.title.string if soup.title else ''))
    org = ld.get('hiringOrganization') or {}
    company = clean(org.get('name') if isinstance(org, dict) else '')
    loc = ''
    jl = ld.get('jobLocation')
    if isinstance(jl, list): jl = jl[0] if jl else {}
    if isinstance(jl, dict):
        ad = jl.get('address') or {}
        if isinstance(ad, dict):
            loc = clean(' '.join(str(ad.get(k) or '') for k in ['addressRegion','addressLocality','streetAddress']))
        else: loc = clean(ad)
    desc = clean(BeautifulSoup(str(ld.get('description') or ''), 'lxml').get_text(' ')) or text
    combined = ' | '.join([title, company, loc, desc])
    if not physical_ok(combined) or not near_ok(combined): return None
    return {
        'company': company or '회사명 확인', 'title': title[:120] or '채용공고',
        'location': loc or '근무지 확인', 'shift': infer_shift(combined), 'salary': '공고 확인',
        'deadline': iso_date(ld.get('validThrough')) or '공고 확인', 'posted': iso_date(ld.get('datePosted')),
        'source': source, 'type': classify(combined), 'url': r.url, 'commute': commute_grade(combined)
    }

def expired(s):
    if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', s or ''): return False
    return datetime.strptime(s, '%Y-%m-%d').date() < datetime.now(KST).date()

prev = {'jobs': []}
if DATA.exists():
    try: prev = json.loads(DATA.read_text(encoding='utf-8'))
    except Exception: pass
old = {j.get('url',''): j for j in prev.get('jobs', [])}
now = datetime.now(KST)
all_jobs, status = {}, {}

for source, template, patterns in SEARCHES:
    urls, msg = [], ''
    for q in KEYWORDS:
        try:
            urls += discover(template, patterns, q)
            time.sleep(.3)
        except Exception as e:
            msg = str(e)[:80]
    urls = list(dict.fromkeys(urls))[:30]
    count = 0
    for u in urls:
        try:
            j = extract(u, source)
            if j and not expired(j['deadline']):
                all_jobs[j['url']] = j; count += 1
            time.sleep(.2)
        except Exception: pass
    status[source] = {'ok': count > 0, 'message': msg, 'found': count}

# If a source is temporarily blocked, preserve its still-active previous listings.
for u, j in old.items():
    if u not in all_jobs and not expired(j.get('deadline','')):
        st = status.get(j.get('source',''), {})
        if not st.get('ok', False): all_jobs[u] = j

for j in all_jobs.values():
    oj = old.get(j['url'])
    j['first_seen'] = (oj or {}).get('first_seen') or now.isoformat(timespec='seconds')
    try:
        fs = datetime.fromisoformat(j['first_seen'])
        if fs.tzinfo is None: fs = fs.replace(tzinfo=KST)
        j['new'] = (now - fs).total_seconds() <= 48*3600
    except Exception: j['new'] = False

rank = {'◎':0,'○':1,'△':2}
jobs = sorted(all_jobs.values(), key=lambda j: (rank.get(j.get('commute'),9), j.get('deadline') or '9999', j.get('company') or ''))
DATA.write_text(json.dumps({'updated_at': now.isoformat(timespec='seconds'), 'source_status': status, 'jobs': jobs}, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'updated {len(jobs)} jobs')
