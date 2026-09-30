# Capital Hybrid Cloud Bot

تم استبدال ملفات EA القديمة بـPython Bot يعمل مع Capital.com Public API عبر GitHub Actions.

## الحالة الحالية
- التشغيل: GitHub Actions.
- الفحص داخل الدورة: كل 10 ثوانٍ.
- دورة GitHub: 13 دقيقة كل 15 دقيقة.
- الوضع الافتراضي: `DRY_RUN=true`.
- لا MT5 Desktop.
- لا Grid / Martingale / Averaging.
- لا يتم تعديل صفقات مجهولة من هذا البوت.

## قبل التشغيل
ضع Secrets:
`CAPITAL_BASE_URL`, `CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, `CAPITAL_PASSWORD`, `TRADE_SIZE`, `DRY_RUN`.

ابدأ على Demo فقط.

## ملاحظة
طبقة حماية الأرباح المتقدمة قيد الإضافة ولم تُفعّل في النسخة الحالية؛ لا تشغّل Live قبل اكتمال اختبارها.

Capital.com توثق REST/WebSocket API وإمكانية فتح وتعديل وإغلاق المراكز عبر API. 
