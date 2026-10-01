# UAE Business AI Agent

وكيل أعمال قابل للتوسع لمراقبة السوق، استقبال طلبات الشركات، إعداد العروض،
إدارة التنفيذ، CRM، التواصل، الفوترة والمدفوعات — مع طبقة موافقات للعمليات الحساسة.

## الوضع الحالي
- Core orchestration
- Persistent JSON state
- Activity/approval log
- Mobile dashboard prototype
- Provider-neutral adapters
- Dry-run افتراضيًا

## المراحل التالية
1. Market research connector
2. CRM connector
3. Email connector
4. WhatsApp Business API connector
5. Invoice/payment provider
6. Execution tool registry
7. Authentication + audit log
8. Production database
9. Background worker/scheduler

## أمان
لا تضع مفاتيح الدفع أو WhatsApp أو البريد داخل الكود. استخدم Secrets/Environment
Variables. أبقِ DRY_RUN=true إلى أن يتم اختبار كل connector.
