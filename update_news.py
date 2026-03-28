"""
بوت الأخبار التلقائي - يعمل كل 3 ساعات عبر GitHub Actions
- يجلب ~50 خبر جديد موزعة على كل الأنواع
- يعالجها بذكاء اصطناعي Gemini (مجاني)
- يحفظها في الأرشيف للأبد - لا حذف أبداً
- مع صور ومصادر وتفاصيل كاملة
"""

import feedparser
import json
import os
import sys
import time
import re
import hashlib
import requests
from urllib.parse import urlparse
from datetime import datetime, timezone

# ─── إعداد AI — Fallback تلقائي (Flash-Lite → Flash → Groq) ─────────────────

# ── Gemini (مشترك بين Flash-Lite و Flash) ──
try:
    from google import genai as google_genai
    from google.genai import types as genai_types
    _GEMINI_LITE_KEY  = os.environ.get("GEMINI_API_KEY", "")
    _GEMINI_FLASH_KEY = os.environ.get("GEMINI_FLASH_API_KEY", "") or _GEMINI_LITE_KEY
    GEMINI_LITE_READY  = bool(_GEMINI_LITE_KEY)
    GEMINI_FLASH_READY = bool(_GEMINI_FLASH_KEY)
    if GEMINI_LITE_READY:
        print("✅ Gemini 2.5 Flash-Lite جاهز  (1000 طلب/يوم)")
    if GEMINI_FLASH_READY:
        print("✅ Gemini 2.5 Flash جاهز        (250 طلب/يوم)")
except Exception as e:
    GEMINI_LITE_READY = GEMINI_FLASH_READY = False
    print(f"⚠️  خطأ تهيئة Gemini: {e}")

# ── Groq ──
try:
    from groq import Groq as GroqClient, RateLimitError as GroqRateLimit
    _GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
    GROQ_READY = bool(_GROQ_KEY)
    if GROQ_READY:
        _groq_client = GroqClient(api_key=_GROQ_KEY)
        print("✅ Groq LLaMA 3.3 70B جاهز      (14400 طلب/يوم)")
except Exception as e:
    GROQ_READY = False
    print(f"⚠️  خطأ تهيئة Groq: {e}")

AI_ENABLED = GEMINI_LITE_READY or GEMINI_FLASH_READY or GROQ_READY
if not AI_ENABLED:
    print("⚠️  لا يوجد أي AI key — سيتم حفظ الأخبار بدون معالجة AI")

# ─── مصادر RSS لكل تصنيف (متعددة لضمان التغطية) ─────────────────────────────
RSS_FEEDS = {
    "سياسة": [
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/73d0e1b4-532f-45ef-b135-bfdff8b8cab9",
        "https://www.skynewsarabia.com/rss/v1/politics.xml",
        "https://arabic.cnn.com/api/v1/rss/world/rss.xml",
        "https://arabic.rt.com/rss/politics/",
        "https://rss.rtarabic.com/politics/",
    ],
    "اقتصاد": [
        "https://www.skynewsarabia.com/rss/v1/economy.xml",
        "https://arabic.rt.com/rss/economy/",
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/02d2e95b-f6d4-4fcc-960f-ae6f7a2bc890",
        "https://rss.rtarabic.com/economy/",
    ],
    "تقنية": [
        "https://www.skynewsarabia.com/rss/v1/technology.xml",
        "https://arabic.rt.com/rss/technology/",
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/62a1baf1-56b3-4fd4-9cec-9e62a9dd476f",
        "https://rss.rtarabic.com/science/",
    ],
    "رياضة": [
        "https://www.skynewsarabia.com/rss/v1/sport.xml",
        "https://arabic.rt.com/rss/sport/",
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/adb73d2b-d468-4bd2-a50c-a99c05c2c8fb",
        "https://rss.rtarabic.com/sport/",
    ],
    "صحة": [
        "https://www.skynewsarabia.com/rss/v1/health.xml",
        "https://arabic.rt.com/rss/health/",
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/c9e72cd9-4c7e-416d-aadd-ac38e0b84ee5",
    ],
    "علوم": [
        "https://www.skynewsarabia.com/rss/v1/science.xml",
        "https://arabic.rt.com/rss/science/",
        "https://rss.rtarabic.com/science/",
    ],
    "فن وثقافة": [
        "https://www.skynewsarabia.com/rss/v1/art-culture.xml",
        "https://arabic.rt.com/rss/culture/",
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/8f6d0bc8-3f2e-4916-abb8-d8e54e6b0cad",
    ],
    "أخبار عامة": [
        "https://www.skynewsarabia.com/rss/v1/world.xml",
        "https://rss.rtarabic.com/news/",
        "https://arabic.rt.com/rss/world/",
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/73d0e1b4-532f-45ef-b135-bfdff8b8cab9",
    ],
}

# عدد الأخبار المطلوبة من كل تصنيف (مجموع ~50-56 خبر كل 3 ساعات)
ARTICLES_PER_CATEGORY = 7

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept-Language": "ar,en;q=0.9",
}

# ─── دوال مساعدة ────────────────────────────────────────────────────────────

def clean_html(text):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', str(text))
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def get_hash(link):
    return hashlib.md5(link.strip().encode('utf-8', errors='ignore')).hexdigest()

def estimate_reading_time(text):
    words = len(re.findall(r'\S+', text or ''))
    return max(1, round(words / 200))

def extract_image_from_entry(entry):
    """محاولات متعددة لاستخراج الصورة من مدخل RSS"""
    # 1. media:thumbnail
    for key in ('media_thumbnail', 'media_content'):
        val = getattr(entry, key, None)
        if val and isinstance(val, list) and val[0].get('url'):
            return val[0]['url']
    # 2. enclosures
    for enc in getattr(entry, 'enclosures', []):
        if enc.get('type', '').startswith('image') and enc.get('href'):
            return enc['href']
    # 3. summary img tag
    summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary or '')
    if m:
        url = m.group(1)
        if url.startswith('http'):
            return url
    return ''

def download_full_article(url, timeout=12):
    """تحميل المقال الكامل مع استخراج الصورة والنص"""
    try:
        from newspaper import Article, Config
        cfg = Config()
        cfg.request_timeout = timeout
        cfg.browser_user_agent = HEADERS['User-Agent']
        cfg.fetch_images = True
        cfg.memoize_articles = False
        art = Article(url, config=cfg, language='ar')
        art.download()
        art.parse()
        return art.text or '', art.top_image or ''
    except Exception:
        pass

    # fallback بـ requests مباشرة
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        text = clean_html(resp.text)[:3000]
        img_match = re.search(r'<meta[^>]+og:image[^>]+content=["\']([^"\']+)["\']', resp.text)
        img = img_match.group(1) if img_match else ''
        return text, img
    except Exception:
        return '', ''

def get_source_name(url):
    try:
        domain = urlparse(url).netloc.replace('www.', '').replace('arabic.', '')
        names = {
            'aljazeera.net': 'الجزيرة',
            'skynewsarabia.com': 'سكاي نيوز عربية',
            'cnn.com': 'CNN عربي',
            'rt.com': 'RT عربي',
            'rtarabic.com': 'RT عربي',
            'bbc.com': 'BBC عربي',
            'alarabiya.net': 'العربية',
        }
        for k, v in names.items():
            if k in domain:
                return v
        return domain.split('.')[0]
    except Exception:
        return ''

# ─── معالجة AI — Fallback تلقائي ────────────────────────────────────────────

AI_CALLS  = 0
AI_ERRORS = 0

def _build_prompt(title, text, category):
    return f"""أنت رئيس تحرير صحفي محترف متخصص في الإعلام العربي.

أعد كتابة هذا الخبر كمقال صحفي تفصيلي ومشوق.

العنوان: {title}
التصنيف: {category}
النص الأصلي:
{text[:3500]}

القواعد:
- اكتب مقالاً تفصيلياً لا يقل عن 500 كلمة
- افصل الفقرات بـ <br><br>
- العنوان جذاب وواضح (أقل من 110 أحرف)
- اكتب تاريخ الخبر الدقيق 100% متى نزل بالسنة و الشهر و اليوم و الساعة و الدقيقه  و اتاكد التاريخ الصحيح 100% لاتكتب غير الصحيح و بالتوقيت مكة كتب الخبر متى نزل بالضبط و ما اريد اخبار قديمة كل بحث جيب اخبار جديده 
- الملخص جملة أو جملتان تلخص الخبر
- حدد أهمية الخبر من 1 إلى 10 (10 = عاجل جداً)
- إذا كان الخبر عاجلاً ضع isBreaking: true

أجب بـ JSON صحيح فقط بدون أي نص خارجه:
{{
  "title": "...",
  "summary": "...",
  "content": "فقرة أولى...<br><br>فقرة ثانية...<br><br>فقرة ثالثة...",
  "category": "سياسة أو اقتصاد أو تقنية أو رياضة أو صحة أو علوم أو فن وثقافة أو أخبار عامة",
  "tags": ["وسم1", "وسم2", "وسم3", "وسم4"],
  "importance": 6,
  "isBreaking": false
}}"""

def _parse_ai_response(raw):
    """استخراج وتحقق من JSON الـ AI"""
    raw = raw.strip()
    # إزالة markdown code blocks
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
    raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
    raw = raw.strip()

    json_match = re.search(r'\{[\s\S]*?\}(?=\s*$|\s*```)', raw)
    if not json_match:
        json_match = re.search(r'\{[\s\S]*\}', raw)
    if not json_match:
        return None

    data = json.loads(json_match.group())
    required = ['title', 'content', 'category']
    if not all(k in data and data[k] for k in required):
        return None

    data['title']      = str(data['title'])[:120].strip()
    data['content']    = str(data['content']).strip()
    data['summary']    = str(data.get('summary', ''))[:300].strip()
    data['importance'] = max(1, min(10, int(data.get('importance', 6))))
    data['isBreaking'] = bool(data.get('isBreaking', False))
    data['tags']       = [str(t)[:30] for t in data.get('tags', [])[:6] if t]
    return data

# ── Provider 1: Gemini 2.5 Flash-Lite ──
def _call_gemini_lite(prompt):
    client = google_genai.Client(api_key=_GEMINI_LITE_KEY)
    resp = client.models.generate_content(
        model="gemini-2.5-flash-lite-preview-06-17",
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            max_output_tokens=2048,
            temperature=0.7,
        ),
    )
    return resp.text

# ── Provider 2: Gemini 2.5 Flash ──
def _call_gemini_flash(prompt):
    client = google_genai.Client(api_key=_GEMINI_FLASH_KEY)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            max_output_tokens=2048,
            temperature=0.7,
        ),
    )
    return resp.text

# ── Provider 3: Groq LLaMA 3.3 70B ──
def _call_groq(prompt):
    resp = _groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "أنت رئيس تحرير صحفي محترف. أجب بـ JSON فقط بدون أي نص إضافي."},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=2048,
        temperature=0.7,
    )
    return resp.choices[0].message.content

# ── قائمة الـ Providers بالترتيب ──
_AI_PROVIDERS = []
if GEMINI_LITE_READY:
    _AI_PROVIDERS.append(("Gemini Flash-Lite", _call_gemini_lite))
if GEMINI_FLASH_READY:
    _AI_PROVIDERS.append(("Gemini Flash",      _call_gemini_flash))
if GROQ_READY:
    _AI_PROVIDERS.append(("Groq LLaMA 3.3",    _call_groq))

def ai_process(title, text, category):
    global AI_CALLS, AI_ERRORS
    if not AI_ENABLED or not text or len(text.strip()) < 80:
        return None

    AI_CALLS += 1
    prompt = _build_prompt(title, text, category)

    for provider_name, provider_fn in _AI_PROVIDERS:
        try:
            raw = provider_fn(prompt)
            if not raw or not raw.strip():
                continue
            data = _parse_ai_response(raw)
            if data:
                print(f"      🤖 [{provider_name}] ✅")
                return data
        except json.JSONDecodeError:
            AI_ERRORS += 1
            print(f"      ⚠️  [{provider_name}] JSON خطأ — جرّب التالي")
        except Exception as e:
            AI_ERRORS += 1
            err_str = str(e).lower()
            if 'quota' in err_str or 'rate' in err_str or '429' in err_str:
                print(f"      ⏳ [{provider_name}] Rate limit — جرّب التالي")
                time.sleep(5)
            else:
                print(f"      ⚠️  [{provider_name}] خطأ: {e} — جرّب التالي")

    print(f"      ❌ جميع الـ AI providers فشلت لهذا الخبر")
    return None

# ─── تحميل الأرشيف القديم ────────────────────────────────────────────────────

def load_archive():
    if not os.path.exists('news.json'):
        print("📁 ملف news.json غير موجود — سيتم إنشاؤه")
        return []
    try:
        with open('news.json', 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict) and 'articles' in raw:
            return raw['articles']
    except json.JSONDecodeError:
        print("⚠️  news.json تالف — سيتم إعادة البناء من الصفر")
        # احتياطي
        try:
            import shutil
            shutil.copy('news.json', 'news.json.bak')
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️  خطأ في قراءة الأرشيف: {e}")
    return []

# ─── البداية ─────────────────────────────────────────────────────────────────

print("=" * 60)
print("🤖 بوت الأخبار التلقائي — بدء التشغيل")
print(f"🕐 الوقت: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print("=" * 60)

archive = load_archive()
existing_links = {item.get('link', '').strip() for item in archive if item.get('link')}
existing_hashes = {item.get('hash', '') for item in archive if item.get('hash')}

print(f"📚 الأرشيف الحالي: {len(archive)} خبر محفوظ")
print(f"🎯 الهدف: جلب ~{ARTICLES_PER_CATEGORY * len(RSS_FEEDS)} خبر جديد")
print()

new_entries = []

# ─── الجلب الرئيسي ────────────────────────────────────────────────────────────

for category, feeds in RSS_FEEDS.items():
    cat_count = 0
    print(f"📂 [{category}] جاري الجلب...")

    for feed_url in feeds:
        if cat_count >= ARTICLES_PER_CATEGORY:
            break

        try:
            feed = feedparser.parse(feed_url, request_headers=HEADERS)
            if not feed.entries:
                print(f"   ⚠️  لا توجد مدخلات في: {feed_url}")
                continue

            for entry in feed.entries:
                if cat_count >= ARTICLES_PER_CATEGORY:
                    break

                # ── استخراج الرابط ──
                link = (getattr(entry, 'link', '') or getattr(entry, 'id', '')).strip()
                if not link or not link.startswith('http'):
                    continue

                # ── التحقق من التكرار ──
                link_hash = get_hash(link)
                if link in existing_links or link_hash in existing_hashes:
                    continue

                # ── العنوان ──
                title = clean_html(getattr(entry, 'title', ''))
                if not title or len(title) < 8:
                    continue

                # ── الصورة من RSS أولاً (أسرع وأكثر موثوقية) ──
                image = extract_image_from_entry(entry)

                # ── تحميل المقال الكامل ──
                article_text = ''
                full_img = ''
                article_text, full_img = download_full_article(link)

                if not image and full_img:
                    image = full_img

                # نص احتياطي من RSS إذا فشل التحميل
                if not article_text or len(article_text) < 80:
                    article_text = clean_html(
                        getattr(entry, 'summary', '') or
                        getattr(entry, 'description', '') or
                        getattr(entry, 'content', [{}])[0].get('value', '')
                    )

                # صورة احتياطية بناءً على التصنيف
                if not image:
                    cat_seeds = {
                        'سياسة': 'politics', 'اقتصاد': 'business', 'تقنية': 'tech',
                        'رياضة': 'sports', 'صحة': 'health', 'علوم': 'science',
                        'فن وثقافة': 'art', 'أخبار عامة': 'news'
                    }
                    seed = cat_seeds.get(category, 'news') + link_hash[:6]
                    image = f"https://picsum.photos/seed/{seed}/800/450"

                # ── تاريخ النشر ──
                pub = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
                if pub:
                    try:
                        ts = int(time.mktime(pub))
                        date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        ts = int(time.time())
                        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                else:
                    ts = int(time.time())
                    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

                source = get_source_name(link)

                # ── معالجة AI ──
                data = None
                if AI_ENABLED and article_text and len(article_text) > 80:
                    data = ai_process(title, article_text, category)
                    time.sleep(2)  # تجنب rate limit

                # ── بناء الخبر النهائي ──
                body = ''
                if data:
                    body = data['content']
                elif article_text:
                    # تحسين تنسيق النص بدون AI
                    paragraphs = [p.strip() for p in re.split(r'\n{2,}|\. {2,}', article_text) if len(p.strip()) > 30]
                    body = '<br><br>'.join(paragraphs[:8]) if paragraphs else article_text[:2500]
                else:
                    body = title

                summary = ''
                if data:
                    summary = data.get('summary', '')
                if not summary and article_text:
                    summary = article_text[:250].strip()
                    if len(article_text) > 250:
                        summary += '...'

                news_item = {
                    "id": int(time.time() * 1000) + len(new_entries),
                    "hash": link_hash,
                    "title": (data['title'] if data else title)[:120],
                    "link": link,
                    "image": image,
                    "category": data['category'] if data else category,
                    "body": body,
                    "summary": summary,
                    "importance": data.get('importance', 5) if data else 5,
                    "isBreaking": bool(data.get('isBreaking', False)) if data else False,
                    "isTrending": False,
                    "tags": data.get('tags', [category]) if data else [category],
                    "timestamp": ts,
                    "date": date_str,
                    "source": source,
                    "sourceLink": link,
                    "viewCount": 0,
                    "commentCount": 0,
                    "readingTime": estimate_reading_time(body),
                }

                new_entries.append(news_item)
                existing_links.add(link)
                existing_hashes.add(link_hash)
                cat_count += 1

                ai_tag = "🤖 AI" if data else "📝 RAW"
                print(f"   ✅ {ai_tag} [{cat_count}/{ARTICLES_PER_CATEGORY}] {news_item['title'][:65]}...")

        except Exception as e:
            print(f"   ❌ خطأ في {feed_url}: {type(e).__name__}: {e}")
            continue

    print(f"   → تم جلب {cat_count} خبر جديد في [{category}]")
    print()

# ─── الدمج والحفظ ────────────────────────────────────────────────────────────

print("=" * 60)
print(f"📰 أخبار جديدة هذه الدورة: {len(new_entries)}")
print(f"📚 الأرشيف السابق: {len(archive)}")

# دمج: الجديد أمام القديم (الأخبار القديمة لا تُحذف أبداً)
final_archive = new_entries + archive

# تعليم الأخبار المهمة كـ trending
for i, article in enumerate(final_archive):
    final_archive[i]['isTrending'] = (i < 30 and article.get('importance', 5) >= 7)

# ترتيب: عاجل أولاً، ثم الأهمية، ثم الأحدث
final_archive.sort(
    key=lambda x: (
        int(x.get('isBreaking', False)),
        x.get('importance', 5),
        x.get('timestamp', 0)
    ),
    reverse=True
)

total = len(final_archive)
print(f"📦 إجمالي الأرشيف بعد الدمج: {total} خبر")
providers_str = " + ".join(p[0] for p in _AI_PROVIDERS) if _AI_PROVIDERS else "لا يوجد"
print(f"🤖 أُعولج بـ AI: {AI_CALLS} خبر | فشل AI: {AI_ERRORS} | Providers: {providers_str}")

# حفظ
output = {
    "lastUpdated": datetime.now(timezone.utc).isoformat(),
    "totalCount": total,
    "aiProcessed": AI_CALLS,
    "articles": final_archive
}

try:
    with open('news.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ تم الحفظ بنجاح في news.json")
    print(f"🗃️  الأرشيف الكلي: {total} خبر (لن يُحذف أي خبر أبداً)")
except Exception as e:
    print(f"❌ خطأ في الحفظ: {e}")
    sys.exit(1)

print("=" * 60)
print("✅ انتهى البوت — الدورة القادمة خلال 5 ساعات")
print("=" * 60)
