# MT5 Ready EA

تم استبدال مشروع Capital.com القديم بهذا الإصدار الجاهز لـ MetaTrader 5.

## EA
- 2MACDSTO v1.4
- Two MACDs + Stochastic
- Fixed volume: 0.01 lot
- Grid: disabled
- Martingale: disabled
- Multiple signal positions: enabled
- Trailing stop: enabled
- Account login/password/server are NOT stored in this repository.

## MT5
انسخ:
- `Experts/2MACDSTO_AMIN.mq5` إلى مجلد Experts
- `Include/EAUtils.mqh` و `Include/errordescription.mqh` إلى Include

ثم افتح MetaEditor واعمل Compile.

> ملاحظة: هذه الاستراتيجية منشورة أصلاً لاختبار NZDUSD على إطار 3 ساعات، لذلك لا يوجد ضمان أنها مناسبة لكل رمز أو حساب. يجب اختبارها Demo قبل Live.
