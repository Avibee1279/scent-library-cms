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

  thumbs.forEach((thumb, index) => {
    thumb.addEventListener('click', () => showImage(index));
  });

  const prev = document.querySelector('.gallery-nav.prev');
  const next = document.querySelector('.gallery-nav.next');
  if(prev) prev.addEventListener('click', () => showImage(currentIndex - 1));
  if(next) next.addEventListener('click', () => showImage(currentIndex + 1));

  // Swipe support on mobile
  let startX = null;
  mainImg.addEventListener('touchstart', e => { startX = e.touches[0].clientX; }, {passive:true});
  mainImg.addEventListener('touchend', e => {
    if(startX === null) return;
    const endX = e.changedTouches[0].clientX;
    const diff = endX - startX;
    if(Math.abs(diff) > 45){
      if(diff < 0) showImage(currentIndex + 1);
      else showImage(currentIndex - 1);
    }
    startX = null;
  }, {passive:true});
})();
