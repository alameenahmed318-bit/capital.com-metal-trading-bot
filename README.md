# Capital Hybrid Cloud Bot

بوت Python سحابي لـ Capital.com عبر GitHub Actions وCapital Public API.

## التشغيل
- الحساب والـAPI credentials محفوظة في GitHub Secrets فقط.
- ابدأ على Demo مع `DRY_RUN=true`.
- شغّل Workflow: **Capital Hybrid Bot**.

## الاستراتيجية الهجينة
- قراءة أسعار حديثة كل 10 ثوانٍ أثناء تشغيل الدورة.
- EMA 9/21 + زخم + ATR-like volatility.
- دخول مرن بدل فلتر ثابت واحد.
- لا Grid ولا Martingale ولا Averaging.
- يسمح بصفقات مستقلة على أدوات مختلفة.
- حماية ربح تدريجية للصفقات التي فتحها هذا البوت فقط.
- لا يلمس الصفقات اليدوية أو الصفقات التي لا يملك معرفها.

## التشغيل السحابي
GitHub Actions يشغّل دورة متكررة ويعيد تشغيل البوت دوريًا. لا يحتاج MT5 Desktop.

> ابدأ Demo أولًا. لا توجد استراتيجية تضمن الربح، وتنفيذ الأوامر والأسعار قد تختلف عن الإشارات.
