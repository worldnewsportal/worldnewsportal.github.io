import feedparser
import json
import os
import time
from datetime import datetime
import google.generativeai as genai
from newspaper import Article

# إعداد مفتاح الذكاء الاصطناعي
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

# روابط جلب الأخبار (أضفنا المزيد لضمان توفر 50 خبر)
RSS_FEEDS =[
    "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/73d0e1b4-532f-45ef-b135-bfdff8b8cab9",
    "https://arabic.cnn.com/api/v1/rss/tech/rss.xml",
    "https://rss.rtarabic.com/news",
    "https://www.skynewsarabia.com/rss"
]

# 1. قراءة الأخبار القديمة لمنع التكرار
existing_news =[]
existing_links = set()
if os.path.exists('news.json'):
    try:
        with open('news.json', 'r', encoding='utf-8') as f:
            existing_news = json.load(f)
            for item in existing_news:
                existing_links.add(item['original_link'])
    except:
        pass

new_news =[]
target_news_count = 50  # العدد المطلوب في كل تشغيلة

def generate_detailed_article(title, full_text):
    try:
        prompt = f"""
        بصفتك صحفياً محترفاً، قم بكتابة تقرير إخباري مفصل وشامل بناءً على المعطيات التالية:
        العنوان الأصلي: {title}
        النص الأصلي: {full_text}
        
        المطلوب بصيغة JSON فقط:
        {{
            "category": "تصنيف الخبر (سياسة، اقتصاد، رياضة، تقنية، صحة)",
            "seo_title": "عنوان جذاب جداً وخالي من حقوق الطبع",
            "detailed_article": "مقال صحفي مفصل من عدة فقرات يشرح الخبر بالكامل بطريقة احترافية ومحايدة (استخدم علامات <br> لتقسيم الفقرات)"
        }}
        """
        response = model.generate_content(prompt)
        ai_data = json.loads(response.text.replace('```json', '').replace('```', ''))
        return ai_data
    except Exception as e:
        return None

# 2. حلقة جلب الأخبار
for url in RSS_FEEDS:
    if len(new_news) >= target_news_count:
        break
        
    feed = feedparser.parse(url)
    for entry in feed.entries:
        if len(new_news) >= target_news_count:
            break
            
        # تخطي الخبر إذا كان موجوداً مسبقاً
        if entry.link in existing_links:
            continue
            
        print(f"جاري معالجة: {entry.title}")
        
        try:
            # الدخول للخبر وسحب النص الكامل والصورة الأصلية
            article = Article(entry.link)
            article.download()
            article.parse()
            full_text = article.text
            top_image = article.top_image
            
            if len(full_text) < 150: # تجاهل الأخبار القصيرة جداً
                continue
                
            # إرسال النص للذكاء الاصطناعي لكتابة مقال مفصل
            ai_result = generate_detailed_article(entry.title, full_text)
            
            if ai_result:
                news_item = {
                    "id": str(hash(entry.link))[1:10], # إنشاء ID مميز للتعليقات
                    "title": ai_result["seo_title"],
                    "original_link": entry.link,
                    "category": ai_result["category"],
                    "detailed_article": ai_result["detailed_article"],
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "image": top_image if top_image else "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"
                }
                new_news.append(news_item)
                existing_links.add(entry.link)
                
                # توقف 4 ثواني لمنع حظر جوجل (Rate Limit)
                time.sleep(4)
                
        except Exception as e:
            print(f"فشل معالجة الخبر: {e}")
            continue

# 3. دمج الأخبار الجديدة مع القديمة (نحتفظ بآخر 200 خبر فقط حتى لا يصبح الموقع بطيئاً)
all_news = new_news + existing_news
all_news = all_news[:200]

# حفظ الملف
with open('news.json', 'w', encoding='utf-8') as f:
    json.dump(all_news, f, ensure_ascii=False, indent=4)

print(f"تم جلب {len(new_news)} خبر جديد بنجاح وتفصيلها!")
