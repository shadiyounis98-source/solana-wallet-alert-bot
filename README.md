# Solana Wallet Alert Bot — GitHub كل 5 دقائق

هذه نسخة خاصة ببوت محفظة سولانا فقط. تم حذف/تجاهل بوت عملات الميم والانفجار من هذه الحزمة.

## ماذا تعمل؟

GitHub Actions يشغل البوت كل 5 دقائق:

1. يقرأ آخر معاملات المحفظة.
2. يفحص إذا فيه شراء جديد.
3. يطبق شروط استراتيجية المحفظة.
4. يرسل تنبيه Telegram إذا النتيجة PASS.
5. يحفظ `state.json` داخل الريبو حتى لا يكرر نفس التنبيه.

## الخطوات على GitHub

### 1) ارفع الملفات إلى Repository

ارفع محتوى هذه الحزمة إلى ريبو GitHub.

الأفضل يكون Public إذا تريد GitHub Actions مجاني بالكامل على runner عادي.

### 2) أضف Telegram Secrets

من GitHub:

`Repo > Settings > Secrets and variables > Actions > New repository secret`

أضف السرّين:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

لا ترفع ملف `.env` إلى GitHub.

### 3) شغل أول تجربة يدويًا

اذهب إلى:

`Actions > Solana Wallet Alert Bot - كل 5 دقائق > Run workflow`

أول تشغيل سيعمل Bootstrap للحالة الحالية حتى لا يرسل تنبيهات قديمة.

بعدها سيشتغل تلقائيًا كل 5 دقائق.

## ملاحظات مهمة

- GitHub schedules تعمل بتوقيت UTC.
- GitHub قد يؤخر scheduled workflow أحيانًا وقت الضغط.
- هذا مناسب لبوت المحفظة لأن كل 5 دقائق مقبول.
- هذا ليس مناسبًا لبوت عملات الانفجار لأنه يحتاج ثواني.
- في Public repo، GitHub قد يعطل scheduled workflows بعد 60 يوم بدون نشاط؛ لكن هذا workflow يحدث `state.json` غالبًا، وهذا يساعد على إبقاء النشاط.

## تغيير المحفظة

الملف الحالي يراقب:

```text
5G6nFdugA2D5qGP4zr6b2DzPKwCTuP22gSEAhykWpLF
```

إذا أردت تغييرها، عدّل `WATCH_WALLET` داخل:

`.github/workflows/solana-wallet-alert-5min.yml`

## تشغيل محلي اختياري

```bash
cd solana_wallet_alert_bot
cp .env.example .env
# عدّل بيانات Telegram
python3 -m pip install -r requirements.txt
RUN_ONCE=true MAX_CYCLES=1 python3 bot.py
```
