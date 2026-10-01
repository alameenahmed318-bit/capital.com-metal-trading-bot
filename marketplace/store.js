// UAE Market store data + cart/order state. No payment secrets belong here.
const UAE_MARKET_PRODUCTS=[
{id:"P001",name:"سماعات لاسلكية Pro",category:"إلكترونيات",price:129,compareAt:179,stock:24,rating:4.8,emoji:"🎧"},
{id:"P002",name:"ساعة ذكية رياضية",category:"إلكترونيات",price:199,compareAt:249,stock:18,rating:4.7,emoji:"⌚"},
{id:"P003",name:"حقيبة يومية أنيقة",category:"أزياء",price:89,compareAt:129,stock:31,rating:4.6,emoji:"👜"},
{id:"P004",name:"حذاء رياضي خفيف",category:"أزياء",price:149,compareAt:199,stock:16,rating:4.8,emoji:"👟"},
{id:"P005",name:"طقم عناية بالبشرة",category:"جمال",price:75,compareAt:99,stock:40,rating:4.5,emoji:"🧴"},
{id:"P006",name:"مصباح مكتب ذكي",category:"المنزل",price:59,compareAt:79,stock:22,rating:4.4,emoji:"💡"},
{id:"P007",name:"زجاجة ماء حرارية",category:"المنزل",price:45,compareAt:65,stock:35,rating:4.6,emoji:"🥤"},
{id:"P008",name:"نظارة شمسية عصرية",category:"أزياء",price:69,compareAt:99,stock:27,rating:4.5,emoji:"🕶️"}
];
function setMarketProducts(items){UAE_MARKET_PRODUCTS.splice(0,UAE_MARKET_PRODUCTS.length,...items)}
function loadCart(){try{return JSON.parse(localStorage.getItem("uae_market_cart")||"[]")}catch{return[]}}
function saveCart(c){localStorage.setItem("uae_market_cart",JSON.stringify(c))}
function cartItems(){return loadCart()}
function cartTotal(c=cartItems()){return c.reduce((s,x)=>s+(x.price*x.qty),0)}
function addToCart(productId){const p=UAE_MARKET_PRODUCTS.find(x=>x.id===productId);if(!p)return;const c=loadCart();const row=c.find(x=>x.id===productId);if(row){if(row.qty<p.stock)row.qty++}else c.push({...p,qty:1});saveCart(c);window.dispatchEvent(new Event("cartchange"))}
function removeFromCart(productId){const c=loadCart().filter(x=>x.id!==productId);saveCart(c);window.dispatchEvent(new Event("cartchange"))}
function setCartQty(productId,qty){const c=loadCart();const row=c.find(x=>x.id===productId);if(!row)return;if(qty<=0)return removeFromCart(productId);row.qty=Math.min(qty,row.stock);saveCart(c);window.dispatchEvent(new Event("cartchange"))}
