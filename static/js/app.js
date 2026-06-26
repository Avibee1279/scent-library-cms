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
