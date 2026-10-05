# UAE Market Backend

واجهة API أولية للطلبات، مبنية ببايثون وSQLite بدون اعتماد خارجي.

## Endpoints
- GET /api/health
- GET /api/products
- POST /api/orders
- GET /api/orders/<order_id>

الـAPI يعيد حساب الأسعار من كتالوج الخادم ولا يثق بسعر المتصفح.

## الإنتاج
قبل فتحه للعامة يجب تشغيله خلف HTTPS، إضافة مصادقة/حدود طلبات، قاعدة بيانات مُدارة، وStripe Checkout + webhook للتحقق من الدفع.