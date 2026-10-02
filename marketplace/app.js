// UAE Market storefront UI. Cart state is shared with checkout.html via store.js.
let products=[];
let cat="الكل";

const $=id=>document.getElementById(id);

async function loadProducts(){
  try{
    const r=await fetch("https://uae-market-api.onrender.com/api/products");
    const data=await r.json();
    const rows=Object.entries(data).map(([id,p])=>({id,n:p.name,c:p.category||"منتجات",p:Number(p.price),o:Number(p.compareAt||p.price*1.25),e:p.emoji||"🛍️",stock:Number(p.stock||0),image:p.image||""})).filter(x=>x.stock>0);
    setMarketProducts(rows.map(x=>({id:x.id,name:x.n,category:x.c,price:x.p,compareAt:x.o,stock:x.stock,emoji:x.e,image:x.image})));
    products=rows;
  }catch(e){products=UAE_MARKET_PRODUCTS.map((p)=>({id:p.id,n:p.name,c:p.category,p:p.price,o:p.compareAt,e:p.emoji,stock:p.stock,image:p.image||""}));}
  render(); updateCart();
}

function renderCats(){
  const cs=["الكل",...new Set(products.map(x=>x.c))];
  $("categories").innerHTML=cs.map(x=>`<button class="${x===cat?"active":""}" onclick="cat='${x}';render()">${x}</button>`).join("");
}

function render(){
  renderCats();
  const q=$("search").value.trim().toLowerCase();
  const list=products.filter(x=>(cat==="الكل"||x.c===cat)&&x.n.toLowerCase().includes(q));
  $("countText").textContent=`${list.length} منتجات`;
  $("products").innerHTML=list.map(x=>`<article class="card"><span class="badge">خصم</span><a href="product.html?id=${encodeURIComponent(x.id)}" aria-label="عرض ${x.n}"><div class="visual">${x.e}</div></a><div class="info"><div class="name"><a href="product.html?id=${encodeURIComponent(x.id)}">${x.n}</a></div><div class="meta">${x.c} · توصيل داخل الإمارات</div><div class="price">AED ${x.p} <span class="old">AED ${x.o}</span></div><button class="add" onclick="add('${x.id}')">أضف للسلة</button></div></article>`).join("");
}

function add(id){
  addToCart(id);
  updateCart();
  openCart();
}

function updateCart(){
  const c=cartItems();
  $("cartCount").textContent=c.reduce((n,x)=>n+x.qty,0);
  $("cartTotal").textContent="AED "+cartTotal(c);
  $("cartItems").innerHTML=c.length
    ? c.map(x=>`<div class="cartRow"><span>${x.emoji} ${x.name} × ${x.qty}</span><b>AED ${x.price*x.qty}</b><button class="remove" onclick="removeFromCart('${x.id}')">×</button></div>`).join("")
    : "<p style='padding:20px;color:#777'>السلة فارغة حالياً.</p>";
}

function openCart(){$("cart").classList.add("open");$("overlay").classList.add("show")}
function closeCart(){$("cart").classList.remove("open");$("overlay").classList.remove("show")}

$("search").addEventListener("input",render);
$("searchBtn").addEventListener("click",render);
$("cartBtn").addEventListener("click",openCart);
$("closeCart").addEventListener("click",closeCart);
$("overlay").addEventListener("click",closeCart);
$("checkout").addEventListener("click",()=>{
  if(!cartItems().length){alert("السلة فارغة.");return;}
  location.href="checkout.html";
});
$("accountBtn").addEventListener("click",()=>alert("حساب العميل سيتم ربطه في المرحلة التالية."));

window.addEventListener("cartchange",updateCart);
loadProducts();