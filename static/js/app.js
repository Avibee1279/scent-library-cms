const progress = document.getElementById('topProgress');
function updateProgress(){
  const scrollTop = window.scrollY || document.documentElement.scrollTop;
  const height = document.documentElement.scrollHeight - window.innerHeight;
  if(progress){ progress.style.width = height > 0 ? `${(scrollTop / height) * 100}%` : '0%'; }
}
window.addEventListener('scroll', updateProgress); updateProgress();

const revealItems = document.querySelectorAll('.section-reveal, .card-reveal');
const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if(entry.isIntersecting){ entry.target.classList.add('visible'); observer.unobserve(entry.target); }
  });
}, { threshold: 0.12 });
revealItems.forEach(item => observer.observe(item));

const mobileMenu = document.getElementById('mobileMenu');
const navLinks = document.getElementById('navLinks');
if(mobileMenu && navLinks){
  mobileMenu.addEventListener('click', () => navLinks.classList.toggle('open'));
  navLinks.querySelectorAll('a').forEach(a => a.addEventListener('click', () => navLinks.classList.remove('open')));
}

// Local wishlist counter. This is client-side only, useful for browsing.
(function(){
  const key = 'sl_wishlist';
  const count = document.getElementById('wishlistCount');
  function get(){ try{return JSON.parse(localStorage.getItem(key) || '[]')}catch(e){return []} }
  function set(items){ localStorage.setItem(key, JSON.stringify(items)); update(); }
  function update(){
    const items = get();
    if(count) count.textContent = items.length;
    document.querySelectorAll('.wishlist-toggle').forEach(btn => {
      const active = items.some(x => String(x.id) === String(btn.dataset.id));
      btn.classList.toggle('active', active);
      btn.textContent = active ? '♥' : '♡';
    });
  }
  document.querySelectorAll('.wishlist-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      let items = get();
      const id = btn.dataset.id;
      if(items.some(x => String(x.id) === String(id))){ items = items.filter(x => String(x.id) !== String(id)); }
      else { items.push({id, name: btn.dataset.name || 'Perfume'}); }
      set(items);
      showToast(items.some(x => String(x.id) === String(id)) ? 'Added to wishlist' : 'Removed from wishlist');
    });
  });
  const wishlistBtn = document.getElementById('wishlistBtn');
  if(wishlistBtn){ wishlistBtn.addEventListener('click', () => showToast(get().length ? get().map(x => x.name).join(', ') : 'Wishlist is empty')); }
  update();
})();

// Request basket. No payment. It builds a request and sends it to the existing CMS order-request route.
(function(){
  const key = 'sl_request_basket';
  const drawer = document.getElementById('basketDrawer');
  const backdrop = document.getElementById('basketBackdrop');
  const openBtn = document.getElementById('basketOpen');
  const closeBtn = document.getElementById('basketClose');
  const count = document.getElementById('basketCount');
  const itemsBox = document.getElementById('basketItems');
  const messageField = document.getElementById('basketMessage');
  const extraNote = document.getElementById('basketExtraNote');
  const whatsappBtn = document.getElementById('basketWhatsapp');
  const basketForm = document.querySelector('.basket-form');

  function get(){ try{return JSON.parse(localStorage.getItem(key) || '[]')}catch(e){return []} }
  function set(items){ localStorage.setItem(key, JSON.stringify(items)); render(); }
  function totalQty(items){ return items.reduce((sum, x) => sum + Number(x.qty || 1), 0); }
  function money(n){ return Number(n || 0).toLocaleString('en-US', {maximumFractionDigits:0}); }
  function buildMessage(){
    const items = get();
    const lines = items.map((x, i) => `${i+1}. ${x.name}${x.size ? ' - ' + x.size : ''} x ${x.qty || 1} (Rs ${money(x.price)})`);
    const note = extraNote && extraNote.value.trim() ? `\nNote: ${extraNote.value.trim()}` : '';
    return `Request basket from The Scent Library:\n${lines.join('\n')}${note}`;
  }
  function open(){ if(!drawer) return; drawer.classList.add('open'); drawer.setAttribute('aria-hidden','false'); if(backdrop){backdrop.hidden=false;} render(); }
  function close(){ if(!drawer) return; drawer.classList.remove('open'); drawer.setAttribute('aria-hidden','true'); if(backdrop){backdrop.hidden=true;} }
  function render(){
    const items = get();
    if(count) count.textContent = totalQty(items);
    if(messageField) messageField.value = buildMessage();
    if(!itemsBox) return;
    if(!items.length){ itemsBox.innerHTML = '<div class="basket-empty">Your request basket is empty. Add a perfume from the shop.</div>'; return; }
    itemsBox.innerHTML = items.map(x => `
      <div class="basket-line" data-id="${x.id}">
        ${x.image ? `<img src="${x.image}" alt="${x.name}">` : '<div></div>'}
        <div><strong>${x.name}</strong><small>${x.size || ''} · Rs ${money(x.price)}</small></div>
        <div class="qty-controls"><button type="button" data-dec="${x.id}">−</button><span>${x.qty || 1}</span><button type="button" data-inc="${x.id}">+</button></div>
      </div>`).join('');
    itemsBox.querySelectorAll('[data-inc]').forEach(btn => btn.addEventListener('click', () => changeQty(btn.dataset.inc, 1)));
    itemsBox.querySelectorAll('[data-dec]').forEach(btn => btn.addEventListener('click', () => changeQty(btn.dataset.dec, -1)));
  }
  function changeQty(id, delta){
    let items = get();
    items = items.map(x => String(x.id) === String(id) ? {...x, qty: Math.max(0, Number(x.qty || 1) + delta)} : x).filter(x => Number(x.qty || 0) > 0);
    set(items);
  }
  document.querySelectorAll('.add-to-basket').forEach(btn => {
    btn.addEventListener('click', () => {
      const item = {id: btn.dataset.id, name: btn.dataset.name, price: btn.dataset.price, size: btn.dataset.size, image: btn.dataset.image, qty: 1};
      const items = get();
      const existing = items.find(x => String(x.id) === String(item.id));
      if(existing) existing.qty = Number(existing.qty || 1) + 1; else items.push(item);
      set(items);
      open();
    });
  });
  if(openBtn) openBtn.addEventListener('click', open);
  if(closeBtn) closeBtn.addEventListener('click', close);
  if(backdrop) backdrop.addEventListener('click', close);
  if(extraNote) extraNote.addEventListener('input', () => { if(messageField) messageField.value = buildMessage(); });
  if(basketForm){
    basketForm.addEventListener('submit', (e) => {
      if(!get().length){ e.preventDefault(); showToast('Add at least one perfume to the basket.'); return; }
      if(messageField) messageField.value = buildMessage();
      window.setTimeout(() => localStorage.removeItem(key), 250);
    });
  }
  if(whatsappBtn){
    whatsappBtn.addEventListener('click', () => {
      if(!get().length){ showToast('Add at least one perfume to the basket.'); return; }
      const msg = encodeURIComponent(buildMessage());
      window.open(`https://wa.me/${window.SL_WHATSAPP || ''}?text=${msg}`, '_blank');
    });
  }
  render();
})();

function showToast(message){
  const old = document.querySelector('.wishlist-toast');
  if(old) old.remove();
  const toast = document.createElement('div');
  toast.className = 'wishlist-toast';
  toast.textContent = message;
  document.body.appendChild(toast);
  window.setTimeout(() => toast.remove(), 2300);
}

// Product detail gallery: click thumbnails and use left/right arrows
(function(){
  const mainImg = document.getElementById('mainGalleryImage');
  const thumbsWrap = document.getElementById('detailGalleryThumbs');
  if(!mainImg || !thumbsWrap) return;

  const thumbs = Array.from(thumbsWrap.querySelectorAll('.gallery-thumb'));
  if(!thumbs.length) return;
  let currentIndex = Math.max(0, thumbs.findIndex(t => t.classList.contains('active')));

  function showImage(index){
    if(index < 0) index = thumbs.length - 1;
    if(index >= thumbs.length) index = 0;
    const thumb = thumbs[index];
    const full = thumb.dataset.full;
    if(!full) return;

    currentIndex = index;
    thumbs.forEach(t => t.classList.remove('active'));
    thumb.classList.add('active');
    thumb.scrollIntoView({behavior:'smooth', inline:'center', block:'nearest'});

    mainImg.classList.add('switching');
    window.setTimeout(() => {
      mainImg.src = full;
      mainImg.classList.remove('switching');
    }, 120);
  }

  thumbs.forEach((thumb, index) => thumb.addEventListener('click', () => showImage(index)));
  const prev = document.querySelector('.gallery-nav.prev');
  const next = document.querySelector('.gallery-nav.next');
  if(prev) prev.addEventListener('click', () => showImage(currentIndex - 1));
  if(next) next.addEventListener('click', () => showImage(currentIndex + 1));

  let startX = null;
  mainImg.addEventListener('touchstart', e => { startX = e.touches[0].clientX; }, {passive:true});
  mainImg.addEventListener('touchend', e => {
    if(startX === null) return;
    const endX = e.changedTouches[0].clientX;
    const diff = endX - startX;
    if(Math.abs(diff) > 45){ diff < 0 ? showImage(currentIndex + 1) : showImage(currentIndex - 1); }
    startX = null;
  }, {passive:true});
})();
